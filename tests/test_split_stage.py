"""The split stage: emit, the answer rules, the proposal it writes (and that nothing moves), reset, API mode."""
import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, knowledge, store
from evidence_echo.stages import split
from tests.conftest import FakeBackend
from tests.split_fixtures import add_many, add_question, add_study

TARGET = "subtopic:ai-search"


@pytest.fixture
def world(db):
    """'AI search' holds 20 of 40 counted studies: three questions (6, 5, 4 studies) and 5 loose ones."""
    store.seed_subtopics(db, ["AI search", "Links", "Other"])
    w = {"qa": add_question(db, "ai-search", "Qa?"), "qb": add_question(db, "ai-search", "Qb?"),
         "qc": add_question(db, "ai-search", "Qc?")}
    w["a"] = add_many(db, 6, subtopic="ai-search", metric="clicks", question=w["qa"])
    w["b"] = add_many(db, 5, subtopic="ai-search", metric="clicks", question=w["qb"])
    w["c"] = add_many(db, 4, subtopic="ai-search", metric="clicks", question=w["qc"])
    w["loose"] = add_many(db, 5, subtopic="ai-search", metric="clicks")
    add_many(db, 20, metric="clicks")                           # unsorted
    return w


def _answer(groups, target=TARGET):
    return json.dumps({"results": [{"id": target, "groups": groups}]})


def _ok_groups(w):
    return [{"name": "What AI cites", "questions": [f"q{w['qa']}"], "studies": []},
            {"name": "GEO tactics", "questions": [f"q{w['qb']}", f"q{w['qc']}"], "studies": [f"s{w['loose'][0]}"]}]


def _snapshot(db):
    return [tuple(r) for r in db.execute(
        "SELECT id, subtopic_id, question_id, metric_id FROM studies ORDER BY id")], \
        [tuple(r) for r in db.execute("SELECT id, subtopic_id FROM questions ORDER BY id")]


def test_emit_payload_shape(db, topic, world):
    store.subtopic_for_choice(db, {"propose": "Old idea"}, create=True)
    store.reject_subtopic(db, "old-idea")
    data = json.loads(split.emit(db, topic))
    assert data["stage"] == "split" and len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == TARGET and item["target"] == "subtopic" and item["name"] == "AI search"
    assert [(q["id"], q["studies"]) for q in item["questions"]] == [
        (f"q{world['qa']}", 6), (f"q{world['qb']}", 5), (f"q{world['qc']}", 4)]
    first = item["questions"][0]
    assert first["text"] == "Qa?" and first["measure"] == "the measure" and first["kind"] == "level"
    assert len(first["examples"]) == 3
    assert [s["id"] for s in item["studies"]] == [f"s{i}" for i in sorted(world["loose"], reverse=True)]
    assert item["studies"][0]["measured"] == "clicks rose"
    assert {"slug": "links", "name": "Links"} in data["subtopics"]
    assert {"slug": "clicks", "name": "Clicks"} in data["metrics"]
    assert "Old idea" in data["used_names"] and "old-idea" in data["used_names"] and "Clicks" in data["used_names"]
    assert "untrusted" in data["instructions"] and "at least 3" in data["instructions"]


def test_emit_metric_target(db, topic):
    store.seed_subtopics(db, ["Links"])
    ids = add_many(db, 12, metric="other", measured="AI citations up")
    add_many(db, 8, metric="clicks")
    data = json.loads(split.emit(db, topic))
    item = data["items"][0]
    assert item["id"] == "metric:other" and item["target"] == "metric"
    assert [s["id"] for s in item["studies"]] == [f"s{i}" for i in sorted(ids, reverse=True)]
    assert item["studies"][0]["measured"] == "AI citations up" and item["studies"][0]["measure"] is None


def test_metric_studies_are_capped(db, topic):
    store.seed_subtopics(db, ["Links"])
    add_many(db, knowledge.SPLIT_MAX_STUDIES + 5, metric="other")
    item = json.loads(split.emit(db, topic))["items"][0]
    assert len(item["studies"]) == knowledge.SPLIT_MAX_STUDIES


def test_emit_empty_when_no_target(db, topic):
    store.seed_subtopics(db, ["Links"])
    add_many(db, 3, subtopic="links", metric="clicks")
    data = json.loads(split.emit(db, topic))
    assert data["stage"] == "split" and data["items"] == []


