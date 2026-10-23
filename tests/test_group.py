"""Questions: the group stage - selection, payload, apply checks, API mode, reset, CLI."""
import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, knowledge, store
from evidence_echo.stages import group
from tests.conftest import FakeBackend
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)


def results(*items):
    return json.dumps({"results": list(items)})


def new(sid, key, text="Does X raise Y?", measure="Y", direction="up", kind="difference",
        comparison="with X vs without X"):
    question = {"key": key, "text": text, "measure": measure, "kind": kind}
    if kind == "difference" and comparison is not None:
        question["comparison"] = comparison
    res = {"id": f"s{sid}", "new_question": question}
    if direction is not None:
        res["direction"] = direction
    return res


def old(sid, question, direction="up", kind=None):
    res = {"id": f"s{sid}", "question": question}
    if direction is not None:
        res["direction"] = direction
    if kind is not None:
        res["question_kind"] = ({"kind": kind, "comparison": "A vs B"} if kind == "difference"
                                else {"kind": kind})
    return res


def ids(selected):
    return [s["id"] for s in selected]


def test_selection_newest_first_capped_at_40(subtopiced, topic):
    made = [add(subtopiced, by=f"org{n}") for n in range(45)]
    data = json.loads(group.emit(subtopiced, topic))
    assert len(data["items"]) == 40 and data["remaining"] == 5
    assert data["items"][0]["id"] == f"s{made[-1]}" and data["stage"] == "group"
    assert data["items"][0]["subtopic"] == "title-tags-meta"
    assert {"name", "what_was_tested", "findings", "metric", "baseline", "sample_scale",
            "research_timeframe"} <= set(data["items"][0])
    assert "fixed_question" not in data["items"][0]
    assert "untrusted" in data["instructions"]


def test_selection_skips_unsorted_and_pinned(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, subtopic=None, by="U")
    graded = add(subtopiced, question=qid, by="A")
    moved = add(subtopiced, question=qid, by="B")
    store.move_study(subtopiced, moved, qid)           # pinned: direction cleared, asked for direction only
    waiting = {s["id"]: s for s in store.studies_to_group(subtopiced)}
    assert list(waiting) == [moved] and waiting[moved]["fixed_question"] == qid
    assert graded not in waiting


def test_prompt_version_matches_store():
    assert group.PROMPT_VERSION == store.GROUP_VERSION == "group-2"


def test_regroup_selects_group1_studies_once(subtopiced):
    qid = q(subtopiced)
    sid = add(subtopiced, question=qid, by="A")
    subtopiced.execute("UPDATE studies SET grouped_version='group-1' WHERE id=?", (sid,))
    subtopiced.commit()
    waiting = store.studies_to_group(subtopiced)
    assert ids(waiting) == [sid] and waiting[0]["fixed_question"] is None
    out = group.apply(subtopiced, results(old(sid, f"q{qid}")))
    assert out["applied"] == 1 and out["remaining"] == 0
    assert store.studies_to_group(subtopiced) == []


def test_regroup_terminates_without_directions_for_level_questions(subtopiced):
    level = q(subtopiced, text="How much?", kind="level")
    cap = q(subtopiced, text="Can it?", kind="capability")
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    out = group.apply(subtopiced, results(old(a, f"q{level}", None), old(b, f"q{cap}", None)))
    assert out["applied"] == 2 and out["remaining"] == 0
    assert store.studies_to_group(subtopiced) == []
    c = add(subtopiced, by="C")                       # a pinned study under a level question is complete
    store.move_study(subtopiced, c, level)
    assert store.studies_to_group(subtopiced) == []


