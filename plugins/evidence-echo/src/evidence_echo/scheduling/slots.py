"""Slot math: the latest slot at or before a moment, the next one after it.

A slot is a local wall-clock time in the schedule's zone (07:00 stays 07:00 across a DST change),
returned as an aware UTC datetime.
"""
import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from evidence_echo.scheduling.spec import Rule

_SPAN_DAYS = 400   # longer than any rule's period


def _utc(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz).astimezone(timezone.utc)


def _biweekly_ok(d: date, rule: Rule, anchor: date | None) -> bool:
    base = anchor or (date(2001, 1, 1) + timedelta(days=rule.weekday))   # 2001-01-01 is a Monday
    return (d - base).days % 14 == 0


def slots_on(rule: Rule, d: date, tz: ZoneInfo, anchor: date | None = None) -> list[datetime]:
    """The rule's slots on local date d, ascending."""
    k = rule.kind
    if k in ("off", "per_run"):
        return []
    if k in ("hourly", "every"):
        step = 1 if k == "hourly" else rule.hours
        return sorted(_utc(d, time(h), tz) for h in range(0, 24, step))
    if k == "daily":
        return [_utc(d, rule.time, tz)]
    if k == "weekly":
        return [_utc(d, rule.time, tz)] if d.weekday() == rule.weekday else []
    if k == "biweekly":
        ok = d.weekday() == rule.weekday and _biweekly_ok(d, rule, anchor)
        return [_utc(d, rule.time, tz)] if ok else []
    if k == "monthly":
        last = calendar.monthrange(d.year, d.month)[1]
        day = last if rule.day == "last" else rule.day
        return [_utc(d, rule.time, tz)] if d.day == day else []
    raise ValueError(f"unknown rule kind {k}")


def last_slot(rule: Rule, now: datetime, tz: ZoneInfo, anchor: date | None = None) -> datetime | None:
    if rule.kind in ("off", "per_run"):
        return None
    now = now.astimezone(timezone.utc)
    today = now.astimezone(tz).date()
    for back in range(_SPAN_DAYS):
        found = [s for s in slots_on(rule, today - timedelta(days=back), tz, anchor) if s <= now]
        if found:
            return max(found)
    return None


def next_slot(rule: Rule, after: datetime, tz: ZoneInfo, anchor: date | None = None) -> datetime | None:
    if rule.kind in ("off", "per_run"):
        return None
    after = after.astimezone(timezone.utc)
    start = after.astimezone(tz).date() - timedelta(days=1)   # a zone behind UTC may still be on that date
    for ahead in range(_SPAN_DAYS + 1):
        found = [s for s in slots_on(rule, start + timedelta(days=ahead), tz, anchor) if s > after]
        if found:
            return min(found)
    return None
