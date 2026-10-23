"""Glossary rename: the engine, LLM contract and CLI say subtopic; the old words are gone."""
import json

import pytest

from evidence_echo import cli
from evidence_echo.core import contracts, store
from evidence_echo.stages import judge
from tests.test_judge import VERDICT, _seed_extracted
from tests.test_judge_subtopics import _apply


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_cli_subtopics_list(workspace, capsys):
    code, out, _ = _run(capsys, "subtopics", "list")
    assert code == 0 and out["proposals"] == 0 and len(out["subtopics"]) == 15 and "themes" not in out


def test_cli_themes_is_gone(workspace, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["themes", "list"])
    assert exc.value.code == 2


def test_old_theme_field_is_not_accepted():
    answer = {"id": "s1", "theme": {"slug": "local"}, "metric": "clicks", "entities": [],
              "direction": "positive"}
    accepted, rejected = contracts.check_payload("classify", json.dumps({"results": [answer]}), {"s1"})
    assert not accepted and "unexpected field 'theme'" in json.dumps(rejected)


def test_judge_ignores_the_old_theme_field(db, topic):
    verdict = {k: v for k, v in VERDICT.items() if k != "subtopic"}
    verdict["theme"] = {"propose": "Title tags & meta"}
    _apply(db, verdict=verdict)
    assert [s for s in store.list_subtopics(db) if s["status"] == "proposed"] == []


def test_judge_emit_carries_subtopics(db, topic):
    store.seed_subtopics(db, topic.subtopics)
    _seed_extracted(db)
    payload = json.loads(judge.emit(db, topic))
    assert len(payload["subtopics"]) == 15 and payload["topic"]["subtopics"]
    assert "themes" not in payload and "themes" not in payload["topic"]
