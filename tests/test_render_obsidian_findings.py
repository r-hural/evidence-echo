"""Quotes-mode Obsidian notes: findings per subtopic and per study; frontmatter unchanged."""
import dataclasses

from evidence_echo.render import obsidian
from tests.html_fixtures import finding, finding_row, findings_subtopic
from tests.obsidian_fixtures import study, topic
from tests.test_render_obsidian import _frontmatter

EVIL = "[[x]] #tag | a <b>x</b> $m$ %%c%% :: end"
SUB = {"key": "local", "name": "Local", "question": None, "label": None, "independent": 0, "echo": None}


def quotes(studies, rows):
    t = topic(studies)
    return dataclasses.replace(t, mode="quotes", findings=[findings_subtopic(rows)],
                               finding_count=sum(1 for r in rows if r.finding))


def _link(t, sid, alias):
    return f"[[Evidence Echo/SEO/studies/{obsidian._study_stems(t)[sid]}|{alias}]]"


def _study_path(t, sid):
    return f"SEO/studies/{obsidian._study_stems(t)[sid]}.md"


def test_obsidian_quotes_subtopic_note_lists_findings():
    s1, s2, s3 = (study(n, subtopic=SUB, findings=[], findings_state="done") for n in (1, 2, 3))
    rows = [finding_row(1), finding_row(2, finding=finding(2, quote_checked=False, metric_name=None)),
            finding_row(3, finding=None, state="waiting"), finding_row(4, finding=None, state="failed", name="Gone")]
    t = quotes([s1, s2, s3], rows)
    lines = obsidian.render_notes(t, "SEO")["SEO/subtopics/Local.md"].splitlines()
    assert "# Local" in lines
    assert f"- {_link(t, 1, 'Study 1: +4.2% clicks')} · Clicks rose 1% · Clicks" in lines
    assert "  > “Clicks rose 1% on forty pages.”" in lines
    assert f"- {_link(t, 2, 'Study 2: +4.2% clicks')} · Clicks rose 2%" in lines
    assert "  > “Clicks rose 2% on forty pages.” # quote not found in the article" in lines
    assert f"- {_link(t, 3, 'Study 3: +4.2% clicks')} · findings waiting" in lines
    assert "- Gone" in lines                       # failed, and no note for that study: plain text


def test_obsidian_study_note_findings_section():
    s1 = study(1, subtopic=SUB, findings=[finding(1), finding(2, quote_checked=False)], findings_state="done")
    t = quotes([s1], [finding_row(1)])
    lines = obsidian.render_notes(t, "SEO")[_study_path(t, 1)].splitlines()
    assert "## Findings" in lines
    assert "- Clicks rose 1%: “Clicks rose 1% on forty pages.”" in lines
    assert "- Clicks rose 2%: “Clicks rose 2% on forty pages.” # quote not found in the article" in lines
    t2 = quotes([study(2, findings=[], findings_state="waiting")], [])
    assert "Findings: waiting" in obsidian.render_notes(t2, "SEO")[_study_path(t2, 2)]
    t3 = quotes([study(3, findings=[], findings_state="failed")], [])
    assert "Findings" not in obsidian.render_notes(t3, "SEO")[_study_path(t3, 3)]


def test_obsidian_frontmatter_unchanged_in_quotes_mode():
    a = obsidian.render_notes(topic([study(1, subtopic=SUB)]), "SEO")
    t = quotes([study(1, subtopic=SUB, findings=[finding(1)], findings_state="done")], [finding_row(1)])
    b = obsidian.render_notes(t, "SEO")
    key = _study_path(t, 1)
    assert _frontmatter(a[key]) == _frontmatter(b[key])


def test_obsidian_index_lists_findings_subtopics_in_quotes_mode():
    t = quotes([study(1, subtopic=SUB, findings=[finding(1)], findings_state="done")], [finding_row(1)])
    notes = obsidian.render_notes(t, "SEO")
    assert "[[Evidence Echo/SEO/subtopics/Local|Local]]" in notes["SEO/_index.md"]
    assert "SEO/subtopics/Local.md" in notes


def test_obsidian_finding_is_neutralised():
    s1 = study(1, subtopic=SUB, findings=[finding(1, text=EVIL, quote=EVIL)], findings_state="done")
    t = quotes([s1], [finding_row(1, finding=finding(1, text=EVIL, quote=EVIL))])
    notes = obsidian.render_notes(t, "SEO")
    for path in ("SEO/subtopics/Local.md", _study_path(t, 1)):
        body = notes[path].split("\n---\n", 1)[-1]
        evil_lines = [line for line in body.splitlines() if "end" in line and "a" in line and "m" in line]
        assert evil_lines, path
        for line in evil_lines:
            assert "[[x]]" not in line and "<b>" not in line and "%%c%%" not in line, line
            assert "\\[\\[x\\]\\]" in line and "\\#tag" in line and "&lt;b>" in line and "\\$m\\$" in line, line
    for lead in ("> q", "- q", "| q", "# q", "1. q"):
        sub = dataclasses.replace(t, findings=[findings_subtopic([
            finding_row(1, finding=finding(1, text=lead, quote=lead))])])
        note = obsidian.render_notes(sub, "SEO")["SEO/subtopics/Local.md"]
        row = [line for line in note.splitlines() if line.startswith("- [[")][0]
        assert f" · {lead}" not in row
        quote_line = [line for line in note.splitlines() if line.startswith("  > ")][0]
        assert quote_line.startswith("  > “") and f"“{lead}" not in quote_line, quote_line
