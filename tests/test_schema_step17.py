"""Schema step 17: finding questions, questions.mode, studies.findings_subtopic_id; nothing is filled."""
import sqlite3

from evidence_echo.core import store

FINDINGS_NEW = ("  version TEXT NOT NULL,\n  question_id INTEGER REFERENCES questions(id),\n"
                "  direction TEXT,\n  grouped_version TEXT,\n  by_owner INTEGER NOT NULL DEFAULT 0,\n"
                "  UNIQUE(study_id, position)")


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _version16(path):
    """A database as step 16 left it: the current schema minus the step 17 columns."""
    schema = store.SCHEMA
    for old, new in (("  findings_subtopic_id INTEGER REFERENCES subtopics(id),\n", ""),
                     ("  comparison TEXT,\n  mode TEXT NOT NULL DEFAULT 'questions'\n)", "  comparison TEXT\n)"),
                     (FINDINGS_NEW, "  version TEXT NOT NULL,\n  UNIQUE(study_id, position)")):
        assert old in schema
        schema = schema.replace(old, new)
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    assert "mode" not in _cols(conn, "questions") and "by_owner" not in _cols(conn, "findings")
    assert "findings_subtopic_id" not in _cols(conn, "studies")
    conn.execute("INSERT INTO subtopics (id, slug, name, status, created_at) VALUES (1, 's', 'S', 'seeded', 'x')")
    conn.execute("INSERT INTO questions (id, subtopic_id, text, measure, created_at) VALUES (1, 1, 't', 'm', 'x')")
    conn.execute("INSERT INTO studies (id, canonical_name, created_at) VALUES (1, 'a', 'x')")
    conn.execute("INSERT INTO items (id, url, source_name, source_type, fetched_at) "
                 "VALUES (1, 'http://x/1', 'src', 'rss', 'x')")
    conn.execute("INSERT INTO findings (study_id, item_id, position, text, quote, quote_checked, version) "
                 "VALUES (1, 1, 1, 't', 'q', 1, 'findings-1')")
    conn.execute("PRAGMA user_version = 16")
    conn.commit()
    conn.close()


def test_step17_on_a_step16_database(tmp_path):
    path = tmp_path / "echo.db"
    _version16(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION == 18
    f = dict(conn.execute("SELECT * FROM findings").fetchone())
    assert f["question_id"] is None and f["direction"] is None and f["grouped_version"] is None
    assert f["by_owner"] == 0
    assert conn.execute("SELECT mode FROM questions").fetchone()[0] == "questions"
    assert conn.execute("SELECT findings_subtopic_id FROM studies").fetchone()[0] is None
    conn.close()
    again = store.connect(path)
    assert again.execute("PRAGMA user_version").fetchone()[0] == 18
    assert again.execute("SELECT mode FROM questions").fetchone()[0] == "questions"
    assert again.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
    again.close()


def test_fresh_database_has_step17_columns(tmp_path):
    conn = store.connect(tmp_path / "echo.db")
    assert {"question_id", "direction", "grouped_version", "by_owner"} <= _cols(conn, "findings")
    assert "mode" in _cols(conn, "questions") and "findings_subtopic_id" in _cols(conn, "studies")
    conn.close()
