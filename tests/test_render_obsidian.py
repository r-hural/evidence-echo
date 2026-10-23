import json
import os
import re
from pathlib import Path

import pytest

from evidence_echo.render import obsidian
from evidence_echo.render.model import Link, SeriesSummary, SourceSummary
from tests.obsidian_fixtures import entry, study, topic, view


def test_fixtures_build_a_topic():
    t = topic([study(1), study(2, series="Weekly")])
    assert [s.entry.study_id for s in t.studies] == [1, 2]
    assert t.sources == [SourceSummary("Example Blog", [1, 2], 0, 0)]
    assert t.studies[1].entry.series == "Weekly" and t.studies[0].last_reported == "2026-08-15"


@pytest.mark.parametrize("name,slug", [
    ("Title-tag rewrites on 12k pages: +4.2% organic clicks",
     "title-tag-rewrites-on-12k-pages-4-2-organic-clicks"),
    ("  --Hello__World--  ", "hello-world"),
    ("Рост CTR на 12%", "рост-ctr-на-12"),
    ("!!!", "study"), ("", "study"), (None, "study"),
])
def test_study_slug(name, slug):
    assert obsidian.study_slug(name) == slug


def test_study_slug_is_at_most_60_chars_and_never_ends_with_a_dash():
    slug = obsidian.study_slug("word " * 30)
    assert len(slug) <= 60 and not slug.endswith("-") and slug.startswith("word-word-")


@pytest.mark.parametrize("name,cleaned", [
    ('a\\b/c:d*e?f"g<h>i|j#k^l[m]n', "a-b-c-d-e-f-g-h-i-j-k-l-m-n"),
    ("../../evil", "-..-evil"),
    ("..", "source"),
    ("  Search Engine Land  ", "Search Engine Land"),
    ("tab\there", "tab-here"),
    ("CON", "CON-"), ("aux.log", "aux-.log"), ("Console", "Console"),
    ("a`b%c$d(e)", "a-b-c-d(e)"), ("x%%y``z", "x--y--z"),
    ("", "source"), (None, "source"),
])
def test_clean_name(name, cleaned):
    assert obsidian.clean_name(name, "source") == cleaned


def test_clean_name_is_at_most_80_chars():
    assert obsidian.clean_name("x" * 100 + ".", "s") == "x" * 80


def test_unique_names_number_collisions_in_name_order_ignoring_case():
    assert obsidian.unique_names(["a:b", "A-B", "a/b", "Other"], "source") == {
        "A-B": "A-B", "Other": "Other", "a/b": "a-b-2", "a:b": "a-b-3"}


def test_unique_names_skip_a_suffix_taken_by_a_real_name():
    assert obsidian.unique_names(["a-b", "a-b-2", "a/b"], "source") == {
        "a-b": "a-b", "a-b-2": "a-b-2", "a/b": "a-b-3"}


def test_topic_folder_is_the_cleaned_name_or_the_id():
    assert obsidian.topic_folder(topic(name="SEO: tests / 2026"), {}) == "SEO- tests - 2026"
    assert obsidian.topic_folder(topic(name=".."), {}) == "seo"


def test_topic_folder_of_another_topic_gets_the_id():
    t = topic(name="seo", topic_id="seo2")
    assert obsidian.topic_folder(t, {"seo": ["SEO/_index.md"]}) == "seo (seo2)"
    assert obsidian.topic_folder(t, {"seo2": ["seo/_index.md"]}) == "seo"


def test_quote_is_a_json_string_with_yaml_unsafe_characters_escaped():
    assert obsidian.quote('a: "b" #c\nd') == '"a: \\"b\\" #c\\nd"'
    assert obsidian.quote("x yz﻿") == '"x\\u2028y\\u0085z\\ufeff"'
    assert obsidian.quote(None) == '""'
    assert obsidian.quote("Рост") == '"Рост"'


def test_frontmatter_lines():
    assert obsidian.frontmatter([("tier", "B"), ("red_flags", ["no_baseline", "cherry_pick"]),
                                 ("series", None), ("editions", [])]) == [
        "---", 'tier: "B"', 'red_flags: ["no_baseline", "cherry_pick"]', 'series: ""',
        "editions: []", "---"]


_RAW = re.compile("[\x00-\x1f\x7f-\x9f  ﻿]")


