"""Per-topic run lock: <data>/scheduling/locks/<topic>.lock holds {pid, started}.

A lock is stale after 3 hours, or when its (long-lived) holder process is gone. A manual report
records no pid (the engine process that takes it exits at once), so only the age frees it.
A scheduled run's parent (run-due) holds the lock for the claude process it starts: that child
passes through, told by EV_ECHO_SCHEDULED_RUN.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STALE_AFTER = timedelta(hours=3)
ENV_SCHEDULED = "EV_ECHO_SCHEDULED_RUN"


def path_of(data_dir: Path, topic: str) -> Path:
    return Path(data_dir) / "scheduling" / "locks" / f"{topic}.lock"


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _stale(holder: dict | None, now: datetime) -> bool:
    if not holder:
        return True   # unreadable lock file: treat as left over
    try:
        started = datetime.fromisoformat(holder["started"])
    except (KeyError, TypeError, ValueError):
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if now - started > STALE_AFTER:
        return True
    pid = holder.get("pid")
    return bool(pid) and not pid_alive(pid)


def acquire(data_dir: Path, topic: str, pid: int | None = None, now: datetime | None = None) -> dict | None:
    """None when the lock is now ours; else the holder {pid, started}."""
    if os.environ.get(ENV_SCHEDULED) == topic:
        return None
    now = now or datetime.now(timezone.utc)
    path = path_of(data_dir, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"pid": pid, "started": now.isoformat()})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = _read(path)
            if not _stale(holder, now):
                return holder
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        return None
    return _read(path) or {"pid": None, "started": now.isoformat()}


def holder(data_dir: Path, topic: str, now: datetime | None = None) -> dict | None:
    """The live holder of the lock, or None when it is free or stale."""
    path = path_of(data_dir, topic)
    if not path.exists():
        return None
    h = _read(path)
    return None if _stale(h, now or datetime.now(timezone.utc)) else h


def started_at(data_dir: Path, topic: str) -> str | None:
    h = _read(path_of(data_dir, topic))
    return h.get("started") if h else None


def release_manual(data_dir: Path, topic: str) -> bool:
    """Free the lock only when a manual run holds it (no pid). True when freed or already free."""
    h = _read(path_of(data_dir, topic))
    if h and h.get("pid") and not _stale(h, datetime.now(timezone.utc)):
        return False
    release(data_dir, topic)
    return True


def release(data_dir: Path, topic: str) -> None:
    try:
        path_of(data_dir, topic).unlink()
    except OSError:
        pass
