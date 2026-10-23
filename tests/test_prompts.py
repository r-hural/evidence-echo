import json
from pathlib import Path

from evidence_echo.core import interfaces, store
from evidence_echo.core.topics import Topic
from evidence_echo.stages import judge, match, report, triage, verify
from tests.conftest import FakeBackend, PLUGIN_DIR, make_item, seed_new_study
from tests.test_judge import VERDICT, _seed_extracted

CRO = Topic(id="cro", name="CRO Echo", domain="conversion rate optimization",
            scope="Landing pages, checkout flows and pricing pages.")
TOPIC_BLOCK = {"id": "cro", "name": "CRO Echo", "domain": "conversion rate optimization",
               "scope": "Landing pages, checkout flows and pricing pages.", "subtopics": []}


def _judged(db):
    item_id = _seed_extracted(db)
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(item_id))]}))
    match.fixed_pass(db)


def test_triage_emit_and_prompt_use_topic(db):
    store.insert_items(db, [make_item()])
    payload = json.loads(triage.emit(db, CRO))
    assert payload["topic"] == TOPIC_BLOCK
    assert "conversion rate optimization content" in payload["instructions"]
    assert "Scope: Landing pages" in payload["instructions"]
    assert "Items outside the scope -> pass false" in payload["instructions"]
    backend = FakeBackend(['{"pass": true, "reason": "numbers"}'])
    triage.run_api(db, backend, CRO)
    assert "screening conversion rate optimization content" in backend.prompts[0]
    assert "Scope: Landing pages, checkout flows and pricing pages." in backend.prompts[0]
    assert "Items outside the scope -> pass: false." in backend.prompts[0]


def test_judge_emit_and_prompt_use_topic(db):
    _seed_extracted(db)
    payload = json.loads(judge.emit(db, CRO))
    assert payload["topic"] == TOPIC_BLOCK
    assert "grading conversion rate optimization content" in payload["instructions"]
    assert "Scope: Landing pages" in payload["instructions"]
    backend = FakeBackend([json.dumps(VERDICT)])
    judge.run_api(db, backend, CRO)
    assert "one conversion rate optimization content item" in backend.prompts[0]
    assert "Scope: Landing pages" in backend.prompts[0]


def test_verify_emit_and_prompt_use_topic(db):
    _judged(db)
    payload = json.loads(verify.emit(db, CRO))
    assert payload["topic"] == TOPIC_BLOCK
    assert "auditor of conversion rate optimization content" in payload["instructions"]
    assert "Scope: Landing pages" in payload["instructions"]
    backend = FakeBackend(['{"agrees": true, "adjusted_tier": null, "note": "ok"}'])
    verify.run_api(db, backend, CRO)
    assert "auditor of conversion rate optimization content" in backend.prompts[0]


def test_report_emit_and_heading_use_topic(db, tmp_path):
    payload = json.loads(report.emit(db, CRO, "de"))
    assert payload["topic"] == TOPIC_BLOCK
    assert "Output language: de" in payload["instructions"]
    seed_new_study(db)
    path = Path(report.render(db, '{"results": []}', tmp_path, "de", CRO)["path"])
    assert path.read_text(encoding="utf-8").startswith("# CRO Echo Report - ")


def test_no_seo_literal_in_stage_modules():
    for path in sorted((PLUGIN_DIR / "src" / "evidence_echo" / "stages").glob("*.py")):
        assert "SEO" not in path.read_text(encoding="utf-8"), path.name


def test_skeptic_agent_reads_topic_from_payload():
    text = (PLUGIN_DIR / "agents" / "skeptic.md").read_text(encoding="utf-8")
    assert "topic.domain" in text and "topic.scope" in text
    assert "edition" not in text.lower()
    assert "SEO" not in text


def test_skeptic_cannot_write_files():
    # it reads untrusted article text; a write tool would turn an injection into a file write
    text = (PLUGIN_DIR / "agents" / "skeptic.md").read_text(encoding="utf-8")
    [tools] = [line for line in text.splitlines() if line.startswith("tools:")]
    assert tools == "tools: Read"


def test_report_names_who_ran_the_study():
    # "one practitioner / one vendor reports" reads oddly; the summary names the author instead
    text = report.INSTRUCTIONS
    assert "provenance.conducted_by" in text and "sources" in text
    assert "practitioner reports" not in text and "Never write an anonymous" in text