def _frontmatter(text: str) -> dict:
    """Strict line-based check: `key: <JSON string>` or `key: [<JSON strings>]`, nothing else."""
    lines = text.split("\n")
    assert lines[0] == "---"
    end = lines.index("---", 1)
    out = {}
    for line in lines[1:end]:
        m = re.fullmatch(r"([a-z_]+): (.+)", line)
        assert m, f"not a frontmatter line: {line!r}"
        raw = m[2]
        assert raw[0] in '"[' and not _RAW.search(raw), raw
        value = json.loads(raw)
        assert isinstance(value, str) or (
            isinstance(value, list) and all(isinstance(v, str) for v in value)), raw
        out[m[1]] = value
    return out


def _awkward():
    v = view(flags=["no_baseline"], provenance={
        "conducted_by": 'Acme: "SEO" #1\nteam', "relation": "vendor", "conflict_of_interest": None})
    s = study(3, title='Clicks: +4% #win "really"\nsecond line', series='Series: #2 "x"',
              editions=["2025", "2026"], view=v, research_timeframe="Q1: 2026 late")
    return topic([s], name='SEO: "lab" #1',
                 series=[SeriesSummary('Series: #2 "x"', [("2026", 3)])])


def test_study_note_frontmatter_round_trips_awkward_values():
    notes = obsidian.render_notes(_awkward(), "SEO")
    fm = _frontmatter(notes["SEO/studies/clicks-4-win-really-second-line-s3.md"])
    assert fm == {
        "title": 'Clicks: +4% #win "really"\nsecond line', "tier": "B", "topic": 'SEO: "lab" #1',
        "relation": "vendor", "conducted_by": 'Acme: "SEO" #1\nteam', "red_flags": ["no_baseline"],
        "disputed": "false", "series": 'Series: #2 "x"', "editions": ["2025", "2026"],
        "first_reported": "2026-08-15", "last_reported": "2026-08-15",
        "research_timeframe": "Q1: 2026 late",
        "sources": ["[[Evidence Echo/SEO/sources/Example Blog|Example Blog]]"],
        "evidence_echo_id": "s3"}


def test_frontmatter_parses_as_yaml_when_pyyaml_is_available():
    yaml = pytest.importorskip("yaml")  # not installed in .venv today: skipped
    for text in obsidian.render_notes(_awkward(), "SEO").values():
        if text.startswith("---\n"):
            assert yaml.safe_load(text.split("---\n")[1]) == _frontmatter(text)


def test_study_note_body():
    v = view(flags=["no_baseline"], metric="organic clicks")
    s = study(1, title="First", series="Weekly", view=v, reported_on=["2026-08-15", "2026-09-01"],
              last="2026-09-01")
    notes = obsidian.render_notes(topic([s], series=[SeriesSummary("Weekly", [("1", 1)])]), "SEO")
    body = notes["SEO/studies/first-s1.md"].split("---\n", 2)[2].splitlines()
    assert body == [
        "", obsidian.GENERATED, "", "# First", "", "Grade: B  ", "",
        "A split test on 12k pages.", "",
        "Red flags: no baseline  ", "Measured: organic clicks  ", "",
        "Series: [[Evidence Echo/SEO/series/Weekly|Weekly]]  ",
        "*Research: 2026-Q2*  ", "*Published: 2026-08-10*  ",
        "Primary source: <https://example.com/study>  ",
        "Coverage: [[Evidence Echo/SEO/sources/Example Blog|Example Blog]]: <https://example.com/post-1>  ",
        "Reported in: 2026-08-15, 2026-09-01"]


def test_links_between_study_series_and_source_notes():
    t = topic([study(1, title="First", series="Weekly", editions=["1"]),
               study(2, title="Second", series="Weekly", editions=["2"])],
              series=[SeriesSummary("Weekly", [("1", 1), ("2", 2)])])
    notes = obsidian.render_notes(t, "SEO")
    assert list(notes) == ["SEO/_index.md", "SEO/studies/first-s1.md", "SEO/studies/second-s2.md",
                           "SEO/series/Weekly.md", "SEO/sources/Example Blog.md"]
    series = notes["SEO/series/Weekly.md"].splitlines()
    assert series.index("- [[Evidence Echo/SEO/studies/first-s1|1]]") < series.index(
        "- [[Evidence Echo/SEO/studies/second-s2|2]]")
    source = notes["SEO/sources/Example Blog.md"].splitlines()
    assert "- B · 2026-08-15 · [[Evidence Echo/SEO/studies/first-s1|First]]" in source
    assert "Disputed items: 0 · Items with red flags: 0" in source
    for text in notes.values():  # every link opens a note of this export
        for target in re.findall(r"\[\[Evidence Echo/([^|\]]+)\|", text):
            assert target + ".md" in notes, target


