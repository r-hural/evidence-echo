"""`ev notify`: send a report summary, an "empty" or "failed" message, or a test message.

Channels used are the topic's `notify` list in schedule.json. A channel that fails is logged
(run log, kind "notify") and reported in the JSON; the command exits 1 only when every channel failed.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from evidence_echo.core import config, topics
from evidence_echo.core.settings import ConfigError
from evidence_echo.notify import NotifyError, summary
from evidence_echo.notify import email as email_channel
from evidence_echo.notify import telegram as telegram_channel
from evidence_echo.render import html as html_render
from evidence_echo.scheduling import runlog, spec

CHANNELS = ("telegram", "email")
ATTACHMENTS = "attachments"      # <data>/scheduling/attachments: the phone copy of the last report sent
# test hooks: a fake httpx client for Telegram, a fake SMTP factory for email
DEPS = {"telegram_client": None, "smtp_factory": None}


def add_parsers(sub) -> None:
    np = sub.add_parser("notify", help="send a report summary by the topic's channels")
    np.add_argument("--render-output", metavar="FILE", help="the JSON `report --render` printed")
    np.add_argument("--empty", action="store_true", help="nothing new: send the empty-report message")
    np.add_argument("--failed", metavar="TEXT", help="the run failed: send one message a day")
    np.add_argument("words", nargs="*", metavar="test <channel> | telegram-chat-id")


def _secrets(ws) -> dict:
    keys = config.delivery_keys(ws.plugin_data_dir)
    return {"telegram": config._key("TELEGRAM_BOT_TOKEN", keys), "email": config._key("SMTP_PASSWORD", keys)}


def _deliver(name: str, sf, secrets: dict, subject: str, text: str, tg_html: str, mail_html: str,
             attachment: Path | None) -> None:
    channel = sf.channels.get(name)
    if channel is None:
        raise NotifyError(f"{name} is not set up (use /evidence-echo:schedule or ev schedule channel {name} ...)")
    if name == "telegram":
        if not channel.get("chat_id"):
            raise NotifyError("Telegram chat id missing (ev schedule channel telegram --chat-id ...)")
        telegram_channel.send(secrets["telegram"], str(channel["chat_id"]), tg_html, attachment,
                              client=DEPS["telegram_client"])
    elif name == "email":
        email_channel.send(channel, secrets["email"], subject, text, mail_html, attachment,
                           smtp_factory=DEPS["smtp_factory"])
    else:
        raise NotifyError(f"unknown channel {name}")


def _send_all(ws, sf, topic: str, names: list[str], trigger: str, subject: str, text: str, tg_html: str,
              mail_html: str, attachment: Path | None) -> dict:
    secrets = _secrets(ws)
    sent, failed = [], []
    for name in names:
        started = runlog.now_iso()
        try:
            _deliver(name, sf, secrets, subject, text, tg_html, mail_html, attachment)
            sent.append(name)
            outcome, error = "ok", None
        except NotifyError as exc:
            failed.append({"channel": name, "error": str(exc)})
            outcome, error = "failed", f"message not sent ({name}): {exc}"
        try:
            runlog.append(ws.data_dir, {"topic": topic, "kind": "notify", "trigger": trigger, "started": started,
                                        "finished": runlog.now_iso(), "outcome": outcome, "error": error,
                                        "channel": name})
        except OSError:
            pass
    return {"sent": sent, "failed": failed}


def _finish(result: dict, names: list[str]) -> dict:
    if names and not result["sent"]:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        raise NotifyError("every channel failed: " + "; ".join(f["error"] for f in result["failed"]))
    return result


def _topic_name(ws, topic: str) -> str:
    try:
        return topics.load_topic(ws.topics_dir, topic).name
    except (ConfigError, OSError):
        return topic


def _channels_of(sf, topic: str) -> list[str]:
    t = sf.topics.get(topic)
    return [n for n in (t.notify if t else list(sf.channels)) if n in CHANNELS]


def _period_label(sf, topic: str, date: str) -> str:
    t = sf.topics.get(topic)
    kind = t.report.kind if t else "off"
    return (f"{kind} report" if kind in ("daily", "weekly", "biweekly", "monthly") else "report") + f" · {date}"


def _period(sf, topic: str) -> str:
    t = sf.topics.get(topic)
    return summary.period_words(t.report.kind if t else "off")


def _duplicate_pairs(cfg) -> list[dict]:
    from evidence_echo import api
    return api.studies_duplicates(cfg)["pairs"]


def _phone_attachment(data_dir: Path, report: Path, pairs: list[dict]) -> Path:
    """Write the phone copy of a report to <data>/scheduling/attachments/, replacing older copies."""
    folder = data_dir / "scheduling" / ATTACHMENTS
    if report.resolve().parent == folder.resolve():
        return report                 # the saved report lives here: attach it as it is
    copy = html_render.phone_copy(report.read_text(encoding="utf-8"), pairs)   # build first, delete after
    folder.mkdir(parents=True, exist_ok=True)
    keep = report.resolve()
    for old in folder.iterdir():
        if old.is_file() and old.resolve() != keep:
            old.unlink()
    target = folder / report.name
    target.write_text(copy, encoding="utf-8", newline="\n")
    return target


def _attachment_for(ws, report: Path, pairs: list[dict]) -> Path:
    """The phone copy of the saved report; the saved report itself when the copy cannot be made."""
    try:
        return _phone_attachment(ws.data_dir, report, pairs)
    except Exception:
        return report


REPORT_SUFFIXES = {".html", ".md"}


def _report_folders(topic: str) -> list[Path]:
    cfg = config.load(topic)
    return [d for d in (cfg.reports_dir, cfg.md_dir) if d is not None]


def _is_report_file(topic: str, path: Path) -> bool:
    """True for an .html or .md file inside the topic's reports folders (symlinks resolved)."""
    if path.suffix.lower() not in REPORT_SUFFIXES:
        return False
    try:
        real = path.resolve(strict=True)
        folders = [Path(d).resolve() for d in _report_folders(topic)]
    except Exception:
        return False
    return any(real.is_relative_to(d) for d in folders)


