import dataclasses
import json
import re
import smtplib
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import httpx
import pytest

from evidence_echo import cli
from evidence_echo.notify import NotifyError, summary
from evidence_echo.notify import commands as notify_commands
from evidence_echo.notify import email as email_channel
from evidence_echo.notify import telegram as telegram_channel
from evidence_echo.render import model
from evidence_echo.scheduling import runlog, spec
from tests.conftest import write_plugin_keys

SECRET_TOKEN = "123456:SECRET-token"


# --- helpers ----------------------------------------------------------------------------------

def item(tier="B", title="Title tag rewrites: +4.2% clicks", url="https://www.example.com/post"):
    return {"tier": tier, "title": title, "url": url}


def summ(top=None, new=None, **over):
    top = top if top is not None else [item()]
    base = {"topic": "SEO", "topic_id": "seo", "date": "2026-11-14", "new": len(top) if new is None else new,
            "disputed": 1, "best_tier": "B", "top": top, "source_errors": [], "report_file": "D:/r/seo.html"}
    base.update(over)
    return base


class TagChecker(HTMLParser):
    """Telegram HTML must be balanced and use only its small tag set."""
    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ("b", "a"):
            self.bad.append(tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append("/" + tag)


def assert_valid_telegram(message):
    checker = TagChecker()
    checker.feed(message)
    assert checker.bad == [] and checker.stack == []
    assert not re.search(r"<(?!/?(?:b|a)[ >])", message)   # no raw "<" outside the two tags


def tg_transport(calls, ok=True, description="Bad Request: chat not found"):
    def handler(request: httpx.Request):
        calls.append(request)
        if ok:
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(400, json={"ok": False, "description": description})
    return httpx.Client(transport=httpx.MockTransport(handler))


class FakeSMTP:
    instances = []

    def __init__(self, host, port, security):
        self.host, self.port, self.security = host, port, security
        self.steps, self.messages = [], []
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.steps.append("starttls")

    def login(self, user, password):
        self.steps.append(("login", user, password))

    def send_message(self, msg):
        self.messages.append(msg)

    def quit(self):
        self.steps.append("quit")


EMAIL = {"host": "smtp.example.com", "port": 587, "user": "me", "from": "me@example.com",
         "to": "you@example.com", "security": "starttls"}


# --- summary ----------------------------------------------------------------------------------

def test_from_report_orders_by_grade_and_takes_five(db):
    entries = [model.Entry(study_id=i, title=f"T{i}", summary="", tier=t, disputed=False, skeptic_tiers=[],
                           no_quote_only=False, dispute_reason=None, skeptic_notes=[], view={}, series=None,
                           editions=[], research_timeframe=None, published=None, primary_url="https://p.example/x",
                           coverage=[model.Link("Blog", f"https://blog.example/{i}")] if i else [])
               for i, t in enumerate(["C", "A", "B", "A", "D", "B", "C", None])]
    disputed = dataclasses.replace(entries[0], study_id=99, title="Dispute", tier="A", disputed=True)
    rep = model.Report("seo", "SEO", "2026-11-14", entries, [disputed], [], ["a: b"])
    s = summary.from_report(rep, None, "r.md")
    assert [(i["tier"], i["title"]) for i in s["top"]] == [("A", "T1"), ("A", "T3"), ("B", "T2"), ("B", "T5"), ("C", "T0")]
    assert s["new"] == 9 and s["disputed"] == 1 and s["best_tier"] == "A"
    only = summary.from_report(model.Report("seo", "SEO", "2026-11-14", [], [disputed], [], []), None, "r.md")
    assert only["top"] == [{"tier": "A", "title": "Dispute", "url": "", "disputed": True}] or only["top"][0]["disputed"]
    assert s["report_file"] == "r.md" and s["source_errors"] == ["a: b"]
    assert s["top"][4]["url"] == "https://p.example/x"          # no coverage link: the primary source
    assert summary.from_report(rep, "r.html", "r.md")["report_file"] == "r.html"


def test_plain_text_layout():
    out = summary.text(summ([item(), item("C", "Second", "https://x.example/2")], new=6), "weekly report · 2026-11-14",
                       ["Apify token missing (plugin settings): A, B"], {"splits": 2, "duplicates": 3})
    lines = out.splitlines()
    assert lines[0] == "Evidence Echo · SEO · weekly report · 2026-11-14"
    assert lines[1] == "6 new studies · best grade B · 1 disputed"
    assert "B  Title tag rewrites: +4.2% clicks — example.com" in lines
    assert "+4 more in the report" in lines
    assert ("Waiting for you: 2 split proposals, 3 possible duplicates "
            "(see the report; decide in Claude Code: 'review Evidence Echo duplicates')") in lines
    assert "Source errors: Apify token missing (plugin settings): A, B" in lines
    assert lines[-1] == "Full report attached · saved on this computer as seo.html"


def test_empty_and_failed_text():
    assert summary.empty_text("SEO", "this week") == "SEO: nothing new this week."
    assert summary.failed_text("SEO", "budget exceeded") == "SEO: report failed — budget exceeded"


def test_telegram_html_with_300_hostile_entries_stays_valid_and_short():
    """Review Focus 5."""
    hostile = [item("A", f"<b>&amp; \"x\" title {i} " + "long " * 80, f"https://e.example/?a=1&b=<{i}>") for i in range(300)]
    s = summ(hostile, new=300)
    out = summary.telegram_html(s, "weekly report · 2026-11-14", ["Bad <source> & co: " + "x" * 500] * 9,
                                {"splits": 1, "duplicates": 0})
    assert len(out) <= 4096
    assert_valid_telegram(out)
    assert re.search(r"\+\d+ more in the report", out)
    assert "<b>&amp;" not in out and "&lt;b&gt;&amp;amp;" in out


def test_telegram_html_short_message_lists_everything():
    out = summary.telegram_html(summ([item(), item("C", "Other", "https://o.example/p")]), "report", [], None)
    assert_valid_telegram(out)
    assert "more in the report" not in out
    assert '<a href="https://www.example.com/post">example.com</a>' in out


def test_non_http_links_are_dropped():
    out = summary.telegram_html(summ([item(url="javascript:alert(1)")]), "report", [], None)
    assert "javascript" not in out
    assert "javascript" not in summary.email_html(summ([item(url="javascript:alert(1)")]), "report", [], None)


def test_email_html_uses_inline_css_only():
    out = summary.email_html(summ(), "weekly report", ["x: y"], {"splits": 1})
    assert "<style" not in out and "<script" not in out and 'style="' in out and "x: y" in out


# --- channels ---------------------------------------------------------------------------------

def test_telegram_send_message_then_document(tmp_path):
    report = tmp_path / "seo.html"
    report.write_text("<html>report</html>", encoding="utf-8")
    calls = []
    telegram_channel.send(SECRET_TOKEN, "42", "<b>hi</b>", report, client=tg_transport(calls))
    assert [c.url.path.rsplit("/", 1)[1] for c in calls] == ["sendMessage", "sendDocument"]
    assert b"parse_mode=HTML" in calls[0].content and b"chat_id=42" in calls[0].content
    assert b"<html>report</html>" in calls[1].content and b'filename="seo.html"' in calls[1].content


def test_telegram_without_attachment_sends_one_call():
    calls = []
    telegram_channel.send(SECRET_TOKEN, "42", "x", None, client=tg_transport(calls))
    assert len(calls) == 1


def test_telegram_error_carries_the_api_description_without_the_token():
    with pytest.raises(NotifyError) as exc:
        telegram_channel.send(SECRET_TOKEN, "42", "x", None,
                              client=tg_transport([], ok=False, description=f"bad {SECRET_TOKEN}"))
    assert "chat not found" not in str(exc.value) and SECRET_TOKEN not in str(exc.value)
    with pytest.raises(NotifyError, match="chat not found"):
        telegram_channel.send(SECRET_TOKEN, "42", "x", None, client=tg_transport([], ok=False))


def test_telegram_network_error_redacts_the_token():
    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")
    client = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(NotifyError) as exc:
        telegram_channel.send(SECRET_TOKEN, "42", "x", None, client=client)
    assert SECRET_TOKEN not in str(exc.value)


def test_missing_token_names_the_plugin_setting():
    with pytest.raises(NotifyError, match="plugin settings"):
        telegram_channel.send("", "42", "x")
    with pytest.raises(NotifyError, match="plugin settings"):
        email_channel.send(EMAIL, "", "s", "t", "<p>t</p>", smtp_factory=FakeSMTP)


def test_telegram_chats_lists_private_chats_only():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": [
            {"message": {"chat": {"id": 7, "type": "private", "first_name": "Ann", "last_name": "B"}}},
            {"message": {"chat": {"id": 7, "type": "private", "first_name": "Ann", "last_name": "B"}}},
            {"message": {"chat": {"id": -9, "type": "group", "title": "G"}}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert telegram_channel.chats(SECRET_TOKEN, client=client) == [{"id": 7, "name": "Ann B"}]


def test_email_has_text_and_html_parts_and_the_attachment(tmp_path):
    FakeSMTP.instances.clear()
    report = tmp_path / "seo.html"
    report.write_text("<html>full</html>", encoding="utf-8")
    email_channel.send(EMAIL, "pw", "Subject", "plain body", "<p>html body</p>", report, smtp_factory=FakeSMTP)
    [smtp] = FakeSMTP.instances
    assert smtp.steps == ["starttls", ("login", "me", "pw"), "quit"] and (smtp.host, smtp.port) == ("smtp.example.com", 587)
    [msg] = smtp.messages
    assert msg["Subject"] == "Subject" and msg["To"] == "you@example.com"
    kinds = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in kinds and "text/html" in kinds
    [attached] = [p for p in msg.iter_attachments()]
    assert attached.get_filename() == "seo.html" and b"full" in attached.get_payload(decode=True)
    assert msg.get_body(("plain",)).get_content().strip() == "plain body"


def test_email_ssl_skips_starttls():
    FakeSMTP.instances.clear()
    email_channel.send(dict(EMAIL, security="ssl", port=465), "pw", "S", "t", "<p>t</p>", smtp_factory=FakeSMTP)
    assert FakeSMTP.instances[0].steps == [("login", "me", "pw"), "quit"]


def test_email_failure_is_a_notify_error_without_the_password():
    class Broken(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, f"bad credentials {password}".encode())
    with pytest.raises(NotifyError) as exc:
        email_channel.send(EMAIL, "pw-secret", "S", "t", "<p>t</p>", smtp_factory=Broken)
    assert "pw-secret" not in str(exc.value)
    with pytest.raises(NotifyError, match="missing host"):
        email_channel.send({k: v for k, v in EMAIL.items() if k != "host"}, "pw", "S", "t", "h", smtp_factory=FakeSMTP)


# --- the command ------------------------------------------------------------------------------

@pytest.fixture
def delivery(workspace, tmp_path, monkeypatch):
    """A workspace with both channels set up, the secrets in the session's keys file, fakes injected."""
    write_plugin_keys(tmp_path / "plugin-data", TELEGRAM_BOT_TOKEN=SECRET_TOKEN, SMTP_PASSWORD="pw")
    sf = spec.load(workspace)
    sf.channels = {"telegram": {"chat_id": "42"}, "email": dict(EMAIL)}
    sf.topics["seo"] = spec.TopicSchedule(report=spec.parse_rule("weekly mon 08:00", "report"),
                                          notify=["telegram", "email"])
    spec.save(workspace, sf)
    calls = []
    FakeSMTP.instances.clear()
    monkeypatch.setitem(notify_commands.DEPS, "telegram_client", tg_transport(calls))
    monkeypatch.setitem(notify_commands.DEPS, "smtp_factory", FakeSMTP)
    monkeypatch.setattr(notify_commands, "_report_folders", lambda topic: [tmp_path])   # reports live in tmp_path
    return workspace, calls


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def render_file(tmp_path, report_file, **over):
    out = {"path": "x.md", "summary": summ(report_file=str(report_file), **over)}
    path = tmp_path / "render.json"
    path.write_text(json.dumps(out), encoding="utf-8")
    return path


def test_notify_render_output_sends_to_both_channels(delivery, tmp_path, capsys):
    workspace, calls = delivery
    report = tmp_path / "seo.html"
    report.write_text("<html>r</html>", encoding="utf-8")
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, report)))
    assert code == 0 and json.loads(out) == {"sent": ["telegram", "email"], "failed": []}
    assert [c.url.path.rsplit("/", 1)[1] for c in calls] == ["sendMessage", "sendDocument"]
    assert b"<html>r</html>" in calls[1].content                                   # the HTML file
    [smtp] = FakeSMTP.instances
    assert smtp.messages[0]["Subject"].startswith("Evidence Echo · SEO: 1 new study")
    assert [r["outcome"] for r in runlog.read_all(workspace) if r["kind"] == "notify"] == ["ok", "ok"]


