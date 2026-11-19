import json
import sys
import time
import os
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

import httpx
import pytest

from evidence_echo import cli
from evidence_echo.notify import commands as notify_commands
from evidence_echo.scheduling import lock, run_due, runlog, runner, spec
from evidence_echo.scheduling.runner import ClaudeRunner, RunResult


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


MON_NOON = utc(2026, 11, 9, 12, 0)        # a Monday, after 08:00
SET_AT = "2026-10-01T00:00:00+00:00"


class FakeRunner:
    """Stands in for claude -p; like the skill it logs an ok report run when told to."""
    def __init__(self, data_dir, result=None, log_ok=True):
        self.data_dir, self.calls, self.result, self.log_ok = data_dir, [], result or RunResult(True, None, 0.42), log_ok
        self.clock = MON_NOON   # the frozen time of the run being tested; go() sets it

    def report(self, topic, data_dir, sf, env):
        self.calls.append({"topic": topic, "budget": sf.api_spend_limit_usd, "env": env})
        if self.log_ok and self.result.ok:
            runlog.append(data_dir, {"topic": topic, "kind": "report", "trigger": "scheduled",
                                     "started": self.clock.isoformat(), "finished": self.clock.isoformat(),
                                     "outcome": "ok"})
        return self.result


class Fetcher:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, topic):
        self.calls.append(topic)
        if topic in self.fail:
            raise RuntimeError("feed down")


def write_runtime(data_dir, data):
    runner.runtime_path(data_dir).parent.mkdir(parents=True, exist_ok=True)
    runner.runtime_path(data_dir).write_text(json.dumps(data), encoding="utf-8")


def setup(tmp_path, **topics):
    """topics: id -> (fetch rule, report rule) or a TopicSchedule."""
    sf = spec.ScheduleFile(timezone="UTC")
    for tid, rules in topics.items():
        if isinstance(rules, spec.TopicSchedule):
            sf.topics[tid] = rules
        else:
            fetch, report = rules
            sf.topics[tid] = spec.TopicSchedule(fetch=spec.parse_rule(fetch, "fetch"),
                                                report=spec.parse_rule(report, "report"), set_at=SET_AT)
    spec.save(tmp_path, sf)
    return sf


def go(tmp_path, now=MON_NOON, runner_=None, fetcher=None, **kw):
    runner_ = runner_ or FakeRunner(tmp_path)
    if isinstance(runner_, FakeRunner):
        runner_.clock = now
    return run_due.run_due(tmp_path, now, runner_, fetcher or Fetcher(), **kw)


def test_a_weekly_report_missed_for_three_weeks_runs_once(tmp_path):
    """Review Focus 2."""
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    fake = FakeRunner(tmp_path)
    out = go(tmp_path, utc(2026, 11, 9, 12), fake)
    assert [r["kind"] for r in out["ran"]] == ["report"] and len(fake.calls) == 1
    out = go(tmp_path, utc(2026, 11, 9, 13), fake)                      # the same slot: nothing owed
    assert out["ran"] == [] and len(fake.calls) == 1


def test_missed_fetch_slots_collapse_into_one_fetch_run(tmp_path):
    setup(tmp_path, seo=("daily 07:00", "off"))
    fetcher = Fetcher()
    out = go(tmp_path, utc(2026, 11, 14, 12), fetcher=fetcher)           # 6 weeks of daily slots missed
    assert fetcher.calls == ["seo"] and [r["kind"] for r in out["ran"]] == ["fetch"]
    assert go(tmp_path, utc(2026, 11, 14, 13), fetcher=fetcher)["ran"] == [] and len(fetcher.calls) == 1