def send_report(ws, topic: str, output_file: str) -> dict:
    try:
        out = json.loads(Path(output_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"--render-output {output_file}: {exc}") from exc
    if not isinstance(out, dict):
        raise ConfigError(f"--render-output {output_file}: expected the JSON `report --render` printed")
    if out.get("skipped"):
        return send_empty(ws, topic)
    s = out.get("summary")
    if not isinstance(s, dict):
        raise ConfigError(f"--render-output {output_file}: no summary in it (is it the output of `report --render`?)")
    sf = spec.load(ws.data_dir)
    names = _channels_of(sf, topic)
    if not names:
        return {"sent": [], "failed": [], "note": f"no channel is set up for {topic}"}
    pairs = None                      # None: the lookup failed
    try:
        cfg = config.load(topic)
        try:
            pairs = _duplicate_pairs(cfg)
        except Exception:
            pass
        pending = summary.pending(cfg, count_duplicates=pairs is None)
        if pairs is not None:
            pending["duplicates"] = len(pairs)
    except Exception:
        pending = {}
    problems = s.get("source_errors", [])
    label = _period_label(sf, topic, s.get("date", ""))
    attachment = Path(s["report_file"]) if s.get("report_file") else None
    if attachment is not None and not _is_report_file(topic, attachment):
        attachment = None   # the render output is written by the session: attach only a saved report
    if attachment is not None and attachment.suffix != ".html":
        attachment = attachment if attachment.is_file() else None
    elif attachment is not None and attachment.is_file():
        attachment = _attachment_for(ws, attachment, pairs or [])   # no section when the lookup failed
    result = _send_all(ws, sf, topic, names, "report", summary.subject(s),
                       summary.text(s, label, problems, pending),
                       summary.telegram_html(s, label, problems, pending),
                       summary.email_html(s, label, problems, pending), attachment)
    return _finish(result, names)


def send_empty(ws, topic: str) -> dict:
    sf = spec.load(ws.data_dir)
    t = sf.topics.get(topic)
    when = (t.notify_when_empty if t and t.notify_when_empty else None) or sf.notify_when_empty
    if when != "on":
        return {"sent": [], "failed": [], "skipped": "notify_when_empty is off"}
    names = _channels_of(sf, topic)
    if not names:
        return {"sent": [], "failed": [], "note": f"no channel is set up for {topic}"}
    name = _topic_name(ws, topic)
    message = summary.empty_text(name, _period(sf, topic))
    result = _send_all(ws, sf, topic, names, "empty", f"Evidence Echo · {name}: nothing new", message,
                       summary.telegram_plain(message), summary.email_html_plain(message), None)
    return _finish(result, names)


def send_failed(ws, topic: str, error: str) -> dict:
    sf = spec.load(ws.data_dir)
    now = datetime.now(timezone.utc)
    if runlog.failed_notice_today(ws.data_dir, topic, now):
        return {"sent": [], "failed": [], "skipped": "a failure message already went out today"}
    names = _channels_of(sf, topic)
    if not names:
        return {"sent": [], "failed": [], "note": f"no channel is set up for {topic}"}
    name = _topic_name(ws, topic)
    message = summary.failed_text(name, error)
    result = _send_all(ws, sf, topic, names, "failed-message", f"Evidence Echo · {name}: report failed",
                       message, summary.telegram_plain(message), summary.email_html_plain(message), None)
    return _finish(result, names)


def send_test(ws, name: str) -> dict:
    if name not in CHANNELS:
        raise ConfigError(f"unknown channel '{name}' (channels: {', '.join(CHANNELS)})")
    sf = spec.load(ws.data_dir)
    message = "Evidence Echo test message: this channel works."
    secrets = _secrets(ws)
    try:
        _deliver(name, sf, secrets, "Evidence Echo test message", message, summary.telegram_plain(message),
                 summary.email_html_plain(message), None)
    except NotifyError as exc:
        raise NotifyError(str(exc)) from None
    return {"sent": [name], "failed": []}


def chat_ids(ws) -> dict:
    return {"chats": telegram_channel.chats(_secrets(ws)["telegram"], client=DEPS["telegram_client"])}


def dispatch(args) -> dict:
    ws = config.workspace()
    words = list(args.words or [])
    if words[:1] == ["test"]:
        if len(words) != 2:
            raise ConfigError("usage: ev notify test telegram|email")
        return send_test(ws, words[1])
    if words == ["telegram-chat-id"]:
        return chat_ids(ws)
    if words:
        raise ConfigError(f"unknown notify command '{' '.join(words)}' (test <channel>, telegram-chat-id)")
    chosen = [bool(args.render_output), bool(args.empty), args.failed is not None]
    if sum(chosen) != 1:
        raise ConfigError("notify needs exactly one of --render-output FILE, --empty or --failed TEXT")
    topic = config.select_topic(ws, args.topic)
    if args.render_output:
        return send_report(ws, topic, args.render_output)
    if args.empty:
        return send_empty(ws, topic)
    return send_failed(ws, topic, args.failed)
