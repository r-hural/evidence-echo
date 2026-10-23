import json
from types import SimpleNamespace

from evidence_echo.core import store
from evidence_echo.stages import fallback
from tests.conftest import make_item

GOOD = "Measured: 12% more clicks over 8 weeks on 40 sites. " * 20
TOPIC = SimpleNamespace(id="t", name="T", domain="d", scope="s", subtopics=[],
                        sources=[{"type": "page", "name": "Blog", "url": "https://e.com/blog/"}])


def _flagged(db, url="https://e.com/blog/a-post-here", state="triaged"):
    return store.insert_item(db, dict(make_item(url=url), state=state, fetch_status="needs_session"))


def test_emit_lists_flagged_items_and_blocked_pages(db):
    _flagged(db)
    store.record_fetch(db, "Blog", "page", new=0, error="HTTP 403", blocked=True)
    env = json.loads(fallback.emit(db, TOPIC))
    assert env["stage"] == "fetch"
    wants = {(i["want"], i["url"]) for i in env["items"]}
    assert ("text", "https://e.com/blog/a-post-here") in wants
    assert ("links", "https://e.com/blog/") in wants
    assert {i["id"][0] for i in env["items"]} == {"i", "s"}
    assert "untrusted" in env["instructions"]


def test_apply_text(db):
    iid = _flagged(db)
    out = fallback.apply(db, TOPIC, json.dumps({"results": [{"id": f"i{iid}", "text": GOOD}]}))
    assert out == {"applied": 1, "rejected": []}
    item = store.get_item(db, iid)
    assert (item["state"], item["text_source"], item["fetch_status"]) == ("extracted", "session", None)


def test_apply_rejects_junk_unknown_and_both_fields(db):
    iid = _flagged(db)
    out = fallback.apply(db, TOPIC, json.dumps({"results": [
        {"id": f"i{iid}", "text": "Just a moment..."},
        {"id": "i99999", "text": GOOD},
        {"id": f"i{iid}", "text": GOOD, "links": []},
    ]}))
    assert out["applied"] == 0
    assert sorted(r["id"] for r in out["rejected"]) == sorted([f"i{iid}", "i99999", f"i{iid}"])
    assert store.get_item(db, iid)["fetch_status"] == "needs_session"


def test_apply_links_filters_and_stores(db):
    store.record_fetch(db, "Blog", "page", new=0, error="HTTP 403", blocked=True)
    sid = store.get_source_state(db, "Blog")["rowid"]
    out = fallback.apply(db, TOPIC, json.dumps({"results": [{"id": f"s{sid}", "links": [
        {"url": "https://e.com/blog/new-study-2026", "title": "New study"},
        {"url": "https://evil.com/blog/phish-page-here", "title": "x"},
        {"url": "javascript:alert(1)", "title": "x"},
        {"url": "https://e.com/tag/seo/", "title": "tag"}]}]}))
    assert out["applied"] == 1
    assert [i["url"] for i in store.items_in_state(db, "fetched")] == ["https://e.com/blog/new-study-2026"]
    assert store.get_source_state(db, "Blog")["blocked"] == 0


def test_text_for_full_mode_item_keeps_fetched_state(db):
    iid = _flagged(db, state="fetched")
    fallback.apply(db, TOPIC, json.dumps({"results": [{"id": f"i{iid}", "text": GOOD}]}))
    assert store.get_item(db, iid)["state"] == "fetched"
