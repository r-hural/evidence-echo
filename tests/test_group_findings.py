"""Findings mode: the group stage files findings (not studies) under findings-mode questions."""
import json
from dataclasses import replace

from evidence_echo import api
from evidence_echo.core import config, store
from evidence_echo.stages import group
from tests.conftest import FakeBackend
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)

F = "findings"
_count = [0]


def with_findings(db, n=2, subtopic="title-tags-meta"):
    _count[0] += 1
    sid = add(db, subtopic=subtopic, by=f"org{_count[0]}")
    item = db.execute("SELECT id FROM items WHERE study_id=?", (sid,)).fetchone()[0]
    store.write_findings(db, sid, item, [{"text": f"finding {i} of {sid}", "quote": "q", "quote_checked": True,
                                          "measure": f"m{i}"} for i in range(n)], store.FINDINGS_VERSION)
    return sid, [f["id"] for f in store.study_findings(db, sid)]


def fq(db, text="Does X raise Y?", kind="difference", subtopic="title-tags-meta"):
    return store.create_question(db, store.get_subtopic(db, subtopic)["id"], text, "Y", kind,
                                 "with X vs without X" if kind == "difference" else None, mode="findings")


def results(*items):
    return json.dumps({"results": list(items)})


def old(fid, question, direction="up"):
    res = {"id": f"f{fid}", "question": question}
    if direction is not None:
        res["direction"] = direction
    return res


def new(fid, key, direction="up", text="Does X raise Y?"):
    return {"id": f"f{fid}", "new_question": {"key": key, "text": text, "measure": "Y", "kind": "difference",
                                              "comparison": "with X vs without X"}, "direction": direction}


def test_version_constant():
    assert group.FINDINGS_PROMPT_VERSION == store.FINDINGS_GROUP_VERSION == "group-f1"


def test_findings_emit_items_are_findings(subtopiced, topic):
    store.create_question(subtopiced, 1, "Old mode?", "M", "level")
    made = [with_findings(subtopiced, 2) for _ in range(25)]
    data = json.loads(group.emit(subtopiced, topic, F))
    assert len(data["items"]) == 40 and data["remaining"] == 10
    first = data["items"][0]
    assert first["id"] == f"f{made[-1][1][0]}" and first["study"] == f"s{made[-1][0]}"
    assert first["finding"] == f"finding 0 of {made[-1][0]}" and first["measure"] == "m0"
    assert {"subtopic", "metric", "name", "what_was_tested", "sample_scale", "research_timeframe"} <= set(first)
    assert data["questions"] == []                      # the questions-mode question is not offered
    assert "never join the same question" in data["instructions"]


def test_findings_apply_files_findings_not_studies(subtopiced, topic):
    sid, (f1, f2) = with_findings(subtopiced)
    out = group.apply(subtopiced, results(new(f1, "a"), new(f2, "b", "down", "Does W raise V?")), F)
    assert (out["applied"], out["created"], out["rejected"], out["remaining"]) == (2, 2, [], 0)
    assert store.get_study(subtopiced, sid)["question_id"] is None
    rows = store.study_findings(subtopiced, sid)
    assert all(r["grouped_version"] == "group-f1" and r["question_id"] for r in rows)
    assert rows[0]["question_id"] != rows[1]["question_id"] and rows[0]["direction"] == "up"
    assert {store.get_question(subtopiced, r["question_id"])["mode"] for r in rows} == {"findings"}


def test_two_findings_of_one_study_never_share_a_question(subtopiced, topic):
    sid, (f1, f2) = with_findings(subtopiced)
    other, (g1, _) = with_findings(subtopiced)
    qid = fq(subtopiced)
    out = group.apply(subtopiced, results(old(f1, f"q{qid}"), old(f2, f"q{qid}")), F)
    assert out["applied"] == 1 and [r["id"] for r in out["rejected"]] == [f"f{f2}"]
    assert store.get_finding(subtopiced, f2)["question_id"] is None
    sid3, (h1, h2) = with_findings(subtopiced)           # the same new key twice
    out = group.apply(subtopiced, results(new(h1, "z"), old(h2, "z")), F)
    assert out["applied"] == 1 and [r["id"] for r in out["rejected"]] == [f"f{h2}"]
    assert store.get_finding(subtopiced, h2)["question_id"] is None
    held = store.get_finding(subtopiced, h1)["question_id"]   # against a stored finding
    out = group.apply(subtopiced, results(old(h2, f"q{held}")), F)
    assert out["applied"] == 0 and "already has a finding" in out["rejected"][0]["errors"][0]


