import pytest

from evidence_echo.render import html as html_render


def study(sid, name="Study name", finding="7% more traffic", tier="A", url=None):
    return {"id": sid, "canonical_name": name, "findings": finding, "research_timeframe": "2025",
            "evidence_tier": tier, "editions": [], "series_name": None, "primary_url": url}


def pair(check=("undecided", None)):
    return {"keep": 147, "other": 167, "rule": "shared key numbers: 7%, 8000",
            "check": {"verdict": check[0], "note": check[1]} if check else None,
            "keep_study": study(147), "other_study": study(167, name="Other study")}


PAGE = ('<!doctype html><html><body><header class="ee-mast" id="ee-top"><div class="ee-mast-in">'
        '<nav class="ee-crumbs"><ol><li><a href="../overview.html">Evidence Echo</a></li>'
        '<li><a href="seo.html">SEO Echo</a></li><li aria-current="page">Report</li></ol></nav>'
        '<nav class="ee-links" aria-label="Pages"><a href="seo.html">All studies</a>'
        '<a href="seo-sources.html">Sources</a></nav></div></header><main>'
        '<a href="#s1">Jump</a> <a href="https://example.com/a" rel="noopener noreferrer">Article</a> '
        '<a href="searches/x.html">Saved search</a> <a href="seo.html#s9">Earlier study</a> '
        '<a href="mailto:me@example.com">Mail</a></main>'
        '<footer class="ee-foot"><a class="ee-top-link" href="#ee-top">Back to top</a></footer></body></html>')


def hrefs(text):
    import re
    return re.findall(r'href="([^"]*)"', text)


def test_phone_copy_keeps_only_web_mail_and_in_page_links():
    out = html_render.phone_copy(PAGE, [])
    assert sorted(hrefs(out)) == ["#ee-top", "#s1", "https://example.com/a", "mailto:me@example.com"]
    for text in ("Evidence Echo", "SEO Echo", "Saved search", "Earlier study"):
        assert text in out
    assert 'class="ee-links"' not in out and "All studies" not in out


def test_phone_copy_without_pairs_adds_no_section():
    assert "ee-duplicates" not in html_render.phone_copy(PAGE, [])


def test_the_section_lands_before_the_footer():
    out = html_render.phone_copy(PAGE, [pair()])
    assert out.index('id="ee-duplicates"') < out.index("</main>") < out.index('<footer class="ee-foot"')
    assert "max-width" not in out[out.index('id="ee-duplicates"'):out.index("</main>")].split(">")[0]
    assert "Possible duplicates waiting for you" in out
    assert "To decide, ask Claude Code to review Evidence Echo duplicates." in out


@pytest.mark.parametrize("page", [PAGE.replace('<footer class="ee-foot">', "<footer>"),
                                  '<html><body><main><a href="x.html">X</a></main></body></html>',
                                  "<html><body><p>no main</p></body></html>"])
def test_the_section_lands_inside_the_page_whatever_its_layout(page):
    out = html_render.phone_copy(page, [pair()])
    assert out.index('id="ee-duplicates"') < out.index("</body>")
    assert all(h.startswith(("#", "http:", "https:", "mailto:")) for h in hrefs(out))


@pytest.mark.parametrize("check,words", [(("same", None), "Automatic check: same study"),
                                         (("different", "other sample"), "Automatic check: different studies — other sample"),
                                         (("undecided", None), "Automatic check: undecided"),
                                         (None, "Automatic check: not run")])
def test_a_pair_shows_both_studies_the_reason_and_the_check(check, words):
    out = html_render.duplicates_section([pair(check)])
    for text in ("study 147", "study 167", "Study name", "Other study", "7% more traffic",
                 "Flagged because: shared key numbers: 7%, 8000", words):
        assert text in out


def test_pair_text_is_escaped_and_unsafe_links_are_dropped():
    evil = '<script>alert(1)</script>'
    p = pair()
    p["keep_study"] = study(147, name=evil, finding=evil, url="javascript:alert(1)")
    out = html_render.duplicates_section([p])
    assert "<script>" not in out and "&lt;script&gt;" in out and "javascript:" not in out


def test_a_long_finding_is_cut():
    p = pair()
    p["keep_study"] = study(147, finding="x" * 1000)
    assert "x" * 401 not in html_render.duplicates_section([p])


def test_no_pairs_no_section():
    assert html_render.duplicates_section([]) == ""