def test_report_slot_beats_a_fetch_slot_and_settles_it(tmp_path):
    setup(tmp_path, seo=("daily 07:00", "weekly mon 08:00"))
    fetcher, fake = Fetcher(), FakeRunner(tmp_path)
    out = go(tmp_path, MON_NOON, fake, fetcher)
    assert [r["kind"] for r in out["ran"]] == ["report"] and fetcher.calls == []
    assert go(tmp_path, utc(2026, 11, 9, 14), fake, fetcher)["ran"] == []   # the report run fetched too


def test_a_fetch_slot_alone_runs_a_fetch(tmp_path):
    setup(tmp_path, seo=("daily 07:00", "weekly mon 08:00"))
    fetcher, fake = Fetcher(), FakeRunner(tmp_path)
    go(tmp_path, MON_NOON, fake, fetcher)
    out = go(tmp_path, utc(2026, 11, 10, 8), fake, fetcher)             # Tuesday 08:00: fetch due, report not
    assert [r["kind"] for r in out["ran"]] == ["fetch"] and fetcher.calls == ["seo"] and len(fake.calls) == 1


def test_a_manual_report_on_saturday_does_not_stop_mondays_slot(tmp_path):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    runlog.append(tmp_path, {"topic": "seo", "kind": "report", "trigger": "manual", "outcome": "ok",
                             "started": "2026-11-07T10:00:00+00:00", "finished": "2026-11-07T10:05:00+00:00"})
    fake = FakeRunner(tmp_path)
    assert [r["kind"] for r in go(tmp_path, MON_NOON, fake)["ran"]] == ["report"]


def test_monthly_report_asks_for_the_long_linkedin_window(tmp_path):
    setup(tmp_path, seo=("off", "monthly last 09:00"))
    fake = FakeRunner(tmp_path)
    go(tmp_path, utc(2026, 11, 30, 12), fake)
    [call] = fake.calls
    assert call["env"] == {"EV_ECHO_SCHEDULED_RUN": "seo", "EV_ECHO_LINKEDIN_WINDOW": "month"}
    assert call["budget"] == 6.0
    other = tmp_path / "weekly"
    other.mkdir()
    weekly = FakeRunner(other)
    setup(other, seo=("off", "weekly mon 08:00"))
    go(other, MON_NOON, weekly)
    assert weekly.calls[0]["env"] == {"EV_ECHO_SCHEDULED_RUN": "seo"}


def test_per_run_report_follows_the_fetch_slot(tmp_path):
    setup(tmp_path, seo=("daily 07:00", "per_run"))
    fetcher, fake = Fetcher(), FakeRunner(tmp_path)
    out = go(tmp_path, MON_NOON, fake, fetcher)
    assert [r["kind"] for r in out["ran"]] == ["report"] and fetcher.calls == []
    assert go(tmp_path, utc(2026, 11, 9, 13), fake, fetcher)["ran"] == []


def test_paused_and_desktop_topics_are_left_alone(tmp_path):
    base = dict(fetch=spec.parse_rule("daily 07:00", "fetch"), report=spec.parse_rule("daily 08:00", "report"),
                set_at=SET_AT)
    setup(tmp_path, a=spec.TopicSchedule(paused=True, **base), b=spec.TopicSchedule(desktop_task=True, **base))
    fetcher, fake = Fetcher(), FakeRunner(tmp_path)
    assert go(tmp_path, MON_NOON, fake, fetcher)["ran"] == [] and fake.calls == [] and fetcher.calls == []


def test_a_held_lock_is_skipped_at_once_and_the_slot_stays_owed(tmp_path):
    setup(tmp_path, seo=("off", "weekly mon 08:00"), cro=("daily 07:00", "off"))
    assert lock.acquire(tmp_path, "seo", pid=os.getpid()) is None       # a live holder: this process
    fake, fetcher = FakeRunner(tmp_path), Fetcher()
    out = run_due.run_due(tmp_path, MON_NOON, fake, fetcher)
    assert out["skipped"] == [{"topic": "seo", "kind": "report", "why": "locked"}] and fake.calls == []
    assert fetcher.calls == ["cro"]                                       # the other topic is not held up
    [row] = [r for r in runlog.read_all(tmp_path) if r["outcome"] == "skipped"]
    assert row["topic"] == "seo"
    lock.release(tmp_path, "seo")
    assert [r["kind"] for r in go(tmp_path, MON_NOON, fake)["ran"]] == ["report"]   # next wake: still due


