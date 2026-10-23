"""Schema step 5 (SP3b-0): every SP3b table and column, on a new and on a 0.7.2 (version 4) database."""
import sqlite3

import pytest

from evidence_echo.core import store
from tests.test_store_migration import _v05

V4 = """
CREATE TABLE items (id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, source_name TEXT NOT NULL,
  source_type TEXT NOT NULL, title TEXT DEFAULT '', excerpt TEXT DEFAULT '', full_text TEXT,
  published TEXT, fetched_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'fetched',
  triage_pass INTEGER, triage_reason TEXT, verdict_pass INTEGER, evidence_tier TEXT, facts TEXT,
  primary_url TEXT, skeptic_agrees INTEGER, skeptic_tier TEXT, skeptic_note TEXT,
  disputed INTEGER NOT NULL DEFAULT 0, study_id INTEGER, canonical_url TEXT, content_hash TEXT,
  text_source TEXT, fetch_status TEXT, date_unknown INTEGER NOT NULL DEFAULT 0,
  first_study_id INTEGER, match_reason TEXT, quotes TEXT, quote_check TEXT, capped_from TEXT,
  judged_from TEXT, red_flags TEXT, dispute_reason TEXT, provenance TEXT, judge_tier TEXT);
CREATE TABLE studies (id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL, what_was_tested TEXT,
  findings TEXT, evidence_tier TEXT, research_timeframe TEXT, sample_scale TEXT, has_controls INTEGER,
  methodology_stated INTEGER, duration TEXT, entities TEXT, primary_url TEXT, series_id INTEGER,
  editions TEXT, primary_canonical TEXT, fingerprint TEXT, merged_into INTEGER, metric TEXT,
  baseline TEXT, conducted_by TEXT, relation TEXT, conflict_of_interest TEXT,
  created_by_item INTEGER, created_at TEXT NOT NULL);
CREATE TABLE embeddings (item_id INTEGER NOT NULL, model TEXT NOT NULL, vector BLOB NOT NULL,
  PRIMARY KEY (item_id, model));
CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, stage TEXT NOT NULL, detail TEXT);
CREATE TABLE reports (id INTEGER PRIMARY KEY, path TEXT NOT NULL, rendered_at TEXT NOT NULL);
CREATE TABLE report_entries (report_id INTEGER NOT NULL, study_id INTEGER NOT NULL,
  title TEXT NOT NULL, summary TEXT, PRIMARY KEY (report_id, study_id));
CREATE TABLE sources_state (name TEXT PRIMARY KEY, type TEXT NOT NULL, added_at TEXT NOT NULL,
  last_checked TEXT, last_new_item TEXT, items_total INTEGER NOT NULL DEFAULT 0,
  consecutive_errors INTEGER NOT NULL DEFAULT 0, failing_since TEXT, last_error TEXT,
  blocked INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, backfilled_at TEXT, cursor TEXT);
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT NOT NULL, key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL);
CREATE UNIQUE INDEX items_canonical_url ON items(canonical_url);
INSERT INTO studies (id, canonical_name, findings, evidence_tier, metric, conducted_by, created_at)
  VALUES (1, 'Title test', '+4.2% clicks', 'B', 'organic clicks', 'Acme', '2026-10-01');
INSERT INTO items (id, url, canonical_url, source_name, source_type, fetched_at, state, study_id,
                   evidence_tier, red_flags)
  VALUES (7, 'https://e.com/a', 'https://e.com/a', 'Blog', 'rss', '2026-10-01', 'reported', 1,
          'B', '["no_baseline"]');
INSERT INTO reports (id, path, rendered_at) VALUES (1, '/r/old.md', '2026-10-02T08:00:00+00:00');
INSERT INTO report_entries VALUES (1, 1, 'Old title', 'Old summary');
PRAGMA user_version = 4;
"""
NEW_TABLES = {
    "subtopics": ["id", "slug", "name", "status", "merged_into", "created_at"],
    "pair_explanations": ["id", "study_a", "study_b", "subtopic_id", "metric", "reason", "quote_a",
                          "quote_b", "note", "status", "inputs_hash", "prompt_version", "created_at"],
    "primary_sources": ["url", "text", "status", "attempts", "fetched_at", "last_error"],
    "primary_checks": ["item_id", "primary_url", "numbers", "missing", "verdict", "quote_article",
                       "quote_primary", "note", "status", "prompt_version", "checked_at"],
}
NEW_COLUMNS = {"items": ["subtopic_choice", "metric_code", "direction"],
               "studies": ["subtopic_id", "metric_code", "direction"]}
PAIR_INSERT = ("INSERT INTO pair_explanations (study_a, study_b, metric, status, inputs_hash, "
               "created_at) VALUES (?, ?, ?, 'undecided', 'h1', '2026-10-16')")


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _v4(path):
    raw = sqlite3.connect(path)
    raw.executescript(V4)
    raw.close()


