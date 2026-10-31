import json

import pytest

from evidence_echo import cli
from evidence_echo.core import config, grading, settings, topics
from evidence_echo.core.settings import ConfigError

TEMPLATES = config.TEMPLATES_DIR


def _file(tmp_path, tid):
    return tmp_path / tid / topics.TOPIC_FILE


def test_template_topic_reads_block_from_template(tmp_path):
    topics.create_from_template(tmp_path, "ex", "exercise", TEMPLATES)
    assert "grading" not in json.loads(_file(tmp_path, "ex").read_text(encoding="utf-8"))
    t = topics.load_topic(tmp_path, "ex")
    assert t.grading_source == "template" and not t.grading_in_file
    assert t.grading["caps"]["study_type"]["animal"] == "C"


def test_blank_topic_uses_generic_block(tmp_path):
    t = topics.create_blank(tmp_path, "b", "B", "d", "s")
    assert t.grading_source == "generic" and t.grading == grading.generic()


def test_seo_template_has_no_block(tmp_path):
    t = topics.create_from_template(tmp_path, "s", "seo", TEMPLATES)
    assert t.grading_source == "generic" and t.grading == grading.generic()


def test_file_block_beats_template(tmp_path):
    topics.create_from_template(tmp_path, "ex", "exercise", TEMPLATES)
    block = {"tiers": {"A": "a", "B": "b", "C": "c", "D": "d"}}
    topics.set_field(tmp_path, "ex", "grading", json.dumps(block))
    t = topics.load_topic(tmp_path, "ex")
    assert t.grading_source == "file" and t.grading_in_file
    assert t.grading["tiers"]["A"] == "a" and t.grading["caps"] == {}
    assert json.loads(_file(tmp_path, "ex").read_text(encoding="utf-8"))["grading"]["tiers"]["B"] == "b"
    assert topics.to_dict(t)["grading"]["tiers"]["C"] == "c"


@pytest.mark.parametrize("bad", [
    '{"tiers": {"A": "x"}}',
    '{"nope": 1}',
    '{"labels": ["not_a_fact"]}',
    '{"tiers": {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}}',
    '{"study_types": ["rct"], "caps": {"study_type": {"animal": "C"}}}',
    '{"disclaimer": "one\\ntwo"}',
    '{"disclaimer": "' + "x" * 201 + '"}',
])
def test_grading_block_rejects_bad_values(tmp_path, bad):
    topics.create_blank(tmp_path, "b", "B", "d", "s")
    with pytest.raises(ConfigError) as exc:
        topics.set_field(tmp_path, "b", "grading", bad)
    assert "field 'grading'" in str(exc.value) and "grading." in str(exc.value)


def test_grading_not_json(tmp_path):
    topics.create_blank(tmp_path, "b", "B", "d", "s")
    for raw in ("not json", "[1]"):
        with pytest.raises(ConfigError, match="a JSON object"):
            topics.set_field(tmp_path, "b", "grading", raw)


def test_set_grading_null_removes_file_block(tmp_path):
    topics.create_from_template(tmp_path, "ex", "exercise", TEMPLATES)
    topics.set_field(tmp_path, "ex", "grading", '{"disclaimer": "Mine."}')
    assert topics.load_topic(tmp_path, "ex").grading_source == "file"
    topics.set_field(tmp_path, "ex", "grading", "null")
    t = topics.load_topic(tmp_path, "ex")
    assert t.grading_source == "template" and "grading" not in json.loads(
        _file(tmp_path, "ex").read_text(encoding="utf-8"))


def test_template_grading(tmp_path):
    assert topics.template_grading("blank", TEMPLATES) is None
    assert topics.template_grading("seo", TEMPLATES) is None
    assert topics.template_grading("nope", TEMPLATES) is None
    assert topics.template_grading("exercise", TEMPLATES)["red_flags"]


def test_cli_topic_grading_prints_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    assert cli.main(["topic", "add", "ex", "--from", "exercise"]) == 0
    capsys.readouterr()
    assert cli.main(["topic", "grading", "ex"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["topic"] == "ex" and out["source"] == "template"
    assert out["grading"]["disclaimer"].startswith("Summary of published studies")