def test_apply_creates_proposal_without_moving(db, world):
    before = _snapshot(db)
    out = split.apply(db, _answer(_ok_groups(world)))
    assert out == {"applied": 1, "rejected": []}
    assert _snapshot(db) == before                                       # nothing moved
    proposal = dict(db.execute("SELECT * FROM split_proposals").fetchone())
    assert (proposal["target"], proposal["status"], proposal["studies_at"]) == ("subtopic", "pending", 20)
    assert proposal["source_id"] == store.get_subtopic(db, "ai-search")["id"] and proposal["source_choice"] is None
    assert [(r["slug"], r["status"]) for r in db.execute("SELECT * FROM subtopics WHERE status='proposed'")] == [
        ("what-ai-cites", "proposed"), ("geo-tactics", "proposed")]
    groups = db.execute("SELECT * FROM split_groups ORDER BY id").fetchall()
    assert [(g["mode"], g["status"]) for g in groups] == [("new", "pending")] * 2
    moves = [(m["group_id"], m["question_id"], m["study_id"]) for m in db.execute("SELECT * FROM split_moves")]
    assert moves == [(groups[0]["id"], world["qa"], None), (groups[1]["id"], world["qb"], None),
                     (groups[1]["id"], world["qc"], None), (groups[1]["id"], None, world["loose"][0])]
    assert knowledge.split_targets(db) == []                             # a pending proposal blocks the target
    assert store.metrics_overview(db)["metrics"][0]["split"] is None


def test_apply_into_group(db, world):
    groups = [{"name": "What AI cites", "questions": [f"q{world['qa']}"]},
              {"into": "links", "studies": [f"s{i}" for i in world["loose"][:3]]}]
    assert split.apply(db, _answer(groups)) == {"applied": 1, "rejected": []}
    group = db.execute("SELECT * FROM split_groups WHERE mode='into'").fetchone()
    assert group["dest_id"] == store.get_subtopic(db, "links")["id"]
    assert [r["slug"] for r in db.execute("SELECT slug FROM subtopics WHERE status='proposed'")] == ["what-ai-cites"]


def test_apply_metric_proposal(db, topic):
    store.seed_subtopics(db, ["Links"])
    ids = add_many(db, 12, metric="other")
    add_many(db, 8, metric="clicks")
    groups = [{"name": "AI citations", "studies": [f"s{i}" for i in ids[:6]]}]
    assert split.apply(db, _answer(groups, "metric:other")) == {"applied": 1, "rejected": []}
    assert store.get_metric(db, "ai-citations")["status"] == "proposed"
    assert db.execute("SELECT COUNT(*) FROM studies WHERE metric_id=?",
                      (store.get_metric(db, "other")["id"],)).fetchone()[0] == 12      # nothing moved
    rows = {r["slug"]: r for r in store.metrics_overview(db)["metrics"]}
    assert rows["ai-citations"]["split"] == 1 and rows["clicks"]["split"] is None


def _bad(w, **over):
    return {**{"name": "Fresh", "questions": [f"q{w['qa']}"], "studies": []}, **over}


