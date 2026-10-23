import json

from evidence_echo.core import store
from evidence_echo.stages import triage
from tests.conftest import make_item, FakeBackend


def _seed(db, n=2):
    for i in range(n):
        store.insert_items(db, [make_item(url=f"https://example.com/p{i}")])


def test_emit_lists_fetched_items_with_opaque_ids(db, topic):
    _seed(db)
    payload = json.loads(triage.emit(db, topic))
    assert payload["stage"] == "triage"
    assert payload["topic"]["id"] == "seo"
    assert len(payload["items"]) == 2
    assert payload["items"][0]["id"].startswith("i")
    assert "title" in payload["items"][0] and "excerpt" in payload["items"][0]


def test_apply_moves_items(db, topic):
    _seed(db)
    ids = [it["id"] for it in json.loads(triage.emit(db, topic))["items"]]
    results = json.dumps({"results": [
        {"id": ids[0], "pass": True, "reason": "measured outcome"},
        {"id": ids[1], "pass": False, "reason": "opinion"},
    ]})
    triage.apply(db, results)
    assert len(store.items_in_state(db, "triaged")) == 1
    assert len(store.items_in_state(db, "rejected_triage")) == 1


def test_run_api_uses_backend(db, topic):
    _seed(db, n=1)
    backend = FakeBackend(['{"pass": true, "reason": "numbers present"}'])
    triage.run_api(db, backend, topic)
    assert len(store.items_in_state(db, "triaged")) == 1
    assert "measured outcome" in backend.prompts[0].lower()
    assert "SEO and AI search" in backend.prompts[0]


def test_run_api_unparseable_response_keeps_item_fetched(db, topic):
    # contract: an unusable response is rejected and the item is retried next run
    _seed(db, n=1)
    out = triage.run_api(db, FakeBackend(["not json at all"]), topic)
    assert out["processed"] == 0 and out["rejected"]
    assert len(store.items_in_state(db, "fetched")) == 1
