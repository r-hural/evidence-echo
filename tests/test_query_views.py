import json
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit
from urllib.request import url2pathname

from evidence_echo import query
from evidence_echo.core import config, grading, store
from evidence_echo.render import model
from tests.conftest import make_workspace
from tests.evidence_fixtures import add_study, seed_topic


def paths_for(data_dir, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data_dir))
    return config.read_paths()


def tp_of(data_dir, monkeypatch, topic_id):
    return next(t for t in paths_for(data_dir, monkeypatch).topics if t.id == topic_id)


def test_view_fields_questions_mode(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = seed_topic(data, "fit", template="exercise")
    with closing(store.connect(db)) as conn:
        sid = add_study(conn, "Training trial", "strength rose", tier="C", conducted_by="Uni Lab",
                        headline_quote="strength rose by five percent", question="Does training raise strength?",
                        subtopic="strength")
        conn.execute("UPDATE items SET red_flags=?, capped_from='B', cap_reasons=?, disputed=1, "
                     "skeptic_tier='D' WHERE study_id=?", (json.dumps(["multiple_metrics"]),
                                                           json.dumps(["animal"]), sid))
        conn.execute("UPDATE studies SET study_type='animal' WHERE id=?", (sid,))
        conn.commit()
        store.record_report(conn, "r2.html", [(sid, "Training trial", "the newest summary")])
        tp = tp_of(data, monkeypatch, "fit")
        study = store.get_study(conn, sid)
        view = query.study_view(conn, tp, study, query.question_index(conn, "questions"))
        group = store.study_items(conn, sid, store.STUDY_ITEM_STATES)
        expected = model.grading_view(study, group, store.primary_checks_for(conn, [g["id"] for g in group]),
                                      store.study_metric(conn, study), store.study_entities(conn, sid),
                                      grading.rules_of(tp.topic))
    keys = {"id", "topic", "name", "conducted_by", "relation", "tier", "skeptic_tier", "disputed", "capped_from",
            "cap_reasons", "labels", "red_flags", "summary", "headline", "questions", "subtopic", "metric",
            "entities", "data_date", "reported_at", "url", "primary_url", "page"}
    assert keys <= set(view)
    assert view["id"] == f"s{sid}" and view["tier"] == "C" and view["skeptic_tier"] == "D"
    assert view["disputed"] is True and view["capped_from"] == "B"
    assert view["labels"] == expected["grade_labels"] == ["animal"]
    assert view["cap_reasons"] == expected["cap_reasons"] and view["cap_reasons"]
    assert view["red_flags"] == [{"code": "multiple_metrics", "text": grading.ALL_RED_FLAGS["multiple_metrics"]}]
    assert view["summary"] == "the newest summary"
    assert view["headline"]["quote"] == "strength rose by five percent"
    assert view["subtopic"] == "Strength" and view["reported_at"]
    assert view["questions"][0]["text"] == "Does training raise strength?"


def test_view_questions_label_matches_page(tmp_path):
    db = seed_topic(tmp_path / "data", "seo")
    with closing(store.connect(db)) as conn:
        a = add_study(conn, "Study A", "clicks up", conducted_by="Org One", question="Do titles help?",
                      subtopic="title-tags")
        b = add_study(conn, "Study B", "clicks up", conducted_by="Org Two", question="Do titles help?",
                      subtopic="title-tags")
        index = query.question_index(conn, "questions")
    assert index[a][0]["label"] == index[b][0]["label"] == "consistent"
    assert index[a][0]["independent"] == 2


def test_view_findings_mode(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = seed_topic(data, "fit", mode="findings")
    with closing(store.connect(db)) as conn:
        sid = add_study(conn, "Trial", "mixed", finding_texts=["strength kept", "soreness rose"],
                        subtopic="title-tags")
        sub = store.get_subtopic(conn, "title-tags")["id"]
        conn.execute("UPDATE studies SET findings_subtopic_id=? WHERE id=?", (sub, sid))
        q1 = store.create_question(conn, sub, "Is strength kept?", "strength", "difference", "with versus without", mode="findings")
        q2 = store.create_question(conn, sub, "Does soreness change?", "soreness", "difference", "with versus without", mode="findings")
        conn.execute("UPDATE findings SET question_id=?, direction='no_change' WHERE study_id=? AND position=1",
                     (q1, sid))
        conn.execute("UPDATE findings SET question_id=?, direction='up', quote_checked=0 WHERE study_id=? "
                     "AND position=2", (q2, sid))
        conn.commit()
        tp = tp_of(data, monkeypatch, "fit")
        view = query.study_view(conn, tp, store.get_study(conn, sid), query.question_index(conn, "findings"))
    assert {q["text"] for q in view["questions"]} == {"Is strength kept?", "Does soreness change?"}
    assert [f["checked"] for f in view["findings"]] == [True, False]
    assert view["findings"][0]["text"] == "strength kept" and view["findings"][0]["quote"]


def test_view_quotes_mode(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = seed_topic(data, "q", mode="quotes")
    with closing(store.connect(db)) as conn:
        sid = add_study(conn, "Trial", "mixed", finding_texts=["one finding"])
        tp = tp_of(data, monkeypatch, "q")
        index = query.question_index(conn, "quotes")
        view = query.study_view(conn, tp, store.get_study(conn, sid), index)
    assert index == {} and view["questions"] == []
    assert view["findings"][0]["text"] == "one finding"


def test_page_link_only_when_page_exists(tmp_path, monkeypatch):
    data = tmp_path / "data"
    reports = tmp_path / "Berichte mit Leer und Ü"
    seed_topic(data, "seo")
    monkeypatch.setenv("EV_ECHO_REPORTS_DIR", str(reports))
    tp = tp_of(data, monkeypatch, "seo")
    assert query.page_link(tp, 12, True) is None
    page = tp.reports_dir / "seo.html"
    page.parent.mkdir(parents=True)
    page.write_text("<html></html>", encoding="utf-8")
    link = query.page_link(tp, 12, True)
    assert link.startswith("file:///") and link.endswith("/seo.html#s12")
    assert " " not in link and "Ü" not in link
    assert url2pathname(urlsplit(link).path) == str(page.resolve())
    assert query.page_link(tp, 12, False) is None


def test_merged_study_resolves(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = seed_topic(data, "seo")
    with closing(store.connect(db)) as conn:
        keep = add_study(conn, "Keeper", "clicks")
        gone = add_study(conn, "Merged", "clicks")
        conn.execute("UPDATE studies SET merged_into=? WHERE id=?", (keep, gone))
        conn.commit()
        tp = tp_of(data, monkeypatch, "seo")
        view = query.study_view(conn, tp, store.get_study(conn, gone), {})
    assert view["id"] == f"s{keep}"


def test_list_topics_statuses(tmp_path, monkeypatch):
    data = tmp_path / "data"
    make_workspace(data, ())
    with closing(store.connect(seed_topic(data, "seo"))) as conn:
        for n in range(3):
            add_study(conn, f"Study {n}", "clicks", finding_texts=["a finding"] if n == 0 else ())
    seed_topic(data, "cro")
    (data / "topics" / "cro" / "echo.db").unlink()
    old = seed_topic(data, "old")
    with closing(sqlite3.connect(old)) as raw:
        raw.execute("PRAGMA user_version = 13")
    seed_topic(data, "bad")
    (data / "topics" / "bad" / "topic.json").write_text("{nope", encoding="utf-8")
    seed_topic(data, "fit", mode="findings", template="exercise")
    rows = {r["id"]: r for r in query.list_topics(paths_for(data, monkeypatch))}
    assert rows["seo"]["status"] == "ok" and rows["seo"]["studies"] == 3 and rows["seo"]["findings"] == 1
    assert rows["seo"]["last_report"] and rows["seo"]["grouping_mode"] == "questions"
    assert rows["cro"]["status"] == "no data yet: run a report" and rows["cro"]["studies"] == 0
    assert rows["old"]["status"] == "needs_upgrade: run any Evidence Echo report"
    assert "topic.json" in rows["bad"]["status"]
    assert rows["fit"]["disclaimer"] and rows["fit"]["grouping_mode"] == "findings"
    assert rows["seo"]["disclaimer"] is None
