from datetime import datetime, timezone

import httpx
import pytest

from evidence_echo.sources import base, rss

FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>a</title><link>https://s.example/a</link>
<pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
PAGE = """<html><head>
<link rel="alternate" type="application/rss+xml" href="/blog/feed/">
</head><body>hi</body></html>"""


class Web(dict):
    """url -> body (str) or a ready httpx.Response; unknown s.example URLs are 404,
    other hosts are down. `seen` lists every URL requested, in order."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def serve(self, request):
        url = str(request.url)
        self.seen.append(url)
        body = self.get(url)
        if isinstance(body, httpx.Response):
            return body
        if body is not None:
            return httpx.Response(200, content=body.encode("utf-8"))
        if request.url.host == "s.example":
            return httpx.Response(404)
        raise httpx.ConnectError("down")

    def ctx(self):
        return base.FetchContext(cutoff=datetime.now(timezone.utc),
                                 client=httpx.Client(transport=httpx.MockTransport(self.serve)),
                                 limiter=base.RateLimiter(interval=0))


@pytest.fixture
def web(monkeypatch):
    pages = Web()
    real = rss.discover_feed
    monkeypatch.setattr(rss, "discover_feed", lambda url, ctx=None: real(url, ctx or pages.ctx()))
    return pages


def test_feed_url_is_used_as_is(web):
    web["https://s.example/feed"] = FEED
    out = rss.discover_feed("https://s.example/feed")
    assert out["url"] == "https://s.example/feed"
    assert out["discovered_from"] is None
    assert out["check"]["status"] == "ok"


def test_page_with_alternate_link_resolves_relative_href(web):
    web["https://s.example/"] = PAGE
    web["https://s.example/blog/feed/"] = FEED
    out = rss.discover_feed("https://s.example/")
    assert out["url"] == "https://s.example/blog/feed/"
    assert out["discovered_from"] == "https://s.example/"
    assert out["check"]["status"] == "ok"


def test_atom_alternate_is_found(web):
    web["https://s.example/"] = PAGE.replace("application/rss+xml", "application/atom+xml")
    web["https://s.example/blog/feed/"] = FEED
    assert rss.discover_feed("https://s.example/")["url"] == "https://s.example/blog/feed/"


def test_page_without_feed_link_keeps_url_with_failing_check(web):
    web["https://s.example/"] = "<html><body>no feed</body></html>"
    out = rss.discover_feed("https://s.example/")
    assert out["url"] == "https://s.example/" and out["discovered_from"] is None
    assert out["check"]["status"] == "not_feed"


def test_network_error_keeps_url(web):
    out = rss.discover_feed("https://down.example/")
    assert out["url"] == "https://down.example/" and out["discovered_from"] is None
    assert out["check"]["status"] in ("http_error", "error", "not_feed")


@pytest.mark.parametrize("href", ["file:///etc/passwd", "file://server/share/feed.xml", "ftp://x/f"])
def test_advertised_non_http_feed_is_never_parsed(web, href):
    web["https://s.example/"] = PAGE.replace("/blog/feed/", href)
    out = rss.discover_feed("https://s.example/")
    assert out["url"] == "https://s.example/" and out["discovered_from"] is None
    assert all(u.startswith("https://") for u in web.seen)


# --- F3 (security scan 2026-10-22): discovery uses the guarded http_get ---

@pytest.mark.parametrize("target", ["http://127.0.0.1:8080/", "http://192.168.0.1/"])
def test_discovery_does_not_follow_redirect_to_private_host(web, target):
    web["https://s.example/"] = httpx.Response(302, headers={"Location": target})
    out = rss.discover_feed("https://s.example/")
    assert out["url"] == "https://s.example/" and out["discovered_from"] is None
    assert all(u.startswith("https://s.example/") for u in web.seen)


@pytest.mark.parametrize("href", ["http://192.168.0.1/cgi-bin/feed", "http://127.0.0.1/feed",
                                  "http://lan.example/feed"])
def test_advertised_feed_on_private_host_is_not_used(web, monkeypatch, href):
    real = base.resolve_host
    monkeypatch.setattr(base, "resolve_host",
                        lambda host: ["10.0.0.5"] if host == "lan.example" else real(host))
    web["https://s.example/"] = PAGE.replace("/blog/feed/", href)
    out = rss.discover_feed("https://s.example/")
    assert out["url"] == "https://s.example/" and out["discovered_from"] is None
    assert all(u.startswith("https://s.example/") for u in web.seen)


def test_discovery_page_over_the_size_cap_finds_nothing(web, monkeypatch):
    monkeypatch.setattr(base, "MAX_BYTES", 50)
    web["https://s.example/"] = PAGE
    web["https://s.example/blog/feed/"] = FEED
    out = rss.discover_feed("https://s.example/")
    assert out["url"] == "https://s.example/" and out["discovered_from"] is None
