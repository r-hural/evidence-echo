"""Glossary rename: schema step 7 - subtopics tables/columns and studies.match_keys."""
import json

from evidence_echo.core import store
from legacy_schema import to_legacy_names


def _as_v6(path):
    """A real schema-6 database: today's schema under the old names, user_version 6, with rows."""
    conn = store.connect(path)
    to_legacy_names(conn)
    conn.execute("INSERT INTO themes (id, slug, name, status, merged_into, created_at, position) "
                 "VALUES (1, 'links', 'Links', 'seeded', NULL, 'x', 0)")
    conn.execute("INSERT INTO themes (id, slug, name, status, merged_into, created_at, position) "
                 "VALUES (2, 'anchors', 'Anchors', 'confirmed', 1, 'x', NULL)")
    conn.execute("INSERT INTO themes (id, slug, name, status, merged_into, created_at, position) "
                 "VALUES (3, 'old', 'Old', 'retired', NULL, 'x', NULL)")
    conn.execute("INSERT INTO themes (id, slug, name, status, merged_into, created_at, position) "
                 "VALUES (4, 'idea', 'Idea', 'proposed', NULL, 'x', NULL)")
    conn.execute("INSERT INTO studies (id, canonical_name, created_at, theme_id, entities) "
                 "VALUES (1, 's', 'x', 2, '{\"key_numbers\": [\"4.2%\"]}')")
    conn.execute("INSERT INTO studies (id, canonical_name, created_at, theme_id, entities) "
                 "VALUES (2, 't', 'x', NULL, NULL)")
    facts = {"entities": {"key_numbers": ["4.2%"]}, "x": 1}
    for i, (f, choice) in enumerate(((json.dumps(facts), '{"slug": "links"}'), (None, None),
                                     ("not json", None), ('{"x": 1}', None)), start=1):
        conn.execute("INSERT INTO items (id, url, source_name, source_type, title, state, fetched_at, "
                     "facts, theme_choice) VALUES (?,?,?,?,?,?,?,?,?)",
                     (i, f"https://e.com/{i}", "s", "rss", "t", "judged", "x", f, choice))
    conn.execute("INSERT INTO pair_explanations (study_a, study_b, theme_id, metric, status, "
                 "inputs_hash, created_at) VALUES (1, 2, 2, 'ctr', 'agreed', 'h', 'x')")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()


def test_latest_version_is_14():
    assert store.LATEST_VERSION == 18 and 17 in store.STEPS


def test_step7_renames_and_keeps_rows(tmp_path):
    path = tmp_path / "echo.db"
    _as_v6(path)
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    rows = [tuple(r) for r in conn.execute(
        "SELECT id, slug, status, merged_into, position FROM subtopics ORDER BY id")]
    assert rows == [(1, "links", "seeded", None, 0), (2, "anchors", "confirmed", 1, None),
                    (3, "old", "retired", None, None), (4, "idea", "proposed", None, None)]
    study = conn.execute("SELECT * FROM studies WHERE id=1").fetchone()
    assert study["subtopic_id"] == 2 and study["match_keys"] == '{"key_numbers": ["4.2%"]}'
    item = conn.execute("SELECT * FROM items WHERE id=1").fetchone()
    assert item["subtopic_choice"] == '{"slug": "links"}'
    assert json.loads(item["facts"]) == {"match_keys": {"key_numbers": ["4.2%"]}, "x": 1}
    pair = conn.execute("SELECT * FROM pair_explanations").fetchone()
    assert pair["subtopic_id"] == 2
    conn.close()


def test_step7_foreign_keys_point_at_subtopics(tmp_path):
    path = tmp_path / "echo.db"
    _as_v6(path)
    conn = store.connect(path)
    # SQLite quotes a renamed table's name in the references it rewrites
    sql = {r[0]: (r[1] or "").replace('"', "")
           for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}
    assert "REFERENCES subtopics" in sql["studies"] and "REFERENCES subtopics" in sql["pair_explanations"]
    assert "themes" not in " ".join(sql.values())
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()


def test_step7_leaves_odd_facts_alone(tmp_path):
    path = tmp_path / "echo.db"
    _as_v6(path)
    conn = store.connect(path)
    facts = {r[0]: r[1] for r in conn.execute("SELECT id, facts FROM items")}
    assert facts[2] is None and facts[3] == "not json" and facts[4] == '{"x": 1}'
    conn.close()


def test_step7_is_idempotent(tmp_path):
    path = tmp_path / "echo.db"
    store.connect(path).close()
    conn = store.connect(path)
    conn.execute("BEGIN")
    store._step7(conn)                                  # already renamed: nothing to do, no error
    conn.rollback()
    conn.close()


def test_fresh_database_has_new_names(tmp_path):
    conn = store.connect(tmp_path / "new.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert store._has_table(conn, "subtopics") and not store._has_table(conn, "themes")
    cols = {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            for t in ("studies", "items", "pair_explanations")}
    assert "subtopic_id" in cols["studies"] and "match_keys" in cols["studies"]
    assert "subtopic_choice" in cols["items"] and "subtopic_id" in cols["pair_explanations"]
    conn.close()
