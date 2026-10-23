"""The owner's split decisions: accept / reject / rename per group, the source question, topic.json."""
import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, knowledge, store
from evidence_echo.core.settings import ConfigError
from evidence_echo.stages import classify, split
from tests.split_fixtures import add_many, add_question, add_study
from tests.test_split_stage import TARGET, _answer, _ok_groups, world  # noqa: F401  (the world fixture)


def _propose(db, world, groups=None):
    out = split.apply(db, _answer(groups or _ok_groups(world)))
    assert out["applied"] == 1, out
    return db.execute("SELECT id FROM split_proposals ORDER BY id DESC").fetchone()[0]


def _gids(db, pid):
    return [r[0] for r in db.execute("SELECT id FROM split_groups WHERE proposal_id=? ORDER BY id", (pid,))]


def _sub_of(db, sid):
    found = store.resolve_subtopic(db, store.get_study(db, sid)["subtopic_id"])
    return found["slug"] if found else None


def _status(db, slug):
    return store.get_subtopic(db, slug)["status"]


def test_accept_moves_question_with_studies(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    out = store.accept_split_group(db, g1)
    assert out == {"accepted": g1, "kind": "subtopic", "name": "What AI cites", "source_name": "AI search",
                   "source_slug": "ai-search", "mode": "new", "moved_questions": 1, "moved_studies": 6, "ask_source": False}
    question = store.get_question(db, world["qa"])
    assert store.resolve_subtopic(db, question["subtopic_id"])["slug"] == "what-ai-cites"
    assert {_sub_of(db, s) for s in world["a"]} == {"what-ai-cites"}
    assert {_sub_of(db, s) for s in world["b"] + world["c"] + world["loose"]} == {"ai-search"}   # untouched
    assert store.resolve_subtopic(db, store.get_question(db, world["qb"])["subtopic_id"])["slug"] == "ai-search"
    assert _status(db, "what-ai-cites") == "confirmed" and _status(db, "geo-tactics") == "proposed"
    assert db.execute("SELECT status FROM split_groups WHERE id=?", (g1,)).fetchone()[0] == "accepted"
    assert db.execute("SELECT status FROM split_proposals WHERE id=?", (pid,)).fetchone()[0] == "pending"
    placed, _ = knowledge.placed_studies(db)
    assert sum(1 for s, sub, q in placed if q and q["id"] == world["qa"] and sub["slug"] == "what-ai-cites") == 6


def test_accept_moves_loose_study(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    out = store.accept_split_group(db, g2)
    assert (out["moved_questions"], out["moved_studies"]) == (2, 10)          # qb 5 + qc 4 + one loose
    assert _sub_of(db, world["loose"][0]) == "geo-tactics"
    assert {_sub_of(db, s) for s in world["loose"][1:]} == {"ai-search"}
    assert {_sub_of(db, s) for s in world["b"] + world["c"]} == {"geo-tactics"}


def test_accept_into_group(db, world):
    groups = [{"name": "What AI cites", "questions": [f"q{world['qa']}"]},
              {"into": "links", "studies": [f"s{i}" for i in world["loose"][:3]]}]
    pid = _propose(db, world, groups)
    g1, g2 = _gids(db, pid)
    out = store.accept_split_group(db, g2)
    assert out["mode"] == "into" and out["name"] == "Links" and out["moved_studies"] == 3
    assert {_sub_of(db, s) for s in world["loose"][:3]} == {"links"}
    assert _status(db, "links") == "seeded"                                    # an existing row is not confirmed


def test_accept_metric_moves_only_studies_still_other(db):
    ids = add_many(db, 12, metric="other")
    add_many(db, 8, metric="clicks")
    out = split.apply(db, _answer([{"name": "AI citations", "studies": [f"s{i}" for i in ids[:6]]}], "metric:other"))
    assert out["applied"] == 1
    pid = db.execute("SELECT id FROM split_proposals").fetchone()[0]
    (gid,) = _gids(db, pid)
    store.set_study_metric(db, ids[0], "ctr")                                  # the owner set one meanwhile
    listed = store.split_overview(db)["proposals"][0]["groups"][0]
    assert listed["studies"] == 5                                              # only the moves that still hold
    result = store.accept_split_group(db, gid)
    assert result["kind"] == "metric" and result["moved_studies"] == 5 and result["ask_source"] is False
    metrics = {i: (store.study_metric(db, store.get_study(db, i)) or {}).get("slug") for i in ids}
    assert metrics[ids[0]] == "ctr" and {metrics[i] for i in ids[1:6]} == {"ai-citations"}
    assert {metrics[i] for i in ids[6:]} == {"other"}
    assert store.get_metric(db, "ai-citations")["status"] == "confirmed"
    assert db.execute("SELECT status FROM split_proposals").fetchone()[0] == "decided"
    assert db.execute("SELECT source_choice FROM split_proposals").fetchone()[0] is None


def test_accept_skips_moves_that_no_longer_hold(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    # qb was merged into a question of another subtopic; one study of qc was moved there by the owner
    links_q = add_question(db, "links", "Links question?")
    store.merge_questions(db, world["qb"], links_q)
    store.move_study(db, world["c"][0], links_q)
    overview = {g["id"]: g for g in store.split_overview(db)["proposals"][0]["groups"]}
    assert overview[g2]["studies"] == 3 + 1                                    # qc minus the moved one, plus one loose
    out = store.accept_split_group(db, g2)
    assert (out["moved_questions"], out["moved_studies"]) == (1, 4)
    assert {_sub_of(db, s) for s in world["b"]} == {"links"}                   # followed the merge, not moved by us
    assert _sub_of(db, world["c"][0]) == "links" and {_sub_of(db, s) for s in world["c"][1:]} == {"geo-tactics"}


def test_reject_moves_nothing(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    before = [tuple(r) for r in db.execute("SELECT id, subtopic_id, question_id FROM studies ORDER BY id")]
    out = store.reject_split_group(db, g1)
    assert out["rejected"] == g1 and out["name"] == "What AI cites" and out["ask_source"] is False
    assert [tuple(r) for r in db.execute("SELECT id, subtopic_id, question_id FROM studies ORDER BY id")] == before
    assert _status(db, "what-ai-cites") == "rejected"
    assert db.execute("SELECT COUNT(*) FROM split_moves WHERE group_id=?", (g1,)).fetchone()[0] == 0
    assert db.execute("SELECT status FROM split_groups WHERE id=?", (g1,)).fetchone()[0] == "rejected"
    with pytest.raises(ConfigError, match="already decided"):
        store.reject_split_group(db, g1)
    with pytest.raises(ConfigError, match="no split group"):
        store.accept_split_group(db, 999)


def test_rename_then_accept(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    assert store.rename_split_group(db, g1, "  AI   citations ") == {"renamed": g1, "name": "AI citations"}
    assert store.get_subtopic(db, "ai-citations")["name"] == "AI citations"
    assert store.get_subtopic(db, "what-ai-cites") is None
    with pytest.raises(ConfigError, match="already used"):
        store.rename_split_group(db, g1, "links")
    with pytest.raises(ConfigError, match="1 to 80"):
        store.rename_split_group(db, g1, "x" * 81)
    with pytest.raises(ConfigError, match="reserved"):
        store.rename_split_group(db, g1, "Other")
    assert store.rename_split_group(db, g1, "AI citations") == {"renamed": g1, "name": "AI citations"}   # own name ok
    out = store.accept_split_group(db, g1)
    assert out["name"] == "AI citations" and _status(db, "ai-citations") == "confirmed"
    with pytest.raises(ConfigError, match="already decided"):
        store.rename_split_group(db, g1, "Again")


def test_rename_refused_for_into_groups(db, world):
    groups = [{"name": "What AI cites", "questions": [f"q{world['qa']}"]},
              {"into": "links", "studies": [f"s{i}" for i in world["loose"][:3]]}]
    pid = _propose(db, world, groups)
    with pytest.raises(ConfigError, match="moves into an existing"):
        store.rename_split_group(db, _gids(db, pid)[1], "Whatever")


def test_source_question_only_after_all_decided_with_an_accept(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    assert store.accept_split_group(db, g1)["ask_source"] is False             # g2 is still open
    with pytest.raises(ConfigError, match="not asked"):
        store.set_split_source(db, pid, "keep")
    assert store.reject_split_group(db, g2)["ask_source"] is True
    overview = store.split_overview(db)["proposals"][0]
    assert overview["source_choice"] is None and overview["ask_source"] is True
    assert overview["source_studies"] == 14                                    # 20 minus qa's six
    out = store.set_split_source(db, pid, "keep")
    assert out == {"proposal": pid, "source": "ai-search", "source_name": "AI search", "choice": "keep",
                   "studies_to_resort": 0}
    assert _status(db, "ai-search") == "seeded"
    row = dict(db.execute("SELECT * FROM split_proposals WHERE id=?", (pid,)).fetchone())
    assert row["status"] == "decided" and row["source_choice"] == "keep" and row["decided_at"]
    assert store.split_overview(db)["proposals"] == []


def test_nothing_accepted_asks_no_source_question(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    assert store.reject_split_group(db, g1)["ask_source"] is False
    assert store.reject_split_group(db, g2)["ask_source"] is False
    row = dict(db.execute("SELECT * FROM split_proposals WHERE id=?", (pid,)).fetchone())
    assert row["status"] == "decided" and row["source_choice"] is None and row["decided_at"]


def test_retire_source_rearms_classify(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    store.accept_split_group(db, g1)
    store.accept_split_group(db, g2)
    assert db.execute("UPDATE studies SET classified_version=?", ("classify-2",)).rowcount
    db.commit()
    out = store.set_split_source(db, pid, "retire")
    assert out["choice"] == "retire" and out["studies_to_resort"] == 4         # the four loose ones left
    assert _status(db, "ai-search") == "retired"
    for sid in world["loose"][1:]:
        study = store.get_study(db, sid)
        assert study["subtopic_id"] is None and study["classified_version"] is None
    assert {s["id"] for s in store.studies_to_classify(db, classify.PROMPT_VERSION)} >= set(world["loose"][1:])
    assert _sub_of(db, world["a"][0]) == "what-ai-cites"                        # moved studies keep their part
    assert db.execute("SELECT status FROM split_proposals").fetchone()[0] == "decided"


def test_retire_refused_for_other(db):
    store.seed_subtopics(db, ["Links", "Other"])
    ids = add_many(db, 12, subtopic="other", metric="clicks")
    add_many(db, 8, subtopic="links", metric="clicks")
    out = split.apply(db, _answer([{"name": "Part one", "studies": [f"s{i}" for i in ids[:6]]},
                                   {"name": "Part two", "studies": [f"s{i}" for i in ids[6:]]}], "subtopic:other"))
    assert out["applied"] == 1
    pid = db.execute("SELECT id FROM split_proposals").fetchone()[0]
    for gid in _gids(db, pid):
        store.accept_split_group(db, gid)
    with pytest.raises(ConfigError, match='"Other" cannot be retired'):
        store.set_split_source(db, pid, "retire")
    assert _status(db, "other") == "seeded" and db.execute("SELECT status FROM split_proposals").fetchone()[0] == "pending"
    with pytest.raises(ConfigError, match="keep or retire"):
        store.set_split_source(db, pid, "drop")
    assert store.set_split_source(db, pid, "keep")["choice"] == "keep"


def test_proposal_decided_after_source_choice(db, world):
    pid = _propose(db, world)
    for gid in _gids(db, pid):
        store.accept_split_group(db, gid)
    assert db.execute("SELECT status FROM split_proposals").fetchone()[0] == "pending"     # waits for the source
    store.set_split_source(db, pid, "keep")
    assert db.execute("SELECT status FROM split_proposals").fetchone()[0] == "decided"
    with pytest.raises(ConfigError, match="not asked"):
        store.set_split_source(db, pid, "retire")


def test_judge_proposal_never_joins_split_name(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    rows = db.execute("SELECT COUNT(*) FROM subtopics").fetchone()[0]
    assert store.subtopic_for_choice(db, {"propose": "What AI cites"}, create=True) == (None, None)
    assert store.subtopic_for_choice(db, {"propose": "what-ai-cites"}) == (None, None)
    assert store.subtopic_for_choice(db, {"slug": "what-ai-cites"}) == (None, None)
    assert db.execute("SELECT COUNT(*) FROM subtopics").fetchone()[0] == rows
    item = {"subtopic_choice": json.dumps({"propose": "What AI cites"})}
    assert store.subtopic_of_item(db, item) is None
    store.reject_split_group(db, g1)                                           # a later reject moves nothing
    assert {_sub_of(db, s) for s in world["a"]} == {"ai-search"}
    assert store.subtopic_for_choice(db, {"propose": "What AI cites"}) == (None, None)      # rejected: no subtopic


def test_subtopic_ops_refuse_split_names(db, world):
    _propose(db, world)
    for call in (lambda: store.accept_subtopic(db, "what-ai-cites"),
                 lambda: store.reject_subtopic(db, "what-ai-cites"),
                 lambda: store.map_subtopic(db, "what-ai-cites", "links"),
                 lambda: store.merge_subtopics(db, "what-ai-cites", "links"),
                 lambda: store.rename_subtopic(db, "what-ai-cites", "Else")):
        with pytest.raises(ConfigError, match='"what-ai-cites" is a split proposal: use ev split'):
            call()
    with pytest.raises(ConfigError, match="not an active"):
        store.merge_subtopics(db, "links", "what-ai-cites")


def test_metric_ops_refuse_split_names(db):
    ids = add_many(db, 12, metric="other")
    add_many(db, 8, metric="clicks")
    split.apply(db, _answer([{"name": "AI citations", "studies": [f"s{i}" for i in ids[:6]]}], "metric:other"))
    for call in (lambda: store.accept_metric(db, "ai-citations"), lambda: store.reject_metric(db, "ai-citations"),
                 lambda: store.merge_metrics(db, "ai-citations", "clicks"),
                 lambda: store.rename_metric(db, "ai-citations", "Else")):
        with pytest.raises(ConfigError, match='"ai-citations" is a split proposal: use ev split'):
            call()


def test_lists_mark_split_names(db, world):
    pid = _propose(db, world)
    before = store.subtopics_overview(db)
    rows = {r["slug"]: r for r in before["subtopics"]}
    assert rows["what-ai-cites"]["split"] == pid and rows["geo-tactics"]["split"] == pid
    assert rows["ai-search"]["split"] is None and rows["links"]["split"] is None
    assert before["proposals"] == 0                                            # split names are not judge proposals
    store.subtopic_for_choice(db, {"propose": "Image SEO"}, create=True)
    assert store.subtopics_overview(db)["proposals"] == 1
    g1, _ = _gids(db, pid)
    store.accept_split_group(db, g1)
    assert {r["slug"]: r for r in store.subtopics_overview(db)["subtopics"]}["what-ai-cites"]["split"] is None


def test_split_overview_shape(db, world):
    pid = _propose(db, world)
    (proposal,) = store.split_overview(db)["proposals"]
    assert proposal["id"] == pid and proposal["target"] == "subtopic" and proposal["studies_at"] == 20
    assert proposal["source"] == {"slug": "ai-search", "name": "AI search"} and proposal["source_choice"] is None
    first = proposal["groups"][0]
    assert first == {"id": _gids(db, pid)[0], "mode": "new", "slug": "what-ai-cites", "name": "What AI cites",
                     "status": "pending", "studies": 6, "examples": first["examples"]}
    assert len(first["examples"]) == 3 and proposal["groups"][1]["studies"] == 10


def test_accepted_name_lands_after_source_in_topic_json(workspace):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    store.seed_metrics(conn, cfg.topic.metrics)
    ids = add_many(conn, 16, subtopic="ai-overviews-ai-search", metric="clicks")
    add_many(conn, 24, metric="clicks")
    split.apply(conn, _answer([{"name": "Part one", "studies": [f"s{i}" for i in ids[:8]]},
                               {"name": "Part two", "studies": [f"s{i}" for i in ids[8:12]]}],
                              "subtopic:ai-overviews-ai-search"))
    conn.close()
    cfg = config.load()
    out = api.split_accept(cfg, 1)
    assert out["accepted"] == 1 and out["moved_studies"] == 8
    path = workspace / "topics" / "seo" / "topic.json"
    names = json.loads(path.read_text(encoding="utf-8"))["subtopics"]
    at = names.index("AI Overviews & AI search")
    assert names[at + 1] == "Part one" and names[-1] == "Other"
    api.split_accept(config.load(), 2)
    names = json.loads(path.read_text(encoding="utf-8"))["subtopics"]
    assert names[at + 1:at + 3] == ["Part two", "Part one"]                    # right after the source, newest first
    # the list order shows up in the database after seeding: the confirmed part sorts with the seeded ones
    with api._topic_db(config.load()) as conn:
        order = [t["name"] for t in store.active_subtopics(conn)]
    assert order.index("Part two") == order.index("AI Overviews & AI search") + 1
    api.split_source(config.load(), 1, "retire")
    assert "AI Overviews & AI search" not in json.loads(path.read_text(encoding="utf-8"))["subtopics"]
    with api._topic_db(config.load()) as conn:
        assert store.get_subtopic(conn, "ai-overviews-ai-search")["status"] == "retired"


def test_accepted_metric_name_lands_before_other_in_topic_json(workspace):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    store.seed_metrics(conn, cfg.topic.metrics)
    ids = add_many(conn, 12, metric="other")
    add_many(conn, 8, metric="clicks")
    split.apply(conn, _answer([{"name": "PRs merged", "studies": [f"s{i}" for i in ids[:6]]}], "metric:other"))
    conn.close()
    out = api.split_accept(config.load(), 1)
    assert out["kind"] == "metric" and out["moved_studies"] == 6
    metrics = json.loads((workspace / "topics" / "seo" / "topic.json").read_text(encoding="utf-8"))["metrics"]
    assert metrics[-2:] == ["PRs merged", "Other"]


def test_accepted_metric_name_without_a_metrics_key_writes_the_default_list(workspace):
    path = workspace / "topics" / "seo" / "topic.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("metrics")
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_metrics(conn, cfg.topic.metrics)
    ids = add_many(conn, 12, metric="other")
    add_many(conn, 8, metric="clicks")
    split.apply(conn, _answer([{"name": "PRs merged", "studies": [f"s{i}" for i in ids[:6]]}], "metric:other"))
    conn.close()
    api.split_accept(config.load(), 1)
    written = json.loads(path.read_text(encoding="utf-8"))["metrics"]
    assert written[:3] == ["Clicks", "Impressions", "Rankings"] and written[-2:] == ["PRs merged", "Other"]


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_split_cli_roundtrip(workspace, capsys):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    store.seed_metrics(conn, cfg.topic.metrics)
    ids = add_many(conn, 18, subtopic="ai-overviews-ai-search", metric="clicks")
    add_many(conn, 22, metric="clicks")
    split.apply(conn, _answer([{"name": "Part one", "studies": [f"s{i}" for i in ids[:6]]},
                               {"name": "Part two", "studies": [f"s{i}" for i in ids[6:12]]},
                               {"name": "Part three", "studies": [f"s{i}" for i in ids[12:15]]}],
                              "subtopic:ai-overviews-ai-search"))
    conn.close()
    code, out, _ = _run(capsys, "split", "list")
    assert code == 0 and out["proposals"][0]["source"]["slug"] == "ai-overviews-ai-search"
    assert [g["name"] for g in out["proposals"][0]["groups"]] == ["Part one", "Part two", "Part three"]
    assert _run(capsys, "split", "accept", "1")[1]["moved_studies"] == 6
    assert _run(capsys, "split", "rename", "2", "Second part")[1] == {"renamed": 2, "name": "Second part"}
    assert _run(capsys, "split", "accept", "2")[1]["name"] == "Second part"
    out = _run(capsys, "split", "reject", "3")[1]
    assert out["rejected"] == 3 and out["ask_source"] is True
    assert _run(capsys, "split", "source", "1", "keep")[1]["choice"] == "keep"
    assert _run(capsys, "split", "list")[1] == {"proposals": []}
    code, _, err = _run(capsys, "split", "accept", "1")
    assert code == 2 and "already decided" in err
    code, _, err = _run(capsys, "split", "source", "1", "drop")
    assert code == 2 and "keep or retire" in err
    with pytest.raises(SystemExit):                                            # argparse: a group id is a number
        cli.main(["split", "accept", "x"])


def test_split_name_choice_keeps_study_in_classify_queue(db, world):
    """A judge or classify proposal that hits a pending split name is no choice: the study stays
    without a subtopic and classify asks it again (it is never marked classified for it)."""
    from evidence_echo.stages import match
    from tests.test_judge_subtopics import _apply as judge_apply
    _propose(db, world)
    iid, out = judge_apply(db, subtopic={"propose": "What AI cites"}, metric="clicks")
    assert out["applied"] == 1, out
    match.fixed_pass(db)
    sid = store.get_item(db, iid)["study_id"]
    found = store.get_study(db, sid)
    assert found["subtopic_id"] is None and found["classified_version"] is None
    db.execute("UPDATE items SET state='verified' WHERE id=?", (iid,))       # as after the verify stage
    db.commit()
    assert sid in [s["id"] for s in store.studies_to_classify(db, classify.PROMPT_VERSION)]
    reply = {"id": f"s{sid}", "subtopic": {"propose": "What AI cites"}, "metric": "clicks",
             "entities": [], "data_date": "2024", "headline": None}
    out = classify.apply(db, json.dumps({"results": [reply]}))
    assert out["applied"] == 0 and "split proposal" in out["rejected"][0]["errors"][0]
    assert store.get_study(db, sid)["classified_version"] is None
    assert sid in [s["id"] for s in store.studies_to_classify(db, classify.PROMPT_VERSION)]


def test_split_decisions_do_not_raise_the_template_question(workspace):
    """Accepting a part or retiring the source edits topic.json's list; that is the owner's own list
    now, so the template-switch question (which would undo it) is not raised for this template."""
    cfg = config.load()
    assert "template_update" not in api.subtopics_list(cfg)
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    store.seed_metrics(conn, cfg.topic.metrics)
    ids = add_many(conn, 16, subtopic="ai-overviews-ai-search", metric="clicks")
    add_many(conn, 24, metric="clicks")
    split.apply(conn, _answer([{"name": "Part one", "studies": [f"s{i}" for i in ids[:8]]},
                               {"name": "Part two", "studies": [f"s{i}" for i in ids[8:12]]}],
                              "subtopic:ai-overviews-ai-search"))
    conn.close()
    api.split_accept(config.load(), 1)
    assert "template_update" not in api.subtopics_list(config.load())
    api.split_reject(config.load(), 2)
    api.split_source(config.load(), 1, "retire")
    assert "template_update" not in api.subtopics_list(config.load())


def test_parts_of_the_other_subtopic_land_before_other(workspace):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    store.seed_metrics(conn, cfg.topic.metrics)
    ids = add_many(conn, 12, subtopic="other", metric="clicks")
    add_many(conn, 28, metric="clicks")
    split.apply(conn, _answer([{"name": "Part one", "studies": [f"s{i}" for i in ids[:6]]},
                               {"name": "Part two", "studies": [f"s{i}" for i in ids[6:10]]}],
                              "subtopic:other"))
    conn.close()
    api.split_accept(config.load(), 1)
    names = json.loads((workspace / "topics" / "seo" / "topic.json").read_text(encoding="utf-8"))["subtopics"]
    assert names[-2:] == ["Part one", "Other"]


def test_renamed_source_is_found_in_topic_json_by_slug(workspace):
    """topic.json keeps the listed name after an owner rename; the source is found by its slug."""
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    store.seed_metrics(conn, cfg.topic.metrics)
    store.rename_subtopic(conn, "ai-overviews-ai-search", "AI search")
    ids = add_many(conn, 16, subtopic="ai-overviews-ai-search", metric="clicks")
    add_many(conn, 24, metric="clicks")
    split.apply(conn, _answer([{"name": "Part one", "studies": [f"s{i}" for i in ids[:8]]},
                               {"name": "Part two", "studies": [f"s{i}" for i in ids[8:12]]}],
                              "subtopic:ai-overviews-ai-search"))
    conn.close()
    path = workspace / "topics" / "seo" / "topic.json"
    api.split_accept(config.load(), 1)
    names = json.loads(path.read_text(encoding="utf-8"))["subtopics"]
    assert names[names.index("AI Overviews & AI search") + 1] == "Part one"
    api.split_reject(config.load(), 2)
    api.split_source(config.load(), 1, "retire")
    assert "AI Overviews & AI search" not in json.loads(path.read_text(encoding="utf-8"))["subtopics"]


def test_seeding_leaves_a_listed_split_name_pending(db, world):
    pid = _propose(db, world)
    store.seed_subtopics(db, ["AI search", "What AI cites", "Links", "Other"])
    assert _status(db, "what-ai-cites") == "proposed"
    assert store.subtopics_overview(db)["subtopics"] and any(
        r["slug"] == "what-ai-cites" and r["split"] == pid for r in store.subtopics_overview(db)["subtopics"])


def test_reset_split_decides_a_proposal_waiting_only_for_its_source(db, world):
    """Every group decided, one accepted, the source question open: reset closes the proposal
    (no source answer) instead of deleting it, so its accepted groups and the re-arm count stay."""
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    store.accept_split_group(db, g1)
    store.reject_split_group(db, g2)
    out = store.clear_pending_splits(db)
    assert out["proposals"] == 0 and out["closed"] == 1
    row = db.execute("SELECT status, source_choice, studies_at FROM split_proposals WHERE id=?", (pid,)).fetchone()
    assert (row[0], row[1], row[2]) == ("decided", None, 20)
    assert db.execute("SELECT COUNT(*) FROM split_groups WHERE proposal_id=?", (pid,)).fetchone()[0] == 2
    assert _status(db, "what-ai-cites") == "confirmed"
    assert store.split_overview(db)["proposals"] == []


def test_reset_split_keeps_accepted_part_drops_undecided_one(db, world):
    pid = _propose(db, world)
    g1, g2 = _gids(db, pid)
    store.accept_split_group(db, g1)
    out = store.clear_pending_splits(db)
    assert out == {"proposals": 0, "closed": 1, "groups": 1, "names": 1}
    assert [r[0] for r in db.execute("SELECT id FROM split_groups WHERE proposal_id=?", (pid,))] == [g1]
    assert store.get_subtopic(db, "geo-tactics") is None and _status(db, "what-ai-cites") == "confirmed"
