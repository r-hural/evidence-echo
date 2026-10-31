"""Switching grouping mode round trips keep the owner's work (grouping modes part 2, task 9)."""
import json
import re

from evidence_echo.core import knowledge, store
from evidence_echo.render import html as html_render
from evidence_echo.render import model
from evidence_echo.stages import group
from tests.test_knowledge_findings import fq, put, study, subs  # noqa: F401  (fixture)
from tests.test_knowledge_questions import add, q
from tests.test_report_golden import NOW, frozen  # noqa: F401  (fixture)


def snapshot(db, sids):
    out = {}
    for sid in sids:
        s = store.get_study(db, sid)
        final = store.resolve_question(db, s["question_id"])
        out[sid] = (final["id"] if final else None, s["question_direction"], s["question_by_owner"],
                    s["subtopic_id"], s["grouped_version"])
    return out


def results(*items):
    return json.dumps({"results": list(items)})


def test_questions_findings_questions_keeps_owner_pins(subs):
    q1, q2, q3 = (q(subs, text=t, kind="level") for t in ("One?", "Two?", "Three?"))
    s1, s2, s3 = (add(subs, question=qid, direction=None, by=f"org{i}") for i, qid in enumerate((q1, q1, q3)))
    store.move_study(subs, s1, q2)                              # owner move (pins)
    store.rename_question(subs, q1, "One renamed?")             # owner rename (pins its studies)
    store.merge_questions(subs, q3, q2)                         # owner merge
    before = snapshot(subs, (s1, s2, s3))
    assert store.studies_to_group(subs) == []
    # switch to findings: backfill, then group the findings under findings-mode questions
    for sid in (s1, s2, s3):
        item = subs.execute("SELECT id FROM items WHERE study_id=?", (sid,)).fetchone()[0]
        store.write_findings(subs, sid, item, [{"text": f"f of {sid}", "quote": "x", "quote_checked": True}],
                             store.FINDINGS_VERSION)
    fids = [f["id"] for sid in (s1, s2, s3) for f in store.study_findings(subs, sid)]
    out = group.apply(subs, results(*({"id": f"f{fid}", "new_question": {
        "key": f"k{fid}", "text": f"Finding question {fid}?", "measure": "Y", "kind": "level"}} for fid in fids)),
        "findings")
    assert out["applied"] == 3 and store.findings_to_group(subs) == []
    assert snapshot(subs, (s1, s2, s3)) == before               # findings mode left studies alone
    # switch back: nothing to group, every pin and question as before
    assert store.studies_to_group(subs) == []
    assert snapshot(subs, (s1, s2, s3)) == before
    assert store.get_question(subs, q1)["text"] == "One renamed?"
    assert {e.key for e in knowledge.evidence(subs)} == {"title-tags-meta"}


def test_findings_questions_findings_keeps_finding_pins(subs):
    a, b = fq(subs, text="Qa?", kind="level"), fq(subs, text="Qb?", kind="level")
    sid, (f1, f2) = study(subs, n=2, by="A")
    put(subs, f1, a, None)
    put(subs, f2, b, None)
    store.move_finding(subs, f1, a)             # pin f1 (stays in a)
    other, (g1,) = study(subs, by="B")
    put(subs, g1, b, None)
    store.move_finding(subs, g1, a)                             # owner moves g1 from b to a
    store.rename_question(subs, b, "Qb renamed?")
    before = {f["id"]: (f["question_id"], f["direction"], f["by_owner"])
              for f in (*store.study_findings(subs, sid), *store.study_findings(subs, other))}
    # questions mode in between: a questions-mode question and study grouping
    qq = q(subs, kind="level")
    store.set_study_question(subs, sid, qq, None, store.GROUP_VERSION)
    assert store.study_findings(subs, sid)[0]["question_id"] == a       # findings questions stay stored
    assert {x["mode"] for x in store.list_questions(subs, "findings")} == {"findings"}
    after = {f["id"]: (f["question_id"], f["direction"], f["by_owner"])
             for f in (*store.study_findings(subs, sid), *store.study_findings(subs, other))}
    assert after == before
    assert store.get_question(subs, b)["text"] == "Qb renamed?"
    assert store.findings_to_group(subs) == []                          # pins hold; nothing asked again


def test_quotes_findings_same_data(subs):
    sid, (f1, f2) = study(subs, n=2, by="A")
    rows_quotes = [dict(f) for f in store.study_findings(subs, sid)]
    a = fq(subs, text="Qa?", kind="level")
    put(subs, f1, a, None)                                              # findings mode: group + relate
    rows_findings = [dict(f) for f in store.study_findings(subs, sid)]
    strip = ("question_id", "direction", "grouped_version", "question_by_owner")
    clean = lambda rows: [{k: v for k, v in r.items() if k not in strip} for r in rows]  # noqa: E731
    assert clean(rows_quotes) == clean(rows_findings)
    assert store.findings_progress(subs, store.FINDINGS_VERSION) == {"done": 1, "pending": 0}


def test_switch_shows_waiting_until_done(subs, topic, frozen):
    q1 = q(subs, kind="level")
    sids = [add(subs, question=q1, direction=None, by=f"org{i}") for i in range(2)]
    for sid in sids:
        item = subs.execute("SELECT id FROM items WHERE study_id=?", (sid,)).fetchone()[0]
        store.write_findings(subs, sid, item, [{"text": f"f{sid}", "quote": "x", "quote_checked": True}],
                             store.FINDINGS_VERSION)
    store.record_report(subs, "r.md", [(s, f"T{s}", f"S{s}") for s in sids])
    t = model.build_topic(subs, topic, NOW, "findings")
    out = html_render.topic_page(t, "overview.html", "report.html")
    assert "Waiting to be split (2)" in out
    block = out.split('class="ee-waiting-split"')[1]
    assert all(f'href="#s{s}"' in block for s in sids)
    assert re.search(r"Waiting to be split \(2\)", out)
