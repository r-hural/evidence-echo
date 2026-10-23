"""End to end: a step-9 database with group-1 grouping is re-grouped under group-2 until nothing waits."""
import json
import re
from datetime import datetime, timezone

from evidence_echo.core import knowledge, store
from evidence_echo.render import html as html_render
from evidence_echo.render import model
from evidence_echo.stages import group
from tests.test_knowledge_questions import add, q

NOW = datetime(2026, 10, 21, 9, 30, tzinfo=timezone.utc)


def _make_step9(path):
    """Two subtopics; the same question twice (q42/q69-like), an owner-renamed question with a
    study, and a 'how much' question whose studies got up/down directions under group-1."""
    conn = store.connect(path)
    store.seed_subtopics(conn, ["Title tags & meta", "Local"])
    dup_a = q(conn, subtopic="title-tags-meta", text="Is Google serving altered pages to rank trackers?",
              measure="pages served", kind=None)
    dup_b = q(conn, subtopic="local", text="Is Google serving altered pages to rank trackers?",
              measure="pages served", kind=None)
    owner = q(conn, subtopic="title-tags-meta", text="Does freshness raise citations?", measure="citations",
              kind=None)
    store.rename_question(conn, owner, "Does freshness raise AI citations?")
    level = q(conn, subtopic="title-tags-meta", text="How much does ChatGPT cite Reddit?", measure="share",
              kind=None)
    s = {
        "org1": add(conn, subtopic="title-tags-meta", question=dup_a, by="Org1", direction="up"),
        "org2": add(conn, subtopic="local", question=dup_b, by="Org2", direction="down"),
        "own": add(conn, subtopic="title-tags-meta", question=owner, by="Own", direction="up"),
        "r1": add(conn, subtopic="title-tags-meta", question=level, by="R1", direction="down"),
        "r2": add(conn, subtopic="title-tags-meta", question=level, by="R2", direction="up"),
    }
    conn.execute("UPDATE studies SET grouped_version='group-1'")
    conn.execute("ALTER TABLE questions DROP COLUMN kind")
    conn.execute("ALTER TABLE questions DROP COLUMN comparison")
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    conn.close()
    return dict(s, dup_a=dup_a, dup_b=dup_b, owner=owner, level=level)


def _answers(payload, ids):
    """What a careful answerer would say for each waiting study."""
    kinds = {x["id"]: x["kind"] for x in payload["questions"]}
    plan = {  # study name -> (question id, kind, comparison, direction)
        "study by Org1": (ids["dup_a"], "difference", "altered pages vs normal pages", "up"),
        "study by Org2": (ids["dup_a"], "difference", "altered pages vs normal pages", "down"),
        "study by Own": (ids["owner"], "difference", "fresh pages vs stale pages", "up"),
        "study by R1": (ids["level"], "level", None, None),
        "study by R2": (ids["level"], "level", None, None),
    }
    results = []
    for item in payload["items"]:
        qid, kind, comparison, direction = plan[item["name"]]
        if item.get("fixed_question"):
            qid = int(item["fixed_question"][1:])
        res = {"id": item["id"], "question": f"q{qid}"}
        if kinds.get(f"q{qid}") is None:
            res["question_kind"] = {"kind": kind, **({"comparison": comparison} if comparison else {})}
        if direction:
            res["direction"] = direction
        results.append(res)
    return json.dumps({"results": results})


def test_regroup_end_to_end(tmp_path, topic):
    path = tmp_path / "echo.db"
    ids = _make_step9(path)
    conn = store.connect(path)                                     # step 10 runs here
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    assert len(store.studies_to_group(conn)) == 5                  # everything is re-grouped once
    for _ in range(5):
        payload = json.loads(group.emit(conn, topic))
        if not payload["items"]:
            break
        out = group.apply(conn, _answers(payload, ids))
        assert out["rejected"] == []
    assert store.studies_to_group(conn) == []

    org1, org2 = (store.get_study(conn, ids[k]) for k in ("org1", "org2"))
    assert org1["question_id"] == org2["question_id"] == ids["dup_a"]            # one question holds both
    assert org1["subtopic_id"] == org2["subtopic_id"] == store.get_subtopic(conn, "title-tags-meta")["id"]
    own = store.get_study(conn, ids["own"])
    assert own["question_id"] == ids["owner"] and own["question_by_owner"] in (0, 1)
    question = store.get_question(conn, ids["owner"])
    assert (question["text"], question["kind"]) == ("Does freshness raise AI citations?", "difference")
    assert store.get_question(conn, ids["level"])["kind"] == "level"

    evidence = {g.question_id: g for sub in knowledge.evidence(conn) for g in sub.questions}
    assert evidence[ids["dup_a"]].label == "contested" and evidence[ids["dup_a"]].independent == 2
    assert evidence[ids["level"]].label is None and evidence[ids["level"]].independent == 2
    assert all(g.label is None for g in evidence.values() if g.kind != "difference")

    t = model.build_topic(conn, topic, NOW)
    section = html_render.evidence_section(t)
    assert not re.search("[↑↓↕]", section)
    assert "Compared: altered pages vs normal pages" in section
    assert section.count("Compared:") == 1                          # only the difference question with 2+ studies is listed
    conn.close()
