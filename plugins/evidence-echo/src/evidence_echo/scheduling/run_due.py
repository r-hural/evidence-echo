"""`ev run-due`: start whatever the schedules say is due, at most one run per topic.

Started by the OS task at the wake times (and at logon / after a missed trigger). It works only
from the schedules and the run log, so missed slots collapse into one run. Fetch runs are keyless
and a report run is claude -p, which has the plugin settings; only the message about a failed report
uses a key, the delivery secrets the SessionStart hook keeps for this process. It never fails:
every problem is a log line, the exit code is 0.
"""
import contextlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from evidence_echo.core import config
from evidence_echo.scheduling import lock, runlog, slots, spec, status, usage
from evidence_echo.scheduling.runner import ClaudeRunner

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
LONG_WINDOW_KINDS = ("biweekly", "monthly")


def _warn(message: str) -> None:
    print(f"evidence-echo: run-due: {message}", file=sys.stderr)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _baseline(data_dir: Path, topic: str, kind: str, t: spec.TopicSchedule) -> datetime:
    """The time after which a slot is still owed: the last good run, else when the schedule was set."""
    best = runlog.last_success(data_dir, topic, kind)
    if kind == "fetch":   # a report run fetches first, so it settles the fetch slots too
        report = runlog.last_success(data_dir, topic, "report")
        best = max([d for d in (best, report) if d], default=None)
    return best or runlog.parse_time(t.set_at) or EPOCH


def _report_slot(t: spec.TopicSchedule, now: datetime, tz):
    if t.report.kind == "per_run":   # every fetch run ends in a report
        return slots.last_slot(t.fetch, now, tz, t.anchor)
    return slots.last_slot(t.report, now, tz, t.anchor)


def _failed_slot_before(data_dir: Path, topic: str, kind: str, slot: datetime) -> bool:
    """A report that already failed for this slot is not paid for again; the next slot tries anew."""
    for row in runlog.read_all(data_dir):
        if (row.get("topic") == topic and row.get("kind") == kind and row.get("outcome") == "failed"
                and runlog.parse_time(row.get("slot")) == slot):
            return True
    return False


def _acquire(data_dir: Path, topic: str) -> bool:
    """Never waits: a held topic is skipped, stays owed (no success logged) and is tried again at
    the next wake; the other topics are not held up."""
    return lock.acquire(data_dir, topic, pid=os.getpid()) is None


def _log(data_dir: Path, entry: dict) -> None:
    try:
        runlog.append(data_dir, entry)
    except OSError as exc:
        _warn(f"run log not written ({exc})")


def fetch_run(topic: str) -> None:
    """Keyless and LLM-free: fetch, then extract. Raises on failure."""
    from evidence_echo import api
    cfg = config.load(topic)
    api.fetch(cfg)
    api.extract(cfg)


def _run_fetch(data_dir: Path, topic: str, slot: datetime, fetcher, now: datetime) -> dict:
    started = now.isoformat()
    try:
        fetcher(topic)
        outcome, error = "ok", None
    except Exception as exc:   # one topic's trouble never stops the others
        outcome, error = "failed", f"{type(exc).__name__}: {exc}"
    _log(data_dir, {"topic": topic, "kind": "fetch", "trigger": "scheduled", "slot": _iso(slot),
                    "started": started, "finished": now.isoformat(), "outcome": outcome, "error": error})
    return {"topic": topic, "kind": "fetch", "outcome": outcome, **({"error": error} if error else {})}


def _workspace(data_dir: Path) -> config.Workspace:
    data_dir = Path(data_dir)
    return config.Workspace(data_dir, config._plugin_data_dir(data_dir), data_dir / "topics", None)


def _announce_failure(data_dir: Path, topic: str, error: str | None) -> None:
    """The report skill cannot say so itself when claude never started (an expired login, say).
    At most one message a day: a failure the skill already announced is not repeated."""
    from evidence_echo.notify import commands as notify
    try:
        with contextlib.redirect_stdout(sys.stderr):   # stdout carries run-due's own JSON
            notify.send_failed(_workspace(data_dir), topic, error or "unknown error")
    except Exception as exc:   # the channel's error is in the run log; this text may not be safe to print
        _warn(f"{topic}: failure message not sent ({type(exc).__name__})")


REPORT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-report(-\d+)?\.html$")


def _saved_report(topic: str, started: str) -> Path | None:
    """The report rendered during this run, if any; any error means none was found."""
    try:
        since = (runlog.parse_time(started) or EPOCH).timestamp()
        found = [p for p in config.load(topic).reports_dir.iterdir()
                 if REPORT_NAME.match(p.name) and p.stat().st_mtime >= since]
        return max(found, key=lambda p: p.stat().st_mtime) if found else None
    except Exception:
        return None


