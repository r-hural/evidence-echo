"""The shared card texts (evidence-ui): facts, labels, subtopic, research and reported lines."""
from evidence_echo.render import evidence_text as et
from evidence_echo.render import markdown
from tests import html_fixtures as hf

SUB = {"key": "k", "name": "S", "question": "Q?", "label": "consistent", "independent": 2}


def test_fact_pairs_order_and_baseline_split():
    v = hf.view(flags=["no_baseline"], provenance={"conflict_of_interest": "sells it"},
                metric="clicks", baseline="prior month")
    assert [n for n, _ in markdown.fact_pairs(v)] == [
        "Red flags", "Conflict of interest", "Measured", "Baseline"]


def test_fact_pairs_original_values():
    gone = hf.view(primary={"status": "unreachable"})
    assert markdown.fact_pairs(gone)[-1] == ("Original", "not reachable")
    bad = hf.view(primary={"status": "agreed", "verdict": "misrepresented", "note": "n",
                           "missing": ["1", "2"]})
    value = markdown.fact_pairs(bad)[-1][1]
    assert value.startswith("misrepresented") and "numbers not found — 1, 2" in value


def test_subtopic_lines_with_label():
    assert markdown.subtopic_lines(SUB) == [
        ("Subtopic", "S"), ("Question", "Q? · ✓ consistent (2 independent studies)")]


def test_subtopic_lines_without_question():
    assert markdown.subtopic_lines(dict(SUB, question=None)) == [("Subtopic", "S")]


def test_research_pairs_drop_empty_published():
    assert markdown.research_pairs(hf.entry(1, published=None, research_timeframe=None)) == [
        ("Research", "unstated")]
    assert markdown.research_pairs(hf.entry(1))[1] == ("Published", "2026-08-10")


def test_reported_value_single_and_range():
    assert markdown.reported_value("2026-10-18", "2026-10-18", 1) == "2026-10-18 · in 1 report"
    assert markdown.reported_value("2026-10-18", "2026-10-20", 3) == \
        "first 2026-10-18 · last 2026-10-20 · in 3 reports"


def test_label_text_symbols():
    assert [et.label_text(w) for w in ("consistent", "contested", "varied")] == [
        "✓ consistent", "⇅ contested", "≈ varied"]
    assert et.label_text("other") == "other"


def test_markdown_entry_splits_lines():
    e = hf.entry(1, subtopic=dict(SUB, label="contested"), view=hf.view(metric="m", baseline="b"))
    text = "\n".join(markdown.entry_lines(e))
    assert "Subtopic: S  " in text
    assert "Question: Q? · ⇅ contested (2 independent studies)  " in text
    assert "*Research: 2026-08*  " in text and "*Published: 2026-08-10*  " in text
    assert " | Published" not in text and "; baseline:" not in text