def test_the_lock_is_released_after_a_run(tmp_path):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    go(tmp_path)
    assert lock.acquire(tmp_path, "seo") is None


def test_a_runner_timeout_is_a_failed_entry_and_not_retried_for_the_slot(tmp_path):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    write_runtime(tmp_path, {"claude": "C:/bin/claude.exe"})

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 3600)
    out = go(tmp_path, runner_=ClaudeRunner(run=slow, detect=lambda *a: "api"))
    assert out["ran"][0]["outcome"] == "failed" and "timed out after 60 min" in out["ran"][0]["error"]
    [row] = [r for r in runlog.read_all(tmp_path) if r["kind"] == "report"]
    assert (row["outcome"], row["trigger"]) == ("failed", "scheduled") and "timed out" in row["error"]
    fake = FakeRunner(tmp_path)
    again = go(tmp_path, utc(2026, 11, 9, 15), fake)
    assert again["skipped"][0]["why"] == "already failed for this slot" and fake.calls == []
    assert [r["kind"] for r in go(tmp_path, utc(2026, 11, 16, 9), fake)["ran"]] == ["report"]   # next slot


def test_claude_runner_command_and_result(tmp_path):
    write_runtime(tmp_path, {"claude": "C:/bin/claude.exe", "runner_args": ["--plugin-dir", "R"]})
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "result", "is_error": False,
                                                               "total_cost_usd": 0.37, "result": "done"}), "")
    result = ClaudeRunner(run=fake_run, detect=lambda *a: "api").report(
        "seo", tmp_path, spec.ScheduleFile(api_spend_limit_usd=2.0), {"EV_ECHO_SCHEDULED_RUN": "seo"})
    assert result == RunResult(True, None, 0.37, "api")
    work = seen["env"]["EV_ECHO_WORK_DIR"]
    assert seen["cmd"] == ["C:/bin/claude.exe", "-p", "/evidence-echo:report --topic seo --unattended",
                           "--permission-mode", "dontAsk",
                           "--allowedTools", "Bash(python *),Bash(python3 *),Bash(py -3 *),Read,Write,WebFetch,Agent,Skill",
                           "--disallowedTools", "PowerShell,Edit,NotebookEdit,WebSearch,Glob,Grep",
                           "--max-budget-usd", "2", "--output-format", "json", "--setting-sources", "user",
                           "--strict-mcp-config",
                           "--append-system-prompt", runner.policy_prompt(work), "--plugin-dir", "R"]
    assert Path(work).as_posix() in runner.policy_prompt(work)
    guard_dir = seen["env"]["EV_ECHO_GUARD_DIR"]
    assert Path(work) == Path(guard_dir) / "work" and Path(guard_dir).parent == tmp_path / "scheduling" / "guard"
    assert seen["env"]["EV_ECHO_SCHEDULED_RUN"] == "seo" and seen["cwd"] == guard_dir and seen["timeout"] == 3600
    assert not Path(work).exists() and not Path(seen["env"]["EV_ECHO_GUARD_DIR"]).exists()   # both removed


@pytest.mark.parametrize("claude", ["claude", r".\claude.CMD", "bin/claude"])
def test_a_relative_claude_path_is_never_run(tmp_path, claude):
    write_runtime(tmp_path, {"claude": claude})
    ran = []
    result = ClaudeRunner(run=lambda cmd, **kw: ran.append(cmd), detect=lambda *a: "api").report(
        "seo", tmp_path, spec.ScheduleFile(), {})
    assert not result.ok and "claude was not found" in result.error and ran == []


