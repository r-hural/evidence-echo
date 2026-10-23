# tests/test_knowledge_questions.py
"""Questions: labels on 2+ independent groups, echoes, order, subtopic changes, other studies."""
import pytest

from evidence_echo.core import knowledge, store
from tests.conftest import make_item


@pytest.fixture
def subtopiced(db):
    store.seed_subtopics(db, ["Title tags & meta", "Local", "Backlinks"])
    return db


def q(db, subtopic="title-tags-meta", text="Does X raise Y?", measure="Y", kind="difference",
      comparison="with X vs without X") -> int:
    return store.create_question(db, store.get_subtopic(db, subtopic)["id"], text, measure, kind,
                                 comparison if kind == "difference" else None)


def add(db, subtopic="title-tags-meta", question=None, direction="up", by=None, tier="B", items=1,
        text=None) -> int:
    """A study with `items` verified items, filed under `subtopic` and, when `question` is a
    question id, grouped into it with `direction` (None: grouped but no direction yet)."""
    tid = store.get_subtopic(db, subtopic)["id"] if subtopic else None
    sid = store.create_study(db, tier, {"what_was_tested": f"study by {by}", "findings": "f"}, None,
                             provenance={"conducted_by": by}, subtopic_id=tid)
    for n in range(items):
        url = f"https://e.com/{sid}/{n}"
        store.insert_items(db, [make_item(url=url)])
        db.execute("UPDATE items SET study_id=?, state='verified', verdict_pass=1, full_text=? "
                   "WHERE url=?", (sid, text, url))
    db.commit()
    if question is not None and direction is not None:
        store.set_study_question(db, sid, question, direction, "group-2")
    elif question is not None:
        db.execute("UPDATE studies SET question_id=? WHERE id=?", (question, sid))
        db.commit()
    return sid


def by_key(db):
    return {e.key: e for e in knowledge.evidence(db)}


def test_label_needs_two_independent_groups(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A")
    add(subtopiced, question=qid, by="A")
    only = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert only.label is None and only.independent == 1
    add(subtopiced, question=qid, by="B")
    both = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert both.label == "consistent" and both.independent == 2


def test_labels_on_question_directions(subtopiced):
    a, b, c = q(subtopiced, text="a"), q(subtopiced, text="b"), q(subtopiced, text="c")
    add(subtopiced, question=a, by="A"); add(subtopiced, question=a, by="B", direction="down")
    add(subtopiced, question=b, by="A"); add(subtopiced, question=b, by="B", direction="no_change")
    add(subtopiced, question=c, by="A", direction="mixed"); add(subtopiced, question=c, by="B", direction="mixed")
    labels = {g.text: g.label for g in by_key(subtopiced)["title-tags-meta"].questions}
    assert labels == {"a": "contested", "b": "varied", "c": "consistent"}


def test_no_label_while_a_direction_is_missing(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A")
    add(subtopiced, question=qid, by="B", direction=None)
    group = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert group.label is None and group.independent == 2
    assert None in group.directions.values()
    assert [r.direction for r in group.rows] == ["up", None]


def test_order_labelled_then_one_group_then_others(subtopiced):
    lone = q(subtopiced, text="lone")
    series = q(subtopiced, text="series")
    wide = q(subtopiced, text="wide")
    narrow = q(subtopiced, text="narrow")
    add(subtopiced, question=lone, by="A")
    add(subtopiced, question=series, by="A"); add(subtopiced, question=series, by="A")
    add(subtopiced, question=narrow, by="A"); add(subtopiced, question=narrow, by="B")
    for org in "ABC":
        add(subtopiced, question=wide, by=org)
    ungrouped = add(subtopiced, by="Z")
    ev = by_key(subtopiced)["title-tags-meta"]
    assert [g.text for g in ev.questions] == ["wide", "narrow", "series"]
    others = {o.row.study_id: o.question for o in ev.others}
    assert list(others.values()).count("lone") == 1
    assert others[ungrouped] is None and len(others) == 2


def test_unsorted_has_only_others(subtopiced):
    add(subtopiced, subtopic=None, by="A")
    add(subtopiced, subtopic=None, by="B")
    evidence = knowledge.evidence(subtopiced)
    assert [e.key for e in evidence] == [knowledge.UNSORTED]
    assert evidence[0].questions == [] and len(evidence[0].others) == 2
    assert evidence[0].subtopic_id is None and evidence[0].name == knowledge.UNSORTED_NAME


def test_subtopic_merge_keeps_questions_counting(subtopiced):
    qid = q(subtopiced, subtopic="local")
    add(subtopiced, subtopic="local", question=qid, by="A")
    add(subtopiced, subtopic="local", question=qid, by="B")
    store.merge_subtopics(subtopiced, "local", "backlinks")
    ev = by_key(subtopiced)
    assert "local" not in ev
    assert [g.question_id for g in ev["backlinks"].questions] == [qid]
    assert ev["backlinks"].questions[0].label == "consistent"
    assert knowledge.waiting_count(subtopiced) == 0


def test_study_moved_to_another_subtopic_is_ungrouped(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A")
    moved = add(subtopiced, question=qid, by="B")
    subtopiced.execute("UPDATE studies SET subtopic_id=? WHERE id=?",
                       (store.get_subtopic(subtopiced, "local")["id"], moved))
    subtopiced.commit()
    ev = by_key(subtopiced)
    assert [o.question for o in ev["local"].others] == [None]
    assert ev["local"].others[0].row.study_id == moved and ev["local"].others[0].row.direction is None
    assert ev["title-tags-meta"].questions == []
    assert knowledge.waiting_count(subtopiced) == 1


def test_merged_question_counts_in_target(subtopiced):
    a, b = q(subtopiced, text="a"), q(subtopiced, text="b")
    add(subtopiced, question=a, by="A")
    add(subtopiced, question=b, by="B")
    store.merge_questions(subtopiced, a, b)
    groups = by_key(subtopiced)["title-tags-meta"].questions
    assert [(g.question_id, g.independent) for g in groups] == [(b, 2)]


def test_echo_lines_per_question(subtopiced):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="A")
    b = add(subtopiced, question=qid, by="B")
    group = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert group.echoes == {a: "corroborated by 1 other independent study",
                            b: "corroborated by 1 other independent study"}
    solo = q(subtopiced, text="solo")
    s = add(subtopiced, question=solo, by="C", items=2)
    ev = by_key(subtopiced)["title-tags-meta"]
    assert knowledge.study_questions(knowledge.evidence(subtopiced))[s]["echo"] == "1 study · 2 articles"
    assert [o.row.study_id for o in ev.others] == [s]


def test_study_questions_shape(subtopiced):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="A")
    b = add(subtopiced, question=qid, by="B")
    loose = add(subtopiced, by="C")
    add(subtopiced, subtopic=None, by="D")
    got = knowledge.study_questions(knowledge.evidence(subtopiced))
    assert set(got) == {a, b, loose}
    assert got[a] == {"key": "title-tags-meta", "name": "Title tags & meta", "question": "Does X raise Y?",
                      "label": "consistent", "independent": 2,
                      "echo": "corroborated by 1 other independent study"}
    assert got[loose]["question"] is None and got[loose]["label"] is None


def test_independence_by_conducted_by(subtopiced):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="Ahrefs", tier="C")
    b = add(subtopiced, question=qid, by="  ahrefs ", tier="A")       # same organisation: one independent study
    c = add(subtopiced, question=qid, by=None)
    d = add(subtopiced, question=qid, by="")                           # no conducted_by: each counts on its own
    group = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert group.independent == 3 and group.groups == [[b, a], [d], [c]]


