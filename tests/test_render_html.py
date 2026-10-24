"""SP5a-H: HTML pages. Pure renderer tests: model objects built directly, tmp_path only."""
import html
import json
import re

import pytest

from evidence_echo.render import html as html_render
from evidence_echo.render import html_assets

EVIL = '<script>alert("x")</script>&\''


def test_esc_escapes_markup_quotes_and_ampersands():
    assert html_render.esc(EVIL) == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&amp;&#x27;"
    assert html_render.esc(None) == ""
    assert html_render.esc(3) == "3"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "java\nscript:alert(1)",
    "data:text/html,<b>x</b>", "file:///etc/passwd", "//evil.example/x", "/local", "",
    "https://exa mple.com", "https://example.com/\x00", "ftp://example.com/x"])
def test_link_refuses_anything_but_http_and_https(url):
    out = html_render.link(url, "name")
    assert "<a" not in out and "href" not in out
    assert "<b>" not in out


@pytest.mark.parametrize("url", ["https://example.com/a?b=1&c=2", "HTTP://EXAMPLE.COM/"])
def test_link_makes_an_anchor_for_web_urls(url):
    out = html_render.link(url, "Example")
    assert out == f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">Example</a>'


def test_link_escapes_the_url_inside_the_attribute():
    out = html_render.link('https://e.com/?q="><script>x</script>')
    assert "<script>" not in out and '"><' not in out
    assert 'href="https://e.com/?q=&quot;&gt;&lt;script&gt;x&lt;/script&gt;"' in out


def test_link_without_web_scheme_shows_name_and_escaped_url():
    assert html_render.link("javascript:alert(1)", "Blog") == "Blog (javascript:alert(1))"
    assert html_render.link("javascript:alert(1)") == "javascript:alert(1)"
    assert html_render.link(None, "Blog") == "Blog"


def test_json_script_cannot_close_its_tag():
    data = {"title": "</script><script>alert(1)</script> & <!--", "n": 1}
    out = html_render.json_script(data, "ee-data")
    assert out.startswith('<script type="application/json" id="ee-data">')
    assert out.count("</script>") == 1 and out.endswith("</script>")
    inner = out[len('<script type="application/json" id="ee-data">'):-len("</script>")]
    assert "<" not in inner and ">" not in inner and "&" not in inner
    assert json.loads(inner) == data


def test_href_is_relative_between_siblings(tmp_path):
    reports = tmp_path / "R" / "seo"
    assert html_render.href(reports / "seo.html", reports) == "seo.html"
    assert html_render.href(tmp_path / "R" / "overview.html", reports) == "../overview.html"


def test_href_on_another_drive_is_a_file_uri(tmp_path, monkeypatch):
    def other_drive(*_):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")
    monkeypatch.setattr(html_render.os.path, "relpath", other_drive)
    target = tmp_path / "overview.html"
    assert html_render.href(target, tmp_path / "x") == target.resolve().as_uri()


def test_page_is_self_contained():
    out = html_render.page("T <1>", "<p>body</p>")
    assert out.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in out
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in out
    assert "<title>T &lt;1&gt;</title>" in out
    assert f"<style>{html_assets.CSS}</style>" in out
    assert " src=" not in out and "<link" not in out and "@import" not in out
    assert "url(" not in html_assets.CSS


def test_write_is_atomic_and_overwrites(tmp_path):
    path = tmp_path / "a" / "b.html"
    html_render._write(path, "one")
    html_render._write(path, "two – ü")
    assert path.read_text(encoding="utf-8") == "two – ü"
    assert list(path.parent.iterdir()) == [path]