def test_emit_lists_topic_questions_once(subtopiced, topic):
    here = q(subtopiced, text="here?", measure="m")
    there = q(subtopiced, subtopic="local", text="elsewhere?", kind="level")
    q(subtopiced, subtopic="backlinks", text="empty?")
    add(subtopiced, question=here, by="A")
    add(subtopiced, subtopic="local", question=there, by="C", direction=None)
    add(subtopiced, by="B")
    data = json.loads(group.emit(subtopiced, topic))
    assert data["questions"] == [
        {"id": f"q{here}", "text": "here?", "measure": "m", "kind": "difference",
         "comparison": "with X vs without X", "subtopic": "title-tags-meta", "examples": ["study by A"]},
        {"id": f"q{there}", "text": "elsewhere?", "measure": "Y", "kind": "level", "comparison": None,
         "subtopic": "local", "examples": ["study by C"]}]


def test_cross_subtopic_join_moves_study(subtopiced):
    elsewhere = q(subtopiced, subtopic="local")
    a = add(subtopiced, by="A")
    out = group.apply(subtopiced, results(old(a, f"q{elsewhere}")))
    assert out["applied"] == 1 and out["rejected"] == []
    got = store.get_study(subtopiced, a)
    assert got["question_id"] == elsewhere
    assert got["subtopic_id"] == store.get_subtopic(subtopiced, "local")["id"]


def test_direction_refused_on_level_question(subtopiced):
    level = q(subtopiced, kind="level")
    a = add(subtopiced, by="A")
    out = group.apply(subtopiced, results(old(a, f"q{level}", "up")))
    assert out["applied"] == 0 and "leave it out" in out["rejected"][0]["errors"][0]


def test_direction_required_on_difference_question(subtopiced):
    diff = q(subtopiced)
    a = add(subtopiced, by="A")
    out = group.apply(subtopiced, results(old(a, f"q{diff}", None)))
    assert out["applied"] == 0 and "direction" in out["rejected"][0]["errors"][0]


def test_new_difference_question_needs_comparison(subtopiced):
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    out = group.apply(subtopiced, results(new(a, "n1", comparison=None), new(b, "n2", comparison="x" * 121)))
    assert out["applied"] == 0 and len(out["rejected"]) == 2
    assert all("comparison" in r["errors"][0] for r in out["rejected"])
    again = group.apply(subtopiced, results(new(a, "n1", kind="level", direction=None)))
    assert again["applied"] == 1 and store.list_questions(subtopiced)[0]["kind"] == "level"


def test_new_level_question_with_direction_refused(subtopiced):
    a = add(subtopiced, by="A")
    out = group.apply(subtopiced, results(new(a, "n1", kind="level", direction="up")))
    assert out["applied"] == 0 and "direction" in out["rejected"][0]["errors"][0]


def test_question_kind_fills_null_kind_once(subtopiced):
    legacy = q(subtopiced, kind=None)
    a = add(subtopiced, by="A")
    out = group.apply(subtopiced, results(old(a, f"q{legacy}", "up")))
    assert out["applied"] == 0 and "no kind yet" in out["rejected"][0]["errors"][0]
    out = group.apply(subtopiced, results(old(a, f"q{legacy}", "up", kind="difference")))
    assert out["applied"] == 1
    got = store.get_question(subtopiced, legacy)
    assert (got["kind"], got["comparison"]) == ("difference", "A vs B")


def test_conflicting_question_kind_in_one_file(subtopiced):
    legacy = q(subtopiced, kind=None)
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    out = group.apply(subtopiced, results(old(a, f"q{legacy}", "up", kind="difference"),
                                          old(b, f"q{legacy}", None, kind="level")))
    assert out["applied"] == 1
    assert out["rejected"][0]["id"] == f"s{b}"
    assert f"question q{legacy} was just set to difference" in out["rejected"][0]["errors"][0]
    assert store.get_question(subtopiced, legacy)["kind"] == "difference"


def test_question_kind_refused_when_kind_known(subtopiced):
    known = q(subtopiced)                                          # a difference question
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    out = group.apply(subtopiced, results(old(a, f"q{known}", None, kind="level")))
    assert out["applied"] == 0 and "already has kind" in out["rejected"][0]["errors"][0]
    out = group.apply(subtopiced, results(old(b, f"q{known}", "up", kind="difference")))
    assert out["applied"] == 1                                     # the same kind repeated is accepted


