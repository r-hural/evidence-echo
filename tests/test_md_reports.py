"""md_reports: off keeps the Markdown files out of the reports folder (in <data>/markdown), as long
as HTML is on; Claude still reads them there."""
import json
from contextlib import closing
from pathlib import Path

import pytest

from evidence_echo import api
from evidence_echo.core import config, settings, store
from evidence_echo.core.settings import ConfigError
from tests.conftest import seed_topic_study

EMPTY = json.dumps({"results": []})


def _render(**kw):
    seed_topic_study()   # a report needs a new study
    return api.render_report(config.load(), EMPTY, **kw)


def _md_off():
    api.config_set(config.workspace(), "md_reports", "off")


def test_setting_defaults_on_and_is_workspace_wide():
    assert settings.GLOBAL_DEFAULTS["md_reports"] == "on"
    assert "md_reports" not in settings.OVERRIDES
    assert settings.check_value("md_reports", "off", "f") == "off"
    with pytest.raises(ConfigError, match="md_reports"):
        settings.check_value("md_reports", "no", "f")


def test_md_on_writes_the_report_into_the_reports_folder(workspace):
    cfg = config.load()
    out = _render()
    assert Path(out["path"]).parent == cfg.reports_dir
    assert Path(out["overview"]) == cfg.overview_dir / "overview.md"


def test_md_off_moves_the_markdown_and_keeps_the_html(workspace):
    _md_off()
    cfg = config.load()
    out = _render()
    md = Path(out["path"])
    assert md.parent == workspace / "markdown" / "seo" and md.is_file()   # Claude reads it here
    assert not list(cfg.reports_dir.glob("*.md"))
    assert Path(out["html"]["report"]) == cfg.reports_dir / (md.stem + ".html")
    assert Path(out["overview"]) == workspace / "markdown" / "overview.md"
    assert not (cfg.overview_dir / "overview.md").exists()
    assert (cfg.overview_dir / "overview.html").is_file()
    with closing(store.connect(cfg.db_path)) as conn:      # recorded under its reports-folder name
        assert store.latest_report(conn)["path"] == str(cfg.reports_dir / md.name)


def test_md_off_links_point_at_the_html_report(workspace):
    _md_off()
    cfg = config.load()
    out = _render()
    html_name = Path(out["path"]).stem + ".html"
    assert html_name in Path(out["overview"]).read_text(encoding="utf-8")
    assert html_name in Path(out["html"]["topic"]).read_text(encoding="utf-8")
    assert html_name in (cfg.overview_dir / "overview.html").read_text(encoding="utf-8")


def test_md_off_with_html_off_keeps_the_markdown_visible(workspace):
    _md_off()
    api.config_set(config.workspace(), "html_reports", "off")
    cfg = config.load()
    out = _render()
    assert Path(out["path"]).parent == cfg.reports_dir
    assert Path(out["overview"]) == cfg.overview_dir / "overview.md"


def test_md_off_same_day_reports_never_share_an_html_page(workspace):
    _md_off()
    first, second = _render(), _render()
    assert Path(first["html"]["report"]) != Path(second["html"]["report"])
    assert Path(first["html"]["report"]).is_file() and Path(second["html"]["report"]).is_file()


def test_md_back_on_never_overwrites_an_html_page_without_markdown(workspace):
    _md_off()
    first = _render()
    api.config_set(config.workspace(), "md_reports", "on")
    second = _render()
    assert Path(second["path"]).parent == config.load().reports_dir
    assert Path(second["html"]["report"]) != Path(first["html"]["report"])


def test_md_off_removes_a_stale_overview_md(workspace):
    cfg = config.load()
    _render()
    assert (cfg.overview_dir / "overview.md").is_file()
    _md_off()
    _render()
    assert not (cfg.overview_dir / "overview.md").exists()


def test_md_off_rejections_follow_the_report(workspace):
    _md_off()
    cfg = config.load()
    out = _render(rejections=True)
    rej = out["rejections"]
    assert Path(rej["path"]).parent == workspace / "markdown" / "seo" and Path(rej["path"]).is_file()
    assert Path(rej["html"]).parent == cfg.reports_dir
    assert Path(out["html"]["report"]).name in Path(rej["html"]).read_text(encoding="utf-8")
