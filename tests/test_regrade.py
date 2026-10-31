"""Regrade stage: the code-only re-cap pass and the LLM re-grade on a rules change."""
import copy
import json
from dataclasses import replace

import pytest

from evidence_echo.core import config, grading, interfaces, settings, store, topics
from evidence_echo.core.settings import ConfigError
from evidence_echo.stages import judge, match, regrade, report, verify
from tests.conftest import FakeBackend, make_item
from tests.test_judge import SENTENCE, TEXT, VERDICT


def _exercise():
    return topics.load_template("exercise", config.TEMPLATES_DIR)


def _with_grading(topic, **changes):
    block = copy.deepcopy(topic.grading)
    for key, value in changes.items():
        block[key] = value
    return replace(topic, grading=grading.effective(block))


def _seed(db, topic, n=1, *, verified="A", unique=True, text=TEXT, **facts):
    """An item judged (and matched, and verified when `verified`) under `topic`; its own study."""
    url = f"https://example.com/p{n}"
    store.insert_items(db, [make_item(url=url)])
    iid = store.find_by_canonical(db, store.canonical_url(url))["id"]
    store.set_triage(db, iid, True, "ok")
    if text is not None:
        store.set_full_text(db, iid, text)
    else:
        store.set_full_text(db, iid, "x")
        db.execute("UPDATE items SET full_text=NULL WHERE id=?", (iid,))
        db.commit()
    all_facts = {**VERDICT["facts"], "study_type": "rct", **facts}
    extra = {}
    if unique:
        all_facts["match_keys"] = {"key_numbers": [f"{n}.7%"]}
        all_facts["what_was_tested"] = f"Study number {n}"
        extra = {"primary_url": f"https://orig.example.com/study{n}", "quotes": [
            {"number": f"{n}.7%", "quote": SENTENCE}]}
    res = dict(VERDICT, id=interfaces.opaque_id(iid), facts=all_facts, **extra)
    judge.apply(db, json.dumps({"results": [res]}), topic)
    match.fixed_pass(db)
    if verified:
        verify.apply(db, json.dumps({"results": [{"id": interfaces.opaque_id(iid), "tier": verified,
                                                  "red_flags": [], "note": "n"}]}), topic)
    return iid


def _answer(iid, tier="A", study_type="rct", peer_review="reviewed", text_basis="full"):
    return {"id": interfaces.opaque_id(iid), "tier": tier, "study_type": study_type,
            "peer_review": peer_review, "text_basis": text_basis}


def _hash(topic):
    return grading.rules_hash(grading.rules_of(topic))


def test_tier_text_change_selects_items(db):
    ex = _exercise()
    _seed(db, ex)
    assert store.items_to_regrade(db, _hash(ex)) == []
    tiers = dict(ex.grading["tiers"], A="Only a randomised trial in humans counts as A.")
    changed = _with_grading(ex, tiers=tiers)
    assert len(store.items_to_regrade(db, _hash(changed))) == 1


def test_null_graded_rules_selected(db):
    ex = _exercise()
    one = _seed(db, ex, 1)
    two = _seed(db, ex, 2)
    first = _seed(db, ex, 3, unique=False)        # same primary url: the next one joins its study
    joiner = _seed(db, ex, 4, unique=False)
    db.execute("UPDATE items SET graded_rules=NULL")
    db.commit()
    assert store.get_item(db, joiner)["study_id"] == store.get_item(db, first)["study_id"]
    assert {it["id"] for it in store.items_to_regrade(db, _hash(ex))} == {one, two, first}


def test_merged_away_creator_not_selected(db):
    ex = _exercise()
    one = _seed(db, ex, 1)
    two = _seed(db, ex, 2)
    db.execute("UPDATE items SET graded_rules=NULL")
    db.commit()
    store.merge_studies(db, store.get_item(db, one)["study_id"], store.get_item(db, two)["study_id"])
    assert [it["id"] for it in store.items_to_regrade(db, _hash(ex))] == [one]


def test_caps_only_change_needs_no_llm(db):
    ex = _exercise()
    iid = _seed(db, ex, study_type="observational")
    assert store.get_item(db, iid)["evidence_tier"] == "A"
    caps = copy.deepcopy(ex.grading["caps"])
    caps.setdefault("study_type", {})["observational"] = "B"
    capped = _with_grading(ex, caps=caps)
    payload = json.loads(regrade.emit(db, capped))
    assert payload["items"] == [] and payload["recapped"] >= 1
    item = store.get_item(db, iid)
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "B"
    assert item["evidence_tier"] == "B" and item["capped_from"] == "A"
    assert json.loads(item["cap_reasons"]) == ["observational"]
    assert item["caps_hash"] == grading.caps_hash(capped.grading)
    assert json.loads(regrade.emit(db, capped))["recapped"] == 0


