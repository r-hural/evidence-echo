from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from evidence_echo.core.urls import canonical_url
from evidence_echo.sources import base, page

FIX = Path(__file__).parent / "fixtures"
INDEX = (FIX / "page_index.html").read_text(encoding="utf-8")
P2 = (FIX / "page_index_p2.html").read_text(encoding="utf-8")
SRC = {"type": "page", "name": "Research Blog", "url": "https://example.com/blog/"}


def _ctx(pages, cursors=None, backfill=None, days=3650):
    def handler(request):
        body = pages.get(str(request.url))
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)
    return base.FetchContext(
        cutoff=datetime.now(timezone.utc) - timedelta(days=days),
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        limiter=base.RateLimiter(interval=0), cursors=cursors or {}, backfill=backfill)


def test_article_links_filter():
    links = page.article_links(INDEX, "https://example.com/blog/", "https://example.com/blog/")
    assert [l.url for l in links] == [
        "https://example.com/blog/ab-test-title-tags-12k-pages/",
        "https://example.com/blog/internal-links-study-2026/?utm_source=home",
        "https://example.com/blog/schema-markup-experiment"]
    assert links[0].title == "A/B test: title tags on 12k pages"
    assert "12,000 pages" in links[0].excerpt
    assert links[0].published.date().isoformat() == "2026-10-10"
    assert links[2].published is None


def test_keep_link_rejects():
    idx = "https://example.com/blog/"
    for url in ["https://example.com/tag/seo/", "https://example.com/author/jane/",
                "https://example.com/blog/page/2/", "https://other-site.com/blog/x-y-z",
                "https://example.com/media/chart-2026.png", "https://example.com/files/r.pdf",
                "javascript:void(0)", "mailto:a@b.c", "https://example.com/", idx]:
        assert not page.keep_link(url, idx), url


def test_learn_pattern():
    assert page.learn_pattern(["https://e.com/blog/a-b-c", "https://e.com/blog/d-e-f",
                               "https://e.com/news/x-y-z"]) == "/blog/"
    assert page.learn_pattern(["https://e.com/a-b-c"]) is None


def test_next_page():
    assert page.next_page(INDEX, "https://example.com/blog/") == "https://example.com/blog/page/2/"
    assert page.next_page(P2, "https://example.com/blog/page/2/") is None


def test_list_new_first_run_learns_pattern():
    res = page.list_new([SRC], _ctx({"https://example.com/blog/": INDEX}))["Research Blog"]
    assert res.error is None and len(res.entries) == 3
    assert res.cursor == {"pattern": "/blog/"}


