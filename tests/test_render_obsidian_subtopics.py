# tests/test_render_obsidian_subtopics.py
"""Questions: Obsidian subtopic notes, study frontmatter and links, the index's subtopic list."""
import dataclasses

from evidence_echo.core import knowledge
from evidence_echo.render import obsidian
from tests.obsidian_fixtures import study, topic
from tests.test_render_html_subtopics import other, question, sub
from tests.test_render_obsidian import _frontmatter

SUBTOPIC = {"key": "title-tags-meta", "name": "Title tags & meta", "question": "Does X raise Y?",
            "label": "contested", "independent": 2,
            "echo": "corroborated by 1 other independent study"}


def q(**over):
    return question(**{"text": "Does X raise Y?", **over})


def _topic(evidence=None, subtopic=SUBTOPIC):
    t = topic([study(1, subtopic=subtopic), study(2, subtopic=subtopic)])
    return dataclasses.replace(t, evidence=[sub([q()])] if evidence is None else evidence)


def _link(t, sid, alias):
    return f"[[Evidence Echo/SEO/studies/{obsidian._study_stems(t)[sid]}|{alias}]]"


def _note(t, name="Title tags & meta"):
    return obsidian.render_notes(t, "SEO")[f"SEO/subtopics/{name}.md"]


def test_subtopic_note_lists_questions_then_others():
    t = _topic([sub([q()], others=[other(3, "Lone question?"), other(4)])])
    note = _note(t)
    assert "# Title tags & meta" in note and "## Does X raise Y? · ⇅ contested" in note
    assert "2 independent studies · 3 articles · best A  " in note
    assert note.index("## Does X raise Y?") < note.index("## Other studies")
    lines = note.splitlines()
    assert "- Study 3 · Lone question?" in lines and "- Study 4 · not grouped yet" in lines


def test_question_without_label_has_a_plain_heading():
    note = _note(_topic([sub([q(label=None, independent=1)])]))
    assert "## Does X raise Y?" in note.splitlines() and "None" not in note


def test_study_note_frontmatter_and_subtopic_link():
    t = _topic()
    notes = obsidian.render_notes(t, "SEO")
    path = f"SEO/studies/{obsidian._study_stems(t)[1]}.md"
    fm = _frontmatter(notes[path])
    assert fm["subtopic"] == "Title tags & meta" and "metric" not in fm
    lines = notes[path].splitlines()
    assert "Subtopic: [[Evidence Echo/SEO/subtopics/Title tags & meta|Title tags & meta]]  " in lines
    assert ("Question: Does X raise Y? · ⇅ contested (2 independent studies) · "
            "corroborated by 1 other independent study  ") in lines


def test_study_note_subtopic_line_with_question():
    bare = _topic(subtopic=dict(SUBTOPIC, label=None, echo=None, independent=0, question=None))
    path = f"SEO/studies/{obsidian._study_stems(bare)[1]}.md"
    assert "Subtopic: [[Evidence Echo/SEO/subtopics/Title tags & meta|Title tags & meta]]  " in         obsidian.render_notes(bare, "SEO")[path].splitlines()
    asked = _topic(subtopic=dict(SUBTOPIC, label=None, echo=None, independent=1))
    lines = obsidian.render_notes(asked, "SEO")[path].splitlines()
    assert "Subtopic: [[Evidence Echo/SEO/subtopics/Title tags & meta|Title tags & meta]]  " in lines
    assert "Question: Does X raise Y?  " in lines


def test_index_lists_the_subtopics_and_unsorted_gets_its_note():
    unsorted = sub(key=knowledge.UNSORTED, subtopic_id=None, name="Unsorted", others=[other(3)])
    notes = obsidian.render_notes(_topic([sub([q()]), unsorted]), "SEO")
    index = notes["SEO/_index.md"]
    assert "## Subtopics" in index
    assert index.index("[[Evidence Echo/SEO/subtopics/Title tags & meta|Title tags & meta]]") < \
           index.index("[[Evidence Echo/SEO/subtopics/Unsorted|Unsorted]]")
    assert "SEO/subtopics/Unsorted.md" in notes