def test_links_use_the_full_vault_path_per_topic():
    seo = obsidian.render_notes(topic([study(1)], name="SEO", topic_id="seo"), "SEO")
    diet = obsidian.render_notes(topic([study(1)], name="Diet", topic_id="diet"), "Diet")
    assert "[[Evidence Echo/SEO/sources/Example Blog|" in seo["SEO/studies/study-1-4-2-clicks-s1.md"]
    assert "[[Evidence Echo/Diet/sources/Example Blog|" in diet["Diet/studies/study-1-4-2-clicks-s1.md"]
    assert "Evidence Echo/SEO/" not in "".join(diet.values())


def test_source_note_counts_and_unknown_studies():
    t = topic([study(1, title="One", tier=None)],
              sources=[SourceSummary("Example Blog", [1, 99], 2, 1)])
    source = obsidian.render_notes(t, "SEO")["SEO/sources/Example Blog.md"].splitlines()
    assert "- ? · 2026-08-15 · [[Evidence Echo/SEO/studies/one-s1|One]]" in source
    assert not any("s99" in line for line in source)
    assert "Disputed items: 2 · Items with red flags: 1" in source


def test_index_groups_by_tier_and_has_a_dataview_table():
    t = topic([study(1, title="Alpha", tier="A"), study(2, title="Unrated", tier=None),
               study(3, title="Dee", tier="D")])
    index = obsidian.render_notes(t, "SEO")["SEO/_index.md"].splitlines()
    assert index.index("## Tier A") < index.index("## Tier D") < index.index("## Ungraded")
    assert "## Tier B" not in index
    assert "- [[Evidence Echo/SEO/studies/unrated-s2|Unrated]]" in index
    assert index[-3:] == ["```dataview",
                          'TABLE tier, relation, red_flags FROM "Evidence Echo/SEO/studies" SORT tier',
                          "```"]


def test_index_of_an_empty_topic():
    notes = obsidian.render_notes(topic([]), "SEO")
    assert list(notes) == ["SEO/_index.md"]
    assert "*No studies reported yet.*" in notes["SEO/_index.md"].splitlines()


def test_grading_lines_match_the_markdown_report():
    from evidence_echo.render import markdown
    from evidence_echo.render.model import Report
    v = view(flags=["no_baseline", "cherry_pick"],
             provenance={"conducted_by": "Acme", "relation": "vendor",
                         "conflict_of_interest": "sells the tool"},
             missing_numbers=["9%"], judged_from="excerpt", metric="organic clicks",
             baseline="prior 28 days")
    md = markdown.report_markdown(Report("seo", "SEO", "2026-10-16", [entry(1, view=v)], [], [], [])
                                  ).splitlines()
    lines = obsidian.grading_lines(v)
    assert len(lines) == 6 and all(line in md for line in lines)
    assert obsidian.grading_lines(view(baseline="prior 28 days")) == ["Baseline: prior 28 days  "]
    assert obsidian.grading_lines(view()) == []


def test_disputed_study_shows_both_grades_and_the_reason():
    v = view(provenance={"conducted_by": None, "relation": "independent", "conflict_of_interest": None},
             capped_from="A")
    s = study(1, title="T", disputed=True, skeptic_tiers=["D"], skeptic_notes=["small sample"],
              dispute_reason="grades two tiers apart", view=v)
    note = obsidian.render_notes(topic([s]), "SEO")["SEO/studies/t-s1.md"]
    lines = note.splitlines()
    assert "Grade: B / skeptic: D · independent · capped from A  " in lines
    assert "> Disputed: grades two tiers apart. Skeptic's doubts: small sample" in lines
    assert _frontmatter(note)["disputed"] == "true"


def test_no_quote_dispute_keeps_a_single_grade_and_gap_note_shows_when_undisputed():
    s = study(1, title="T", disputed=True, no_quote_only=True,
              dispute_reason="no quoted number found in the text", view=view(gap_note="judge: A"))
    lines = obsidian.render_notes(topic([s]), "SEO")["SEO/studies/t-s1.md"].splitlines()
    assert "Grade: B  " in lines
    assert "> Disputed: no quoted number found in the text. Skeptic's doubts: no reason given" in lines
    calm = study(2, title="U", view=view(gap_note="judge: A"))
    assert "Grade: B · judge: A  " in obsidian.render_notes(topic([calm]), "SEO")[
        "SEO/studies/u-s2.md"].splitlines()


