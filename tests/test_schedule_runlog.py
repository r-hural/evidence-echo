import json
import os
from datetime import datetime, timedelta, timezone

from evidence_echo import cli
from evidence_echo.scheduling import lock, runlog, spec, status

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _entry(topic="seo", kind="report", outcome="ok", finished=NOW, **extra):
    return {"topic": topic, "kind": kind, "trigger": "scheduled", "started": finished.isoformat(),
            "finished": finished.isoformat(), "outcome": outcome, **extra}


def test_append_and_last_success(tmp_path):
    assert runlog.last_success(tmp_path, "seo", "report") is None
    runlog.append(tmp_path, _entry(finished=NOW - timedelta(days=2)))
    runlog.append(tmp_path, _entry(outcome="empty", finished=NOW - timedelta(days=1)))
    runlog.append(tmp_path, _entry(outcome="failed", finished=NOW))
    runlog.append(tmp_path, _entry(topic="other", finished=NOW))
    runlog.append(tmp_path, _entry(kind="fetch", finished=NOW))
    assert runlog.last_success(tmp_path, "seo", "report") == NOW - timedelta(days=1)
    assert runlog.last_success(tmp_path, "seo", "fetch") == NOW
    assert runlog.last_failure(tmp_path, "seo")["at"] == NOW.isoformat()


def test_corrupt_line_is_skipped(tmp_path):
    """Review Focus 4: a bad line never stops the log."""
    runlog.append(tmp_path, _entry())
    with runlog.path_of(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write("{broken\n\n[1, 2]\n")
    runlog.append(tmp_path, _entry(outcome="failed", error="boom"))
    assert len(runlog.read_all(tmp_path)) == 2
    assert runlog.last_success(tmp_path, "seo", "report") == NOW


def test_unreadable_log_is_empty(tmp_path):
    runlog.path_of(tmp_path).mkdir(parents=True)   # a folder where the file should be
    assert runlog.read_all(tmp_path) == []


def test_lock_held_returns_the_holder(tmp_path):
    assert lock.acquire(tmp_path, "seo", pid=os.getpid()) is None
    held = lock.acquire(tmp_path, "seo")
    assert held["pid"] == os.getpid()
    lock.release(tmp_path, "seo")
    assert lock.acquire(tmp_path, "seo") is None


def test_stale_lock_is_taken_over(tmp_path):
    assert lock.acquire(tmp_path, "seo", now=NOW - timedelta(hours=4)) is None
    assert lock.acquire(tmp_path, "seo", now=NOW) is None       # 4 h old: stale
    assert lock.acquire(tmp_path, "seo", now=NOW + timedelta(hours=1)) is not None   # fresh now


def test_dead_pid_lock_is_taken_over(tmp_path):
    path = lock.path_of(tmp_path, "seo")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 2 ** 22 + 12345, "started": NOW.isoformat()}), encoding="utf-8")
    assert lock.acquire(tmp_path, "seo", now=NOW) is None


def test_unreadable_lock_file_is_taken_over(tmp_path):
    path = lock.path_of(tmp_path, "seo")
    path.parent.mkdir(parents=True)
    path.write_text("garbage", encoding="utf-8")
    assert lock.acquire(tmp_path, "seo") is None


def test_scheduled_child_passes_through(tmp_path, monkeypatch):
    assert lock.acquire(tmp_path, "seo", pid=os.getpid()) is None
    monkeypatch.setenv("EV_ECHO_SCHEDULED_RUN", "seo")
    assert lock.acquire(tmp_path, "seo") is None
    assert lock.acquire(tmp_path, "other") is None               # a free lock of another topic
    monkeypatch.setenv("EV_ECHO_SCHEDULED_RUN", "other")
    assert lock.acquire(tmp_path, "seo") is not None