def test_pinned_study_fills_kind_and_stays(subtopiced):
    legacy = q(subtopiced, kind=None)
    other = q(subtopiced, text="other")
    sid = add(subtopiced, question=legacy, by="A", direction=None)
    store.move_study(subtopiced, sid, legacy)
    waiting = store.studies_to_group(subtopiced)
    assert ids(waiting) == [sid] and waiting[0]["fixed_question"] == legacy
    out = group.apply(subtopiced, results(old(sid, f"q{other}", "up", kind="level")))
    assert out["applied"] == 0                                    # keeps its question
    out = group.apply(subtopiced, results(old(sid, f"q{legacy}", "up", kind="difference")))
    assert out["applied"] == 1 and out["remaining"] == 0
    got = store.get_study(subtopiced, sid)
    assert (got["question_id"], got["question_direction"], got["question_by_owner"]) == (legacy, "up", 1)
    assert store.get_question(subtopiced, legacy)["kind"] == "difference"
    assert store.studies_to_group(subtopiced) == []


def test_owner_touched_question_keeps_text_and_kind(subtopiced):
    qid = q(subtopiced, text="Original?")
    store.rename_question(subtopiced, qid, "Owner wording?")
    sid = add(subtopiced, question=qid, by="A")
    subtopiced.execute("UPDATE studies SET grouped_version='group-1' WHERE id=?", (sid,))
    subtopiced.commit()
    assert store.studies_to_group(subtopiced) == []                # pinned, kind and direction known
    joiner = add(subtopiced, by="B")
    out = group.apply(subtopiced, results(old(joiner, f"q{qid}", "down")))
    assert out["applied"] == 1
    got = store.get_question(subtopiced, qid)
    assert (got["text"], got["kind"], got["comparison"]) == ("Owner wording?", "difference", "with X vs without X")


def test_apply_existing_question_and_new_question_by_key(subtopiced):
    existing = q(subtopiced, text="old one")
    a, b, c = (add(subtopiced, by=n) for n in "ABC")
    out = group.apply(subtopiced, results(new(a, "n1"), old(b, "n1", "down"), old(c, f"q{existing}", "mixed")))
    assert (out["applied"], out["created"], out["rejected"], out["remaining"]) == (3, 1, [], 0)
    sa, sb, sc = (store.get_study(subtopiced, s) for s in (a, b, c))
    assert sa["question_id"] == sb["question_id"] != sc["question_id"] == existing
    assert (sa["question_direction"], sb["question_direction"], sc["question_direction"]) == ("up", "down", "mixed")
    assert sa["grouped_version"] == "group-2" and sa["question_by_owner"] == 0
    groups = knowledge.evidence(subtopiced)[0].questions
    assert [(g.independent, g.label) for g in groups] == [(2, "contested")]


def test_key_used_before_its_definition_line_works(subtopiced):
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    out = group.apply(subtopiced, results(old(b, "n1", "down"), new(a, "n1")))
    assert (out["applied"], out["created"], out["rejected"]) == (2, 1, [])


def test_key_defined_twice_rejects_both(subtopiced):
    a, b, c = (add(subtopiced, by=n) for n in "ABC")
    out = group.apply(subtopiced, results(new(a, "n1"), new(b, "n1"), old(c, "n1")))
    assert out["applied"] == 0 and out["created"] == 0
    assert sorted(r["id"] for r in out["rejected"]) == [f"s{a}", f"s{b}", f"s{c}"]
    assert store.list_questions(subtopiced) == [] and len(store.studies_to_group(subtopiced)) == 3


def test_key_referenced_but_never_defined_is_rejected(subtopiced):
    a = add(subtopiced, by="A")
    out = group.apply(subtopiced, results(old(a, "n7")))
    assert out["applied"] == 0 and out["rejected"][0]["id"] == f"s{a}"
    assert "n7" in out["rejected"][0]["errors"][0]
    assert ids(store.studies_to_group(subtopiced)) == [a]


