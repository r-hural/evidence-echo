"""Templates suggest a grouping mode; the topic gets it only when the owner accepts (task 9)."""
import json

import pytest

from evidence_echo import api
from evidence_echo.core import config, migrate, topics
from tests.conftest import PLUGIN_DIR

TEMPLATES = PLUGIN_DIR / "templates"
SUGGESTED = {"exercise": "findings", "nutrition": "findings", "seo": "questions", "cro": "questions",
             "ai-coding": "questions"}
SETUP = PLUGIN_DIR / "skills" / "setup" / "SKILL.md"


@pytest.mark.parametrize("tid,mode", sorted((t, m) for t, m in SUGGESTED.items()
                                             if t in topics.template_ids(TEMPLATES)))   # the public tree has no nutrition
def test_template_suggestions(tid, mode):
    assert topics.template_grouping_mode(tid, TEMPLATES) == mode
    assert {r["id"]: r["grouping_mode"] for r in topics.list_templates(TEMPLATES)}[tid] == mode


def test_blank_and_unknown_have_no_suggestion():
    assert topics.template_grouping_mode("blank", TEMPLATES) is None
    assert topics.template_grouping_mode("nope", TEMPLATES) is None


def test_create_from_template_writes_no_grouping_mode(tmp_path):
    t = topics.create_from_template(tmp_path, "ex", "exercise", TEMPLATES)
    assert t.grouping_mode is None
    assert "grouping_mode" not in json.loads((tmp_path / "ex" / topics.TOPIC_FILE).read_text("utf-8")) \
        or json.loads((tmp_path / "ex" / topics.TOPIC_FILE).read_text("utf-8"))["grouping_mode"] is None
    assert topics.load_template("exercise", TEMPLATES).grouping_mode is None


def test_migrated_topic_writes_no_grouping_mode(tmp_path):
    d = tmp_path / "data" / "exercise"
    d.mkdir(parents=True)
    (d / "echo.db").write_bytes(b"")
    migrate.run(tmp_path / "data", tmp_path / "pd", tmp_path / "home", TEMPLATES)
    raw = json.loads((tmp_path / "data" / "topics" / "exercise" / topics.TOPIC_FILE).read_text("utf-8"))
    assert raw.get("grouping_mode") is None


@pytest.mark.parametrize("tid", topics.template_ids(TEMPLATES))
def test_overlay_templates_match_grouping_mode(tid):
    overlay = PLUGIN_DIR.parents[1] / "release" / "overlay" / "plugins" / "evidence-echo" / "templates"
    if not overlay.is_dir():
        pytest.skip("no release/overlay in the public tree")
    if not (overlay / f"{tid}.json").exists():
        pytest.skip("template not in the overlay")
    private = json.loads((TEMPLATES / f"{tid}.json").read_text(encoding="utf-8"))
    public = json.loads((overlay / f"{tid}.json").read_text(encoding="utf-8"))
    assert public.get("grouping_mode") == private.get("grouping_mode")


def test_topic_add_reports_suggestion(workspace):
    ws = config.workspace()
    assert api.topic_add(ws, "ex", "exercise")["suggested_grouping_mode"] == "findings"
    assert api.topic_add(ws, "s2", "seo")["suggested_grouping_mode"] == "questions"
    out = api.topic_add(ws, "b", None, "B", "d", "s")
    assert out["suggested_grouping_mode"] is None and out["grouping_mode"] is None


def test_setup_skill_asks_grouping_mode():
    text = SETUP.read_text(encoding="utf-8")
    assert "Grouping mode for" in text and "ev topic set <id> grouping_mode" in text
    assert "suggested for this field" in text and "re-read" in text