def test_a_run_folder_error_fails_the_run_before_claude_starts(tmp_path):
    write_runtime(tmp_path, {"claude": "C:/bin/claude.exe"})
    (tmp_path / "scheduling" / "guard").write_text("a file, not a folder", encoding="utf-8")
    ran = []
    result = ClaudeRunner(run=lambda cmd, **kw: ran.append(cmd), detect=lambda *a: "api").report(
        "seo", tmp_path, spec.ScheduleFile(), {})
    assert not result.ok and "could not create the run folders" in result.error and ran == []


def test_claude_runner_failures(tmp_path):
    write_runtime(tmp_path, {"claude": "C:/bin/claude.exe"})

    def run_with(code, out, err=""):
        return ClaudeRunner(run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, code, out, err),
                            detect=lambda *a: "api").report("seo", tmp_path, spec.ScheduleFile(api_spend_limit_usd=2.0), {})
    bad = run_with(1, "", "not logged in")
    assert not bad.ok and "code 1" in bad.error and "not logged in" in bad.error
    err = run_with(0, json.dumps([{"type": "system"}, {"type": "result", "is_error": True, "result": "budget exceeded",
                                                       "total_cost_usd": 2.1}]))
    assert not err.ok and "budget exceeded" in err.error and err.session_usd == 2.1
    assert not ClaudeRunner(run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
                            detect=lambda *a: "api").report("seo", tmp_path, spec.ScheduleFile(api_spend_limit_usd=2.0), {}).ok


def test_missing_claude_is_a_failed_run(tmp_path):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    out = go(tmp_path, runner_=ClaudeRunner())
    assert out["ran"][0]["outcome"] == "failed" and "claude was not found" in out["ran"][0]["error"]


def test_a_run_that_logged_nothing_is_a_failure(tmp_path):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    out = go(tmp_path, runner_=FakeRunner(tmp_path, log_ok=False))
    assert out["ran"][0]["outcome"] == "failed" and "without logging" in out["ran"][0]["error"]


TRIP = {"limit": "tool_calls", "value": 501, "max": 500, "calls_at_trip": 501}

def test_a_subscription_run_passes_the_ceiling_and_the_guard(tmp_path):
    write_runtime(tmp_path, {"claude": "C:/bin/claude.exe"})
    seen = {}
    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"], guard_exists=Path(kw["env"]["EV_ECHO_GUARD_DIR"]).is_dir())
        return subprocess.CompletedProcess(cmd, 0, '{"total_cost_usd": 1.0}', "")
    result = ClaudeRunner(run=fake_run, detect=lambda *a: "subscription").report("seo", tmp_path, spec.ScheduleFile(), {})
    assert seen["cmd"][seen["cmd"].index("--max-budget-usd") + 1] == "40" and seen["guard_exists"]
    assert result.basis == "subscription" and not Path(seen["env"]["EV_ECHO_GUARD_DIR"]).exists()

def test_a_billed_run_has_the_policy_guard_but_no_loop_limits(tmp_path):
    write_runtime(tmp_path, {"claude": "C:/bin/claude.exe"})
    seen = {}
    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        return subprocess.CompletedProcess(cmd, 0, "{}", "")
    ClaudeRunner(run=fake_run, detect=lambda *a: "api").report("seo", tmp_path, spec.ScheduleFile(), {})
    assert seen["cmd"][seen["cmd"].index("--max-budget-usd") + 1] == "6" and "EV_ECHO_GUARD_DIR" in seen["env"]
    assert not any(k.startswith("EV_ECHO_GUARD_MAX") for k in seen["env"])

@pytest.mark.parametrize("basis,text", [("api", "API spend limit reached ($6)"),
                                        ("subscription", "Session ceiling reached ($40 API-equivalent)")])
