import json
from pathlib import Path

import pytest

from evidence_echo.core import contracts, interfaces, store
from evidence_echo.stages import judge, match, report, triage, verify
from tests.conftest import FakeBackend, make_item, seed_new_study
from tests.test_judge import VERDICT, _seed_extracted


def _fetched(db, n):
    store.insert_items(db, [make_item(url=f"https://example.com/c{i}") for i in range(n)])
    return [interfaces.opaque_id(it["id"]) for it in store.items_in_state(db, "fetched")]


def _verified(db, agrees=True):
    oid = interfaces.opaque_id(_seed_extracted(db))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid)]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [
        # blind shape; a two-tier gap (judge A vs skeptic C) is what makes a dispute
        {"id": oid, "tier": "A" if agrees else "C", "red_flags": [],
         "note": "ok" if agrees else "no control"}]}))
    return oid


def test_triage_apply_mixed_payload(db):
    a, b, c = _fetched(db, 3)
    out = triage.apply(db, json.dumps({"results": [
        {"id": a, "pass": True, "reason": "numbers"},
        {"id": b, "pass": "yes", "reason": "bad type"},
        {"id": a, "pass": False, "reason": "duplicate"},
        {"id": "i999", "pass": True, "reason": "unknown"},
        {"id": "x1", "pass": True, "reason": "malformed"},
        "not an object",
    ]}))
    assert out["applied"] == 1
    assert [r["id"] for r in out["rejected"]] == [b, a, "i999", "x1", "#5"]
    assert out["rejected"][0]["errors"] == ["pass: expected bool"]
    assert len(store.items_in_state(db, "triaged")) == 1
    assert {interfaces.opaque_id(i["id"]) for i in store.items_in_state(db, "fetched")} == {b, c}


def test_judge_invalid_result_keeps_item_extracted(db):
    oid = interfaces.opaque_id(_seed_extracted(db))
    out = judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid, tier="A+")]}))
    assert out == {"applied": 0,
                   "rejected": [{"id": oid, "errors": ["tier: 'A+' not in A|B|C|D"]}]}
    assert store.items_in_state(db, "extracted")


def test_judge_rejects_item_not_awaiting_judge(db):
    oid = interfaces.opaque_id(_seed_extracted(db))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid)]}))
    match.fixed_pass(db)
    out = judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid)]}))
    assert out["applied"] == 0
    assert out["rejected"][0]["errors"] == [f"id: '{oid}' is not awaiting judge"]


def test_verify_reapply_on_verified_item_for_adjudication(db):
    oid = _verified(db, agrees=False)
    out = verify.apply(db, json.dumps({"results": [
        {"id": oid, "agrees": True, "adjusted_tier": None, "note": "owner ruling: keep A"}]}),
        ruling=True)
    assert out == {"applied": 1, "rejected": []}
    row = store.items_in_state(db, "verified")[0]
    assert row["disputed"] == 0 and row["skeptic_note"] == "owner ruling: keep A"


def test_reapplied_skeptic_output_is_not_a_ruling(db):
    # without --ruling, verified items are not awaiting verify: a second apply of
    # skeptic output (steerable by article text) can neither change tiers nor notes
    oid = _verified(db, agrees=False)
    out = verify.apply(db, json.dumps({"results": [
        {"id": oid, "tier": "D", "red_flags": [], "note": "again"}]}))
    assert out["applied"] == 0
    assert out["rejected"][0]["errors"] == [f"id: '{oid}' is not awaiting verify"]
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    assert store.get_study(db, sid)["evidence_tier"] == "A"


def test_ruling_only_for_verified_items(db):
    oid = interfaces.opaque_id(_seed_extracted(db))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid)]}))
    match.fixed_pass(db)
    out = verify.apply(db, json.dumps({"results": [
        {"id": oid, "agrees": True, "adjusted_tier": "D", "note": "x"}]}), ruling=True)
    assert out["applied"] == 0
    assert out["rejected"][0]["errors"] == [f"id: '{oid}' is not awaiting ruling"]


@pytest.mark.parametrize("mod", [triage, judge, verify])
@pytest.mark.parametrize("payload", ["not json", "[]", '{"results": {}}', '{"items": []}'])
def test_malformed_payload_raises(db, mod, payload):
    with pytest.raises(contracts.ContractError):
        mod.apply(db, payload)


def test_report_malformed_payload_raises(db, tmp_path, topic):
    seed_new_study(db)                      # with nothing new, render stops before reading the payload
    with pytest.raises(contracts.ContractError):
        report.render(db, "not json", tmp_path, "en", topic)


