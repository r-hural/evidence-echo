import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, store
from tests.conftest import PLUGIN_DIR, make_item, make_workspace, seed_topic_study


def _seed():
    conn = store.connect(config.load().db_path)
    store.insert_items(conn, [make_item()])
    conn.close()


def test_templates_works_unconfigured(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    assert cli.main(["templates"]) == 0
    assert "seo" in [t["id"] for t in json.loads(capsys.readouterr().out)]


@pytest.mark.parametrize("cmd", [["fetch"], ["triage"], ["judge"], ["verify"], ["extract"],
                                 ["embed"], ["report"], ["reset", "--stage", "triage"],
                                 ["apply", "--stage", "triage", "x.json"],
                                 ["import-legacy", "x.json"], ["sources", "doctor"]])
def test_pipeline_commands_exit_4_when_not_set_up(cmd, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    assert cli.main(cmd) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Evidence Echo is not set up — run the setup (/evidence-echo:setup)" in captured.err


def test_several_topics_exit_2_listing_them(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("seo", "diet"))
    assert cli.main(["triage"]) == 2
    assert "several topics — pass --topic <id>: diet, seo" in capsys.readouterr().err


def test_topic_option_selects_topic(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("seo", "diet"))
    conn = store.connect(tmp_path / "topics" / "diet" / "echo.db")
    store.insert_items(conn, [make_item()])
    conn.close()
    assert cli.main(["--topic", "diet", "triage"]) == 0
    assert json.loads(capsys.readouterr().out)["topic"]["id"] == "diet"


def test_unknown_topic_exits_2(workspace, capsys):
    assert cli.main(["--topic", "nope", "triage"]) == 2
    assert "Unknown topic 'nope'" in capsys.readouterr().err


def test_status_prints_api_result(workspace, capsys):
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == api.status_all(config.workspace())


def test_triage_session_mode_prints_emit_payload(workspace, capsys):
    _seed()
    assert cli.main(["triage"]) == 0  # no ANTHROPIC_API_KEY -> session mode
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "triage" and payload["topic"]["id"] == "seo"


def test_apply_prints_applied_and_rejected(workspace, tmp_path, capsys):
    _seed()
    cli.main(["triage", "--emit"])
    oid = json.loads(capsys.readouterr().out)["items"][0]["id"]
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"results": [
        {"id": oid, "pass": True, "reason": "ok"},
        {"id": "i999", "pass": True, "reason": "x"}]}), encoding="utf-8")
    assert cli.main(["apply", "--stage", "triage", str(results)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] == 1 and out["rejected"][0]["id"] == "i999"


def test_malformed_payload_exits_1(workspace, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert cli.main(["apply", "--stage", "judge", str(bad)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "not valid JSON" in captured.err


def test_missing_results_file_exits_1(workspace, tmp_path, capsys):
    assert cli.main(["apply", "--stage", "judge", str(tmp_path / "missing.json")]) == 1
    assert "missing.json" in capsys.readouterr().err


def test_bad_fetch_horizon_days_exits_2(workspace, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_FETCH_HORIZON_DAYS", "abc")
    assert cli.main(["extract"]) == 2
    assert "EV_ECHO_FETCH_HORIZON_DAYS" in capsys.readouterr().err


def test_python_m_exit_code(tmp_path):
    env = {**os.environ, "EV_ECHO_DATA_DIR": str(tmp_path / "data"),
           "PYTHONPATH": str(PLUGIN_DIR / "src")}
    proc = subprocess.run([sys.executable, "-m", "evidence_echo", "extract"],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 4
    assert "not set up" in proc.stderr


def test_ruling_flag_only_for_verify(workspace, tmp_path, capsys):
    f = tmp_path / "r.json"
    f.write_text('{"results": []}', encoding="utf-8")
    assert cli.main(["apply", "--stage", "judge", "--ruling", str(f)]) == 1
    assert "--ruling" in capsys.readouterr().err
    assert cli.main(["apply", "--stage", "verify", "--ruling", str(f)]) == 0
    assert json.loads(capsys.readouterr().out) == {"applied": 0, "rejected": []}


def test_sources_doctor_prints_api_result(workspace, monkeypatch, capsys):
    monkeypatch.setattr(api, "sources_doctor", lambda cfg: {"topic": "seo", "sources": []})
    assert cli.main(["sources", "doctor"]) == 0
    assert json.loads(capsys.readouterr().out) == {"topic": "seo", "sources": []}


def test_status_and_overview_work_unconfigured(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["topics"] == []
    assert cli.main(["overview"]) == 0
    assert "No topics yet" in (tmp_path / "reports" / "overview.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("cmd", [["setup", "status"], ["config", "show"], ["templates"],
                                 ["topic", "list"], ["status"], ["overview"]])
def test_commands_allowed_without_setup(cmd, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    assert cli.main(cmd) == 0


def test_usage_error_is_exit_2(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["nonsense"])
    assert exc.value.code == 2


def test_render_exits_0_when_only_the_overview_fails(workspace, tmp_path, monkeypatch, capsys):
    results = tmp_path / "results.json"
    results.write_text('{"results": []}', encoding="utf-8")

    def broken(*a, **kw):
        raise PermissionError("overview.md is locked")

    monkeypatch.setattr(api, "overview", broken)
    seed_topic_study()   # a report needs a new study
    assert cli.main(["report", "--render", str(results)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overview_error"] == "overview.md is locked" and "overview" not in out


def test_database_error_exits_1_without_traceback(workspace, monkeypatch, capsys):
    (workspace / "topics" / "seo" / "echo.db").write_bytes(b"this is not a database" * 50)
    assert cli.main(["--topic", "seo", "fetch"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("evidence-echo: ") and "not a database" in err


def test_fetch_fallback_emit_and_apply_choices(workspace, capsys):
    assert cli.main(["fetch-fallback", "emit"]) == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "fetch"


def test_match_is_a_stage():
    assert cli._parser().parse_args(["match", "--emit"]).cmd == "match"
    assert cli._parser().parse_args(["apply", "--stage", "match", "f.json"]).stage == "match"


def test_studies_commands_exit_codes(workspace, capsys):
    assert cli.main(["studies", "list"]) == 0
    assert cli.main(["studies", "duplicates", "--count"]) == 0
    assert cli.main(["studies", "merge", "1", "1"]) == 2
    assert cli.main(["studies", "unmerge", "5"]) == 2


def test_sources_set_needs_an_option(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("seo",))
    assert cli.main(["sources", "set", "R"]) == 2


def test_fetch_all_flag_parses():
    assert cli._parser().parse_args(["fetch", "--all"]).fetch_all is True
    assert cli._parser().parse_args(["fetch"]).fetch_all is False


def test_report_render_rejections_flag(workspace, tmp_path, capsys):
    results = tmp_path / "results.json"
    results.write_text('{"results": []}', encoding="utf-8")
    seed_topic_study()   # a report needs a new study
    assert cli.main(["report", "--render", str(results), "--rejections"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rejections"]["count"] == 0 and Path(out["rejections"]["path"]).exists()


def test_cli_studies_similarity(workspace, monkeypatch, capsys):
    monkeypatch.setattr(api, "studies_similarity", lambda cfg: {"skipped": "no embedder"})
    assert cli.main(["studies", "similarity"]) == 0
    assert json.loads(capsys.readouterr().out) == {"skipped": "no embedder"}
