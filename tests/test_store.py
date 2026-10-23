import json

from evidence_echo.core import store
from tests.conftest import make_item


def test_insert_new_items_dedupes_by_url(db):
    n1 = store.insert_items(db, [make_item()])
    n2 = store.insert_items(db, [make_item()])  # same URL again
    assert (n1, n2) == (1, 0)
    assert len(store.items_in_state(db, "fetched")) == 1


def test_state_transitions(db):
    store.insert_items(db, [make_item()])
    item = store.items_in_state(db, "fetched")[0]
    store.set_triage(db, item["id"], passed=True, reason="has numbers")
    assert store.items_in_state(db, "triaged")[0]["triage_pass"] == 1
    store.set_triage(db, item["id"], passed=False, reason="opinion")
    assert store.items_in_state(db, "rejected_triage")


def test_set_verdict_leaves_item_judged_without_study(db):
    store.insert_items(db, [make_item()])
    item = store.items_in_state(db, "fetched")[0]
    facts = {"what_was_tested": "title tags", "findings": "+4.2% clicks"}
    assert store.set_verdict(db, item["id"], passed=True, tier="A",
                             facts=facts, primary_url="https://orig.example.com/study") is None
    row = store.get_item(db, item["id"])
    assert row["state"] == "judged" and row["study_id"] is None
    assert row["evidence_tier"] == "A"


def test_verdict_fail_rejects(db):
    store.insert_items(db, [make_item()])
    item = store.items_in_state(db, "fetched")[0]
    store.set_verdict(db, item["id"], passed=False, tier=None, facts={}, primary_url=None)
    assert store.items_in_state(db, "rejected")


def test_skeptic_and_dispute(db):
    store.insert_items(db, [make_item()])
    item = store.items_in_state(db, "fetched")[0]
    store.set_verdict(db, item["id"], passed=True, tier="B", facts={}, primary_url=None)
    store.set_skeptic(db, item["id"], agrees=False, adjusted_tier="C", note="sample unstated")
    row = store.items_in_state(db, "verified")[0]
    assert row["disputed"] == 1 and row["skeptic_tier"] == "C"


def test_insert_item_dedups_on_canonical_url(db):
    base = dict(source_name="B", source_type="rss", title="t", excerpt="e",
                published=None, fetched_at="2026-10-15")
    assert store.insert_item(db, dict(base, url="https://e.com/a?utm_source=x")) is not None
    assert store.insert_item(db, dict(base, url="http://E.com/a")) is None
    assert store.find_by_canonical(db, "https://e.com/a")["url"] == "https://e.com/a?utm_source=x"


def test_record_fetch_counts_errors_and_resets(db):
    for _ in range(2):
        store.record_fetch(db, "S", "rss", new=0, error="HTTP 500")
    s = store.get_source_state(db, "S")
    assert s["consecutive_errors"] == 2 and s["failing_since"] and s["last_error"] == "HTTP 500"
    store.record_fetch(db, "S", "rss", new=3, error=None, cursor={"etag": "x"})
    s = store.get_source_state(db, "S")
    assert (s["consecutive_errors"], s["failing_since"], s["last_error"]) == (0, None, None)
    assert s["items_total"] == 3 and s["last_new_item"] and s["cursor"] == '{"etag": "x"}'
