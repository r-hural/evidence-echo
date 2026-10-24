"""Task 3: the shared fetch cache used by ev fetch / extract / primary, ev fetch --fresh, LinkedIn."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, settings, store, topics
from evidence_echo.sources import base, cache as cache_mod, linkedin
from tests.conftest import make_workspace

FEED_URL = "https://feeds.example.com/feed"
RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>New study of title tags</title><link>https://feeds.example.com/p1</link>
<pubDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>
<description>We tested 100 sites.</description></item></channel></rss>"""


def _two_topics(tmp_path, monkeypatch, **global_values):
    data = tmp_path / "data"
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    make_workspace(data, ("a", "b"), **global_values)
    for tid in ("a", "b"):
        t = topics.load_topic(data / "topics", tid)
        topics.save_topic(data / "topics", replace(
            t, sources=[{"type": "rss", "name": "Shared", "url": FEED_URL}]))
    return data


@pytest.fixture
def requests_made(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=RSS, headers={"content-type": "application/rss+xml"})
    monkeypatch.setattr(base, "make_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(base.RateLimiter, "wait", lambda self, host: True)
    return seen


def _items(tid):
    with store.connect(config.load(tid).db_path) as conn:
        return [i["url"] for i in store.items_in_state(conn, "fetched")]


def test_two_topics_share_one_download(tmp_path, monkeypatch, requests_made):
    _two_topics(tmp_path, monkeypatch)
    api.fetch(config.load("a"))
    api.fetch(config.load("b"))
    assert requests_made == [FEED_URL]
    assert _items("a") == _items("b") == ["https://feeds.example.com/p1"]


def test_fetch_fresh_downloads_again(tmp_path, monkeypatch, requests_made):
    _two_topics(tmp_path, monkeypatch)
    api.fetch(config.load("a"))
    api.fetch(config.load("b"), fresh=True)
    assert requests_made == [FEED_URL, FEED_URL]


def test_cache_off_when_minutes_zero(tmp_path, monkeypatch, requests_made):
    data = _two_topics(tmp_path, monkeypatch, fetch_cache_minutes=0)
    api.fetch(config.load("a"))
    api.fetch(config.load("b"))
    assert requests_made == [FEED_URL, FEED_URL]
    assert not (data / "cache" / "fetch").exists()


def test_turning_the_cache_off_deletes_saved_copies(tmp_path, monkeypatch, requests_made):
    """Saved copies (LinkedIn posts among them) do not outlive fetch_cache_minutes = 0."""
    data = _two_topics(tmp_path, monkeypatch)
    api.fetch(config.load("a"))
    linkedin_copy = cache_mod.for_config(config.load("a"))
    linkedin_copy.put_json("linkedin:actor:week:someone", {"entries": []})
    assert len(list((data / "cache" / "fetch").glob("*.json"))) == 2
    monkeypatch.setenv("EV_ECHO_FETCH_CACHE_MINUTES", "0")
    api.fetch(config.load("b"))
    assert list((data / "cache" / "fetch").glob("*.json")) == []


def test_cli_fetch_fresh_flag(monkeypatch):
    assert cli._parser().parse_args(["fetch", "--fresh"]).fresh is True
    assert cli._parser().parse_args(["fetch"]).fresh is False
    assert cli._parser().parse_args(["fetch", "--all"]).fresh is False


def test_cli_fetch_passes_fresh_to_api(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "data"))
    make_workspace(tmp_path / "data")
    got = {}
    monkeypatch.setattr(api, "fetch", lambda cfg, fetch_all=False, fresh=False: got.update(
        all=fetch_all, fresh=fresh) or {})
    assert cli.main(["fetch", "--fresh"]) == 0
    assert got == {"all": False, "fresh": True}


# --- LinkedIn: only profiles without a cached answer go to Apify -----------------------------

ACTOR = "actor~x"


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _record(slug, url):
    return {"content": f"Study by {slug} of 64 sites", "linkedinUrl": url,
            "postedAt": {"date": datetime.now(timezone.utc).isoformat()},
            "author": {"publicIdentifier": slug, "name": slug}, "query": {"targetUrl": f"https://www.linkedin.com/in/{slug}/"}}


def _apify(monkeypatch, records):
    sent = []

    def post(url, **kw):
        sent.append(kw["json"])
        return _Resp({"data": {"id": "r"}})

    def get(url, **kw):
        if url.endswith("/dataset/items"):
            wanted = {u.split("/in/")[1].strip("/") for u in sent[-1]["targetUrls"]}
            return _Resp([r for r in records if r["author"]["publicIdentifier"] in wanted])
        return _Resp({"data": {"status": "SUCCEEDED"}})
    monkeypatch.setattr(linkedin.httpx, "post", post)
    monkeypatch.setattr(linkedin.httpx, "get", get)
    return sent


def _ctx(tmp_path, fresh=False):
    return base.FetchContext(cutoff=datetime.now(timezone.utc) - timedelta(days=7), client=None,
                             limiter=base.RateLimiter(), apify_token="tok", apify_actor=ACTOR,
                             cache=cache_mod.FetchCache(tmp_path, 60, read=not fresh))


SOURCES = [{"type": "linkedin", "name": "P1", "slug": "p1"}, {"type": "linkedin", "name": "P2", "slug": "p2"}]


def test_linkedin_only_uncached_profiles_sent(tmp_path, monkeypatch):
    records = [_record("p1", "https://www.linkedin.com/posts/1"), _record("p2", "https://www.linkedin.com/posts/2")]
    sent = _apify(monkeypatch, records)
    c = _ctx(tmp_path)
    first = linkedin.list_new(SOURCES[:1], c)             # p1 now cached
    assert [e.url for e in first["P1"].entries] == ["https://www.linkedin.com/posts/1"]
    sent.clear()
    out = linkedin.list_new(SOURCES, c)
    assert len(sent) == 1 and sent[0]["targetUrls"] == ["https://www.linkedin.com/in/p2/"]
    assert [e.url for e in out["P1"].entries] == ["https://www.linkedin.com/posts/1"]
    assert [e.url for e in out["P2"].entries] == ["https://www.linkedin.com/posts/2"]
    assert out["P1"].entries[0].published is not None
    sent.clear()
    again = linkedin.list_new(SOURCES, c)                 # all cached: no Apify call
    assert sent == [] and [e.url for e in again["P2"].entries] == ["https://www.linkedin.com/posts/2"]


def test_linkedin_error_and_unknown_profile_cache_nothing(tmp_path, monkeypatch):
    sent = _apify(monkeypatch, [])                        # Apify answers with no records at all
    c = _ctx(tmp_path)
    out = linkedin.list_new(SOURCES[:1], c)
    assert out["P1"].error == linkedin.NO_POSTS
    sent.clear()
    linkedin.list_new(SOURCES[:1], c)
    assert len(sent) == 1                                 # asked again: nothing was cached

    def failing(url, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(linkedin.httpx, "post", failing)
    assert linkedin.list_new(SOURCES[1:], c)["P2"].error == "boom"
    assert list(tmp_path.glob("*.json")) == []


def test_linkedin_backfill_and_week_modes_do_not_share(tmp_path, monkeypatch):
    sent = _apify(monkeypatch, [_record("p1", "https://www.linkedin.com/posts/1")])
    c = _ctx(tmp_path)
    linkedin.list_new(SOURCES[:1], c)
    c.backfill = base.Backfill(days=30, max_items=10, per_source={"P1": 10})
    sent.clear()
    linkedin.list_new(SOURCES[:1], c)
    assert len(sent) == 1 and sent[0]["maxPosts"] == 10
