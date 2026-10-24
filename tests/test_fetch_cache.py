import os

import httpx
import pytest

from evidence_echo.sources import base, cache as cache_mod
from evidence_echo.sources.cache import FetchCache

URL = "https://example.com/feed"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def _fetched(url=URL):
    return base.Fetched(url, 200, "<rss>é</rss>", "<rss>é</rss>".encode("utf-8"))


def test_put_then_get_within_window(tmp_path):
    clock = Clock()
    c = FetchCache(tmp_path, 60, clock=clock)
    c.put(URL, base.Fetched("https://example.com/final", 200, "text é", b"raw\xff"))
    clock.now += 3599
    got = c.get(URL)
    assert (got.url, got.status, got.text, got.content) == ("https://example.com/final", 200, "text é", b"raw\xff")


def test_expired_entry_is_a_miss_and_prune_deletes_it(tmp_path):
    clock = Clock()
    c = FetchCache(tmp_path, 60, clock=clock)
    c.put(URL, _fetched())
    c.put_json("k", {"a": 1})
    clock.now += 3601
    assert c.get(URL) is None and c.get_json("k") is None
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert c.prune() == 2 and list(tmp_path.glob("*.json")) == []


def test_read_false_skips_reading_but_still_writes(tmp_path):
    clock = Clock()
    FetchCache(tmp_path, 60, clock=clock).put(URL, _fetched())
    fresh = FetchCache(tmp_path, 60, read=False, clock=clock)
    assert fresh.get(URL) is None
    fresh.put(URL, base.Fetched(URL, 200, "newer", b"newer"))
    assert FetchCache(tmp_path, 60, clock=clock).get(URL).text == "newer"


def test_json_roundtrip_and_corrupt_file_is_a_miss(tmp_path):
    c = FetchCache(tmp_path, 60)
    c.put_json("linkedin:a:week:jane", [{"x": 1}])
    assert c.get_json("linkedin:a:week:jane") == [{"x": 1}]
    for f in tmp_path.glob("*.json"):
        f.write_text("{not json", encoding="utf-8")
    assert c.get_json("linkedin:a:week:jane") is None and c.get(URL) is None


def _ctx(handler, cache, limiter=None):
    return base.FetchContext(cutoff=None, client=httpx.Client(transport=httpx.MockTransport(handler)),
                             limiter=limiter or base.RateLimiter(interval=0), cache=cache)


def test_http_get_cache_hit_makes_no_request_and_skips_limiter(tmp_path):
    c = FetchCache(tmp_path, 60)
    c.put(URL, _fetched())

    def handler(request):
        raise AssertionError("network used")

    class Limiter:
        calls = []

        def wait(self, host):
            self.calls.append(host)
            return True
    limiter = Limiter()
    got = base.http_get(URL, _ctx(handler, c, limiter))
    assert got.text == "<rss>é</rss>" and limiter.calls == []


@pytest.mark.parametrize("status,exc", [(403, base.Blocked), (500, base.FetchError)])
def test_http_get_stores_success_only(tmp_path, status, exc):
    c = FetchCache(tmp_path, 60)
    with pytest.raises(exc):
        base.http_get(URL, _ctx(lambda r: httpx.Response(status), c))
    assert list(tmp_path.glob("*.json")) == []


def test_http_get_does_not_store_a_challenge_page(tmp_path):
    c = FetchCache(tmp_path, 60)
    challenge = "<html><title>Just a moment...</title>checking your browser</html>"
    with pytest.raises(base.Blocked):
        base.http_get(URL, _ctx(lambda r: httpx.Response(200, text=challenge), c))
    assert list(tmp_path.glob("*.json")) == []


def test_http_get_stores_under_requested_url_not_redirect_target(tmp_path):
    c = FetchCache(tmp_path, 60)

    def handler(request):
        if request.url.path == "/feed":
            return httpx.Response(301, headers={"location": "https://example.com/real"})
        return httpx.Response(200, text="body")
    got = base.http_get(URL, _ctx(handler, c))
    assert got.url == "https://example.com/real"
    assert c.get(URL).text == "body" and c.get("https://example.com/real") is None


def test_cache_write_error_is_ignored(tmp_path, monkeypatch):
    c = FetchCache(tmp_path / "c", 60)

    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(os, "replace", boom)
    got = base.http_get(URL, _ctx(lambda r: httpx.Response(200, text="ok"), c))
    assert got.text == "ok"


def test_for_config_off_and_fresh(tmp_path):
    class Cfg:
        data_dir = tmp_path
        fetch_cache_minutes = 0
    assert cache_mod.for_config(Cfg) is None
    Cfg.fetch_cache_minutes = 30
    on = cache_mod.for_config(Cfg)
    assert on.minutes == 30 and on.read and on.root == tmp_path / "cache" / "fetch"
    assert cache_mod.for_config(Cfg, fresh=True).read is False
