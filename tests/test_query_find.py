import sqlite3
from contextlib import closing

import pytest

from evidence_echo import query
from evidence_echo.core import config, knowledge, store
from evidence_echo.core.settings import ConfigError
from tests.evidence_fixtures import add_study, seed_topic


def read(data_dir, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data_dir))
    return config.read_paths()


def test_find_ranks_and_limits(tmp_path, monkeypatch):
    data = tmp_path / "data"
    with closing(store.connect(seed_topic(data, "seo"))) as conn:
        for n in range(12):
            add_study(conn, f"Widget study {n}", "widget clicks rose")
    paths = read(data, monkeypatch)
    out = query.find_evidence(paths, "widget clicks", limit=3)
    assert len(out["studies"]) == 3 and out["coverage"]["studies"] == 12
    assert len(query.find_evidence(paths, "widget clicks", limit=0)["studies"]) == 1
    assert len(query.find_evidence(paths, "widget clicks", limit=99)["studies"]) == 12
    assert out["topics_searched"] == ["seo"] and out["skipped"] == []


def study(tier, by=None, n=[0]):
    n[0] += 1
    return {"id": n[0], "tier": tier, "conducted_by": by}


def test_coverage_thin_rule():
    assert query.coverage([study("A", "Org")])["thin"] is True
    two = query.coverage([study("B", "One"), study("B", "Two")])
    assert two["thin"] is False and two["note"] == "2 studies, best grade B" and two["independent"] == 2
    assert query.coverage([study("A", "Same"), study("A", " same ")])["thin"] is True
    assert query.coverage([study("C", "One"), study("C", "Two")])["thin"] is True
    none = query.coverage([])
    assert none["note"] == "no studies found" and none["thin"] is True and none["studies"] == 0
    assert query.coverage([study("B")])["note"] == "1 study, best grade B"
    assert query.coverage([study("B"), study("B")])["independent"] == 2     # no organisation: each alone
    assert query.coverage([], searchable=False)["note"] == "no searchable words in the question"


def test_find_empty_question(tmp_path, monkeypatch):
    data = tmp_path / "data"
    with closing(store.connect(seed_topic(data, "seo"))) as conn:
        add_study(conn, "Widget study", "widget clicks rose")
    out = query.find_evidence(read(data, monkeypatch), "the of ?!")
    assert out["studies"] == [] and out["coverage"]["note"] == "no searchable words in the question"