def test_a_failing_channel_is_logged_and_the_other_still_sends(delivery, tmp_path, capsys, monkeypatch):
    workspace, _ = delivery
    monkeypatch.setitem(notify_commands.DEPS, "telegram_client", tg_transport([], ok=False))
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, tmp_path / "none.html")))
    data = json.loads(out)
    assert code == 0 and data["sent"] == ["email"] and data["failed"][0]["channel"] == "telegram"
    failed = [r for r in runlog.read_all(workspace) if r["kind"] == "notify" and r["outcome"] == "failed"]
    assert len(failed) == 1 and SECRET_TOKEN not in json.dumps(failed)


def test_all_channels_failing_exits_1(delivery, tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(notify_commands.DEPS, "telegram_client", tg_transport([], ok=False))
    monkeypatch.setitem(notify_commands.DEPS, "smtp_factory", lambda *a: (_ for _ in ()).throw(OSError("down")))
    code, out, err = run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, tmp_path / "n.html")))
    assert code == 1 and "every channel failed" in err and len(json.loads(out)["failed"]) == 2


def test_missing_token_in_the_session_names_the_setting(delivery, tmp_path, capsys):
    workspace, _ = delivery
    write_plugin_keys(tmp_path / "plugin-data")          # no secrets
    code, out, err = run(capsys, "--topic", "seo", "notify", "--empty")
    assert code == 1 and "plugin settings" in err


