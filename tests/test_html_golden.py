# tests/test_html_golden.py
"""Characterization: the topic page and the report page of a questions-mode topic, byte for byte.

The golden files under tests/fixtures/html_golden/ were written once, before the grouping-modes work
(HTML_GOLDEN_WRITE=1 creates missing files, it never overwrites one). Never edit or regenerate them.
Any difference is a questions-mode regression in render/html.py, render/model.py or html_assets.py.
"""
import dataclasses
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evidence_echo.core import knowledge, store
from evidence_echo.render import html as html_render
from evidence_echo.render import model
from tests.test_report_golden import NOW, _study, frozen  # noqa: F401  (fixture)

GOLDEN = Path(__file__).parent / "fixtures" / "html_golden"

T = {n: f"Sample {n} measured a {n * 7}% change across {n}00 pages in March." for n in range(1, 9)}


def _s(db, n, primary, skeptic="A", by="Acme", facts=None, **over):
    facts = dict({"what_was_tested": f"Test number {n}", "findings": f"+{n}% in test {n}"}, **(facts or {}))
    return _study(db, n, text=T[n], numbers=[f"{n * 7}%"], quotes=[{"number": f"{n * 7}%", "quote": T[n]}],
                  primary=primary, skeptic=skeptic, facts=facts,
                  provenance={"conducted_by": by, "relation": "agency", "conflict_of_interest": None},
                  note="checked", **over)


def _question(db, sid, text, kind="difference", comparison="with the change vs without"):
    sub = store.get_study(db, sid)["subtopic_id"]
    return store.create_question(db, sub, text, "clicks", kind, comparison if kind == "difference" else None)


def build_golden_db(tmp_path):
    """A topic with a contested question (explained), a level question, a study not grouped yet, a
    disputed study and graded labels; two studies are new (not in any report yet)."""
    db = store.connect(tmp_path / "golden.db")
    from tests.conftest import TEST_METRICS
    store.seed_metrics(db, TEST_METRICS)
    s1 = _s(db, 1, "https://one.example.com/a", by="Acme")
    s2 = _s(db, 2, "https://two.example.com/b", by="Beta", facts={"study_type": "observational"})
    s3 = _s(db, 3, "https://three.example.com/c", by="Beta")
    s4 = _s(db, 4, "https://four.example.com/d", by="Delta")
    s5 = _s(db, 5, "https://five.example.com/e", by="Echo")
    s6 = _s(db, 6, "https://six.example.com/f", skeptic="C", by="Fox")          # grade_gap dispute
    s7 = _s(db, 7, "https://seven.example.com/g", by="Gamma", facts={"study_type": "observational"})
    s8 = _s(db, 8, "https://eight.example.com/h", by="Hotel")
    contested = _question(db, s1, "Do rewritten titles raise clicks?")
    for sid, direction in ((s1, "up"), (s2, "down"), (s3, "up")):
        store.set_study_question(db, sid, contested, direction, store.GROUP_VERSION)
    level = _question(db, s4, "How many pages does a title test cover?", kind="level")
    store.set_study_question(db, s4, level, None, store.GROUP_VERSION)
    store.set_study_question(db, s7, level, None, store.GROUP_VERSION)
    group = next(g for e in knowledge.evidence(db) for g in e.questions if g.question_id == contested)
    state = knowledge.explain_state(db, group)
    store.save_question_explanation(
        db, question_id=contested, status="agreed", summary="The studies disagree on direction.",
        main_result="up", lines=[{"study": s2, "reason": "genuine_conflict", "quote": T[2],
                                  "note": "Observational, not controlled."}],
        inputs_hash=state.inputs_hash, prompt_version="relate-4")
    store.record_report(db, "2026-10-16-report.md", [(s, f"Title {s}", f"Summary {s}")
                                                     for s in (s1, s2, s3, s4, s5, s6)])
    return db


def golden_entries(db) -> dict:
    """The accepted title and summary of each study the golden report shows as new."""
    return {sid: {"title": f"Title {sid}", "summary": f"Summary {sid}"} for sid in model.split(db)[0]}


