import json
import sqlite3

import pytest

from evidence_echo.core import store

V05_SCHEMA = """
CREATE TABLE items (id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, source_name TEXT NOT NULL,
  source_type TEXT NOT NULL, title TEXT DEFAULT '', excerpt TEXT DEFAULT '', full_text TEXT,
  published TEXT, fetched_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'fetched',
  triage_pass INTEGER, triage_reason TEXT, verdict_pass INTEGER, evidence_tier TEXT, facts TEXT,
  primary_url TEXT, skeptic_agrees INTEGER, skeptic_tier TEXT, skeptic_note TEXT,
  disputed INTEGER NOT NULL DEFAULT 0, study_id INTEGER);
CREATE TABLE source_stats (source_name TEXT PRIMARY KEY, type TEXT NOT NULL, last_run TEXT,
  found INTEGER DEFAULT 0, matched INTEGER DEFAULT 0, last_error TEXT);
INSERT INTO items (url, source_name, source_type, excerpt, full_text, fetched_at) VALUES
  ('http://e.com/a?utm_source=x', 'Blog', 'rss', 'ex', 'long text', '2026-10-01'),
  ('https://e.com/a', 'Blog', 'rss', 'ex', 'ex', '2026-10-02'),
  ('https://www.linkedin.com/posts/p1', 'Lily', 'linkedin', 'post', 'post2', '2026-10-03');
INSERT INTO source_stats VALUES ('Blog', 'rss', '2026-10-02', 3, 0, NULL),
                                ('Dead', 'rss', '2026-10-02', 0, 0, 'HTTP 404');
"""


def _v05(path):
    conn = sqlite3.connect(path)
    conn.executescript(V05_SCHEMA)
    conn.close()