def test_reaching_the_dollar_limit_names_the_right_limit(tmp_path, basis, text):
    write_runtime(tmp_path, {"claude": "C:/bin/claude.exe"})
    out = json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True})
    result = ClaudeRunner(run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, out, ""),
                          detect=lambda *a: basis).report("seo", tmp_path, spec.ScheduleFile(), {})
    assert not result.ok and text in result.error

def test_a_trip_survives_a_timeout_and_the_guard_folder_is_removed(tmp_path):
    write_runtime(tmp_path, {"claude": "C:/bin/claude.exe"})
    seen = {}
    def fake_run(cmd, **kw):
        guard = Path(kw["env"]["EV_ECHO_GUARD_DIR"])
        seen["guard"] = guard
        (guard / "trip.json").write_text(json.dumps(TRIP), encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, 3600)
    result = ClaudeRunner(run=fake_run, detect=lambda *a: "subscription").report("seo", tmp_path, spec.ScheduleFile(), {})
    assert not result.ok and result.trip == TRIP and not seen["guard"].exists()

def test_a_guard_trip_is_one_failed_row_with_the_real_cause(tmp_path, monkeypatch):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    sent = []
    monkeypatch.setattr(run_due, "_announce_failure", lambda d, t, e: sent.append(e))
    out = go(tmp_path, runner_=FakeRunner(tmp_path, RunResult(True, None, 3.0, "subscription", TRIP), log_ok=False))
    assert out["ran"][0]["outcome"] == "failed"
    rows = [r for r in runlog.read_all(tmp_path) if r["kind"] == "report"]
    assert len(rows) == 1 and rows[0]["error"].startswith("Stopped by the session guard: 501 tool calls")
    assert rows[0]["api_equivalent_usd"] == 3.0 and sent == [rows[0]["error"]]

@pytest.mark.parametrize("trip", [{"limit": "surprise"}, {"limit": None}, {"limit": "tool_calls", "value": "x"},
                                  {"limit": "total_tokens", "value": "x", "max": 1}])
def test_a_malformed_trip_still_writes_one_failed_row_and_the_message(tmp_path, monkeypatch, trip):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    sent = []
    monkeypatch.setattr(run_due, "_announce_failure", lambda d, t, e: sent.append(e))
    out = go(tmp_path, runner_=FakeRunner(tmp_path, RunResult(True, None, 3.0, "subscription", trip), log_ok=False))
    assert out["ran"][0]["outcome"] == "failed"
    rows = [r for r in runlog.read_all(tmp_path) if r["kind"] == "report"]
    assert len(rows) == 1 and rows[0]["error"].startswith("Stopped by the session guard:")
    assert sent == [rows[0]["error"]]

def test_an_empty_trip_record_counts_as_a_trip(tmp_path, monkeypatch):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    sent = []
    monkeypatch.setattr(run_due, "_announce_failure", lambda d, t, e: sent.append(e))
    out = go(tmp_path, runner_=FakeRunner(tmp_path, RunResult(True, None, 3.0, "subscription", {}), log_ok=False))
    assert out["ran"][0]["outcome"] == "failed" and len(sent) == 1

def test_a_trip_after_a_delivered_report_stays_ok(tmp_path, monkeypatch):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    sent = []
    monkeypatch.setattr(run_due, "_announce_failure", lambda d, t, e: sent.append(e))
    out = go(tmp_path, runner_=FakeRunner(tmp_path, RunResult(True, None, 3.0, "subscription", TRIP)))
    assert out["ran"][0]["outcome"] == "ok" and sent == []
    assert [r["guard_trip"] for r in runlog.read_all(tmp_path) if r["kind"] == "usage"] == [TRIP]

def test_a_trip_after_render_names_the_saved_report(tmp_path, monkeypatch):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    saved = tmp_path / "2026-11-09-report.html"
    monkeypatch.setattr(run_due, "_saved_report", lambda topic, started: saved)
    out = go(tmp_path, runner_=FakeRunner(tmp_path, RunResult(True, None, 3.0, "subscription", TRIP), log_ok=False))
    assert f"The report was saved but not sent: {saved}" in out["ran"][0]["error"]