def test_list_new_blocked_index():
    blocked = base.FetchContext(
        cutoff=datetime.now(timezone.utc), limiter=base.RateLimiter(interval=0),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    res = page.list_new([SRC], blocked)["Research Blog"]
    assert res.blocked and res.error == "HTTP 403" and res.entries == []


def test_list_new_skips_known_links():
    ctx = _ctx({"https://example.com/blog/": INDEX})
    ctx.known = lambda canon: canon.endswith("schema-markup-experiment")
    res = page.list_new([SRC], ctx)["Research Blog"]
    assert len(res.entries) == 2


def test_backfill_follows_pagination():
    ctx = _ctx({"https://example.com/blog/": INDEX, "https://example.com/blog/page/2/": P2},
               backfill=base.Backfill(days=3650, max_items=200, per_source={"Research Blog": 200}))
    res = page.list_new([SRC], ctx)["Research Blog"]
    assert len(res.entries) == 5


def test_check_detects_article_links():
    out = page.check(SRC, _ctx({"https://example.com/blog/": INDEX}))
    assert out["status"] == "ok" and out["article_links"] == 3
    assert out["sample_links"][0] == "https://example.com/blog/ab-test-title-tags-12k-pages/"


def test_check_few_links_is_not_index():
    one = "<html><body><article><a href='/blog/only-one-post-here'>x</a></article></body></html>"
    out = page.check(SRC, _ctx({"https://example.com/blog/": one}))
    assert out["status"] == "few_links" and out["article_links"] == 1


def test_home_page_with_footer_menu_is_not_an_index():
    home = ("<html><body><div class='hero'><a href='/services/'>Services</a>"
            "<a href='/resources/'>Resources</a><a href='/case-studies/'>Cases</a>"
            "<a href='/privacy-policy/'>Privacy</a><a href='/2024/10/'>Archive</a></div></body></html>")
    out = page.check({"name": "Home", "url": "https://example.com/"}, _ctx({"https://example.com/": home}))
    assert out["article_links"] == 0 and out["status"] == "few_links"


def test_next_page_to_other_site_not_followed():
    index = INDEX.replace("/blog/page/2/", "https://evil.com/blog/page/2/")
    ctx = _ctx({"https://example.com/blog/": index},
               backfill=base.Backfill(days=3650, max_items=200, per_source={"Research Blog": 200}))
    res = page.list_new([SRC], ctx)["Research Blog"]
    assert res.error is None and len(res.entries) == 3


def _moved_ctx():
    def handler(request):
        if request.url.host == "old.example.org":
            return httpx.Response(301, headers={"Location": "https://example.com/blog/"})
        return httpx.Response(200, text=INDEX) if str(request.url) == "https://example.com/blog/" \
            else httpx.Response(404)
    return base.FetchContext(cutoff=datetime.now(timezone.utc) - timedelta(days=3650),
                             client=httpx.Client(transport=httpx.MockTransport(handler)),
                             limiter=base.RateLimiter(interval=0))


def test_links_filtered_by_redirect_target():
    moved = {"type": "page", "name": "Moved", "url": "https://old.example.org/news/"}
    res = page.list_new([moved], _moved_ctx())["Moved"]
    assert res.error is None and len(res.entries) == 3
    assert page.check(moved, _moved_ctx())["article_links"] == 3


def _index(slugs, nxt=None):
    arts = "".join(f'<article><h2><a href="/blog/{s}/">{s.replace("-", " ")}</a></h2>'
                   f'<p>Some text about {s}.</p></article>' for s in slugs)
    more = f'<a rel="next" href="{nxt}">Older posts</a>' if nxt else ""
    return f"<html><body><main>{arts}</main>{more}</body></html>"


B = "https://example.com/blog/"


def _gap_ctx(pages, known=(), first_run=False):
    ctx = _ctx(pages)
    known_canon = {canonical_url(B + s + "/") for s in known}
    ctx.known = lambda canon: canon in known_canon
    ctx.first_run = {SRC["name"]} if first_run else set()
    return ctx


def test_gapfill_pages_until_a_known_link():
    pages = {B: _index(["new-one-a", "new-one-b"], B + "page/2/"),
             B + "page/2/": _index(["new-two-a", "old-post-x"], B + "page/3/"),
             B + "page/3/": _index(["older-a-b"])}
    res = page.list_new([SRC], _gap_ctx(pages, known=["old-post-x"]))["Research Blog"]
    assert [e.url for e in res.entries] == [B + "new-one-a/", B + "new-one-b/", B + "new-two-a/"]
    assert res.possible_gap is False


def test_gapfill_stops_at_cap_and_flags_gap():
    pages = {B + ("" if n == 1 else f"page/{n}/"): _index([f"post-{n}-a", f"post-{n}-b"], B + f"page/{n + 1}/")
             for n in range(1, 9)}
    res = page.list_new([SRC], _gap_ctx(pages))["Research Blog"]
    assert len(res.entries) == 2 * page.GAPFILL_MAX_PAGES and res.possible_gap is True


def test_gapfill_without_next_link_no_gap():
    pages = {B: _index(["new-one-a", "new-one-b"])}
    res = page.list_new([SRC], _gap_ctx(pages))["Research Blog"]
    assert len(res.entries) == 2 and res.possible_gap is False


def test_gapfill_off_on_first_run():
    pages = {B: _index(["new-one-a", "new-one-b"], B + "page/2/"),
             B + "page/2/": _index(["new-two-a", "new-two-b"])}
    res = page.list_new([SRC], _gap_ctx(pages, first_run=True))["Research Blog"]
    assert len(res.entries) == 2


def test_gapfill_extra_page_failure_keeps_entries():
    pages = {B: _index(["new-one-a", "new-one-b"], B + "page/2/")}   # page 2 answers 404
    res = page.list_new([SRC], _gap_ctx(pages))["Research Blog"]
    assert res.error is None and len(res.entries) == 2


def test_gapfill_self_loop_no_duplicates():
    pages = {B: _index(["new-one-a", "new-one-b"], B)}   # "next" points back to the same page
    res = page.list_new([SRC], _gap_ctx(pages))["Research Blog"]
    assert [e.url for e in res.entries] == [B + "new-one-a/", B + "new-one-b/"]


def test_gapfill_cap_hit_on_last_page_no_gap():
    n = page.GAPFILL_MAX_PAGES
    pages = {B + ("" if i == 1 else f"page/{i}/"): _index([f"post-{i}-a", f"post-{i}-b"],
                                                          B + f"page/{i + 1}/" if i < n else None)
             for i in range(1, n + 1)}
    res = page.list_new([SRC], _gap_ctx(pages))["Research Blog"]
    assert len(res.entries) == 2 * n and res.possible_gap is False


def test_date_archive_under_the_index_is_not_an_article():
    idx = "https://otterly.ai/blog/"
    assert not page.keep_link("https://otterly.ai/blog/2026/10/19/", idx)
    assert not page.keep_link("https://otterly.ai/blog/2026/10/", idx)
    assert page.keep_link("https://otterly.ai/blog/2026/10/19/geo-experiment-results/", idx)


def test_hub_listing_pages_are_not_articles():
    idx = "https://www.conductor.com/academy/"
    assert not page.keep_link("https://www.conductor.com/academy/content-marketing-hub/", idx)
    assert page.keep_link("https://www.conductor.com/academy/aio-vs-organic-rankings/", idx)


CARDS = """<html><body><main>
<article><a href="/blog/first-study-post/"><img src="a.png" alt="Alt text one"></a>
  <h3><a href="/blog/first-study-post/">First study title</a></h3><p>Ann Writer 4 May 2023 Read More</p></article>
<article><a href="/blog/second-study-post/"><img src="b.png" alt="Second from alt"></a>
  <p>Ben Author Feb. 17, 2025 Read More</p></article>
<article><h3><a href="/blog/third-study-post/">Third study title</a></h3><p>Cleo Editor May 5, 2025</p></article>
<article><h3><a href="/blog/fourth-study-post/">Fourth</a></h3><p>No date here, 12 pages</p></article>
</main></body></html>"""


def test_first_non_empty_title_wins_and_alt_text_is_the_fallback():
    links = page.article_links(CARDS, "https://example.com/blog/", "https://example.com/blog/")
    assert [l.title for l in links] == ["First study title", "Second from alt", "Third study title", "Fourth"]


def test_plain_text_dates_on_cards_are_read():
    links = page.article_links(CARDS, "https://example.com/blog/", "https://example.com/blog/")
    assert [l.published.date().isoformat() if l.published else None for l in links] == [
        "2023-05-04", "2025-02-17", "2025-05-05", None]


BLOG = "https://example.com/blog/"


def _one_link(anchor: str) -> page.Link:
    html = f"<html><body><main><article>{anchor}</article></main></body></html>"
    [link] = page.article_links(html, BLOG, BLOG)
    return link


def test_link_title_prefers_heading_inside_link():
    """B13: a card link wraps heading, author and date; the heading is the title."""
    link = _one_link('<a href="/blog/running-faster-tests"><h3>Running faster tests</h3>'
                     '<span>Jane Roe</span><span>Wed Oct 21 2026</span></a>')
    assert link.title == "Running faster tests"


def test_link_title_drops_trailing_weekday_date():
    link = _one_link('<a href="/blog/intro-to-experiments">Intro to X Wed Oct 07 2026</a>')
    assert link.title == "Intro to X"


def test_link_title_without_heading_unchanged():
    link = _one_link('<a href="/blog/intro-to-experiments">Intro to X: 12% more sign-ups</a>')
    assert link.title == "Intro to X: 12% more sign-ups"