def test_latest_version_is_5():
    assert 5 in store.STEPS and store.LATEST_VERSION >= 5


def test_new_database_has_every_sp3b_table_and_column(db):
    for table, cols in NEW_TABLES.items():
        assert [c for c in _columns(db, table) if c in cols] == cols, table
    for table, cols in NEW_COLUMNS.items():
        assert [c for c in _columns(db, table) if c in cols] == cols, table
    assert "metric" in _columns(db, "studies")                 # SP2's free-text metric stays


def test_step5_on_a_version_4_database(tmp_path):
    path = tmp_path / "echo.db"
    _v4(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    for table, cols in NEW_TABLES.items():
        assert [c for c in _columns(conn, table) if c in cols] == cols, table
    for table, cols in NEW_COLUMNS.items():
        assert [c for c in _columns(conn, table) if c in cols] == cols, table
    study = store.get_study(conn, 1)
    assert (study["findings"], study["metric"], study["conducted_by"]) == (
        "+4.2% clicks", "organic clicks", "Acme")
    assert (study["subtopic_id"], study["metric_code"], study["direction"]) == (None, None, None)
    item = store.get_item(conn, 7)
    assert (item["state"], item["study_id"], item["red_flags"]) == ("reported", 1, '["no_baseline"]')
    assert (item["subtopic_choice"], item["metric_code"], item["direction"]) == (None, None, None)
    [entry] = store.report_history(conn)
    assert (entry["title"], entry["summary"]) == ("Old title", "Old summary")
    for table in NEW_TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    conn.close()


def test_migrated_and_new_databases_have_the_same_columns(tmp_path):
    _v4(tmp_path / "old.db")
    old, new = store.connect(tmp_path / "old.db"), store.connect(tmp_path / "new.db")
    for table in ["items", "studies", *NEW_TABLES]:
        assert sorted(_columns(old, table)) == sorted(_columns(new, table)), table
    old.close()
    new.close()


def test_a_05_database_gets_the_sp3b_columns_through_every_step(tmp_path):
    path = tmp_path / "echo.db"
    _v05(path)                                                 # no studies table before step 2
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    for table, cols in NEW_COLUMNS.items():
        assert [c for c in _columns(conn, table) if c in cols] == cols, table
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
    conn.close()


def test_step5_is_idempotent(tmp_path):
    path = tmp_path / "echo.db"
    _v4(path)
    store.connect(path).close()
    store.connect(path).close()                                # second open: nothing to do
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    store._step5(conn)                                         # tables and columns exist: no error
    for table, cols in NEW_COLUMNS.items():
        assert all(_columns(conn, table).count(c) == 1 for c in cols), table
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    conn.close()


def test_a_failed_step5_leaves_version_4(tmp_path, monkeypatch):
    path = tmp_path / "echo.db"
    _v4(path)

    def boom(conn):
        conn.execute(store._LEGACY_THEMES_DDL)
        raise RuntimeError("disk on fire")
    monkeypatch.setitem(store.STEPS, 5, boom)
    with pytest.raises(store.MigrationError, match="schema version 5"):
        store.connect(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='themes'").fetchone()
    conn.close()


def test_pair_explanations_keep_a_below_b_and_one_row_per_question(db):
    db.execute("INSERT INTO studies (id, canonical_name, created_at) VALUES (1, 'a', 'x'), (2, 'b', 'x')")
    db.execute("INSERT INTO subtopics (slug, name, status, created_at) VALUES ('t', 'T', 'seeded', 'x')")
    for n in (1, 2):
        db.execute("INSERT INTO questions (subtopic_id, text, measure, created_at) VALUES (1, ?, 'm', 'x')",
                   (f"q{n}",))
    insert = ("INSERT INTO pair_explanations (study_a, study_b, question_id, status, inputs_hash, "
              "created_at) VALUES (?, ?, ?, 'undecided', 'h1', '2026-10-16')")
    db.execute(insert, (1, 2, 1))
    with pytest.raises(sqlite3.IntegrityError):              # same pair, same question
        db.execute(insert, (1, 2, 1))
    with pytest.raises(sqlite3.IntegrityError):              # a must be the lower study id
        db.execute(insert, (2, 1, 2))
    db.execute(insert, (1, 2, 2))                            # same pair, another question
    assert db.execute("SELECT COUNT(*) FROM pair_explanations").fetchone()[0] == 2


def test_subtopic_slugs_are_unique(db):
    db.execute("INSERT INTO subtopics (slug, name, status, created_at) VALUES ('local', 'Local', 'seeded', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO subtopics (slug, name, status, created_at) "
                   "VALUES ('local', 'Local SEO', 'proposed', 'x')")


def test_primary_sources_url_cannot_be_null(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(primary_sources)")]
    marks = ", ".join("NULL" if c == "url" else "'x'" for c in cols)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f"INSERT INTO primary_sources ({', '.join(cols)}) VALUES ({marks})")
