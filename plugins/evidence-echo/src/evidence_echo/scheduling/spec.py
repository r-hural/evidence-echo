"""The schedule file (<data>/scheduling/schedule.json) and the rule grammar.

Fetch:  off | hourly | every <N>h | daily HH:MM | weekly <day> HH:MM
Report: off | per_run | daily HH:MM | weekly <day> HH:MM | biweekly <day> HH:MM
        | monthly <1-28|last> HH:MM
"""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from evidence_echo.core.settings import ConfigError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FETCH_KINDS = ("off", "hourly", "every", "daily", "weekly")
REPORT_KINDS = ("off", "per_run", "daily", "weekly", "biweekly", "monthly")
ALLOWED = {
    "fetch": "off, hourly, every <N>h, daily HH:MM, weekly <day> HH:MM",
    "report": "off, per_run, daily HH:MM, weekly <day> HH:MM, biweekly <day> HH:MM, "
              "monthly <1-28|last> HH:MM",
}
DEFAULT_API_SPEND_LIMIT_USD = 6.0
DEFAULT_SESSION_MAX_TOTAL_TOKENS = 30_000_000
DEFAULT_SESSION_MAX_REPEATED_CALLS = 8
DEFAULT_SESSION_MAX_TOOL_CALLS = 500
DEFAULT_SESSION_CEILING_USD = 40.0
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_HOURS = re.compile(r"^(\d{1,2})h$")


@dataclass(frozen=True)
class Rule:
    kind: str
    hours: int | None = None
    weekday: int | None = None
    day: int | str | None = None
    time: dtime | None = None

    def __str__(self) -> str:
        at = self.time.strftime("%H:%M") if self.time else ""
        if self.kind == "every":
            return f"every {self.hours}h"
        if self.kind in ("weekly", "biweekly"):
            return f"{self.kind} {DAYS[self.weekday]} {at}"
        if self.kind == "monthly":
            return f"monthly {self.day} {at}"
        if self.kind == "daily":
            return f"daily {at}"
        return self.kind


OFF = Rule("off")


def _bad(field_name: str, text: str, why: str) -> ConfigError:
    return ConfigError(f"{field_name} schedule '{text}': {why}. Allowed: {ALLOWED[field_name]}")


def _time(field_name: str, text: str, raw: str) -> dtime:
    m = _TIME.match(raw)
    if not m:
        raise _bad(field_name, text, f"'{raw}' is not a time of day (HH:MM, 24-hour)")
    return dtime(int(m.group(1)), int(m.group(2)))


def parse_rule(text: str, field_name: str) -> Rule:
    """A rule from its text; ConfigError names the field and the allowed forms."""
    if field_name not in ALLOWED:
        raise ValueError(f"unknown schedule field {field_name}")
    parts = (text or "").strip().lower().split()
    allowed = FETCH_KINDS if field_name == "fetch" else REPORT_KINDS
    if not parts:
        raise _bad(field_name, text, "empty")
    head = parts[0]
    if head not in (*FETCH_KINDS, *REPORT_KINDS):
        raise _bad(field_name, text, f"unknown form '{head}'")
    if head not in allowed:
        raise _bad(field_name, text, f"'{head}' is not allowed for {field_name}")
    if head in ("off", "per_run", "hourly"):
        if len(parts) != 1:
            raise _bad(field_name, text, f"'{head}' takes nothing after it")
        return Rule(head)
    if head == "every":
        m = _HOURS.match(parts[1]) if len(parts) == 2 else None
        if not m:
            raise _bad(field_name, text, "every needs hours like 'every 6h'")
        hours = int(m.group(1))
        if not 1 <= hours <= 24:
            raise _bad(field_name, text, "hours must be 1 to 24")
        return Rule("every", hours=hours)
    if head == "daily":
        if len(parts) != 2:
            raise _bad(field_name, text, "daily needs a time")
        return Rule("daily", time=_time(field_name, text, parts[1]))
    if head in ("weekly", "biweekly"):
        if len(parts) != 3:
            raise _bad(field_name, text, f"{head} needs a day and a time")
        if parts[1][:3] not in DAYS or len(parts[1]) > 9:
            raise _bad(field_name, text, f"'{parts[1]}' is not a day ({', '.join(DAYS)})")
        return Rule(head, weekday=DAYS.index(parts[1][:3]), time=_time(field_name, text, parts[2]))
    # monthly
    if len(parts) != 3:
        raise _bad(field_name, text, "monthly needs a day (1-28 or last) and a time")
    if parts[1] == "last":
        day: int | str = "last"
    elif parts[1].isdigit() and 1 <= int(parts[1]) <= 28:
        day = int(parts[1])
    else:
        raise _bad(field_name, text, f"day '{parts[1]}' must be 1-28 or last (29-31 do not exist "
                                     "in every month)")
    return Rule("monthly", day=day, time=_time(field_name, text, parts[2]))


@dataclass
class TopicSchedule:
    fetch: Rule = OFF
    report: Rule = OFF
    notify: list[str] = field(default_factory=list)
    paused: bool = False
    notify_when_empty: str | None = None   # None: the global value
    anchor: date | None = None             # biweekly: the first report slot's date
    desktop_task: bool = False
    set_at: str | None = None              # UTC ISO time the schedule was last changed


