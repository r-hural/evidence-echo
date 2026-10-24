"""Questions: the topic page's evidence section and subtopic filter, and the card's subtopic line."""
import json

from evidence_echo.core import knowledge
from evidence_echo.render import html as html_render
from evidence_echo.render import html_assets
from tests.html_fixtures import Refs, entry, report, topic_model, topic_study

EVIL = '<script>alert("x")</script>&\''
QUESTION = "Does updating titles raise clicks?"
SUBTOPIC = {"key": "title-tags-meta", "name": "Title tags & meta", "question": QUESTION,
            "label": "contested", "independent": 2, "echo": None}
DATA_TAG = '<script type="application/json" id="ee-data">'


def question(**over):
    base = dict(kind="difference", question_id=1, text=QUESTION, measure="clicks", subtopic_key="title-tags-meta",
                label="contested", independent=2, articles=3, best_tier="A", groups=[[1], [2]],
                directions={1: "up", 2: "down"}, echoes={})
    base.update(over)
    return knowledge.QuestionGroup(**base)


def sub(questions=(), others=(), **over):
    base = dict(key="title-tags-meta", subtopic_id=1, name="Title tags & meta",
                questions=list(questions), others=list(others))
    base.update(over)
    return knowledge.SubtopicEvidence(**base)


def other(sid, question=None, **over):
    base = dict(study_id=sid, name=f"Study {sid}", date="2025-03", reported=False, headline="12 %",
                headline_label="clicks", direction=None, tier="B", series=None, edition=None)
    base.update(over)
    return knowledge.OtherStudy(knowledge.StudyRow(**base), question)


def page(evidence, studies=None, waiting=0):
    studies = studies or [topic_study(entry(1, subtopic=SUBTOPIC)), topic_study(entry(2, subtopic=SUBTOPIC))]
    return html_render.topic_page(topic_model(studies, evidence=evidence, waiting=waiting),
                                  "overview.html", "r.html")


def _data(text):
    start = text.index(DATA_TAG) + len(DATA_TAG)
    return json.loads(text[start:text.index("</script>", start)])


def test_section_sits_above_the_cards_with_label_and_counts():
    text = page([sub([question()])])
    assert text.index("What the evidence says") < text.index('id="ee-studies"')
    assert '<h3>Title tags &amp; meta</h3>' in text
    assert (f'<strong>{QUESTION}</strong> <span class="meta">2 independent studies · 3 articles · '
            '<span class="ee-label ee-label-contested"><span aria-hidden="true">⇅</span> contested</span></span>'
            in text)
    assert "best A" not in text                      # the page leaves the best tier out


def test_untrusted_subtopic_names_and_notes_are_escaped():
    text = page([sub([question(text=EVIL)], name=EVIL)],
                [topic_study(entry(1, subtopic=dict(SUBTOPIC, name=EVIL, question=EVIL)))])
    assert '<script>alert' not in text and "&lt;script&gt;alert" in text


def test_subtopic_filter_lists_the_page_subtopics_in_evidence_order():
    text = page([sub([question()]), sub(key=knowledge.UNSORTED, subtopic_id=None, name="Unsorted",
                                        others=[other(3)])],
                [topic_study(entry(1, subtopic=SUBTOPIC)), topic_study(entry(3))])
    assert '<select name="subtopic"><option value="">Any subtopic</option>' \
           '<option value="title-tags-meta">Title tags &amp; meta</option>' \
           f'<option value="{knowledge.UNSORTED}">Unsorted</option></select>' in text
    rows = _data(text)["studies"]
    assert [r["subtopic"] for r in rows] == ["title-tags-meta", knowledge.UNSORTED]
    assert "f.subtopic" in html_assets.JS and "value('subtopic')" in html_assets.JS


def test_no_evidence_no_section_and_no_subtopic_filter():
    text = page([], [topic_study(entry(1))])
    assert "What the evidence says" not in text and 'name="subtopic"' not in text
    assert "subtopic" not in _data(text)["studies"][0]


def test_card_subtopic_line_with_echo_on_both_pages():
    e = entry(1, subtopic=dict(SUBTOPIC, echo="corroborated by 1 other independent study"))
    line = (f"<b>Question:</b> {QUESTION} · ⇅ contested (2 independent studies) · "
            "corroborated by 1 other independent study")
    assert line in page([sub([question()])], [topic_study(e)])
    assert line in html_render.report_page(report(new=[e]), "seo.html", "overview.html")


LONG = "word " * 100


def test_unsorted_lists_its_studies_without_questions_or_badges():
    unsorted = sub(key=knowledge.UNSORTED, subtopic_id=None, name="Unsorted", others=[other(1), other(2)])
    text = page([unsorted])
    section = text.split('id="ee-evidence"', 1)[1].split("</section>", 1)[0]
    assert '<span class="meta">2 studies</span></summary>' in section and 'class="badge"' not in section
    assert "not grouped yet" not in section and "ee-single-q" not in section
    assert section.count('<li class="ee-question ee-single"><ol class="ee-single-row">') == 2


