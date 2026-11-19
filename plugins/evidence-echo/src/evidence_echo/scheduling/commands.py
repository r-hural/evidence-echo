"""The `ev schedule ...` commands. Printed as JSON by the CLI; they never fail a session on a
file problem in the scheduling folder (one line on stderr, exit 0), except bad input (exit 2)."""
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evidence_echo.core import config, topics
from evidence_echo.core.settings import ConfigError
from evidence_echo.scheduling import installer, lock, runlog, slots, spec, status

CHANNELS = ("telegram", "email")
LIMIT_FLAGS = {"--api-spend-limit-usd": float, "--session-max-total-tokens": int,
               "--session-max-repeated-calls": int, "--session-max-tool-calls": int,
               "--session-ceiling-usd": float}


def add_parsers(sub) -> None:
    sub.add_parser("run-due", help="start the fetch and report runs the schedules say are due "
                                   "(what the OS task calls)")
    sp = sub.add_parser("schedule", help="scheduled fetch and report runs").add_subparsers(
        dest="action", required=True)
    sp.add_parser("begin", help="take the topic's run lock (a report run starts)")
    se = sp.add_parser("end", help="log a finished report run and release the lock")
    se.add_argument("--outcome", required=True, choices=["ok", "empty", "failed"])
    se.add_argument("--error", default=None)
    se.add_argument("--trigger", choices=["manual", "scheduled"], default=None)
    sp.add_parser("show", help="every schedule, the channels and each topic's next slots")
    ss = sp.add_parser("set", help="set a topic's schedule (with --topic)")
    ss.add_argument("--fetch", metavar="RULE", help=spec.ALLOWED["fetch"])
    ss.add_argument("--report", metavar="RULE", help=spec.ALLOWED["report"])
    ss.add_argument("--notify", metavar="CHANNELS", help="telegram,email or none")
    ss.add_argument("--notify-when-empty", dest="topic_when_empty", choices=["on", "off", "default"])
    ss.add_argument("--desktop-task", choices=["on", "off"])
    sp.add_parser("pause", help="pause a topic's schedule (with --topic)")
    sp.add_parser("resume", help="resume a topic's schedule (with --topic)")
    sp.add_parser("remove", help="remove a topic's schedule (with --topic)")
    sc = sp.add_parser("config", help="time zone, wake times, usage limits, empty-report message")
    sc.add_argument("--timezone")
    sc.add_argument("--wake", help='auto or "every <N>h"')
    for flag, kind in LIMIT_FLAGS.items():
        sc.add_argument(flag, type=kind, dest=flag[2:].replace("-", "_"))
    sc.add_argument("--notify-when-empty", dest="global_when_empty", choices=["on", "off"])
    ch = sp.add_parser("channel", help="set up or remove a delivery channel").add_subparsers(
        dest="channel", required=True)
    ct = ch.add_parser("telegram")
    ct.add_argument("--chat-id", required=True, dest="chat_id")
    ce = ch.add_parser("email")
    ce.add_argument("--host", required=True)
    ce.add_argument("--port", required=True, type=int)
    ce.add_argument("--user", required=True)
    ce.add_argument("--from", required=True, dest="from_addr")
    ce.add_argument("--to", required=True)
    ce.add_argument("--security", choices=["starttls", "ssl"], default="starttls")
    ch.add_parser("remove").add_argument("name", choices=CHANNELS)
    sp.add_parser("install", help="register the OS task that wakes the scheduler")
    sp.add_parser("uninstall", help="remove the OS task")
    sp.add_parser("status", help="the OS task, its wake times and the next wake")


def _warn(message: str) -> None:
    print(f"evidence-echo: {message}", file=sys.stderr)


def _topic(ws, requested: str | None) -> str:
    return config.select_topic(ws, requested)


def begin(data_dir: Path, topic: str) -> dict:
    try:
        held = lock.acquire(data_dir, topic)
    except OSError as exc:
        _warn(f"scheduling folder not writable ({exc}); running without a lock")
        return {"ok": True, "warning": "no lock"}
    if held is None:
        return {"ok": True}
    return {"ok": False, "held_by": held}


