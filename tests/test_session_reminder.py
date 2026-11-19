from pathlib import Path
import os
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import load_script

session_env = load_script("hooks/session_env.py")

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def iso(**delta):
    return (NOW + timedelta(**delta)).isoformat()


def write_status(data, **status):
    folder = data / "scheduling"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(json.dumps(status), encoding="utf-8")


def topic(**kw):
    return {"next_report": None, "last_report_ok": None, "last_failure": None, **kw}


def test_overdue_report(tmp_path):
    write_status(tmp_path, topics={"seo": topic(next_report=iso(days=-3), last_report_ok=iso(days=-10))})
    assert session_env.reminder(tmp_path, NOW) == \
        "Evidence Echo: seo report is 3 days overdue — run /evidence-echo:report?"


def test_not_overdue_within_12_hours_or_after_a_later_ok_run(tmp_path):
    write_status(tmp_path, topics={"seo": topic(next_report=iso(hours=-11)),
                                   "cro": topic(next_report=iso(days=-3), last_report_ok=iso(days=-2))})
    assert session_env.reminder(tmp_path, NOW) is None


def test_several_overdue_topics_in_one_line(tmp_path):
    write_status(tmp_path, topics={"seo": topic(next_report=iso(days=-3)), "cro": topic(next_report=iso(hours=-20))})
    text = session_env.reminder(tmp_path, NOW)
    assert text == "Evidence Echo: cro (20 hours), seo (3 days) reports are overdue — run /evidence-echo:report?"


def test_last_failure_newer_than_the_last_ok(tmp_path):
    write_status(tmp_path, topics={"seo": topic(last_report_ok=iso(days=-5),
                                                last_failure={"at": iso(days=-1), "error": "budget exceeded"}),
                                   "cro": topic(last_report_ok=iso(days=-1),
                                                last_failure={"at": iso(days=-5), "error": "old"})})
    assert session_env.reminder(tmp_path, NOW) == "Evidence Echo: last scheduled seo report failed: budget exceeded"


def test_scheduler_seems_stopped(tmp_path):
    write_status(tmp_path, topics={}, installed=True, next_wake=iso(hours=-3), last_wake=iso(days=-4))
    assert session_env.reminder(tmp_path, NOW) == f"Evidence Echo: the scheduler last ran {(NOW - timedelta(days=4)).date()}"
    write_status(tmp_path, topics={}, installed=True, next_wake=iso(hours=-3), last_wake=None)
    assert session_env.reminder(tmp_path, NOW).endswith("last ran never")
    write_status(tmp_path, topics={}, installed=True, next_wake=iso(hours=-1), last_wake=None)
    assert session_env.reminder(tmp_path, NOW) is None                  # only 1 h late
    write_status(tmp_path, topics={}, installed=True, next_wake=iso(hours=-3), last_wake=iso(hours=-2))
    assert session_env.reminder(tmp_path, NOW) is None                  # it ran after the wake time
    write_status(tmp_path, topics={}, installed=False, next_wake=iso(hours=-3), last_wake=None)
    assert session_env.reminder(tmp_path, NOW) is None                  # not installed: nothing to expect


@pytest.mark.parametrize("content", ["{broken", "[1, 2]", '{"topics": [1]}', '{"topics": {"seo": 5}}',
                                     '{"topics": {"seo": {"next_report": "yesterday"}}}'])
def test_corrupt_status_gives_none_and_no_exception(tmp_path, content):
    folder = tmp_path / "scheduling"
    folder.mkdir()
    (folder / "status.json").write_text(content, encoding="utf-8")
    assert session_env.reminder(tmp_path, NOW) is None


def test_no_status_file(tmp_path):
    assert session_env.reminder(tmp_path, NOW) is None