def _legacy(db, ids):
    """Make items look as if graded before step 15: no raws, no stamps, no new facts."""
    db.execute("""UPDATE items SET judge_tier_raw=NULL, skeptic_tier_raw=NULL, graded_rules=NULL,
                  caps_hash=NULL, judge_version=NULL, verify_version=NULL, ruled_tier=NULL,
                  cap_reasons=NULL, study_type=NULL, peer_review=NULL, text_basis=NULL""")
    for iid in ids:
        facts = store._loads(store.get_item(db, iid)["facts"])
        for key in ("study_type", "peer_review", "text_basis"):
            facts.pop(key, None)
        db.execute("UPDATE items SET facts=? WHERE id=?", (json.dumps(facts), iid))
    db.commit()


def test_recap_on_legacy_items_keeps_generic_grades(db, tmp_path):
    seo = topics.load_template("seo", config.TEMPLATES_DIR)
    plain = _seed(db, seo, 1, verified="A")
    no_controls = _seed(db, seo, 2, verified="B", has_controls=False)       # judge A capped to B
    disputed = _seed(db, seo, 3, verified="D")                              # judge A, skeptic D: gap
    ruled = _seed(db, seo, 4, verified="D")
    verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(ruled), "agrees": True, "adjusted_tier": "B", "note": "ruled"}]}),
        seo, ruling=True)
    unreviewed = _seed(db, seo, 5, verified=None)                           # still matched
    ids = [plain, no_controls, disputed, ruled, unreviewed]
    _legacy(db, ids)
    before = {i: (store.get_item(db, i)["evidence_tier"], store.get_item(db, i)["disputed"],
                  store.get_study(db, store.get_item(db, i)["study_id"])["evidence_tier"]) for i in ids}
    assert before[disputed][1] == 1 and before[ruled][:2] == ("B", 0)
    assert len(store.items_to_regrade(db, _hash(seo))) == len(ids)          # every creator is queued
    assert regrade.recap(db, seo) == len(ids)
    after = {i: (store.get_item(db, i)["evidence_tier"], store.get_item(db, i)["disputed"],
                 store.get_study(db, store.get_item(db, i)["study_id"])["evidence_tier"]) for i in ids}
    assert after == before
    assert regrade.recap(db, seo) == 0
    # a report rendered before the regrade finishes still works
    out = report.render(db, '{"results": []}', tmp_path / "reports", "English", seo)
    assert "path" in out or out.get("skipped")


def test_batch_limit_and_pending(db):
    ex = _exercise()
    for n in range(1, 51):
        _seed(db, ex, n, verified=None)
    db.execute("UPDATE items SET graded_rules=NULL")
    db.commit()
    data = json.loads(regrade.emit(db, ex, cap=40))
    assert len(data["items"]) == 40 and data["pending"] == 10
    assert data["items"][0]["id"].startswith("i") and "full_text" in data["items"][0]


def test_regrade_keeps_findings_quotes_question_and_match(db):
    ex = _exercise()
    iid = _seed(db, ex, study_type="rct")
    before_item, study_id = store.get_item(db, iid), store.get_item(db, iid)["study_id"]
    before_study = store.get_study(db, study_id)
    db.execute("UPDATE items SET graded_rules=NULL WHERE id=?", (iid,))
    db.commit()
    out = regrade.apply(db, json.dumps({"results": [
        _answer(iid, tier="B", study_type="observational", peer_review="preprint")]}), ex)
    assert out["applied"] == 1 and out["rejected"] == [] and out["pending"] == 0
    item, study = store.get_item(db, iid), store.get_study(db, study_id)
    for column in ("state", "evidence_tier", "disputed", "quotes", "quote_check", "study_id", "match_reason",
                   "skeptic_tier", "red_flags"):
        assert item[column] == before_item[column], column
    for column in ("what_was_tested", "findings", "question_id", "evidence_tier", "canonical_name"):
        assert study[column] == before_study[column], column
    assert item["study_type"] == "observational" and study["study_type"] == "observational"
    assert study["peer_review"] == "preprint" and store._loads(item["facts"])["study_type"] == "observational"
    assert item["judge_tier_raw"] == "B" and item["judge_version"] == "regrade-1"
    assert item["graded_rules"] == _hash(ex) and item["verify_version"] is None
    assert store._loads(item["facts"])["findings"] == store._loads(before_item["facts"])["findings"]
    assert db.execute("SELECT detail FROM runs WHERE stage='regrade'").fetchone()[0] == "1"


def test_study_type_outside_topic_list_rejected(db):
    ex = _exercise()
    iid = _seed(db, ex)
    db.execute("UPDATE items SET graded_rules=NULL")
    db.commit()
    out = regrade.apply(db, json.dumps({"results": [_answer(iid, study_type="benchmark")]}), ex)
    assert out["applied"] == 0 and "not in the topic's list" in out["rejected"][0]["errors"][0]
    assert out["pending"] == 1


