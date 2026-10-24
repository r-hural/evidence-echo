# tests/test_front_end_wiring.py
import json
import sqlite3
from pathlib import Path

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, overview as overview_mod, settings
from evidence_echo.render import html, model, obsidian
from tests.conftest import seed_topic_study

EMPTY = json.dumps({"results": []})


@pytest.fixture(autouse=True)
def _new_study(request):
    """Every render here has one new study (a run without new studies writes no report)."""
    if "workspace" in request.fixturenames:
        request.getfixturevalue("workspace")
        seed_topic_study()


@pytest.fixture
def calls(monkeypatch):
    """Replace the three front-end functions with recorders returning a marker dict."""
    seen = {"write_report": [], "write_overview": [], "export": []}

    def write_report(report, topic, reports_dir, overview_dir, md_path, sources_page=None):
        seen["write_report"].append(dict(report=report, topic=topic, reports_dir=reports_dir,
                                         overview_dir=overview_dir, md_path=md_path,
                                         sources_page=sources_page))
        return {"report": str(md_path.with_suffix(".html"))}

    def write_overview(summaries, overview_dir, now):
        seen["write_overview"].append(dict(summaries=summaries, overview_dir=overview_dir, now=now))
        return {"overview": str(overview_dir / "overview.html")}

    def export(topic, vault_dir):
        seen["export"].append(dict(topic=topic, vault_dir=vault_dir))
        return {"dir": str(vault_dir), "written": 1, "removed": 0}

    monkeypatch.setattr(html, "write_report", write_report)
    monkeypatch.setattr(html, "write_overview", write_overview)
    monkeypatch.setattr(obsidian, "export", export)
    return seen


