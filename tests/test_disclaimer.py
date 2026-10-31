"""The topic's grading disclaimer on every page and file (grading rules, Task 9)."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from evidence_echo.core import overview as overview_mod
from evidence_echo.core import topics
from evidence_echo.core.overview import TopicSummary
from evidence_echo.render import html as html_render
from evidence_echo.render import markdown, model, obsidian
from evidence_echo.render import rejections, sources_page
from tests import html_fixtures as hf
from tests import obsidian_fixtures as of
from tests.test_regrade import _exercise, _seed

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
TEXT = "Summary of published studies, not medical, health or training advice."


def _count(text):
    return text.count(TEXT)


def test_exercise_template_disclaimer_is_what_the_tests_expect():
    assert _exercise().grading["disclaimer"] == TEXT


def test_html_pages_show_it_once_first():
    r = hf.report(disclaimer=TEXT)
    page = html_render.report_page(r, "t.html", "o.html")
    assert _count(page) == 1 and '<p class="ee-disclaimer">' in page
    assert page.index("<p class=\"ee-disclaimer\">") < page.index("<h1>")
    t = hf.topic_model([hf.topic_study(hf.entry(1))], disclaimer=TEXT)
    assert _count(html_render.topic_page(t, "o.html", "r.html")) == 1
    empty = hf.topic_model(disclaimer=TEXT)
    assert _count(html_render.topic_page(empty, "o.html", "r.html")) == 1


def test_html_escapes_and_absent_is_empty():
    assert html_render.disclaimer_html(None) == "" and html_render.disclaimer_html("") == ""
    assert "&lt;b&gt;" in html_render.disclaimer_html("<b>")


def test_sources_page_shows_it(db):
    topic = _exercise()
    page = sources_page.build_sources(db, topic, NOW)
    assert page.disclaimer == TEXT
    assert _count(sources_page.sources_html(page, "o.html", "t.html", "r.html")) == 1
    assert "<p class=\"ee-disclaimer\">" not in sources_page.sources_html(
        replace(page, disclaimer=None), "o.html", "t.html", "r.html")


def test_rejections_show_it():
    md = rejections.markdown("Ex", None, [], TEXT)
    assert md.splitlines()[0].startswith("# ") and md.splitlines()[2] == f"*{TEXT}*"
    assert _count(rejections.html_page("Ex", None, [], "r.html", "o.html", "t.html", TEXT)) == 1
    assert TEXT not in rejections.markdown("Ex", None, [])
    assert "<p class=\"ee-disclaimer\">" not in rejections.html_page("Ex", None, [], "r.html")


def test_markdown_report_line_three():
    lines = markdown.report_markdown(hf.report(disclaimer=TEXT)).splitlines()
    assert lines[0].startswith("# ") and lines[1] == "" and lines[2] == f"*{TEXT}*"
    assert TEXT not in markdown.report_markdown(hf.report())


def test_obsidian_index_under_h1():
    notes = obsidian.render_notes(replace(of.topic([of.study(1)]), disclaimer=TEXT), "SEO")
    index = next(v for k, v in notes.items() if k.endswith("_index.md"))
    lines = index.splitlines()
    h1 = next(i for i, line in enumerate(lines) if line.startswith("# "))
    assert lines[h1 + 2] == f"*{TEXT}*" and _count(index) == 1
    assert TEXT not in next(v for k, v in obsidian.render_notes(
        of.topic([of.study(1)]), "SEO").items() if k.endswith("_index.md"))


def _summary(**over):
    report = {"path": "/x/r.md", "rendered_at": "2026-10-16T09:00:00+00:00", "entries": []}
    return TopicSummary("ex", "Ex", report=report, **over)


def test_overview_files_show_it():
    s = _summary(disclaimer=TEXT)
    md = overview_mod.render([s], Path("/x"), NOW)
    assert _count(md) == 1
    lines = md.splitlines()
    h2 = next(i for i, line in enumerate(lines) if line.startswith("## Ex"))
    assert lines[h2 + 2] == f"*{TEXT}*"
    page = html_render.overview_page([s], Path("/x"), NOW)
    assert _count(page) == 1
    assert page.index("<h2>") < page.index("<p class=\"ee-disclaimer\">")
    plain = _summary()
    assert TEXT not in overview_mod.render([plain], Path("/x"), NOW)
    assert "<p class=\"ee-disclaimer\">" not in html_render.overview_page([plain], Path("/x"), NOW)


def test_overview_not_run_and_error_cards_show_it():
    s = TopicSummary("ex", "Ex", disclaimer=TEXT)
    assert _count(overview_mod.render([s], Path("/x"), NOW)) == 1
    assert _count(html_render.overview_page([s], Path("/x"), NOW)) == 1


def test_built_models_carry_it_for_exercise_and_not_seo(db):
    topic = _exercise()
    _seed(db, topic, 1, verified="A")
    seo = topics.load_template("seo", Path(__file__).resolve().parents[1] / "plugins/evidence-echo/templates")
    assert model.build_topic(db, topic, NOW).disclaimer == TEXT
    assert model.build_topic(db, seo, NOW).disclaimer is None
    assert sources_page.build_sources(db, seo, NOW).disclaimer is None
