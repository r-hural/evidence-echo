"""Schema step 11: the metrics and split tables and studies.metric_id; nothing is filled."""
import sqlite3

from evidence_echo.core import store

TABLES = {"metrics", "split_proposals", "split_groups", "split_moves"}


def _version10(path):
    """A database as step 10 left it: the current schema minus the step 11 tables and column."""
    schema = store.SCHEMA.replace(",\n  metric_id INTEGER REFERENCES metrics(id)\n);", "\n);")
    assert schema != store.SCHEMA
    for ddl in store.STEP11_DDL:
        schema = schema.replace(ddl + ";\n", "")
    assert "CREATE TABLE IF NOT EXISTS metrics" not in schema
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("""INSERT INTO studies (id, canonical_name, created_at, metric_code) VALUES
                    (1, 'a', 'x', 'traffic'), (2, 'b', 'x', 'other'), (3, 'c', 'x', NULL)""")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_step11_adds_tables_and_column(tmp_path):
    path = tmp_path / "echo.db"
    _version10(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    assert TABLES <= _tables(conn)
    rows = conn.execute("SELECT id, metric_id, metric_code FROM studies ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(1, None, "traffic"), (2, None, "other"), (3, None, None)]
    assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 0
    before = [tuple(r) for r in conn.execute("SELECT * FROM studies ORDER BY id")]
    conn.close()
    again = store.connect(path)
    assert [tuple(r) for r in again.execute("SELECT * FROM studies ORDER BY id")] == before
    assert again.execute("PRAGMA user_version").fetchone()[0] == 13
    again.close()


def test_fresh_database_has_step11_schema(tmp_path):
    conn = store.connect(tmp_path / "echo.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION == 13
    assert TABLES <= _tables(conn)
    assert "metric_id" in {r["name"] for r in conn.execute("PRAGMA table_info(studies)")}
    assert {r["name"] for r in conn.execute("PRAGMA table_info(metrics)")} == {
        "id", "slug", "name", "status", "merged_into", "position", "created_at"}
    assert {r["name"] for r in conn.execute("PRAGMA table_info(split_proposals)")} == {
        "id", "target", "source_id", "studies_at", "status", "source_choice", "created_at", "decided_at"}
    assert {r["name"] for r in conn.execute("PRAGMA table_info(split_groups)")} == {
        "id", "proposal_id", "mode", "dest_id", "status"}
    assert {r["name"] for r in conn.execute("PRAGMA table_info(split_moves)")} == {
        "group_id", "question_id", "study_id"}
    conn.close()
