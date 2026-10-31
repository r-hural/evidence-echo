"""Schema step 13: the entities tables, studies.entities_checked and items.entity_names; nothing is filled."""
import sqlite3

from evidence_echo.core import store

TABLES = {"entities", "study_entities"}


def _version12(path):
    """A database as step 12 left it: the current schema minus the step 13 tables and columns."""
    schema = store.SCHEMA
    for ddl in (store.ENTITIES_DDL, store.STUDY_ENTITIES_DDL):
        schema = schema.replace(ddl + ";\n", "")
    for line in ("  entity_names TEXT,\n", "  entities_checked INTEGER,\n"):
        assert line in schema
        schema = schema.replace(line, "")
    assert "CREATE TABLE IF NOT EXISTS entities" not in schema
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO studies (id, canonical_name, created_at) VALUES (1, 'a', 'x')")
    conn.execute("INSERT INTO items (id, url, source_name, source_type, fetched_at) "
                 "VALUES (1, 'http://x/1', 'src', 'rss', 'x')")
    conn.execute("PRAGMA user_version = 12")
    conn.commit()
    conn.close()


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_step13_on_a_step12_database(tmp_path):
    path = tmp_path / "echo.db"
    _version12(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert TABLES <= _tables(conn)
    assert conn.execute("SELECT entities_checked FROM studies").fetchone()[0] is None
    assert conn.execute("SELECT entity_names FROM items").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM study_entities").fetchone()[0] == 0
    conn.close()
    again = store.connect(path)
    assert again.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    again.close()


def test_fresh_database_has_entity_schema(tmp_path):
    conn = store.connect(tmp_path / "echo.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION == 18
    assert TABLES <= _tables(conn)
    assert {r["name"] for r in conn.execute("PRAGMA table_info(entities)")} == {
        "id", "slug", "name", "status", "merged_into", "created_at"}
    assert {r["name"] for r in conn.execute("PRAGMA table_info(study_entities)")} == {
        "study_id", "entity_id", "position"}
    assert "entities_checked" in {r["name"] for r in conn.execute("PRAGMA table_info(studies)")}
    assert "entity_names" in {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    conn.close()
