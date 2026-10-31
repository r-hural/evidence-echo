import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from evidence_echo import cli, search
from evidence_echo.core import config, store, topics
from evidence_echo.core.settings import ConfigError
from tests.evidence_fixtures import seed_topic

NOW = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)
TABLES = ("subtopics", "metrics", "entities", "split_proposals", "split_groups", "split_moves")


def rows(db_path, table):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()


def seeded(workspace):
    """Topic 'seo' with a merged subtopic and metric, an entity with an alias, a rejected entity, a split."""
    path = seed_topic(workspace, "seo")
    with closing(store.connect(path)) as conn:
        a = store.get_subtopic(conn, "title-tags")["id"]
        b = conn.execute("INSERT INTO subtopics (slug, name, status, merged_into, created_at, position) "
                         "VALUES ('old-tags', 'Old tags', 'merged', ?, 'x', 9)", (a,)).lastrowid
        clicks = conn.execute("SELECT id FROM metrics WHERE slug='clicks'").fetchone()[0]
        conn.execute("INSERT INTO metrics (slug, name, status, merged_into, position, created_at) "
                     "VALUES ('ctr-old', 'CTR old', 'merged', ?, 9, 'x')", (clicks,))
        e1 = conn.execute("INSERT INTO entities (slug, name, status, merged_into, created_at) "
                          "VALUES ('google', 'Google', 'confirmed', NULL, 'x')").lastrowid
        conn.execute("INSERT INTO entities (slug, name, status, merged_into, created_at) "
                     "VALUES ('goog', 'Goog', 'merged', ?, 'x')", (e1,))
        conn.execute("INSERT INTO entities (slug, name, status, merged_into, created_at) "
                     "VALUES ('spam', 'Spam', 'rejected', NULL, 'x')")
        p = conn.execute("INSERT INTO split_proposals (target, source_id, studies_at, status, created_at) "
                         "VALUES ('subtopic', ?, 3, 'open', 'x')", (b,)).lastrowid
        g = conn.execute("INSERT INTO split_groups (proposal_id, mode, dest_id, status) "
                         "VALUES (?, 'questions', ?, 'open')", (p, a)).lastrowid
        conn.execute("INSERT INTO split_moves (group_id, question_id, study_id) VALUES (?, 1, 2)", (g,))
        conn.commit()
    return path


def test_run_id_rules():
    assert search.run_id("Do AI Overviews differ by language?", NOW, set()) == \
        "20261026-0905-do-ai-overviews-differ-by-language"
    long = search.run_id("word " * 24, NOW, set())
    slug = long[len("20261026-0905-"):]
    assert 0 < len(slug) <= 40 and not slug.endswith("-")
    for q in ("„?!“ 🙂", "Яндекс"):
        rid = search.run_id(q, NOW, set())
        assert rid == "20261026-0905-search"
    taken = {"20261026-0905-search"}
    second = search.run_id("Яндекс", NOW, taken)
    assert second == "20261026-0905-search-2"
    assert search.run_id("Яндекс", NOW, taken | {second}) == "20261026-0905-search-3"
    for rid in (long, second, search.run_id("x/../y", NOW, set())):
        assert config.RUN_ID_RE.match(rid)


def test_search_dir_refuses_bad_ids(tmp_path):
    for bad in ("../x", "20261026-0905-a/b", ""):
        with pytest.raises(ConfigError):
            config.search_dir(tmp_path, "seo", bad)
    ok = config.search_dir(tmp_path, "seo", "20261026-0905-a")
    assert ok == tmp_path / config.SEARCHES_DIR / "seo" / "20261026-0905-a"


def test_start_seeds_copies(workspace):
    topic_db = seeded(workspace)
    cfg = config.load("seo")
    out = search.start(cfg, "Does x help?", NOW)
    run_dir = config.search_dir(workspace, "seo", out["run"])
    assert out["dir"] == str(run_dir) and out["topic"] == "seo"
    scratch = run_dir / search.SCRATCH_DB
    for table in TABLES:
        assert rows(scratch, table) == rows(topic_db, table), table
    assert len(rows(scratch, "subtopics")) >= 2 and len(rows(scratch, "entities")) == 3
    assert rows(scratch, "items") == []
    meta = search.read_meta(run_dir)
    assert meta["status"] == "open" and meta["question"] == "Does x help?" and meta["topic"] == "seo"
    assert meta["run"] == out["run"] and meta["queries"] == [] and meta["candidates"] == []
    assert meta["summary"] is None and meta["writeups"] == {} and meta["kept"] is None
    # same minute, same question: a distinct folder
    again = search.start(cfg, "Does x help?", NOW)
    assert again["run"] == out["run"] + "-2"


def test_start_leaves_topic_untouched(workspace):
    topic_db = seeded(workspace)
    topic_json = workspace / "topics" / "seo" / "topic.json"
    before = (topic_db.read_bytes(), topic_json.read_bytes())
    out = search.start(config.load("seo"), "q", NOW)
    assert (topic_db.read_bytes(), topic_json.read_bytes()) == before
    ws = config.workspace()
    assert out["run"] not in topics.topic_ids(ws.topics_dir) and "searches" not in topics.topic_ids(ws.topics_dir)


def test_start_validates_question(workspace):
    seeded(workspace)
    cfg = config.load("seo")
    for bad in ("", "   ", "x" * 301):
        with pytest.raises(ConfigError):
            search.start(cfg, bad, NOW)
    assert not (workspace / config.SEARCHES_DIR).exists()


def test_start_refuses_old_topic_db(workspace):
    topic_db = seeded(workspace)
    with closing(sqlite3.connect(topic_db)) as conn:
        conn.execute("PRAGMA user_version = 13")
        conn.commit()
    before = topic_db.read_bytes()
    with pytest.raises(ConfigError, match="run a report"):
        search.start(config.load("seo"), "q", NOW)
    assert topic_db.read_bytes() == before


def test_cli_search_start(workspace, capsys):
    seeded(workspace)
    assert cli.main(["--topic", "seo", "search", "start", "--question", "q x"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["topic"] == "seo" and first["run"].endswith("-q-x")
    assert cli.main(["search", "start", "--question", "q", "--topic", "seo"]) == 0
    assert json.loads(capsys.readouterr().out)["topic"] == "seo"


def test_question_whitespace_collapsed(workspace):
    seeded(workspace)
    out = search.start(config.load("seo"), "  Do title\ntags\t\r\n matter?  ", NOW)
    meta = search.read_meta(config.search_dir(workspace, "seo", out["run"]))
    assert meta["question"] == "Do title tags matter?"
