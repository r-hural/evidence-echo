"""Schema step 18: the study_pairs table of the duplicate check; nothing is filled."""
import sqlite3

from evidence_echo.core import store


def test_step18_on_a_step17_database(tmp_path):
    path = tmp_path / "echo.db"
    store.connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE study_pairs")
    raw.execute("PRAGMA user_version = 17")
    raw.commit()
    raw.close()
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION == 18
    assert store.pair_decisions(conn) == {}
    store.save_pair_check(conn, 7, 3, "different", "n", "v")
    assert list(store.pair_decisions(conn)) == [(3, 7)]   # stored lower id first
    conn.close()