def _reports_dir(tmp_path, monkeypatch, files):
    """files: name -> mtime offset in seconds from the returned `since` time."""
    folder = tmp_path / "reports"
    folder.mkdir()
    base = time.time() - 1000
    for name, offset in files.items():
        f = folder / name
        f.write_text("x", encoding="utf-8")
        os.utime(f, (base + offset, base + offset))
    monkeypatch.setattr(run_due.config, "load", lambda topic: type("Cfg", (), {"reports_dir": folder})())
    return folder, datetime.fromtimestamp(base, timezone.utc).isoformat()

def test_saved_report_finds_the_first_and_later_reports_of_a_day(tmp_path, monkeypatch):
    folder, since = _reports_dir(tmp_path, monkeypatch, {"2026-11-18-report.html": 10})
    assert run_due._saved_report("seo", since) == folder / "2026-11-18-report.html"
    (tmp_path / "b").mkdir()
    folder, since = _reports_dir(tmp_path / "b", monkeypatch, {"2026-11-18-report-2.html": 10})
    assert run_due._saved_report("seo", since) == folder / "2026-11-18-report-2.html"

def test_saved_report_ignores_old_and_non_report_files(tmp_path, monkeypatch):
    _, since = _reports_dir(tmp_path, monkeypatch, {"2026-11-17-report.html": -50, "seo.html": 10,
                                                    "2026-11-18-report.md": 10, "2026-11-18-report-x.html": 10})
    assert run_due._saved_report("seo", since) is None

def test_saved_report_returns_the_newest(tmp_path, monkeypatch):
    folder, since = _reports_dir(tmp_path, monkeypatch, {"2026-11-18-report.html": 10, "2026-11-18-report-2.html": 30,
                                                         "2026-11-18-report-3.html": 20})
    assert run_due._saved_report("seo", since) == folder / "2026-11-18-report-2.html"

def test_saved_report_is_none_when_the_config_cannot_load(tmp_path, monkeypatch):
    def boom(topic):
        raise RuntimeError("broken")
    monkeypatch.setattr(run_due.config, "load", boom)
    assert run_due._saved_report("seo", MON_NOON.isoformat()) is None

def test_the_saved_report_search_starts_at_this_topics_start_not_the_wake_time(tmp_path, monkeypatch):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    seen = []
    monkeypatch.setattr(run_due, "_saved_report", lambda topic, started: seen.append(started))
    go(tmp_path, runner_=FakeRunner(tmp_path, RunResult(True, None, 3.0, "subscription", TRIP), log_ok=False))
    assert seen and runlog.parse_time(seen[0]) > MON_NOON + timedelta(days=1)

def test_a_failed_trip_row_finishes_at_the_real_time(tmp_path, monkeypatch):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    monkeypatch.setattr(run_due, "_announce_failure", lambda d, t, e: None)
    go(tmp_path, runner_=FakeRunner(tmp_path, RunResult(True, None, 3.0, "subscription", TRIP), log_ok=False))
    [row] = [r for r in runlog.read_all(tmp_path) if r["kind"] == "report"]
    assert row["started"] == MON_NOON.isoformat() and runlog.parse_time(row["finished"]) > MON_NOON + timedelta(days=1)

def test_a_broken_topic_config_never_hides_the_failure(tmp_path):
    assert run_due._saved_report("no-such-topic", MON_NOON.isoformat()) is None

def test_usage_is_logged_under_the_right_name(tmp_path):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    go(tmp_path, runner_=FakeRunner(tmp_path, RunResult(True, None, 0.42, "api")))
    [row] = [r for r in runlog.read_all(tmp_path) if r["kind"] == "usage"]
    assert (row["usage_basis"], row["api_cost_usd"]) == ("api", 0.42) and "cost_usd" not in row


