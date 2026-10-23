"""Adaptive fetch: how often a source publishes, and whether it is due on this run (pure)."""
from datetime import datetime, timedelta, timezone
from statistics import median

MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 14
SAMPLE_SIZE = 10   # newest dated items used
MIN_DATES = 3
LINKEDIN_MAX_DAYS = 6   # a normal Apify run only returns the last week of posts


def learned_interval(dates: list[datetime]) -> float | None:
    """Half the median gap between the newest publication dates, clamped; None below MIN_DATES."""
    newest = sorted(dates, reverse=True)[:SAMPLE_SIZE]
    if len(newest) < MIN_DATES:
        return None
    gaps = [(a - b).total_seconds() / 86400 for a, b in zip(newest, newest[1:])]
    return float(min(MAX_INTERVAL_DAYS, max(MIN_INTERVAL_DAYS, median(gaps) / 2)))


def interval_cap(source_type: str, horizon_days: int) -> int:
    """Longest interval a source may wait: items published meanwhile must stay inside the horizon."""
    cap = max(1, horizon_days - 1)
    return min(cap, LINKEDIN_MAX_DAYS) if source_type == "linkedin" else cap


def interval(source: dict, dates: list[datetime],
             max_days: float | None = None) -> tuple[float | None, str | None, bool]:
    """(days, "pinned"|"learned"|None, capped): the pin or learned interval, held to max_days."""
    if source.get("fetch_every"):
        days, origin = float(source["fetch_every"]), "pinned"
    else:
        days, origin = learned_interval(dates), "learned"
    if days is None:
        return None, None, False
    if max_days is not None and days > max_days:
        return float(max(MIN_INTERVAL_DAYS, max_days)), origin, True
    return days, origin, False


def _last_checked(state: dict | None) -> datetime | None:
    """None when never checked or the stored value is unreadable (the source is then due)."""
    raw = (state or {}).get("last_checked")
    try:
        checked = datetime.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None
    if checked is not None and checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return checked


def is_due(state: dict | None, interval_days: float | None, now: datetime) -> bool:
    checked = _last_checked(state)
    if checked is None or (state or {}).get("consecutive_errors") or interval_days is None:
        return True
    return now - checked >= timedelta(days=interval_days)


def next_due(state: dict | None, interval_days: float | None) -> datetime | None:
    checked = _last_checked(state)
    if checked is None or interval_days is None:
        return None
    return checked + timedelta(days=interval_days)