def end(data_dir: Path, topic: str, outcome: str, error: str | None, trigger: str | None) -> dict:
    now = datetime.now(timezone.utc)
    scheduled = os.environ.get(lock.ENV_SCHEDULED) == topic
    entry = {"topic": topic, "kind": "report", "trigger": trigger or ("scheduled" if scheduled else "manual"),
             "slot": None, "started": lock.started_at(data_dir, topic) or now.isoformat(),
             "finished": now.isoformat(), "outcome": outcome, "error": error}
    problems = []
    try:
        runlog.append(data_dir, entry)
    except OSError as exc:
        problems.append(f"run log not written ({exc})")
    if not scheduled:   # a scheduled run's parent (run-due) holds the lock and frees it
        lock.release_manual(data_dir, topic)
    try:
        status.refresh(data_dir, spec.load(data_dir), now)
    except (OSError, ConfigError) as exc:
        problems.append(f"status not refreshed ({exc})")
    for p in problems:
        _warn(p)
    return {"ok": not problems, "logged": outcome, **({"problems": problems} if problems else {})}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local(dt, tz) -> dict | None:
    return None if dt is None else {"local": dt.astimezone(tz).isoformat(timespec="minutes"),
                                    "utc": dt.isoformat(timespec="minutes")}


def show(data_dir: Path) -> dict:
    sf = spec.load(data_dir)
    tz, now = sf.zone(), _now()
    out = spec.to_json(sf)
    for tid, t in sf.topics.items():
        out["topics"][tid]["next_fetch"] = _local(slots.next_slot(t.fetch, now, tz, t.anchor), tz)
        out["topics"][tid]["next_report"] = _local(slots.next_slot(t.report, now, tz, t.anchor), tz)
    out["next_wake"] = _local(status.next_wake(sf, now), tz)
    return out


def _known(ws) -> list[str]:
    return topics.topic_ids(ws.topics_dir)


def _save(ws, sf: spec.ScheduleFile) -> None:
    """Write the file (dropping topics that no longer exist), refresh the status, re-time the OS task."""
    known = set(_known(ws))
    sf.topics = {tid: t for tid, t in sf.topics.items() if tid in known}
    spec.save(ws.data_dir, sf)
    status.refresh(ws.data_dir, sf, _now())
    if sf.installed:
        installer.sync(ws.data_dir)


def _default_fetch(report: spec.Rule) -> spec.Rule:
    """A new topic's fetch follows its report when the fetch grammar has that form; otherwise the
    report run does all the fetching."""
    return report if report.kind in ("daily", "weekly") else spec.OFF


def _first_slot_date(rule: spec.Rule, tz, now: datetime):
    local = now.astimezone(tz).date()
    for ahead in range(8):
        d = local + timedelta(days=ahead)
        found = slots.slots_on(rule, d, tz, anchor=d)   # d is the candidate first slot, in either fortnight week
        if d.weekday() == rule.weekday and found and found[0] > now:
            return d
    return local


