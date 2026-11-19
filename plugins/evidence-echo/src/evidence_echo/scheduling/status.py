"""<data>/scheduling/status.json: a small snapshot the SessionStart hook reads (UTC ISO strings only,
so the hook needs no time-zone data and does no schedule math)."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evidence_echo.scheduling import runlog, slots
from evidence_echo.scheduling.spec import ScheduleFile


def path_of(data_dir: Path) -> Path:
    return Path(data_dir) / "scheduling" / "status.json"


def read(data_dir: Path) -> dict:
    try:
        data = json.loads(path_of(data_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def next_wake(sf: ScheduleFile, now: datetime) -> datetime | None:
    """The next time the OS task should start run-due."""
    tz = sf.zone()
    if sf.wake != "auto":
        hours = int(sf.wake.split()[1].rstrip("h"))
        local = now.astimezone(tz)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        n = int((local - midnight).total_seconds() // (hours * 3600)) + 1
        return (midnight + timedelta(hours=hours * n)).astimezone(timezone.utc)
    best = None
    for t in sf.topics.values():
        if t.paused or t.desktop_task:
            continue
        for rule in (t.fetch, t.report):
            nxt = slots.next_slot(rule, now, tz, t.anchor)
            if nxt and (best is None or nxt < best):
                best = nxt
    return best


def refresh(data_dir: Path, sf: ScheduleFile, now: datetime, last_wake: datetime | None = None) -> dict:
    tz = sf.zone()
    previous = read(data_dir)
    topics = {}
    for tid, t in sf.topics.items():
        topics[tid] = {
            "next_report": None if t.paused or t.desktop_task
            else _iso(slots.next_slot(t.report, now, tz, t.anchor)),
            "last_report_ok": _iso(runlog.last_success(data_dir, tid, "report")),
            "last_failure": runlog.last_failure(data_dir, tid)}
    status = {"topics": topics, "next_wake": _iso(next_wake(sf, now)),
              "last_wake": _iso(last_wake) if last_wake else previous.get("last_wake"),
              "installed": sf.installed}
    path = path_of(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return status
