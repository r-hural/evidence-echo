# tests/test_render_model_subtopics.py
"""Questions: the report model carries each study's subtopic and question; the Markdown report shows one Subtopic line."""
import dataclasses
from datetime import datetime, timezone

from evidence_echo.core import knowledge, store
from evidence_echo.render import markdown, model
from tests import html_fixtures
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)
SUBTOPIC = {"key": "local", "name": "Local", "question": "Does X raise Y?", "label": "consistent",
            "independent": 2, "echo": None}


def test_report_entries_carry_their_question(subtopiced, topic):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="A", items=2)
    add(subtopiced, question=qid, by="B", direction="down")
    loose = add(subtopiced, by="C")
    rep = model.build_report(subtopiced, topic, {}, NOW)
    by_id = {e.study_id: e for e in rep.new + rep.disputed}
    assert by_id[a].subtopic == {"key": "title-tags-meta", "name": "Title tags & meta",
                                 "question": "Does X raise Y?", "label": "contested", "independent": 2,
                                 "echo": None}
    assert by_id[loose].subtopic == {"key": "title-tags-meta", "name": "Title tags & meta",
                                     "question": None, "label": None, "independent": 0, "echo": None}


def test_topic_model_appends_evidence_and_waiting(subtopiced, topic):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="A")
    b = add(subtopiced, by="B")
    store.record_report(subtopiced, "r.md", [(a, "T", "S"), (b, "T2", "S2")])
    t = model.build_topic(subtopiced, topic, NOW)
    assert [e.key for e in t.evidence] == ["title-tags-meta"] and t.waiting == 1
    by_id = {ts.entry.study_id: ts.entry for ts in t.studies}
    assert by_id[a].subtopic["question"] == "Does X raise Y?" and by_id[a].subtopic["label"] is None
    assert by_id[b].subtopic["question"] is None
    names = [f.name for f in dataclasses.fields(model.Topic)]
    assert names[-10:-5] == ["evidence", "waiting", "reports", "metrics", "disclaimer"]     # append-only
    assert names[-5:-2] == ["mode", "findings", "finding_count"]
    subtopic_field = {f.name: f for f in dataclasses.fields(model.Entry)}["subtopic"]
    assert subtopic_field.default is None


def test_markdown_subtopic_line_sits_above_the_research_line():
    lines = markdown.entry_lines(html_fixtures.entry(1, subtopic=SUBTOPIC))
    line = "Question: Does X raise Y? · ✓ consistent (2 independent studies)  "
    assert "Subtopic: Local  " in lines
    assert lines[lines.index("Subtopic: Local  ") + 1] == line
    assert lines[lines.index(line) + 1].startswith("*Research:")


def test_card_line_with_question_and_label():
    assert markdown.subtopic_lines(SUBTOPIC) == [
        ("Subtopic", "Local"), ("Question", "Does X raise Y? · ✓ consistent (2 independent studies)")]
    one = dict(SUBTOPIC, independent=3)
    assert markdown.subtopic_lines(one)[-1][1].endswith("consistent (3 independent studies)")


def test_card_line_without_label_or_question():
    assert markdown.subtopic_lines(dict(SUBTOPIC, label=None)) == [
        ("Subtopic", "Local"), ("Question", "Does X raise Y?")]
    alone = dict(SUBTOPIC, label=None, question=None, independent=0)
    assert markdown.subtopic_lines(alone) == [("Subtopic", "Local")]
    assert "Subtopic: Local  " in markdown.entry_lines(html_fixtures.entry(1, subtopic=alone))


def test_entry_without_a_subtopic_has_no_subtopic_line():
    assert not any(l.startswith("Subtopic:") for l in markdown.entry_lines(html_fixtures.entry(2)))


def test_counts_text():
    group = knowledge.QuestionGroup(question_id=1, text="t", measure="m", subtopic_key="local", label=None,
                                    independent=3, articles=1, best_tier=None, groups=[], directions={},
                                    echoes={})
    assert markdown.counts_text(group) == "3 independent studies · 1 article · best ?"


def test_quote_text():
    assert markdown.quote_text("  a  “b”\n c ") == "“a “b” c”"


def test_unsorted_studies_have_no_subtopic_line(subtopiced, topic):
    a = add(subtopiced, subtopic=None, by="A")
    add(subtopiced, subtopic=None, by="B", direction="down")
    rep = model.build_report(subtopiced, topic, {}, NOW)
    by_id = {e.study_id: e for e in rep.new + rep.disputed}
    assert by_id[a].subtopic is None
    assert not any(l.startswith("Subtopic:") for l in markdown.entry_lines(by_id[a]))
