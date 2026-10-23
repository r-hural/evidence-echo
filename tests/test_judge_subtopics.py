"""SP3b-T: the judge's subtopic and metric - payload, validation, study fields."""
import json

import pytest

from evidence_echo import api
from evidence_echo.core import config, interfaces, store
from evidence_echo.stages import judge, match
from tests.conftest import FakeBackend
from tests.test_judge import CLASSIFY, VERDICT, _seed_extracted

PLAIN = {k: v for k, v in VERDICT.items() if k not in CLASSIFY}   # no subtopic; the metric is required
PLAIN = {**PLAIN, "metric": "other"}


def _apply(db, verdict=VERDICT, url="https://example.com/p1", **over):
    iid = _seed_extracted(db, url=url)
    out = judge.apply(db, json.dumps({"results": [dict(verdict, id=interfaces.opaque_id(iid), **over)]}))
    return iid, out


def test_emit_lists_active_subtopics_and_metrics(db, topic):
    store.seed_subtopics(db, topic.subtopics)
    store.subtopic_for_choice(db, {"propose": "Image SEO"}, create=True)    # pending: not offered
    _seed_extracted(db)
    payload = json.loads(judge.emit(db, topic))
    assert payload["subtopics"][0] == {"slug": "title-tags-meta", "name": "Title tags & meta"}
    assert len(payload["subtopics"]) == 15 and "image-seo" not in json.dumps(payload["subtopics"])
    assert "- title-tags-meta: Title tags & meta" in payload["instructions"]
    assert payload["metrics"][0] == {"slug": "clicks", "name": "Clicks"}
    assert payload["metrics"][-1] == {"slug": "other", "name": "Other"}
    assert "- clicks: Clicks" in payload["instructions"] and '"propose"' in payload["instructions"]


def test_no_subtopics_yet_asks_for_a_proposal(db, topic):
    assert "none listed yet - propose one" in judge.instructions(topic, [])


def test_slug_pick_is_stored_and_reaches_the_study(db, topic):
    store.seed_subtopics(db, topic.subtopics)
    iid, out = _apply(db, subtopic={"slug": "local"}, metric="rankings")
    assert out == {"applied": 1, "rejected": []}
    item = store.get_item(db, iid)
    assert json.loads(item["subtopic_choice"]) == {"slug": "local"}
    assert item["metric_code"] == "rankings" and item["direction"] is None
    match.fixed_pass(db)
    study = store.get_study(db, store.get_item(db, iid)["study_id"])
    assert study["subtopic_id"] == store.get_subtopic(db, "local")["id"]
    assert study["metric_code"] == "rankings" and study["direction"] is None
    assert study["metric_id"] == store.get_metric(db, "rankings")["id"]
    assert study["metric"] == VERDICT["facts"]["metric"]       # SP2's free-text measure is untouched


def test_proposal_creates_a_pending_subtopic(db):
    _apply(db, subtopic={"propose": "Image SEO"})
    assert store.get_subtopic(db, "image-seo")["status"] == "proposed"


@pytest.mark.parametrize("over,error", [
    ({"metric": "sessions"}, "metric sessions is not in the topic's list"),
    ({"subtopic": "local"}, "subtopic: expected object"),
    ({"subtopic": {"slug": "nope"}}, "subtopic: unknown slug 'nope'"),
    ({"subtopic": {"slug": "local", "propose": "Local"}}, "subtopic: must be"),
    ({"subtopic": {"propose": "   "}}, "subtopic: the slug or name must be a non-empty string"),
    ({"subtopic": {"propose": "!!!"}}, "subtopic: a proposed name needs"),
    ({"subtopic": {"propose": "x" * 81}}, "subtopic: a proposed name needs"),
])
def test_invalid_classification_rejects_the_item(db, topic, over, error):
    store.seed_subtopics(db, topic.subtopics)
    iid, out = _apply(db, **over)
    assert out["applied"] == 0 and error in out["rejected"][0]["errors"][0]
    assert store.get_item(db, iid)["state"] == "extracted"
    assert [t["slug"] for t in store.list_subtopics(db) if t["status"] == "proposed"] == []


def test_failing_verdict_ignores_the_subtopic(db):
    iid = _seed_extracted(db)
    out = judge.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(iid), "pass": False, "reason": "Opinion.", "subtopic": {"slug": "nope"}}]}))
    assert out["applied"] == 1 and store.get_item(db, iid)["subtopic_choice"] is None


def test_classification_is_optional(db):
    iid, out = _apply(db, verdict=PLAIN)
    item = store.get_item(db, iid)
    assert out["applied"] == 1 and item["subtopic_choice"] is None and item["metric_code"] == "other"


def test_joining_item_fills_nulls_only(db, topic):
    store.seed_subtopics(db, topic.subtopics)
    a, _ = _apply(db, verdict=PLAIN, url="https://example.com/a")
    match.fixed_pass(db)                                  # a creates the study, without a subtopic
    b = _seed_extracted(db, url="https://example.com/b")
    c = _seed_extracted(db, url="https://example.com/c")
    judge.apply(db, json.dumps({"results": [
        dict(VERDICT, id=interfaces.opaque_id(b), subtopic={"slug": "local"}, metric="ctr"),
        dict(VERDICT, id=interfaces.opaque_id(c), subtopic={"slug": "backlinks"}, metric="clicks")]}))
    match.fixed_pass(db)                                  # b, then c, join a's study (same primary url)
    study = store.get_study(db, store.get_item(db, a)["study_id"])
    assert store.get_item(db, c)["study_id"] == study["id"]
    assert study["subtopic_id"] == store.get_subtopic(db, "local")["id"]
    assert study["metric_code"] == "other"                  # a had them; joiners fill nulls only
    assert store.study_metric(db, study)["slug"] == "other"


def test_run_api_prompt_lists_subtopics_and_rejects_an_unknown_slug(db, topic):
    store.seed_subtopics(db, topic.subtopics)
    iid = _seed_extracted(db)
    backend = FakeBackend([json.dumps(dict(VERDICT, subtopic={"slug": "nope"}))])
    out = judge.run_api(db, backend, topic)
    assert out["processed"] == 0 and "unknown slug" in out["rejected"][0]["errors"][0]
    assert "- local: Local" in backend.prompts[0] and "- clicks: Clicks" in backend.prompts[0]
    assert store.get_item(db, iid)["state"] == "extracted"


def test_api_apply_seeds_the_topic_subtopics(workspace):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    iid = _seed_extracted(conn)
    conn.close()
    out = api.apply(cfg, "judge", json.dumps({"results": [
        dict(VERDICT, id=interfaces.opaque_id(iid), subtopic={"slug": "local"})]}))
    assert out == {"applied": 1, "rejected": []}