@dataclass
class ScheduleFile:
    timezone: str = "UTC"
    wake: str = "auto"
    api_spend_limit_usd: float = DEFAULT_API_SPEND_LIMIT_USD
    session_max_total_tokens: int = DEFAULT_SESSION_MAX_TOTAL_TOKENS
    session_max_repeated_calls: int = DEFAULT_SESSION_MAX_REPEATED_CALLS
    session_max_tool_calls: int = DEFAULT_SESSION_MAX_TOOL_CALLS
    session_ceiling_usd: float = DEFAULT_SESSION_CEILING_USD
    notify_when_empty: str = "on"
    channels: dict = field(default_factory=dict)
    topics: dict[str, TopicSchedule] = field(default_factory=dict)
    installed: bool = False

    def zone(self) -> ZoneInfo:
        return zone(self.timezone)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ConfigError(f"unknown time zone '{name}' (use a name like Europe/Berlin)") from exc


def check_wake(text: str) -> str:
    text = (text or "").strip().lower()
    if text == "auto":
        return text
    parts = text.split()
    m = _HOURS.match(parts[1]) if len(parts) == 2 and parts[0] == "every" else None
    if m and 1 <= int(m.group(1)) <= 24:
        return f"every {int(m.group(1))}h"
    raise ConfigError(f"wake must be 'auto' or 'every <N>h' with N from 1 to 24, got '{text}'")


def machine_zone() -> str:
    """The machine's IANA zone name, or UTC when it cannot be told."""
    candidates = []
    try:
        import tzlocal   # optional: present as a dependency of other packages
        candidates.append(tzlocal.get_localzone_name())
    except Exception:
        pass
    candidates.append(os.environ.get("TZ", ""))
    candidates.append(getattr(datetime.now().astimezone().tzinfo, "key", ""))
    for name in candidates:
        if name:
            try:
                ZoneInfo(name)
                return name
            except Exception:
                continue
    return "UTC"


def path_of(data_dir: Path) -> Path:
    return Path(data_dir) / "scheduling" / "schedule.json"


def _topic_from_json(path: Path, tid: str, raw: dict) -> TopicSchedule:
    try:
        anchor = raw.get("anchor")
        return TopicSchedule(
            fetch=parse_rule(raw.get("fetch", "off"), "fetch"),
            report=parse_rule(raw.get("report", "off"), "report"),
            notify=[str(n) for n in raw.get("notify", [])],
            paused=bool(raw.get("paused", False)),
            notify_when_empty=raw.get("notify_when_empty"),
            anchor=date.fromisoformat(anchor) if anchor else None,
            desktop_task=bool(raw.get("desktop_task", False)),
            set_at=raw.get("set_at"))
    except (ConfigError, ValueError, AttributeError, TypeError) as exc:
        raise ConfigError(f"{path}: topic '{tid}': {exc}") from exc


def load(data_dir: Path) -> ScheduleFile:
    path = path_of(data_dir)
    if not path.exists():
        return ScheduleFile(timezone=machine_zone())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path} is unreadable ({exc}); fix or delete it") from exc
    try:
        sf = ScheduleFile(
            timezone=str(raw.get("timezone") or machine_zone()),
            wake=check_wake(raw.get("wake", "auto")),
            api_spend_limit_usd=float(raw.get("api_spend_limit_usd",
                                              raw.get("budget_usd", DEFAULT_API_SPEND_LIMIT_USD))),
            session_max_total_tokens=int(raw.get("session_max_total_tokens", DEFAULT_SESSION_MAX_TOTAL_TOKENS)),
            session_max_repeated_calls=int(raw.get("session_max_repeated_calls",
                                                   DEFAULT_SESSION_MAX_REPEATED_CALLS)),
            session_max_tool_calls=int(raw.get("session_max_tool_calls", DEFAULT_SESSION_MAX_TOOL_CALLS)),
            session_ceiling_usd=float(raw.get("session_ceiling_usd", DEFAULT_SESSION_CEILING_USD)),
            notify_when_empty=str(raw.get("notify_when_empty", "on")),
            channels=dict(raw.get("channels") or {}),
            installed=bool(raw.get("installed", False)))
        zone(sf.timezone)
        for tid, t in (raw.get("topics") or {}).items():
            sf.topics[tid] = _topic_from_json(path, tid, t)
    except (ConfigError, ValueError, TypeError) as exc:
        if str(path) in str(exc):
            raise
        raise ConfigError(f"{path}: {exc}") from exc
    return sf


def to_json(sf: ScheduleFile) -> dict:
    return {
        "timezone": sf.timezone, "wake": sf.wake, "api_spend_limit_usd": sf.api_spend_limit_usd,
        "session_max_total_tokens": sf.session_max_total_tokens,
        "session_max_repeated_calls": sf.session_max_repeated_calls,
        "session_max_tool_calls": sf.session_max_tool_calls,
        "session_ceiling_usd": sf.session_ceiling_usd,
        "notify_when_empty": sf.notify_when_empty, "channels": sf.channels,
        "installed": sf.installed,
        "topics": {tid: {"fetch": str(t.fetch), "report": str(t.report), "notify": t.notify,
                         "paused": t.paused, "notify_when_empty": t.notify_when_empty,
                         "anchor": t.anchor.isoformat() if t.anchor else None,
                         "desktop_task": t.desktop_task, "set_at": t.set_at}
                   for tid, t in sf.topics.items()}}


def save(data_dir: Path, sf: ScheduleFile) -> Path:
    path = path_of(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(to_json(sf), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