def _run_report(data_dir: Path, topic: str, t: spec.TopicSchedule, sf: spec.ScheduleFile, slot: datetime,
                runner, now: datetime) -> dict:
    env = {lock.ENV_SCHEDULED: topic}
    if t.report.kind in LONG_WINDOW_KINDS:
        env["EV_ECHO_LINKEDIN_WINDOW"] = "month"
    started = now.isoformat()
    topic_started = runlog.now_iso()   # this topic's own start: the wake time is shared by every topic
    result = runner.report(topic, data_dir, sf, env)
    done = runlog.last_success(data_dir, topic, "report")
    logged = done is not None and done >= (runlog.parse_time(started) or EPOCH)
    outcome, error = "ok", None
    if result.trip is not None and not logged:
        outcome, error = "failed", usage.trip_message(result.trip)
        saved = _saved_report(topic, topic_started)
        if saved:
            error += f". The report was saved but not sent: {saved}"
    elif result.trip is not None:
        pass   # the session delivered its report before the guard stopped it
    elif not result.ok:
        outcome, error = "failed", result.error
    elif not logged:   # the skill logs ok / empty itself; no line from it means it never got to the end
        outcome, error = "failed", "the report run ended without logging a result"
    fields = usage.log_fields(result.basis, result.session_usd)
    if outcome == "failed":
        _log(data_dir, {"topic": topic, "kind": "report", "trigger": "scheduled", "slot": _iso(slot),
                        "started": started, "finished": runlog.now_iso(), "outcome": "failed",
                        "error": error, **fields})
        _announce_failure(data_dir, topic, error)
    elif result.basis or result.session_usd is not None:
        row = {"topic": topic, "kind": "usage", "trigger": "scheduled", "started": started,
               "finished": now.isoformat(), "outcome": "ok", **fields}
        if result.trip is not None:
            row["guard_trip"] = result.trip
        _log(data_dir, row)
    return {"topic": topic, "kind": "report", "outcome": outcome, **({"error": error} if error else {})}


def run_due(data_dir: Path, now: datetime | None = None, runner=None, fetcher=None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = {"ran": [], "skipped": [], "errors": []}
    try:
        sf = spec.load(data_dir)
    except spec.ConfigError as exc:
        _warn(str(exc))
        out["errors"].append(str(exc))
        return out
    try:
        status.refresh(data_dir, sf, now, last_wake=now)
    except (OSError, spec.ConfigError) as exc:
        _warn(f"status not written ({exc})")
    runner = runner or ClaudeRunner()
    fetcher = fetcher or fetch_run
    tz = sf.zone()
    for topic, t in sf.topics.items():
        if t.paused or t.desktop_task:
            continue
        try:
            report_slot = _report_slot(t, now, tz)
            fetch_slot = slots.last_slot(t.fetch, now, tz, t.anchor)
            kind = slot = None
            if report_slot and report_slot > _baseline(data_dir, topic, "report", t):
                if _failed_slot_before(data_dir, topic, "report", report_slot):
                    # not paid for again; fetching goes on until the next report slot
                    out["skipped"].append({"topic": topic, "kind": "report", "why": "already failed for this slot"})
                else:
                    kind, slot = "report", report_slot
            if kind is None and fetch_slot and fetch_slot > _baseline(data_dir, topic, "fetch", t):
                kind, slot = "fetch", fetch_slot
            if kind is None:
                continue
            if not _acquire(data_dir, topic):
                _log(data_dir, {"topic": topic, "kind": kind, "trigger": "scheduled", "slot": _iso(slot),
                                "started": now.isoformat(), "finished": now.isoformat(),
                                "outcome": "skipped", "error": "another run held the lock"})
                out["skipped"].append({"topic": topic, "kind": kind, "why": "locked"})
                continue
            try:
                if kind == "report":
                    out["ran"].append(_run_report(data_dir, topic, t, sf, slot, runner, now))
                else:
                    out["ran"].append(_run_fetch(data_dir, topic, slot, fetcher, now))
            finally:
                lock.release(data_dir, topic)
        except Exception as exc:
            _warn(f"{topic}: {type(exc).__name__}: {exc}")
            out["errors"].append(f"{topic}: {type(exc).__name__}: {exc}")
    try:
        status.refresh(data_dir, sf, now)
    except (OSError, spec.ConfigError) as exc:
        _warn(f"status not written ({exc})")
    return out


def command(args) -> dict:
    """The CLI entry: never raises, so the OS task never reports a failure."""
    try:
        ws, error = config.workspace_lenient()
        if error:
            _warn(error)
            return {"ran": [], "skipped": [], "errors": [error]}
        return run_due(ws.data_dir)
    except Exception as exc:
        _warn(f"{type(exc).__name__}: {exc}")
        return {"ran": [], "skipped": [], "errors": [str(exc)]}
