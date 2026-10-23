"""Schema step 9 storage: questions, study question columns, per-question pair explanations."""
import pytest

from evidence_echo.core import store


@pytest.fixture
def two(db):
    store.seed_subtopics(db, ["Title tags & meta", "Local"])
    return store.get_subtopic(db, "title-tags-meta")["id"], store.get_subtopic(db, "local")["id"]


def study(db, subtopic_id):
    return store.create_study(db, "B", {"what_was_tested": "t", "findings": "f"}, None,
                              subtopic_id=subtopic_id)


def test_create_question_collapses_whitespace_and_checks_limits(db, two):
    qid = store.create_question(db, two[0], "  a   b ", " m\n x ")
    got = store.get_question(db, qid)
    assert got["text"] == "a b" and got["measure"] == "m x" and got["owner_touched"] == 0
    with pytest.raises(ValueError):
        store.create_question(db, two[0], "x" * 121, "m")
    with pytest.raises(ValueError):
        store.create_question(db, two[0], "t", "m" * 81)
    with pytest.raises(ValueError):
        store.create_question(db, two[0], "  ", "m")
    assert store.create_question(db, two[0], "x" * 120, "m" * 80)


def test_resolve_question_follows_merges(db, two):
    a, b, c = (store.create_question(db, two[0], f"q{n}", "m") for n in range(3))
    store.merge_questions(db, a, b)
    store.merge_questions(db, b, c)
    assert store.resolve_question(db, a)["id"] == c
    assert store.resolve_question(db, 999) is None
    db.execute("UPDATE questions SET merged_into=? WHERE id=?", (a, c))   # a cycle must not loop
    db.commit()
    assert store.resolve_question(db, a) is not None


def test_rename_with_new_measure_clears_directions(db, two):
    q = store.create_question(db, two[0], "Does X raise Y?", "Y")
    s1, s2 = study(db, two[0]), study(db, two[0])
    store.set_study_question(db, s1, q, "up", "group-1")
    store.set_study_question(db, s2, q, "down", "group-1")
    out = store.rename_question(db, q, "Does X lift Y?")
    assert out["directions_cleared"] == 0
    assert store.get_study(db, s1)["question_direction"] == "up"
    out = store.rename_question(db, q, "Does X lift Y?", measure="Y per page")
    assert out == {"renamed": f"q{q}", "text": "Does X lift Y?", "measure": "Y per page",
                   "kind": None, "comparison": None, "directions_cleared": 2}
    assert store.get_study(db, s1)["question_direction"] is None
    assert store.get_study(db, s2)["question_direction"] is None
    assert store.get_study(db, s1)["question_id"] == q
    assert store.get_question(db, q)["owner_touched"] == 1


def test_merge_refuses_other_subtopic_and_self(db, two):
    a = store.create_question(db, two[0], "a", "m")
    b = store.create_question(db, two[1], "b", "m")
    c = store.create_question(db, two[0], "c", "m")
    with pytest.raises(ValueError):
        store.merge_questions(db, a, a)
    assert store.merge_questions(db, a, c)["into"] == f"q{c}"
    assert store.get_question(db, c)["owner_touched"] == 1
    with pytest.raises(ValueError, match="merged"):
        store.merge_questions(db, a, c)


def test_move_study_pins_and_clears_direction(db, two):
    q1 = store.create_question(db, two[0], "a", "m")
    q2 = store.create_question(db, two[0], "b", "m")
    other = store.create_question(db, two[1], "c", "m")
    s = study(db, two[0])
    store.set_study_question(db, s, q1, "up", "group-1")
    assert store.move_study(db, s, q2) == {"moved": f"s{s}", "into": f"q{q2}"}
    got = store.get_study(db, s)
    assert (got["question_id"], got["question_direction"], got["question_by_owner"]) == (q2, None, 1)
    assert store.move_study(db, s, other)["into"] == f"q{other}"      # across subtopics
    assert store.get_study(db, s)["subtopic_id"] == two[1]


def test_clear_grouping_spares_pinned_studies(db, two):
    q = store.create_question(db, two[0], "a", "m")
    touched = store.create_question(db, two[0], "t", "m")
    store.rename_question(db, touched, "t2")
    moved, in_touched, plain = (study(db, two[0]) for _ in range(3))
    store.set_study_question(db, moved, q, "up", "group-1", by_owner=True)
    store.set_study_question(db, in_touched, touched, "up", "group-1")
    store.set_study_question(db, plain, q, "down", "group-1")
    assert store.clear_grouping(db) == 1
    got = store.get_study(db, plain)
    assert (got["question_id"], got["question_direction"], got["grouped_version"]) == (None, None, None)
    assert store.get_study(db, moved)["question_id"] == q
    assert store.get_study(db, in_touched)["question_id"] == touched


