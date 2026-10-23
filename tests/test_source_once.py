from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from evidence_echo.core import store
from evidence_echo.sources import base, once
from evidence_echo.stages import fetch

DOC = "<html><head><title>State of SEO 2026</title></head><body><article>" + \
      "<p>Survey of 4,000 marketers: 61% saw traffic drop. " * 40 + "</p></article></body></html>"
SRC = {"type": "once", "name": "State of SEO 2026", "url": "https://r.com/state-of-seo-2026"}


def _cfg():
    return SimpleNamespace(topic=SimpleNamespace(sources=[SRC], linkedin_apify_actor="", scan_depth=None),
                           fetch_horizon_days=7, scan_depth="headlines", adaptive_fetch="off")


def _ctx(body, status=200):
    return base.FetchContext(
        cutoff=datetime.now(timezone.utc) - timedelta(days=7), limiter=base.RateLimiter(interval=0),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=body))))


def test_once_first_run_stores_text_and_done(db):
    out = fetch.run(db, _cfg(), ctx=_ctx(DOC))
    assert out["sources"]["State of SEO 2026"]["new"] == 1
    item = store.items_in_state(db, "fetched")[0]
    assert "61% saw traffic drop" in item["full_text"] and item["text_source"] == "page"
    assert item["content_hash"] and item["date_unknown"] == 1  # undated documents are kept
    assert store.get_source_state(db, SRC["name"])["done"] == 1


def test_once_done_is_not_fetched_again(db):
    fetch.run(db, _cfg(), ctx=_ctx(DOC))
    out = fetch.run(db, _cfg(), ctx=_ctx("<html>changed</html>", 500))
    assert out["sources"]["State of SEO 2026"]["found"] == 0


def test_once_reset_same_hash_unchanged_new_hash_new_version(db):
    fetch.run(db, _cfg(), ctx=_ctx(DOC))
    store.set_source_fields(db, SRC["name"], "once", done=0)
    out = fetch.run(db, _cfg(), ctx=_ctx(DOC))
    assert out["sources"]["State of SEO 2026"]["new"] == 0
    assert out["sources"]["State of SEO 2026"]["unchanged"] is True
    store.set_source_fields(db, SRC["name"], "once", done=0)
    fetch.run(db, _cfg(), ctx=_ctx(DOC.replace("61%", "64%")))
    urls = [r["url"] for r in db.execute("SELECT url FROM items ORDER BY id")]
    assert urls == [SRC["url"], SRC["url"] + "#v2"]


def test_once_blocked_flags_item_for_session(db):
    out = fetch.run(db, _cfg(), ctx=_ctx("", 403))
    item = store.flagged_items(db)[0]
    assert item["url"] == SRC["url"] and item["full_text"] is None
    assert store.get_source_state(db, SRC["name"])["done"] == 1
    assert out["sources"]["State of SEO 2026"]["blocked"] is True


def test_once_check():
    assert once.check(SRC, _ctx(DOC))["status"] == "ok"
    assert once.check(SRC, _ctx("<html><body>tiny</body></html>"))["status"] == "short"


def test_once_reset_while_still_blocked_rearms_session_fallback(db):
    fetch.run(db, _cfg(), ctx=_ctx("", 403))                 # blocked: waits for the session
    fetch.run(db, _cfg(), ctx=_ctx("", 403))                 # session never fetched it: degraded
    assert store.flagged_items(db) == []
    store.set_source_fields(db, SRC["name"], "once", done=0)  # ev sources reset
    out = fetch.run(db, _cfg(), ctx=_ctx("", 403))
    assert out["sources"][SRC["name"]]["unchanged"] is False
    flagged = store.flagged_items(db)
    assert [i["url"] for i in flagged] == [SRC["url"]] and flagged[0]["state"] == "fetched"
    assert flagged[0]["triage_reason"] is None
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_once_never_skipped_with_adaptive_on(db):
    cfg = _cfg()
    cfg.adaptive_fetch = "on"
    store.record_fetch(db, SRC["name"], "once", new=0, error=None)   # checked just now, not done
    out = fetch.run(db, cfg, ctx=_ctx(DOC))
    assert out["skipped"] == 0 and "skipped" not in out["sources"][SRC["name"]]