def test_study_without_evidence_keeps_its_old_note():
    t = dataclasses.replace(topic([study(1)]), evidence=[])
    notes = obsidian.render_notes(t, "SEO")
    assert not any(p.startswith("SEO/subtopics/") for p in notes)
    assert "subtopic" not in _frontmatter(notes[f"SEO/studies/{obsidian._study_stems(t)[1]}.md"])
    assert "## Subtopics" not in notes["SEO/_index.md"]


def test_untrusted_subtopic_names_are_neutralised():
    evil = "Evil [[x]] #tag `c` <b> a/b"
    t = _topic([sub([q()], name=evil)], subtopic=dict(SUBTOPIC, name=evil))
    notes = obsidian.render_notes(t, "SEO")
    [path] = [p for p in notes if p.startswith("SEO/subtopics/")]
    assert "/" not in path[len("SEO/subtopics/"):]
    body = notes[path]
    assert "[[x]]" not in body and "<b>" not in body and "`c`" not in body and "\\#tag" in body
    study_note = notes[f"SEO/studies/{obsidian._study_stems(t)[1]}.md"]
    assert "[[x]]" not in study_note.split("---", 2)[2]


def test_question_text_is_neutralised_in_obsidian():
    evil = "Is [[x]] <b>bold</b> `c` [a](http://evil.example) real?"
    t = _topic([sub([q(text=evil)], others=[other(3, evil)])], subtopic=dict(SUBTOPIC, question=evil))
    notes = obsidian.render_notes(t, "SEO")
    for body in (_note(t), notes[f"SEO/studies/{obsidian._study_stems(t)[1]}.md"].split("---", 2)[2]):
        assert "[[x]]" not in body and "<b>" not in body and "`c`" not in body
        assert "[a](http://evil.example)" not in body


def test_export_removes_a_subtopic_note_that_is_gone(tmp_path):
    obsidian.export(_topic(), tmp_path)
    note = tmp_path / "Evidence Echo" / "SEO" / "subtopics" / "Title tags & meta.md"
    assert note.exists()
    obsidian.export(_topic(evidence=[]), tmp_path)
    assert not note.exists()


def test_unsorted_subtopic_note_lists_studies_without_questions():
    unsorted = sub(key=knowledge.UNSORTED, subtopic_id=None, name="Unsorted", others=[other(1), other(2)])
    t = _topic([unsorted])
    note = obsidian.render_notes(t, "SEO")["SEO/subtopics/Unsorted.md"]
    assert "## Studies" in note.splitlines() and "None" not in note and "not grouped yet" not in note
    assert f"- {_link(t, 1, 'Study 1: +4.2% clicks')}" in note.splitlines()


def test_obsidian_upgrade_moves_subtopics_folder(tmp_path):
    """A 0.8.0 vault: the manifest lists themes/ notes; the first new export removes them, keeps user files."""
    import json
    root = tmp_path / "Evidence Echo"
    old = root / "SEO" / "themes" / "backlinks.md"
    mine = root / "SEO" / "themes" / "mine.md"
    old.parent.mkdir(parents=True)
    old.write_text("old", encoding="utf-8")
    mine.write_text("mine", encoding="utf-8")
    t = _topic()
    (root / obsidian.MANIFEST).write_text(json.dumps(
        {"version": obsidian.MANIFEST_VERSION, "files": {t.topic_id: ["SEO/themes/backlinks.md"]}}),
        encoding="utf-8")
    obsidian.export(t, tmp_path)
    assert not old.exists()
    assert mine.exists()
    assert (root / "SEO" / "subtopics" / "Title tags & meta.md").exists()


# --- the new texts (evidence-ui) -------------------------------------------------------------

