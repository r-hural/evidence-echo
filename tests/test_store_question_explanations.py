# tests/test_store_question_explanations.py
"""Schema step 12: question_explanations, and the store functions around it."""
import sqlite3

from evidence_echo.core import store
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)

LINE = {"study": 63, "reason": "different_population", "quote": "organic clicks fall 5%", "note": "News sites."}


def save(db, qid, status="agreed", **over):
    args = dict(question_id=qid, status=status, summary="Shops gain.", lines=[LINE], main_result="up",
                inputs_hash="h1", prompt_version="relate-4")
    store.save_question_explanation(db, **{**args, **over})


def test_save_and_read_back_decodes_lines(subtopiced):
    qid = q(subtopiced)
    save(subtopiced, qid)
    row = store.get_question_explanation(subtopiced, qid)
    assert row["lines"] == [LINE] and row["summary"] == "Shops gain." and row["main_result"] == "up"
    save(subtopiced, qid, summary=None, lines=[], inputs_hash="h2", status="undecided")
    row = store.get_question_explanation(subtopiced, qid)
    assert (row["status"], row["summary"], row["lines"], row["inputs_hash"]) == ("undecided", None, [], "h2")
    assert len(store.question_explanations(subtopiced)) == 1
    assert store.get_question_explanation(subtopiced, qid + 99) is None


def test_status_filter(subtopiced):
    a, b = q(subtopiced), q(subtopiced, text="Does Z raise Y?")
    save(subtopiced, a); save(subtopiced, b, status="undecided", summary=None, lines=[])
    assert [r["question_id"] for r in store.question_explanations(subtopiced, status="agreed")] == [a]
    assert len(store.question_explanations(subtopiced)) == 2


def test_clear_returns_count(subtopiced):
    a, b = q(subtopiced), q(subtopiced, text="Does Z raise Y?")
    save(subtopiced, a); save(subtopiced, b)
    assert store.clear_question_explanations(subtopiced) == 2
    assert store.question_explanations(subtopiced) == []
    assert store.clear_question_explanations(subtopiced) == 0


def test_step12_on_a_0_14_0_database_keeps_pair_rows(tmp_path):
    path = tmp_path / "echo.db"
    schema = store.SCHEMA.replace(store.QUESTION_EXPLANATIONS_DDL + ";\n", "")
    assert "question_explanations" not in schema
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO studies (id, canonical_name, created_at) VALUES (1,'a','x'), (2,'b','x')")
    conn.execute("""INSERT INTO pair_explanations (study_a, study_b, status, inputs_hash, created_at)
                    VALUES (1, 2, 'agreed', 'h', 'x')""")
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    assert conn.execute("SELECT COUNT(*) FROM question_explanations").fetchone()[0] == 0
    assert [tuple(r) for r in conn.execute("SELECT study_a, study_b, status FROM pair_explanations")] == [
        (1, 2, "agreed")]
    conn.close()
