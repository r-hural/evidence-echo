# tests/test_apply_two_answers.py
"""Two-file apply (SP3b-0): `apply --stage <stage> <file_a> <file_b>` for TWO_ANSWER_STAGES."""
import inspect
import json
import sqlite3
import types

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config
from evidence_echo.core.settings import ConfigError

A = '{"results": [{"id": "p1", "reason": "method_quality"}]}'
B = '{"results": [{"id": "p1", "reason": "genuine_conflict"}]}'


@pytest.fixture
def dummy(monkeypatch):
    """A registered two-answer stage that records what the engine hands it."""
    calls = []

    def apply(conn, payload_a, payload_b):
        assert isinstance(conn, sqlite3.Connection)
        calls.append((payload_a, payload_b))
        return {"applied": 1, "rejected": [], "undecided": 0}

    monkeypatch.setitem(api.LLM_STAGES, "dummy", types.SimpleNamespace(apply=apply))
    monkeypatch.setattr(api, "TWO_ANSWER_STAGES", (*api.TWO_ANSWER_STAGES, "dummy"))
    return calls


def _files(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(A, encoding="utf-8")
    b.write_text(B, encoding="utf-8")
    return str(a), str(b)


def test_every_two_answer_stage_is_an_llm_stage_with_a_two_payload_apply():
    for stage in api.TWO_ANSWER_STAGES:          # SP3b-T and SP3b-P register theirs here
        assert stage in api.LLM_STAGES, stage
        assert list(inspect.signature(api.LLM_STAGES[stage].apply).parameters)[:3] == [
            "conn", "payload_a", "payload_b"], stage


def test_api_hands_both_payloads_in_order(workspace, dummy):
    out = api.apply(config.load(), "dummy", A, payload_b=B)
    assert out == {"applied": 1, "rejected": [], "undecided": 0}
    assert dummy == [(A, B)]


def test_api_refuses_one_payload_for_a_two_answer_stage(workspace, dummy):
    with pytest.raises(ConfigError, match="needs two results files"):
        api.apply(config.load(), "dummy", A)
    assert dummy == []


@pytest.mark.parametrize("stage", ["triage", "judge", "verify", "match", "report", "fetch"])
def test_api_refuses_two_payloads_for_a_one_answer_stage(workspace, stage):
    with pytest.raises(ConfigError, match="takes one results file"):
        api.apply(config.load(), stage, A, payload_b=B)


def test_cli_two_files(workspace, tmp_path, dummy, capsys):
    a, b = _files(tmp_path)
    assert cli.main(["apply", "--stage", "dummy", a, b]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == 1
    assert dummy == [(A, B)]


def test_cli_one_file_for_a_two_answer_stage_exits_2(workspace, tmp_path, dummy, capsys):
    a, _ = _files(tmp_path)
    assert cli.main(["apply", "--stage", "dummy", a]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--stage dummy needs two results files" in captured.err
    assert dummy == []


def test_cli_two_files_for_a_one_answer_stage_exits_2(workspace, tmp_path, capsys):
    a, b = _files(tmp_path)
    assert cli.main(["apply", "--stage", "triage", a, b]) == 2
    assert "--stage triage takes one results file" in capsys.readouterr().err


def test_cli_a_missing_second_file_exits_1(workspace, tmp_path, dummy, capsys):
    a, _ = _files(tmp_path)
    assert cli.main(["apply", "--stage", "dummy", a, str(tmp_path / "missing.json")]) == 1
    assert "missing.json" in capsys.readouterr().err
    assert dummy == []


def test_cli_three_files_is_a_usage_error(workspace, tmp_path, dummy, capsys):
    a, b = _files(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.main(["apply", "--stage", "dummy", a, b, b])
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_ruling_with_a_two_answer_stage_is_refused(workspace, tmp_path, dummy, capsys):
    a, b = _files(tmp_path)
    assert cli.main(["apply", "--stage", "dummy", "--ruling", a, b]) == 1
    assert "--ruling applies only to --stage verify" in capsys.readouterr().err
    assert dummy == []


def test_cli_the_same_file_twice_exits_2(workspace, tmp_path, dummy, capsys):
    a, _ = _files(tmp_path)
    assert cli.main(["apply", "--stage", "dummy", a, a]) == 2
    assert "needs two different results files, one per blind answer" in capsys.readouterr().err
    assert dummy == []
