"""The judge extracts 1-4 findings per study; match stores them on study creation only."""
import json

from evidence_echo import api
from evidence_echo.core import contracts, findings, interfaces, store
from evidence_echo.stages import judge, match, regrade
from tests.conftest import FakeBackend
from tests.test_judge import SENTENCE, TEXT, VERDICT, _seed_extracted
from tests.test_judge_subtopics import _apply


def f(text="Clicks rose 4.2%", quote=SENTENCE, metric="clicks", measure="organic clicks"):
    return {"text": text, "quote": quote, "metric": metric, "measure": measure}


def test_judge_contract_requires_finding_list_when_pass(db):
    bare = {k: v for k, v in VERDICT.items() if k != "finding_list"}
    iid, out = _apply(db, bare)
    assert out["applied"] == 0 and "finding_list" in out["rejected"][0]["errors"][0]
    assert store.get_item(db, iid)["state"] == "extracted"
    for bad in ([], [f()] * 5):
        iid, out = _apply(db, VERDICT, url=f"https://example.com/n{len(bad)}", finding_list=bad)
        assert out["applied"] == 0 and "finding_list" in out["rejected"][0]["errors"][0]
    iid, out = _apply(db, {"pass": False, "reason": "Opinion piece."}, url="https://example.com/fail")
    assert out["applied"] == 1


def test_judge_contract_finding_shape(db):
    cases = [dict(f(), extra=1), f(text=""), f(text="x" * 161), f(text="two\nlines"), f(quote=7),
             {k: v for k, v in f().items() if k != "measure"}]
    for n, bad in enumerate(cases):
        _, out = _apply(db, VERDICT, url=f"https://example.com/s{n}", finding_list=[bad])
        assert out["applied"] == 0 and "finding_list" in out["rejected"][0]["errors"][0], n
    _, out = _apply(db, VERDICT, url="https://example.com/ok", finding_list=[f(text="y" * 160)])
    assert out["applied"] == 1


def test_judge_rejects_unknown_metric_slug_in_findings(db):
    iid, out = _apply(db, VERDICT, finding_list=[f(metric="nonsense")])
    assert out["applied"] == 0 and "finding_list[0].metric" in out["rejected"][0]["errors"][0]
    assert store.get_item(db, iid)["state"] == "extracted"


def test_findings_stored_on_item_with_quote_check(db):
    iid, out = _apply(db, VERDICT, finding_list=[f(), f(text="Invented", quote="This sentence is not in the article")])
    assert out["applied"] == 1
    stored = store.item_findings(store.get_item(db, iid))
    assert [x["quote_checked"] for x in stored] == [True, False]
    assert stored[0]["metric"] == "clicks" and stored[0]["text"] == "Clicks rose 4.2%"


def _judged(db, n, listed, primary="https://orig.example.com/same"):
    iid = _seed_extracted(db, url=f"https://example.com/m{n}", text=TEXT)
    out = judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid), primary_url=primary,
                                                       finding_list=listed)]}))
    assert out["applied"] == 1
    return iid


def test_match_writes_findings_on_creation_only(db):
    a = _judged(db, 1, [f(), f(text="Second")])
    b = _judged(db, 2, [f(text="B1"), f(text="B2"), f(text="B3")])
    match.fixed_pass(db)
    sid = store.get_item(db, a)["study_id"]
    assert store.get_item(db, b)["study_id"] == sid
    rows = store.study_findings(db, sid)
    assert [r["text"] for r in rows] == ["Clicks rose 4.2%", "Second"]
    assert store.get_study(db, sid)["findings_version"] == "findings-1"
    assert {r["item_id"] for r in rows} == {a}


def test_reset_judge_clears_item_findings(workspace, topic):
    from contextlib import closing
    from evidence_echo.core import config
    cfg = config.load("seo")
    with closing(store.connect(cfg.db_path)) as conn:
        iid, _ = _apply(conn, VERDICT)
        assert store.item_findings(store.get_item(conn, iid)) is not None
    api.reset(cfg, "judge")
    with closing(store.connect(cfg.db_path)) as conn:
        assert store.get_item(conn, iid)["findings"] is None


def test_judge_prompt_names_finding_list(db, topic):
    assert "finding_list" in judge.PROMPT and "{findings_rules}" in judge.PROMPT
    text = judge.instructions(topic, store.active_subtopics(db), store.active_metrics(db))
    assert "finding_list" in text and findings.RULES in text
    _seed_extracted(db)
    backend = FakeBackend([json.dumps(dict(VERDICT))])
    judge.run_api(db, backend, topic)
    assert findings.RULES in backend.prompts[0] and "finding_list" in backend.prompts[0]


def test_judge_prompt_change_triggers_no_regrade(db, topic):
    iid, out = _apply(db, VERDICT)
    assert out["applied"] == 1 and store.get_item(db, iid)["judge_version"] == judge.PROMPT_VERSION
    db.execute("UPDATE items SET judge_version='judge-2' WHERE id=?", (iid,))
    db.commit()
    assert json.loads(regrade.emit(db, topic))["items"] == []


def test_findings_do_not_change_grade(db):
    a, _ = _apply(db, VERDICT, url="https://example.com/g1")
    b, _ = _apply(db, VERDICT, url="https://example.com/g2", finding_list=[f(), f(text="More"), f(text="Third")])
    ia, ib = store.get_item(db, a), store.get_item(db, b)
    for key in ("evidence_tier", "judge_tier", "capped_from", "quote_check", "graded_rules", "caps_hash"):
        assert ia[key] == ib[key], key


def test_shape_errors_helpers():
    assert findings.shape_errors([f()], 1) == [] and findings.shape_errors([], 0) == []
    assert findings.shape_errors("x", 0) and findings.shape_errors([f()] * 5, 0)
    assert contracts.validate_result("judge", dict(VERDICT, id="i1")) == []
