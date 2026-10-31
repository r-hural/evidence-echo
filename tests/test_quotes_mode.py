"""Quotes mode: group, split and relate are skipped; questions survive a switch to quotes and back."""
import json
from contextlib import closing

from evidence_echo import api, cli
from evidence_echo.core import config, settings, store
from tests.conftest import FakeBackend
from tests.test_knowledge_questions import add, q


def _cfg(workspace, **values):
    settings.save_global(workspace, values)
    return config.load("seo")


def _grouped(cfg):
    """Three studies in one question with an explanation, one of them moved by the owner."""
    with closing(store.connect(cfg.db_path)) as conn:
        store.seed_subtopics(conn, cfg.topic.subtopics)
        qid = q(conn)
        sids = [add(conn, question=qid, by=name, direction=d) for name, d in (("A", "up"), ("B", "down"), ("C", "up"))]
        store.move_study(conn, sids[2], qid)
        store.save_question_explanation(conn, question_id=qid, status="agreed", summary="S", main_result="up",
                                        lines=[], inputs_hash="h", prompt_version="relate-4")
        return qid, sids


def _snapshot(cfg):
    with closing(store.connect(cfg.db_path)) as conn:
        studies = [dict(r) for r in conn.execute(
            "SELECT id, question_id, question_direction, question_by_owner, grouped_version FROM studies ORDER BY id")]
        expl = [dict(r) for r in conn.execute("SELECT * FROM question_explanations ORDER BY question_id")]
        return studies, expl, [s["id"] for s in store.studies_to_group(conn)]


def test_group_split_relate_skipped_in_quotes_mode(workspace, monkeypatch):
    cfg = _cfg(workspace, grouping_mode="quotes")
    _grouped(cfg)
    for stage in ("group", "split", "relate"):
        data = json.loads(api.emit(cfg, stage))
        assert data["items"] == [] and data["skipped"] == api.QUOTES_MODE == "grouping_mode quotes", stage
        assert data["stage"] == stage
        assert api.run_api(cfg, stage) == {"processed": 0, "rejected": [], "skipped": "grouping_mode quotes"}
    backend = FakeBackend([])
    monkeypatch.setattr(api.llm_api, "AnthropicBackend", lambda key: backend)
    api.run_api(cfg, "group")
    assert backend.prompts == []


def test_questions_mode_still_runs_group(workspace):
    cfg = _cfg(workspace)
    _grouped(cfg)
    assert "skipped" not in json.loads(api.emit(cfg, "group"))


def test_quotes_round_trip_keeps_questions(workspace):
    cfg = _cfg(workspace)
    _grouped(cfg)
    before = _snapshot(cfg)
    quotes = _cfg(workspace, grouping_mode="quotes", findings_batch=40)
    for stage in ("group", "split", "relate", "findings"):
        json.loads(api.emit(quotes, stage))
    assert _snapshot(quotes) == before
    assert _snapshot(_cfg(workspace, grouping_mode="questions")) == before


def test_questions_commands_note_in_quotes_mode(workspace, capsys):
    cfg = _cfg(workspace)
    qid, sids = _grouped(cfg)
    assert "note" not in api.questions_list(cfg)
    _cfg(workspace, grouping_mode="quotes")
    assert cli.main(["questions", "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["note"] == "topic seo is in quotes mode: questions are kept but not shown"
    assert out["questions"] and out["questions"][0]["id"] == f"q{qid}"
    quotes = config.load("seo")
    assert "note" in api.questions_rename(quotes, f"q{qid}", "Renamed?")
    assert "note" in api.questions_move(quotes, f"s{sids[0]}", f"q{qid}")