def _luminance(colour: str) -> float:
    rgb = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted([_luminance(a), _luminance(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_colour_pairs_meet_wcag_aa(scheme):
    tokens = html_assets.PALETTES[scheme]
    for text, background in html_assets.CONTRAST_PAIRS:
        assert _contrast(tokens[text], tokens[background]) >= 4.5, (scheme, text, background)


def test_both_colour_schemes_define_the_same_tokens_and_css_uses_them():
    assert set(html_assets.PALETTES["light"]) == set(html_assets.PALETTES["dark"])
    assert "@media (prefers-color-scheme: dark)" in html_assets.CSS
    for token in html_assets.PALETTES["light"]:
        assert f"--{token}:" in html_assets.CSS
    for key in html_assets.TIER_KEYS:   # the Harvey ball's fill per tier
        assert f".tier-{key}{{--fill:{html_assets.TIER_FILL[key]}}}" in html_assets.CSS
    assert "conic-gradient(currentColor var(--fill)" in html_assets.CSS


def test_css_wraps_long_strings_and_fits_phones():
    css = html_assets.CSS
    assert "overflow-wrap:anywhere" in css
    assert "main{max-width:var(--page)" in css and "--page:78rem" in css
    assert "max-width:100%" in css
    assert not re.search(r"width:\s*\d{3,}px", css)  # no fixed widths wider than a phone
    assert "</" not in css


from evidence_echo.render import markdown as md_render
from evidence_echo.render.model import Link
from tests.html_fixtures import entry, refs, report, seen, topic_model, topic_study, view

FULL = entry(
    2, tier="C", series="Annual CTR study", editions=["2025", "2026"],
    view=view(flags=["no_baseline", "cherry_pick"], missing_numbers=["9%"], capped_from="A",
              judged_from="excerpt", metric="organic clicks", baseline="previous quarter",
              gap_note="judge: B",
              provenance={"relation": "vendor", "conflict_of_interest": "sells the tool"}),
    coverage=[Link("Blog A", "https://a.example/1"), Link("Blog B", "https://b.example/2")])
DISPUTED = entry(3, tier="A", disputed=True, skeptic_tiers=["C"],
                 dispute_reason="grades two tiers apart", skeptic_notes=["n=3 sites", "no control"],
                 view=view(gap_note="judge: B", provenance={"relation": "agency"}))


def test_label_parts_match_the_markdown_label():
    assert html_render.label_parts(entry(1)) == ("B", [])
    assert html_render.label_parts(FULL) == ("C", ["vendor", "capped from A", "judge: B"])
    # disputed: double grade, and no gap note
    assert html_render.label_parts(DISPUTED) == ("A / skeptic: C", ["agency"])
    no_tiers = entry(4, disputed=True, skeptic_tiers=[])
    assert html_render.label_parts(no_tiers) == ("B / skeptic: disputed", [])
    no_quote = entry(5, disputed=True, no_quote_only=True, skeptic_tiers=["C"])
    assert html_render.label_parts(no_quote) == ("B", [])
    unknown = entry(6, tier=None, view=view(provenance={"relation": "unknown"}))
    assert html_render.label_parts(unknown) == ("?", [])


def test_card_texts_equal_the_markdown_entry():
    """The card shows the same label, grading lines, series and date texts as the Markdown."""
    text = md_render.report_markdown(report(new=[entry(1), FULL], disputed=[DISPUTED]))
    for e in (entry(1), FULL, DISPUTED):
        tier, extras = html_render.label_parts(e)
        assert f"### [{' · '.join([tier] + extras)}] {e.title}" in text
        for line in md_render.grading_texts(e.view):
            assert f"{line}  " in text
        assert f"*Research: {e.research_timeframe}*" in text
        assert f"*Published: {e.published[:10]}*" in text
    assert f"*{html_render.series_text(FULL)}*" in text
    assert f"> {html_render.dispute_text(DISPUTED)}" in text
    # no dispute reason: the Markdown has no "Disputed." lead-in, and neither may the card
    bare = entry(10, disputed=True)
    assert f"> {html_render.dispute_text(bare)}" in md_render.report_markdown(
        report(disputed=[bare], new=[]))


def test_grading_lines_texts():
    assert md_render.grading_texts(FULL.view) == [
        "Red flags: no baseline, cherry pick", "Conflict of interest: sells the tool",
        "Numbers not found in the text: 9%", "Graded from: the excerpt only",
        "Measured: organic clicks", "Baseline: previous quarter"]
    assert md_render.grading_texts(view(baseline="last year")) == ["Baseline: last year"]
    assert md_render.grading_texts(view()) == []


def test_series_and_dispute_texts():
    assert html_render.series_text(FULL) == "Part of: Annual CTR study (editions 2025, 2026)"
    assert html_render.series_text(entry(1, series="S")) == "Part of: S"
    assert html_render.series_text(entry(1)) is None
    assert html_render.dispute_text(DISPUTED) == \
        "Disputed: grades two tiers apart. Skeptic's doubts: n=3 sites; no control"
    assert html_render.dispute_text(entry(1, disputed=True)) == \
        "Skeptic's doubts: no reason given"


def test_card_contents():
    out = html_render.card(FULL)
    assert out.startswith('<article class="card" id="s2" data-study="2">')
    assert '<span class="tier tier-c"><span class="sr">Tier </span>C</span>' in out
    assert "vendor · capped from A · judge: B" in out
    assert f"<h3>{html.escape(FULL.title)}</h3>" in out
    assert f"<p>{FULL.summary}</p>" in out
    assert '<p class="ee-f"><b>Red flags:</b> <span class="ee-warn">no baseline, cherry pick</span></p>' in out
    assert "<b>Part of:</b> Annual CTR study (editions 2025, 2026)" in out
    assert "<b>Research:</b> 2026-08" in out and "<b>Published:</b> 2026-08-10" in out
    assert '<b>Primary source:</b> <a href="https://example.com/study"' in out
    assert '<a href="https://a.example/1" rel="noopener noreferrer">Blog A</a>' in out
    assert 'class="dispute"' not in out


def test_card_marks_disputes_and_ungraded():
    out = html_render.card(DISPUTED)
    assert '<p class="dispute"><b>Disputed:</b> grades two tiers apart.<br><b>Skeptic&#x27;s doubts:</b> ' in out
    assert "<span>skeptic: C · agency</span>" in out   # the margin block drops the " / "
    ungraded = html_render.card(entry(7, tier=None, research_timeframe=None, primary_url=None))
    assert '<span class="tier tier-x"><span class="sr">Tier </span>?</span>' in ungraded
    assert "<b>Research:</b> unstated" in ungraded
    assert "Primary source" not in ungraded


def test_card_escapes_every_text_field():
    e = entry(8, title=EVIL, summary=EVIL, tier=EVIL, series=EVIL, editions=[EVIL],
              research_timeframe=EVIL, published=EVIL, disputed=True, skeptic_tiers=[EVIL],
              dispute_reason=EVIL, skeptic_notes=[EVIL], primary_url="javascript:alert(1)",
              coverage=[Link(EVIL, "javascript:alert(2)"), Link(EVIL, "https://ok.example/")],
              view=view(flags=[EVIL], missing_numbers=[EVIL], capped_from=EVIL, metric=EVIL,
                        baseline=EVIL, gap_note=EVIL,
                        provenance={"relation": EVIL, "conflict_of_interest": EVIL}))
    out = html_render.card(e)
    assert "<script" not in out and 'alert("x")' not in out
    assert html.escape(EVIL, quote=True) in out
    assert refs(out).hrefs == ["https://ok.example/"]


from pathlib import Path

from evidence_echo.render.model import Link as _Link


def _layout(tmp_path):
    overview_dir = tmp_path / "R"
    reports_dir = overview_dir / "seo"
    return reports_dir, overview_dir, reports_dir / "2026-10-16-report.md"


def _sections(text, *markers):
    positions = [text.index(m) for m in markers]
    assert positions == sorted(positions), markers


def test_report_page_sections_in_markdown_order(tmp_path):
    r = report(new=[entry(1)], disputed=[DISPUTED],
               seen=[seen(9, disputed=True, skeptic_tiers=["D"], skeptic_notes=["old data"])],
               source_warnings=["Blog X (failing since 2026-10-01 — check or remove)"])
    out = html_render.report_page(r, "seo.html", "../overview.html")
    assert "<title>SEO Report - 2026-10-16</title>" in out
    assert "<h1>SEO Report - 2026-10-16</h1>" in out
    assert '<a href="seo.html">All studies</a>' in out and ">Overview</a>" not in out   # the crumb leads there
    _sections(out, "<h2>New studies</h2>", 'id="s1"',
              "<h2>Disputed (verdict and skeptic disagree; both grades shown)</h2>", 'id="s3"',
              "<h2>More coverage of earlier studies</h2>", "Source warnings: Blog X")
    assert ('<li><a href="seo.html#s9">Earlier study 9</a> (first reported 2026-10-01): '
            '<a href="https://other.example/9" rel="noopener noreferrer">Other Blog</a>'
            " — disputed: skeptic says D; old data</li>") in out


def test_report_page_without_new_studies_and_optional_sections(tmp_path):
    out = html_render.report_page(report(new=[]), "seo.html", "../overview.html")
    assert '<p class="empty">No confirmed evidence-backed items this run.</p>' in out
    for absent in ("Disputed (", "More coverage", "Source warnings"):
        assert absent not in out


def test_seen_item_disputed_without_tiers():
    out = html_render.seen_item(seen(4, disputed=True), "seo.html")
    assert out.endswith(" — disputed by skeptic</li>")


def test_report_page_escapes_every_text_field():
    r = report(topic_name=EVIL, date=EVIL, new=[entry(1, title=EVIL, summary=EVIL)],
               seen=[seen(9, first_title=EVIL, first_reported=EVIL, disputed=True,
                          skeptic_tiers=[EVIL], skeptic_notes=[EVIL],
                          coverage=[_Link(EVIL, "javascript:alert(3)")])],
               source_warnings=[EVIL])
    out = html_render.report_page(r, "seo.html", "../overview.html")
    assert "<script" not in out and 'alert("x")' not in out
    assert not any(h.lower().startswith("javascript") for h in refs(out).hrefs)


def test_write_report_files_and_links(tmp_path):
    reports_dir, overview_dir, md = _layout(tmp_path)
    t = topic_model([topic_study(entry(1))])
    out = html_render.write_report(report(), t, reports_dir, overview_dir, md)
    assert out == {"report": str(reports_dir / "2026-10-16-report.html"),
                   "topic": str(reports_dir / "seo.html")}
    page_text = (reports_dir / "2026-10-16-report.html").read_text(encoding="utf-8")
    assert '<a href="seo.html">All studies</a>' in page_text
    assert ">Overview</a>" not in page_text
    topic_text = (reports_dir / "seo.html").read_text(encoding="utf-8")
    assert ">Overview</a>" not in topic_text
    assert '<a href="2026-10-16-report.html">Latest report</a>' in topic_text


def test_html_name_follows_the_md_path_including_the_suffix_number(tmp_path):
    reports_dir, overview_dir, _ = _layout(tmp_path)
    md = reports_dir / "2026-10-16-report-2.md"
    out = html_render.write_report(report(), topic_model(), reports_dir, overview_dir, md)
    assert out["report"] == str(reports_dir / "2026-10-16-report-2.html")
    assert (reports_dir / "2026-10-16-report-2.html").exists()


def test_topic_page_is_rewritten_each_run_without_leftovers(tmp_path):
    reports_dir, overview_dir, md = _layout(tmp_path)
    html_render.write_report(report(), topic_model([topic_study(entry(1))]), reports_dir, overview_dir, md)
    html_render.write_report(report(), topic_model([topic_study(entry(1)), topic_study(entry(2))]),
                             reports_dir, overview_dir, md)
    text = (reports_dir / "seo.html").read_text(encoding="utf-8")
    assert 'id="s1"' in text and 'id="s2"' in text
    assert not list(reports_dir.glob("*.tmp"))


def test_topic_page_never_overwrites_the_overview(tmp_path):
    folder = tmp_path / "R"
    folder.mkdir()
    (folder / "overview.html").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="overview.html"):
        html_render.write_report(report(topic_id="overview"), topic_model(topic_id="overview"),
                                 folder, folder, folder / "2026-10-16-report.md")
    assert (folder / "overview.html").read_text(encoding="utf-8") == "keep"


def test_topic_page_with_zero_studies(tmp_path):
    out = html_render.topic_page(topic_model([]), "../overview.html", "2026-10-16-report.html")
    assert "<h1>SEO — all studies</h1>" in out
    assert '<p class="empty">No studies reported yet.</p>' in out
    assert "<form" not in out and "<script" not in out


def test_topic_page_with_one_study(tmp_path):
    out = html_render.topic_page(topic_model([topic_study(entry(1))]), "../overview.html", "r.html")
    assert '<p id="ee-count" class="meta" aria-live="polite">1 study</p>' in out
    assert "<b>Reported:</b> first 2026-10-01 · last 2026-10-16 · in 2 reports" in out
    assert "Updated 2026-10-16 09:00" in out


def test_topic_page_with_many_studies_lists_all_in_model_order():
    studies = [topic_study(entry(i)) for i in (5, 3, 8)]
    out = html_render.topic_page(topic_model(studies), "../overview.html", "r.html")
    assert "3 studies" in out
    _sections(out, 'id="s5"', 'id="s3"', 'id="s8"')


def test_pages_link_only_to_sources_and_siblings(tmp_path):
    reports_dir, overview_dir, md = _layout(tmp_path)
    t = topic_model([topic_study(FULL), topic_study(DISPUTED)])
    r = report(new=[FULL], disputed=[DISPUTED], seen=[seen(9)])
    out = html_render.write_report(r, t, reports_dir, overview_dir, md)
    sources = {"https://example.com/study", "https://a.example/1", "https://b.example/2",
               "https://example.com/post-3", "https://other.example/9"}
    for path in out.values():
        found = refs(Path(path).read_text(encoding="utf-8"))
        assert found.srcs == []
        for h in found.hrefs:
            assert h in sources or (":" not in h and not h.startswith("/")), h


from evidence_echo.core import grading

DATA_TAG = '<script type="application/json" id="ee-data">'


def _data(page_text):
    start = page_text.index(DATA_TAG) + len(DATA_TAG)
    return json.loads(page_text[start:page_text.index("</script>", start)])


def _many():
    return topic_model([
        topic_study(FULL, first="2026-09-01T00:00:00+00:00"),
        topic_study(DISPUTED),
        topic_study(entry(4, tier="D", series="Annual CTR study",
                          view=view(flags=["stale_data"], provenance={"relation": "independent"}))),
    ])


def test_filter_data_fields():
    rows = _data(html_render.topic_page(_many(), "o.html", "r.html"))["studies"]
    assert [r["id"] for r in rows] == [2, 3, 4] and [r["pos"] for r in rows] == [0, 1, 2]
    assert rows[0] == {"id": 2, "pos": 0, "tier": "C", "flags": ["no_baseline", "cherry_pick"],
                       "relation": "vendor", "series": "Annual CTR study", "disputed": False,
                       "text": (FULL.title + " " + FULL.summary).lower(),
                       "first_reported": "2026-09-01T00:00:00+00:00",
                       "last_reported": "2026-10-16T08:00:00+00:00"}
    assert rows[1]["disputed"] is True and rows[1]["relation"] == "agency"


def test_filter_form_is_hidden_until_js_and_every_control_is_labelled():
    out = html_render.topic_page(_many(), "o.html", "r.html")
    assert '<form id="ee-filters" class="filters" hidden>' in out
    assert "<fieldset><legend>Tier</legend>" in out
    for tier in "ABCD":
        assert f'<input type="checkbox" name="tier" value="{tier}" checked>' in out
    assert 'value="?"' not in out  # no ungraded study here
    assert refs(out).unlabelled == []
    # options come from the data, flags in RED_FLAGS order
    flags = [c for c in grading.RED_FLAGS if c in {"no_baseline", "cherry_pick", "stale_data"}]
    for code in flags:
        assert f'<option value="{code}">{code.replace("_", " ")}</option>' in out
    assert '<option value="*">Any red flag</option>' in out
    for rel in ("agency", "independent", "vendor"):
        assert f'<option value="{rel}">{rel}</option>' in out
    assert '<option value="Annual CTR study">Annual CTR study</option>' in out
    assert '<input type="checkbox" name="disputed">' in out
    assert '<input type="search" name="q"' in out
    assert ('<select name="sort"><option value="newest" selected>Newest</option>'
            '<option value="tier">Tier</option>'
            '<option value="first">First reported (oldest first)</option></select>') in out


def test_flag_options_follow_red_flags_order():
    out = html_render.topic_page(_many(), "o.html", "r.html")
    positions = [out.index(f'<option value="{c}">') for c in grading.RED_FLAGS
                 if f'<option value="{c}">' in out]
    assert positions == sorted(positions) and len(positions) == 3


def test_ungraded_studies_get_their_own_tier_box():
    out = html_render.topic_page(topic_model([topic_study(entry(1, tier=None))]), "o.html", "r.html")
    assert '<label><input type="checkbox" name="tier" value="?" checked> Ungraded</label>' in out
    assert _data(out)["studies"][0]["tier"] is None


def test_js_and_page_agree_on_ids_and_control_names():
    out = html_render.topic_page(_many(), "o.html", "r.html")
    for element_id in ("ee-data", "ee-filters", "ee-studies", "ee-count"):
        assert f'id="{element_id}"' in out and element_id in html_assets.JS
    for name in ("flag", "relation", "series", "disputed", "q", "sort"):
        assert f'name="{name}"' in out and f"'{name}'" in html_assets.JS
    assert 'name="tier"' in out and "input[name=tier]:checked" in html_assets.JS
    assert f"<script>{html_assets.JS}</script>" in out
    assert "</" not in html_assets.JS
    assert out.count("</script>") == 2  # the data block and the JS


def test_json_data_cannot_break_out_of_its_script_tag():
    evil = entry(1, title="</script><script>alert(1)</script>", summary="<!-- & -->")
    out = html_render.topic_page(topic_model([topic_study(evil)]), "o.html", "r.html")
    assert out.count("</script>") == 2
    assert "<script>alert(1)" not in out
    assert _data(out)["studies"][0]["text"].startswith("</script><script>alert(1)</script>")


def test_without_js_every_study_is_listed():
    out = html_render.topic_page(_many(), "o.html", "r.html")
    body = out.split('<div id="ee-studies">')[1]
    assert all(f'id="s{i}"' in body for i in (2, 3, 4))
    assert "hidden" not in body.split("</div>")[0]


def test_non_ascii_text_round_trips(tmp_path):
    reports_dir, overview_dir, md = _layout(tmp_path)
    title = "Переписані заголовки на 12 тис. сторінок: +4,2 % кліків"
    t = topic_model([topic_study(entry(1, title=title))])
    html_render.write_report(report(new=[entry(1, title=title)]), t, reports_dir, overview_dir, md)
    text = (reports_dir / "seo.html").read_text(encoding="utf-8")
    assert f"<h3>{title}</h3>" in text
    assert _data(text)["studies"][0]["text"].startswith(title.lower())
    assert title in (reports_dir / "2026-10-16-report.html").read_text(encoding="utf-8")


# --- overview page (Task 5) -------------------------------------------------------------------

from datetime import datetime

from evidence_echo.core.overview import TopicSummary

NOW = datetime(2026, 10, 16, 10, 30)


def _summary(tmp_path, tid="seo", name="SEO", entries=None, html_files=True, stem="2026-10-16-report"):
    reports = tmp_path / "R" / tid
    reports.mkdir(parents=True, exist_ok=True)
    md = reports / f"{stem}.md"
    md.write_text("# r", encoding="utf-8")
    if html_files:
        (reports / f"{stem}.html").write_text("r", encoding="utf-8")
        (reports / f"{tid}.html").write_text("t", encoding="utf-8")
    entries = entries if entries is not None else [
        {"study_id": 1, "title": "C study", "tier": "C", "published": "2026-10-10"},
        {"study_id": 2, "title": "A study", "tier": "A", "published": "2026-10-01"},
        {"study_id": 3, "title": "B new", "tier": "B", "published": "2026-10-12"},
        {"study_id": 4, "title": "B old", "tier": "B", "published": "2026-09-01"},
    ]
    return TopicSummary(tid, name, report={"path": str(md), "rendered_at": "2026-10-16T09:00:00+00:00",
                                           "entries": entries})


def test_overview_card_with_top_three_and_links(tmp_path):
    out = html_render.overview_page([_summary(tmp_path)], tmp_path / "R", NOW)
    assert "<h1>Evidence Echo — Overview</h1>" in out and "Updated 2026-10-16 10:30" in out
    assert "<h2>SEO</h2>" in out
    assert "last run 2026-10-16 · 4 new findings" in out
    _sections(out, "A study", "B new", "B old")
    assert "C study" not in out
    assert '<a href="seo/2026-10-16-report.html">Latest report</a>' in out
    assert '<a href="seo/seo.html">All studies</a>' in out


def test_overview_links_markdown_when_no_html_report(tmp_path):
    out = html_render.overview_page([_summary(tmp_path, html_files=False)], tmp_path / "R", NOW)
    assert '<a href="seo/2026-10-16-report.md">Latest report (Markdown)</a>' in out
    assert "All studies" not in out


def test_overview_one_finding_and_empty_report(tmp_path):
    one = _summary(tmp_path, "a", "Alpha", entries=[{"study_id": 1, "title": "T", "tier": None,
                                                    "published": None}])
    none = _summary(tmp_path, "b", "Beta", entries=[])
    out = html_render.overview_page([one, none], tmp_path / "R", NOW)
    assert "last run 2026-10-16 · 1 new finding</p>" in out
    assert '<span class="tier tier-x"><span class="sr">Tier </span>?</span> T' in out
    assert "0 new findings" in out


def test_overview_setup_error_and_not_run(tmp_path):
    out = html_render.overview_page(
        [TopicSummary("a", "Alpha"), TopicSummary("b", "b", error="b/topic.json: field 'name' missing")],
        tmp_path, NOW)
    assert "<h2>Alpha</h2>" in out and '<p class="meta">not run yet</p>' in out
    assert '<p class="warn">setup error: b/topic.json: field &#x27;name&#x27; missing</p>' in out


def test_overview_without_topics(tmp_path):
    out = html_render.overview_page([], tmp_path, NOW)
    assert '<p class="empty">No topics yet — run /evidence-echo:setup.</p>' in out


def test_overview_escapes_names_titles_and_errors(tmp_path):
    s = _summary(tmp_path, entries=[{"study_id": 1, "title": EVIL, "tier": "B", "published": None}])
    s = TopicSummary(s.id, EVIL, report=s.report)
    out = html_render.overview_page([s, TopicSummary("x", EVIL, error=EVIL)], tmp_path / "R", NOW)
    assert "<script" not in out and 'alert("x")' not in out


def test_overview_report_outside_the_folder_uses_a_relative_or_file_link(tmp_path):
    s = _summary(tmp_path / "elsewhere")
    out = html_render.overview_page([s], tmp_path / "R", NOW)
    for h in refs(out).hrefs:
        assert h.startswith("../") or h.startswith("file:") or h == "#ee-top", h


def test_write_overview(tmp_path):
    out = html_render.write_overview([_summary(tmp_path)], tmp_path / "R", NOW)
    assert out == {"overview": str(tmp_path / "R" / "overview.html")}
    text = (tmp_path / "R" / "overview.html").read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>") and refs(text).srcs == []
    assert not list((tmp_path / "R").glob("*.tmp"))


def test_filters_grid_track_cannot_be_widened_by_a_long_option():
    css = html_assets.CSS
    assert ".filters label{display:flex;flex-direction:column;gap:.3rem;min-width:0;max-width:100%;" in css
    assert ".filters select,.filters input[type=search]{width:100%;min-width:0;" in css


def test_local_links_are_percent_encoded(tmp_path):
    reports = tmp_path / "R" / "C#" / "a b?%"
    out = html_render.href(tmp_path / "R" / "C#" / "x" / "seo.html", reports)
    assert out == "../x/seo.html"
    assert html_render.href(reports / "d#e" / "f%g?.html", reports) == "d%23e/f%25g%3F.html"
    assert html_render.href(reports / "a b.html", reports) == "a%20b.html"


def test_write_removes_the_tmp_file_when_the_write_fails(tmp_path, monkeypatch):
    path = tmp_path / "p.html"

    def boom(src, dst):
        raise OSError("replace failed")
    monkeypatch.setattr(html_render.os, "replace", boom)
    with pytest.raises(OSError):
        html_render._write(path, "x")
    assert not list(tmp_path.glob("*.tmp")) and not path.exists()


# --- breadcrumbs and the report list (evidence-ui) ---------------------------------------

def _crumb_texts(text):
    nav = text.split('<nav class="ee-crumbs"', 1)[1].split("</nav>", 1)[0]
    return re.findall(r"<li[^>]*>(?:<a[^>]*>)?([^<]*)", nav), nav


def test_topic_page_crumbs_and_nav():
    out = html_render.topic_page(topic_model([topic_study(entry(1))]), "../overview.html", "r.html")
    texts, nav = _crumb_texts(out)
    assert texts == ["Evidence Echo", "SEO", "All studies"]
    assert nav.count("<a ") == 2 and nav.endswith('aria-current="page">All studies</li></ol>')
    assert ">Overview</a>" not in out and ">Latest report</a>" in out


def test_report_page_crumbs():
    out = html_render.report_page(report(label="2026-10-20 (2)"), "seo.html", "overview.html")
    assert _crumb_texts(out)[0] == ["Evidence Echo", "SEO", "Report 2026-10-20 (2)"]
    assert 'aria-current="page">Report 2026-10-20 (2)' in out
    assert _crumb_texts(html_render.report_page(report(), "t.html", "o.html"))[0][-1] == \
        "Report 2026-10-16"


def test_overview_has_no_crumbs(tmp_path):
    from datetime import datetime, timezone
    out = html_render.overview_page([], tmp_path, datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert '<nav class="ee-crumbs"' not in out


def test_reports_list_is_closed_and_newest_first(tmp_path):
    from evidence_echo.render.model import ReportLink
    (tmp_path / "a.html").write_text("x")
    (tmp_path / "b.html").write_text("x")
    t = topic_model([topic_study(entry(1))], reports=[ReportLink("2026-10-20", str(tmp_path / "b.html")),
                                                    ReportLink("2026-10-19", str(tmp_path / "a.html"))])
    out = html_render.topic_page(t, "o.html", "r.html", "seo.html", tmp_path)
    assert '<details class="ee-reports"><summary>Reports (2)</summary>' in out
    assert '<details class="ee-reports" open' not in out
    assert out.index("2026-10-20</a>") < out.index("2026-10-19</a>")
    assert 'href="b.html"' in out


def test_no_reports_no_list():
    assert '<details class="ee-reports"' not in html_render.topic_page(topic_model([topic_study(entry(1))]), "o", "r")


# --- two-column cards (evidence-ui) --------------------------------------------------------

def _side(out):
    return out.split('<div class="ee-card-side">', 1)[1]


def test_card_has_two_columns():
    out = html_render.card(FULL)
    main = out.split('<div class="ee-card-main">', 1)[1].split("</div>", 1)[0]
    assert "<h3>" in main and f"<p>{FULL.summary}</p>" in main and "tier" not in main
    rate = out.split('<div class="ee-card-rate">', 1)[1].split("</div>", 1)[0]
    assert 'class="label"' in rate and 'class="label"' not in main + _side(out)


def test_card_facts_are_named_lines():
    e = entry(1, view=view(flags=["no_baseline"], metric="clicks", baseline="prior month"))
    out = html_render.card(e)
    for line in ('<p class="ee-f"><b>Red flags:</b> <span class="ee-warn">no baseline</span></p>',
                 '<p class="ee-f"><b>Measured:</b> clicks</p>',
                 '<p class="ee-f"><b>Baseline:</b> prior month</p>'):
        assert line in out
    assert 'class="grading"' not in out


def test_minimal_card_has_no_empty_fields():
    e = entry(1, summary="", published=None, research_timeframe=None, primary_url=None, coverage=[])
    out = html_render.card(e)
    assert "Published" not in out and "Coverage" not in out and "Primary source" not in out
    assert "<b>Research:</b> unstated" in out
    assert not re.search(r"<b>[^<]*:</b>\s*</p>", out)
    assert "<p></p>" not in out


def test_full_card_keeps_every_fact():
    e = entry(2, disputed=True, dispute_reason="grades two tiers apart", skeptic_notes=["n"],
              series="S", editions=["2026"], subtopic={"key": "k", "name": "Sub", "question": "Q?",
              "label": "contested", "independent": 2, "echo": "corroborated by 1 other"},
              view=view(flags=["no_baseline"], metric="m", baseline="b",
                        primary={"status": "unreachable"}),
              coverage=[Link("A", "https://a.example/1"), Link("B", "https://b.example/2")])
    out = html_render.card(e, "2026-10-18 · in 1 report")
    names = [html.unescape(n) for n in re.findall(r"<b>([^<]*?):</b>", _side(out))]
    assert names == ["Disputed", "Skeptic's doubts", "Red flags", "Measured", "Baseline", "Original",
                     "Subtopic", "Question", "Part of", "Research", "Published", "Primary source",
                     "Coverage", "Reported"]
    assert "Q? · ⇅ contested (2 independent studies) · corroborated by 1 other" in out


def test_reported_single_date():
    out = html_render.topic_page(topic_model([topic_study(
        entry(1), first="2026-10-18", last="2026-10-18", reported_on=["2026-10-18"])]), "o", "r")
    assert "<b>Reported:</b> 2026-10-18 · in 1 report" in out


def test_reported_range():
    out = html_render.topic_page(topic_model([topic_study(
        entry(1), first="2026-10-18", last="2026-10-20",
        reported_on=["2026-10-18", "2026-10-19", "2026-10-20"])]), "o", "r")
    assert "first 2026-10-18 · last 2026-10-20 · in 3 reports" in out


def test_report_page_cards_have_no_reported_line():
    assert "Reported" not in html_render.report_page(report(), "t.html", "o.html")


def test_page_width_and_grid_css():
    css = html_assets.CSS
    assert "--page:78rem" in css and "minmax(0,var(--measure)) minmax(16rem,1fr)" in css
    assert ".ee-card-rate{grid-column:2;grid-row:1;" in css   # the grade: a small block above the facts
    assert "max-width:58rem" in css
    assert "max-width:44rem" in css


def test_report_list_links_the_html_written_after_the_model(tmp_path):
    from evidence_echo.render.model import ReportLink
    (tmp_path / "2026-10-20-report.md").write_text("x")
    (tmp_path / "2026-10-20-report.html").write_text("x")       # the run's own page, written later
    (tmp_path / "2026-10-19-report.md").write_text("x")         # an older run without HTML
    t = topic_model([topic_study(entry(1))],
                    reports=[ReportLink("2026-10-20", str(tmp_path / "2026-10-20-report.md")),
                             ReportLink("2026-10-19", str(tmp_path / "2026-10-19-report.md"))])
    out = html_render.topic_page(t, "o.html", "r.html", "seo.html", tmp_path)
    assert 'href="2026-10-20-report.html">2026-10-20</a>' in out
    assert 'href="2026-10-19-report.md">2026-10-19</a>' in out


def test_overview_last_run_is_local(tmp_path, monkeypatch):
    from datetime import timedelta, timezone
    from evidence_echo.render import clock
    monkeypatch.setattr(clock, "ZONE", timezone(timedelta(hours=2)))
    s = _summary(tmp_path)
    s.report["rendered_at"] = "2026-10-16T22:30:00+00:00"
    assert "last run 2026-10-17" in html_render.overview_page([s], tmp_path / "R", NOW)
    from evidence_echo.core import overview
    assert "last run 2026-10-17" in overview.render([s], tmp_path / "R", NOW)


# --- ratings issue (html-redesign) ---------------------------------------------------------

def test_every_page_has_the_masthead(tmp_path):
    from datetime import datetime, timezone
    rep = html_render.report_page(report(), "seo.html", "overview.html")
    top = html_render.topic_page(topic_model([topic_study(entry(1))]), "overview.html", "r.html")
    over = html_render.overview_page([], tmp_path, datetime(2026, 10, 20, tzinfo=timezone.utc))
    for out in (rep, top, over):
        assert out.index('<header class="ee-mast" id="ee-top">') < out.index("<main>")
    for out in (rep, top):   # the crumbs ride in the bar
        mast = out.split('<header class="ee-mast" id="ee-top">', 1)[1].split("</header>", 1)[0]
        assert '<nav class="ee-crumbs"' in mast
    assert '<span class="ee-brand">Evidence Echo</span>' in over
    assert '<a href="seo.html">All studies</a></nav>' in rep


def test_original_cell_words():
    def cell(primary, url="https://x.example/p"):
        return html_render.original_cell(entry(1, view=view(primary=primary), primary_url=url))
    assert cell({"status": "unreachable"}) == ("not reachable", True)
    assert cell({"status": "not_checked"}) == ("not checked", False)
    assert cell({"status": "agreed", "verdict": "overstated", "missing": []}) == ("overstated", True)
    assert cell({"status": "agreed", "verdict": None, "missing": ["9%"]}) == ("numbers not found", True)
    assert cell({"status": "agreed", "verdict": "faithful", "missing": []}) == ("faithful", False)
    assert cell(None) == ("—", False)
    assert cell(None, url=None) == ("no primary source", False)


def test_ratings_chart_rows_link_their_write_ups():
    out = html_render.report_page(report(new=[entry(1, view=view(flags=["no_baseline"]))],
                                         disputed=[DISPUTED]), "seo.html", "o.html")
    chart = out.split('<table class="ee-chart">', 1)[1].split("</table>", 1)[0]
    assert chart.count("<tr>") == 3   # header + two studies
    assert '<a href="#s1">' in chart and '<a href="#s3">' in chart
    assert '<span class="ee-warn">no baseline</span>' in chart
    assert '<span class="ee-mark" aria-hidden="true">≠</span>C</span>' in chart
    assert out.index('class="ee-chart"') < out.index("<h2>New studies</h2>")
    assert "1 new study · 1 disputed" in out
    assert 'class="ee-chart"' not in html_render.report_page(report(new=[]), "s", "o")


def test_html_escapes_chart_titles():
    out = html_render.ratings_chart([entry(1, title="<b>x</b>")])
    assert "<b>x</b>" not in out and "&lt;b&gt;x&lt;/b&gt;" in out


def test_chart_shows_the_skeptic_grade_and_printed_marks():
    near = entry(1, view=view(gap_note="skeptic: B", primary={"status": "unreachable"}))
    chart = html_render.ratings_chart([near])
    assert '<td data-name="Skeptic"><span class="tier tier-b">' in chart
    assert '<span class="ee-mark" aria-hidden="true">∅</span>not reachable' in chart
    assert 'class="meta ee-key"' in chart
    card = html_render.card(near)
    assert '<b>Original:</b> <span class="ee-warn"><span class="ee-mark" aria-hidden="true">∅</span>' in card


def test_study_rows_lead_with_the_grade():
    from tests.test_render_html_subtopics import other
    out = html_render.single_item(other(3), {}, unsorted=False)
    item = out.split("<li>", 1)[1]
    assert item.startswith('<span class="ee-row"><span class="tier tier-')
    assert out.index('class="ee-single-q') < out.index("<li>")          # the question sits above its study
    assert item.index('class="ee-row-line"') < item.index('<span class="ee-date">[')


def test_filters_are_closed_with_a_hidden_reset_and_cards_are_zebra():
    out = html_render.topic_page(topic_model([topic_study(entry(1)), topic_study(entry(2))]), "o", "r")
    assert '<details class="ee-filter-box" id="ee-filter-box" hidden><summary>Filter and sort' in out
    assert '<button type="button" id="ee-reset" hidden>Reset filters</button></form></details>' in out
    css, js = html_assets.CSS, html_assets.JS
    assert "#ee-studies.ee-js>article.ee-alt{background:var(--zebra)}" in css
    assert "section>article.card:nth-of-type(even)" in css
    assert ".filters .ee-on select" in css and "classList.toggle('ee-on'" in js
    assert "form.reset(); apply();" in js and "reset.hidden = n === 0" in js


def test_report_list_shows_each_reports_study_count(tmp_path):
    from evidence_echo.render.model import ReportLink
    (tmp_path / "b.html").write_text("x")
    (tmp_path / "a.html").write_text("x")
    t = topic_model([topic_study(entry(1))], reports=[ReportLink("2026-10-20", str(tmp_path / "b.html"), 3),
                                                    ReportLink("2026-10-19", str(tmp_path / "a.html"), 1)])
    out = html_render.topic_page(t, "o", "r", reports_dir=tmp_path)
    assert '<a href="b.html">2026-10-20</a> <span class="meta">3 studies</span>' in out
    assert '<a href="a.html">2026-10-19</a> <span class="meta">1 study</span>' in out


def test_every_page_ends_with_the_footer(tmp_path):
    from datetime import datetime, timezone
    when = datetime(2026, 10, 21, 8, 5, tzinfo=timezone.utc)
    out = html_render.page("T", "<p>x</p>", now=when)
    assert out.index("</main>") < out.index('<footer class="ee-foot">') < out.index("</body>")
    assert '<a href="#ee-top">Back to top' in out and 'id="ee-top"' in out
    assert f"Generated {html_render.clock.local_minute(when)}" in out
    for page_html in (html_render.report_page(report(), "seo.html", "o.html"),
                      html_render.topic_page(topic_model([topic_study(entry(1))]), "o", "r"),
                      html_render.overview_page([], tmp_path, when)):
        assert page_html.count('<footer class="ee-foot">') == 1
