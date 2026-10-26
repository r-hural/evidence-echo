# tests/test_relate.py
"""SP3b-T: relate - one explanation per contested question, two blind answers, line-by-line agreement."""
import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, knowledge, store
from evidence_echo.stages import relate
from tests.conftest import FakeBackend, make_item
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)

TEXT_A = "Ecommerce sites saw organic clicks rise 12% after the title change."
TEXT_B = "News publishers saw organic clicks fall 5% after the same change."
TEXT_D = "Local shops saw no change in calls after the same edit."


def contested(db, qid=None):
    """A and C say up, B says down: the main result is up and B is the one to explain."""
    qid = qid or q(db)
    return (add(db, question=qid, by="A", text=TEXT_A),
            add(db, question=qid, by="B", direction="down", text=TEXT_B),
            add(db, question=qid, by="C", text=TEXT_A))


def line(sid, reason="different_population", quote="organic clicks fall 5%", note="News sites."):
    return {"id": f"s{sid}", "reason": reason, "quote": quote, "note": note}


def answer(qid, lines, summary="Shops gain, news sites do not."):
    return {"id": f"q{qid}", "summary": summary, "studies": lines}


def results(*rows):
    return json.dumps({"results": list(rows)})


def test_emit_one_item_per_question_with_main_result_and_explain(subtopiced, topic):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    payload = json.loads(relate.emit(subtopiced, topic))
    assert payload["stage"] == "relate" and payload["choices"] == {"reason": list(knowledge.PAIR_REASONS)}
    [item] = payload["items"]
    assert item["id"] == f"q{qid}" and item["main_result"] == "up" and item["explain"] == [f"s{b}"]
    assert (item["subtopic"], item["question"], item["measure"]) == ("Title tags & meta", "Does X raise Y?", "Y")
    assert [s["id"] for s in item["studies"]] == [f"s{c}", f"s{b}", f"s{a}"]
    for study in item["studies"]:
        assert set(study) == {"id", "title", "direction", "findings", "research_timeframe", "sample_scale", "text"}
    assert next(s for s in item["studies"] if s["id"] == f"s{b}")["text"] == TEXT_B


def test_at_most_ten_questions_newest_first(subtopiced, topic):
    ids = []
    for n in range(11):
        qid = q(subtopiced, text=f"Does X{n} raise Y?")
        add(subtopiced, question=qid, by=f"A{n}"); add(subtopiced, question=qid, by=f"B{n}", direction="down")
        add(subtopiced, question=qid, by=f"C{n}")
        ids.append(qid)
    got = [i["id"] for i in json.loads(relate.emit(subtopiced, topic))["items"]]
    assert relate.RELATE_MAX_QUESTIONS == 10 and got == [f"q{qid}" for qid in reversed(ids)][:10]


def test_only_disagreeing_difference_questions_are_asked(subtopiced, topic):
    one_org = q(subtopiced, text="one org")
    add(subtopiced, question=one_org, by="A"); add(subtopiced, question=one_org, by="A", direction="down")
    agree = q(subtopiced, text="agree")
    add(subtopiced, question=agree, by="A"); add(subtopiced, question=agree, by="B")
    level = q(subtopiced, text="level", kind="level")
    add(subtopiced, question=level, by="A", direction=None); add(subtopiced, question=level, by="B", direction=None)
    add(subtopiced, subtopic=None, by="A"); add(subtopiced, subtopic=None, by="B", direction="down")
    ungrouped = q(subtopiced, text="ungrouped")
    add(subtopiced, question=ungrouped, by="A"); add(subtopiced, question=ungrouped, by="B", direction=None)
    assert json.loads(relate.emit(subtopiced, topic))["items"] == []


def test_consistent_yields_no_item(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="B")
    assert relate.pending_questions(subtopiced) == []


def headline(db, sid, value):
    db.execute("UPDATE studies SET headline_value=? WHERE id=?", (value, sid))
    db.commit()


def level(db, *values, tiers=None):
    """A level question with one study per value (each by its own org); returns (qid, [study ids])."""
    qid = q(db, kind="level")
    ids = []
    for n, value in enumerate(values):
        sid = add(db, question=qid, by=f"O{n}", direction=None, tier=(tiers or "B" * len(values))[n])
        if value is not None:
            headline(db, sid, value)
        ids.append(sid)
    return qid, ids


def test_level_spread_is_asked_with_the_best_graded_value_as_main_result(subtopiced, topic):
    """B9: 0.003% (B) vs 30-40% (D) on one level question is a spread to explain."""
    qid, (a, b) = level(subtopiced, "0.003%", "30–40%", tiers="BD")
    [item] = json.loads(relate.emit(subtopiced, topic))["items"]
    assert item["id"] == f"q{qid}" and item["kind"] == "level"
    assert item["main_result"] == "0.003%" and item["explain"] == [f"s{b}"]
    assert next(s for s in item["studies"] if s["id"] == f"s{b}")["direction"] == "30–40%"