def test_empty_message_respects_notify_when_empty(delivery, capsys):
    workspace, calls = delivery
    assert run(capsys, "--topic", "seo", "notify", "--empty")[0] == 0
    assert len(calls) == 1 and b"nothing+new+this+week" in calls[0].content.replace(b"%20", b"+")
    sf = spec.load(workspace)
    sf.topics["seo"].notify_when_empty = "off"
    spec.save(workspace, sf)
    calls.clear()
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--empty")
    assert code == 0 and json.loads(out)["skipped"] and calls == []
    sf.topics["seo"].notify_when_empty = None
    sf.notify_when_empty = "off"                          # the global value applies when the topic has none
    spec.save(workspace, sf)
    assert json.loads(run(capsys, "--topic", "seo", "notify", "--empty")[1])["skipped"]
    sf.topics["seo"].notify_when_empty = "on"             # the topic overrides the global value
    spec.save(workspace, sf)
    assert json.loads(run(capsys, "--topic", "seo", "notify", "--empty")[1])["sent"] == ["telegram", "email"]


def test_skipped_render_output_is_an_empty_message(delivery, tmp_path, capsys):
    _, calls = delivery
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"path": None, "skipped": "no_new_studies"}), encoding="utf-8")
    assert run(capsys, "--topic", "seo", "notify", "--render-output", str(path))[0] == 0
    assert len(calls) == 1