def test_group_with_differing_members_is_mixed(subtopiced):
    qid = q(subtopiced)
    a1 = add(subtopiced, question=qid, by="Acme", tier="A")
    a2 = add(subtopiced, question=qid, by="Acme", tier="C", direction="down")
    add(subtopiced, question=qid, by="Beta")
    group = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert group.group_dirs() == ["mixed", "up"] and group.label == "varied"
    assert group.directions[a1] == "up" and group.directions[a2] == "down"      # own directions kept
    assert group.echoes == {}               # Acme (mixed) and Beta (up) corroborate nobody


def test_one_group_of_two_agreeing_studies_has_the_series_echo(subtopiced):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="Acme")
    b = add(subtopiced, question=qid, by=" acme ")
    group = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert (group.label, group.independent) == (None, 1)
    assert group.echoes == {a: "1 study · 2 articles", b: "1 study · 2 articles"}


def test_a_study_whose_own_direction_is_mixed(subtopiced):
    one, two = q(subtopiced, text="one"), q(subtopiced, text="two")
    add(subtopiced, question=one, by="A", direction="mixed"); add(subtopiced, question=one, by="B", direction="mixed")
    add(subtopiced, question=two, by="A", direction="mixed"); add(subtopiced, question=two, by="B")
    labels = {g.text: g.label for g in by_key(subtopiced)["title-tags-meta"].questions}
    assert labels == {"one": "consistent", "two": "varied"}


def test_best_tier_over_all_studies_and_articles(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A", tier="C", items=3)
    add(subtopiced, question=qid, by="B", tier="A", items=2)
    group = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert (group.articles, group.best_tier) == (5, "A")


def test_matched_items_do_not_count(subtopiced):
    qid = q(subtopiced)
    sid = add(subtopiced, question=qid)
    subtopiced.execute("UPDATE items SET state='matched' WHERE study_id=?", (sid,))
    subtopiced.commit()
    assert knowledge.evidence(subtopiced) == [] and knowledge.waiting_count(subtopiced) == 0


def test_corroborated_by_several(subtopiced):
    qid = q(subtopiced)
    first = add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="B")
    add(subtopiced, question=qid, by="C")
    assert by_key(subtopiced)["title-tags-meta"].questions[0].echoes[first] == \
        "corroborated by 2 other independent studies"