def test_runtime_json_is_written_when_scheduling_is_in_use(tmp_path):
    env = {"CLAUDE_PLUGIN_ROOT": "/cache/ee/0.18", "CLAUDE_PLUGIN_DATA": "/pdata"}
    session_env.write_runtime(env, tmp_path)
    assert not (tmp_path / "scheduling").exists()                          # scheduling not used: nothing written
    (tmp_path / "scheduling").mkdir()
    session_env.write_runtime(env, tmp_path)
    runtime = json.loads((tmp_path / "scheduling" / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["plugin_root"] == "/cache/ee/0.18" and runtime["plugin_data"] == "/pdata"
    assert runtime["python"] == sys.executable and runtime["runner_args"] == []


def test_runtime_keeps_runner_args_and_a_known_claude(tmp_path, monkeypatch):
    (tmp_path / "scheduling").mkdir()
    (tmp_path / "scheduling" / "runtime.json").write_text(
        json.dumps({"runner_args": ["--plugin-dir", "R"], "claude": "/old/claude"}), encoding="utf-8")
    monkeypatch.setattr(session_env.shutil, "which", lambda name, **kw: None)
    session_env.write_runtime({"CLAUDE_PLUGIN_ROOT": "/new"}, tmp_path)
    runtime = json.loads((tmp_path / "scheduling" / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["runner_args"] == ["--plugin-dir", "R"] and runtime["claude"] == "/old/claude"
    assert runtime["plugin_root"] == "/new"
    monkeypatch.setattr(session_env.shutil, "which", lambda name, **kw: "/new/claude")
    session_env.write_runtime({"CLAUDE_PLUGIN_ROOT": "/new"}, tmp_path)
    assert json.loads((tmp_path / "scheduling" / "runtime.json").read_text(encoding="utf-8"))["claude"] == "/new/claude"


def test_runtime_is_not_overwritten_without_a_plugin_root(tmp_path):
    (tmp_path / "scheduling").mkdir()
    path = tmp_path / "scheduling" / "runtime.json"
    path.write_text('{"plugin_root": "/good"}', encoding="utf-8")
    session_env.write_runtime({}, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"plugin_root": "/good"}


def test_hook_prints_the_reminder_and_never_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(session_env, "_default_home", lambda: tmp_path / "home")
    data = tmp_path / "home" / ".evidence-echo"
    data.mkdir(parents=True)
    (data / "config.json").write_text('{"version": 1}', encoding="utf-8")      # set up: no first-run hint
    now = datetime.now(timezone.utc)
    write_status(data, topics={"seo": topic(next_report=(now - timedelta(days=3)).isoformat())})
    env = {"CLAUDE_PLUGIN_ROOT": "/root", "CLAUDE_PLUGIN_DATA": str(tmp_path / "pd")}
    assert session_env.main(env, session_id="s1") == 0
    out = json.loads(capsys.readouterr().out)
    assert "seo report is 3 days overdue" in out["hookSpecificOutput"]["additionalContext"]
    (data / "scheduling" / "status.json").write_text("{broken", encoding="utf-8")
    assert session_env.main(env, session_id="s1") == 0
    assert capsys.readouterr().out == ""


def test_failed_message_and_fetch_are_worded_by_kind(tmp_path):
    write_status(tmp_path, topics={
        "seo": topic(last_report_ok=iso(hours=-1),
                     last_failure={"at": iso(hours=-2), "error": "chat not found", "kind": "notify"}),
        "cro": topic(last_report_ok=iso(hours=-1),
                     last_failure={"at": iso(hours=-3), "error": "feed down", "kind": "fetch"})})
    assert session_env.reminder(tmp_path, NOW) == (
        "Evidence Echo: last scheduled cro fetch failed: feed down\n"
        "Evidence Echo: the seo report message could not be sent: chat not found")


def test_a_launcher_failure_from_the_run_log_is_shown(tmp_path):
    write_status(tmp_path, topics={}, last_wake=iso(days=-2))
    row = {"topic": None, "kind": "scheduler", "outcome": "failed", "started": iso(hours=-1),
           "finished": iso(hours=-1), "error": "plugin folder moved \u2014 open Claude Code once to refresh"}
    (tmp_path / "scheduling" / "runs.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert session_env.reminder(tmp_path, NOW).startswith("Evidence Echo: the scheduler could not run: plugin folder moved")


def test_a_claude_from_the_current_folder_is_never_recorded(tmp_path, monkeypatch):
    project = tmp_path / "project"; project.mkdir()
    (project / "claude.cmd").write_text("@echo planted", encoding="utf-8")
    (project / "claude").write_text("#!/bin/sh\necho planted", encoding="utf-8")
    (project / "claude").chmod(0o755)
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(project), ""]))
    assert session_env.find_claude() is None
    real = tmp_path / "bin"; real.mkdir()
    for name in ("claude.cmd", "claude"):
        (real / name).write_text("x", encoding="utf-8"); (real / name).chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(real)]))
    found = session_env.find_claude()
    assert found and Path(found).parent == real


def test_a_relative_claude_in_an_old_runtime_file_is_dropped(tmp_path, monkeypatch):
    (tmp_path / "scheduling").mkdir()
    (tmp_path / "scheduling" / "runtime.json").write_text(json.dumps({"claude": ".\\claude.CMD"}), encoding="utf-8")
    monkeypatch.setattr(session_env, "find_claude", lambda: None)
    session_env.write_runtime({"CLAUDE_PLUGIN_ROOT": "/new"}, tmp_path)
    assert json.loads((tmp_path / "scheduling" / "runtime.json").read_text(encoding="utf-8"))["claude"] == ""