def test_pinned_finding_untouched(subtopiced, topic):
    sid, (f1, f2) = with_findings(subtopiced)
    qid, other = fq(subtopiced), fq(subtopiced, "Other?", "level")
    group.apply(subtopiced, results(old(f1, f"q{qid}"), old(f2, f"q{other}", None)), F)
    store.move_finding(subtopiced, f1, qid)             # pins f1, clears its direction
    subtopiced.execute("UPDATE findings SET grouped_version='group-f0'")
    subtopiced.commit()
    waiting = {f["id"]: f for f in store.findings_to_group(subtopiced)}
    assert waiting[f2]["fixed_question"] is None        # unpinned: asked again
    assert waiting[f1]["fixed_question"] == qid         # pinned: only the direction gap
    store.set_finding_direction(subtopiced, f1, "up", "group-f1")
    assert f1 not in {f["id"] for f in store.findings_to_group(subtopiced)}


def test_findings_regroup_after_version_change(subtopiced, topic):
    sid, (f1, f2) = with_findings(subtopiced)
    group.apply(subtopiced, results(new(f1, "a"), new(f2, "b", text="Does W raise V?")), F)
    assert store.findings_to_group(subtopiced) == []
    subtopiced.execute("UPDATE findings SET grouped_version='group-f0'")
    subtopiced.commit()
    assert [f["id"] for f in store.findings_to_group(subtopiced)] == [f1, f2]


def test_reset_group_in_findings_mode_clears_unpinned_findings_only(workspace):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    sid, (f1, f2) = with_findings(conn, subtopic=slug)
    qid = fq(conn, subtopic=slug)
    other = fq(conn, "Other?", "level", subtopic=slug)
    studyq = store.create_question(conn, store.get_subtopic(conn, slug)["id"], "S?", "M", "level")
    store.set_study_question(conn, sid, studyq, None, store.GROUP_VERSION)
    store.set_finding_question(conn, f1, qid, "up", "group-f1")
    store.set_finding_question(conn, f2, other, None, "group-f1")
    store.move_finding(conn, f1, qid)
    conn.close()
    out = api.reset(replace(cfg, grouping_mode="findings"), "group")
    assert out == {"reset": "group", "findings": 1}
    conn = store.connect(cfg.db_path)
    assert store.get_finding(conn, f1)["question_id"] == qid and store.get_finding(conn, f2)["question_id"] is None
    assert store.get_study(conn, sid)["question_id"] == studyq


def test_run_api_findings_mode(subtopiced, topic):
    sid, (f1, f2) = with_findings(subtopiced)
    backend = FakeBackend([
        json.dumps({"new_question": {"text": "Does X raise Y?", "measure": "Y", "kind": "difference",
                                     "comparison": "with X vs without X"}, "direction": "up"}),
        json.dumps({"question": "q1", "direction": "down"})])
    out = group.run_api(subtopiced, backend, topic, F)
    assert out["processed"] == 1 and out["created"] == 1 and out["remaining"] == 1
    assert len(out["rejected"]) == 1 and out["rejected"][0]["id"] == f"f{f2}"
    assert f"finding 0 of {sid}" in backend.prompts[0] and "never join the same question" in backend.prompts[0]
    assert "Does X raise Y?" in backend.prompts[1]


def test_questions_mode_group_unchanged(subtopiced, topic):
    a = add(subtopiced, by="A")
    with_findings(subtopiced)
    qid = q(subtopiced)
    store.create_question(subtopiced, 1, "Findings question?", "M", "level", mode="findings")
    assert group.emit(subtopiced, topic) == group.emit(subtopiced, topic, "questions")
    data = json.loads(group.emit(subtopiced, topic))
    assert all(i["id"].startswith("s") for i in data["items"]) and [x["id"] for x in data["questions"]] == []
    out = group.apply(subtopiced, results({"id": f"s{a}", "question": f"q{qid}", "direction": "up"}), "questions")
    assert out["applied"] == 1 and store.get_study(subtopiced, a)["question_id"] == qid