def test_one_topics_failure_does_not_stop_the_others(tmp_path):
    setup(tmp_path, a=("daily 07:00", "off"), b=("daily 07:00", "off"))
    fetcher = Fetcher(fail={"a"})
    out = go(tmp_path, fetcher=fetcher)
    assert [(r["topic"], r["outcome"]) for r in out["ran"]] == [("a", "failed"), ("b", "ok")]
    assert "feed down" in out["ran"][0]["error"]
    assert runlog.last_success(tmp_path, "b", "fetch") and not runlog.last_success(tmp_path, "a", "fetch")


def test_a_corrupt_schedule_file_is_a_stderr_line_and_exit_0(workspace, capsys):
    """Review Focus 4."""
    path = spec.path_of(workspace)
    path.parent.mkdir(parents=True)
    path.write_text("{oops", encoding="utf-8")
    assert cli.main(["run-due"]) == 0
    captured = capsys.readouterr()
    assert "run-due:" in captured.err and "schedule.json" in captured.err
    assert json.loads(captured.out)["errors"]


def test_run_due_writes_the_status_file_and_the_wake(tmp_path):
    setup(tmp_path, seo=("off", "weekly mon 08:00"))
    go(tmp_path)
    status = json.loads((tmp_path / "scheduling" / "status.json").read_text(encoding="utf-8"))
    assert status["last_wake"] == MON_NOON.isoformat()
    assert status["topics"]["seo"]["last_report_ok"]


def test_an_unwritable_log_does_not_stop_the_run(tmp_path):
    setup(tmp_path, seo=("daily 07:00", "off"))
    runlog.path_of(tmp_path).mkdir()                                     # a folder where runs.jsonl should be
    out = go(tmp_path)
    assert out["ran"][0]["kind"] == "fetch"


def test_a_failed_report_slot_still_lets_the_fetches_run(tmp_path):
    setup(tmp_path, seo=("daily 07:00", "weekly mon 08:00"))
    runlog.append(tmp_path, {"topic": "seo", "kind": "report", "trigger": "scheduled",
                             "slot": utc(2026, 11, 9, 8).isoformat(), "started": utc(2026, 11, 9, 8).isoformat(),
                             "finished": utc(2026, 11, 9, 8).isoformat(), "outcome": "failed", "error": "budget"})
    fetcher, fake = Fetcher(), FakeRunner(tmp_path)
    out = go(tmp_path, utc(2026, 11, 10, 12), fake, fetcher)
    assert [r["kind"] for r in out["ran"]] == ["fetch"] and fetcher.calls == ["seo"] and fake.calls == []


TOKEN = "123456:SECRET-token"
LOGIN_EXPIRED = "claude exited with code 1: Failed to authenticate: OAuth session expired"


def telegram_client(sent, ok=True):
    def handler(request):
        sent.append(request)
        if ok:
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def telegram(tmp_path, monkeypatch):
    """Telegram set up, as a run the OS started sees it: no session, only the scheduler's keys file."""
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    (plugin_data / "scheduler-keys.json").write_text(json.dumps({"TELEGRAM_BOT_TOKEN": TOKEN}), encoding="utf-8")
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(plugin_data))
    monkeypatch.delenv("EV_ECHO_KEYS_ID", raising=False)
    sent = []
    monkeypatch.setitem(notify_commands.DEPS, "telegram_client", telegram_client(sent))
    return sent


def setup_alerts(tmp_path, **more):
    sf = setup(tmp_path, seo=spec.TopicSchedule(report=spec.parse_rule("weekly mon 08:00", "report"),
                                                notify=["telegram"], set_at=SET_AT), **more)
    sf.channels["telegram"] = {"chat_id": "42"}
    spec.save(tmp_path, sf)


def cannot_start(tmp_path):
    return FakeRunner(tmp_path, RunResult(False, LOGIN_EXPIRED, 0.0, "api"))