def test_key_defined_for_another_subtopic_is_rejected(subtopiced):
    a = add(subtopiced, by="A")
    other = add(subtopiced, subtopic="local", by="B")      # a key defined for another subtopic
    out = group.apply(subtopiced, results(new(other, "n1"), old(a, "n1")))
    assert out["applied"] == 1 and out["rejected"][0]["id"] == f"s{a}"


def test_inactive_subtopic_question_is_refused(subtopiced):
    elsewhere = q(subtopiced, subtopic="local")
    subtopiced.execute("UPDATE subtopics SET status='rejected' WHERE slug='local'")
    subtopiced.commit()
    a = add(subtopiced, by="A")
    out = group.apply(subtopiced, results(old(a, f"q{elsewhere}")))
    assert out["applied"] == 0 and "inactive" in out["rejected"][0]["errors"][0]


def test_merged_question_resolves_to_target(subtopiced):
    gone, target = q(subtopiced, text="gone"), q(subtopiced, text="target")
    store.merge_questions(subtopiced, gone, target)
    a = add(subtopiced, by="A")
    assert group.apply(subtopiced, results(old(a, f"q{gone}")))["applied"] == 1
    assert store.get_study(subtopiced, a)["question_id"] == target


def test_fixed_question_must_be_answered_with_it(subtopiced):
    fixed, other = q(subtopiced, text="fixed"), q(subtopiced, text="other")
    sid = add(subtopiced, question=fixed, by="A")
    store.move_study(subtopiced, sid, fixed)
    out = group.apply(subtopiced, results(old(sid, f"q{other}")))
    assert out["applied"] == 0 and "keeps" in out["rejected"][0]["errors"][0]
    assert ids(store.studies_to_group(subtopiced)) == [sid]
    out = group.apply(subtopiced, results(old(sid, f"q{fixed}", "down")))
    assert out["applied"] == 1 and out["remaining"] == 0
    got = store.get_study(subtopiced, sid)
    assert (got["question_id"], got["question_direction"], got["question_by_owner"]) == (fixed, "down", 1)
    data = json.loads(group.emit(subtopiced, type("T", (), dict(id="t", name="t", domain="d", scope="s",
                                                              subtopics=[]))()))
    assert data["items"] == []


def test_text_and_measure_limits_reject_the_item(subtopiced):
    a, b, c = (add(subtopiced, by=n) for n in "ABC")
    out = group.apply(subtopiced, results(new(a, "n1", text="x" * 121), new(b, "n2", measure="m" * 81),
                                          new(c, "n3", text="  ")))
    assert out["applied"] == 0 and len(out["rejected"]) == 3
    assert store.list_questions(subtopiced) == []


def test_rejected_item_stays_selected(subtopiced):
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    out = group.apply(subtopiced, results(new(a, "n1"), old(b, "q999"), {"id": "s999", "question": "q1",
                                                                     "direction": "up"},
                                          {"id": f"s{b}", "question": "n1", "direction": "sideways"}))
    assert out["applied"] == 1 and ids(store.studies_to_group(subtopiced)) == [b]
    assert out["remaining"] == 1 and len(out["rejected"]) == 3


def test_neither_or_both_question_fields_are_rejected(subtopiced):
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    both = {**new(a, "n1"), "question": "q1"}
    out = group.apply(subtopiced, results(both, {"id": f"s{b}", "direction": "up"}))
    assert out["applied"] == 0 and len(out["rejected"]) == 2


def test_run_api_one_call_per_study_and_new_questions_seen_by_later_studies(subtopiced, topic):
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    backend = FakeBackend([
        json.dumps({"new_question": {"text": "Does X raise Y?", "measure": "Y", "kind": "difference",
                                     "comparison": "with X vs without X"}, "direction": "up"}),
        json.dumps({"question": "q1", "direction": "down"})])
    out = group.run_api(subtopiced, backend, topic)
    assert (out["processed"], out["created"], out["rejected"], out["remaining"]) == (2, 1, [], 0)
    assert len(backend.prompts) == 2
    assert "Does X raise Y?" not in backend.prompts[0] and "Does X raise Y?" in backend.prompts[1]
    assert store.get_study(subtopiced, a)["question_id"] == store.get_study(subtopiced, b)["question_id"]