def test_only_web_urls_become_links():
    s = study(1, title="T", primary_url="javascript:alert(1)",
              coverage=[Link("Evil", "file:///etc/passwd"), Link("Spaced", "https://x.test/a b"),
                        Link("Fine", "https://x.test/ok")])
    note = obsidian.render_notes(topic([s]), "SEO")["SEO/studies/t-s1.md"]
    assert "javascript:" not in note and "file:" not in note and "x.test/a b" not in note
    assert "Primary source" not in note
    lines = note.splitlines()
    assert "Coverage: [[Evidence Echo/SEO/sources/Evil|Evil]]  " in lines
    assert "Coverage: [[Evidence Echo/SEO/sources/Fine|Fine]]: <https://x.test/ok>  " in lines


def test_link_breaking_characters_in_titles_are_replaced_in_aliases():
    index = obsidian.render_notes(topic([study(1, title="A [b] | c")]), "SEO")["SEO/_index.md"]
    assert "- [[Evidence Echo/SEO/studies/a-b-c-s1|A (b) / c]]" in index.splitlines()


def test_study_slug_comes_from_canonical_name_and_survives_a_title_change():
    first = obsidian.render_notes(topic([study(7, title="Old title", canonical_name="SEO title test")]), "SEO")
    assert "SEO/studies/seo-title-test-s7.md" in first
    second = obsidian.render_notes(
        topic([study(7, title="New result-first title", canonical_name="SEO title test")]), "SEO")
    assert list(second) == list(first)
    assert "# New result-first title" in second["SEO/studies/seo-title-test-s7.md"].splitlines()


def test_study_slug_falls_back_to_the_title_without_canonical_name():
    notes = obsidian.render_notes(topic([study(7, title="Only a title", canonical_name="")]), "SEO")
    assert "SEO/studies/only-a-title-s7.md" in notes


_HOSTILE = "---\n# H\n[[x]]\n```dataviewjs\nalert(1)\n```\n~~~\n> q\n`$= dv.x`"
_NASTY = "\n```dataviewjs\nx\n[[y]]\n# h"


def _assert_inert(note: str, allowed_headings=1):
    lines = note.split("\n")
    body = lines[lines.index("---", 1) + 1:] if lines[0] == "---" else lines
    while body and not body[-1]:
        body = body[:-1]
    if body[-3:-2] == ["```dataview"]:  # the index's own query block
        body = body[:-3] + [""]
    assert not [x for x in body if x.startswith(("```", "~~~")) and x != "```dataview"], body
    assert len([x for x in body if x.startswith("#")]) <= allowed_headings, body
    assert not [x for x in body if x.startswith(">") and not x.startswith("> Disputed")], body
    text = "\n".join(body)
    assert not re.search(r"(?<!\\)\[\[(?!Evidence Echo/)", text)
    assert not re.search(r"(?<!\\)`", text)


def test_hostile_summary_is_inert():
    note = obsidian.render_notes(topic([study(1, title="T", summary=_HOSTILE)]), "SEO")["SEO/studies/t-s1.md"]
    _assert_inert(note)


def test_hostile_title_conflict_and_dispute_reason_are_inert():
    v = view(provenance={"conducted_by": None, "relation": "vendor",
                         "conflict_of_interest": "sells" + _NASTY},
             flags=["no_baseline"], missing_numbers=["9%" + _NASTY], metric="m" + _NASTY,
             baseline="b" + _NASTY, gap_note="g" + _NASTY, capped_from="A" + _NASTY)
    s = study(1, title="T" + _NASTY, disputed=True, skeptic_tiers=["D"], dispute_reason="r" + _NASTY,
              skeptic_notes=["n" + _NASTY], view=v, research_timeframe="q" + _NASTY)
    notes = obsidian.render_notes(topic([s], name="N" + _NASTY), "SEO")
    for text in notes.values():
        _assert_inert(text, allowed_headings=4)  # the renderer's own headings: title, tiers, lists
        assert "# h" not in text.split("\n")
    assert "\n```dataviewjs" not in "".join(notes.values())


def test_md_escapes_backslashes_and_block_starts():
    bs = chr(92)
    assert obsidian._md("a" + bs + "`b") == "a" + bs * 3 + "`b"
    assert obsidian._md("1. x\n- y\n  # z\n___", multiline=True) == (
        "1" + bs + ". x\n" + bs + "- y\n" + bs + "# z\n" + bs + "___")