def test_unsorted_studies_still_match_the_unsorted_filter():
    unsorted = sub(key=knowledge.UNSORTED, subtopic_id=None, name="Unsorted", others=[other(1), other(2)])
    text = page([unsorted], [topic_study(entry(1)), topic_study(entry(2))])   # no subtopic line
    assert [r.get("subtopic") for r in _data(text)["studies"]] == [knowledge.UNSORTED] * 2


# --- evidence labels, legend and closed explanations (evidence-ui) -------------------------

def test_label_markup_has_symbol_and_bar():
    text = page([sub([question()])])
    assert 'class="ee-question ee-multi ee-q-contested"' in text
    assert '<span aria-hidden="true">⇅</span> contested' in text
    assert f"<strong>{QUESTION}</strong>" in text
    assert 'ee-q-none' in page([sub([question(label=None)])])


def test_legend_once_only_with_labels():
    legend = ("✓ consistent: all independent studies point the same way · "
              "⇅ contested: some say up, some say down · ≈ varied: any other mix")
    two = page([sub([question(), question(question_id=2, text="Other?", label="varied")])])
    assert two.count(f'<p class="meta ee-legend">{legend}</p>') == 1
    assert 'class="meta ee-legend"' not in page([sub([question(label=None)])])


def test_the_question_opens_its_study_list():
    rows = [other(1).row, other(2).row]
    text = page([sub([question(rows=rows)])])
    assert (f'<details class="ee-studies"><summary><strong>{QUESTION}</strong> '
            '<span class="meta">2 independent studies · 3 articles · <span class="ee-label ee-label-contested">'
            '<span aria-hidden="true">⇅</span> contested</span></span></summary><ol>') in text
    assert "Studies, newest first" not in text
    assert f"<summary><strong>{QUESTION}" not in page([sub([question()])])   # no rows: nothing to open


def test_subtopic_index_draws_extent_rules():
    two = page([sub([question()]), sub([], [other(3)], key="links", subtopic_id=2, name="Links")])
    index = two.split('<nav class="ee-index"', 1)[1].split("</nav>", 1)[0]
    assert '<a href="#sub-title-tags-meta">Title tags &amp; meta</a><span class="ee-n">2</span>' in index
    assert "--w:100%" in index and "--w:50%" in index
    assert 'id="sub-links"' in two
    assert 'class="ee-index"' not in page([sub([question()])])   # one subtopic: no index


def test_label_after_counts_in_summary():
    rows = [other(1).row, other(2).row]
    text = page([sub([question(rows=rows, label="varied")])])
    head = text.split("<summary><strong>", 1)[1].split("</summary>", 1)[0]
    assert head.index("2 independent studies") < head.index("ee-label") < head.index("varied")
    assert '<span aria-hidden="true">≈</span> varied' in head


def test_no_label_column_css():
    css = html_assets.CSS
    assert "8.5rem" in css                       # the card fact column keeps its width
    assert ".ee-question{position" not in css and ".ee-question>.ee-label" not in css
    assert "padding:.6rem 0 .6rem 8.5rem" not in css


def test_comparison_line_for_difference_only():
    text = page([sub([question(comparison="pages with an updated date vs without"),
                      question(question_id=2, text="How much?", kind="level", label=None, comparison=None),
                      question(question_id=3, text="Can it?", kind="capability", label=None, comparison="x")])])
    assert text.count('class="ee-cmp"') == 1
    assert '<span class="ee-cmp">Compared: pages with an updated date vs without</span>' in text


def test_comparison_is_escaped_everywhere():
    evil = '<script>alert("x")</script> [[x]] > q'
    text = page([sub([question(comparison=evil, rows=[other(1).row])])])
    assert "<script>alert" not in text and "&lt;script&gt;" in text
    from evidence_echo.render import obsidian
    from tests.obsidian_fixtures import study as ostudy, topic as otopic
    import dataclasses
    t = dataclasses.replace(otopic([ostudy(1), ostudy(2)]), evidence=[sub([question(comparison=evil)])])
    note = obsidian.render_notes(t, "SEO")["SEO/subtopics/Title tags & meta.md"]
    assert "[[x]]" not in note and "<script>" not in note
    assert not any(line.startswith(">") for line in note.splitlines() if "Compared" in line)
    assert "Compared:" in note


def test_label_hidden_for_level_question():
    text = page([sub([question(kind="level", label=None, rows=[other(1).row])])])
    assert "ee-label" not in text.split("<style", 1)[0] + text.split("</style>", 1)[-1]


# --- one explanation block per question ---------------------------------------------------------

