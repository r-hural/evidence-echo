import json

from evidence_echo.core import config, grading, interfaces, store, topics
from evidence_echo.stages import judge, match, verify
from tests.test_judge import VERDICT, _seed_extracted


def _topic(template):
    return topics.load_template(template, config.TEMPLATES_DIR)


def _matched(db, topic, url="https://example.com/p1", **facts):
    facts.setdefault("study_type", "rct" if topic is not None and topic.id == "exercise" else "controlled_test")
    iid = _seed_extracted(db, url=url)
    res = dict(VERDICT, id=interfaces.opaque_id(iid), facts=dict(VERDICT["facts"], **facts))
    judge.apply(db, json.dumps({"results": [res]}), topic)
    match.fixed_pass(db)
    return iid


def _answer(iid, tier, flags=()):
    return {"id": interfaces.opaque_id(iid), "tier": tier, "red_flags": list(flags), "note": "n"}


def test_verify_payload_lists_global_and_topic_flags(db):
    ex = _topic("exercise")
    _matched(db, ex)
    flags = json.loads(verify.emit(db, ex))["topic"]["grading"]["red_flags"]
    assert list(flags) == list(grading.RED_FLAGS) + ["surrogate_outcome", "animal_to_human", "small_sample"]
    flags = json.loads(verify.emit(db, _topic("seo")))["topic"]["grading"]["red_flags"]
    assert list(flags) == list(grading.RED_FLAGS)


def test_verify_rejects_flag_of_another_field(db):
    ex = _topic("exercise")
    one = _matched(db, ex)
    two = _matched(db, ex, url="https://example.com/p2")
    out = verify.apply(db, json.dumps({"results": [
        _answer(one, "A", ["benchmark_contamination"]), _answer(two, "A")]}), ex)
    assert out["applied"] == 1 and len(out["rejected"]) == 1
    assert "benchmark_contamination is not a flag of this topic" in out["rejected"][0]["errors"][0]
    assert store.get_item(db, one)["state"] == "matched"
    assert store.get_item(db, two)["state"] == "verified"


def test_skeptic_a_on_animal_study_shows_c(db):
    ex = _topic("exercise")
    iid = _matched(db, ex, study_type="animal")
    verify.apply(db, json.dumps({"results": [_answer(iid, "A")]}), ex)
    item = store.get_item(db, iid)
    assert item["evidence_tier"] == "C" and item["capped_from"] == "A"
    assert json.loads(item["cap_reasons"]) == ["animal"] and item["disputed"] == 0
    assert item["skeptic_tier_raw"] == "A" and item["skeptic_tier"] == "C"
    assert item["verify_version"] == "verify-2"
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "C"


def test_caps_before_compare_no_false_gap(db):
    ex = _topic("exercise")
    iid = _matched(db, ex, study_type="animal")
    verify.apply(db, json.dumps({"results": [_answer(iid, "A")]}), ex)
    assert store.get_item(db, iid)["dispute_reason"] is None


def test_ruling_accepts_reported_dispute(db):
    iid = _matched(db, None)
    verify.apply(db, json.dumps({"results": [_answer(iid, "C")]}))
    db.execute("UPDATE items SET state='reported' WHERE id=?", (iid,))
    db.commit()
    assert store.get_item(db, iid)["disputed"] == 1
    out = verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(iid), "agrees": True, "adjusted_tier": "B", "note": "ruled"}]}),
        None, ruling=True)
    assert out["applied"] == 1
    assert store.get_item(db, iid)["state"] == "reported"
