import sqlite3

from evidence_echo.core import store

V08_SP2 = """
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
CREATE TABLE reports (id INTEGER PRIMARY KEY, path TEXT NOT NULL, rendered_at TEXT NOT NULL);
CREATE TABLE report_entries (report_id INTEGER NOT NULL, study_id INTEGER NOT NULL,
  title TEXT NOT NULL, PRIMARY KEY (report_id, study_id));
INSERT INTO studies (id, canonical_name, findings, evidence_tier, created_at)
  VALUES (1, 'Old', '+4.2% clicks', 'B', '2026-10-01');
INSERT INTO reports (id, path, rendered_at) VALUES (1, '/r/old.md', '2026-10-02T08:00:00+00:00');
INSERT INTO report_entries (report_id, study_id, title) VALUES (1, 1, 'Old title');
PRAGMA user_version = 3;
"""


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_fresh_database_has_the_summary_column(db):
    assert "summary" in _columns(db, "report_entries")
    assert db.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION


def test_migration_step4_from_08_sp2(tmp_path):
    path = tmp_path / "echo.db"
    raw = sqlite3.connect(path)
    raw.executescript(V08_SP2)
    raw.close()
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert "summary" in _columns(conn, "report_entries")
    [row] = store.report_history(conn)
    assert (row["study_id"], row["title"], row["summary"]) == (1, "Old title", None)
    assert store.get_study(conn, 1)["findings"] == "+4.2% clicks"
    conn.close()
    store.connect(path).close()                       # second open: nothing to do
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert conn.execute("SELECT COUNT(*) FROM report_entries").fetchone()[0] == 1
    conn.close()


def test_step4_is_idempotent(db):
    store._step4(db)                                  # the column exists already: no error
    store._step4(db)
    assert _columns(db, "report_entries").count("summary") == 1


def test_record_report_with_and_without_summary(db):
    a = store.create_study(db, "B", {"what_was_tested": "a"}, None)
    b = store.create_study(db, "C", {"what_was_tested": "b"}, None)
    store.record_report(db, "/r/1.md", [(a, "A title", "A summary"), (b, "B title")])
    rows = store.report_history(db)
    assert [(r["study_id"], r["title"], r["summary"]) for r in rows] == [
        (a, "A title", "A summary"), (b, "B title", None)]
    assert store.latest_report(db)["entries"][0]["title"] == "A title"


def test_report_history_is_oldest_first(db, monkeypatch):
    sid = store.create_study(db, "B", {"what_was_tested": "a"}, None)
    monkeypatch.setattr(store, "_now", lambda: "2026-10-02T00:00:00+00:00")
    store.record_report(db, "/r/2.md", [(sid, "Second", "S2")])
    monkeypatch.setattr(store, "_now", lambda: "2026-10-01T00:00:00+00:00")
    store.record_report(db, "/r/1.md", [(sid, "First", "S1")])
    assert [r["title"] for r in store.report_history(db)] == ["First", "Second"]


def test_study_items_and_series_name(db):
    from tests.conftest import make_item
    sid = store.create_study(db, "B", {"what_was_tested": "a", "series": "Alpha Report"}, None)
    store.insert_items(db, [make_item(url="https://e.com/1"), make_item(url="https://e.com/2")])
    db.execute("UPDATE items SET study_id=?, state='reported' WHERE url='https://e.com/1'", (sid,))
    db.execute("UPDATE items SET study_id=?, state='rejected' WHERE url='https://e.com/2'", (sid,))
    db.commit()
    assert [i["url"] for i in store.study_items(db, sid, ("reported", "verified"))] == ["https://e.com/1"]
    assert store.series_name(db, store.get_study(db, sid)["series_id"]) == "Alpha Report"
    assert store.series_name(db, None) is None and store.series_name(db, 999) is None
