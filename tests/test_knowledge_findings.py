"""Evidence over findings (grouping modes part 2, task 4): one entry per (question, study)."""
import pytest

from evidence_echo.core import knowledge, store
from tests.conftest import make_item


@pytest.fixture
def subs(db):
    store.seed_subtopics(db, ["Title tags & meta", "Local", "Backlinks"])
    return db


def fq(db, subtopic="title-tags-meta", text="Does X raise Y?", kind="difference", measure="Y"):
    return store.create_question(db, store.get_subtopic(db, subtopic)["id"], text, measure, kind,
                                 "with X vs without X" if kind == "difference" else None, mode="findings")


def study(db, subtopic="title-tags-meta", by=None, n=1, tier="B", headline=None, version="findings-1"):
    """A counting study with `n` findings (not grouped yet); version None = backfill pending."""
    tid = store.get_subtopic(db, subtopic)["id"] if subtopic else None
    sid = store.create_study(db, tier, {"what_was_tested": f"study by {by}", "findings": "f"}, None,
                             provenance={"conducted_by": by}, subtopic_id=tid)
    url = f"https://e.com/{sid}"
    store.insert_items(db, [make_item(url=url)])
    db.execute("UPDATE items SET study_id=?, state='verified', verdict_pass=1 WHERE url=?", (sid, url))
    if headline:
        db.execute("UPDATE studies SET headline_value=? WHERE id=?", (headline, sid))
    db.commit()
    item = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    if version == "findings-1":
        store.write_findings(db, sid, item, [{"text": f"s{sid} finding {i + 1}", "quote": f"quote {i + 1}",
                                              "quote_checked": True} for i in range(n)], version)
    elif version:
        db.execute("UPDATE studies SET findings_version=? WHERE id=?", (version, sid))
        db.commit()
    return sid, [f["id"] for f in store.study_findings(db, sid)]


def put(db, fid, qid, direction="up"):
    store.set_finding_question(db, fid, qid, direction, "group-f1")


def by_key(db):
    return {e.key: e for e in knowledge.evidence(db, "findings")}


def test_study_under_three_questions(subs):
    sid, (f1, f2, f3) = study(subs, n=3)
    a, b = fq(subs, text="a"), fq(subs, "local", text="b")
    c = fq(subs, "backlinks", text="c", kind="level")
    put(subs, f1, a)
    put(subs, f2, b)
    put(subs, f3, c, None)
    for other, q_ in ((study(subs, by="O1")[1][0], a), (study(subs, "local", by="O2")[1][0], b),
                      (study(subs, "backlinks", by="O3")[1][0], c)):
        put(subs, other, q_, None if q_ == c else "up")
    ev = by_key(subs)
    for key, qid, n in (("title-tags-meta", a, 1), ("local", b, 2), ("backlinks", c, 3)):
        g = [x for x in ev[key].questions if x.question_id == qid][0]
        row = [r for r in g.rows if r.study_id == sid][0]
        assert row.finding["text"] == f"s{sid} finding {n}"
        assert g.findings[sid]["text"] == f"s{sid} finding {n}"
    assert all(sid not in [o.row.study_id for o in e.others] for e in ev.values())


def test_labels_over_findings(subs):
    qid = fq(subs)
    (_, (a,)), (_, (b,)), (_, (c,)) = study(subs, by="A"), study(subs, by="B"), study(subs, by="C")
    put(subs, a, qid, "up")
    put(subs, b, qid, "up")
    assert by_key(subs)["title-tags-meta"].questions[0].label == "consistent"
    put(subs, c, qid, "down")
    g = by_key(subs)["title-tags-meta"].questions[0]
    assert g.label == "contested" and g.independent == 3
    # a level question carries no label; a kind change to level clears it
    store.rename_question(subs, qid, "Does X raise Y?", kind="level")
    assert by_key(subs)["title-tags-meta"].questions[0].label is None