@pytest.mark.parametrize("label,make,error", [
    ("one group", lambda w: [_bad(w)], "1 groups; a subtopic split has 2 to 8"),
    ("nine groups", lambda w: [_bad(w, name=f"N{i}", questions=[f"q{w['qa']}"] if i == 0 else [],
                                    studies=[f"s{w['loose'][0]}"] if i == 1 else []) for i in range(9)],
     "9 groups; a subtopic split has 2 to 8"),
    ("long name", lambda w: [_bad(w, name="x" * 81), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "a name needs 1 to 80 characters"),
    ("duplicate name", lambda w: [_bad(w), _bad(w, name="fresh", questions=[f"q{w['qb']}"])],
     "used twice in the answer"),
    ("duplicate slug", lambda w: [_bad(w), _bad(w, name="FRESH!", questions=[f"q{w['qb']}"])],
     "used twice in the answer"),
    ("named Other", lambda w: [_bad(w, name="Other"), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "is reserved"),
    ("no letter", lambda w: [_bad(w, name="!!!"), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "a name needs a letter or digit"),
    ("clash with a rejected row", lambda w: [_bad(w, name="Old idea"), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "already used by subtopic 'old-idea'"),
    ("clash with a retired row", lambda w: [_bad(w, name="Dead one"), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "already used by subtopic 'dead-one'"),
    ("clash with a merged row", lambda w: [_bad(w, name="Merged away"), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "already used by subtopic 'merged-away'"),
    ("clash with an active one", lambda w: [_bad(w, name="links"), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "already used by subtopic 'links'"),
    ("foreign question", lambda w: [_bad(w, questions=["q999"]), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "'q999' is not a question of this subtopic"),
    ("foreign study", lambda w: [_bad(w, studies=["s999"]), _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "'s999' is not a study of this subtopic"),
    ("id twice", lambda w: [_bad(w), _bad(w, name="Ok")], "is used more than once"),
    ("group under three studies", lambda w: [_bad(w, questions=[], studies=[f"s{w['loose'][0]}"]),
                                              _bad(w, name="Ok", questions=[f"q{w['qb']}"])],
     "1 studies; a group needs at least 3"),
    ("into the source", lambda w: [_bad(w), {"into": "ai-search", "questions": [f"q{w['qb']}"]}],
     "is the subtopic being split"),
    ("into a pending split name", lambda w: [_bad(w), {"into": "pending-name", "questions": [f"q{w['qb']}"]}],
     "is not an active subtopic of the topic"),
    ("into an unknown name", lambda w: [_bad(w), {"into": "nowhere", "questions": [f"q{w['qb']}"]}],
     "is not an active subtopic of the topic"),
])
def test_split_name_rules(db, world, label, make, error):
    store.subtopic_for_choice(db, {"propose": "Old idea"}, create=True)
    store.reject_subtopic(db, "old-idea")
    store._insert_subtopic(db, "Dead one", "retired")
    store._insert_subtopic(db, "Merged away", "seeded")
    store.merge_subtopics(db, "merged-away", "links")
    store._insert_subtopic(db, "Pending name", "proposed")
    db.commit()
    before = _snapshot(db)
    rows = db.execute("SELECT COUNT(*) FROM subtopics").fetchone()[0]
    out = split.apply(db, _answer(make(world)))
    assert out["applied"] == 0, label
    assert any(error in e for r in out["rejected"] for e in r["errors"]), (label, out)
    assert _snapshot(db) == before and db.execute("SELECT COUNT(*) FROM subtopics").fetchone()[0] == rows
    assert db.execute("SELECT COUNT(*) FROM split_proposals").fetchone()[0] == 0


def test_split_name_hostile_text_is_stored_as_data(db, world):
    name = "<script>alert(1)</script>"
    groups = [_bad(world, name=name), _bad(world, name='a "quoted" [[link]] > x', questions=[f"q{world['qb']}"])]
    assert split.apply(db, _answer(groups))["applied"] == 1
    assert store.get_subtopic(db, store.subtopic_slug(name))["name"] == name


def test_metric_groups_take_studies_only(db):
    store.seed_subtopics(db, ["Links"])
    ids = add_many(db, 12, metric="other")
    add_many(db, 8, metric="clicks")
    out = split.apply(db, _answer([{"name": "AI citations", "questions": ["q1"], "studies": []}], "metric:other"))
    assert out["applied"] == 0 and "uses studies only" in out["rejected"][0]["errors"][0]
    out = split.apply(db, _answer([{"name": "Clicks", "studies": [f"s{i}" for i in ids[:5]]}], "metric:other"))
    assert out["applied"] == 0 and "already used by metric 'clicks'" in out["rejected"][0]["errors"][0]
    ten = [{"name": f"M{n}", "studies": [f"s{i}" for i in ids[:3]]} for n in range(11)]
    out = split.apply(db, _answer(ten, "metric:other"))
    assert "11 groups; a metric split has 1 to 10" in out["rejected"][0]["errors"][0]
    out = split.apply(db, _answer([{"into": "other", "studies": [f"s{i}" for i in ids[:5]]}], "metric:other"))
    assert "is the metric being split" in out["rejected"][0]["errors"][0]


@pytest.mark.parametrize("group,error", [
    ({"name": "A", "into": "links"}, "exactly one of name or into"),
    ({"questions": ["q1"]}, "exactly one of name or into"),
    ({"name": "A", "color": 1}, "unexpected field 'color'"),
    ({"name": "A", "questions": "q1"}, "must be a list of id strings"),
    ({"name": 3}, "must be a non-empty string"),
])
def test_split_answer_shape_is_checked(db, world, group, error):
    out = split.apply(db, _answer([group, {"name": "B", "questions": [f"q{world['qb']}"]}]))
    assert out["applied"] == 0 and error in out["rejected"][0]["errors"][0]


def test_answer_for_a_target_that_is_not_waiting(db, world):
    out = split.apply(db, _answer(_ok_groups(world), "subtopic:links"))
    assert out["applied"] == 0 and "is not awaiting split" in out["rejected"][0]["errors"][0]
    out = split.apply(db, _answer(_ok_groups(world), "nonsense"))
    assert "malformed" in out["rejected"][0]["errors"][0]


def test_reset_split_drops_pending_only(db, world):
    split.apply(db, _answer(_ok_groups(world)))
    decided = store.create_split_proposal(db, "subtopic", store.get_subtopic(db, "links")["id"], 20,
                                          [{"mode": "new", "name": "Kept part", "studies": []}])
    db.execute("UPDATE split_proposals SET status='decided' WHERE id=?", (decided,))
    db.execute("UPDATE subtopics SET status='confirmed' WHERE slug='kept-part'")
    db.commit()
    out = store.clear_pending_splits(db)
    assert out == {"proposals": 1, "closed": 0, "groups": 2, "names": 2}
    assert db.execute("SELECT COUNT(*) FROM split_proposals").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM split_groups").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM split_moves").fetchone()[0] == 0
    assert [r["slug"] for r in db.execute("SELECT slug FROM subtopics WHERE status IN ('proposed')")] == []
    assert store.get_subtopic(db, "kept-part")["status"] == "confirmed"
    assert store.clear_pending_splits(db) == {"proposals": 0, "closed": 0, "groups": 0, "names": 0}


def test_run_api_split(db, topic, world):
    reply = json.dumps({"groups": _ok_groups(world)})
    backend = FakeBackend([reply])
    out = split.run_api(db, backend, topic)
    assert out == {"processed": 1, "rejected": []}
    prompt = backend.prompts[0]
    assert "untrusted" in prompt and "- links: Links" in prompt and "Qa?" in prompt
    assert db.execute("SELECT status FROM split_proposals").fetchone()[0] == "pending"
    # a bad answer is rejected whole and nothing is written; the target is asked again next time
    store.clear_pending_splits(db)
    bad = FakeBackend([json.dumps({"groups": [_bad(world)]})])
    out = split.run_api(db, bad, topic)
    assert out["processed"] == 0 and out["rejected"][0]["id"] == TARGET
    assert db.execute("SELECT COUNT(*) FROM split_proposals").fetchone()[0] == 0
    broken = FakeBackend(["not json"])
    assert "not valid JSON" in split.run_api(db, broken, topic)["rejected"][0]["errors"][0]


def test_split_is_a_stage_and_a_reset_target():
    assert "split" in api.LLM_STAGES and "split" in api.RESET_STAGES and "split" not in api.TWO_ANSWER_STAGES
    assert split.PROMPT_VERSION == "split-1"
    parser = cli._parser()
    assert "split" in parser._subparsers._group_actions[0].choices


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_session_round_trip_and_reset(workspace, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_JUDGMENT_BACKEND", "session")
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    store.seed_metrics(conn, cfg.topic.metrics)
    ids = add_many(conn, 16, subtopic="ai-overviews-ai-search", metric="clicks")
    add_many(conn, 24, metric="clicks")
    conn.close()
    code, out, _ = _run(capsys, "split")
    assert code == 0 and out["items"][0]["id"] == "subtopic:ai-overviews-ai-search"
    answer = _answer([{"name": "Part one", "studies": [f"s{i}" for i in ids[:8]]},
                      {"name": "Part two", "studies": [f"s{i}" for i in ids[8:12]]}],
                     "subtopic:ai-overviews-ai-search")
    f = tmp_path / "split.json"
    f.write_text(answer, encoding="utf-8")
    assert _run(capsys, "apply", "--stage", "split", str(f))[1] == {"applied": 1, "rejected": []}
    assert _run(capsys, "split")[1]["items"] == []                         # a pending proposal blocks it
    code, out, _ = _run(capsys, "reset", "--stage", "split")
    assert code == 0 and out == {"reset": "split", "proposals": 1, "closed": 0, "groups": 2, "names": 2}
    assert _run(capsys, "split")[1]["items"][0]["id"] == "subtopic:ai-overviews-ai-search"