def test_merged_studies_and_merged_subtopics(subtopiced):
    qid = q(subtopiced)
    keep, other = add(subtopiced, question=qid, by="A"), add(subtopiced, question=qid, by="B")
    store.merge_studies(subtopiced, keep, other)
    stale = add(subtopiced, subtopic="local", by="C")
    store.merge_subtopics(subtopiced, "local", "title-tags-meta")
    subtopiced.execute("UPDATE studies SET subtopic_id=? WHERE id=?",
                       (store.get_subtopic(subtopiced, "local")["id"], stale))
    subtopiced.commit()                                    # an id of the merged subtopic resolves too
    ev = by_key(subtopiced)["title-tags-meta"]
    assert {o.row.study_id for o in ev.others} | {r.study_id for g in ev.questions for r in g.rows} == {keep, stale}
    assert [e.key for e in knowledge.evidence(subtopiced)] == ["title-tags-meta"]


def test_unsorted_and_subtopic_order(subtopiced):
    store.subtopic_for_choice(subtopiced, {"propose": "Image SEO"}, create=True)
    store.subtopic_for_choice(subtopiced, {"propose": "Guest posts"}, create=True)
    store.reject_subtopic(subtopiced, "guest-posts")
    p, r, n = add(subtopiced, subtopic="image-seo"), add(subtopiced, subtopic="guest-posts"), add(subtopiced, subtopic=None)
    add(subtopiced, subtopic="local"); add(subtopiced, subtopic="title-tags-meta")
    assert [(e.key, e.name) for e in knowledge.evidence(subtopiced)] == [
        ("title-tags-meta", "Title tags & meta"), ("local", "Local"), (knowledge.UNSORTED, "Unsorted")]
    unsorted = by_key(subtopiced)[knowledge.UNSORTED]
    assert {o.row.study_id for o in unsorted.others} == {p, r, n} and unsorted.subtopic_id is None
    store.accept_subtopic(subtopiced, "image-seo")
    assert [e.key for e in knowledge.evidence(subtopiced)] == [
        "title-tags-meta", "local", "image-seo", knowledge.UNSORTED]


def test_studies_without_a_counted_item_are_left_out(subtopiced):
    add(subtopiced, items=0)
    assert knowledge.evidence(subtopiced) == []


def test_group_direction_mixed():
    assert knowledge._group_direction(["up", "down"]) == "mixed"
    assert knowledge._question_label(["up", "no_change"]) == "varied"
    assert knowledge._question_label(["up"]) is None


def test_no_echo_while_directions_are_unread(subtopiced):
    """After a measure change (directions cleared) nothing may claim corroboration."""
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="A", direction=None)
    b = add(subtopiced, question=qid, by="B", direction=None)
    group = by_key(subtopiced)["title-tags-meta"].questions[0]
    assert a not in group.echoes and b not in group.echoes


def test_no_label_for_level_and_capability(subtopiced):
    for kind in ("level", "capability"):
        qid = q(subtopiced, text=kind, kind=kind)
        add(subtopiced, question=qid, by="A", direction=None); add(subtopiced, question=qid, by="B", direction=None)
    groups = by_key(subtopiced)["title-tags-meta"].questions
    assert {g.text: (g.label, g.kind, g.comparison) for g in groups} == {
        "level": (None, "level", None), "capability": (None, "capability", None)}
    assert all(g.independent == 2 and not g.echoes for g in groups)


def test_no_label_for_null_kind(subtopiced):
    qid = q(subtopiced, kind=None)
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="B")
    [g] = by_key(subtopiced)["title-tags-meta"].questions
    assert g.label is None and g.kind is None and not g.echoes


def test_difference_label_rule_unchanged(subtopiced):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A"); add(subtopiced, question=qid, by="B", direction="down")
    [g] = by_key(subtopiced)["title-tags-meta"].questions
    assert (g.label, g.kind, g.comparison) == ("contested", "difference", "with X vs without X")


def test_echo_only_for_difference(subtopiced):
    diff = q(subtopiced, text="d")
    a = add(subtopiced, question=diff, by="A"); add(subtopiced, question=diff, by="B")
    level = q(subtopiced, text="l", kind="level")
    c = add(subtopiced, question=level, by="C", direction=None, items=2)
    groups = {g.text: g for g in by_key(subtopiced)["title-tags-meta"].questions}
    assert groups["d"].echoes[a] == "corroborated by 1 other independent study"
    other = {o.row.study_id: o for o in by_key(subtopiced)["title-tags-meta"].others}
    assert other[c].echo == "1 study · 2 articles"
