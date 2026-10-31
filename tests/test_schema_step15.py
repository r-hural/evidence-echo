"""Schema step 15: grading facts, raw tiers, cap reasons and rule stamps; nothing is filled."""
import sqlite3

from evidence_echo.core import store

ITEM_COLS = {"study_type", "peer_review", "text_basis", "cap_reasons", "graded_rules", "caps_hash",
             "judge_version", "verify_version", "judge_tier_raw", "skeptic_tier_raw", "ruled_tier"}
STUDY_COLS = {"study_type", "peer_review", "text_basis"}


def _version14(path):
    """A database as step 14 left it: the current schema minus the step 15 columns."""
    schema = store.SCHEMA
    for name, decl in store.STEP15_ITEM_COLUMNS:
        line = f"  {name} {decl}"
        assert line in schema
    lines = [ln for ln in schema.split("\n")
             if ln.strip().rstrip(",") not in {f"{n} {d}" for n, d in store.STEP15_ITEM_COLUMNS}]
    schema = "\n".join(lines)
    schema = schema.replace("rejected_at TEXT,\n);", "rejected_at TEXT\n);")
    schema = schema.replace("metric_id INTEGER REFERENCES metrics(id),\n);",
                            "metric_id INTEGER REFERENCES metrics(id)\n);")
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO studies (id, canonical_name, created_at) VALUES (1, 'a', 'x')")
    conn.execute("INSERT INTO items (id, url, source_name, source_type, fetched_at, evidence_tier) "
                 "VALUES (1, 'http://x/1', 'src', 'rss', 'x', 'B')")
    conn.execute("PRAGMA user_version = 14")
    conn.commit()
    conn.close()


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_step15_on_a_step14_database(tmp_path):
    path = tmp_path / "echo.db"
    _version14(path)
    raw = sqlite3.connect(path)
    assert not ITEM_COLS & _cols(raw, "items") and not STUDY_COLS & _cols(raw, "studies")
    raw.close()
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert ITEM_COLS <= _cols(conn, "items") and STUDY_COLS <= _cols(conn, "studies")
    row = conn.execute("SELECT * FROM items WHERE id=1").fetchone()
    assert all(row[c] is None for c in ITEM_COLS) and row["evidence_tier"] == "B"
    srow = conn.execute("SELECT * FROM studies WHERE id=1").fetchone()
    assert all(srow[c] is None for c in STUDY_COLS) and srow["canonical_name"] == "a"
    conn.close()
    again = store.connect(path)
    assert again.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    again.close()


def test_fresh_database_has_step15_columns(tmp_path):
    conn = store.connect(tmp_path / "echo.db")
    assert store.LATEST_VERSION == 18
    assert ITEM_COLS <= _cols(conn, "items") and STUDY_COLS <= _cols(conn, "studies")
    conn.close()