_VECTORS = ["<!-- x", '<iframe src="https://e">', "T %%", "a %%% b", "![](https://t/p.png)",
            "[c](obsidian://x)", "[x]: https://e", "tier:: A", "[relation:: vendor]", "___",
            "2024. results", "10) results", "<script>x</script>"]


def test_md_neutralises_html_comments_links_fields_and_rules():
    bs = chr(92)
    for vector in _VECTORS:
        for multiline in (False, True):
            out = obsidian._md(vector, multiline=multiline)
            assert "<" not in out and "%%" not in out, out
            assert not re.search("(?<!" + bs * 2 + ")[" + bs + "[" + bs + "]]", out), out
            assert "::" not in out and not out.startswith(("_", "[")), out
    assert obsidian._md("2024. results") == "2024" + bs + ". results"
    assert obsidian._md("10) x") == "10" + bs + ") x"
    assert obsidian._md("a %%% b") == "a %" + bs + "%" + bs + "% b"
    assert obsidian._md("tier:: A") == "tier:" + bs + ": A"
    assert obsidian._md("<b>") == "&lt;b>"


def test_hostile_vectors_in_summary_and_title_keep_the_rest_of_the_note():
    for vector in _VECTORS:
        s = study(1, title="T " + vector, summary="ok\n" + vector + "\nafter", series="# h <b>",
                  view=view(flags=["no_baseline"]))
        notes = obsidian.render_notes(topic([s]), "SEO")  # the series is not exported: unlinked label
        body = next(v for k, v in notes.items() if "/studies/" in k).split("---\n", 2)[2]
        plain = re.sub("<https?://[^<>]+>", "", body)  # the renderer's own autolinks
        assert "<" not in plain and "%%" not in body, (vector, body)
        assert "Red flags: no baseline" in body and "Grade: B" in body


def test_alias_cannot_swallow_the_closing_brackets_and_unlinked_alias_is_escaped():
    bs = chr(92)
    names = obsidian._Names(base="Evidence Echo/SEO", studies={1: "a-s1"}, series={}, sources={})
    assert names.link("studies", 1, "x" + bs) == "[[Evidence Echo/SEO/studies/a-s1|x]]"
    assert "<" not in names.link("studies", 1, "a <b> %%") and "%%" not in names.link("studies", 1, "%%")
    assert names.link("series", "none", "# h <b>") == bs + "# h &lt;b>"


def test_aliases_cannot_form_dataview_inline_fields():
    s = study(1, title="Wk ed [tier:: D]", series="Weekly", editions=["1"],
              coverage=[Link("Src [relation:: vendor]", "https://x.test/ok")])
    t = topic([s], series=[SeriesSummary("Weekly", [("1", 1)])],
              sources=[SourceSummary("Src [relation:: vendor]", [1], 0, 0)])
    for text in obsidian.render_notes(t, "SEO").values():
        body = text.split("---\n", 2)[-1]
        assert "::" not in body, body
        assert not re.search(r"[(\[][^\])]*::", body)


def test_hostile_urls_are_percent_encoded():
    url = "https://e/x(tier::A)y%%z[[Secret]]|p"
    s = study(1, title="T", primary_url=url, coverage=[Link("Fine", url)])
    note = obsidian.render_notes(topic([s]), "SEO")["SEO/studies/t-s1.md"]
    assert "Primary source: <https://e/x%28tier%3A:A%29y%25%25z%5B%5BSecret%5D%5D%7Cp>  " in note.splitlines()
    for bad in ("::", "%%", "[[Secret", "(tier"):
        assert bad not in note.split("---\n", 2)[2].replace("[[Evidence Echo/", ""), bad


def test_a_gfm_table_delimiter_row_is_escaped():
    out = obsidian._md("a | b\n:-- | :--\nc | d", multiline=True).split("\n")
    assert out[1] == chr(92) + ":-- | :--" and out[0] == "a | b"


_WEB_PROBES = ["https://e/%(x", "https://e/%[x", "https://e/%|", "https://e/%::", "https://e/%%(",
               "https://e/%%%", "https://e/%(a)::b[[c]]|d%%e", "https://e/a:::b%"]


def test_web_never_forms_a_comment_field_or_link():
    for probe in _WEB_PROBES:
        out = obsidian._web(probe)
        assert out and out.startswith("https://e/"), probe
        for bad in ("%%", "::", "[[", "[", "]", "(", ")", "|"):
            assert bad not in out, (probe, out)
    assert obsidian._web("https://e/%(x") == "https://e/%25%28x"
    assert obsidian._web("https://e/%::") == "https://e/%25%3A:"
    kept = "https://e/%28tier%3A%3AA%29?q=%7c"
    assert obsidian._web(kept) == kept


