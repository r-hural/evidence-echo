import json

from evidence_echo.core import interfaces, store
from evidence_echo.stages import judge
from tests.conftest import TEST_METRICS, make_item, FakeBackend

TEXT = ("Variant pages saw +4.2% organic clicks across 12k pages. "
        "The control group stayed flat over six weeks.")
SENTENCE = "Variant pages saw +4.2% organic clicks across 12k pages."

CLASSIFY = {"subtopic": {"propose": "Title tags & meta"}, "metric": "clicks"}

VERDICT = {
    "pass": True, "tier": "A",
    "facts": {
        "what_was_tested": "Title tag rewrites",
        "findings": "+4.2% organic clicks on variant group",
        "research_timeframe": "2026-Q2",
        "sample_scale": "12k pages",
        "has_controls": True, "methodology_stated": True,
        "duration": "6 weeks",
        "match_keys": {"key_numbers": ["4.2%", "12000"]},
        "metric": "organic clicks", "baseline": "control group",
    },
    "primary_url": "https://orig.example.com/study",
    "quotes": [{"number": "4.2%", "quote": SENTENCE}, {"number": "12k", "quote": SENTENCE}],
    "provenance": {"conducted_by": "Example Agency", "relation": "agency",
                   "conflict_of_interest": None},
    **CLASSIFY,
    "entities": [],
}


def _seed_extracted(db, url="https://example.com/p1", text=TEXT):
    if store.metric_for_slug(db, "clicks") is None:            # a connection no topic seeded
        store.seed_metrics(db, TEST_METRICS)
    store.insert_items(db, [make_item(url=url)])
    item = [i for i in store.items_in_state(db, "fetched")][0]
    store.set_triage(db, item["id"], True, "ok")
    store.set_full_text(db, item["id"], text)
    return item["id"]


def test_emit_includes_full_text_and_tier_instructions(db, topic):
    _seed_extracted(db)
    payload = json.loads(judge.emit(db, topic))
    assert payload["stage"] == "judge"
    assert "full_text" in payload["items"][0]
    assert "publication_date" in payload["items"][0]
    assert "Tier" in payload["instructions"]
    assert "research_timeframe" in payload["instructions"]


def test_apply_leaves_study_to_match(db):
    item_id = _seed_extracted(db)
    from evidence_echo.core import interfaces
    judge.apply(db, json.dumps(
        {"results": [dict(VERDICT, id=interfaces.opaque_id(item_id))]}))
    row = store.items_in_state(db, "judged")[0]
    assert row["evidence_tier"] == "A" and row["study_id"] is None
    from evidence_echo.stages import match
    match.fixed_pass(db)
    row = store.get_item(db, item_id)
    assert row["state"] == "matched"
    assert store.get_study(db, row["study_id"])["research_timeframe"] == "2026-Q2"


def test_judge_instructions_ask_for_series_and_editions(topic):
    text = judge.instructions(topic)
    assert "series" in text and "editions" in text
    assert "series" in judge.PROMPT and "editions" in judge.PROMPT


def test_run_api(db, topic):
    _seed_extracted(db)
    backend = FakeBackend([json.dumps(VERDICT)])
    judge.run_api(db, backend, topic)
    assert store.items_in_state(db, "judged")
    prompt = backend.prompts[0]
    assert "Tier A" in prompt and "research_timeframe" in prompt


def test_run_api_parse_error_leaves_item_extracted(db, topic):
    _seed_extracted(db)
    backend = FakeBackend(["garbage"])
    judge.run_api(db, backend, topic)
    assert store.items_in_state(db, "extracted")  # untouched, retried next run


def _apply(db, iid, **over):
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid), **over)]}))
    return store.get_item(db, iid)


def test_apply_checks_quotes_and_records_provenance(db):
    item = _apply(db, _seed_extracted(db))
    assert item["quote_check"] == "ok" and item["capped_from"] is None
    assert item["judge_tier"] == item["evidence_tier"] == "A"
    assert item["judged_from"] == "full_text"
    assert json.loads(item["provenance"])["relation"] == "agency"


def test_apply_caps_a_without_controls(db):
    facts = dict(VERDICT["facts"], has_controls=False)
    item = _apply(db, _seed_extracted(db), facts=facts)
    assert item["evidence_tier"] == "B" and item["capped_from"] == "A"


def test_apply_without_quotes_is_none(db):
    item = _apply(db, _seed_extracted(db), quotes=None)
    assert item["quote_check"] == "none"


def test_apply_partial_quotes(db):
    item = _apply(db, _seed_extracted(db), quotes=[{"number": "4.2%", "quote": SENTENCE}])
    assert item["quote_check"] == "partial"


def test_excerpt_text_marks_judged_from(db):
    iid = _seed_extracted(db)
    store.set_item_fields(db, iid, text_source="excerpt")
    assert _apply(db, iid)["judged_from"] == "excerpt"


def test_quotes_are_checked_against_the_emitted_text_only(db):
    iid = _seed_extracted(db, text="x " * 7600 + SENTENCE)   # sentence starts after 15,000 chars
    assert _apply(db, iid)["quote_check"] == "none"


def test_instructions_ask_for_quotes_and_provenance(topic):
    text = judge.instructions(topic)
    for word in ("quotes", "exactly", "provenance", "conflict_of_interest", "metric", "baseline"):
        assert word in text


def test_run_api_checks_quotes(db, topic):
    iid = _seed_extracted(db)
    judge.run_api(db, FakeBackend([json.dumps(VERDICT)]), topic)
    item = store.get_item(db, iid)
    assert item["quote_check"] == "ok" and item["judged_from"] == "full_text"


def test_fail_stores_reason(db):
    oid = _seed_extracted(db)
    judge.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(oid), "pass": False, "reason": "Opinion piece; no measured outcome."}]}))
    row = store.get_item(db, oid)
    assert row["state"] == "rejected"
    assert row["judge_reason"] == "Opinion piece; no measured outcome."
    assert row["rejected_at"] is not None


def test_pass_clears_reason(db):
    oid = _seed_extracted(db)
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(oid), reason="x")]}))
    assert store.get_item(db, oid)["judge_reason"] is None


def test_prompts_ask_for_reason():
    assert '"reason"' in judge.INSTRUCTIONS
    assert '"reason"' in judge.PROMPT


def test_reset_judge_clears_reason(workspace):
    from evidence_echo import api
    from evidence_echo.core import config
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    oid = _seed_extracted(conn)
    judge.apply(conn, json.dumps({"results": [
        {"id": interfaces.opaque_id(oid), "pass": False, "reason": "Opinion."}]}))
    conn.close()
    assert api.reset(cfg, "judge") == {"reset_to": "extracted"}
    conn = store.connect(cfg.db_path)
    row = store.get_item(conn, oid)
    assert row["judge_reason"] is None and row["state"] == "extracted"
    conn.close()
