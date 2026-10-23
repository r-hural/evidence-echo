"""Metrics end to end on a copy-shaped database: a step-10 SEO database with one big subtopic and many
'other' metrics is opened (step 11, seeding), classified, split on the owner's say-so, and rendered."""
import json
import sqlite3
from datetime import datetime, timezone

from evidence_echo import api
from evidence_echo.core import config, store
from evidence_echo.render import html as html_render
from evidence_echo.render import model

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
AI = "AI Overviews & AI search"
AI_SLUG = "ai-overviews-ai-search"
QUESTION_SIZES = [4, 4, 3, 3, 3, 3, 3, 3]            # 26 studies under eight questions
OLD_CODES = ["traffic"] * 4 + ["rankings"] * 3 + ["engagement"] * 3 + ["other"] * 30


def _topic_json_without_metrics(workspace):
    path = workspace / "topics" / "seo" / "topic.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("metrics", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _build_step10(path, subtopic_names):
    """A database as the engine left it at schema step 10: the current schema minus the step 11 tables
    and column, 40 counted studies, 26 of them in one subtopic under 8 questions, old metric codes."""
    schema = store.SCHEMA.replace(",\n  metric_id INTEGER REFERENCES metrics(id)\n);", "\n);")
    for ddl in store.STEP11_DDL:
        schema = schema.replace(ddl + ";\n", "")
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    ids = {}
    for name in subtopic_names:
        ids[store.subtopic_slug(name)] = conn.execute(
            "INSERT INTO subtopics (slug, name, status, created_at) VALUES (?,?,'seeded','x')",
            (store.subtopic_slug(name), name)).lastrowid
    questions = [conn.execute(
        "INSERT INTO questions (subtopic_id, text, measure, created_at, kind) VALUES (?,?,?,'x','level')",
        (ids[AI_SLUG], f"Question {n}?", f"measure {n}")).lastrowid for n in range(len(QUESTION_SIZES))]
    placement = [(AI_SLUG, q) for q, size in zip(questions, QUESTION_SIZES) for _ in range(size)]
    placement += [("backlinks", None)] * (len(OLD_CODES) - len(placement))
    for n, ((slug, question), code) in enumerate(zip(placement, OLD_CODES), 1):
        sid = conn.execute(
            """INSERT INTO studies (canonical_name, findings, metric, created_at, subtopic_id, question_id,
               metric_code, direction, data_date, headline_value, headline_label, headline_quote,
               headline_checked, classified_version, evidence_tier)
               VALUES (?, 'f', 'clicks rose', 'x', ?, ?, ?, 'positive', '2024', '+4%', 'clicks', 'q', 1,
                       'classify-1', 'B')""",
            (f"Study {n}", ids[slug], question, code)).lastrowid
        conn.execute("""INSERT INTO items (url, source_name, source_type, title, state, fetched_at, study_id,
                        full_text) VALUES (?, 'Blog', 'rss', ?, 'verified', 'x', ?, 'text')""",
                     (f"https://e.com/{n}", f"Study {n}", sid))
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()


