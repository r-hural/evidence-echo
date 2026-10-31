"""Grouping modes part 1: the `grouping_mode` and `findings_batch` settings."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from evidence_echo import cli
from evidence_echo.core import config, settings, topics
from evidence_echo.core.settings import ConfigError

SETUP_SKILL = (Path(config.__file__).parents[3] / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out else None), out.err


def test_grouping_mode_default_is_questions(workspace):
    cfg = config.load("seo")
    assert cfg.grouping_mode == "questions" and cfg.findings_batch == 40


def test_grouping_mode_resolution_order(workspace, monkeypatch):
    settings.save_global(workspace, {"grouping_mode": "quotes"})
    assert config.load("seo").grouping_mode == "quotes"
    topic = topics.load_topic(workspace / "topics", "seo")
    topics.save_topic(workspace / "topics", replace(topic, grouping_mode="questions"))
    assert config.load("seo").grouping_mode == "questions"
    monkeypatch.setenv("EV_ECHO_GROUPING_MODE", "quotes")
    assert config.load("seo").grouping_mode == "quotes"


def test_grouping_mode_rejects_unknown(workspace, capsys):
    code, _, err = _run(capsys, "config", "set", "grouping_mode", "nope")
    assert code == 2 and "questions, quotes, findings" in err
    code, _, err = _run(capsys, "topic", "set", "seo", "grouping_mode", "x")
    assert code == 2 and "questions, quotes, findings" in err


def test_findings_batch_validation():
    assert settings.check_value("findings_batch", "0", "f") == 0
    assert settings.check_value("findings_batch", 500, "f") == 500
    for bad in (-1, "x", True, 501):
        with pytest.raises(ConfigError, match=r"findings_batch.*\(0 = off\)"):
            settings.check_value("findings_batch", bad, "f")
    assert "findings_batch" in settings.OVERRIDES and "grouping_mode" in settings.OVERRIDES
    assert settings.GROUPING_MODES == ("questions", "quotes", "findings") and settings.FINDINGS_BATCH_MAX == 500


def test_topic_set_grouping_mode_null_restores_global(workspace, capsys):
    settings.save_global(workspace, {"grouping_mode": "quotes"})
    assert _run(capsys, "topic", "set", "seo", "grouping_mode", "questions")[0] == 0
    assert config.load("seo").grouping_mode == "questions"
    assert _run(capsys, "topic", "set", "seo", "grouping_mode", "null")[0] == 0
    assert config.load("seo").grouping_mode == "quotes"


def test_setup_table_lists_grouping_mode():
    assert "| `grouping_mode` |" in SETUP_SKILL


def test_findings_mode_accepted(workspace):
    assert settings.GROUPING_MODES == ("questions", "quotes", "findings")
    settings.save_global(workspace, {"grouping_mode": "findings"})
    assert config.load("seo").grouping_mode == "findings"