def set_topic(ws, topic: str, fetch, report, notify, when_empty, desktop_task) -> dict:
    if not ws.configured:
        raise config.SetupRequired(config.NOT_SET_UP)
    ids = _known(ws)
    if topic not in ids:
        raise ConfigError(f"unknown topic '{topic}' (topics: {', '.join(ids) or 'none'})")
    if all(v is None for v in (fetch, report, notify, when_empty, desktop_task)):
        raise ConfigError("nothing to set: pass --fetch, --report, --notify, --notify-when-empty "
                          "or --desktop-task")
    sf = spec.load(ws.data_dir)
    tz, now = sf.zone(), _now()
    new_fetch = spec.parse_rule(fetch, "fetch") if fetch is not None else None
    new_report = spec.parse_rule(report, "report") if report is not None else None
    names = None
    if notify is not None:
        names = [] if notify.strip().lower() == "none" else [n.strip().lower() for n in notify.split(",") if n.strip()]
        bad = [n for n in names if n not in CHANNELS]
        if bad:
            raise ConfigError(f"unknown channel {', '.join(bad)} (channels: {', '.join(CHANNELS)}, or none)")
    t = sf.topics.get(topic)
    if t is None:
        t = spec.TopicSchedule(notify=[c for c in CHANNELS if c in sf.channels])
        sf.topics[topic] = t
        if new_report is not None and new_fetch is None:
            t.fetch = _default_fetch(new_report)
    if new_fetch is not None:
        t.fetch = new_fetch
    if new_report is not None:
        changed = new_report != t.report
        t.report = new_report
        if new_report.kind != "biweekly":
            t.anchor = None
        elif t.anchor is None or changed:
            t.anchor = _first_slot_date(new_report, tz, now)
    if names is not None:
        t.notify = names
    if when_empty is not None:
        t.notify_when_empty = None if when_empty == "default" else when_empty
    if desktop_task is not None:
        t.desktop_task = desktop_task == "on"
    t.set_at = now.isoformat()
    _save(ws, sf)
    return show(ws.data_dir)


def _topic_action(ws, topic: str, action: str) -> dict:
    sf = spec.load(ws.data_dir)
    if topic not in sf.topics:
        raise ConfigError(f"topic '{topic}' has no schedule yet (use: schedule set --topic {topic} ...)")
    if action == "remove":
        del sf.topics[topic]
    else:
        sf.topics[topic].paused = action == "pause"
        sf.topics[topic].set_at = _now().isoformat()
    _save(ws, sf)
    return show(ws.data_dir)


def set_config(ws, tz, wake, limits: dict[str, float | int], when_empty) -> dict:
    sf = spec.load(ws.data_dir)
    if tz is not None:
        spec.zone(tz)
        sf.timezone = tz
    if wake is not None:
        sf.wake = spec.check_wake(wake)
    for name, value in limits.items():
        if not math.isfinite(value) or value <= 0:
            raise ConfigError(f"--{name.replace('_', '-')} must be above 0")
        setattr(sf, name, value)
    if when_empty is not None:
        sf.notify_when_empty = when_empty
    _save(ws, sf)
    return show(ws.data_dir)


def set_channel(ws, args) -> dict:
    sf = spec.load(ws.data_dir)
    if args.channel == "remove":
        sf.channels.pop(args.name, None)
        for t in sf.topics.values():
            t.notify = [n for n in t.notify if n != args.name]
    elif args.channel == "telegram":
        sf.channels["telegram"] = {"chat_id": args.chat_id}
    else:
        sf.channels["email"] = {"host": args.host, "port": args.port, "user": args.user,
                                "from": args.from_addr, "to": args.to, "security": args.security}
    _save(ws, sf)
    return show(ws.data_dir)


def dispatch(args) -> dict:
    ws = config.workspace()
    data_dir = ws.data_dir
    if args.action in ("begin", "end"):
        topic = _topic(ws, args.topic)
        if args.action == "begin":
            return begin(data_dir, topic)
        return end(data_dir, topic, args.outcome, args.error, args.trigger)
    if args.action == "show":
        return show(data_dir)
    if args.action == "set":
        return set_topic(ws, _topic(ws, args.topic), args.fetch, args.report, args.notify,
                         args.topic_when_empty, args.desktop_task)
    if args.action in ("pause", "resume", "remove"):
        return _topic_action(ws, _topic(ws, args.topic), args.action)
    if args.action == "config":
        limits = {f[2:].replace("-", "_"): getattr(args, f[2:].replace("-", "_")) for f in LIMIT_FLAGS}
        limits = {k: v for k, v in limits.items() if v is not None}
        return set_config(ws, args.timezone, args.wake, limits, args.global_when_empty)
    if args.action == "channel":
        return set_channel(ws, args)
    if args.action == "install":
        return installer.install(data_dir)
    if args.action == "uninstall":
        return installer.uninstall(data_dir)
    if args.action == "status":
        return installer.status_report(data_dir)
    raise ConfigError(f"unknown schedule action {args.action}")
