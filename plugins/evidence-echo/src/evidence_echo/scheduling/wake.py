"""Wake times: when the OS task must start `run-due`.

`wake: auto` — one trigger per distinct slot time of the unpaused topics (no polling in between);
`wake: every <N>h` — one repeating trigger. Times are converted from the schedule's zone to the
machine's local time, which is what the OS schedulers use.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from evidence_echo.scheduling import spec
from evidence_echo.scheduling.spec import ScheduleFile


@dataclass(frozen=True)
class Trigger:
    kind: str                  # daily | weekly | monthly | interval
    time: dtime | None = None   # local time of day (interval: always 00:00, the grid starts at midnight)
    weekday: int | None = None   # 0 = Monday
    day: int | None = None
    hours: int | None = None

    def label(self) -> str:
        at = self.time.strftime("%H:%M") if self.time else ""
        if self.kind == "interval":
            return f"every {self.hours}h"
        if self.kind == "weekly":
            return f"weekly {spec.DAYS[self.weekday]} {at}"
        if self.kind == "monthly":
            return f"monthly {self.day} {at}"
        return f"daily {at}"


def _local(d: date, t: dtime, tz: ZoneInfo, local_tz) -> datetime:
    return datetime.combine(d, t, tzinfo=tz).astimezone(local_tz)


def _next_date(weekday: int | None, day: int | None, tz: ZoneInfo) -> date:
    today = datetime.now(tz).date()
    if weekday is not None:
        return today + timedelta(days=(weekday - today.weekday()) % 7)
    return today.replace(day=day)


def _from_rule(rule: spec.Rule, tz: ZoneInfo, local_tz) -> Trigger | None:
    k = rule.kind
    if k in ("off", "per_run"):
        return None
    if k == "hourly":
        return Trigger("interval", dtime(0, 0), hours=1)
    if k == "every":
        return Trigger("interval", dtime(0, 0), hours=rule.hours)
    if k == "daily":
        return Trigger("daily", _local(date.today(), rule.time, tz, local_tz).time().replace(second=0, microsecond=0))
    if k in ("weekly", "biweekly"):   # biweekly: wake weekly; run-due decides whether the week counts
        at = _local(_next_date(rule.weekday, None, tz), rule.time, tz, local_tz)
        return Trigger("weekly", at.time().replace(second=0, microsecond=0), weekday=at.weekday())
    if k == "monthly":
        if rule.day == "last":   # no fixed day number: wake daily at that time
            return Trigger("daily", _local(date.today(), rule.time, tz, local_tz).time().replace(second=0, microsecond=0))
        at = _local(_next_date(None, rule.day, tz), rule.time, tz, local_tz)
        return Trigger("monthly", at.time().replace(second=0, microsecond=0), day=min(max(at.day, 1), 28))
    return None


def _machine_offset(naive: datetime):
    return naive.astimezone().utcoffset()


def _shifts_apart(tz: ZoneInfo, year: int | None = None, machine_offset=None) -> bool:
    """The schedule's zone and the machine's change clocks differently (one has DST and the other
    not, or on other dates), so a trigger fixed in local time is an hour off for part of the year.
    The machine's offsets come from the OS rules for each date, not from a fixed-offset tzinfo."""
    year = year or date.today().year
    machine_offset = machine_offset or _machine_offset
    probes = [datetime(year, m, 15, 12) for m in range(1, 13)]
    diffs = {datetime.combine(p.date(), p.time(), tzinfo=tz).utcoffset() - machine_offset(p)
             for p in probes}
    return len(diffs) > 1


def _an_hour_later(t: Trigger) -> Trigger | None:
    if t.kind == "interval" or t.time.hour == 23:   # past midnight would change the day: rare, skipped
        return None
    return Trigger(t.kind, t.time.replace(hour=t.time.hour + 1), t.weekday, t.day, t.hours)


def times(sf: ScheduleFile, local_tz) -> list[Trigger]:
    if sf.wake != "auto":
        return [Trigger("interval", dtime(0, 0), hours=int(sf.wake.split()[1].rstrip("h")))]
    tz = sf.zone()
    found = set()
    for t in sf.topics.values():
        if t.paused or t.desktop_task:
            continue
        for rule in (t.fetch, t.report):
            trigger = _from_rule(rule, tz, local_tz)
            if trigger:
                found.add(trigger)
                later = _an_hour_later(trigger) if _shifts_apart(tz) else None
                if later:   # a second wake an hour later catches the slot whichever side shifted
                    found.add(later)
    return sorted(found, key=lambda g: (g.kind, g.time or dtime(0, 0), g.weekday or 0, g.day or 0, g.hours or 0))