def test_find_all_topics_and_skipped(tmp_path, monkeypatch):
    data = tmp_path / "data"
    with closing(store.connect(seed_topic(data, "seo"))) as conn:
        add_study(conn, "Widget study", "widget clicks rose")
    old = seed_topic(data, "old")
    with closing(sqlite3.connect(old)) as raw:
        raw.execute("PRAGMA user_version = 13")
    before = old.read_bytes()
    out = query.find_evidence(read(data, monkeypatch), "widget clicks")
    assert out["topics_searched"] == ["seo"]
    assert out["skipped"] == [{"topic": "old", "status": "needs_upgrade: run any Evidence Echo report"}]
    assert old.read_bytes() == before
    with closing(sqlite3.connect(old)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 13


def test_find_locked_topic_is_skipped(tmp_path, monkeypatch):
    data = tmp_path / "data"
    with closing(store.connect(seed_topic(data, "seo"))) as conn:
        add_study(conn, "Widget study", "widget clicks rose")
    cro = seed_topic(data, "cro")
    with closing(store.connect(cro)) as conn:
        add_study(conn, "Widget other", "widget clicks rose")
    monkeypatch.setattr(query, "BUSY_TIMEOUT", 0.1)
    paths = read(data, monkeypatch)
    locker = sqlite3.connect(cro, isolation_level=None)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        out = query.find_evidence(paths, "widget clicks")
    finally:
        locker.close()
    assert out["topics_searched"] == ["seo"] and len(out["studies"]) == 1
    assert [s["topic"] for s in out["skipped"]] == ["cro"] and out["skipped"][0]["status"]


def test_find_unknown_topic(tmp_path, monkeypatch):
    data = tmp_path / "data"
    seed_topic(data, "seo")
    with pytest.raises(ConfigError, match="Unknown topic 'nope'.*seo"):
        query.find_evidence(read(data, monkeypatch), "widget", topic="nope")


def test_find_questions_block(tmp_path, monkeypatch):
    data = tmp_path / "data"
    with closing(store.connect(seed_topic(data, "seo"))) as conn:
        a = add_study(conn, "Widget A", "widget clicks rose", conducted_by="Org One", question="Do widgets help?",
                      subtopic="title-tags")
        b = add_study(conn, "Widget B", "widget clicks rose", conducted_by="Org Two", question="Do widgets help?",
                      subtopic="title-tags")
    seed_topic(data, "quotes-topic", mode="quotes")
    with closing(store.connect(data / "topics" / "quotes-topic" / "echo.db")) as conn:
        add_study(conn, "Widget C", "widget clicks rose")
    out = query.find_evidence(read(data, monkeypatch), "widget clicks")
    seo_questions = [q for q in out["questions"] if q["topic"] == "seo"]
    assert len(seo_questions) == 1 and seo_questions[0]["studies"] == [f"s{a}", f"s{b}"]
    assert seo_questions[0]["label"] == "consistent" and seo_questions[0]["text"] == "Do widgets help?"
    assert [q for q in out["questions"] if q["topic"] == "quotes-topic"] == []


def test_get_study_details_and_excerpt(tmp_path, monkeypatch):
    data = tmp_path / "data"
    quote = "widget clicks rose by five percent"
    text = ("Intro words. " * 80) + quote + (" Closing words. " * 80)
    with closing(store.connect(seed_topic(data, "seo"))) as conn:
        a = add_study(conn, "Widget A", "widget clicks rose", conducted_by="Org One", question="Do widgets help?",
                      subtopic="title-tags", headline_quote=quote, full_text=text, direction="up")
        b = add_study(conn, "Widget B", "widget clicks fell", conducted_by="Org Two", question="Do widgets help?",
                      subtopic="title-tags", direction="down")
        c = add_study(conn, "Widget C", "no text", headline_quote="absent from the article text", full_text="x")
        item = conn.execute("SELECT id FROM items WHERE study_id=?", (a,)).fetchone()[0]
        conn.execute("""INSERT INTO primary_checks (item_id, primary_url, verdict, note, status, checked_at)
                        VALUES (?, 'https://p.example/x', 'faithful', 'matches', 'agreed', 'now')""", (item,))
        conn.commit()
        group = next(g for sub in knowledge.evidence(conn) for g in sub.questions)
        state = knowledge.explain_state(conn, group)
        store.save_question_explanation(
            conn, question_id=group.question_id, status="agreed", summary="Different populations.",
            lines=[{"study": a, "reason": "different_population", "quote": "q", "note": "n"}],
            main_result=state.main_result, inputs_hash=state.inputs_hash, prompt_version="t")
    paths = read(data, monkeypatch)
    got = query.get_study(paths, "seo", f"s{a}")
    assert got["id"] == f"s{a}" and len(got["items"]) == 1 and got["items"][0]["url"].startswith("https://")
    assert got["primary"] == {"status": "agreed", "verdict": "faithful", "note": "matches"}
    assert got["explanation_lines"] == [{"question": "Do widgets help?", "reason": "different_population",
                                         "quote": "q", "note": "n"}]
    assert len(got["excerpt"]) <= 600 and quote in got["excerpt"]
    assert query.get_study(paths, "seo", a)["id"] == query.get_study(paths, "seo", str(a))["id"]
    assert query.get_study(paths, "seo", f"s{c}")["excerpt"] is None
    assert query.get_study(paths, "seo", f"s{b}")["explanation_lines"] == []
    with pytest.raises(ConfigError):
        query.get_study(paths, "seo", "s99")
    with pytest.raises(ConfigError):
        query.get_study(paths, "nope", "s1")
