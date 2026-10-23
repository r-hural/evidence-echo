"""Entities on the pages: the About line, the Entity filter, the Obsidian frontmatter, escaping."""
from evidence_echo.render import html as html_render
from evidence_echo.render import html_assets, markdown, obsidian
from tests import html_fixtures as hf
from tests import obsidian_fixtures as of
from tests.test_render_html_subtopics import _data
from tests.test_render_metrics import LIST, _view
from tests.test_render_obsidian import _frontmatter

HOSTILE = '<script>alert(1)</script> "x" [[y]] | >z'
SHOP, CHAT, WIX = ("shopify", "Shopify"), ("chatgpt", "ChatGPT"), ("wix", "Wix")


def _page(entries, **over):
    studies = [hf.topic_study(e) for e in entries]
    return html_render.topic_page(hf.topic_model(studies, **over), "overview.html", "r.html")


def _entries():
    return [hf.entry(1, view=hf.view(entities=[SHOP, CHAT])), hf.entry(2, view=hf.view(entities=[SHOP])),
            hf.entry(3, view=hf.view(entities=[WIX])), hf.entry(4)]


def test_about_line_before_measured():
    view = _view("clicks", "Clicks", "organic clicks", entities=[SHOP, CHAT])
    names = [n for n, _ in markdown.fact_pairs(view)]
    assert names.index("About") + 1 == names.index("Measured")
    assert dict(markdown.fact_pairs(view))["About"] == "Shopify, ChatGPT"
    assert "About" not in [n for n, _ in markdown.fact_pairs(_view("clicks", "Clicks", None))]
    assert "About" not in [n for n, _ in markdown.fact_pairs(hf.view(entities=[]))]


def test_about_line_on_the_card_and_in_the_report():
    e = hf.entry(1, view=hf.view(entities=[SHOP, CHAT]))
    assert "About" in html_render.card(e, "2026-10-16") and "Shopify, ChatGPT" in html_render.card(e, "x")
    assert "About: Shopify, ChatGPT  " in markdown.entry_lines(e)


def test_entity_filter_options_and_order():
    text = _page(_entries())
    select = ('<select name="entity"><option value="">Any entity</option>'
              '<option value="shopify">Shopify (2)</option><option value="chatgpt">ChatGPT (1)</option>'
              '<option value="wix">Wix (1)</option></select>')
    assert f"<label>Entity {select}</label>" in text
    assert 'name="entity"' not in _page([hf.entry(1), hf.entry(2)])


def test_entity_filter_sits_after_metric_or_subtopic():
    entries = [hf.entry(1, view=_view("clicks", "Clicks", entities=[SHOP])),
               hf.entry(2, view=_view("rankings", "Rankings", entities=[SHOP]))]
    text = _page(entries, metrics=LIST)
    assert text.index('name="metric"') < text.index('name="entity"') < text.index('name="disputed"')
    assert text.index("</label>", text.index('name="metric"')) + len("</label>") == text.index("<label>Entity")
    from tests.test_render_html_subtopics import SUBTOPIC, sub, question
    one = [hf.entry(1, view=hf.view(entities=[SHOP]), subtopic=SUBTOPIC)]
    text = _page(one, evidence=[sub([question()])])
    assert 'name="metric"' not in text
    assert text.index('name="subtopic"') < text.index('name="entity"') < text.index('name="disputed"')


def test_filter_rows_carry_entity_slugs_and_search_text():
    rows = {r["id"]: r for r in _data(_page(_entries()))["studies"]}
    assert rows[2]["entities"] == ["shopify"] and "shopify" in rows[2]["text"]
    assert rows[1]["entities"] == ["shopify", "chatgpt"] and "chatgpt" in rows[1]["text"]
    assert "entities" not in rows[4]


def test_entity_filter_js_wiring():
    js = html_assets.JS
    assert "if (f.entity && (s.entities || []).indexOf(f.entity) < 0) return false;" in js
    assert "entity: value('entity')" in js
    assert hf.refs(_page(_entries())).unlabelled == []


def test_obsidian_frontmatter_lists_entities():
    named = of.study(1, view=of.view(entities=[SHOP, CHAT]))
    none = of.study(2)
    t = of.topic([named, none])
    notes = obsidian.render_notes(t, "SEO")
    stems = obsidian._study_stems(t)
    front = {sid: _frontmatter(notes[f"SEO/studies/{stems[sid]}.md"]) for sid in (1, 2)}
    assert front[1]["entities"] == ["Shopify", "ChatGPT"] and "entities" not in front[2]
    assert "About: Shopify, ChatGPT" in notes[f"SEO/studies/{stems[1]}.md"]


def test_entity_name_is_escaped_everywhere():
    view = hf.view(entities=[("hostile", HOSTILE)])
    e = hf.entry(1, view=view)
    card = html_render.card(e, "x")
    assert "<script>alert(1)</script>" not in card and "&lt;script&gt;" in card
    text = _page([e, hf.entry(2, view=hf.view(entities=[("hostile", HOSTILE)]))])
    assert "<script>alert(1)</script>" not in text.replace(html_assets.JS, "")
    option = text[text.index('<select name="entity">'):text.index("</select>", text.index('<select name="entity">'))]
    assert "&lt;script&gt;" in option and "<script>" not in option
    line = [t for t in markdown.grading_texts(view) if t.startswith("About")][0]
    assert "<script>" not in line and "\\[\\[y\\]\\]" in line
    note = obsidian.render_notes(of.topic([of.study(1, view=of.view(entities=[("hostile", HOSTILE)]))]), "SEO")
    body = next(v for k, v in note.items() if "/studies/" in k)
    assert "[[y]]" not in body.split("---", 2)[2] and "<script>" not in body.split("---", 2)[2]
    assert not any(ln.startswith(">z") or ln.startswith("> z") for ln in body.splitlines())
