import json

from evidence_echo.core import grading, store, interfaces
from evidence_echo.stages import judge, match, verify
from tests.conftest import FakeBackend
from tests.test_judge import VERDICT, _seed_extracted


def _seed_matched(db, url="https://example.com/p1", **over):
    item_id = _seed_extracted(db, url=url)
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(item_id), **over)]}))
    match.fixed_pass(db)
    return item_id


def _grade(db, item_id, tier, flags=(), note="n"):
    return verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(item_id), "tier": tier, "red_flags": list(flags), "note": note}]}))


def test_emit_is_blind(db, topic):
    _seed_matched(db)
    payload = json.loads(verify.emit(db, topic))
    item = payload["items"][0]
    assert set(item) == {"id", "title", "publication_date", "full_text"}
    assert "claimed" not in payload["instructions"]
    assert all(code in payload["instructions"] for code in ("no_baseline", "headline_overclaim"))


def test_equal_grade_agrees(db):
    iid = _seed_matched(db)
    _grade(db, iid, "A")
    item = store.get_item(db, iid)
    assert item["state"] == "verified" and item["disputed"] == 0 and item["skeptic_agrees"] == 1


def test_one_tier_gap_takes_lower_and_lowers_created_study(db):
    iid = _seed_matched(db)
    _grade(db, iid, "B", flags=["no_baseline"])
    item = store.get_item(db, iid)
    assert item["evidence_tier"] == "B" and item["judge_tier"] == "A" and item["disputed"] == 0
    assert json.loads(item["red_flags"]) == ["no_baseline"]
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "B"


def test_two_tier_gap_is_a_dispute(db):
    iid = _seed_matched(db)
    _grade(db, iid, "C")
    item = store.get_item(db, iid)
    assert item["disputed"] == 1 and item["dispute_reason"] == "grade_gap"
    assert item["evidence_tier"] == "A"
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "A"


def test_no_quote_is_a_dispute(db):
    iid = _seed_matched(db, quotes=None)
    _grade(db, iid, "A")
    item = store.get_item(db, iid)
    assert item["disputed"] == 1 and item["dispute_reason"] == "no_quote"


def test_red_flags_never_change_the_tier(db):
    iid = _seed_matched(db)
    _grade(db, iid, "A", flags=list(grading.RED_FLAGS))
    item = store.get_item(db, iid)
    assert item["evidence_tier"] == "A" and item["disputed"] == 0


def test_unknown_red_flag_is_rejected_and_stays_matched(db):
    iid = _seed_matched(db)
    out = _grade(db, iid, "A", flags=["vibes"])
    assert out["applied"] == 0 and out["rejected"]
    assert store.get_item(db, iid)["state"] == "matched"


def test_joining_item_does_not_lower_study(db):
    first = _seed_matched(db, url="https://example.com/p1")
    _grade(db, first, "A")
    second = _seed_extracted(db, url="https://example.com/p2")
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(second))]}))
    match.fixed_pass(db)                                # same primary_url -> joins study of `first`
    joined = store.get_item(db, second)
    assert joined["study_id"] == store.get_item(db, first)["study_id"]
    _grade(db, second, "B")
    assert store.get_item(db, second)["evidence_tier"] == "B"
    assert store.get_study(db, joined["study_id"])["evidence_tier"] == "A"


def test_pre_sp2_matched_item_is_not_disputed(db):
    iid = _seed_matched(db)
    db.execute("UPDATE items SET judge_tier=NULL, quote_check=NULL WHERE id=?", (iid,))
    db.commit()
    _grade(db, iid, "A")
    item = store.get_item(db, iid)
    assert item["disputed"] == 0 and item["dispute_reason"] is None


def test_skeptic_shape_rejected_for_ruling_and_vice_versa(db):
    iid = _seed_matched(db)
    old = {"results": [{"id": interfaces.opaque_id(iid), "agrees": True,
                        "adjusted_tier": None, "note": "n"}]}
    assert verify.apply(db, json.dumps(old))["applied"] == 0
    _grade(db, iid, "C")                                # dispute: grade gap
    blind = {"results": [{"id": interfaces.opaque_id(iid), "tier": "C", "note": "n"}]}
    assert verify.apply(db, json.dumps(blind), ruling=True)["applied"] == 0


def test_ruling_sets_the_study_tier(db):
    iid = _seed_matched(db)
    _grade(db, iid, "C")
    ruling = {"results": [{"id": interfaces.opaque_id(iid), "agrees": True,
                           "adjusted_tier": "C", "note": "owner"}]}
    assert verify.apply(db, json.dumps(ruling), ruling=True)["applied"] == 1
    item = store.get_item(db, iid)
    assert item["evidence_tier"] == "C" and item["disputed"] == 0
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "C"


def test_run_api_is_blind_and_compares(db, topic):
    iid = _seed_matched(db)
    backend = FakeBackend(['{"tier": "B", "red_flags": ["cherry_pick"], "note": "ok"}'])
    out = verify.run_api(db, backend, topic)
    assert out["processed"] == 1
    item = store.get_item(db, iid)
    assert item["evidence_tier"] == "B" and json.loads(item["red_flags"]) == ["cherry_pick"]
    prompt = backend.prompts[0]
    assert "overclaim" in prompt.lower() and "Claimed tier" not in prompt
    assert "Title tag rewrites" not in prompt          # judge's facts are not shown


def test_skeptic_above_capped_judge_never_raises_the_tier(db):
    iid = _seed_matched(db, facts=dict(VERDICT["facts"], has_controls=False))  # capped A -> B
    assert store.get_item(db, iid)["judge_tier"] == "B"
    _grade(db, iid, "A")
    item = store.get_item(db, iid)
    assert item["evidence_tier"] == "B" and item["disputed"] == 0
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "B"


def test_skeptic_far_above_judge_is_a_dispute_at_the_judge_tier(db):
    iid = _seed_matched(db, tier="D")
    _grade(db, iid, "A")
    item = store.get_item(db, iid)
    assert item["disputed"] == 1 and item["dispute_reason"] == "grade_gap"
    assert item["evidence_tier"] == "D"
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "D"


def test_resolving_ruling_clears_the_dispute_reason(db):
    iid = _seed_matched(db)
    _grade(db, iid, "C")
    ruling = {"results": [{"id": interfaces.opaque_id(iid), "agrees": True,
                           "adjusted_tier": "C", "note": "owner"}]}
    verify.apply(db, json.dumps(ruling), ruling=True)
    item = store.get_item(db, iid)
    assert item["disputed"] == 0 and item["dispute_reason"] is None


def test_non_resolving_ruling_keeps_the_dispute_reason(db):
    iid = _seed_matched(db)
    _grade(db, iid, "C")
    ruling = {"results": [{"id": interfaces.opaque_id(iid), "agrees": False,
                           "adjusted_tier": None, "note": "owner unsure"}]}
    verify.apply(db, json.dumps(ruling), ruling=True)
    item = store.get_item(db, iid)
    assert item["disputed"] == 1 and item["dispute_reason"] == "grade_gap"
