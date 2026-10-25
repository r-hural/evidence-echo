"""Schema step 10: question kind and comparison, old directions cleared, judged studies marked classified."""
import sqlite3

import pytest

from evidence_echo.core import store
from evidence_echo.stages import classify


def _version9(path):
    """A database as step 9 left it: the current schema minus the step 10 columns."""
    schema = store.SCHEMA.replace(store.QUESTIONS_DDL, store.QUESTIONS_DDL.replace(
        ",\n  kind TEXT,\n  comparison TEXT", ""))
    assert schema != store.SCHEMA
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO subtopics (id, slug, name, status, created_at) VALUES (1, 's', 'S', 'seeded', 'x')")
    conn.execute("""INSERT INTO items (id, url, title, source_name, source_type, state, fetched_at, metric_code)
                    VALUES (1, 'u1', 't', 'src', 'rss', 'verified', 'x', 'ctr'),
                           (2, 'u2', 't', 'src', 'rss', 'verified', 'x', NULL),
                           (3, 'u3', 't', 'src', 'rss', 'verified', 'x', NULL)""")
    conn.execute("UPDATE items SET subtopic_choice='s' WHERE id=3")
    conn.execute("INSERT INTO questions (id, subtopic_id, text, measure, created_at) VALUES (1, 1, 'Q?', 'm', 'x')")
    conn.execute("""INSERT INTO studies (id, canonical_name, created_at, question_id, question_direction,
                    grouped_version, created_by_item) VALUES
                    (1, 'a', 'x', 1, 'up', 'group-1', 1), (2, 'b', 'x', 1, 'down', 'group-1', 2),
                    (3, 'c', 'x', NULL, NULL, NULL, 3)""")
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    conn.close()


def test_step10_adds_kind_and_comparison(tmp_path):
    path = tmp_path / "echo.db"
    _version9(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    question = dict(conn.execute("SELECT * FROM questions").fetchone())
    assert question["kind"] is None and question["comparison"] is None
    assert question["text"] == "Q?" and question["measure"] == "m"
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM studies")}
    assert all(r["question_direction"] is None for r in rows.values())
    assert rows[1]["question_id"] == 1 and rows[1]["grouped_version"] == "group-1"
    before = [tuple(r) for r in conn.execute("SELECT * FROM studies ORDER BY id")]
    conn.close()
    again = store.connect(path)
    assert [tuple(r) for r in again.execute("SELECT * FROM studies ORDER BY id")] == before
    again.close()


def test_step10_marks_judged_studies_classified(tmp_path):
    path = tmp_path / "echo.db"
    _version9(path)
    conn = store.connect(path)
    got = {r["id"]: r["classified_version"] for r in conn.execute("SELECT id, classified_version FROM studies")}
    assert got == {1: "classify-1", 2: None, 3: "classify-1"}
    conn.close()


def test_step10_classify_version_matches_classify():
    assert store.CLASSIFY_VERSION_AT_STEP10 == "classify-1"   # what step 10 stamped; classify has moved on


@pytest.fixture
def two(db):
    store.seed_subtopics(db, ["Title tags & meta", "Local"])
    return store.get_subtopic(db, "title-tags-meta")["id"], store.get_subtopic(db, "local")["id"]


def _study(db, subtopic_id):
    return store.create_study(db, "B", {"what_was_tested": "t", "findings": "f"}, None, subtopic_id=subtopic_id)


def test_create_question_kind_rules(db, two):
    with pytest.raises(ValueError):
        store.create_question(db, two[0], "Q?", "m", "difference")
    with pytest.raises(ValueError):
        store.create_question(db, two[0], "Q?", "m", "level", "a vs b")
    with pytest.raises(ValueError):
        store.create_question(db, two[0], "Q?", "m", "difference", "x" * 121)
    with pytest.raises(ValueError):
        store.create_question(db, two[0], "Q?", "m", "other")
    qid = store.create_question(db, two[0], "Q?", "m", "difference", "  a   vs b ")
    got = store.get_question(db, qid)
    assert (got["kind"], got["comparison"]) == ("difference", "a vs b")
    assert store.get_question(db, store.create_question(db, two[0], "L?", "m", "level"))["comparison"] is None


def test_set_question_kind_only_once_and_clears_level_directions(db, two):
    qid = store.create_question(db, two[0], "Q?", "m")
    sid = _study(db, two[0])
    store.set_study_question(db, sid, qid, "up", "group-2")
    store.set_question_kind(db, qid, "level", None)
    assert store.get_question(db, qid)["kind"] == "level"
    assert store.get_study(db, sid)["question_direction"] is None
    with pytest.raises(ValueError, match="already has kind level"):
        store.set_question_kind(db, qid, "capability", None)


def test_set_study_question_moves_subtopic(db, two):
    qid = store.create_question(db, two[1], "Q?", "m", "difference", "a vs b")
    sid = _study(db, two[0])
    store.set_study_question(db, sid, qid, "up", "group-2")
    assert store.get_study(db, sid)["subtopic_id"] == two[1]


def test_set_study_question_direction_rules(db, two):
    diff = store.create_question(db, two[0], "D?", "m", "difference", "a vs b")
    level = store.create_question(db, two[0], "L?", "m", "level")
    sid = _study(db, two[0])
    with pytest.raises(ValueError):
        store.set_study_question(db, sid, diff, None, "group-2")
    with pytest.raises(ValueError):
        store.set_study_question(db, sid, level, "up", "group-2")
    store.set_study_question(db, sid, level, None, "group-2")
    assert store.get_study(db, sid)["question_direction"] is None