def test_a_report_that_cannot_start_sends_the_failure_message(tmp_path, telegram):
    setup_alerts(tmp_path)
    out = go(tmp_path, runner_=cannot_start(tmp_path))
    assert out["ran"][0]["outcome"] == "failed"
    [request] = telegram
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    body = unquote_plus(request.content.decode())
    assert "chat_id=42" in body and "Failed to authenticate" in body
    [row] = [r for r in runlog.read_all(tmp_path) if r["kind"] == "notify"]
    assert (row["trigger"], row["outcome"]) == ("failed-message", "ok")


def test_a_failure_the_skill_already_announced_today_is_not_sent_twice(tmp_path, telegram):
    setup_alerts(tmp_path)
    runlog.append(tmp_path, {"topic": "seo", "kind": "notify", "trigger": "failed-message", "outcome": "ok",
                             "started": runlog.now_iso(), "finished": runlog.now_iso(), "channel": "telegram"})
    go(tmp_path, runner_=cannot_start(tmp_path))
    assert telegram == []


def test_a_good_report_sends_no_failure_message(tmp_path, telegram):
    setup_alerts(tmp_path)
    go(tmp_path)
    assert telegram == []


def test_a_fetch_failure_sends_no_failure_message(tmp_path, telegram):
    sf = setup(tmp_path, seo=spec.TopicSchedule(fetch=spec.parse_rule("daily 07:00", "fetch"),
                                                notify=["telegram"], set_at=SET_AT))
    sf.channels["telegram"] = {"chat_id": "42"}
    spec.save(tmp_path, sf)
    go(tmp_path, fetcher=Fetcher(fail={"seo"}))
    assert telegram == []


def test_an_undeliverable_failure_message_does_not_stop_the_other_topics(tmp_path, telegram, monkeypatch):
    monkeypatch.setitem(notify_commands.DEPS, "telegram_client", telegram_client([], ok=False))
    setup_alerts(tmp_path, other=("daily 07:00", "off"))
    fetcher = Fetcher()
    out = go(tmp_path, runner_=cannot_start(tmp_path), fetcher=fetcher)
    assert [(r["topic"], r["outcome"]) for r in out["ran"]] == [("seo", "failed"), ("other", "ok")]
    assert out["errors"] == [] and fetcher.calls == ["other"]
    [row] = [r for r in runlog.read_all(tmp_path) if r["kind"] == "notify"]
    assert row["outcome"] == "failed" and TOKEN not in json.dumps(row)


def test_a_missing_scheduler_keys_file_still_logs_the_failed_report(tmp_path, telegram):
    (tmp_path / "plugin-data" / "scheduler-keys.json").unlink()
    setup_alerts(tmp_path)
    out = go(tmp_path, runner_=cannot_start(tmp_path))
    assert out["ran"][0]["outcome"] == "failed" and telegram == []
    assert [r["outcome"] for r in runlog.read_all(tmp_path) if r["kind"] == "report"] == ["failed"]


def test_a_session_uses_its_own_keys_and_never_the_schedulers(tmp_path, telegram, monkeypatch):
    monkeypatch.setenv("EV_ECHO_KEYS_ID", "a-session-without-keys")
    setup_alerts(tmp_path)
    ws = run_due._workspace(tmp_path)
    assert notify_commands._secrets(ws)["telegram"] == ""


def test_run_killable_returns_the_output():
    done = runner.run_killable([sys.executable, "-c", "print('hi')"], cwd=".", env=dict(os.environ), timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "hi"


def test_run_killable_kills_the_whole_tree_on_timeout():
    script = ("import subprocess, sys, time; "
              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); time.sleep(120)")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run_killable([sys.executable, "-c", script], cwd=".", env=dict(os.environ), timeout=2)
    assert time.monotonic() - started < 60      # never waits for the grandchild holding the output