def test_only_one_failed_message_per_topic_per_day(delivery, capsys):
    workspace, calls = delivery
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--failed", "budget exceeded")
    assert code == 0 and json.loads(out)["sent"] == ["telegram", "email"] and len(calls) == 1
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--failed", "again")
    assert json.loads(out)["skipped"] and len(calls) == 1
    assert runlog.failed_notice_today(workspace, "seo", datetime.now(timezone.utc))


def test_notify_test_and_chat_id(delivery, capsys, monkeypatch):
    _, calls = delivery
    assert json.loads(run(capsys, "notify", "test", "telegram")[1]) == {"sent": ["telegram"], "failed": []}
    assert json.loads(run(capsys, "notify", "test", "email")[1])["sent"] == ["email"]
    assert run(capsys, "notify", "test", "pigeon")[0] == 2
    monkeypatch.setitem(notify_commands.DEPS, "telegram_client", httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok": True, "result": [
            {"message": {"chat": {"id": 5, "type": "private", "username": "me"}}}]}))))
    assert json.loads(run(capsys, "notify", "telegram-chat-id")[1]) == {"chats": [{"id": 5, "name": "me"}]}


def test_notify_needs_exactly_one_mode(workspace, capsys):
    assert run(capsys, "--topic", "seo", "notify")[0] == 2
    assert run(capsys, "--topic", "seo", "notify", "--empty", "--failed", "x")[0] == 2


def test_no_channel_configured_is_a_note_not_an_error(workspace, capsys):
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--empty")
    assert code == 0 and "no channel" in json.loads(out)["note"]