def test_migration_step1(tmp_path):
    path = tmp_path / "echo.db"
    _v05(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    rows = [dict(r) for r in conn.execute("SELECT * FROM items ORDER BY id")]
    assert [r["canonical_url"] for r in rows] == [
        "https://e.com/a", None, "https://www.linkedin.com/posts/p1"]   # collision: lower id keeps it
    assert [r["text_source"] for r in rows] == ["page", "excerpt", "feed"]
    states = {s["name"]: s for s in store.all_source_states(conn)}
    assert states["Blog"]["consecutive_errors"] == 0
    assert states["Dead"]["consecutive_errors"] == 1 and states["Dead"]["last_error"] == "HTTP 404"
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_stats'").fetchone()
    conn.close()
    store.connect(path).close()   # second open: nothing left to migrate


def test_failed_migration_leaves_file_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "echo.db"
    _v05(path)
    before = path.read_bytes()

    def boom(conn):
        conn.execute("ALTER TABLE items ADD COLUMN canonical_url TEXT")
        raise RuntimeError("disk on fire")
    monkeypatch.setitem(store.STEPS, 1, boom)
    with pytest.raises(store.MigrationError, match="schema version 1"):
        store.connect(path)
    assert path.read_bytes() == before


def test_new_database_has_latest_schema(tmp_path):
    conn = store.connect(tmp_path / "new.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    assert {"canonical_url", "content_hash", "text_source", "fetch_status", "date_unknown"} <= cols
    assert {"first_study_id", "match_reason"} <= cols


def _newer(path):
    store.connect(path).close()
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {store.LATEST_VERSION + 1}")
    conn.close()


def test_newer_database_is_refused_not_restamped(tmp_path):
    path = tmp_path / "echo.db"
    _newer(path)
    with pytest.raises(store.MigrationError, match="newer"):
        store.connect(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION + 1
    conn.close()


def test_newer_database_cli_exit_1(tmp_path, monkeypatch, capsys):
    from evidence_echo import api, cli
    from evidence_echo.core import config
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    ws = config.workspace()
    api.topic_add(ws, "t", None, "T", "d", "s")
    _newer(ws.topics_dir / "t" / "echo.db")
    assert cli.main(["--topic", "t", "sources", "list"]) == 1
    assert "newer" in capsys.readouterr().err


V06_EXTRA = """
CREATE TABLE studies (id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL, what_was_tested TEXT,
  findings TEXT, evidence_tier TEXT, research_timeframe TEXT, sample_scale TEXT, has_controls INTEGER,
  methodology_stated INTEGER, duration TEXT, entities TEXT, primary_url TEXT, created_at TEXT NOT NULL);
INSERT INTO studies (id, canonical_name, entities, primary_url, created_at) VALUES
  (1, 'SO survey', '{"key_numbers": ["44%"]}', 'https://survey.stackoverflow.co/2025/?utm_source=x', '2026-10-01'),
  (2, 'Title tags', NULL, NULL, '2026-10-02');
"""


def test_migration_step2_from_06(tmp_path, monkeypatch):
    path = tmp_path / "echo.db"
    _v05(path)
    monkeypatch.setattr(store, "LATEST_VERSION", 1)          # open as 0.6 would: step 1 only
    store.connect(path).close()
    monkeypatch.undo()
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER IF EXISTS items_rejected_at")   # a step-1-only database has no rejected_at column
    conn.execute("DROP TABLE studies")                        # replace with the exact 0.6 shape
    conn.executescript(V06_EXTRA)
    conn.execute("""UPDATE items SET state='judged', study_id=1, facts=?
                    WHERE id=1""", ('{"entities": {"key_numbers": ["79%", "2025"]}}',))
    conn.execute("UPDATE items SET state='verified', study_id=2 WHERE id=3")
    conn.commit()
    conn.close()

    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    s1 = store.get_study(conn, 1)
    assert s1["primary_canonical"] == "https://survey.stackoverflow.co/2025/"
    assert json.loads(s1["fingerprint"]) == ["44%", "79%"]
    assert store.get_study(conn, 2)["primary_canonical"] is None
    items = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM items")}
    assert items[1]["state"] == "matched" and items[1]["first_study_id"] == 1
    assert items[3]["state"] == "verified" and items[3]["first_study_id"] == 2
    assert conn.execute("SELECT COUNT(*) FROM studies WHERE merged_into IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='series'").fetchone()
    conn.close()
    store.connect(path).close()                               # second open: nothing to do


def test_failed_step2_leaves_file_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "echo.db"
    _v05(path)
    monkeypatch.setattr(store, "LATEST_VERSION", 1)
    store.connect(path).close()
    monkeypatch.undo()
    before = path.read_bytes()

    def boom(conn):
        conn.execute("ALTER TABLE studies ADD COLUMN series_id INTEGER")
        raise RuntimeError("disk on fire")
    monkeypatch.setitem(store.STEPS, 2, boom)
    with pytest.raises(store.MigrationError, match="schema version 2"):
        store.connect(path)
    assert path.read_bytes() == before


def test_migration_step2_tolerates_malformed_json(tmp_path, monkeypatch):
    path = tmp_path / "echo.db"
    _v05(path)
    monkeypatch.setattr(store, "LATEST_VERSION", 1)
    store.connect(path).close()
    monkeypatch.undo()
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER IF EXISTS items_rejected_at")   # a step-1-only database has no rejected_at column
    conn.execute("DROP TABLE studies")
    conn.executescript(V06_EXTRA)
    conn.execute("UPDATE studies SET entities='{not json' WHERE id=1")
    conn.execute("UPDATE studies SET entities='[1, 2]' WHERE id=2")
    conn.execute("UPDATE items SET state='judged', study_id=1, facts='garbage' WHERE id=1")
    conn.execute("UPDATE items SET state='verified', study_id=2, facts=? WHERE id=3",
                 ('{"entities": [1]}',))
    conn.execute("UPDATE items SET state='judged', study_id=2, facts=? WHERE id=2",
                 ('{"entities": {"key_numbers": "44%"}}',))
    conn.commit()
    conn.close()

    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    for sid in (1, 2):
        study = store.get_study(conn, sid)
        assert study["series_id"] is None and json.loads(study["fingerprint"]) == []
    states = {r["id"]: (r["state"], r["first_study_id"]) for r in conn.execute("SELECT * FROM items")}
    assert states[1] == ("matched", 1) and states[2] == ("matched", 2) and states[3] == ("verified", 2)
    conn.close()


def _version8(path):
    """A database as step 8 left it: the current schema minus the step 9 additions."""
    import re
    schema = store.SCHEMA.replace(store.QUESTIONS_DDL + ";\n", "")
    schema = re.sub(r",\n  question_id INTEGER[^;]*?question_by_owner INTEGER NOT NULL DEFAULT 0", "", schema)
    schema = schema.replace(store.PAIR_EXPLANATIONS_DDL, store._PAIR_EXPLANATIONS_V8_DDL)
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO studies (id, canonical_name, created_at) VALUES (1, 'a', 'x'), (2, 'b', 'x')")
    conn.execute("""INSERT INTO pair_explanations (study_a, study_b, metric, status, inputs_hash, created_at)
                    VALUES (1, 2, 'clicks', 'agreed', 'h', 'x')""")
    conn.execute("PRAGMA user_version = 8")
    conn.commit()
    conn.close()


def test_step9_adds_questions_and_keeps_old_explanations(tmp_path):
    path = tmp_path / "echo.db"
    _version8(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    row = dict(conn.execute("SELECT * FROM pair_explanations").fetchone())
    assert row["question_id"] is None and row["metric"] == "clicks" and row["status"] == "agreed"
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(studies)")}
    assert {"question_id", "question_direction", "grouped_version", "question_by_owner"} <= cols
    subtopic = store._insert_subtopic(conn, "T", "seeded")
    qid = store.create_question(conn, subtopic, "Q?", "m")
    store.save_pair_explanation(conn, study_a=1, study_b=2, subtopic_id=None, status="agreed", reason="r",
                                quote_a="a", quote_b="b", note="n", inputs_hash="h2",
                                prompt_version="relate-2", question_id=qid)
    assert len(store.pair_explanations(conn)) == 2
    conn.close()
    store.connect(path).close()   # second open: nothing left to migrate
