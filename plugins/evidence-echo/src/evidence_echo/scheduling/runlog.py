"""The run log: <data>/scheduling/runs.jsonl, one JSON line per run (or notification)."""
import json
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ("topic", "kind", "trigger", "slot", "started", "finished", "outcome", "error", "usage_basis")


def path_of(data_dir: Path) -> Path:
    return Path(data_dir) / "scheduling" / "runs.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append(data_dir: Path, entry: dict) -> None:
    """Add one line; raises OSError when the folder is not writable (callers decide what to do)."""
    path = path_of(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {k: entry.get(k) for k in FIELDS}
    row.update({k: v for k, v in entry.items() if k not in FIELDS})
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_all(data_dir: Path) -> list[dict]:
    """Every readable line; a corrupt line or an unreadable file is skipped, never fatal."""
    try:
        text = path_of(data_dir).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def parse_time(raw) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def last_success(data_dir: Path, topic: str, kind: str) -> datetime | None:
    """When the latest ok or empty run of this kind finished (its start when unfinished)."""
    best = None
    for row in read_all(data_dir):
        if row.get("topic") == topic and row.get("kind") == kind and row.get("outcome") in ("ok", "empty"):
            at = parse_time(row.get("finished") or row.get("started"))
            if at and (best is None or at > best):
                best = at
    return best


FAILURE_KINDS = ("report", "fetch", "notify")


def last_failure(data_dir: Path, topic: str) -> dict | None:
    """The latest failure of the topic's runs or messages that no later success of the same kind
    has cleared: {"at", "error", "kind"} (kind: report | fetch | notify)."""
    latest = {}   # kind -> (time, outcome, error) of the newest row
    for row in read_all(data_dir):
        kind = row.get("kind")
        if row.get("topic") != topic or kind not in FAILURE_KINDS:
            continue
        if row.get("outcome") not in ("ok", "empty", "failed"):
            continue
        at = parse_time(row.get("finished") or row.get("started"))
        if at and (kind not in latest or at >= latest[kind][0]):
            latest[kind] = (at, row.get("outcome"), row.get("error"))
    failed = [(at, kind, error) for kind, (at, outcome, error) in latest.items() if outcome == "failed"]
    if not failed:
        return None
    at, kind, error = max(failed, key=lambda f: f[0])
    return {"at": at.isoformat(), "error": error, "kind": kind}


def failed_notice_today(data_dir: Path, topic: str, now: datetime) -> bool:
    """A failure message of this topic already went out today (UTC date)."""
    for row in read_all(data_dir):
        if (row.get("topic") == topic and row.get("kind") == "notify" and row.get("trigger") == "failed-message"
                and row.get("outcome") == "ok"):
            at = parse_time(row.get("finished") or row.get("started"))
            if at and at.date() == now.astimezone(timezone.utc).date():
                return True
    return False
