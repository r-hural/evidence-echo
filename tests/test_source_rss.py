from datetime import datetime, timedelta, timezone

import httpx
import pytest

from evidence_echo.sources import base, linkedin, rss
from evidence_echo.stages import fetch

# --- feeds are served by a fake HTTP transport, so no test touches the network ---

_RSS = ("<?xml version='1.0'?><rss version='2.0'><channel><title>T</title>"
        "<item><title>A</title><link>https://e.com/a</link>"
        "<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>"
        "<item><title>B</title><link>https://e.com/b</link>"
        "<pubDate>Tue, 05 Mar 2024 00:00:00 GMT</pubDate></item></channel></rss>")
_EMPTY_RSS = "<?xml version='1.0'?><rss version='2.0'><channel><title>T</title></channel></rss>"
SRC = {"type": "rss", "name": "Blog", "url": "https://e.com/feed"}


def _feed_ctx(body: str = "", status: int = 200, exc=None, days=3650, handler=None, seen=None):
    def serve(request):
        if seen is not None:
            seen.append(str(request.url))
        if handler:
            return handler(request)
        if exc:
            raise exc
        return httpx.Response(status, content=body.encode("utf-8"))
    return base.FetchContext(cutoff=datetime.now(timezone.utc) - timedelta(days=days),
                             client=httpx.Client(transport=httpx.MockTransport(serve)),
                             limiter=base.RateLimiter(interval=0))


def test_check_rss_ok_reports_count_and_latest_date(monkeypatch):
    assert rss.check(SRC, _feed_ctx(_RSS)) == {
        "name": "Blog", "type": "rss", "url": "https://e.com/feed", "status": "ok",
        "http_status": 200, "entries": 2,
        "latest_entry_date": "2024-03-05T00:00:00+00:00", "detail": None}


def test_check_rss_tolerates_bom_and_leading_whitespace(monkeypatch):
    out = rss.check(SRC, _feed_ctx("﻿\n\n  " + _RSS))
    assert (out["status"], out["entries"]) == ("ok", 2)


def test_check_rss_http_error(monkeypatch):
    out = rss.check(SRC, _feed_ctx("<html>Not found</html>", status=404))
    assert (out["status"], out["http_status"], out["entries"]) == ("http_error", 404, 0)


def test_check_rss_html_page_is_not_feed(monkeypatch):
    out = rss.check(SRC, _feed_ctx("<html><body><p>a blog <b>page</p></body></html>"))
    assert out["status"] == "not_feed"
    assert out["detail"]


def test_check_rss_valid_feed_without_entries_is_empty(monkeypatch):
    out = rss.check(SRC, _feed_ctx(_EMPTY_RSS))
    assert (out["status"], out["entries"], out["latest_entry_date"]) == ("empty", 0, None)


def test_check_rss_network_failure_is_error(monkeypatch):
    out = rss.check(SRC, _feed_ctx(exc=httpx.ConnectError("connection refused")))
    assert (out["status"], out["http_status"]) == ("error", None)
    assert "connection refused" in out["detail"]


def test_doctor_skips_linkedin_without_calling_network(monkeypatch):
    monkeypatch.setattr(linkedin.httpx, "post", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(linkedin.httpx, "get", lambda *a, **k: pytest.fail("network"))
    li = {"type": "linkedin", "name": "Jane", "slug": "jane"}
    out = fetch.doctor([SRC, li], _feed_ctx(_RSS))
    assert [r["status"] for r in out] == ["ok", "skipped"]
    assert out[1]["url"] == "https://www.linkedin.com/in/jane/"


def test_list_new_returns_dated_and_undated_entries(monkeypatch):
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    feed = (f"<?xml version='1.0'?><rss version='2.0'><channel><title>T</title>"
            f"<item><title>A</title><link>https://e.com/a</link><pubDate>{fresh}</pubDate></item>"
            f"<item><title>B</title><link>https://e.com/b</link></item></channel></rss>")
    res = rss.list_new([SRC], _feed_ctx(feed, days=7))["Blog"]
    assert res.error is None
    assert [(e.url, e.published is not None) for e in res.entries] == [
        ("https://e.com/a", True), ("https://e.com/b", False)]


@pytest.mark.parametrize("status", [404, 410, 500])
def test_list_new_http_error_with_xml_body_is_error(monkeypatch, status):
    res = rss.list_new([SRC], _feed_ctx(_EMPTY_RSS, status=status))["Blog"]
    assert res.error == f"HTTP {status}" and res.entries == [] and res.blocked is False


# --- F1 (security scan 2026-10-22): feeds go through the guarded http_get ---

def _redirect_to(target):
    def handler(request):
        if request.url.host == "e.com":
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, content=_RSS.encode())
    return handler


