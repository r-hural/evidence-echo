"""Quotes-mode Markdown: findings under each entry; questions-mode entries are unchanged."""
import re

from evidence_echo.render import markdown
from tests.html_fixtures import entry, finding, report

EVIL = "<b>x</b> [link](http://e) &amp; [[x]] #tag | a"
SUBTOPIC = {"key": "local", "name": "Local", "question": None, "label": None, "independent": 0, "echo": None}


def test_markdown_quotes_entry_lists_findings():
    e = entry(1, subtopic=SUBTOPIC, findings=[finding(1), finding(2, quote_checked=False)], findings_state="done")
    lines = markdown.entry_lines(e)
    assert "Subtopic: Local  " in lines
    at = lines.index("Findings:  ")
    assert lines[at - 1] == "Subtopic: Local  "
    assert lines[at + 1] == "- Clicks rose 1%: “Clicks rose 1% on forty pages.”  "
    assert lines[at + 2] == "- Clicks rose 2%: “Clicks rose 2% on forty pages.” # quote not found in the article  "
    assert "Question:" not in "\n".join(lines)
    text = markdown.report_markdown(report(new=[e], mode="quotes"))
    assert "Findings:  \n- Clicks rose 1%" in text


def test_markdown_findings_waiting_failed_and_none():
    assert markdown.findings_lines([], "waiting") == ["Findings: waiting  "]
    assert markdown.findings_lines([], "failed") == [] and markdown.findings_lines([], None) == []
    waiting = entry(2, findings=[], findings_state="waiting")
    assert "Findings: waiting  " in markdown.entry_lines(waiting)
    failed = entry(3, findings=[], findings_state="failed")
    assert not any(line.startswith("Findings") for line in markdown.entry_lines(failed))


def test_markdown_questions_entry_unchanged():
    lines = markdown.entry_lines(entry(1))
    assert not any(line.startswith("Findings") for line in lines)
    assert markdown.entry_lines(entry(1, findings=None)) == lines


def test_markdown_finding_is_plain():
    out = "\n".join(markdown.findings_lines([finding(1, text=EVIL, quote=EVIL)], "done"))
    assert "<b>" not in out and "&lt;b\\>x&lt;/b\\>" in out
    assert "\\[link\\](http" in out and "[link]" not in out.replace("\\[link\\]", "")
    assert "[[" not in out.replace("\\[\\[", "")
    assert "&amp;" not in re.sub(r"\\&amp;", "", out)
    assert " | " not in out.replace("\\|", "") and "#tag" not in out.replace("\\#tag", "")


def test_markdown_finding_leading_markers_are_inert():
    for lead in ("> quote", "- item", "+ item", "# head"):
        line = markdown.findings_lines([finding(1, text=lead, quote=lead)], "done")[1]
        assert line.startswith("- \\"), line
        assert f"- {lead}" not in line


def test_markdown_finding_leading_number_is_not_a_list():
    for lead, want in (("1. Clicks rose", "1\\. Clicks rose"), ("12) CTR fell", "12\\) CTR fell")):
        assert markdown.inert(lead) == want
