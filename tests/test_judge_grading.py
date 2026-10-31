import json

from evidence_echo.core import config, grading, interfaces, store, topics
from evidence_echo.stages import judge
from tests.conftest import make_item
from tests.test_judge import VERDICT, _seed_extracted


def _topic(template):
    return topics.load_template(template, config.TEMPLATES_DIR)


def _apply(db, topic, iid, facts=None, **over):
    res = dict(VERDICT, id=interfaces.opaque_id(iid), **over)
    if facts is not None:
        res["facts"] = dict(VERDICT["facts"], **facts)
    return judge.apply(db, json.dumps({"results": [res]}), topic)


def test_judge_prompt_uses_topic_tiers():
    text = judge.instructions(_topic("exercise"))
    assert "Randomised controlled trial in humans" in text and "animal" in text
    assert "A/B or split test" not in text
    assert "A/B or split test" in judge.instructions(_topic("seo"))   # seo has no block: the generic one


def test_judge_rejects_study_type_outside_subset(db):
    iid = _seed_extracted(db)
    out = _apply(db, _topic("exercise"), iid, facts={"study_type": "benchmark"})
    assert out["applied"] == 0
    assert out["rejected"][0]["errors"] == ["study_type benchmark is not in the topic's list"]
    assert store.get_item(db, iid)["state"] == "extracted"


def test_other_always_allowed(db):
    iid = _seed_extracted(db)
    out = _apply(db, _topic("exercise"), iid, facts={"study_type": "other"})
    assert out["applied"] == 1


def test_judge_requires_new_facts_when_passing(db):
    iid = _seed_extracted(db)
    facts = {k: v for k, v in VERDICT["facts"].items() if k != "peer_review"}
    res = dict(VERDICT, id=interfaces.opaque_id(iid), facts=facts)
    out = judge.apply(db, json.dumps({"results": [res]}), _topic("exercise"))
    assert out["applied"] == 0
    assert any("peer_review" in e for e in out["rejected"][0]["errors"])


def test_text_basis_forced_on_excerpt(db):
    iid = _seed_extracted(db)
    db.execute("UPDATE items SET text_source='excerpt' WHERE id=?", (iid,))
    db.commit()
    out = _apply(db, _topic("exercise"), iid, facts={"study_type": "rct"}, tier="A")
    assert out["applied"] == 1
    item = store.get_item(db, iid)
    assert json.loads(item["facts"])["text_basis"] == "abstract_only"
    assert item["text_basis"] == "abstract_only"
    assert item["judge_tier"] == "B" and json.loads(item["cap_reasons"]) == ["abstract_only"]


def test_judge_caps_animal_and_stamps(db):
    topic = _topic("exercise")
    iid = _seed_extracted(db)
    _apply(db, topic, iid, facts={"study_type": "animal"}, tier="A")
    item = store.get_item(db, iid)
    assert item["judge_tier"] == "C" and item["judge_tier_raw"] == "A" and item["capped_from"] == "A"
    assert item["graded_rules"] == grading.rules_hash(topic.grading)
    assert item["caps_hash"] == grading.caps_hash(topic.grading)
    assert item["judge_version"] == "judge-3"
