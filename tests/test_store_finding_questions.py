"""Findings mode storage (schema step 17): finding questions, mode guards, position-1 subtopic."""
import pytest

from evidence_echo.core import store


@pytest.fixture
def two(db):
    store.seed_subtopics(db, ["Title tags & meta", "Local"])
    return store.get_subtopic(db, "title-tags-meta")["id"], store.get_subtopic(db, "local")["id"]


def study_with_findings(db, subtopic_id, n=2):
    sid = store.create_study(db, "B", {"what_was_tested": "t", "findings": "f"}, None, subtopic_id=subtopic_id)
    db.execute("INSERT INTO items (url, source_name, source_type, fetched_at) "
               "VALUES (?, 'src', 'rss', 'x')", (f"http://x/{sid}",))
    item = db.execute("SELECT MAX(id) FROM items").fetchone()[0]
    store.write_findings(db, sid, item, [{"text": f"f{i}", "quote": f"q{i}", "quote_checked": True}
                                         for i in range(n)], "findings-1")
    return sid, [f["id"] for f in store.study_findings(db, sid)]


def fq(db, subtopic_id, text="Does X raise Y?", **kw):
    return store.create_question(db, subtopic_id, text, "Y", mode="findings", **kw)


def test_question_mode_helper_and_list(db, two):
    assert store.question_mode("findings") == "findings"
    assert store.question_mode("quotes") == store.question_mode("questions") == "questions"
    a = store.create_question(db, two[0], "a", "m")
    b = fq(db, two[0], "b")
    assert [q["id"] for q in store.list_questions(db)] == [a, b]
    assert [q["id"] for q in store.list_questions(db, "findings")] == [b]
    assert [q["id"] for q in store.list_questions(db, "questions")] == [a]
    with pytest.raises(ValueError):
        store.create_question(db, two[0], "c", "m", mode="other")


def test_set_finding_question_rules(db, two):
    _, (f1, f2) = study_with_findings(db, two[0])
    diff = fq(db, two[0], kind="difference", comparison="A vs B")
    level = fq(db, two[0], "level q", kind="level")
    with pytest.raises(ValueError):
        store.set_finding_question(db, f1, diff, None, "group-3")
    store.set_finding_question(db, f1, diff, "up", "group-3")
    assert store.get_finding(db, f1)["direction"] == "up"
    with pytest.raises(ValueError):
        store.set_finding_question(db, f2, level, "up", "group-3")
    store.set_finding_question(db, f2, level, None, "group-3", by_owner=True)
    assert store.get_finding(db, f2)["by_owner"] == 1
    plain = store.create_question(db, two[0], "plain", "m")
    with pytest.raises(ValueError):
        store.set_finding_question(db, f1, plain, None, "group-3")


def test_same_study_findings_never_share_a_question(db, two):
    _, (f1, f2) = study_with_findings(db, two[0])
    a, b = fq(db, two[0], "a", kind="level"), fq(db, two[0], "b", kind="level")
    store.set_finding_question(db, f1, a, None, "group-3")
    with pytest.raises(ValueError, match="already has a finding"):
        store.set_finding_question(db, f2, a, None, "group-3")
    store.set_finding_question(db, f2, b, None, "group-3")
    # merging f2's question into the one f1 holds would make them share it: refused
    with pytest.raises(ValueError, match="has findings under both"):
        store.merge_questions(db, b, a)
    _, (g1, g2) = study_with_findings(db, two[0])
    c = fq(db, two[0], "c", kind="level")
    store.set_finding_question(db, g1, c, None, "group-3")
    with pytest.raises(ValueError):
        store.move_finding(db, g2, c)
    store.move_finding(db, g2, a)
    assert store.get_finding(db, g2)["by_owner"] == 1


