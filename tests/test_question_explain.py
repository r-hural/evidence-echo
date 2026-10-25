# tests/test_question_explain.py
"""Relate: the explain state of a question (main result, studies to explain, inputs hash)."""
from evidence_echo.core import knowledge
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)


def group_of(db, qid):
    return next(g for e in knowledge.evidence(db) for g in e.questions if g.question_id == qid)


def test_main_result_is_the_most_common_non_mixed_direction(subtopiced):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="A"); b = add(subtopiced, question=qid, by="B")
    c = add(subtopiced, question=qid, by="C", direction="mixed")
    d = add(subtopiced, question=qid, by="D", direction="no_change")
    st = knowledge.explain_state(subtopiced, group_of(subtopiced, qid))
    assert st.main_result == "up" and st.explain == [d, c] and set(st.studies) == {a, b, c, d}


def test_mixed_is_never_the_main_result(subtopiced):
    qid = q(subtopiced)
    m1 = add(subtopiced, question=qid, by="A", direction="mixed")
    m2 = add(subtopiced, question=qid, by="B", direction="mixed")
    add(subtopiced, question=qid, by="C")
    st = knowledge.explain_state(subtopiced, group_of(subtopiced, qid))
    assert st.main_result == "up" and st.explain == [m2, m1]


def test_main_result_tie_by_tier_then_newest(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A", tier="A"); add(subtopiced, question=qid, by="B", direction="down", tier="C")
    assert knowledge.explain_state(subtopiced, group_of(subtopiced, qid)).main_result == "up"   # tier beats newest
    qid2 = q(subtopiced, text="Does Z raise Y?")
    add(subtopiced, question=qid2, by="A", tier="B"); add(subtopiced, question=qid2, by="B", direction="down", tier="B")
    assert knowledge.explain_state(subtopiced, group_of(subtopiced, qid2)).main_result == "down"


def test_ten_groups_give_one_state_with_at_most_nine_lines(subtopiced):
    qid = q(subtopiced)
    for n in range(10):
        add(subtopiced, question=qid, by=f"O{n}", direction=("up", "down", "no_change")[n % 3])
    st = knowledge.explain_state(subtopiced, group_of(subtopiced, qid))
    assert len(st.studies) == 10 and len(st.explain) <= 9


def test_more_than_twelve_groups_are_capped(subtopiced):
    qid = q(subtopiced)
    ids = [add(subtopiced, question=qid, by=f"O{n}", direction="down" if n < 3 else "up") for n in range(16)]
    st = knowledge.explain_state(subtopiced, group_of(subtopiced, qid))
    assert set(ids[:3]) <= set(st.studies) and len(st.studies) == 15   # 12 newest + 3 older down ones


def test_no_state_for_consistent_level_or_unlabelled(subtopiced):
    qid = q(subtopiced); add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="B")
    assert knowledge.explain_state(subtopiced, group_of(subtopiced, qid)) is None


def test_hash_changes_with_a_new_study_or_direction(subtopiced):
    qid = q(subtopiced); add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="B", direction="down")
    h1 = knowledge.explain_state(subtopiced, group_of(subtopiced, qid)).inputs_hash
    assert knowledge.explain_state(subtopiced, group_of(subtopiced, qid)).inputs_hash == h1
    add(subtopiced, question=qid, by="C", direction="no_change")
    assert knowledge.explain_state(subtopiced, group_of(subtopiced, qid)).inputs_hash != h1


# --- the knowledge model shows the explanation only while it matches the question ------------------
from evidence_echo.core import store  # noqa: E402


def save_agreed(db, qid, lines, summary="Shops gain."):
    state = knowledge.explain_state(db, group_of(db, qid))
    store.save_question_explanation(db, question_id=qid, status="agreed", summary=summary, lines=lines,
                                    main_result=state.main_result, inputs_hash=state.inputs_hash,
                                    prompt_version="relate-4")
    return state


def test_agreed_row_with_current_hash_is_shown(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="C")
    b = add(subtopiced, question=qid, by="B", direction="down")
    line = {"study": b, "reason": "different_population", "quote": "q q q", "note": "n"}
    save_agreed(subtopiced, qid, [line])
    assert group_of(subtopiced, qid).explanation == {"summary": "Shops gain.", "main_result": "up", "lines": [line]}