def test_level_values_close_together_yield_no_item(subtopiced):
    level(subtopiced, "12%", "14%")
    assert relate.pending_questions(subtopiced) == []


def test_level_range_ends_at_one_count(subtopiced):
    """0.4% vs 1-5% overlaps closely (1/0.4 = 2.5): no spread; 1 is not dropped from the range."""
    level(subtopiced, "0.4%", "1–5%", tiers="BD")
    assert relate.pending_questions(subtopiced) == []


def test_level_spread_needs_the_same_unit(subtopiced):
    level(subtopiced, "12%", "40,000 sessions")
    assert relate.pending_questions(subtopiced) == []


def test_level_without_two_values_yields_no_item(subtopiced):
    level(subtopiced, "12%", None)
    assert relate.pending_questions(subtopiced) == []


def test_level_spread_ignores_a_study_without_value(subtopiced):
    qid, (a, b, c) = level(subtopiced, "0.003%", None, "35%", tiers="BAD")
    [p] = relate.pending_questions(subtopiced)
    assert p["state"].explain == [c] and p["state"].main_result == "0.003%"


def test_difference_hash_unchanged(subtopiced):
    """Explanations stored before B9 stay valid: a difference question's inputs hash is as before."""
    qid = q(subtopiced)
    contested(subtopiced, qid)
    [p] = relate.pending_questions(subtopiced)
    assert p["state"].inputs_hash == "b2ff27c0f588f36a1b774e002d841b12b49ce172a296421cf16fa93b87573ef5"


def test_level_agreed_lines_carry_the_value(subtopiced, topic):
    qid, (a, b) = level(subtopiced, "0.003%", "30–40%", tiers="BD")
    db = subtopiced
    db.execute("UPDATE items SET full_text=? WHERE study_id=?", ("Ramp says 30-40% of direct traffic is AI.", b))
    db.commit()
    lines = [{"id": f"s{b}", "reason": "different_population", "quote": "Ramp says 30-40% of direct", "note": "One company."}]
    out = relate.apply(db, results(answer(qid, lines)), results(answer(qid, lines)))
    assert out["applied"] == 1
    row = store.get_question_explanation(db, qid)
    assert row["main_result"] == "0.003%" and row["lines"][0]["value"] == "30–40%"


def test_instructions_name_every_reason(topic):
    text = relate.instructions(topic)
    assert set(relate.REASONS) == set(knowledge.PAIR_REASONS)
    assert all(code in text for code in knowledge.PAIR_REASONS) and "untrusted" in text


def test_rules_text_names_question_and_measure(topic):
    text = relate.instructions(topic)
    assert "one " in text and "question" in text and "measure" in text and "main_result" in text
    assert "Study A" in text and "never" in text          # the summary must not say "Study A"
    assert relate.PROMPT_VERSION == "relate-4" and knowledge.RELATE_HASH_TAG == "relate-4"


def test_agreed_lines_and_summary_are_stored(subtopiced):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    one = results(answer(qid, [line(b)]))
    out = relate.apply(subtopiced, one, results(answer(qid, [line(b, note="other words")], summary="x")))
    assert out == {"applied": 1, "agreed": 1, "undecided": 0, "rejected": []}
    row = store.get_question_explanation(subtopiced, qid)
    assert (row["status"], row["summary"], row["main_result"], row["prompt_version"]) == (
        "agreed", "Shops gain, news sites do not.", "up", "relate-4")
    assert row["lines"] == [{"study": b, "reason": "different_population", "quote": "organic clicks fall 5%",
                             "note": "News sites."}]
    assert relate.pending_questions(subtopiced) == []


def two_to_explain(db):
    qid = q(db)
    add(db, question=qid, by="A", text=TEXT_A); add(db, question=qid, by="B", text=TEXT_A)
    b = add(db, question=qid, by="C", direction="down", text=TEXT_B)
    d = add(db, question=qid, by="D", direction="no_change", text=TEXT_D)
    return qid, b, d


def test_summary_dropped_when_one_line_fails(subtopiced):
    qid, b, d = two_to_explain(subtopiced)
    first = answer(qid, [line(b), line(d, quote="no change in calls")])
    second = answer(qid, [line(b), line(d, reason="method_quality", quote="no change in calls")])
    out = relate.apply(subtopiced, results(first), results(second))
    assert (out["agreed"], out["undecided"]) == (1, 0)
    row = store.get_question_explanation(subtopiced, qid)
    assert row["summary"] is None and [x["study"] for x in row["lines"]] == [b]