def test_status_json_is_utc_strings(tmp_path):
    sf = spec.ScheduleFile(timezone="Europe/Berlin")
    sf.topics["seo"] = spec.TopicSchedule(report=spec.parse_rule("weekly mon 08:00", "report"),
                                          fetch=spec.parse_rule("daily 07:00", "fetch"))
    runlog.append(tmp_path, _entry(finished=NOW - timedelta(days=3)))
    runlog.append(tmp_path, _entry(outcome="failed", error="boom", finished=NOW - timedelta(days=1)))
    out = status.refresh(tmp_path, sf, NOW, last_wake=NOW)
    assert json.loads(status.path_of(tmp_path).read_text(encoding="utf-8")) == out
    t = out["topics"]["seo"]
    assert t["next_report"] == "2026-11-16T07:00:00+00:00"          # Monday 08:00 Berlin (CET)
    assert t["last_report_ok"] == (NOW - timedelta(days=3)).isoformat()
    assert t["last_failure"] == {"at": (NOW - timedelta(days=1)).isoformat(), "error": "boom", "kind": "report"}
    assert out["next_wake"] == "2026-11-15T06:00:00+00:00"          # the next daily fetch slot
    assert out["last_wake"] == NOW.isoformat() and out["installed"] is False
    again = status.refresh(tmp_path, sf, NOW)                        # keeps the previous last_wake
    assert again["last_wake"] == NOW.isoformat()


def test_next_wake_with_a_fixed_interval(tmp_path):
    sf = spec.ScheduleFile(timezone="UTC", wake="every 6h")
    assert status.next_wake(sf, datetime(2026, 11, 14, 13, 30, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)


def test_cli_begin_end(workspace, capsys):
    assert cli.main(["--topic", "seo", "schedule", "begin"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    assert cli.main(["--topic", "seo", "schedule", "begin"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "started" in out["held_by"]
    assert cli.main(["--topic", "seo", "schedule", "end", "--outcome", "empty"]) == 0
    assert json.loads(capsys.readouterr().out)["logged"] == "empty"
    [row] = runlog.read_all(workspace)
    assert (row["topic"], row["kind"], row["trigger"], row["outcome"]) == ("seo", "report", "manual", "empty")
    assert row["started"] and row["finished"]
    assert (workspace / "scheduling" / "status.json").exists()
    assert cli.main(["--topic", "seo", "schedule", "begin"]) == 0     # the lock was released
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_cli_end_failed_records_the_error(workspace, capsys):
    assert cli.main(["--topic", "seo", "schedule", "end", "--outcome", "failed", "--error", "boom",
                     "--trigger", "scheduled"]) == 0
    [row] = runlog.read_all(workspace)
    assert (row["outcome"], row["error"], row["trigger"]) == ("failed", "boom", "scheduled")


def test_end_with_an_unwritable_scheduling_folder_exits_0(workspace, capsys):
    (workspace / "scheduling").write_text("not a folder", encoding="utf-8")   # Review Focus 4
    assert cli.main(["--topic", "seo", "schedule", "end", "--outcome", "ok"]) == 0
    captured = capsys.readouterr()
    assert "evidence-echo:" in captured.err and json.loads(captured.out)["ok"] is False
    assert cli.main(["--topic", "seo", "schedule", "begin"]) == 0


def test_a_manual_end_never_frees_a_scheduled_runs_lock(tmp_path):
    import os
    from evidence_echo.scheduling import lock
    assert lock.acquire(tmp_path, "seo", pid=os.getpid()) is None
    assert lock.release_manual(tmp_path, "seo") is False and lock.path_of(tmp_path, "seo").exists()
    lock.release(tmp_path, "seo")
    assert lock.acquire(tmp_path, "seo") is None          # a manual run: no pid
    assert lock.release_manual(tmp_path, "seo") is True and not lock.path_of(tmp_path, "seo").exists()


def test_last_failure_by_kind_and_cleared_by_a_later_success(tmp_path):
    from evidence_echo.scheduling import runlog as rl

    def row(kind, outcome, hour, error=None):
        at = f"2026-11-14T{hour:02d}:00:00+00:00"
        rl.append(tmp_path, {"topic": "seo", "kind": kind, "started": at, "finished": at,
                             "outcome": outcome, "error": error})
    row("fetch", "failed", 1, "feed down")
    row("fetch", "ok", 2)
    assert rl.last_failure(tmp_path, "seo") is None
    row("notify", "failed", 3, "message not sent (telegram): chat not found")
    assert rl.last_failure(tmp_path, "seo")["kind"] == "notify"