def test_varied_question_shows_its_explanation(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A")
    b = add(subtopiced, question=qid, by="B", direction="no_change")
    save_agreed(subtopiced, qid, [{"study": b, "reason": "method_quality", "quote": "q q q", "note": "n"}], summary=None)
    g = group_of(subtopiced, qid)
    assert g.label == "varied" and g.explanation["summary"] is None and g.explanation["lines"][0]["study"] == b


def test_undecided_row_is_never_shown(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="B", direction="down")
    state = knowledge.explain_state(subtopiced, group_of(subtopiced, qid))
    store.save_question_explanation(subtopiced, question_id=qid, status="undecided", summary=None, lines=[],
                                    main_result="up", inputs_hash=state.inputs_hash, prompt_version="relate-4")
    assert group_of(subtopiced, qid).explanation is None


def test_study_leaving_the_question_hides_the_block(subtopiced):
    qid = q(subtopiced)
    other = q(subtopiced, text="Does Z raise Y?")
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="C")
    b = add(subtopiced, question=qid, by="B", direction="down")
    save_agreed(subtopiced, qid, [{"study": b, "reason": "method_quality", "quote": "q q q", "note": "n"}])
    assert group_of(subtopiced, qid).explanation is not None
    store.set_study_question(subtopiced, b, other, "down", "group-2")
    assert all(g.explanation is None for e in knowledge.evidence(subtopiced) for g in e.questions)


def test_new_study_hides_the_block_until_asked_again(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="C")
    b = add(subtopiced, question=qid, by="B", direction="down")
    save_agreed(subtopiced, qid, [{"study": b, "reason": "method_quality", "quote": "q q q", "note": "n"}])
    add(subtopiced, question=qid, by="D", direction="no_change")
    assert group_of(subtopiced, qid).explanation is None


def test_a_main_result_study_is_always_sent(subtopiced):
    qid = q(subtopiced)
    ups = [add(subtopiced, question=qid, by=f"U{n}") for n in range(3)]
    for n, d in enumerate(["down", "down", "no_change", "no_change"] + ["mixed"] * 10):
        add(subtopiced, question=qid, by=f"O{n}", direction=d)
    st = knowledge.explain_state(subtopiced, group_of(subtopiced, qid))
    assert st.main_result == "up" and len(st.explain) == 14 and not st.truncated
    assert ups[2] in st.studies and len(st.studies) == 15


def test_more_than_sixteen_to_explain_is_truncated(subtopiced):
    qid = q(subtopiced)
    for n in range(19):
        add(subtopiced, question=qid, by=f"U{n}")
    downs = [add(subtopiced, question=qid, by=f"D{n}", direction="down") for n in range(18)]
    st = knowledge.explain_state(subtopiced, group_of(subtopiced, qid))
    assert st.truncated and st.explain == sorted(downs, reverse=True)[:16] and len(st.studies) == 17   # 16 + the best up study


def test_level_explanation_shows_values_not_directions(subtopiced):
    """B9: a level question's explanation names values ("0.003%"), not directions."""
    from evidence_echo.render import html as html_render, obsidian
    qid = q(subtopiced, kind="level")
    a = add(subtopiced, question=qid, by="A", direction=None, tier="B")
    b = add(subtopiced, question=qid, by="B", direction=None, tier="D")
    for sid, value in ((a, "0.003%"), (b, "30–40%")):
        subtopiced.execute("UPDATE studies SET headline_value=? WHERE id=?", (value, sid))
    subtopiced.commit()
    line = {"study": b, "reason": "different_population", "quote": "q q q", "note": "n", "value": "30–40%"}
    save_agreed(subtopiced, qid, [line])
    g = group_of(subtopiced, qid)
    page = html_render.question_explanation(g, {})
    assert "Main result: 0.003%" in page and "(30–40%)" in page and "not read yet" not in page
    names = obsidian._Names(base="Evidence Echo/seo", studies={}, series={}, sources={})
    note = "\n".join(obsidian._question_callout(g, names, {}))
    assert "Main result: 0\\.003%" in note and "(30–40%)" in note   # _md escapes the dot