def test_question_heading_has_symbol():
    for label, symbol in (("consistent", "✓"), ("contested", "⇅"), ("varied", "≈")):
        note = _note(_topic([sub([q(label=label)])]))
        assert f"## Does X raise Y? · {symbol} {label}" in note.splitlines()


def test_study_note_splits_research_and_published():
    t = _topic()
    path = f"SEO/studies/{obsidian._study_stems(t)[1]}.md"
    lines = obsidian.render_notes(t, "SEO")[path].splitlines()
    assert any(line.startswith("*Research: ") and "|" not in line for line in lines)
    assert any(line.startswith("*Published: ") for line in lines)


def test_study_note_question_line():
    t = _topic()
    path = f"SEO/studies/{obsidian._study_stems(t)[1]}.md"
    assert ("Question: Does X raise Y? · ⇅ contested (2 independent studies) · "
            "corroborated by 1 other independent study  ") in obsidian.render_notes(t, "SEO")[path].splitlines()


def test_no_arrows_in_obsidian_rows():
    rows = [other(1, direction="up").row, other(2, direction="down").row]
    t = _topic([sub([q(rows=rows)])])
    note = obsidian.render_notes(t, "SEO")["SEO/subtopics/Title tags & meta.md"]
    assert not any(ch in note for ch in "\u2191\u2193\u2195")
    line = next(l for l in note.splitlines() if l.startswith("- 2025-03"))
    assert line.count(" · ") == 3                            # date · name · headline · tier


# --- one explanation callout per question ---------------------------------------------------------

def _explained(lines, summary="Shops gain, news sites do not.", **over):
    return q(explanation={"summary": summary, "main_result": "up", "lines": lines}, **over)


def _line(study=2, reason="different_population", quote="news sites fell", note="News sites."):
    return {"study": study, "reason": reason, "quote": quote, "note": note}


def _callout(note):
    lines = note.splitlines()
    start = lines.index("> [!note]- Why the results differ")
    block = []
    for line in lines[start:]:
        if not line.startswith(">"):
            break
        block.append(line)
    return block


def test_question_callout_one_per_question():
    t = _topic([sub([_explained([_line()])])])
    note = _note(t)
    assert note.count("> [!note]- Why the results differ") == 1
    block = _callout(note)
    assert block[1:5] == ["> Shops gain, news sites do not.", ">", "> Main result: up", ">"]
    assert block[5] == f"> - {_link(t, 2, 'Study 2: +4.2% clicks')} (down) — different population"
    assert block[6:] == [">   News sites.", ">   “news sites fell”"]


def test_question_callout_without_summary_or_note():
    block = _callout(_note(_topic([sub([_explained([_line(note="")], summary=None)])])))
    assert block[1] == "> Main result: up" and ">   “news sites fell”" in block
    assert not any(line.strip() == ">   " for line in block)


def test_no_callout_without_an_explanation():
    assert "Why the results differ" not in _note(_topic())


def test_question_callout_is_neutralised():
    line = _line(quote="> [[evil]]\n# head", note="first line\n> [[x]] second")
    note = _note(_topic([sub([_explained([line], summary="sum\n> [[s]] `c`")])]))
    block = _callout(note)
    assert all(l.startswith(">") for l in block)
    assert "[[x]]" not in note and "[[evil]]" not in note and "[[s]]" not in note
    assert not any(l.startswith("> > ") or l.startswith("> #") for l in block)


def test_callout_lines_stay_inside_the_quote_block():
    note = _note(_topic([sub([_explained([_line(), _line(study=1, note="a\n\nb", quote="x\ny")])])]))
    lines = note.splitlines()
    start = lines.index("> [!note]- Why the results differ")
    end = next((i for i in range(start, len(lines)) if not lines[i].startswith(">")), len(lines))
    assert all(l.strip() != "" for l in lines[start:end]) and "b" in "".join(lines[start:end])
