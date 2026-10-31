"""Findings-mode Obsidian notes: question rows show findings, a waiting section, study notes list questions."""
import dataclasses

from evidence_echo.core import knowledge
from evidence_echo.render import obsidian
from tests.obsidian_fixtures import study, topic
from tests.test_render_html_subtopics import other, question, sub

EVIL = "[[x]] #tag | a <b>x</b> $m$ %%c%% :: end\n# heading"


def finding(i, text=None, quote=None, checked=True):
    return {"id": i, "position": 1, "text": text or f"Clicks rose {i}%",
            "quote": quote or f"Clicks rose {i}% on forty pages.", "quote_checked": checked}


def row(sid, **f):
    return knowledge.StudyRow(study_id=sid, name=f"Study {sid}", date="2025-03", reported=False, headline="HEADLINE",
                              headline_label="clicks", direction=None, tier="B", series=None, edition=None,
                              finding=finding(sid, **f))


def qgroup(*sids, **over):
    return question(text="Does X raise Y?", groups=[[s] for s in sids], directions={s: "up" for s in sids},
                    rows=[row(s) for s in sids], findings={s: finding(s) for s in sids}, independent=len(sids),
                    label="consistent", **over)


def _topic(evidence, studies=None):
    return dataclasses.replace(topic(studies or [study(1), study(2)]), mode="findings", evidence=evidence)


def _note(t, name="Title tags & meta"):
    return obsidian.render_notes(t, "SEO")[f"SEO/subtopics/{name}.md"]


def _link(t, sid, alias):
    return f"[[Evidence Echo/SEO/studies/{obsidian._study_stems(t)[sid]}|{alias}]]"


def test_obsidian_question_rows_show_findings():
    t = _topic([sub([qgroup(1, 2)])])
    lines = _note(t).splitlines()
    assert f"- 2025-03 · {_link(t, 1, 'Study 1: +4.2% clicks')} · Clicks rose 1% · B" in lines
    at = lines.index(next(x for x in lines if "Clicks rose 1% · B" in x))
    assert lines[at + 1] == "  > “Clicks rose 1% on forty pages.”"
    assert "HEADLINE" not in "\n".join(lines)
    unchecked = dataclasses.replace(qgroup(1), rows=[dataclasses.replace(row(1), finding=finding(1, checked=False))])
    assert "  > “Clicks rose 1% on forty pages.” # quote not found in the article" in _note(_topic([sub([unchecked])]))


def test_obsidian_waiting_section():
    t = _topic([sub([qgroup(1)], waiting=[dataclasses.replace(row(2), finding=None)])])
    note = _note(t)
    assert note.rstrip().endswith(f"- {_link(t, 2, 'Study 2: +4.2% clicks')}")
    assert "\n## Waiting to be split\n" in note
    assert "Waiting to be split" not in _note(_topic([sub([qgroup(1)])]))


def test_obsidian_study_note_questions():
    qs = [{"question_id": 1, "question": "Does X raise Y?", "label": "consistent", "independent": 2, "echo": None,
           "finding": "Clicks rose 1%", "quote_checked": True},
          {"question_id": 2, "question": "Does Z hurt?", "label": None, "independent": 1, "echo": None,
           "finding": "Rankings fell", "quote_checked": False}]
    subtopic = {"key": "title-tags-meta", "name": "Title tags & meta", "question": None, "label": None,
                "independent": 0, "echo": None, "questions": qs, "waiting": True}
    t = _topic([sub([qgroup(1, 2)])], [study(1, subtopic=subtopic), study(2)])
    note = obsidian.render_notes(t, "SEO")[f"SEO/studies/{obsidian._study_stems(t)[1]}.md"]
    lines = note.splitlines()
    assert any(x.startswith("Subtopic: [[Evidence Echo/SEO/subtopics/Title tags & meta|") for x in lines)
    assert "Question: Does X raise Y? · ✓ consistent (2 independent studies) — Clicks rose 1%  " in lines
    assert ("Question: Does Z hurt? — Rankings fell # quote not found in the article  " in lines)
    assert "Findings: waiting to be split  " in lines


def test_obsidian_findings_text_neutralised():
    g = dataclasses.replace(qgroup(1), rows=[dataclasses.replace(row(1), finding=finding(1, text=EVIL, quote=EVIL))])
    t = _topic([sub([g], others=[])])
    body = _note(t).split("Studies, newest first:")[1]
    bs = chr(92)
    assert f"{bs}[{bs}[x{bs}]{bs}]" in body and "&lt;b>" in body and f"{bs}#tag" in body
    assert "<b>" not in body and "%%c%%" not in body and "$m$" not in body
    assert "\n# heading" not in body and f"\n{bs}# heading" not in body   # one line: no heading can form


def test_obsidian_findings_mode_study_note_has_no_quotes_mode_findings():
    from tests.html_fixtures import finding as finding_view
    subtopic = {"key": "title-tags-meta", "name": "Title tags & meta", "question": None, "label": None,
                "independent": 0, "echo": None, "questions": [], "waiting": True}
    t = _topic([sub([qgroup(1, 2)])], [study(1, subtopic=subtopic, findings=[], findings_state="waiting"),
                                        study(2, subtopic={**subtopic, "waiting": False},
                                              findings=[finding_view(1)], findings_state="done")])
    notes = obsidian.render_notes(t, "SEO")
    one = notes[f"SEO/studies/{obsidian._study_stems(t)[1]}.md"].splitlines()
    two = notes[f"SEO/studies/{obsidian._study_stems(t)[2]}.md"]
    assert [x for x in one if x.startswith("Findings")] == ["Findings: waiting to be split  "]
    assert "## Findings" not in "\n".join(one) and "## Findings" not in two