@pytest.mark.parametrize("target", ["http://127.0.0.1:8080/admin", "http://192.168.1.1/feed",
                                    "http://169.254.169.254/latest/meta-data/"])
def test_list_new_refuses_redirect_to_private_host(target):
    seen = []
    res = rss.list_new([SRC], _feed_ctx(handler=_redirect_to(target), seen=seen))["Blog"]
    assert res.entries == [] and "refused" in res.error
    assert seen == ["https://e.com/feed"]


def test_check_refuses_redirect_to_private_host():
    seen = []
    out = rss.check(SRC, _feed_ctx(handler=_redirect_to("http://127.0.0.1/"), seen=seen))
    assert out["status"] == "error" and "refused" in out["detail"]
    assert seen == ["https://e.com/feed"]


def test_list_new_refuses_feed_on_private_host():
    seen = []
    src = {**SRC, "url": "http://10.0.0.7/feed"}
    res = rss.list_new([src], _feed_ctx(_RSS, seen=seen))["Blog"]
    assert "refused" in res.error and seen == []


def test_list_new_follows_public_redirect():
    def handler(request):
        if request.url.path == "/feed":
            return httpx.Response(301, headers={"Location": "https://e.com/new-feed"})
        return httpx.Response(200, content=_RSS.encode())
    res = rss.list_new([SRC], _feed_ctx(handler=handler))["Blog"]
    assert res.error is None and len(res.entries) == 2


@pytest.mark.parametrize("status", [401, 403, 451])
def test_list_new_refused_status_is_blocked(status):
    res = rss.list_new([SRC], _feed_ctx("<html>no</html>", status=status))["Blog"]
    assert res.blocked is True and res.error == f"HTTP {status}"


def test_feed_over_the_size_cap_is_an_error(monkeypatch):
    monkeypatch.setattr(base, "MAX_BYTES", 100)
    res = rss.list_new([SRC], _feed_ctx(_RSS))["Blog"]
    assert res.entries == [] and "larger than" in res.error


def test_feed_encoding_comes_from_the_xml_declaration():
    feed = ("<?xml version='1.0' encoding='windows-1252'?><rss version='2.0'><channel>"
            "<title>T</title><item><title>Caf\xe9</title><link>https://e.com/c</link></item>"
            "</channel></rss>").encode("latin-1")
    ctx = _feed_ctx(handler=lambda r: httpx.Response(200, content=feed))
    res = rss.list_new([SRC], ctx)["Blog"]
    assert [e.title for e in res.entries] == ["Caf\xe9"]


# --- the body is data: feedparser must never treat it as a URL or a file path to open ---

def test_feed_body_naming_a_local_file_is_not_opened(tmp_path):
    secret = tmp_path / "local.xml"
    secret.write_text(_RSS, encoding="utf-8")
    res = rss.list_new([SRC], _feed_ctx(str(secret)))["Blog"]
    assert res.entries == []


def test_feed_body_naming_a_url_is_not_fetched(monkeypatch):
    opened = []
    monkeypatch.setattr("urllib.request.OpenerDirector.open",
                        lambda self, *a, **k: opened.append(a) or pytest.fail("opened a URL"))
    out = rss.check(SRC, _feed_ctx("http://127.0.0.1:8080/admin"))
    assert out["status"] == "not_feed" and opened == []
