from datetime import datetime, timedelta, timezone

from evidence_echo.sources import base, linkedin

APIFY_DUMMY = "apify_api_SECRET123"  # gitleaks:allow (dummy token)
LI_SOURCES = [{"type": "linkedin", "name": "Jane", "slug": "jane"}]


def _ctx():
    return base.FetchContext(cutoff=datetime.now(timezone.utc) - timedelta(days=7), client=None,
                             limiter=base.RateLimiter(), apify_token=APIFY_DUMMY, apify_actor="actor~x")


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_normalize_record():
    record = {
        "content": "Study of 64 sites shows 1B clicks distribution shift",
        "linkedinUrl": "https://www.linkedin.com/posts/abc",
        "postedAt": {"date": datetime.now(timezone.utc).isoformat()},
        "author": {"publicIdentifier": "janedoe", "name": "Jane Doe"},
    }
    name, entry = linkedin.normalize_record(record, {"janedoe": "Jane Doe"})
    assert name == "Jane Doe"
    assert entry.url == "https://www.linkedin.com/posts/abc"
    assert entry.text and entry.published


def test_apify_token_sent_in_header_not_url(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return _Resp({"data": {"id": "run1"}})

    def fake_get(url, **kw):
        calls.append((url, kw))
        if url.endswith("/dataset/items"):
            return _Resp([])
        return _Resp({"data": {"status": "SUCCEEDED"}})

    monkeypatch.setattr(linkedin.httpx, "post", fake_post)
    monkeypatch.setattr(linkedin.httpx, "get", fake_get)
    linkedin.list_new(LI_SOURCES, _ctx())

    assert len(calls) == 3
    for url, kw in calls:
        assert APIFY_DUMMY not in url
        assert APIFY_DUMMY not in repr(kw.get("params"))
        assert kw.get("headers", {}).get("Authorization") == f"Bearer {APIFY_DUMMY}"


def test_apify_error_message_redacts_token(monkeypatch):
    def failing_post(url, **kw):
        raise RuntimeError(f"Client error for url {url}?token={APIFY_DUMMY}")

    monkeypatch.setattr(linkedin.httpx, "post", failing_post)
    res = linkedin.list_new(LI_SOURCES, _ctx())
    assert APIFY_DUMMY not in res["Jane"].error and "***" in res["Jane"].error


def test_missing_token_is_an_error_per_source(monkeypatch):
    """Without a token nothing is fetched, and each LinkedIn source says why (not a silent 0)."""
    monkeypatch.setattr(linkedin.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    ctx = _ctx()
    ctx.apify_token = ""
    out = linkedin.list_new(LI_SOURCES, ctx)
    assert out["Jane"].entries == []
    assert out["Jane"].error == linkedin.NO_TOKEN
    assert "Apify token" in linkedin.NO_TOKEN and len(linkedin.NO_TOKEN) <= 40


def _fake_apify(monkeypatch, dataset):
    monkeypatch.setattr(linkedin.httpx, "post", lambda url, **kw: _Resp({"data": {"id": "run1"}}))
    monkeypatch.setattr(linkedin.httpx, "get", lambda url, **kw: _Resp(
        dataset if url.endswith("/dataset/items") else {"data": {"status": "SUCCEEDED"}}))


def _record(slug, author_slug=None):
    return {"content": "post", "linkedinUrl": f"https://www.linkedin.com/posts/{slug}-1",
            "postedAt": {"date": datetime.now(timezone.utc).isoformat()},
            "author": {"publicIdentifier": author_slug or slug, "name": author_slug or slug},
            "query": {"targetUrl": f"https://www.linkedin.com/in/{slug}/"}}


def test_profile_with_no_posts_is_a_warning(monkeypatch):
    """A successful run with nothing for a profile (often a wrong slug) is not a silent 0."""
    sources = LI_SOURCES + [{"type": "linkedin", "name": "Gone", "slug": "gone-123"}]
    _fake_apify(monkeypatch, [_record("jane")])
    out = linkedin.list_new(sources, _ctx())
    assert len(out["Jane"].entries) == 1 and out["Jane"].error is None
    assert out["Gone"].entries == [] and out["Gone"].error == linkedin.NO_POSTS
    assert len(linkedin.NO_POSTS) <= 40


def test_profile_with_only_reposts_is_not_a_warning(monkeypatch):
    """The profile answered; its reposts are dropped as other authors' posts, without a warning."""
    _fake_apify(monkeypatch, [_record("jane", author_slug="someone-else")])
    out = linkedin.list_new(LI_SOURCES, _ctx())
    assert out["Jane"].entries == [] and out["Jane"].error is None