def test_pair_explanations_are_keyed_per_question(db, two):
    q1 = store.create_question(db, two[0], "a", "m")
    q2 = store.create_question(db, two[0], "b", "m")
    a, b = study(db, two[0]), study(db, two[0])
    common = dict(study_a=a, study_b=b, subtopic_id=two[0], status="agreed", reason="r", quote_a="x",
                  quote_b="y", note="n", inputs_hash="h", prompt_version="relate-2")
    store.save_pair_explanation(db, question_id=q1, **common)
    store.save_pair_explanation(db, question_id=q2, **common)
    assert len(store.pair_explanations(db)) == 2
    store.save_pair_explanation(db, question_id=q1, **{**common, "reason": "r2"})
    assert len(store.pair_explanations(db)) == 2
    assert store.get_pair_explanation(db, a, b, q1)["reason"] == "r2"
    assert store.get_pair_explanation(db, a, b, q2)["reason"] == "r"


def test_merge_across_subtopics_moves_studies(db, two):
    a = store.create_question(db, two[0], "a", "m", "level")
    b = store.create_question(db, two[1], "b", "m", "level")
    s = study(db, two[0])
    store.set_study_question(db, s, a, None, "group-2")
    store.merge_questions(db, a, b)
    got = store.get_study(db, s)
    assert got["subtopic_id"] == two[1] and store.resolve_question(db, got["question_id"])["id"] == b


def test_merge_refuses_other_kind(db, two):
    a = store.create_question(db, two[0], "a", "m", "level")
    b = store.create_question(db, two[0], "b", "m", "capability")
    with pytest.raises(ValueError, match="level question.*capability question"):
        store.merge_questions(db, a, b)
    assert store.get_question(db, a)["merged_into"] is None


def test_merge_refuses_inactive_target_subtopic(db, two):
    a = store.create_question(db, two[0], "a", "m", "level")
    b = store.create_question(db, two[1], "b", "m", "level")
    db.execute("UPDATE subtopics SET status='rejected' WHERE id=?", (two[1],))
    db.commit()
    with pytest.raises(ValueError, match="inactive subtopic"):
        store.merge_questions(db, a, b)
    assert store.get_question(db, a)["merged_into"] is None


def test_merge_null_kind_takes_source_kind(db, two):
    a = store.create_question(db, two[0], "a", "m", "difference", "x vs y")
    b = store.create_question(db, two[0], "b", "m")
    store.merge_questions(db, a, b)
    got = store.get_question(db, b)
    assert (got["kind"], got["comparison"]) == ("difference", "x vs y")


def test_merge_different_comparisons_clears_directions(db, two):
    a = store.create_question(db, two[0], "a", "m", "difference", "x vs y")
    b = store.create_question(db, two[0], "b", "m", "difference", "p vs q")
    s = study(db, two[0])
    store.set_study_question(db, s, a, "up", "group-2")
    assert store.merge_questions(db, a, b)["directions_cleared"] == 1
    assert store.get_study(db, s)["question_direction"] is None


def test_merge_same_comparison_keeps_directions(db, two):
    a = store.create_question(db, two[0], "a", "m", "difference", "X  vs Y")
    b = store.create_question(db, two[1], "b", "m", "difference", "x vs y")
    s = study(db, two[0])
    store.set_study_question(db, s, a, "up", "group-2")
    store.merge_questions(db, a, b)
    assert store.get_study(db, s)["question_direction"] == "up"


def test_move_across_subtopics(db, two):
    a = store.create_question(db, two[1], "a", "m", "level")
    s = study(db, two[0])
    store.move_study(db, s, a)
    assert store.get_study(db, s)["subtopic_id"] == two[1]
    db.execute("UPDATE subtopics SET status='retired' WHERE id=?", (two[1],))
    db.commit()
    with pytest.raises(ValueError, match="inactive"):
        store.move_study(db, s, a)


def test_rename_kind_to_level_clears_directions(db, two):
    a = store.create_question(db, two[0], "a", "m", "difference", "x vs y")
    s = study(db, two[0])
    store.set_study_question(db, s, a, "up", "group-2")
    out = store.rename_question(db, a, "a?", kind="level")
    assert out["directions_cleared"] == 1 and out["comparison"] is None
    assert store.get_study(db, s)["question_direction"] is None
    with pytest.raises(ValueError):
        store.rename_question(db, a, "a?", kind="difference")     # needs a comparison


def test_merge_giving_a_level_kind_clears_the_targets_directions(db, two):
    a = store.create_question(db, two[0], "a", "m", "level")
    b = store.create_question(db, two[0], "b", "m")                  # kind NULL, a legacy direction
    s = study(db, two[0])
    store.set_study_question(db, s, b, "up", "group-1")
    store.merge_questions(db, a, b)
    assert store.get_question(db, b)["kind"] == "level"
    assert store.get_study(db, s)["question_direction"] is None      # no directions under level
