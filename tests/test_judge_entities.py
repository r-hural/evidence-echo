"""The judge names the entities a study is about: prompt, validation, storage (nothing created yet)."""
import json

from evidence_echo.core import interfaces, store
from evidence_echo.stages import judge
from tests.conftest import FakeBackend
from tests.test_judge import VERDICT, _seed_extracted


def _verdict(db, iid, **over):
    return judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid), **over)]}))


def _linked_study(db, name, entities):
    sid = store.create_study(db, "B", {"what_was_tested": name}, None)
    store.link_study_entities(db, sid, entities)
    return sid


def test_prompt_and_emit_list_entities(db, topic):
    _seed_extracted(db)
    payload = json.loads(judge.emit(db, topic))
    assert payload["entities"] == [] and "Entities: none named yet." in payload["instructions"]
    _linked_study(db, "a", ["Shopify", "ChatGPT"])
    _linked_study(db, "b", ["ChatGPT"])
    payload = json.loads(judge.emit(db, topic))
    assert payload["entities"] == ["ChatGPT", "Shopify"]
    text = payload["instructions"]
    assert "Entities already named in this topic (reuse these names):" in text
    assert text.index("- ChatGPT") < text.index("- Shopify")
    many = [f"Name {i:03d}" for i in range(store.ENTITY_PROMPT_MAX + 5)]
    for chunk in range(0, len(many), 5):
        _linked_study(db, f"c{chunk}", many[chunk:chunk + 5])
    assert len(json.loads(judge.emit(db, topic))["entities"]) == store.ENTITY_PROMPT_MAX


def test_rules_text():
    assert "entities:" in judge.CLASSIFY_RULES
    assert "Not who ran or published the study" in judge.CLASSIFY_RULES
    assert "Not generic concepts" in judge.CLASSIFY_RULES
    assert "entities is required when pass is true" in judge.PASS_RULE
    assert '"entities": ["..."]' in judge.PROMPT and '"entities": ["..."]' in judge.INSTRUCTIONS


def test_passing_verdict_without_entities_is_rejected(db):
    iid = _seed_extracted(db)
    missing = {k: v for k, v in VERDICT.items() if k != "entities"}
    for bad in (missing, dict(VERDICT, entities="ChatGPT"), dict(VERDICT, entities=None)):
        out = judge.apply(db, json.dumps({"results": [dict(bad, id=interfaces.opaque_id(iid))]}))
        assert out["applied"] == 0 and "entities" in out["rejected"][0]["errors"][0]
        assert store.get_item(db, iid)["state"] == "extracted"
    out = judge.apply(db, json.dumps({"results": [dict(missing, id=interfaces.opaque_id(iid))]}))
    assert out["rejected"][0]["errors"] == ["entities: a list of names, [] when none"]


def test_bad_elements_are_dropped_not_rejected(db):
    iid = _seed_extracted(db)
    out = _verdict(db, iid, entities=["ChatGPT", 5, "x" * 61])
    assert out["applied"] == 1
    assert store.get_item(db, iid)["entity_names"] == '["ChatGPT"]'


def test_failing_verdict_stores_null_and_needs_no_entities(db):
    iid = _seed_extracted(db)
    out = judge.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(iid), "pass": False, "reason": "Opinion."}]}))
    assert out["applied"] == 1 and store.get_item(db, iid)["entity_names"] is None


def test_no_entity_row_at_judge_time(db):
    iid = _seed_extracted(db)
    _verdict(db, iid, entities=["ChatGPT", "Shopify"])
    assert db.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0


def test_run_api_applies_entities(db, topic):
    iid = _seed_extracted(db)
    backend = FakeBackend([json.dumps(dict(VERDICT, entities=["Shopify"]))])
    result = judge.run_api(db, backend, topic)
    assert result["processed"] == 1 and store.get_item(db, iid)["entity_names"] == '["Shopify"]'
    assert "Entities: none named yet." in backend.prompts[0]
    iid2 = _seed_extracted(db, url="https://example.com/p2")
    bad = FakeBackend([json.dumps({k: v for k, v in VERDICT.items() if k != "entities"})])
    result = judge.run_api(db, bad, topic)
    assert result["processed"] == 0 and "entities" in result["rejected"][0]["errors"][0]
    assert store.get_item(db, iid2)["state"] == "extracted"
