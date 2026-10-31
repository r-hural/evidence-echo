import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from evidence_echo import query
from evidence_echo.core import store
from tests.evidence_fixtures import add_study, seed_topic


@pytest.fixture
def conn(tmp_path):
    path = seed_topic(tmp_path / "data", "seo")
    with closing(store.connect(path)) as c:
        yield c


def test_words_stop_words_and_stemming():
    got = query.words("Do the rankings of studies change?")
    assert "the" not in got and "do" not in got
    assert "rank" in got and "study" in got
    assert query.words("changes") == query.words("changed") == query.words("change")


def test_words_keep_non_ascii_exact():
    got = query.words("Größere Bilder übersetzen")
    assert "größere" in got and "übersetzen" in got


def test_rank_finds_entity_alias(conn):
    a = add_study(conn, "Overview study", "clicks fell", entities=["Google AI Overviews"])
    add_study(conn, "Other study", "titles")
    store.alias_entity(conn, "google-ai-overviews", "AIO")
    bundles = query.study_bundles(conn, "questions")
    ranked = query.rank(bundles, query.words("AIO click share"))
    assert ranked and ranked[0][1] == a


def test_rank_finds_finding_text(conn):
    b = add_study(conn, "Training trial", "soreness", finding_texts=["strength not preserved"])
    add_study(conn, "Other trial", "soreness")
    ranked = query.rank(query.study_bundles(conn, "findings"), query.words("strength preserved"))
    assert [sid for _, sid in ranked] == [b]


def test_rank_needs_two_words_for_long_questions(conn):
    add_study(conn, "Title study", "title changes")
    ranked = query.rank(query.study_bundles(conn, "questions"),
                        query.words("hreflang canonical redirect title"))
    assert ranked == []


def test_empty_question_matches_nothing(conn):
    add_study(conn, "Title study", "title changes")
    assert query.words("the of ?!") == []
    assert query.words("") == []
    assert query.words("🙂🙂") == []
    assert query.rank(query.study_bundles(conn, "questions"), []) == []


def test_open_ro_refuses_writes(tmp_path):
    path = seed_topic(tmp_path / "Daten mit Ümlaut und Leer", "seo")
    with closing(query.open_ro(path)) as ro:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO runs (started_at, stage) VALUES ('x', 'y')")


def test_db_status(tmp_path):
    data = tmp_path / "data"
    path = seed_topic(data, "seo")
    tp = SimpleNamespace(error=None, db_path=path)
    assert query.db_status(tp) is None
    assert query.db_status(SimpleNamespace(error=None, db_path=tmp_path / "none.db")) == "no data yet: run a report"
    assert query.db_status(SimpleNamespace(error="cro/topic.json: bad", db_path=path)) == "cro/topic.json: bad"
    for version, expected in ((13, "needs_upgrade: run any Evidence Echo report"),
                              (store.LATEST_VERSION + 1, "newer than this plugin: update Evidence Echo")):
        with closing(sqlite3.connect(path)) as raw:
            raw.execute(f"PRAGMA user_version = {version}")
        assert query.db_status(tp) == expected


def test_stem_meets_singular_and_plural_of_short_e_words():
    for single, plural in (("site", "sites"), ("page", "pages"), ("rule", "rules")):
        assert query.words(single) == query.words(plural)


def test_list_topics_survives_a_corrupt_database(tmp_path):
    from evidence_echo.core import config
    path = seed_topic(tmp_path / "data", "seo")
    path.write_bytes(b"this is not a database" * 100)
    import os
    os.environ["EV_ECHO_DATA_DIR"] = str(tmp_path / "data")
    try:
        rows = query.list_topics(config.read_paths())
    finally:
        del os.environ["EV_ECHO_DATA_DIR"]
    assert rows[0]["status"].startswith("cannot be read right now")


def test_words_keep_a_b_as_one_word():
    assert query.words("A/B test") == query.words("a-b tests") == query.words("AB test") == ["ab", "test"]
