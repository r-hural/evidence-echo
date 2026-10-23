"""Wave 4a: schema step 6 - headline/data-date columns, classified_version, themes.position."""
import sqlite3

from evidence_echo.core import store
from legacy_schema import to_legacy_names

ITEM_NEW = ["headline", "data_date"]
STUDY_NEW = ["headline_value", "headline_label", "headline_quote", "headline_checked",
             "data_date", "classified_version"]


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _as_v5(path):
    """A real step-5 database: today's schema minus step 6's columns, user_version 5."""
    conn = store.connect(path)
    to_legacy_names(conn)                               # a step-5 database has the old names
    for table, cols in (("items", ITEM_NEW), ("studies", STUDY_NEW), ("themes", ["position"])):
        for col in cols:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    conn.execute("INSERT INTO studies (id, canonical_name, created_at) VALUES (1, 'old', 'x')")
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()


def test_step6_exists():
    assert store.LATEST_VERSION >= 6 and 6 in store.STEPS


def test_new_database_has_the_step6_columns(db):
    assert [c for c in _cols(db, "items") if c in ITEM_NEW] == ITEM_NEW
    assert [c for c in _cols(db, "studies") if c in STUDY_NEW] == STUDY_NEW
    assert "position" in _cols(db, "subtopics")


def test_step6_on_a_version_5_database_keeps_rows(tmp_path):
    path = tmp_path / "echo.db"
    _as_v5(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert store.get_study(conn, 1)["canonical_name"] == "old"
    assert store.get_study(conn, 1)["classified_version"] is None
    old, new = conn, store.connect(tmp_path / "new.db")
    for table in ("items", "studies", "subtopics"):          # step 7 ran after step 6
        assert sorted(_cols(old, table)) == sorted(_cols(new, table)), table
    old.close(); new.close()


def test_step6_is_idempotent(tmp_path):
    path = tmp_path / "echo.db"
    conn = store.connect(path)
    to_legacy_names(conn); conn.commit(); conn.close()
    conn = sqlite3.connect(path)
    store._step6(conn)                                 # columns exist: no error, no duplicates
    assert _cols(conn, "studies").count("data_date") == 1
    conn.close()


def test_headline_columns():
    assert store.headline_columns(None) == (None, None, None, None)
    assert store.headline_columns({"value": "−3 %", "label": "ctr", "quote": "q", "checked": True}) == (
        "−3 %", "ctr", "q", 1)