def test_md_breaks_every_colon_pair():
    bs = chr(92)
    assert obsidian._md("a:::b") == "a:" + bs + ":" + bs + ":b"
    assert "::" not in obsidian._md("x ::::: y")


_BT = chr(96)
_EVIL = "\n".join([_HOSTILE, _NASTY, *_VECTORS, "a:::b", "(k:: v) [k:: v]", _BT + "$= dv.x" + _BT,
                   "%(x %[x %| %:: %%( %%%", chr(92), "[[Evil|x]] ![[img.png]] <a href=x>", "%"])
_EVIL_URL = "https://e/%(x%[x%|%::%%(%%%(a)::b[[c]]|d%%e<"[:-1]


def _hostile_topic():
    v = view(flags=[_EVIL], provenance={"conducted_by": _EVIL, "relation": _EVIL,
                                        "conflict_of_interest": _EVIL},
             missing_numbers=[_EVIL], judged_from="excerpt", metric=_EVIL, baseline=_EVIL,
             gap_note=_EVIL, capped_from=_EVIL)
    names = [_EVIL, "Src " + _BT + "x" + _BT + " %%y (k:: v)", "%%", _BT * 3]
    s1 = study(1, title=_EVIL, summary=_EVIL, tier=_EVIL, disputed=True, skeptic_tiers=[_EVIL],
               dispute_reason=_EVIL, skeptic_notes=[_EVIL], view=v, series=_EVIL, editions=[_EVIL],
               research_timeframe=_EVIL, published=_EVIL, primary_url=_EVIL_URL,
               coverage=[Link(n, u) for n, u in zip(names, [_EVIL_URL, _EVIL_URL + _BT, _EVIL, None])],
               canonical_name=_EVIL, first=_EVIL, last=_EVIL, reported_on=[_EVIL, _BT + "%%::"])
    s2 = study(2, title=_EVIL, summary=_EVIL, tier=_BT, series=names[1], editions=[_EVIL],
               coverage=[Link(names[2], _EVIL_URL)], first=_BT + "%%" + _BT, view=v)
    series = [SeriesSummary(_EVIL, [(_EVIL, 1), (_EVIL, 2)]), SeriesSummary(names[1], [(_EVIL, 2)])]
    sources = [SourceSummary(n, [1, 2], 1, 1) for n in names]
    return topic([s1, s2], sources=sources, series=series, name=_EVIL, topic_id="seo")


def test_a_fully_hostile_topic_renders_inert_notes():
    t = _hostile_topic()
    folder = obsidian.topic_folder(t, {})
    notes = obsidian.render_notes(t, folder)
    assert len(notes) == 1 + 2 + 2 + 4
    link = re.compile(r"\[\[(Evidence Echo/[^|\]]*)\|[^\]]*\]\]")
    for path, text in notes.items():
        targets = link.findall(text)
        assert targets, path
        for target in targets:
            for bad in (_BT, "%%", "::", "[", "]", "|", "<"):
                assert bad not in target, (path, target)
            assert target.split("/", 1)[1] + ".md" in notes, target
        fm = _frontmatter(text) if text.startswith("---") else {}  # data, parsed separately
        body = text.split("---\n", 2)[2] if fm else text
        assert body.count("```") == (2 if path.endswith("_index.md") else 0), path
        plain = link.sub("", body)
        assert "%%" not in plain and "::" not in plain, (path, plain)
        assert "[[" not in plain, (path, plain)
        assert not re.search("(?<!" + chr(92) * 2 + ")" + _BT, plain.replace("```dataview", "")
                             .replace("```", "")), (path, plain)
        assert "<" not in re.sub(r"<https://[^<>\s]+>", "", plain), (path, plain)
        assert "%%" not in body and "::" not in body, path


