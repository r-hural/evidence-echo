import json
from datetime import datetime, timezone

from evidence_echo.core import store
from evidence_echo.render import model
from evidence_echo.stages import match, verify
from tests.conftest import FakeBackend, make_item


def pregraded(db, url, *, disputed=0, reason=None, tier="C", number="7.7%"):
    """An item as `keep` leaves it: judged, with the blind grade already stored."""
    store.insert_items(db, [make_item(url=url)])
    item_id = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    facts = {"what_was_tested": "x", "findings": "f", "research_timeframe": "t",
             "match_keys": {"key_numbers": [number]}}
    db.execute("UPDATE items SET state='judged', verdict_pass=1, evidence_tier=?, judge_tier=?, "
               "judge_tier_raw='A', skeptic_tier=?, skeptic_tier_raw=?, skeptic_agrees=?, skeptic_note='n', "
               "red_flags='[]', disputed=?, dispute_reason=?, caps_hash='h1', facts=?, study_id=NULL "
               "WHERE id=?",
               (tier, "A", tier, tier, int(not disputed), disputed, reason, json.dumps(facts), item_id))
    db.commit()
    return item_id


def plain(db, url):
    store.insert_items(db, [make_item(url=url)])
    item_id = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    db.execute("UPDATE items SET state='judged', verdict_pass=1, evidence_tier='B', facts=?, study_id=NULL "
               "WHERE id=?", (json.dumps({"match_keys": {"key_numbers": ["1%"]}}), item_id))
    db.commit()
    return item_id


def test_pregraded_item_skips_skeptic(db, topic):
    a = pregraded(db, "https://e.com/a")
    assert match.fixed_pass(db) == []
    assert store.get_item(db, a)["state"] == "matched"
    out = json.loads(verify.emit(db, topic))
    assert out["items"] == []
    item = store.get_item(db, a)
    assert item["state"] == "verified" and item["skeptic_tier"] == "C"
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "C"


def test_pregraded_disputed_item_keeps_dispute(db, topic):
    a = pregraded(db, "https://e.com/a", disputed=1, reason="tier_gap", tier="C")
    match.fixed_pass(db)
    assert store.promote_pregraded(db) == 1
    item = store.get_item(db, a)
    assert item["state"] == "verified" and item["disputed"] == 1 and item["dispute_reason"] == "tier_gap"
    report = model.build_report(db, topic, {}, datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert [e.study_id for e in report.disputed] == [item["study_id"]] and report.new == []


def test_normal_items_unchanged(db, topic):
    a = plain(db, "https://e.com/a")
    match.fixed_pass(db)
    assert store.promote_pregraded(db) == 0
    assert len(json.loads(verify.emit(db, topic))["items"]) == 1
    assert store.get_item(db, a)["state"] == "matched"
    # a verify reset clears the skeptic fields: the item is asked again, not promoted
    verify.apply(db, json.dumps({"results": [{"id": json.loads(verify.emit(db, topic))["items"][0]["id"],
                                              "tier": "B", "red_flags": [], "note": "ok"}]}), topic)
    assert store.get_item(db, a)["state"] == "verified"
    db.execute("UPDATE items SET state='matched' WHERE id=?", (a,))
    db.commit()
    store.clear_blind_grades(db)
    assert len(json.loads(verify.emit(db, topic))["items"]) == 1
    assert store.get_item(db, a)["state"] == "matched"


def test_promote_in_api_mode(db, topic):
    a = pregraded(db, "https://e.com/a")
    match.fixed_pass(db)
    backend = FakeBackend([])      # any call pops from an empty list and fails
    out = verify.run_api(db, backend, topic)
    assert backend.prompts == [] and out["rejected"] == []
    assert store.get_item(db, a)["state"] == "verified"


def test_promote_in_apply(db, topic):
    a = pregraded(db, "https://e.com/a")
    match.fixed_pass(db)
    assert verify.apply(db, json.dumps({"results": []}), topic)["applied"] == 0
    assert store.get_item(db, a)["state"] == "verified"


def test_grading_columns_survive_promote(db, topic):
    a = pregraded(db, "https://e.com/a")
    before = store.get_item(db, a)
    match.fixed_pass(db)
    store.promote_pregraded(db)
    after = store.get_item(db, a)
    for col in ("judge_tier_raw", "skeptic_tier_raw", "caps_hash", "judge_tier", "skeptic_tier",
                "evidence_tier", "skeptic_agrees"):
        assert after[col] == before[col], col


def test_joined_study_keeps_its_tier(db, topic):
    sid = store.create_study(db, "A", {"match_keys": {"key_numbers": ["7.7%"]}}, "https://orig.example/r")
    a = pregraded(db, "https://e.com/a", tier="C")
    db.execute("UPDATE items SET primary_url='https://orig.example/r' WHERE id=?", (a,))
    db.commit()
    match.fixed_pass(db)
    store.promote_pregraded(db)
    item = store.get_item(db, a)
    assert item["study_id"] == sid and item["state"] == "verified"
    assert store.get_study(db, sid)["evidence_tier"] == "A"