def test_render_output_carries_a_summary(workspace, capsys):
    """api.render_report adds the summary the notify command sends."""
    from tests.conftest import seed_topic_study
    seed_topic_study()
    code, out, _ = run(capsys, "--topic", "seo", "report")
    assert code == 0
    payload = json.loads(out)
    results = {"results": [{"study_id": i["study_id"], "title": "T: +4% clicks", "summary": "S."}
                           for i in payload["items"]]} if "items" in payload else {"results": []}
    f = Path(workspace).parent / "entries.json"
    f.write_text(json.dumps(results), encoding="utf-8")
    code, out, _ = run(capsys, "--topic", "seo", "report", "--render", str(f))
    rendered = json.loads(out)
    s = rendered["summary"]
    assert s["topic_id"] == "seo" and s["new"] >= 1 and s["top"][0]["title"] == "T: +4% clicks"
    assert s["top"][0]["disputed"] is True    # the seeded study has no quote: it is in the Disputed section
    assert s["report_file"].endswith((".html", ".md"))


# --- the phone copy ---------------------------------------------------------------------------

PAIRS = {"pairs": [{"keep": 1, "other": 2, "rule": "shared key numbers: 7%", "check": None,
                    "keep_study": {"id": 1, "canonical_name": "First", "findings": "f", "research_timeframe": None,
                                   "evidence_tier": "B", "editions": [], "series_name": None, "primary_url": None},
                    "other_study": {"id": 2, "canonical_name": "Second", "findings": "g", "research_timeframe": None,
                                    "evidence_tier": "C", "editions": [], "series_name": None, "primary_url": None}}]}
LOCAL_PAGE = ('<html><body><main><a href="seo.html">All studies</a> '
              '<a href="https://example.com/a">Article</a></main><footer class="ee-foot"></footer></body></html>')


def sent_document(calls):
    return [c for c in calls if c.url.path.endswith("/sendDocument")][0].content


def test_the_attachment_is_a_phone_copy_and_the_saved_report_is_unchanged(delivery, tmp_path, capsys, monkeypatch):
    workspace, calls = delivery
    monkeypatch.setattr(notify_commands, "_duplicate_pairs", lambda cfg: PAIRS["pairs"])
    report = tmp_path / "2026-11-19-report.html"
    report.write_text(LOCAL_PAGE, encoding="utf-8")
    assert run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, report)))[0] == 0
    body = sent_document(calls)
    assert b'href="seo.html"' not in body and b"https://example.com/a" in body
    assert b"Possible duplicates waiting for you" in body and b'filename="2026-11-19-report.html"' in body
    assert report.read_text(encoding="utf-8") == LOCAL_PAGE
    [smtp] = FakeSMTP.instances
    attached = [p for p in smtp.messages[0].iter_attachments()][0].get_content()
    assert 'href="seo.html"' not in attached


def test_the_pair_search_runs_once_and_gives_the_count(delivery, tmp_path, capsys, monkeypatch):
    from evidence_echo import api
    workspace, calls = delivery
    searches = []
    monkeypatch.setattr(notify_commands, "_duplicate_pairs", lambda cfg: searches.append(1) or PAIRS["pairs"] * 2)

    def no_count(*a, **k):
        raise AssertionError("the duplicates were counted a second time")
    monkeypatch.setattr(api, "studies_duplicates", no_count)
    report = tmp_path / "2026-11-19-report.html"
    report.write_text(LOCAL_PAGE, encoding="utf-8")
    assert run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, report)))[0] == 0
    assert len(searches) == 1
    sent = [c for c in calls if c.url.path.endswith("/sendMessage")][0].content
    assert "2 possible duplicates" in urllib.parse.unquote_plus(sent.decode())


def test_the_attachments_folder_keeps_only_the_newest_copy(delivery, tmp_path, capsys, monkeypatch):
    workspace, _ = delivery
    monkeypatch.setattr(notify_commands, "_duplicate_pairs", lambda cfg: [])
    folder = workspace / "scheduling" / "attachments"
    for name in ("2026-11-12-report.html", "2026-11-19-report.html"):
        report = tmp_path / name
        report.write_text(LOCAL_PAGE, encoding="utf-8")
        run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, report)))
    assert sorted(p.name for p in folder.iterdir()) == ["2026-11-19-report.html"]


@pytest.mark.parametrize("broken", ["pairs", "copy"])
def test_a_failing_copy_still_sends_the_saved_report(delivery, tmp_path, capsys, monkeypatch, broken):
    _, calls = delivery
    if broken == "pairs":
        monkeypatch.setattr(notify_commands, "_duplicate_pairs", lambda cfg: (_ for _ in ()).throw(RuntimeError("locked")))
    else:
        monkeypatch.setattr(notify_commands, "_phone_attachment", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    report = tmp_path / "2026-11-19-report.html"
    report.write_text(LOCAL_PAGE, encoding="utf-8")
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, report)))
    assert code == 0 and json.loads(out)["sent"] == ["telegram", "email"]
    body = sent_document(calls)
    assert b"https://example.com/a" in body
    if broken == "pairs":
        assert b'href="seo.html"' not in body      # the copy was still used, without the duplicates section


