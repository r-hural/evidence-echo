from datetime import datetime, timezone

import httpx
import pytest

from evidence_echo import sources
from evidence_echo.core.settings import ConfigError
from evidence_echo.sources import base

ARTICLE = "<html><body><article><h1>Test</h1>" + "<p>We tested 40 pages. " * 60 + "</p></article></body></html>"
CHALLENGE = "<html><title>Just a moment...</title><body>Enable JavaScript and cookies to continue</body></html>"


class Clock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def ctx_for(handler, clock=None):
    clock = clock or Clock()
    return base.FetchContext(
        cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        limiter=base.RateLimiter(clock=clock, sleep=clock.sleep))


def test_unknown_kind():
    with pytest.raises(ConfigError, match="source type must be one of"):
        sources.get_kind("youtube")


def test_rate_limiter_spaces_requests_per_host():
    c = Clock()
    rl = base.RateLimiter(clock=c, sleep=c.sleep)
    assert rl.wait("a") and rl.wait("a") and rl.wait("b")
    assert c.slept == [1.0]


def test_rate_limiter_wait_cap():
    c = Clock()
    rl = base.RateLimiter(clock=c, sleep=c.sleep)
    rl.penalize("a", 120)
    assert rl.wait("a") is False and c.slept == []


def test_http_get_ok_and_user_agent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, text=ARTICLE)
    got = base.http_get("https://e.com/a", ctx_for(handler))
    assert got.status == 200 and "40 pages" in got.text
    assert seen["ua"].startswith("EvidenceEcho/")


@pytest.mark.parametrize("response", [httpx.Response(403), httpx.Response(200, text=CHALLENGE)])
def test_http_get_blocked(response):
    with pytest.raises(base.Blocked):
        base.http_get("https://e.com/a", ctx_for(lambda r: response))


def test_http_get_429_retries_after_short_wait():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "2"}) if len(calls) == 1 else \
            httpx.Response(200, text=ARTICLE)
    assert base.http_get("https://e.com/a", ctx_for(handler)).status == 200
    assert len(calls) == 2


def test_http_get_429_long_wait_is_blocked():
    with pytest.raises(base.Blocked):
        base.http_get("https://e.com/a", ctx_for(
            lambda r: httpx.Response(429, headers={"Retry-After": "3600"})))


def test_http_get_size_cap():
    big = "x" * (base.MAX_BYTES + 10)
    with pytest.raises(base.FetchError, match="5 MB"):
        base.http_get("https://e.com/a", ctx_for(lambda r: httpx.Response(200, text=big)))


def test_http_get_404_is_error():
    with pytest.raises(base.FetchError, match="HTTP 404"):
        base.http_get("https://e.com/a", ctx_for(lambda r: httpx.Response(404)))


@pytest.mark.parametrize("text,junk", [
    ("short", True),
    ("Access denied. " + "x" * 600, True),
    ("We measured 12% more clicks. " * 120 + " access denied appears in a quote", False),
    ("Real article text with numbers. " * 30, False),
])
def test_looks_like_junk(text, junk):
    assert base.looks_like_junk(text) is junk


def test_article_text_statuses():
    assert base.article_text("https://e.com/a", ctx_for(lambda r: httpx.Response(200, text=ARTICLE))).status == "ok"
    assert base.article_text("https://e.com/a", ctx_for(lambda r: httpx.Response(403))).status == "blocked"
    assert base.article_text("https://e.com/a", ctx_for(
        lambda r: httpx.Response(200, text="<html><body><p>Hi</p></body></html>"))).status == "short"
    assert base.article_text("https://e.com/a", ctx_for(lambda r: httpx.Response(500))).status == "error"


def test_find_date():
    html = '<html><head><meta property="article:published_time" content="2026-09-30T10:00:00Z"></head><body>x</body></html>'
    assert base.find_date(html, "https://e.com/a").date().isoformat() == "2026-09-30"
    assert base.find_date("<html><body>no date</body></html>", "https://e.com/a") is None


def test_field_checkers():
    assert base.http_url("https://a/") is None
    assert base.http_url("file:///x") == "needs an http(s) url"
    assert base.http_url(None) == "needs 'url'"
    assert base.required_text("slug")("") == "needs 'slug'"
    assert base.COMMON_FIELDS["scan_depth"]("deep") == "scan_depth must be headlines or full"
    assert base.COMMON_FIELDS["scan_depth"](None) is None


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://localhost:8080/", "http://169.254.169.254/latest",
                                 "http://10.0.0.5/a", "http://[::1]/a", "http://printer.local/a"])
