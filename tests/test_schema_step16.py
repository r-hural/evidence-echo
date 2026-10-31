"""Schema step 16: the findings table, items.findings and studies.findings_version; nothing is filled."""
import sqlite3

from evidence_echo.core import store


def _version15(path):
    """A database as step 15 left it: the current schema minus the step 16 table and columns."""
    schema = store.SCHEMA.replace(store.FINDINGS_DDL + ";\n", "")
    for line in (",\n  findings TEXT\n);", "  findings_version TEXT,\n"):
        assert line in schema
    schema = schema.replace(",\n  findings TEXT\n);", "\n);").replace("  findings_version TEXT,\n", "")
    assert "CREATE TABLE IF NOT EXISTS findings" not in schema
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO studies (id, canonical_name, created_at) VALUES (1, 'a', 'x')")
    conn.execute("INSERT INTO items (id, url, source_name, source_type, fetched_at) "
                 "VALUES (1, 'http://x/1', 'src', 'rss', 'x')")
    conn.execute("PRAGMA user_version = 15")
    conn.commit()
    conn.close()


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_step16_on_a_step15_database(tmp_path):
    path = tmp_path / "echo.db"
    _version15(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert "findings" in _tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    assert conn.execute("SELECT findings FROM items").fetchone()[0] is None
    assert conn.execute("SELECT findings_version FROM studies").fetchone()[0] is None
    conn.close()
    again = store.connect(path)
    assert again.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert again.execute("SELECT COUNT(*) FROM studies").fetchone()[0] == 1
    again.close()


def test_fresh_database_has_findings_schema(tmp_path):
    conn = store.connect(tmp_path / "echo.db")
    assert {r["name"] for r in conn.execute("PRAGMA table_info(findings)")} == {
        "id", "study_id", "item_id", "position", "text", "quote", "quote_checked", "metric_id",
        "measure", "version", "question_id", "direction", "grouped_version", "by_owner"}
    conn.close()
