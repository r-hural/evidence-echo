"""Metrics on the pages: the Measured line, the Metric filter, the Obsidian frontmatter, escaping."""
import json
from datetime import datetime, timezone

from evidence_echo.core import store
from evidence_echo.render import html as html_render
from evidence_echo.render import html_assets, markdown, model, obsidian
from tests import html_fixtures as hf
from tests import obsidian_fixtures as of
from tests.test_render_html_subtopics import DATA_TAG, _data
from tests.test_render_obsidian import _frontmatter

EVIL = '<script>alert("x")</script> [[evil]]'
LIST = [("clicks", "Clicks"), ("rankings", "Rankings"), ("ai-citations", "AI citations"), ("other", "Other")]


def _view(slug=None, name=None, free=None, **over):
    return hf.view(metric=free, metric_slug=slug, metric_name=name, **over)


def _page(entries, metrics=LIST):
    studies = [hf.topic_study(e) for e in entries]
    return html_render.topic_page(hf.topic_model(studies, metrics=list(metrics)), "overview.html", "r.html")


def test_measured_line_variants():
    assert markdown.fact_pairs(_view("clicks", "Clicks", "organic clicks")) == [("Measured", "Clicks · organic clicks")]
    assert markdown.fact_pairs(_view("other", None, "organic clicks")) == [("Measured", "organic clicks")]
    assert markdown.fact_pairs(_view(None, None, "organic clicks")) == [("Measured", "organic clicks")]
    assert markdown.fact_pairs(_view("clicks", "Clicks", None)) == [("Measured", "Clicks")]
    assert markdown.fact_pairs(_view("other", None, None)) == []
    assert markdown.fact_pairs(_view()) == []
    assert markdown.fact_pairs(hf.view(metric="organic clicks")) == [("Measured", "organic clicks")]   # an old view


def test_measured_line_on_the_card_and_in_the_report():
    e = hf.entry(1, view=_view("clicks", "Clicks", "organic clicks"))
    assert "Measured" in html_render.card(e, "2026-10-16") and "Clicks · organic clicks" in html_render.card(e, "x")
    report = markdown.entry_lines(e)
    assert "Measured: Clicks · organic clicks  " in report


def _entries():
    return [hf.entry(1, view=_view("clicks", "Clicks")), hf.entry(2, view=_view("clicks", "Clicks")),
            hf.entry(3, view=_view("ai-citations", "AI citations")),
            hf.entry(4, view=_view("other", None)), hf.entry(5, view=_view())]


def test_metric_filter_options_and_counts():
    text = _page(_entries())
    select = '<select name="metric"><option value="">Any metric</option>' \
             '<option value="clicks">Clicks (2)</option>' \
             '<option value="ai-citations">AI citations (1)</option>' \
             '<option value="other">Other (1)</option></select>'
    assert f"<label>Metric {select}</label>" in text
    assert text.index('name="subtopic"') < text.index('name="metric"') if 'name="subtopic"' in text else True
    rows = {r["id"]: r for r in _data(text)["studies"]}
    assert rows[1]["metric"] == "clicks" and rows[3]["metric"] == "ai-citations" and rows[4]["metric"] == "other"
    assert "metric" not in rows[5]                                          # no metric: no key


def test_metric_filter_follows_list_order_with_other_last():
    text = _page(_entries(), metrics=[("ai-citations", "AI citations"), ("clicks", "Clicks"), ("other", "Other")])
    options = text[text.index('<select name="metric">'):]
    options = options[:options.index("</select>")]
    assert options.index("AI citations (1)") < options.index("Clicks (2)") < options.index("Other (1)")


def test_metric_filter_without_a_list_falls_back_to_the_views():
    text = _page(_entries(), metrics=[])
    assert "Clicks (2)" in text and "AI citations (1)" in text and "Other (1)" in text
    options = text[text.index('<select name="metric">'):]
    assert options.index("Other (1)") > options.index("Clicks (2)")


def test_metric_filter_hidden_with_one_metric():
    one = [hf.entry(1, view=_view("clicks", "Clicks")), hf.entry(2, view=_view("clicks", "Clicks")),
           hf.entry(3, view=_view())]
    assert 'name="metric"' not in _page(one)
    other_only = [hf.entry(1, view=_view("other", None)), hf.entry(2, view=_view("other", None))]
    assert 'name="metric"' not in _page(other_only)
    assert 'name="metric"' not in _page([hf.entry(1)])                      # old views: nothing to filter
    assert 'name="metric"' in _page(_entries())


