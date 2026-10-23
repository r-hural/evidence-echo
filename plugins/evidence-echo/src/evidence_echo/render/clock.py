"""Local time for display: timestamps are stored in UTC and shown in the machine's zone.

Dates that come from articles are never converted; only engine timestamps go through here.
"""
from datetime import datetime, timezone, tzinfo

ZONE: tzinfo | None = None   # None: the machine's local zone; tests set a fixed zone


def local(ts: str | datetime) -> datetime:
    """A stored timestamp as a datetime in the display zone (a naive value is taken as UTC)."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ZONE)


def local_date(ts) -> str:
    return local(ts).strftime("%Y-%m-%d") if ts else ""


def local_minute(ts) -> str:
    return local(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