def test_no_text_marks_unavailable(db):
    ex = _exercise()
    iid = _seed(db, ex, text=None)
    db.execute("UPDATE items SET graded_rules=NULL, excerpt='' WHERE id=?", (iid,))
    db.commit()
    tier = store.get_item(db, iid)["evidence_tier"]
    data = json.loads(regrade.emit(db, ex))
    assert data["items"] == [] and data["pending"] == 0
    item = store.get_item(db, iid)
    assert item["graded_rules"] == "unavailable" and item["evidence_tier"] == tier
    assert store.items_to_regrade(db, _hash(ex)) == []


def test_excerpt_regrade_gets_abstract_only(db):
    ex = _exercise()
    iid = _seed(db, ex, text=None)
    db.execute("UPDATE items SET graded_rules=NULL, excerpt='A short abstract of the trial.' WHERE id=?", (iid,))
    db.commit()
    data = json.loads(regrade.emit(db, ex))
    assert data["items"][0]["text_from"] == "excerpt" and "abstract" in data["items"][0]["full_text"]
    regrade.apply(db, json.dumps({"results": [_answer(iid, tier="A", text_basis="full")]}), ex)
    item = store.get_item(db, iid)
    assert item["text_basis"] == "abstract_only" and item["judge_tier"] == "B"
    assert json.loads(item["cap_reasons"]) == ["abstract_only"]


def test_run_api_regrades_and_reports_counts(db):
    ex = _exercise()
    iid = _seed(db, ex)
    db.execute("UPDATE items SET graded_rules=NULL")
    db.commit()
    backend = FakeBackend([json.dumps({k: v for k, v in _answer(iid, tier="C").items() if k != "id"})])
    out = regrade.run_api(db, backend, ex)
    assert out == {"processed": 1, "rejected": [], "recapped": 0, "done": 1, "pending": 0}
    assert "Tier A" in backend.prompts[0] and "study_type" in backend.prompts[0]
    assert store.get_item(db, iid)["judge_tier"] == "C"


def test_regrade_batch_zero_is_off_but_recaps(db):
    ex = _exercise()
    iid = _seed(db, ex, study_type="observational")
    caps = copy.deepcopy(ex.grading["caps"])
    caps.setdefault("study_type", {})["observational"] = "C"
    capped = _with_grading(ex, caps=caps)
    data = json.loads(regrade.emit(db, capped, cap=0))
    assert data["skipped"] == regrade.OFF and data["items"] == [] and data["recapped"] == 1
    assert store.get_item(db, iid)["evidence_tier"] == "C"
    out = regrade.run_api(db, None, capped, cap=0)
    assert out["skipped"] == regrade.OFF and out["processed"] == 0


def test_regrade_payload_carries_topic_rules(db):
    ex = _exercise()
    _seed(db, ex)
    db.execute("UPDATE items SET graded_rules=NULL")
    db.commit()
    data = json.loads(regrade.emit(db, ex))
    assert data["stage"] == "regrade" and data["topic"]["grading"]["tiers"]["A"] == ex.grading["tiers"]["A"]
    assert ex.grading["tiers"]["A"] in data["instructions"] and "peer_review" in data["instructions"]


def test_contract_needs_tier_and_the_three_facts():
    from evidence_echo.core import contracts
    good = {"id": "i1", "tier": "A", "study_type": "rct", "peer_review": "unknown", "text_basis": "full"}
    assert contracts.validate_result("regrade", good) == []
    for key in ("tier", "study_type", "peer_review", "text_basis"):
        assert contracts.validate_result("regrade", {k: v for k, v in good.items() if k != key})
    assert contracts.validate_result("regrade", dict(good, study_type="mouse"))


def test_regrade_batch_setting(workspace, monkeypatch):
    assert settings.GLOBAL_DEFAULTS["regrade_batch"] == 40 and "regrade_batch" in settings.OVERRIDES
    assert settings.check_value("regrade_batch", "0", "f") == 0
    assert settings.check_value("regrade_batch", 500, "f") == 500
    for bad in ("-1", "501", "x", "2.5", True):
        with pytest.raises(ConfigError, match="regrade_batch"):
            settings.check_value("regrade_batch", bad, "f")
    assert config.load().regrade_batch == 40
    settings.save_global(workspace, {"regrade_batch": 10})
    assert config.load().regrade_batch == 10
    t = topics.load_topic(workspace / "topics", "seo")
    topics.save_topic(workspace / "topics", replace(t, regrade_batch=3))
    assert config.load().regrade_batch == 3
    monkeypatch.setenv("EV_ECHO_REGRADE_BATCH", "0")
    assert config.load().regrade_batch == 0