def test_report_rejected_entry_falls_back_to_default_rendering(db, tmp_path, topic):
    _verified(db)
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    out = report.render(db, json.dumps({"results": [
        {"study_id": sid, "title": 42, "summary": "S"},
        {"study_id": 999, "title": "T", "summary": "S"},
    ]}), tmp_path, "en", topic)
    assert out["applied"] == 0
    assert [r["id"] for r in out["rejected"]] == [str(sid), "999"]
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "Title tag rewrites" in text            # canonical name fallback
    assert "+4.2% organic clicks" in text          # findings fallback
    assert store.items_in_state(db, "reported")


def test_triage_run_api_rejects_bad_response_and_keeps_state(db, topic):
    _fetched(db, 2)
    out = triage.run_api(db, FakeBackend(['{"pass": true, "reason": "ok"}',
                                          '{"pass": "maybe"}']), topic)
    assert out["processed"] == 1
    assert out["rejected"][0]["errors"] == ["pass: expected bool", "reason: missing"]
    assert len(store.items_in_state(db, "fetched")) == 1


def test_judge_run_api_rejects_schema_violation(db, topic):
    oid = interfaces.opaque_id(_seed_extracted(db))
    out = judge.run_api(db, FakeBackend([json.dumps(dict(VERDICT, tier="Z"))]), topic)
    assert out == {"processed": 0,
                   "rejected": [{"id": oid, "errors": ["tier: 'Z' not in A|B|C|D"]}]}
    assert store.items_in_state(db, "extracted")


def test_verify_run_api_returns_counts(db, topic):
    oid = interfaces.opaque_id(_seed_extracted(db))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid)]}))
    match.fixed_pass(db)
    out = verify.run_api(db, FakeBackend(
        ['{"tier": "A", "red_flags": [], "note": "ok"}']), topic)
    assert out == {"processed": 1, "rejected": []}


def test_run_api_backend_exception_is_rejected_not_raised(db, topic):
    _fetched(db, 1)

    class Boom:
        def complete(self, prompt, max_tokens=500):
            raise RuntimeError("overloaded")

    out = triage.run_api(db, Boom(), topic)
    assert out["processed"] == 0
    assert out["rejected"][0]["errors"] == ["backend error: overloaded"]
    assert store.items_in_state(db, "fetched")


def test_verify_apply_without_red_flags(db):
    oid = interfaces.opaque_id(_seed_extracted(db))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid)]}))
    match.fixed_pass(db)
    out = verify.apply(db, json.dumps({"results": [{"id": oid, "tier": "A", "note": "ok"}]}))
    assert out == {"applied": 1, "rejected": []}


def test_ruling_for_skeptics_tier_changes_reported_tier(db, tmp_path, topic):
    oid = _verified(db, agrees=False)                       # judge said A, skeptic said C
    verify.apply(db, json.dumps({"results": [
        {"id": oid, "agrees": True, "adjusted_tier": "C", "note": "owner ruling: C"}]}),
        ruling=True)
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    assert store.get_study(db, sid)["evidence_tier"] == "C"
    out = report.render(db, json.dumps({"results": [
        {"study_id": sid, "title": "T", "summary": "S"}]}), tmp_path, "en", topic)
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "### [C · agency] T" in text and "judge:" not in text and "[A]" not in text
    assert "Disputed" not in text


def test_skeptic_reply_far_below_judge_does_not_change_study_tier(db, topic):
    # untrusted article text can steer the skeptic; only owner rulings may change tiers
    oid = interfaces.opaque_id(_seed_extracted(db))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid)]}))
    match.fixed_pass(db)
    verify.run_api(db, FakeBackend(
        ['{"tier": "D", "red_flags": [], "note": "ok"}']), topic)   # 3-tier gap: disputed, study stays A
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    assert store.get_study(db, sid)["evidence_tier"] == "A"


def test_first_session_verify_far_below_judge_keeps_study_tier(db):
    oid = interfaces.opaque_id(_seed_extracted(db))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=oid)]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [
        {"id": oid, "tier": "D", "red_flags": [], "note": "ok"}]}))   # 3-tier gap: disputed, study stays A
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    assert store.get_study(db, sid)["evidence_tier"] == "A"


def test_disputed_entry_shows_both_grades_and_the_doubt(db, tmp_path, topic):
    _verified(db, agrees=False)                             # judge said A, skeptic said C
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    assert json.loads(report.emit(db, topic, "en"))["items"][0]["skeptic_tiers"] == ["C"]
    out = report.render(db, json.dumps({"results": [
        {"study_id": sid, "title": "T", "summary": "S"}]}), tmp_path, "en", topic)
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "### [A / skeptic: C · agency] T" in text
    assert "> Disputed: grades two tiers apart. Skeptic's doubts: no control" in text
    assert "## Disputed" in text