def test_a_report_inside_the_attachments_folder_is_sent_and_kept(delivery, tmp_path, capsys, monkeypatch):
    workspace, calls = delivery
    monkeypatch.setattr(notify_commands, "_duplicate_pairs", lambda cfg: PAIRS["pairs"])
    folder = workspace / "scheduling" / "attachments"
    folder.mkdir(parents=True)
    report = folder / "2026-11-19-report.html"
    report.write_text(LOCAL_PAGE, encoding="utf-8")
    other = folder / "old-report.html"
    other.write_text("old", encoding="utf-8")
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, report)))
    assert code == 0 and json.loads(out)["sent"] == ["telegram", "email"]
    assert b'href="seo.html"' in sent_document(calls) and b'filename="2026-11-19-report.html"' in sent_document(calls)
    assert report.read_text(encoding="utf-8") == LOCAL_PAGE
    assert other.read_text(encoding="utf-8") == "old"


def test_a_failing_phone_copy_keeps_the_older_copies(delivery, tmp_path, capsys, monkeypatch):
    workspace, calls = delivery
    monkeypatch.setattr(notify_commands, "_duplicate_pairs", lambda cfg: [])
    monkeypatch.setattr(notify_commands.html_render, "phone_copy",
                        lambda *a: (_ for _ in ()).throw(ValueError("bad page")))
    folder = workspace / "scheduling" / "attachments"
    folder.mkdir(parents=True)
    older = folder / "2026-11-12-report.html"
    older.write_text("older copy", encoding="utf-8")
    report = tmp_path / "2026-11-19-report.html"
    report.write_text(LOCAL_PAGE, encoding="utf-8")
    code, out, _ = run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, report)))
    assert code == 0 and json.loads(out)["sent"] == ["telegram", "email"]
    assert b'href="seo.html"' in sent_document(calls)       # the saved report went out
    assert older.read_text(encoding="utf-8") == "older copy"


def test_a_markdown_only_report_is_attached_as_it_is(delivery, tmp_path, capsys, monkeypatch):
    workspace, calls = delivery
    monkeypatch.setattr(notify_commands, "_duplicate_pairs", lambda cfg: PAIRS["pairs"])
    report = tmp_path / "2026-11-19-report.md"
    report.write_text("# Report\n[All](seo.html)\n", encoding="utf-8")
    run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, report)))
    assert b"[All](seo.html)" in sent_document(calls)
    assert not (workspace / "scheduling" / "attachments").exists()


def test_the_waiting_line_says_where_to_decide_duplicates():
    assert summary._waiting({"splits": 2, "duplicates": 3}) == (
        "Waiting for you: 2 split proposals, 3 possible duplicates "
        "(see the report; decide in Claude Code: 'review Evidence Echo duplicates')")
    assert summary._waiting({"splits": 1, "duplicates": 0}) == "Waiting for you: 1 split proposal"
    assert summary._waiting({}) is None


@pytest.mark.parametrize("name", ["keys.json", "secret.txt", "id_rsa"])
def test_only_a_saved_report_is_ever_attached(delivery, tmp_path, capsys, monkeypatch, name):
    _, calls = delivery
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    for path in (outside / "seo.html", tmp_path / name):
        path.write_text("SECRET", encoding="utf-8")
        code, out, _ = run(capsys, "--topic", "seo", "notify", "--render-output", str(render_file(tmp_path, path)))
        assert code == 0 and json.loads(out)["sent"] == ["telegram", "email"]
    assert all(c.url.path.endswith("sendMessage") for c in calls)                 # no document sent
    assert all(not m.get_payload() or b"SECRET" not in m.as_bytes() for smtp in FakeSMTP.instances
               for m in smtp.messages)


def test_report_folders_are_the_topics_reports_and_markdown_folders(workspace, monkeypatch):
    class Cfg:
        reports_dir, md_dir = Path("/r"), None
    monkeypatch.setattr(notify_commands.config, "load", lambda topic: Cfg)
    assert notify_commands._report_folders("seo") == [Path("/r")]
    Cfg.md_dir = Path("/md")
    assert notify_commands._report_folders("seo") == [Path("/r"), Path("/md")]