def test_independence_counts_an_organisation_once(subs):
    qid = fq(subs)
    for by in ("Acme", "Acme", "Other"):
        put(subs, study(subs, by=by)[1][0], qid, "up")
    g = by_key(subs)["title-tags-meta"].questions[0]
    assert g.independent == 2 and len(g.rows) == 3


def test_study_counted_once_per_question(subs):
    sid, (f1, f2) = study(subs, n=2)
    other, (o1,) = study(subs, by="O")
    qid = fq(subs)
    put(subs, o1, qid, "up")
    put(subs, f2, qid, "down")
    # a duplicate written by SQL, bypassing the store's same-study rule
    subs.execute("UPDATE findings SET question_id=?, direction='up' WHERE id=?", (qid, f1))
    subs.commit()
    g = by_key(subs)["title-tags-meta"].questions[0]
    rows = [r for r in g.rows if r.study_id == sid]
    assert len(rows) == 1 and rows[0].finding["position"] == 1
    assert g.directions[sid] == "up"


def test_question_in_other_subtopic_lists_study(subs):
    sid, (f1,) = study(subs, "title-tags-meta")
    qid = fq(subs, "local")
    put(subs, f1, qid)
    put(subs, study(subs, "local", by="O")[1][0], qid)
    ev = by_key(subs)
    assert [g.question_id for g in ev["local"].questions] == [qid]
    assert sid in ev["local"].questions[0].directions
    assert "title-tags-meta" not in ev          # nothing is left in the study's own subtopic
    assert knowledge.finding_questions(list(ev.values()))[sid][0]["question_id"] == qid


def test_waiting_lists_pending_and_ungrouped(subs):
    pending, _ = study(subs, version=None)
    ungrouped, _ = study(subs, by="U")
    partial, (p1, p2) = study(subs, by="P", n=2)
    qid = fq(subs)
    put(subs, p1, qid)
    put(subs, study(subs, by="O")[1][0], qid)
    ev = by_key(subs)["title-tags-meta"]
    assert {r.study_id for r in ev.waiting} == {pending, ungrouped, partial}
    assert partial in {r.study_id for r in ev.questions[0].rows}          # grouped and waiting
    assert pending not in {o.row.study_id for o in ev.others}
    assert knowledge.waiting_count(subs, "findings") == 3


def test_failed_study_is_loose(subs):
    failed, _ = study(subs, version="findings-1:failed")
    ev = by_key(subs)["title-tags-meta"]
    assert [o.row.study_id for o in ev.others] == [failed]
    assert ev.others[0].question is None and ev.waiting == []
    assert knowledge.waiting_count(subs, "findings") == 0


def test_level_value_only_from_position_one(subs):
    qid = fq(subs, kind="level", text="How big?")
    a, (a1, a2) = study(subs, by="A", n=2, headline="10%")
    b, (b1,) = study(subs, by="B", headline="40%")
    put(subs, a2, qid, None)           # position 2: no value
    put(subs, b1, qid, None)
    g = by_key(subs)["title-tags-meta"].questions[0]
    assert knowledge.explain_state(subs, g) is None
    c, (c1,) = study(subs, by="C", headline="12%")
    put(subs, c1, qid, None)           # position 1 of C: 12% vs 40% spread
    g = by_key(subs)["title-tags-meta"].questions[0]
    state = knowledge.explain_state(subs, g)
    assert state is not None and state.directions[a] is None and state.explain == [b]


def test_finding_in_the_hash_only_in_findings_mode(subs):
    sid, (f1,) = study(subs)
    base = knowledge._study_inputs(subs, store.get_study(subs, sid))
    assert "finding" not in base
    assert knowledge._study_inputs(subs, store.get_study(subs, sid), {"id": f1, "text": "t"})["finding"] == {
        "id": f1, "text": "t"}