def test_position_one_sets_findings_subtopic(db, two):
    sid, (f1, f2) = study_with_findings(db, two[0])
    q_local = fq(db, two[1], kind="level")
    store.set_finding_question(db, f2, q_local, None, "group-3")
    assert store.get_study(db, sid)["findings_subtopic_id"] is None
    other = fq(db, two[1], "other", kind="level")
    store.set_finding_question(db, f1, other, None, "group-3")
    study = store.get_study(db, sid)
    assert study["findings_subtopic_id"] == two[1]
    assert study["subtopic_id"] == two[0] and study["question_id"] is None


def test_study_subtopic_by_mode(db, two):
    sid, _ = study_with_findings(db, two[0])
    study = store.get_study(db, sid)
    assert store.study_subtopic(db, study, "findings")["id"] == two[0]
    db.execute("UPDATE studies SET findings_subtopic_id=? WHERE id=?", (two[1], sid))
    study = store.get_study(db, sid)
    assert store.study_subtopic(db, study, "findings")["id"] == two[1]
    assert store.study_subtopic(db, study, "questions")["id"] == two[0]
    assert store.study_subtopic(db, study, "quotes")["id"] == two[0]
    db.execute("UPDATE subtopics SET status='retired' WHERE id=?", (two[1],))
    assert store.study_subtopic(db, study, "findings")["id"] == two[0]


def test_clear_finding_grouping_keeps_pins(db, two):
    sid, (f1, f2) = study_with_findings(db, two[0])
    a, b = fq(db, two[0], "a", kind="level"), fq(db, two[0], "b", kind="level")
    store.set_finding_question(db, f1, a, None, "group-3")
    store.set_finding_question(db, f2, b, None, "group-3", by_owner=True)
    assert store.get_study(db, sid)["findings_subtopic_id"] == two[0]
    assert store.clear_finding_grouping(db) == 1
    assert store.get_finding(db, f1)["question_id"] is None
    assert store.get_finding(db, f1)["grouped_version"] is None
    assert store.get_study(db, sid)["findings_subtopic_id"] is None
    assert store.get_finding(db, f2)["question_id"] == b
    _, (g1, _) = study_with_findings(db, two[0])
    c = fq(db, two[0], "c", kind="level")
    store.set_finding_question(db, g1, c, None, "group-3")
    store.rename_question(db, c, "renamed c")
    assert store.clear_finding_grouping(db) == 0
    assert store.get_finding(db, g1)["question_id"] == c


def test_merge_questions_refuses_mixed_modes(db, two):
    a = store.create_question(db, two[0], "a", "m")
    b = fq(db, two[0], "b")
    with pytest.raises(ValueError, match="different grouping modes"):
        store.merge_questions(db, a, b)


def test_move_study_refuses_findings_question(db, two):
    sid, _ = study_with_findings(db, two[0])
    q = fq(db, two[0])
    with pytest.raises(ValueError):
        store.move_study(db, sid, q)
    with pytest.raises(ValueError):
        store.set_study_question(db, sid, q, "up", "group-3")


def test_kind_change_clears_finding_directions(db, two):
    _, (f1, _) = study_with_findings(db, two[0])
    q = fq(db, two[0], kind="difference", comparison="A vs B")
    store.set_finding_question(db, f1, q, "up", "group-3")
    out = store.rename_question(db, q, "Level now?", kind="level")
    assert out["directions_cleared"] == 1
    assert store.get_finding(db, f1)["direction"] is None


def test_set_finding_direction_and_pin(db, two):
    _, (f1, _) = study_with_findings(db, two[0])
    q = fq(db, two[0], kind="difference", comparison="A vs B")
    store.set_finding_question(db, f1, q, "up", "group-3")
    store.set_finding_direction(db, f1, "down", "group-3")
    assert store.get_finding(db, f1)["direction"] == "down"
    assert not store.is_finding_pinned(db, store.get_finding(db, f1))
    store.move_finding(db, f1, q)
    finding = store.get_finding(db, f1)
    assert finding["direction"] is None and store.is_finding_pinned(db, finding)