def _vault(workspace, tmp_path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    api.config_set(config.workspace(), "obsidian_dir", str(vault))
    return vault


def test_html_on_calls_both_writers_with_the_expected_arguments(workspace, calls):
    cfg = config.load()
    out = api.render_report(cfg, EMPTY)
    [rep] = calls["write_report"]
    assert isinstance(rep["report"], model.Report) and rep["report"].topic_id == "seo"
    assert isinstance(rep["topic"], model.Topic) and rep["topic"].topic_id == "seo"
    assert rep["reports_dir"] == cfg.reports_dir and rep["overview_dir"] == cfg.overview_dir
    assert rep["md_path"] == Path(out["path"])
    assert rep["sources_page"].topic_id == "seo"                        # the Sources page is built too
    [ov] = calls["write_overview"]
    assert ov["overview_dir"] == cfg.overview_dir
    assert [s.id for s in ov["summaries"]] == ["seo"]
    assert isinstance(ov["summaries"][0], overview_mod.TopicSummary)
    assert ov["summaries"][0].report["path"] == out["path"]            # today's report is latest
    assert out["html"] == {"report": str(Path(out["path"]).with_suffix(".html")),
                           "overview": str(cfg.overview_dir / "overview.html")}
    assert calls["export"] == [] and "obsidian" not in out             # obsidian_dir unset
    assert Path(out["overview"]).name == "overview.md"


def test_html_off_skips_html(workspace, calls):
    settings.save_global(workspace, {"html_reports": "off"})
    out = api.render_report(config.load(), EMPTY)
    assert calls["write_report"] == [] and calls["write_overview"] == []
    assert "html" not in out and "html_error" not in out
    assert Path(out["path"]).exists() and Path(out["overview"]).exists()


def test_obsidian_export_gets_the_topic_model_and_the_vault(workspace, tmp_path, calls):
    vault = _vault(workspace, tmp_path)
    out = api.render_report(config.load(), EMPTY)
    [ex] = calls["export"]
    assert isinstance(ex["topic"], model.Topic) and ex["vault_dir"] == vault
    assert ex["topic"] is calls["write_report"][0]["topic"]           # built once per render
    assert out["obsidian"] == {"dir": str(vault), "written": 1, "removed": 0}


def test_obsidian_runs_with_html_off(workspace, tmp_path, calls):
    _vault(workspace, tmp_path)
    settings.save_global(workspace, {**config.workspace().settings, "html_reports": "off"})
    out = api.render_report(config.load(), EMPTY)
    assert calls["write_report"] == [] and len(calls["export"]) == 1 and "obsidian" in out


def test_html_output_lists_the_written_pages(workspace, tmp_path):
    _vault(workspace, tmp_path)
    out = api.render_report(config.load(), EMPTY)
    assert set(out) == {"path", "applied", "rejected", "overview", "html", "obsidian"}
    assert set(out["html"]) == {"report", "topic", "overview", "sources"}
    assert Path(out["html"]["report"]).is_file() and Path(out["html"]["topic"]).is_file()
    assert set(out["obsidian"]) == {"dir", "written", "removed"}


@pytest.mark.parametrize("error", [OSError("disk full"), sqlite3.OperationalError("locked"),
                                   ValueError("bad value")])
def test_html_error_is_reported_and_the_report_kept(workspace, tmp_path, calls, monkeypatch, error):
    def broken(*a, **kw):
        raise error

    _vault(workspace, tmp_path)
    monkeypatch.setattr(html, "write_report", broken)
    out = api.render_report(config.load(), EMPTY)
    assert out["html_error"] == str(error) and "html" not in out
    assert Path(out["path"]).exists() and "overview" in out
    assert len(calls["export"]) == 1 and "obsidian" in out             # obsidian still runs


@pytest.mark.parametrize("error", [OSError("vault is read-only"), ValueError("bad name")])
def test_obsidian_error_is_reported_and_the_report_kept(workspace, tmp_path, calls, monkeypatch,
                                                        error):
    def broken(*a, **kw):
        raise error

    _vault(workspace, tmp_path)
    monkeypatch.setattr(obsidian, "export", broken)
    out = api.render_report(config.load(), EMPTY)
    assert out["obsidian_error"] == str(error) and "obsidian" not in out
    assert Path(out["path"]).exists() and "html" in out


def test_topic_model_failure_is_an_html_error(workspace, calls, monkeypatch):
    def broken(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(model, "build_topic", broken)
    out = api.render_report(config.load(), EMPTY)
    assert out["html_error"] == "database is locked" and calls["write_report"] == []


def test_cli_exits_0_when_html_and_obsidian_fail(workspace, tmp_path, calls, monkeypatch, capsys):
    def broken(*a, **kw):
        raise PermissionError("locked")

    _vault(workspace, tmp_path)
    monkeypatch.setattr(html, "write_report", broken)
    monkeypatch.setattr(obsidian, "export", broken)
    results = tmp_path / "results.json"
    results.write_text(EMPTY, encoding="utf-8")
    assert cli.main(["report", "--render", str(results)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["html_error"] == "locked" and out["obsidian_error"] == "locked"
    assert Path(out["path"]).exists()


def test_a_deleted_vault_becomes_an_obsidian_error(workspace, tmp_path, monkeypatch):
    def export(topic, vault_dir):  # what SP5a-O does: the vault must exist
        if not vault_dir.is_dir():
            raise OSError(f"obsidian_dir {vault_dir} does not exist")
        return {"dir": str(vault_dir)}

    vault = _vault(workspace, tmp_path)
    vault.rmdir()
    monkeypatch.setattr(obsidian, "export", export)
    out = api.render_report(config.load(), EMPTY)
    assert "does not exist" in out["obsidian_error"] and Path(out["path"]).exists()


def test_overview_writes_html_when_on(workspace, calls):
    out = api.overview(config.workspace())
    [ov] = calls["write_overview"]
    assert ov["overview_dir"] == workspace / "reports"
    assert [s.id for s in ov["summaries"]] == ["seo"]
    assert out["html"] == {"overview": str(workspace / "reports" / "overview.html")}


def test_overview_skips_html_when_off(workspace, calls, monkeypatch):
    monkeypatch.setenv("EV_ECHO_HTML_REPORTS", "off")
    out = api.overview(config.workspace())
    assert calls["write_overview"] == [] and set(out) == {"path"}


def test_overview_html_error_is_reported(workspace, monkeypatch):
    def broken(*a, **kw):
        raise OSError("overview.html is locked")

    monkeypatch.setattr(html, "write_overview", broken)
    out = api.overview(config.workspace())
    assert out["html_error"] == "overview.html is locked" and Path(out["path"]).exists()


def test_render_writes_overview_html_once(workspace, calls):
    api.render_report(config.load(), EMPTY)
    assert len(calls["write_overview"]) == 1         # not again inside overview()


def test_overview_works_unconfigured(tmp_path, monkeypatch, calls):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    out = api.overview(config.workspace())
    assert Path(out["path"]).exists() and calls["write_overview"][0]["summaries"] == []


def test_renderer_bug_is_an_html_error_and_cli_exits_0(workspace, tmp_path, calls, monkeypatch, capsys):
    def broken(*a, **kw):
        raise KeyError("tier")

    monkeypatch.setattr(html, "write_report", broken)
    out = api.render_report(config.load(), EMPTY)
    assert "tier" in out["html_error"] and Path(out["path"]).exists()
    results = tmp_path / "results.json"
    results.write_text(EMPTY, encoding="utf-8")
    seed_topic_study()                       # the second render needs a new study of its own
    assert cli.main(["report", "--render", str(results)]) == 0
    assert "html_error" in json.loads(capsys.readouterr().out)


def test_overview_failure_keeps_the_report_page_result(workspace, calls, monkeypatch):
    def broken(*a, **kw):
        raise OSError("overview locked")

    monkeypatch.setattr(html, "write_overview", broken)
    out = api.render_report(config.load(), EMPTY)
    assert out["html_error"] == "overview locked" and "report" in out["html"]


def test_non_json_html_result_is_an_html_error_and_cli_exits_0(workspace, tmp_path, calls,
                                                               monkeypatch, capsys):
    monkeypatch.setattr(html, "write_report", lambda *a, **kw: {"report": Path("x.html")})
    results = tmp_path / "results.json"
    results.write_text(EMPTY, encoding="utf-8")
    assert cli.main(["report", "--render", str(results)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "html_error" in out and Path(out["path"]).exists()


def test_non_json_obsidian_result_is_an_obsidian_error_and_cli_exits_0(workspace, tmp_path, calls,
                                                                       monkeypatch, capsys):
    _vault(workspace, tmp_path)
    monkeypatch.setattr(obsidian, "export", lambda *a, **kw: {"dir": Path("v")})
    results = tmp_path / "results.json"
    results.write_text(EMPTY, encoding="utf-8")
    assert cli.main(["report", "--render", str(results)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "obsidian_error" in out and "obsidian" not in out and Path(out["path"]).exists()