def test_metric_filter_sits_right_after_the_subtopic_filter():
    from evidence_echo.core import knowledge
    from tests.test_render_html_subtopics import SUBTOPIC, sub, question
    entries = [hf.entry(1, view=_view("clicks", "Clicks"), subtopic=SUBTOPIC),
               hf.entry(2, view=_view("rankings", "Rankings"), subtopic=SUBTOPIC)]
    t = hf.topic_model([hf.topic_study(e) for e in entries], metrics=LIST, evidence=[sub([question()])])
    text = html_render.topic_page(t, "overview.html", "r.html")
    assert text.index('name="subtopic"') < text.index('name="metric"') < text.index('name="disputed"')
    assert text.index("</label>", text.index('name="subtopic"')) + len("</label>") == text.index("<label>Metric")


def test_metric_filter_js_wiring():
    js = html_assets.JS
    assert "if (f.metric && s.metric !== f.metric) return false;" in js
    assert "value('metric')" in js and "metric: value('metric')" in js


def test_metric_filter_shares_the_look_of_the_other_selects():
    text = _page(_entries())
    assert text.count("<select name=") >= 4
    assert hf.refs(text).unlabelled == []                                   # every control sits in a <label>


def test_obsidian_metric_frontmatter():
    named = of.study(1, view=_view("clicks", "Clicks"))
    plain_other = of.study(2, view=_view("other", None))
    none = of.study(3)
    notes = obsidian.render_notes(of.topic([named, plain_other, none]), "SEO")
    stems = obsidian._study_stems(of.topic([named, plain_other, none]))
    front = {sid: _frontmatter(notes[f"SEO/studies/{stems[sid]}.md"]) for sid in (1, 2, 3)}
    assert front[1]["metric"] == "Clicks"
    assert "metric" not in front[2] and "metric" not in front[3]            # Other and no metric: absent
    keys = list(front[1])
    assert "subtopic" not in keys or keys.index("metric") == keys.index("subtopic") + 1


def test_obsidian_measured_line():
    t = of.topic([of.study(1, view=_view("clicks", "Clicks", "organic clicks"))])
    note = obsidian.render_notes(t, "SEO")[f"SEO/studies/{obsidian._study_stems(t)[1]}.md"]
    assert "Measured: Clicks · organic clicks" in note


def test_metric_name_is_escaped_everywhere():
    view = _view("evil", EVIL, EVIL)
    e = hf.entry(1, view=view)
    # HTML card and filter
    text = _page([e, hf.entry(2, view=_view("clicks", "Clicks"))], metrics=[("evil", EVIL), ("clicks", "Clicks")])
    assert "<script>alert" not in text.replace(DATA_TAG, "").split("</head>")[-1].split("<script>")[0]
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; [[evil]] · &lt;script&gt;" in text
    assert text.count("<script") == 2 or "<script>alert" not in text                    # page script + data only
    assert "<script>alert" not in text
    # Markdown report: plain text, no tag or link can form
    lines = "\n".join(markdown.entry_lines(e))
    assert "<script>" not in lines and "\\[\\[evil\\]\\]" in lines and "&lt;script&gt;" in lines
    # Obsidian note body and frontmatter
    t = of.topic([of.study(1, view=view)])
    note = obsidian.render_notes(t, "SEO")[f"SEO/studies/{obsidian._study_stems(t)[1]}.md"]
    body = note.split("\n---\n", 1)[1]                                                # after the frontmatter
    assert "<script>" not in body and "[[evil]]" not in body
    assert _frontmatter(note)["metric"] == EVIL                                       # a JSON string: inert data


def test_grading_view_carries_the_resolved_metric(db, topic):
    """The view has the metric slug, and the name unless it is Other."""
    from tests.test_render_model import NOW, _study
    sid = _study(db, 1, metric="ctr")
    other = _study(db, 2, numbers=("77%",), primary="https://two.example.com/b", metric="other",
                   text="Rankings fell 9% across forty sites in March.", quotes=None)
    study = store.get_study(db, sid)
    view = model.grading_view(study, [], None, store.study_metric(db, study))
    assert (view["metric_slug"], view["metric_name"]) == ("ctr", "CTR")
    view = model.grading_view(store.get_study(db, other), [], None, store.study_metric(db, store.get_study(db, other)))
    assert (view["metric_slug"], view["metric_name"]) == ("other", None)
    none = model.grading_view(store.get_study(db, sid), [])
    assert none["metric_slug"] is None and none["metric_name"] is None
