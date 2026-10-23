"""F4 (security scan 2026-10-22): feed-supplied URLs and titles cannot inject Markdown or HTML
into the Markdown report."""
import pytest

from evidence_echo.render import markdown
from evidence_echo.render.model import Entry, Link, SeenEntry

BREAKOUT = "https://evil.example/a)<img/src=x/onerror=alert(1)>("
JS_LINK = "https://evil.example/a)[Full dataset](javascript:alert(1))("
VIEW = {"provenance": {"relation": "unknown", "conflict_of_interest": None}, "capped_from": None,
        "gap_note": None, "flags": [], "missing_numbers": [], "judged_from": "full_text",
        "metric": None, "baseline": None}


def _entry(**kw):
    base = dict(study_id=1, title="Title tag test", summary="Clicks rose 4%.", tier="B",
                disputed=False, skeptic_tiers=[], no_quote_only=False, dispute_reason=None,
                skeptic_notes=[], view=VIEW, series=None, editions=[], research_timeframe=None,
                published=None, primary_url=None, coverage=[])
    return Entry(**{**base, **kw})


def _seen(**kw):
    base = dict(study_id=2, first_title="Earlier study", first_reported="2026-10-01",
                coverage=[], disputed=False, skeptic_tiers=[], skeptic_notes=[])
    return SeenEntry(**{**base, **kw})


def _text(lines):
    return "\n".join(lines)


@pytest.mark.parametrize("url", [BREAKOUT, JS_LINK, "javascript:alert(1)", "https://e.com/a b",
                                 "https://e.com/<x>", "file:///etc/passwd"])
def test_unsafe_coverage_url_is_not_a_link(url):
    out = _text(markdown.entry_lines(_entry(coverage=[Link("Blog", url)])))
    assert "Coverage: Blog  " in out
    assert "<img" not in out and "](javascript" not in out and "](https://evil" not in out


def test_safe_coverage_url_keeps_its_link_with_brackets_encoded():
    out = markdown.entry_lines(_entry(coverage=[Link("Blog", "https://e.com/a_(b)[c]")]))
    assert "Coverage: [Blog](https://e.com/a_%28b%29%5Bc%5D)  " in out


def test_plain_coverage_url_is_unchanged():
    out = markdown.entry_lines(_entry(coverage=[Link("Blog", "https://example.com/g12?x=1&y=2")]))
    assert "Coverage: [Blog](https://example.com/g12?x=1&y=2)  " in out


@pytest.mark.parametrize("url", [BREAKOUT, "javascript:alert(1)"])
def test_seen_line_drops_unsafe_links(url):
    line = markdown.seen_line(_seen(coverage=[Link("Blog", url), Link("Fine", "https://ok.example/p")]))
    assert "<img" not in line and "javascript" not in line
    assert line.endswith(": Blog, [Fine](https://ok.example/p)")


@pytest.mark.parametrize("url", ["https://e.com/x>[a](javascript:alert(1))", "javascript:alert(1)"])
def test_unsafe_primary_url_is_not_shown(url):
    out = _text(markdown.entry_lines(_entry(primary_url=url)))
    assert "Primary source" not in out and "javascript" not in out


def test_safe_primary_url_is_an_autolink():
    assert "Primary source: <https://x.example.com/x>  " in markdown.entry_lines(
        _entry(primary_url="https://x.example.com/x"))


def test_feed_and_llm_text_carries_no_tags():
    tag = "<img src=x onerror=alert(1)>"
    entry = _entry(title=f"Study {tag}", summary=f"Summary {tag}", disputed=True,
                   skeptic_notes=[f"note {tag}"])
    seen = _seen(first_title=f"Old {tag}", disputed=True, skeptic_notes=[f"doubt {tag}"])
    out = _text(markdown.entry_lines(entry)) + "\n" + markdown.seen_line(seen)
    assert "<img" not in out
    assert out.count("&lt;img src=x onerror=alert(1)>") == 5  # titles, summary, both notes


def test_feed_and_llm_text_cannot_form_a_link():
    planted = r"Read [the data](javascript:alert(1)) or ![x](https://t.example/p.png) \[y](z)"
    entry = _entry(title=planted, summary=planted, disputed=True, skeptic_notes=[planted])
    seen = _seen(first_title=planted, disputed=True, skeptic_notes=[planted])
    out = _text(markdown.entry_lines(entry)) + "\n" + markdown.seen_line(seen)
    assert "](javascript" not in out.replace(r"\]", "") and "![x](" not in out
    assert out.count(r"Read \[the data\](javascript:alert(1)) or !\[x\](https://t.example/p.png)") == 5
