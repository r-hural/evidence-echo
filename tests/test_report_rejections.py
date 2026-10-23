import json
import time
from contextlib import closing
from pathlib import Path

from conftest import make_item
from evidence_echo import api, cli
from evidence_echo.core import config, store

EMPTY = json.dumps({"results": []})
from tests.conftest import seed_topic_study


def _add_fails(cfg, n_triage=1, n_judge=1, tag="a"):
    with closing(store.connect(cfg.db_path)) as conn:
        for i in range(n_triage):
            iid = store.insert_item(conn, make_item(url=f"https://x.com/{tag}-t{i}",
                                                    title=f"Triage fail {tag}{i}"))
            store.set_triage(conn, iid, False, "no data")
        for i in range(n_judge):
            iid = store.insert_item(conn, make_item(url=f"https://x.com/{tag}-j{i}",
                                                    title=f"Judge fail {tag}{i}"))
            store.set_verdict(conn, iid, False, None, {}, None, reason="no control group")


def _setup(workspace, **settings_):
    for k, v in settings_.items():
        api.config_set(config.workspace(), k, v)
    cfg = config.load()
    _add_fails(cfg)
    return cfg


def test_flag_writes_pair_with_setting_off(workspace):
    cfg = _setup(workspace)
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, EMPTY, rejections=True)
    md = Path(out["path"]).with_name(Path(out["path"]).name.replace("-report", "-rejected"))
    assert out["rejections"]["count"] == 2
    assert out["rejections"]["path"] == str(md) and md.exists()
    assert Path(out["rejections"]["html"]).exists()


def test_setting_on_writes_without_flag(workspace):
    cfg = _setup(workspace, rejections_report="on")
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, EMPTY)
    assert out["rejections"]["count"] == 2 and Path(out["rejections"]["path"]).exists()


def test_setting_off_no_flag_no_file(workspace):
    cfg = _setup(workspace)
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, EMPTY)
    assert "rejections" not in out and "rejections_error" not in out
    assert not list(cfg.reports_dir.glob("*-rejected*"))


def test_html_off_markdown_only(workspace):
    cfg = _setup(workspace, html_reports="off")
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, EMPTY, rejections=True)
    assert "html" not in out["rejections"] and Path(out["rejections"]["path"]).exists()
    assert not list(cfg.reports_dir.glob("*-rejected*.html"))


def test_window_moves_with_each_report(workspace):
    cfg = _setup(workspace)
    seed_topic_study()   # a report needs a new study
    api.render_report(cfg, EMPTY)
    time.sleep(0.01)
    _add_fails(cfg, 1, 0, tag="b")
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, EMPTY, rejections=True)
    assert out["rejections"]["count"] == 1
    text = Path(out["rejections"]["path"]).read_text(encoding="utf-8")
    assert "Triage fail b0" in text and "Triage fail a0" not in text


def test_second_render_pairs_suffix(workspace):
    cfg = _setup(workspace)
    seed_topic_study()   # a report needs a new study
    first = api.render_report(cfg, EMPTY, rejections=True)
    before = Path(first["rejections"]["path"]).read_text(encoding="utf-8")
    seed_topic_study()   # a report needs a new study
    second = api.render_report(cfg, EMPTY, rejections=True)
    assert Path(first["rejections"]["path"]).name.endswith("-rejected.md")
    assert Path(second["rejections"]["path"]).name.endswith("-rejected-2.md")
    assert Path(first["rejections"]["path"]).read_text(encoding="utf-8") == before


def test_rejections_error_keeps_exit_and_report(workspace):
    cfg = _setup(workspace)
    reports = cfg.reports_dir
    reports.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (reports / f"{day}-rejected.md").write_text("existing", encoding="utf-8")
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, EMPTY, rejections=True)
    assert "rejections_error" in out and "rejections" not in out
    assert Path(out["path"]).exists() and isinstance(out["rejected"], list)


def test_any_rejections_failure_is_rejections_error(workspace, monkeypatch):
    cfg = _setup(workspace)

    def boom(*a, **k):
        raise TypeError("boom")
    monkeypatch.setattr(api.rejections_render, "rows_in_window", boom)
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, EMPTY, rejections=True)
    assert "boom" in out["rejections_error"] and Path(out["path"]).exists()


def test_cli_rejections_error_still_exits_0(workspace, monkeypatch, tmp_path, capsys):
    _setup(workspace)

    def boom(*a, **k):
        raise TypeError("boom")
    monkeypatch.setattr(api.rejections_render, "rows_in_window", boom)
    f = tmp_path / "r.json"
    f.write_text(EMPTY, encoding="utf-8")
    seed_topic_study()   # a report needs a new study
    assert cli.main(["report", "--render", str(f), "--rejections"]) == 0
    assert "rejections_error" in capsys.readouterr().out


def test_cli_rejections_without_render_exit_2(workspace, capsys):
    _setup(workspace)
    assert cli.main(["report", "--rejections"]) == 2
    assert "--rejections goes only with --render" in capsys.readouterr().err


def test_rejections_page_crumbs_link_the_overview_and_topic_page(workspace):
    import re
    cfg = _setup(workspace)
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, EMPTY, rejections=True)
    page = Path(out["rejections"]["html"])
    nav = page.read_text(encoding="utf-8").split('<nav class="ee-crumbs"', 1)[1].split("</nav>", 1)[0]
    targets = [(page.parent / h).resolve() for h in re.findall(r'href="([^"]+)"', nav)]
    assert targets == [(cfg.overview_dir / "overview.html").resolve(),
                       (cfg.reports_dir / f"{cfg.topic.id}.html").resolve()]