def test_http_get_refuses_non_public_hosts(url):
    with pytest.raises(base.FetchError, match="refused"):
        base.http_get(url, ctx_for(lambda r: httpx.Response(200, text=ARTICLE)))


def test_http_get_refuses_redirect_to_private_host():
    def handler(request):
        if request.url.host == "e.com":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/meta"})
        return httpx.Response(200, text=ARTICLE)
    with pytest.raises(base.FetchError, match="refused"):
        base.http_get("https://e.com/a", ctx_for(handler))


def test_http_get_follows_public_redirect():
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"Location": "/b"})
        return httpx.Response(200, text=ARTICLE)
    assert base.http_get("https://e.com/a", ctx_for(handler)).url.endswith("/b")


@pytest.mark.parametrize("url", [r"http://127.0.0.1\@e.com/x", r"http://e.com\@127.0.0.1/x",
                                 "http://[::ffff:127.0.0.1]/x", "http://0x7f.0.0.1/x"])
def test_http_get_parser_differential_urls_refused_or_public(url):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(200, text=ARTICLE)
    try:
        base.http_get(url, ctx_for(handler))
    except (base.FetchError, base.Blocked):
        pass
    assert not any(h in ("127.0.0.1", "localhost") for h in seen)


@pytest.mark.parametrize("url", ["https://xn--/post-1", "https://xn--a.com/post-1"])
def test_malformed_host_is_fetch_error(url):
    ctx = ctx_for(lambda r: httpx.Response(200, text=ARTICLE))
    with pytest.raises(base.FetchError):
        base.http_get(url, ctx)
    assert base.article_text(url, ctx).status == "error"


def test_redirect_to_malformed_host_is_fetch_error():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://xn--/"})
    with pytest.raises(base.FetchError):
        base.http_get("https://e.com/a", ctx_for(handler))


@pytest.mark.parametrize("url", [
    "http://localhost./", "http://foo.localhost./", "http://10.0.0.1./", "http://LOCALHOST/",
    "http://127.1/", "http://2130706433/", "http://0x7f000001/", "http://0/", "http://0x7f.0.0.1/",
    "http://017700000001/", "http://224.0.0.1/", "http://240.0.0.1/", "http://0.0.0.0/",
    "http://[::]/", "http://[ff02::1]/"])
def test_http_get_refuses_disguised_local_hosts(url):
    with pytest.raises(base.FetchError, match="refused"):
        base.http_get(url, ctx_for(lambda r: httpx.Response(200, text=ARTICLE)))


def _resolver(monkeypatch, table):
    def resolve(host):
        if host not in table:
            raise base.FetchError(f"cannot resolve {host}")
        return table[host]
    monkeypatch.setattr(base, "resolve_host", resolve)


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["93.184.215.14", "10.1.2.3"],
                                       ["::ffff:127.0.0.1"], ["fe80::1"], ["169.254.169.254"]])
def test_http_get_refuses_names_resolving_to_private_addresses(monkeypatch, addresses):
    _resolver(monkeypatch, {"rebind.example.com": addresses})
    seen = []
    with pytest.raises(base.FetchError, match="refused"):
        base.http_get("https://rebind.example.com/a",
                      ctx_for(lambda r: seen.append(r) or httpx.Response(200, text=ARTICLE)))
    assert seen == []


def test_http_get_checks_dns_on_every_redirect_hop(monkeypatch):
    _resolver(monkeypatch, {"e.com": ["93.184.215.14"], "inside.e.com": ["192.168.1.10"]})
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://inside.e.com/"})
    with pytest.raises(base.FetchError, match="refused"):
        base.http_get("https://e.com/a", ctx_for(handler))
    assert hosts == ["e.com"]


def test_http_get_unresolvable_host_is_fetch_error(monkeypatch):
    _resolver(monkeypatch, {})
    with pytest.raises(base.FetchError, match="cannot resolve"):
        base.http_get("https://nowhere.example/a", ctx_for(lambda r: httpx.Response(200, text=ARTICLE)))


def test_http_get_public_name_allowed(monkeypatch):
    _resolver(monkeypatch, {"e.com": ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"]})
    assert base.http_get("https://e.com./a", ctx_for(lambda r: httpx.Response(200, text=ARTICLE))).status == 200


def test_resolve_host_uses_getaddrinfo(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 0, 0, 2))])
    assert base.real_resolve_host("e.com") == ["10.0.0.1", "fe80::1%eth0"]

    def fail(*a, **kw):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(base.FetchError, match="cannot resolve"):
        base.real_resolve_host("e.com")