def _vault(tmp_path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def _files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_export_writes_the_layout_and_manifest(tmp_path):
    vault = _vault(tmp_path)
    t = topic([study(1, title="First", series="Weekly")], series=[SeriesSummary("Weekly", [("1", 1)])])
    out = obsidian.export(t, vault)
    root = vault / "Evidence Echo"
    assert set(out) == {"dir", "written", "removed"}
    assert Path(out["dir"]).samefile(root / "SEO") and out["written"] == 4 and out["removed"] == 0
    assert _files(root) == [".evidence-echo-export.json", "SEO/_index.md", "SEO/series/Weekly.md",
                            "SEO/sources/Example Blog.md", "SEO/studies/first-s1.md"]
    assert json.loads((root / obsidian.MANIFEST).read_text(encoding="utf-8")) == {
        "version": 1, "files": {"seo": ["SEO/_index.md", "SEO/series/Weekly.md",
                                        "SEO/sources/Example Blog.md", "SEO/studies/first-s1.md"]}}
    assert not list(vault.rglob("*.tmp"))


def test_second_export_removes_a_merged_away_study_and_keeps_user_files(tmp_path):
    vault = _vault(tmp_path)
    obsidian.export(topic([study(1, title="Keep", series="Weekly"), study(2, title="Merged")],
                          series=[SeriesSummary("Weekly", [("1", 1)])]), vault)
    folder = vault / "Evidence Echo" / "SEO"
    (folder / "studies" / "my own notes.md").write_text("mine", encoding="utf-8")
    (folder / "scratch.md").write_text("mine too", encoding="utf-8")
    out = obsidian.export(topic([study(1, title="Keep")]), vault)  # study 2 merged into 1; no series
    assert out["removed"] == 2  # studies/merged-s2.md and series/Weekly.md
    assert sorted(p.name for p in (folder / "studies").iterdir()) == ["keep-s1.md", "my own notes.md"]
    assert (folder / "scratch.md").read_text(encoding="utf-8") == "mine too"
    assert not (folder / "series").exists()  # emptied folder removed


def test_nothing_is_written_outside_the_export_folder(tmp_path):
    vault = _vault(tmp_path)
    before = set(_files(tmp_path))
    s = study(1, title="../../evil", series="../../evil",
              coverage=[Link("../../evil", "https://x.test/1")])
    obsidian.export(topic([s], name="../../evil", series=[SeriesSummary("../../evil", [("1", 1)])]),
                    vault)
    new = set(_files(tmp_path)) - before
    assert new and all(p.startswith("vault/Evidence Echo/") for p in new)
    assert {"vault/Evidence Echo/-..-evil/sources/-..-evil.md",
            "vault/Evidence Echo/-..-evil/series/-..-evil.md",
            "vault/Evidence Echo/-..-evil/studies/evil-s1.md"} <= new


def test_manifest_entries_outside_the_folder_are_never_deleted(tmp_path):
    vault = _vault(tmp_path)
    victim, near = tmp_path / "victim.md", vault / "outside.md"
    victim.write_text("keep", encoding="utf-8")
    near.write_text("keep", encoding="utf-8")
    root = vault / "Evidence Echo"
    root.mkdir()
    (root / obsidian.MANIFEST).write_text(json.dumps({"version": 1, "files": {"seo": [
        "../outside.md", "../../victim.md", str(victim), "SEO/studies/../../../outside.md"]}}),
        encoding="utf-8")
    out = obsidian.export(topic([study(1)]), vault)
    assert out["removed"] == 0 and victim.exists() and near.exists()


@pytest.mark.parametrize("bad", [
    "not json", '["SEO/studies/two-s2.md"]',
    '{"version": 2, "files": {"seo": ["SEO/studies/two-s2.md"]}}',
    '{"version": 1, "files": {"seo": "SEO/studies/two-s2.md"}}', None])
def test_missing_or_corrupt_manifest_deletes_nothing(tmp_path, bad):
    vault = _vault(tmp_path)
    obsidian.export(topic([study(1, title="One"), study(2, title="Two")]), vault)
    manifest = vault / "Evidence Echo" / obsidian.MANIFEST
    if bad is None:
        manifest.unlink()
    else:
        manifest.write_text(bad, encoding="utf-8")
    out = obsidian.export(topic([study(1, title="One")]), vault)
    assert out["removed"] == 0
    assert (vault / "Evidence Echo" / "SEO" / "studies" / "two-s2.md").exists()
    assert json.loads(manifest.read_text(encoding="utf-8"))["files"]["seo"] == [
        "SEO/_index.md", "SEO/sources/Example Blog.md", "SEO/studies/one-s1.md"]


def test_topic_rename_moves_the_notes(tmp_path):
    vault = _vault(tmp_path)
    obsidian.export(topic([study(1)], name="SEO"), vault)
    out = obsidian.export(topic([study(1)], name="Search"), vault)
    root = vault / "Evidence Echo"
    assert out["removed"] == 3 and not (root / "SEO").exists()
    assert (root / "Search" / "studies" / "study-1-4-2-clicks-s1.md").exists()


def test_case_only_rename_keeps_the_note(tmp_path):
    vault = _vault(tmp_path)
    obsidian.export(topic([study(1, coverage=[Link("Foo Blog", "https://x.test/1")])]), vault)
    obsidian.export(topic([study(1, coverage=[Link("foo blog", "https://x.test/1")])]), vault)
    [note] = list((vault / "Evidence Echo" / "SEO" / "sources").iterdir())
    assert note.name.casefold() == "foo blog.md"
    assert "# foo blog" in note.read_text(encoding="utf-8").splitlines()


def test_failed_write_leaves_no_tmp_and_keeps_the_manifest(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    obsidian.export(topic([study(1)]), vault)
    manifest = vault / "Evidence Echo" / obsidian.MANIFEST
    before = manifest.read_text(encoding="utf-8")

    def locked(src, dst):
        raise PermissionError(f"{dst} is open in another program")

    monkeypatch.setattr(obsidian.os, "replace", locked)
    with pytest.raises(PermissionError):
        obsidian.export(topic([study(1, title="Changed"), study(2)]), vault)
    assert manifest.read_text(encoding="utf-8") == before
    assert not list(vault.rglob("*.tmp"))


def test_unchanged_notes_are_not_rewritten(tmp_path):
    vault = _vault(tmp_path)
    t = topic([study(1)])
    obsidian.export(t, vault)
    note = vault / "Evidence Echo" / "SEO" / "_index.md"
    os.utime(note, ns=(1_000_000_000, 1_000_000_000))
    obsidian.export(t, vault)
    assert note.stat().st_mtime_ns == 1_000_000_000


def test_missing_vault_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not create vaults"):
        obsidian.export(topic([]), tmp_path / "no-vault")
    assert not (tmp_path / "no-vault").exists()


def test_two_topics_share_the_manifest(tmp_path):
    vault = _vault(tmp_path)
    obsidian.export(topic([study(1)], name="SEO", topic_id="seo"), vault)
    out = obsidian.export(topic([study(1)], name="seo", topic_id="seo2"), vault)
    root = vault / "Evidence Echo"
    assert Path(out["dir"]).name == "seo (seo2)"
    assert set(json.loads((root / obsidian.MANIFEST).read_text(encoding="utf-8"))["files"]) == {
        "seo", "seo2"}
    again = obsidian.export(topic([study(1)], name="SEO", topic_id="seo"), vault)
    assert again["removed"] == 0 and Path(again["dir"]).name == "SEO"
    assert (root / "seo (seo2)" / "_index.md").exists()


def test_unreadable_manifest_aborts_the_export_and_changes_nothing(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    obsidian.export(topic([study(1, title="One"), study(2, title="Two")]), vault)
    manifest = vault / "Evidence Echo" / obsidian.MANIFEST
    before = manifest.read_bytes()
    real = Path.read_text

    def locked(self, *args, **kwargs):
        if self.name == obsidian.MANIFEST:
            raise PermissionError("locked by sync")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(PermissionError):
        obsidian.export(topic([study(1, title="One")]), vault)
    monkeypatch.undo()
    assert manifest.read_bytes() == before
    assert (vault / "Evidence Echo" / "SEO" / "studies" / "two-s2.md").exists()


def test_manifest_entries_with_a_colon_are_never_followed(tmp_path):
    vault = _vault(tmp_path)
    obsidian.export(topic([study(1)]), vault)
    root = vault / "Evidence Echo"
    user = root / "SEO" / "user.md"
    user.write_text("mine", encoding="utf-8")
    manifest = root / obsidian.MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"]["seo"].append("SEO/user.md:x.md")
    manifest.write_text(json.dumps(data), encoding="utf-8")
    seen = []
    real = obsidian._inside
    obsidian._inside = lambda r, rel: (seen.append(rel), real(r, rel))[1]
    try:
        out = obsidian.export(topic([study(1)]), vault)
    finally:
        obsidian._inside = real
    assert "SEO/user.md:x.md" not in seen
    assert out["removed"] == 0 and user.exists()


def test_dollar_and_hash_are_escaped_in_text_and_aliases():
    bs = chr(92)
    assert obsidian._md("$5k-$10k") == bs + "$5k-" + bs + "$10k"
    assert obsidian._md("see #seo and # alone") == "see " + bs + "#seo and # alone"
    assert obsidian._md("#tag") == bs + "#tag"
    assert obsidian._alias("$5k #seo # x") == bs + "$5k " + bs + "#seo # x"
    assert obsidian._md("a" + bs + "$") == "a" + bs * 2 + bs + "$"
