"""Findings storage: write/read, the backfill queue, merges, metric merges, set_verdict."""
import json

from evidence_echo.core import store
from tests.test_judge import VERDICT, _seed_extracted
from tests.test_knowledge_questions import add, subtopiced  # noqa: F401  (fixture)

V = store.FINDINGS_VERSION


def finding(text="Clicks rose 4%", quote="Clicks rose 4% on forty pages.", checked=True, metric="clicks",
            measure="organic clicks"):
    return {"text": text, "quote": quote, "quote_checked": checked, "metric": metric, "measure": measure}


def item_of(db, sid) -> int:
    return db.execute("SELECT id FROM items WHERE study_id=? ORDER BY id", (sid,)).fetchone()[0]


def put(db, sid, n=1, version=V):
    return store.write_findings(db, sid, item_of(db, sid), [finding(f"f{i}") for i in range(n)], version)


def selected(db, version=V):
    return [s["id"] for s in store.studies_without_findings(db, version)]


def test_write_and_read_findings(subtopiced):
    sid = add(subtopiced)
    n = store.write_findings(subtopiced, sid, item_of(subtopiced, sid),
                             [finding(), finding("Other", "q two", False, metric="nope", measure=None)], V)
    assert n == 2
    rows = store.study_findings(subtopiced, sid)
    assert [r["position"] for r in rows] == [1, 2]
    assert [r["quote_checked"] for r in rows] == [1, 0]
    assert rows[0]["metric_slug"] == "clicks" and rows[0]["metric_name"] == "Clicks"
    assert rows[1]["metric_slug"] is None and rows[1]["metric_name"] is None and rows[1]["measure"] is None
    assert subtopiced.execute("SELECT metric_id FROM findings WHERE position=2").fetchone()[0] is None
    assert store.get_study(subtopiced, sid)["findings_version"] == "findings-1" == V
    assert store.findings_by_study(subtopiced)[sid] == rows


def test_write_findings_replaces_rows(subtopiced):
    sid = add(subtopiced)
    put(subtopiced, sid, 3)
    put(subtopiced, sid, 1)
    rows = store.study_findings(subtopiced, sid)
    assert [(r["position"], r["text"]) for r in rows] == [(1, "f0")]


def test_other_metric_has_no_name(subtopiced):
    sid = add(subtopiced)
    store.write_findings(subtopiced, sid, item_of(subtopiced, sid), [finding(metric="other")], V)
    row = store.study_findings(subtopiced, sid)[0]
    assert row["metric_slug"] == "other" and row["metric_name"] is None


def test_without_findings_selection(subtopiced):
    none, current, old, rejected, failed, old_failed = (add(subtopiced) for _ in range(6))
    merged, nopage = add(subtopiced), add(subtopiced)
    put(subtopiced, current)
    put(subtopiced, old, version="findings-0")
    for sid, value in ((rejected, "findings-1:rejected"), (failed, "findings-1:failed"),
                       (old_failed, "findings-0:failed")):
        subtopiced.execute("UPDATE studies SET findings_version=? WHERE id=?", (value, sid))
    store.merge_studies(subtopiced, none, merged)
    subtopiced.execute("UPDATE items SET state='extracted' WHERE study_id=?", (nopage,))
    subtopiced.commit()
    assert selected(subtopiced) == sorted([none, old, rejected], reverse=True)
    assert store.findings_progress(subtopiced, V) == {"done": 1, "pending": 3}


def test_rejected_twice_fails(subtopiced):
    sid = add(subtopiced)
    assert store.mark_findings_rejected(subtopiced, sid, V) is False
    assert sid in selected(subtopiced)
    assert store.mark_findings_rejected(subtopiced, sid, V) is True
    assert store.get_study(subtopiced, sid)["findings_version"] == "findings-1:failed"
    assert sid not in selected(subtopiced)


def test_mark_failed(subtopiced):
    sid = add(subtopiced)
    store.mark_findings_failed(subtopiced, sid, V)
    assert store.get_study(subtopiced, sid)["findings_version"] == "findings-1:failed"
    assert selected(subtopiced) == []


def test_clear_findings_all_and_one(subtopiced):
    a, b, c = add(subtopiced), add(subtopiced), add(subtopiced)
    put(subtopiced, a, 2)
    put(subtopiced, b, 1)
    store.mark_findings_failed(subtopiced, c, V)
    assert store.clear_findings(subtopiced, a) == {"studies": 1, "findings": 2}
    assert store.study_findings(subtopiced, a) == []
    assert store.get_study(subtopiced, a)["findings_version"] is None
    assert store.clear_findings(subtopiced, c) == {"studies": 1, "findings": 0}
    assert c in selected(subtopiced)                         # a cleared :failed study is queued again
    assert store.clear_findings(subtopiced) == {"studies": 3, "findings": 1}
    assert sorted(selected(subtopiced)) == [a, b, c]


def test_merge_keeps_survivor_findings(subtopiced):
    keep, other = add(subtopiced), add(subtopiced)
    put(subtopiced, keep, 2)
    put(subtopiced, other, 3)
    subtopiced.execute("UPDATE items SET first_study_id=study_id")
    subtopiced.commit()
    store.merge_studies(subtopiced, keep, other)
    assert len(store.study_findings(subtopiced, keep)) == 2
    assert store.study_findings(subtopiced, other) == []
    assert store.get_study(subtopiced, other)["findings_version"] is None
    store.unmerge_study(subtopiced, other)
    assert other in selected(subtopiced) and keep not in selected(subtopiced)


def test_metric_merge_moves_findings(subtopiced):
    db = subtopiced
    sid = add(db)
    store.write_findings(db, sid, item_of(db, sid), [finding(metric="ctr")], V)
    assert store.merge_metrics(db, "ctr", "clicks")["moved_findings"] == 1
    assert store.study_findings(db, sid)[0]["metric_slug"] == "clicks"
    store.set_study_metric(db, sid, "impressions")
    assert store.study_findings(db, sid)[0]["metric_slug"] == "clicks"


def test_set_verdict_stores_and_clears_findings(db):
    iid = _seed_extracted(db, url="https://example.com/f1", text="x")
    listed = [finding()]
    store.set_verdict(db, iid, True, "A", VERDICT["facts"], None, findings=listed)
    assert store.item_findings(store.get_item(db, iid)) == listed
    store.set_verdict(db, iid, False, None, {}, None, reason="no")
    assert store.item_findings(store.get_item(db, iid)) is None
    assert store.item_findings({"findings": None}) is None
    assert store.item_findings({"findings": json.dumps(listed)}) == listed