def test_api_mode_same_rules(subtopiced, topic):
    level = q(subtopiced, text="How much Z?", kind="level")
    legacy = q(subtopiced, text="Legacy?", kind=None)
    add(subtopiced, question=level, by="X", direction=None)
    y = add(subtopiced, question=legacy, by="Y", direction=None)
    subtopiced.execute("UPDATE studies SET grouped_version='group-2' WHERE id IN (1, ?)", (y,))
    subtopiced.commit()
    a, b, c = (add(subtopiced, by=n) for n in "ABC")
    backend = FakeBackend([
        json.dumps({"question": f"q{level}", "direction": "up"}),                    # c: refused, level has none
        json.dumps({"question": f"q{legacy}", "direction": "up"}),                   # b: refused, no kind given
        json.dumps({"question": f"q{legacy}", "question_kind": {"kind": "level"}}),  # a: sets the kind
        json.dumps({"question": f"q{legacy}"})])                                     # y: kind known now
    out = group.run_api(subtopiced, backend, topic)
    assert out["processed"] == 2 and len(out["rejected"]) == 2 and out["remaining"] == 2
    assert store.get_question(subtopiced, legacy)["kind"] == "level"
    assert f"q{legacy} [none yet]" in backend.prompts[0] and f"q{level} [level]" in backend.prompts[0]
    assert f"q{legacy} [level]" in backend.prompts[3]
    assert ids(store.studies_to_group(subtopiced)) == [c, b] and store.get_study(subtopiced, y)["grouped_version"] == "group-2"


def test_run_api_rejection_keeps_the_study_waiting(subtopiced, topic):
    a = add(subtopiced, by="A")
    out = group.run_api(subtopiced, FakeBackend([json.dumps({"question": "q9", "direction": "up"})]), topic)
    assert out["processed"] == 0 and out["remaining"] == 1 and out["rejected"][0]["id"] == f"s{a}"


def test_reset_stage_group_clears_unpinned(workspace):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    qid = q(conn, subtopic=slug)
    plain = add(conn, subtopic=slug, question=qid, by="A")
    pinned = add(conn, subtopic=slug, question=qid, by="B")
    store.move_study(conn, pinned, qid)
    conn.close()
    assert api.reset(cfg, "group") == {"reset": "group", "studies": 1}
    conn = store.connect(cfg.db_path)
    assert store.get_study(conn, plain)["question_id"] is None
    assert store.get_study(conn, pinned)["question_id"] == qid
    conn.close()


def _run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_cli_group_emit_and_apply(workspace, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_JUDGMENT_BACKEND", "session")
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    sid = add(conn, subtopic=slug, by="A")
    conn.close()
    code, out = _run(capsys, "group")
    assert code == 0 and out["items"][0]["id"] == f"s{sid}" and out["questions"] == []
    f = tmp_path / "g.json"
    f.write_text(results(new(sid, "n1")), encoding="utf-8")
    code, out = _run(capsys, "apply", "--stage", "group", str(f))
    assert code == 0 and (out["applied"], out["created"], out["remaining"]) == (1, 1, 0)
    assert _run(capsys, "group")[1]["items"] == []
    assert _run(capsys, "reset", "--stage", "group")[1] == {"reset": "group", "studies": 1}
    assert [i["id"] for i in _run(capsys, "group")[1]["items"]] == [f"s{sid}"]


def test_group_is_a_stage_and_a_reset_target():
    assert "group" in api.LLM_STAGES and "group" not in api.TWO_ANSWER_STAGES
    assert "group" in api.RESET_STAGES