class _Now(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture
def golden_db(tmp_path, frozen, monkeypatch):
    monkeypatch.setattr(html_render, "datetime", _Now)      # the page footer's "now"
    db = build_golden_db(tmp_path)
    yield db
    db.close()


@pytest.fixture
def labelled(topic):
    """The seo template lists no grade labels; this topic shows `observational`."""
    return dataclasses.replace(topic, grading={**topic.grading, "labels": ["observational"]})


def _check(name: str, actual: str) -> None:
    path = GOLDEN / name
    if not path.exists():
        if os.environ.get("HTML_GOLDEN_WRITE") == "1":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(actual, encoding="utf-8", newline="\n")
            pytest.skip("golden written")
        pytest.fail(f"missing golden file {path} (run once with HTML_GOLDEN_WRITE=1)")
    assert actual == path.read_text(encoding="utf-8"), f"{name} differs from the golden file"


def test_topic_page_matches_golden(golden_db, labelled):
    t = model.build_topic(golden_db, labelled, NOW)
    _check("topic.html", html_render.topic_page(t, "overview.html", "2026-10-16-report.html", "seo.html"))


def test_report_page_matches_golden(golden_db, labelled):
    r = model.build_report(golden_db, labelled, golden_entries(golden_db), NOW)
    _check("report.html", html_render.report_page(r, "seo.html", "overview.html"))


def test_quotes_mode_pages_render(golden_db, labelled):
    t = model.build_topic(golden_db, labelled, NOW, "quotes")
    topic_html = html_render.topic_page(t, "overview.html", "2026-10-16-report.html", "seo.html")
    r = model.build_report(golden_db, labelled, golden_entries(golden_db), NOW, "quotes")
    report_html = html_render.report_page(r, "seo.html", "overview.html")
    assert 'id="ee-findings"' in topic_html and 'id="ee-evidence"' not in topic_html
    assert t.finding_count == 8 and "findings waiting" not in topic_html
    assert "<b>Findings:</b>" in report_html and "Question" not in report_html.split("</header>", 1)[1]
    assert "Do rewritten titles raise clicks?" not in topic_html     # the questions are kept, not shown


def test_findings_mode_pages_render(golden_db, labelled):
    """The golden DB grouped in findings mode renders both pages; the questions goldens still match."""
    db = golden_db
    asked = {}
    for s in store.active_studies(db):
        item = store.study_items(db, s["id"], ("verified",))[0]["id"]
        store.write_findings(db, s["id"], item, [{"text": f"Finding of study {s['id']}", "quote": T[s["id"]],
                                                   "quote_checked": True}], store.FINDINGS_VERSION)
        fid = store.study_findings(db, s["id"])[0]["id"]
        key = (s["subtopic_id"], s["id"] % 2)
        if key not in asked:
            asked[key] = store.create_question(db, s["subtopic_id"], f"Findings question {len(asked)}?", "clicks",
                                                "difference", "with vs without", mode="findings")
        store.set_finding_question(db, fid, asked[key], "up", "group-f1")
    t = model.build_topic(db, labelled, NOW, "findings")
    topic_html = html_render.topic_page(t, "overview.html", "2026-10-16-report.html", "seo.html")
    r = model.build_report(db, labelled, golden_entries(db), NOW, "findings")
    report_html = html_render.report_page(r, "seo.html", "overview.html")
    assert 'id="ee-evidence"' in topic_html and 'id="ee-findings"' not in topic_html
    assert "<b>Questions:</b>" in topic_html and "<b>Questions:</b>" in report_html
    assert "Finding of study 1" in topic_html and "Findings question 0?" in topic_html
    assert t.finding_count >= 6
    # questions mode is untouched by the findings rows
    q = model.build_topic(db, labelled, NOW)
    _check("topic.html", html_render.topic_page(q, "overview.html", "2026-10-16-report.html", "seo.html"))
