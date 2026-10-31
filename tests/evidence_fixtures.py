"""Seeded topic databases for the evidence server tests (built with store.connect here, never in the server)."""
import json
from contextlib import closing
from itertools import count

from evidence_echo.core import config, store, topics
from tests.conftest import make_item, make_workspace

_n = count(1)


def seed_topic(data_dir, topic_id, mode="questions", template="seo"):
    """Make the topic's folder and database; returns the database path."""
    if not (data_dir / "config.json").exists():
        make_workspace(data_dir, ())
    if not (data_dir / "topics" / topic_id / "topic.json").exists():
        from dataclasses import replace
        base = topics.load_template(template, config.TEMPLATES_DIR)
        topics.save_topic(data_dir / "topics", replace(base, id=topic_id, name=topic_id.upper(),
                                                       grouping_mode=mode))
    path = data_dir / "topics" / topic_id / "echo.db"
    with closing(store.connect(path)) as conn:
        topic = topics.load_topic(data_dir / "topics", topic_id, check_sources=False)
        store.seed_subtopics(conn, ["Title tags", *topic.subtopics])
        store.seed_metrics(conn, ["Clicks", "Rankings", *topic.metrics, "Other"])
    return path


def add_study(conn, name, findings, tier="B", conducted_by=None, entities=(), finding_texts=(),
              question=None, subtopic=None, reported=True, headline_quote=None, full_text="text",
              direction="up", metric=None):
    """One counted study. `question` is a question text (created under `subtopic`), `finding_texts` become
    findings rows, `reported` puts it in a recorded report. Returns the study id."""
    n = next(_n)
    provenance = {"conducted_by": conducted_by, "relation": "third_party"} if conducted_by else None
    sid = store.create_study(
        conn, tier, {"what_was_tested": name, "findings": findings, "research_timeframe": "2024"}, None,
        provenance=provenance, metric=metric,
        headline={"value": "+5%", "label": "clicks", "quote": headline_quote, "checked": True}
        if headline_quote else None)
    url = f"https://e.com/s{sid}-{n}"
    store.insert_items(conn, [make_item(url=url)])
    iid = conn.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    conn.execute("UPDATE items SET study_id=?, state=?, full_text=?, facts=?, skeptic_tier=? WHERE id=?",
                 (sid, "reported" if reported else "verified", full_text,
                  json.dumps({"what_was_tested": name}), tier, iid))
    conn.execute("UPDATE studies SET created_by_item=? WHERE id=?", (iid, sid))
    if subtopic is not None:
        conn.execute("UPDATE studies SET subtopic_id=? WHERE id=?", (store.get_subtopic(conn, subtopic)["id"], sid))
    if question is not None:
        sub = store.get_subtopic(conn, subtopic or "title-tags")
        qid = next((r[0] for r in conn.execute("SELECT id FROM questions WHERE text=?", (question,))), None)
        if qid is None:
            qid = store.create_question(conn, sub["id"], question, "the measure", "difference", comparison="with versus without")
        conn.execute("UPDATE studies SET question_id=?, question_direction=? WHERE id=?", (qid, direction, sid))
    conn.commit()
    if entities:
        store.link_study_entities(conn, sid, list(entities))
    if finding_texts:
        store.write_findings(conn, sid, iid, [{"text": t, "quote": t, "quote_checked": True}
                                              for t in finding_texts], "test")
    if reported:
        store.record_report(conn, "r.html", [(sid, name, findings)])
    return sid