def _explained(lines, summary="Shops gain, news sites do not.", **over):
    return question(explanation={"summary": summary, "main_result": "up", "lines": lines}, **over)


def _line(study=2, reason="different_population", quote="news sites fell", note="News sites."):
    return {"study": study, "reason": reason, "quote": quote, "note": note}


def test_one_block_per_question_with_lines():
    other_line = _line(study=9, reason="method_quality", quote="no control group", note="Weak design.")
    text = page([sub([_explained([_line(), other_line], groups=[[1], [2], [9]],
                                 directions={1: "up", 2: "down", 9: "no_change"})])])
    assert text.count('<details class="ee-why">') == 1 and "<details class=\"ee-why\" open" not in text
    block = text[text.index('<details class="ee-why">'):]
    block = block[:block.index("</details>")]
    assert "<summary>Why the results differ</summary><p>Shops gain, news sites do not.</p>" in block
    assert '<p class="meta">Main result: up</p>' in block and block.count("<li>") == 2
    assert "(down) — different population<p>News sites.</p><blockquote>“news sites fell”</blockquote>" in block
    assert "(no change) — method quality<p>Weak design.</p>" in block


def test_summary_absent_when_none():
    text = page([sub([_explained([_line()], summary=None)])])
    assert "<summary>Why the results differ</summary><p class=\"meta\">Main result: up</p>" in text


def test_line_links_to_the_card():
    text = page([sub([_explained([_line(study=2), _line(study=9)], groups=[[1], [2], [9]],
                                 directions={1: "up", 2: "down", 9: "down"})])])
    assert '<a href="#s2" title="Study 2: title rewrites, +4.2% clicks">' in text
    assert "#s9" not in text                      # study 9 is not on the page: plain text, no link


def test_question_explanation_is_escaped():
    line = _line(study=2, quote=EVIL, note=EVIL)
    studies = [topic_study(entry(1, subtopic=SUBTOPIC)),
               topic_study(entry(2, title='<script>"x"</script>', subtopic=SUBTOPIC))]
    text = html_render.evidence_section(topic_model(studies, evidence=[sub([_explained([line], summary=EVIL)])]))
    assert "<script" not in text
    assert 'title="&lt;script&gt;&quot;x&quot;&lt;/script&gt;"' in text
    assert text.count("&lt;script&gt;alert") >= 3        # summary, note and quote


def test_no_block_without_explanation():
    assert "ee-why" not in page([sub([question()])]).split("</style>", 1)[-1]


def test_note_cut_to_note_max_and_empty_note_has_no_paragraph():
    text = page([sub([_explained([_line(note="word " * 100)])])])
    assert "<p>" + ("word " * 59) + "word…</p>" in text and ("word " * 100).strip() not in text
    plain = page([sub([_explained([_line(note="")])])])
    assert "different population<blockquote>" in plain


def test_title_cut_to_60():
    long = "T" * 80
    studies = [topic_study(entry(1, subtopic=SUBTOPIC)), topic_study(entry(2, title=long, subtopic=SUBTOPIC))]
    text = page([sub([_explained([_line()])])], studies)
    assert f'title="{long}">{"T" * 59}…</a>' in text


def test_block_links_are_page_anchors_only():
    refs = Refs()
    refs.feed(html_render.evidence_section(topic_model(
        [topic_study(entry(1, subtopic=SUBTOPIC)), topic_study(entry(2, subtopic=SUBTOPIC))],
        evidence=[sub([_explained([_line()])])])))
    assert refs.hrefs == ["#s2"] and refs.srcs == []


# --- flat question list: subtopic title opens it, 2+ study questions stand out ----------------

def test_subtopic_title_opens_one_flat_question_list():
    text = page([sub([question()], others=[other(3, question="Lone?")])])
    section = text.split('id="ee-evidence"', 1)[1].split("</section>", 1)[0]
    assert ('<details class="ee-sub"><summary><h3>Title tags &amp; meta</h3>'
            '<span class="meta">2 questions · ') in section
    assert '<details class="ee-sub" open' not in section                      # closed by default
    questions = section.split('<ul class="ee-questions">', 1)[1].split("</ul></details>", 1)[0]
    assert questions.index("ee-multi") < questions.index("ee-single")       # one level, 2+ first
    assert "ee-others" not in section and "Other studies" not in section


def test_multi_study_questions_are_highlighted_and_single_questions_bold():
    css = html_assets.CSS
    assert ".ee-question.ee-multi{background:var(--card);border-left:3px solid var(--rule)" in css
    assert ".ee-single-q{display:block;font-weight:600}" in css
    assert ".ee-question>strong,.ee-question>details>summary>strong{font-weight:600}" in css


def test_a_link_to_a_subtopic_opens_it():
    assert "details.ee-sub" in html_assets.JS and "addEventListener('hashchange'" in html_assets.JS