def test_no_line_agreed_is_undecided_with_no_text(subtopiced):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    out = relate.apply(subtopiced, results(answer(qid, [line(b)])),
                       results(answer(qid, [line(b, reason="method_quality")])))
    assert (out["agreed"], out["undecided"]) == (0, 1)
    row = store.get_question_explanation(subtopiced, qid)
    assert row["status"] == "undecided" and row["summary"] is None and row["lines"] == []


def test_quote_not_in_the_text_is_undecided(subtopiced):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    bad = results(answer(qid, [line(b, quote="clicks went up a lot")]))
    assert relate.apply(subtopiced, bad, bad)["undecided"] == 1


def test_quote_must_come_from_that_studys_own_text(subtopiced):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    other = results(answer(qid, [line(b, quote="organic clicks rise 12%")]))   # in TEXT_A, not in b's text
    assert relate.apply(subtopiced, other, other)["undecided"] == 1


@pytest.mark.parametrize("over", [{"summary": "  "}, {"note": ""}])
def test_blank_summary_or_note_is_rejected(subtopiced, over):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    bad = answer(qid, [line(b, note=over.get("note", "News sites."))], summary=over.get("summary", "Fine."))
    out = relate.apply(subtopiced, results(bad), results(bad))
    assert out["applied"] == 0 and out["rejected"] and store.get_question_explanation(subtopiced, qid) is None


def test_truncated_explain_never_keeps_a_summary(subtopiced):
    qid = q(subtopiced)
    for n in range(19):
        add(subtopiced, question=qid, by=f"U{n}", text=TEXT_A)
    for n in range(18):
        add(subtopiced, question=qid, by=f"D{n}", direction="down", text=TEXT_B)
    [pending] = relate.pending_questions(subtopiced)
    full = results(answer(qid, [line(sid) for sid in pending["state"].explain]))
    assert relate.apply(subtopiced, full, full)["agreed"] == 1
    row = store.get_question_explanation(subtopiced, qid)
    assert row["summary"] is None and len(row["lines"]) == 16


def test_no_text_gives_undecided_and_no_block(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="C")
    b = add(subtopiced, question=qid, by="B", direction="down")
    both = results(answer(qid, [line(b)]))
    assert relate.apply(subtopiced, both, both)["undecided"] == 1
    row = store.get_question_explanation(subtopiced, qid)
    assert row["status"] == "undecided" and row["lines"] == [] and row["summary"] is None
    [group] = knowledge.evidence(subtopiced)[0].questions
    assert group.explanation is None


def test_quotes_with_typographic_quotes_still_agree(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="C")
    b = add(subtopiced, question=qid, by="B", direction="down",
            text='The team said "organic clicks fell 5%" after the change.')
    both = results(answer(qid, [line(b, quote="said “organic clicks fell  5%” after")]))
    assert relate.apply(subtopiced, both, both)["agreed"] == 1


@pytest.mark.parametrize("make", [
    lambda b, other: [line(b), line(b)],                 # an id twice
    lambda b, other: [],                                 # a missing id
    lambda b, other: [line(b), line(other)],             # a main-result study added
])
def test_answer_must_cover_explain_exactly(subtopiced, make):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    good = results(answer(qid, [line(b)]))
    bad = results(answer(qid, make(b, a)))
    out = relate.apply(subtopiced, good, bad)
    assert out["applied"] == 0 and store.get_question_explanation(subtopiced, qid) is None
    assert [r["file"] for r in out["rejected"]] == ["b"] and "must answer exactly" in out["rejected"][0]["errors"][0]
    assert [p["question_id"] for p in relate.pending_questions(subtopiced)] == [qid]


def test_one_sided_or_invalid_answers_store_nothing(subtopiced):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    good = results(answer(qid, [line(b)]))
    out = relate.apply(subtopiced, good, results())
    assert out["applied"] == 0
    assert out["rejected"] == [{"id": f"q{qid}", "file": "b", "errors": [relate.ONE_SIDED]}]
    out = relate.apply(subtopiced, good, results(answer(qid, [line(b, reason="vibes")])))
    assert out["applied"] == 0 and len(out["rejected"]) == 1 and out["rejected"][0]["file"] == "b"
    assert "reason: 'vibes' not in" in out["rejected"][0]["errors"][0]
    assert store.question_explanations(subtopiced) == []


