# tests/test_render_questions.py
"""Questions end to end on the page: built from a database, rendered as Markdown and HTML."""
import json
from datetime import datetime, timezone

from evidence_echo.core import knowledge, store
from evidence_echo.render import html as html_render
from evidence_echo.render import markdown, model
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)
EVIL = '<script>alert("x")</script> [[x]] [a](http://evil.example)'


def reported(db, *sids):
    store.record_report(db, "r.md", [(sid, f"Title {sid}", f"Summary {sid}") for sid in sids])


def topic_page(db, topic):
    t = model.build_topic(db, topic, NOW)
    return t, html_render.topic_page(t, "overview.html", "r.html")


def section(text):
    return text.split('id="ee-evidence"', 1)[1].split("</section>", 1)[0]


def test_card_line_with_question_and_label(subtopiced, topic):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="A")
    add(subtopiced, question=qid, by="B")
    rep = model.build_report(subtopiced, topic, {}, NOW)
    entry = next(e for e in rep.new if e.study_id == a)
    assert markdown.subtopic_lines(entry.subtopic) == [
        ("Subtopic", "Title tags & meta"),
        ("Question", "Does X raise Y? · ✓ consistent (2 independent studies)")]
    assert ("<b>Question:</b> Does X raise Y? · ✓ consistent (2 independent studies)"
            " · corroborated by 1 other independent study") in html_render.report_page(rep, "t.html", "o.html")


def test_card_line_without_label_or_question(subtopiced, topic):
    a = add(subtopiced, by="A")
    rep = model.build_report(subtopiced, topic, {}, NOW)
    text = markdown.report_markdown(rep)
    assert "Subtopic: Title tags & meta  \n" in text and "Question:" not in text
    assert "<b>Subtopic:</b> Title tags &amp; meta<" in html_render.report_page(rep, "t.html", "o.html")
    assert rep.new[0].study_id == a


def test_markdown_report_subtopic_line(subtopiced, topic):
    qid = q(subtopiced)
    add(subtopiced, question=qid, by="A")
    add(subtopiced, question=qid, by="B", direction="down")
    text = markdown.report_markdown(model.build_report(subtopiced, topic, {}, NOW))
    assert text.count("Subtopic: Title tags & meta  \nQuestion: Does X raise Y? · ⇅ contested "
                      "(2 independent studies)  \n") == 2


def test_section_order_labelled_one_group_others(subtopiced, topic):
    labelled, series, lone = q(subtopiced, text="Labelled?"), q(subtopiced, text="Series?"), q(subtopiced, text="Lone?")
    ids = [add(subtopiced, question=labelled, by="A"), add(subtopiced, question=labelled, by="B"),
           add(subtopiced, question=series, by="C"), add(subtopiced, question=series, by="C"),
           add(subtopiced, question=lone, by="D"), add(subtopiced, by="E")]
    reported(subtopiced, *ids)
    _, text = topic_page(subtopiced, topic)
    body = section(text)
    assert body.index("Labelled?") < body.index("Series?") < body.index("Lone?") < body.index("not grouped yet")
    assert '<strong>Labelled?</strong>' in body and 'ee-q-consistent' in body
    assert "<strong>Series?</strong> <span class=\"meta\">1 independent study · 2 articles" in body
    assert "Other studies" not in body and body.count('class="ee-question ee-multi') == 2
    assert '<li class="ee-question ee-single"><span class="ee-single-q">Lone?</span>' in body
    assert '<span class="ee-single-q ee-ungrouped">not grouped yet</span>' in body
    assert '<span class="meta">3 questions · 6 studies</span></summary>' in body


def test_ungrouped_studies_stay_on_the_page(subtopiced, topic):
    ids = [add(subtopiced, by="A"), add(subtopiced, by="B", subtopic="local"), add(subtopiced, subtopic=None)]
    reported(subtopiced, *ids)
    t, text = topic_page(subtopiced, topic)
    body = section(text)
    assert body.count("not grouped yet") == 2 and "3 studies wait" not in body
    assert "2 studies wait to be grouped" in body                   # the Unsorted study is never grouped
    for sid in ids:
        assert f'href="#s{sid}"' in body
    assert '<span class="meta">1 study</span></summary>' in body and "Unsorted studies" not in body
    assert t.waiting == 2


def test_waiting_note_only_when_non_zero(subtopiced, topic):
    qid = q(subtopiced)
    a = add(subtopiced, question=qid, by="A")
    reported(subtopiced, a)
    assert "wait" not in topic_page(subtopiced, topic)[1].split('id="ee-evidence"', 1)[1].split("</section>")[0]
    b = add(subtopiced, by="B")
    store.record_report(subtopiced, "r2.md", [(b, "T", "S")])
    assert "1 study waits to be grouped" in topic_page(subtopiced, topic)[1]


def test_question_text_is_escaped_everywhere(subtopiced, topic):
    qid = q(subtopiced, text=EVIL[:120], measure=EVIL[:80])
    a = add(subtopiced, question=qid, by="A")
    b = add(subtopiced, question=qid, by="B", direction="down")
    report_html = html_render.report_page(model.build_report(subtopiced, topic, {}, NOW), "t.html", "o.html")
    reported(subtopiced, a, b)
    state = knowledge.explain_state(subtopiced, next(g for e in knowledge.evidence(subtopiced)
                                                     for g in e.questions if g.question_id == qid))
    store.save_question_explanation(subtopiced, question_id=qid, status="agreed", summary=EVIL, main_result="up",
                                    lines=[{"study": b, "reason": "genuine_conflict", "quote": EVIL, "note": EVIL}],
                                    inputs_hash=state.inputs_hash, prompt_version="relate-4")
    _, topic_html = topic_page(subtopiced, topic)
    for text in (topic_html, report_html):
        assert "<script>alert" not in text and "&lt;script&gt;alert" in text
        assert 'href="http://evil.example' not in text


def test_subtopic_filter_options_come_from_studies(subtopiced, topic):
    ids = [add(subtopiced, by="A"), add(subtopiced, by="B", subtopic="local")]
    add(subtopiced, by="C", subtopic="backlinks")                    # not reported: no option
    reported(subtopiced, *ids)
    _, text = topic_page(subtopiced, topic)
    assert ('<option value="">Any subtopic</option><option value="title-tags-meta">Title tags &amp; meta'
            '</option><option value="local">Local</option></select>') in text
    data = json.loads(text.split('id="ee-data">', 1)[1].split("</script>", 1)[0])
    assert sorted(r["subtopic"] for r in data["studies"]) == ["local", "title-tags-meta"]


def test_markdown_report_question_is_plain_text(subtopiced, topic):
    qid = q(subtopiced, text=EVIL)
    add(subtopiced, question=qid, by="A")
    text = markdown.report_markdown(model.build_report(subtopiced, topic, {}, NOW))
    assert "<script>" not in text and "[a](http" not in text
    assert "Subtopic: Title tags & meta  \nQuestion: &lt;script&gt;" in text
