"""Every shipped template is complete: its lists end with Other, it has enough sources, and its
source names are publications, not people. Runs on the private templates and, in the public tree,
on the shorter lists from release/overlay."""
import json
import re

import pytest

from evidence_echo.core import topics
from tests.conftest import PLUGIN_DIR

TEMPLATES = PLUGIN_DIR / "templates"
HEALTH = ("nutrition", "exercise")


@pytest.mark.parametrize("tid", topics.template_ids(TEMPLATES))
def test_template_is_complete(tid):
    t = topics.load_template(tid, TEMPLATES)
    assert t.subtopics[-1] == "Other" and t.metrics[-1] == "Other"
    assert len(set(t.subtopics)) == len(t.subtopics) and len(set(t.metrics)) == len(t.metrics)
    assert 7 <= len(t.sources) <= 30
    assert {s["type"] for s in t.sources} <= {"rss", "page"}
    assert len({s["url"] for s in t.sources}) == len(t.sources)
    # "Growth Memo", not "Growth Memo (Kevin Indig)": demo pages and reports show source names
    assert [s["name"] for s in t.sources if re.search(r"\([A-Z][a-z]+ [A-Z][a-z]+\)", s["name"])] == []


@pytest.mark.parametrize("tid", [t for t in topics.template_ids(TEMPLATES) if t in HEALTH])
def test_health_templates_say_not_advice(tid):
    data = json.loads((TEMPLATES / f"{tid}.json").read_text(encoding="utf-8"))
    assert re.search(r"Research tracking only, not [a-z, ]*advice", data["scope"])