def _studies(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT id, subtopic_id, question_id, metric_id FROM studies ORDER BY id")]


def _metric_slug(conn, sid):
    found = store.study_metric(conn, store.get_study(conn, sid))
    return found["slug"] if found else None


def _sub_slug(conn, sid):
    found = store.resolve_subtopic(conn, store.get_study(conn, sid)["subtopic_id"])
    return found["slug"] if found else None


def test_metrics_end_to_end(workspace, tmp_path):
    topic_json = _topic_json_without_metrics(workspace)
    cfg = config.load()
    assert "metrics" not in json.loads(topic_json.read_text(encoding="utf-8"))
    _build_step10(cfg.db_path, cfg.topic.subtopics)

    # 1. open it: step 11 and seeding; old codes map where they fit, the 30 old 'other' wait
    with api._topic_db(cfg) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert [m["name"] for m in store.active_metrics(conn)] == cfg.topic.metrics and len(cfg.topic.metrics) == 13
        named = {sid: _metric_slug(conn, sid) for sid in range(1, 41)}
        assert sorted(v for v in named.values() if v) == sorted(
            ["organic-traffic"] * 4 + ["rankings"] * 3 + ["engagement"] * 3)
        waiting = [sid for sid, v in named.items() if v is None]
        assert len(waiting) == 30

    # 2. classify: fake answers, rounds until nothing waits (at most three)
    ai_citations = set(waiting[:20])
    rounds = 0
    while True:
        out = json.loads(api.emit(cfg, "classify"))
        if not out["items"]:
            break
        rounds += 1
        assert rounds <= 3
        answers = [{"id": item["id"], "metric": "ai-citations" if int(item["id"][1:]) in ai_citations else "other",
                    "entities": []}
                   for item in out["items"]]
        applied = api.apply(cfg, "classify", json.dumps({"results": answers}))
        assert applied["rejected"] == []
        if applied["waiting"] == 0:
            break
    assert rounds >= 1
    with api._topic_db(cfg) as conn:
        counts = {}
        for sid in range(1, 41):
            counts[_metric_slug(conn, sid)] = counts.get(_metric_slug(conn, sid), 0) + 1
        assert counts == {"ai-citations": 20, "other": 10, "organic-traffic": 4, "rankings": 3, "engagement": 3}

    # 3. split: two targets, the subtopic (26 of 40) and the 'other' metric (10 of 40 = 25%)
    payload = json.loads(api.emit(cfg, "split"))
    assert [i["id"] for i in payload["items"]] == [f"subtopic:{AI_SLUG}", "metric:other"]
    subtopic_item, metric_item = payload["items"]
    assert len(subtopic_item["questions"]) == 8 and sum(q["studies"] for q in subtopic_item["questions"]) == 26
    assert len(metric_item["studies"]) == 10
    ranked = [q["id"] for q in subtopic_item["questions"]]
    answer = {"results": [
        {"id": subtopic_item["id"], "groups": [
            {"name": "What AI engines cite", "questions": ranked[:3]},
            {"name": "Brand mentions", "questions": ranked[3:6]}]},
        {"id": "metric:other", "groups": [
            {"name": "Zero-click answers", "studies": [s["id"] for s in metric_item["studies"][:4]]}]}]}
    with api._topic_db(cfg) as conn:
        before = _studies(conn)
    assert api.apply(cfg, "split", json.dumps(answer)) == {"applied": 2, "rejected": []}
    with api._topic_db(cfg) as conn:
        assert _studies(conn) == before                                      # nothing moved without an accept
        proposals = {p["target"]: p for p in store.split_overview(conn)["proposals"]}
    assert set(proposals) == {"subtopic", "metric"}
    assert api.split_list(cfg)["proposals"][0]["groups"][0]["studies"] == sum(
        q["studies"] for q in subtopic_item["questions"][:3])

    # 4. the owner decides: accept one part, rename and accept the other, reject the metric part
    parts = {g["name"]: g["id"] for g in proposals["subtopic"]["groups"]}
    metric_group = proposals["metric"]["groups"][0]["id"]
    first = api.split_accept(cfg, parts["What AI engines cite"])
    assert first["moved_questions"] == 3 and first["ask_source"] is False
    assert api.split_rename(cfg, parts["Brand mentions"], "Brand visibility in AI") == {
        "renamed": parts["Brand mentions"], "name": "Brand visibility in AI"}
    second = api.split_accept(cfg, parts["Brand mentions"])
    assert second["moved_questions"] == 3 and second["ask_source"] is True
    api.split_source(cfg, proposals["subtopic"]["id"], "keep")
    assert api.split_reject(cfg, metric_group)["rejected"] == metric_group

    # 5. the result
    cfg = config.load()
    with api._topic_db(cfg) as conn:
        by_sub = {}
        for sid in range(1, 41):
            by_sub[_sub_slug(conn, sid)] = by_sub.get(_sub_slug(conn, sid), 0) + 1
        assert by_sub[AI_SLUG] == 26 - 11 - 9 == 6                          # the leftovers stay in the source
        assert by_sub["what-ai-engines-cite"] == 11 and by_sub["brand-visibility-in-ai"] == 9
        assert store.get_subtopic(conn, AI_SLUG)["status"] == "seeded"      # kept, still active
        assert store.get_subtopic(conn, "brand-mentions") is None            # renamed: the slug followed
        for question in store.list_questions(conn):                          # questions moved whole
            studies = [r[0] for r in conn.execute("SELECT id FROM studies WHERE question_id=?", (question["id"],))]
            assert {_sub_slug(conn, s) for s in studies} == {
                store.resolve_subtopic(conn, question["subtopic_id"])["slug"]}
        metrics = [_metric_slug(conn, sid) for sid in range(1, 41)]
        assert metrics.count("other") == 10 and metrics.count("ai-citations") == 20      # the rejected part moved nothing
        assert store.get_metric(conn, "zero-click-answers")["status"] == "rejected"
        assert [p["status"] for p in conn.execute("SELECT status FROM split_proposals")] == ["decided", "decided"]
        assert [r[0] for r in conn.execute("SELECT source_choice FROM split_proposals ORDER BY id")] == ["keep", None]
    names = json.loads(topic_json.read_text(encoding="utf-8"))["subtopics"]
    at = names.index(AI)
    assert names[at + 1:at + 3] == ["Brand visibility in AI", "What AI engines cite"] and names[-1] == "Other"
    assert json.loads(api.emit(cfg, "split"))["items"] == []                # decided: not asked again (re-arm rule)

    # 6. the topic page: the Metric filter and the Measured line
    with api._topic_db(cfg) as conn:
        store.record_report(conn, str(tmp_path / "r.md"), [(sid, f"Study {sid}", "summary") for sid in range(1, 41)])
        page = html_render.topic_page(model.build_topic(conn, cfg.topic, NOW), "overview.html", "r.html")
    assert '<select name="metric">' in page and "AI citations (20)" in page and "Other (10)" in page
    assert "Organic traffic · clicks rose" in page and "AI citations · clicks rose" in page
    assert page.index("Organic traffic (4)") < page.index("AI citations (20)") < page.index("Other (10)")
