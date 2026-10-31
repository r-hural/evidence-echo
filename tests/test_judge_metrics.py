"""Metrics: the judge picks a metric slug from the topic's list; the old direction is gone."""
import json
import re

from evidence_echo.core import contracts, interfaces, store
from evidence_echo.stages import classify, judge, match
from tests.conftest import FakeBackend, TEST_METRICS
from tests.test_judge import VERDICT, _seed_extracted
from tests.test_judge_subtopics import _apply


def test_judge_prompt_lists_metrics_without_direction(db, topic):
    store.seed_subtopics(db, topic.subtopics)
    text = judge.instructions(topic, store.active_subtopics(db), store.active_metrics(db))
    assert "Metrics (slug: name):\n- clicks: Clicks" in text and "- other: Other" in text
    assert text.index("- clicks: Clicks") < text.rindex("- other: Other")
    rule = "Pick the most specific subtopic and metric that fits; a broad one only when no narrower one does."
    assert rule in text and rule in judge.PROMPT.format(
        domain="d", scope="s", tier_rules="", classify_rules=judge.CLASSIFY_RULES, findings_rules="", pass_rule=judge.PASS_RULE,
        subtopics="", metrics="", entities="", published="", title="", full_text="")
    assert "metric is required when pass is true" in text
    for blob in (text, judge.PROMPT, judge.INSTRUCTIONS, judge.CLASSIFY_RULES, judge.PASS_RULE):
        for word in ("direction:", '"direction"', "positive", "no_effect"):
            assert word not in blob, word
    assert judge.metric_list([]) == 'Metrics: none listed - use "other".'


def test_judge_unknown_metric_rejected(db, topic):
    iid, out = _apply(db, metric="sessions")
    assert out["applied"] == 0
    assert out["rejected"][0]["errors"] == ["metric sessions is not in the topic's list"]
    assert store.get_item(db, iid)["state"] == "extracted"
    backend = FakeBackend([json.dumps(dict(VERDICT, metric="sessions"))])
    result = judge.run_api(db, backend, topic)
    assert result["processed"] == 0 and "not in the topic's list" in result["rejected"][0]["errors"][0]
    assert "- clicks: Clicks" in backend.prompts[0]


def test_judge_failing_verdict_needs_no_metric(db):
    iid = _seed_extracted(db)
    out = judge.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(iid), "pass": False, "reason": "Opinion.", "metric": "nope"}]}))
    assert out["applied"] == 1


def test_judge_merged_metric_slug_is_accepted(db):
    store.merge_metrics(db, "impressions", "clicks")
    iid, out = _apply(db, metric="impressions")
    assert out["applied"] == 1 and store.get_item(db, iid)["metric_code"] == "impressions"
    match.fixed_pass(db)
    study = store.get_study(db, store.get_item(db, iid)["study_id"])
    assert store.study_metric(db, study)["slug"] == "clicks"


def test_judge_stores_metric_slug_and_metric_id(db):
    iid, out = _apply(db, metric="ctr")
    assert out["applied"] == 1
    item = store.get_item(db, iid)
    assert item["metric_code"] == "ctr" and item["direction"] is None
    match.fixed_pass(db)
    study = store.get_study(db, store.get_item(db, iid)["study_id"])
    assert study["metric_code"] == "ctr" and study["metric_id"] == store.get_metric(db, "ctr")["id"]
    assert study["metric"] == VERDICT["facts"]["metric"]                 # the free-text measure is untouched


def test_judge_contract_has_no_direction():
    assert "direction" not in {f.name for f in contracts.JUDGE} | {f.name for f in contracts.CLASSIFY}
    items = contracts.export_json_schema("judge")["properties"]["results"]["items"]
    assert "direction" not in items["properties"] and "direction" not in items["then"]["required"]
    # a leftover direction in an answer is ignored by the judge contract
    assert contracts.validate_result("judge", dict(VERDICT, id="i1", direction="positive")) == []
    assert contracts.validate_result("judge", dict(VERDICT, id="i1", metric=None)) == [
        "metric: required when pass is true"]


def test_old_code_item_matched_after_step11_maps_via_legacy(bare_db):
    db = bare_db
    store.seed_subtopics(db, ["Local"])
    iid = _seed_extracted(db)                              # seeds the test metrics too
    store.set_verdict(db, iid, True, "B", {"what_was_tested": "x", "findings": "y",
                                           "research_timeframe": "2026", "metric": "free"}, None,
                      subtopic_choice={"slug": "local"}, metric="traffic")      # an old fixed code
    match.fixed_pass(db)
    study = store.get_study(db, store.get_item(db, iid)["study_id"])
    assert study["metric_code"] == "traffic" and study["metric_id"] is None
    assert study["classified_version"] is None             # classify asks it again
    store.seed_metrics(db, TEST_METRICS, {"traffic": "Organic traffic"})
    assert store.study_metric(db, store.get_study(db, study["id"]))["slug"] == "organic-traffic"


def test_study_with_slug_and_subtopic_is_marked_classified(db):
    store.seed_subtopics(db, ["Local"])
    iid, _ = _apply(db, subtopic={"slug": "local"}, metric="ctr")
    match.fixed_pass(db)
    study = store.get_study(db, store.get_item(db, iid)["study_id"])
    assert study["classified_version"] == classify.PROMPT_VERSION
    iid2, _ = _apply(db, url="https://example.com/p2", subtopic={"slug": "local"}, metric="other",
                     facts=dict(VERDICT["facts"], match_keys={"key_numbers": ["99%"]}),
                     primary_url="https://example.com/elsewhere")
    match.fixed_pass(db)
    assert store.get_study(db, store.get_item(db, iid2)["study_id"])["classified_version"] == classify.PROMPT_VERSION


def test_no_reference_to_removed_vocabularies():
    from tests.conftest import PLUGIN_DIR
    found = []
    for path in (PLUGIN_DIR / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"knowledge\.(METRICS|DIRECTIONS)\b|^(METRICS|DIRECTIONS)\s*=", text, re.M):
            found.append(path.name)
    assert found == []