def test_stored_question_is_skipped_until_its_inputs_change(subtopiced, topic):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    relate.apply(subtopiced, results(answer(qid, [line(b)])),
                 results(answer(qid, [line(b, reason="method_quality")])))
    assert json.loads(relate.emit(subtopiced, topic))["items"] == []       # undecided, unchanged: not asked
    url = "https://e.com/new-coverage"
    store.insert_items(subtopiced, [make_item(url=url)])
    subtopiced.execute("UPDATE items SET study_id=?, state='verified', verdict_pass=1 WHERE url=?", (b, url))
    subtopiced.commit()
    assert [i["id"] for i in json.loads(relate.emit(subtopiced, topic))["items"]] == [f"q{qid}"]
    both = results(answer(qid, [line(b)]))
    relate.apply(subtopiced, both, both)
    assert [r["status"] for r in store.question_explanations(subtopiced)] == ["agreed"]   # one row, updated


def test_measure_change_hides_the_explanation_until_directions_return(subtopiced):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    both = results(answer(qid, [line(b)]))
    relate.apply(subtopiced, both, both)
    assert knowledge.evidence(subtopiced)[0].questions[0].explanation
    assert store.rename_question(subtopiced, qid, "Does X raise Y?", measure="Z")["directions_cleared"] == 3
    [group] = knowledge.evidence(subtopiced)[0].questions
    assert group.label is None and group.explanation is None
    assert relate.pending_questions(subtopiced) == []
    for sid, direction in ((a, "up"), (b, "down"), (c, "up")):
        store.set_question_direction(subtopiced, sid, direction, "group-1")
    assert [p["question_id"] for p in relate.pending_questions(subtopiced)] == [qid]


def api_reply(b):
    return json.dumps({k: v for k, v in answer(0, [line(b)]).items() if k != "id"})


def test_run_api_asks_twice_with_the_same_prompt(subtopiced, topic):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    backend = FakeBackend([api_reply(b), api_reply(b)])
    assert relate.run_api(subtopiced, backend, topic) == {"processed": 1, "rejected": []}
    assert len(backend.prompts) == 2 and backend.prompts[0] == backend.prompts[1]
    assert TEXT_A in backend.prompts[0] and "different_population" in backend.prompts[0]
    assert "Does X raise Y?" in backend.prompts[0]
    assert store.get_question_explanation(subtopiced, qid)["status"] == "agreed"


def test_run_api_error_stores_nothing(subtopiced, topic):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    out = relate.run_api(subtopiced, FakeBackend([api_reply(b), "garbage"]), topic)
    assert out["processed"] == 0 and out["rejected"] and store.question_explanations(subtopiced) == []


def test_run_api_wrong_ids_store_nothing(subtopiced, topic):
    qid = q(subtopiced)
    a, b, c = contested(subtopiced, qid)
    wrong = json.dumps({"summary": "s", "studies": [line(a)]})
    out = relate.run_api(subtopiced, FakeBackend([wrong, wrong]), topic)
    assert out["processed"] == 0 and store.question_explanations(subtopiced) == []


def test_reset_relate_clears_question_explanations(workspace):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    qid = q(conn, subtopic=store.active_subtopics(conn)[0]["slug"])
    store.save_question_explanation(conn, question_id=qid, status="agreed", summary="s", lines=[],
                                    main_result="up", inputs_hash="h", prompt_version="relate-4")
    conn.close()
    assert api.reset(cfg, "relate") == {"reset": "relate", "questions": 1}
    conn = store.connect(cfg.db_path)
    assert store.question_explanations(conn) == []
    conn.close()


def test_api_cli_and_the_relate_setting(workspace, tmp_path, monkeypatch, capsys):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    qid = q(conn, subtopic=slug)
    add(conn, subtopic=slug, question=qid, by="A", text=TEXT_A)
    add(conn, subtopic=slug, question=qid, by="C", text=TEXT_A)
    b = add(conn, subtopic=slug, question=qid, by="B", direction="down", text=TEXT_B)
    conn.close()
    assert [i["id"] for i in json.loads(api.emit(cfg, "relate"))["items"]] == [f"q{qid}"]
    fa, fb = tmp_path / "a.json", tmp_path / "b.json"
    fa.write_text(results(answer(qid, [line(b)])), encoding="utf-8")
    fb.write_text(results(answer(qid, [line(b)])), encoding="utf-8")
    assert cli.main(["apply", "--stage", "relate", str(fa)]) == 2          # one file is refused
    capsys.readouterr()
    assert cli.main(["apply", "--stage", "relate", str(fa), str(fb)]) == 0
    assert json.loads(capsys.readouterr().out)["agreed"] == 1
    monkeypatch.setenv("EV_ECHO_RELATE", "off")
    off = config.load()
    assert json.loads(api.emit(off, "relate"))["items"] == []
    assert api.run_api(off, "relate") == {"processed": 0, "rejected": [], "skipped": api.RELATE_OFF}
