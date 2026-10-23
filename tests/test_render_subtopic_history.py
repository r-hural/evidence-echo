"""Wave 4a Part 1 display: HTML <details> list per evidence row, Obsidian subtopic-note lists."""
import dataclasses
import re

from evidence_echo.core import knowledge
from evidence_echo.render import html as html_render
from evidence_echo.render import html_assets, obsidian
from tests.html_fixtures import entry, topic_model, topic_study
from tests.obsidian_fixtures import study, topic as obs_topic
from tests.test_render_html_subtopics import question, sub

EVIL = '<b>x</b> [[Evil]] `c` $m$ #tag %%'
SERIES = "Seer Interactive CTR study"


def row(sid, **over):
    base = dict(study_id=sid, name=f"Study {sid}", date="2024-05", reported=False, headline="−15 %",
                headline_label="organic CTR", direction="down", tier="B", series=SERIES, edition=sid)
    base.update(over)
    return knowledge.StudyRow(**base)


def rows():
    return [row(1), row(2, date="2025-03", headline="−34 %"),
            row(3, series=None, edition=None, tier="A", reported=True, headline=None)]


ROWS = rows()
CHANGE = knowledge.ChangeLine(SERIES, [("−15 %", "2024"), ("−34 %", "2025")])


def page(evidence, waiting=0, studies=None):
    studies = studies or [topic_study(entry(1)), topic_study(entry(2))]
    return html_render.topic_page(topic_model(studies, evidence=evidence, waiting=waiting), "o.html", "r.html")


def test_details_list_closed_by_default_with_links_and_tier():
    text = page([sub([question(rows=ROWS, changes=[CHANGE])])])
    block = text[text.index('<details class="ee-studies">'):text.index("</details>")]
    assert "<details " in text and " open" not in block
    assert "<summary><strong>" in block and "Studies, newest first" not in block   # the question opens it
    assert block.index("2024-05") < block.index("2025-03") < block.index("reported 2024-05")
    assert '<a class="ee-row" href="#s1">' in block and '<a class="ee-row" href="#s2">' in block
    assert "#s3" not in block and "Study 3" in block                       # no card: plain name
    assert '<span class="ee-row">' in block                                 # ... and no link
    assert "↓" not in block and 'class="sr">down' not in block              # no direction arrow
    assert '<span class="ee-date">[reported 2024-05]</span>' in block       # the date last, bracketed
    assert "no checked number" in block and "(edition 2 of series)" in block
    assert '<p class="meta ee-change">Seer Interactive CTR study: −15 % (2024) → −34 % (2025)</p>' in block


def test_row_text_is_escaped():
    text = page([sub([question(rows=[row(9, name=EVIL, headline=EVIL, headline_label=EVIL)],
                               changes=[knowledge.ChangeLine(EVIL, [(EVIL, "2024"), (EVIL, "2025")])])])])
    assert "<b>x</b>" not in text and "&lt;b&gt;x&lt;/b&gt;" in text


def test_no_rows_no_details():
    assert '<details class="ee-studies"' not in page([sub([question()])])   # the CSS rules are always on the page


def test_waiting_note():
    assert "3 studies wait to be grouped" in page([sub([question()])], waiting=3)
    assert "1 study waits to be grouped" in page([], waiting=1)        # section with the note only
    assert "ee-waiting" not in page([sub([question()])], waiting=0)
    assert 'id="ee-evidence"' not in page([], waiting=0)


def test_list_css_fits_a_phone():
    rules = [r for r in html_assets.CSS.split("}") if ".ee-studies" in r]
    assert rules and not any("nowrap" in r or re.search(r"(?<![-\w])width:\s*\d", r) for r in rules)
    assert "overflow-wrap:anywhere" in html_assets.CSS


def _obs(evidence):
    t = obs_topic([study(1), study(2)])
    return obsidian.render_notes(dataclasses.replace(t, evidence=evidence), "SEO")


def test_subtopic_note_lists_rows_with_wikilinks_and_change_lines():
    note = _obs([sub([question(rows=ROWS, changes=[CHANGE])])])["SEO/subtopics/Title tags & meta.md"]
    stem = obsidian._study_stems(obs_topic([study(1), study(2)]))
    assert "Studies, newest first:" in note
    line = next(l for l in note.splitlines() if l.startswith("- 2025-03"))
    assert f"[[Evidence Echo/SEO/studies/{stem[2]}|" in line
    assert "−34 % organic CTR · B (edition 2 of series)" in line
    assert "- reported 2024-05 · Study 3 · no checked number · A" in note
    assert "Seer Interactive CTR study: −15 % (2024) → −34 % (2025)  " in note
    assert " · up" not in note.replace("· up ·", "")                     # no bare direction list


def test_subtopic_note_neutralises_row_text():
    note = _obs([sub([question(rows=[row(9, name=EVIL, headline=EVIL, headline_label=EVIL)])])])[
        "SEO/subtopics/Title tags & meta.md"]
    assert "[[Evil]]" not in note and "<b>" not in note and "`c`" not in note and "%%" not in note


def test_waiting_text():
    from evidence_echo.render import evidence_text
    assert evidence_text.waiting_text(0) is None
    assert evidence_text.waiting_text(1) == "1 study waits to be grouped"
    assert evidence_text.waiting_text(2) == "2 studies wait to be grouped"
