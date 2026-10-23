"""Wave 4a: the judge's headline and data_date - checked, normalised, carried to the study."""
import json

import pytest

from evidence_echo import api
from evidence_echo.core import config, interfaces, store
from evidence_echo.stages import judge, match
from tests.conftest import FakeBackend
from tests.test_judge import SENTENCE, VERDICT, _seed_extracted

HEADLINE = {"value": "+4.2%", "label": "organic clicks", "quote": SENTENCE}


def _apply(db, url="https://example.com/p1", **over):
    iid = _seed_extracted(db, url=url)
    out = judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid), **over)]}))
    return iid, out


def test_instructions_ask_for_headline_and_data_date(topic):
    text = judge.instructions(topic, [])
    for word in ('"headline"', '"data_date"', "YYYY-MM", "measures the metric", "required"):
        assert word in text, word


def test_headline_and_data_date_reach_the_study(db):
    iid, out = _apply(db, headline=HEADLINE, data_date="2026-4")
    assert out == {"applied": 1, "rejected": []}
    item = store.get_item(db, iid)
    assert json.loads(item["headline"]) == {"value": "+4.2%", "label": "organic clicks",
                                            "quote": SENTENCE, "checked": True}
    assert item["data_date"] == "2026-04"
    match.fixed_pass(db)
    study = store.get_study(db, store.get_item(db, iid)["study_id"])
    assert (study["headline_value"], study["headline_label"], study["headline_checked"]) == (
        "+4.2%", "organic clicks", 1)
    assert (study["headline_quote"], study["data_date"]) == (SENTENCE, "2026-04")


def test_failed_check_is_stored_unchecked(db):
    iid, _ = _apply(db, headline=dict(HEADLINE, value="-9%"))
    assert json.loads(store.get_item(db, iid)["headline"])["checked"] is False


def test_invalid_data_date_is_dropped_not_rejected(db):
    iid, out = _apply(db, data_date="spring 2026")
    assert out["applied"] == 1 and store.get_item(db, iid)["data_date"] is None


def test_joiner_fills_an_empty_or_unchecked_headline_only(db):
    a, _ = _apply(db, headline=dict(HEADLINE, value="-9%"))                # unchecked
    match.fixed_pass(db)
    sid = store.get_item(db, a)["study_id"]
    b, _ = _apply(db, url="https://example.com/p2", headline=HEADLINE, data_date="2025")
    store.assign_study(db, b, sid, "test")
    study = store.get_study(db, sid)
    assert (study["headline_checked"], study["headline_value"], study["data_date"]) == (1, "+4.2%", "2025")
    c, _ = _apply(db, url="https://example.com/p3", headline=dict(HEADLINE, label="other"), data_date="2020")
    store.assign_study(db, c, sid, "test")
    assert (store.get_study(db, sid)["headline_label"], store.get_study(db, sid)["data_date"]) == (
        "organic clicks", "2025")                                         # checked one stays


@pytest.mark.parametrize("over,error", [
    ({"metric": None}, "metric: required when pass is true"),
    ({"headline": {"value": "4.2%", "label": "x"}}, "headline: must have value, label and quote"),
    ({"headline": dict(HEADLINE, label="one two three four five six seven")}, "headline: label"),
    ({"headline": dict(HEADLINE, value="  ")}, "headline: value, label and quote must be non-empty"),
    ({"headline": "4.2%"}, "headline: expected object"),
])
def test_invalid_knowledge_fields_reject_the_item(db, over, error):
    iid, out = _apply(db, **over)
    assert out["applied"] == 0 and error in out["rejected"][0]["errors"][0]
    assert store.get_item(db, iid)["state"] == "extracted"


def test_metric_missing_from_a_passing_verdict_rejects(db):
    iid = _seed_extracted(db)
    res = {k: v for k, v in VERDICT.items() if k != "metric"}
    out = judge.apply(db, json.dumps({"results": [dict(res, id=interfaces.opaque_id(iid))]}))
    assert "metric: required when pass is true" in out["rejected"][0]["errors"][0]


def test_failing_verdict_needs_no_metric_and_clears_headline(db):
    iid = _seed_extracted(db)
    out = judge.apply(db, json.dumps({"results": [{"id": interfaces.opaque_id(iid), "pass": False, "reason": "Opinion."}]}))
    assert out["applied"] == 1
    item = store.get_item(db, iid)
    assert (item["headline"], item["data_date"]) == (None, None)


def test_api_mode_carries_the_headline(db, topic):
    iid = _seed_extracted(db)
    backend = FakeBackend([json.dumps(dict(VERDICT, headline=HEADLINE, data_date="2026"))])
    assert judge.run_api(db, backend, topic)["processed"] == 1
    assert store.get_item(db, iid)["data_date"] == "2026"


def test_reset_judge_clears_headline_and_data_date(workspace):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    iid = _seed_extracted(conn)
    judge.apply(conn, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid),
                                                   headline=HEADLINE, data_date="2026")]}))
    conn.close()
    api.reset(cfg, "judge")
    conn = store.connect(cfg.db_path)
    item = store.get_item(conn, iid)
    assert (item["state"], item["headline"], item["data_date"]) == ("extracted", None, None)
    conn.close()