def test_finding_questions_map(subs):
    sid, (f1, f2) = study(subs, n=2)
    multi, solo = fq(subs, text="multi"), fq(subs, text="solo", kind="level")
    put(subs, f1, multi)
    put(subs, f2, solo, None)
    put(subs, study(subs, by="O")[1][0], multi)
    mapped = knowledge.finding_questions(knowledge.evidence(subs, "findings"))
    first, second = mapped[sid]
    assert (first["question"], first["finding"], first["quote_checked"]) == ("multi", f"s{sid} finding 1", True)
    assert first["question_id"] == multi and first["independent"] >= 1
    assert (second["question"], second["label"]) == ("solo", None)


def test_relate_ready_false_while_pending_or_ungrouped(subs):
    assert knowledge.relate_ready(subs)
    sid, _ = study(subs, version=None)
    assert not knowledge.relate_ready(subs)            # backfill pending
    store.write_findings(subs, sid, subs.execute("SELECT id FROM items").fetchone()[0],
                         [{"text": "t", "quote": "q", "quote_checked": True}], "findings-1")
    f1 = store.study_findings(subs, sid)[0]["id"]
    assert not knowledge.relate_ready(subs)            # ungrouped
    put(subs, f1, fq(subs))
    assert knowledge.relate_ready(subs)


def test_questions_mode_evidence_unchanged(tmp_path):
    from tests.test_html_golden import build_golden_db
    db = build_golden_db(tmp_path)
    assert knowledge.evidence(db) == knowledge.evidence(db, "questions")
    assert all(not e.waiting and all(o.question_id is None for o in e.others)
               and all(not g.findings for g in e.questions) for e in knowledge.evidence(db))


def test_study_with_two_single_study_questions_in_one_subtopic(subs):
    sid, (f1, f2) = study(subs, n=2)
    a, b = fq(subs, text="Qa?"), fq(subs, text="Qb?")
    put(subs, f1, a)
    put(subs, f2, b)
    others = by_key(subs)["title-tags-meta"].others
    assert sorted((o.question_id, o.question) for o in others) == [(a, "Qa?"), (b, "Qb?")]
    assert {o.row.finding["id"] for o in others} == {f1, f2}


def test_merge_refuses_two_findings_of_one_study_under_one_question(subs):
    sid, (f1, f2) = study(subs, n=2)
    a, b = fq(subs, text="Qa?"), fq(subs, text="Qb?")
    put(subs, f1, a)
    put(subs, f2, b)
    with pytest.raises(ValueError, match=f"s{sid} has findings under both"):
        store.merge_questions(subs, a, b)
    assert store.get_finding(subs, f1)["question_id"] == a


def test_merge_across_subtopics_moves_findings_subtopic(subs):
    sid, (f1,) = study(subs)
    a, b = fq(subs, text="Qa?"), fq(subs, "local", text="Qb?")
    put(subs, f1, a)
    store.merge_questions(subs, a, b)
    assert store.get_study(subs, sid)["findings_subtopic_id"] == store.get_subtopic(subs, "local")["id"]


def test_series_editions_count_studies_not_findings(subs):
    sid, (f1, f2) = study(subs, n=2)
    put(subs, f1, fq(subs, text="Qa?"))
    put(subs, f2, fq(subs, text="Qb?"))
    series = store.get_or_create_series(subs, "Annual")
    subs.execute("UPDATE studies SET series_id=? WHERE id=?", (series, sid))
    subs.commit()
    assert [o.row.edition for o in by_key(subs)["title-tags-meta"].others] == [None, None]
    other, (g1,) = study(subs)
    put(subs, g1, fq(subs, text="Qc?"))
    subs.execute("UPDATE studies SET series_id=? WHERE id=?", (series, other))
    subs.commit()
    editions = {}
    for o in by_key(subs)["title-tags-meta"].others:
        editions.setdefault(o.row.study_id, set()).add(o.row.edition)
    assert sorted(editions[sid] | editions[other]) == [1, 2]
    assert len(editions[sid]) == 1 and len(editions[other]) == 1
