from pathlib import Path

from evidence_echo.core import store
from evidence_echo.sources import base
from evidence_echo.stages import extract
from tests.conftest import make_item

FIXTURES = Path(__file__).parent / "fixtures"
HTML = (FIXTURES / "article.html").read_text(encoding="utf-8")


def test_extract_text_from_html():
    text = extract.text_from_html(HTML, url="https://example.com/post")
    assert "12,000 product pages" in text
    assert "4.2 percent" in text


class FakeKind:
    def __init__(self, text=None, status="ok"):
        self.text, self.status = text, status

    def full_text(self, url, ctx):
        if self.status != "ok":
            return base.FullText("HTTP 500", self.status)
        return base.FullText(extract.text_from_html(self.text, url), "ok")


def test_run_extracts_triaged_items(db, monkeypatch):
    store.insert_items(db, [make_item()])
    item = store.items_in_state(db, "fetched")[0]
    store.set_triage(db, item["id"], passed=True, reason="ok")
    monkeypatch.setattr(extract, "get_kind", lambda t: FakeKind(HTML))
    result = extract.run(db, base.FetchContext(cutoff=None, client=None, limiter=base.RateLimiter()))
    assert result["extracted"] == 1
    row = store.items_in_state(db, "extracted")[0]
    assert "split test" in row["full_text"]


def test_run_download_failure_falls_back_to_excerpt(db, monkeypatch):
    store.insert_items(db, [make_item()])
    item = store.items_in_state(db, "fetched")[0]
    store.set_triage(db, item["id"], passed=True, reason="ok")
    monkeypatch.setattr(extract, "get_kind", lambda t: FakeKind(status="error"))
    out = extract.run(db, base.FetchContext(cutoff=None, client=None, limiter=base.RateLimiter()))
    assert out["degraded"] == 1
    row = store.items_in_state(db, "extracted")[0]
    assert row["full_text"] == row["excerpt"]  # degrade gracefully, keep moving


def test_linkedin_item_with_feed_text_needs_no_call(db, monkeypatch):
    store.insert_item(db, dict(make_item(url="https://www.linkedin.com/posts/x", source_type="linkedin"),
                               state="triaged", full_text="post text", text_source="feed"))
    monkeypatch.setattr(extract, "get_kind", lambda t: (_ for _ in ()).throw(AssertionError("no call")))
    assert extract.run(db)["extracted"] == 1


def test_kind_exception_degrades_item_and_extract_continues(db, monkeypatch):
    store.insert_items(db, [make_item(), make_item(url="https://example.com/post-2")])
    for it in store.items_in_state(db, "fetched"):
        store.set_triage(db, it["id"], passed=True, reason="ok")

    class Boom:
        def full_text(self, url, ctx):
            if url.endswith("post-1"):
                raise ValueError("malformed host")
            return base.FullText(extract.text_from_html(HTML, url), "ok")
    monkeypatch.setattr(extract, "get_kind", lambda t: Boom())
    out = extract.run(db, base.FetchContext(cutoff=None, client=None, limiter=base.RateLimiter()))
    assert out == {"extracted": 2, "degraded": 1, "needs_session": 0}


def test_malformed_host_through_real_kind_degrades(db):
    import httpx
    store.insert_items(db, [make_item(url="https://xn--/post-1")])
    item = store.items_in_state(db, "fetched")[0]
    store.set_triage(db, item["id"], passed=True, reason="ok")
    ctx = base.FetchContext(cutoff=None, limiter=base.RateLimiter(interval=0),
                            client=httpx.Client(transport=httpx.MockTransport(
                                lambda r: httpx.Response(200, text=HTML))))
    assert extract.run(db, ctx)["degraded"] == 1
