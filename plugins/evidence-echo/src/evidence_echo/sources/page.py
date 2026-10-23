"""page kind: a blog or news index page; new article links on it become items."""
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import courlan
from lxml import html as lxml_html

from evidence_echo.core.urls import canonical_url
from evidence_echo.sources import base

TYPE = "page"
FIELDS = {"name": base.required_text("name"), "url": base.http_url}
HAS_HEADLINES = True
MIN_ARTICLE_LINKS = 3
MAX_PAGES = 20
GAPFILL_MAX_PAGES = 5   # S2: pages read on a normal run while every link is new
CAN_PAGE = True
EXCERPT_CHARS = 300
_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".mp3", ".zip",
             ".css", ".js", ".xml", ".pdf"}   # R8: PDF is a later kind
_NAV_PARTS = re.compile(r"/(tag|tags|category|categories|author|authors|page|search|login|"
                        r"signin|signup|register|feed|rss|wp-admin|cart|account)(/|$)", re.I)
_NAV_PARTS_MORE = re.compile(r"/(privacy|terms|legal|cookies?|about|contact|careers|jobs|imprint|"
                             r"impressum|sitemap|services|resources|pricing|products?)([-/]|$)", re.I)
_DATE_ARCHIVE = re.compile(r"/\d{4}(/\d{1,2})?(/\d{1,2})?/?$")   # a year/month/day archive at any depth
_HUB = re.compile(r"[-/]hub/?$", re.I)                              # a category listing ("seo-hub/")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                                       "oct", "nov", "dec"), 1)}
_TEXT_DATE = re.compile(r"\b(?:(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})"
                        r"|([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4}))\b")
_NEXT_TEXT = re.compile(r"(older( posts| entries)?|next( page)?|more posts|›|»|→)[›»→]?", re.I)


@dataclass
class Link:
    url: str
    title: str
    excerpt: str
    published: datetime | None
    title_from_text: bool = True   # False: the title came from an image's alt text


def _domain(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    return courlan.extract_domain(url) or host or None


def _slug_like(path: str) -> bool:
    parts = [p for p in path.split("/") if p]
    if not parts:
        return False
    seg = parts[-1]
    if len(parts) == 1:  # a top-level page: only a long slug or a numbered one reads as an article
        return seg.count("-") >= 2 or any(ch.isdigit() for ch in seg)
    return "-" in seg or any(ch.isdigit() for ch in seg)


def keep_link(url: str, index_url: str) -> bool:
    canon = canonical_url(url)
    if not canon:
        return False
    parts, index = urlsplit(canon), urlsplit(canonical_url(index_url) or index_url)
    if _domain(canon) != _domain(index_url):
        return False
    path = parts.path
    if os.path.splitext(path)[1].lower() in _SKIP_EXT or _NAV_PARTS.search(path):
        return False
    if index.path.startswith(path):  # the index itself or one of its ancestors
        return False
    if _NAV_PARTS_MORE.search(path) or _DATE_ARCHIVE.search(path) or _HUB.search(path):
        return False
    index_dir = index.path if index.path.endswith("/") else index.path.rsplit("/", 1)[0] + "/"
    if not path.startswith(index_dir):  # must be deeper than, or beside, the index page
        return False
    return _slug_like(path) and not courlan.is_not_crawlable(canon)


def _in_chrome(el) -> bool:
    return any(a.tag in ("nav", "header", "footer", "aside") for a in el.iterancestors())


def _container(el):
    for a in el.iterancestors():
        if a.tag in ("article", "li", "section") or (a.tag == "div" and len(a.text_content()) < 2000):
            return a
    return el.getparent()


def _text_date(text: str) -> datetime | None:
    """The first plain-text date on a card: "4 May 2023", "May 5, 2025", "Feb. 17, 2025"."""
    for m in _TEXT_DATE.finditer(text):
        day, month, year = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(5), m.group(4), m.group(6))
        number = _MONTHS.get(month[:3].lower())
        if number is None:
            continue
        try:
            return datetime(int(year), number, int(day), tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _time_in(container) -> datetime | None:
    for t in container.iter("time"):
        raw = t.get("datetime") or ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return _text_date(" ".join(container.text_content().split()))


def _link_title(a) -> str:
    """The link's text; for an image-only link, the image's alt text (or the link's own label)."""
    text = " ".join(a.text_content().split())
    if text:
        return text
    for img in a.iter("img"):
        if (img.get("alt") or "").strip():
            return " ".join(img.get("alt").split())
    return " ".join((a.get("aria-label") or a.get("title") or "").split())


def article_links(html: str, page_url: str, index_url: str) -> list[Link]:
    try:
        doc = lxml_html.fromstring(html)
    except (ValueError, lxml_html.etree.ParserError):
        return []
    out, seen = [], {}
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or _in_chrome(a):
            continue
        url = urljoin(page_url, href)
        canon = canonical_url(url)
        if not canon or not keep_link(url, index_url):
            continue
        text = " ".join(a.text_content().split())
        if canon in seen:   # the same article again: a text link beats an earlier image link
            first = out[seen[canon]]
            if text and first.title != text and not first.title_from_text:
                first.title, first.title_from_text = text, True
                first.excerpt = first.excerpt.replace(text, "", 1).strip()
            continue
        seen[canon] = len(out)
        box = _container(a)
        title = _link_title(a)
        context = " ".join(box.text_content().split()) if box is not None else ""
        excerpt = context.replace(title, "", 1).strip()[:EXCERPT_CHARS]
        out.append(Link(url, title, excerpt, _time_in(box) if box is not None else None,
                        title_from_text=bool(text)))
    return out


def learn_pattern(urls: list[str]) -> str | None:
    """The first path segment most article links share (≥ half of them), as '/seg/'."""
    firsts = [p[0] for p in (urlsplit(u).path.strip("/").split("/") for u in urls) if len(p) > 1]
    if not firsts:
        return None
    seg, count = Counter(firsts).most_common(1)[0]
    return f"/{seg}/" if count * 2 >= len(urls) else None


def next_page(html: str, page_url: str) -> str | None:
    try:
        doc = lxml_html.fromstring(html)
    except (ValueError, lxml_html.etree.ParserError):
        return None
    for el in doc.iter("link", "a"):
        if "next" in (el.get("rel") or "").lower().split() and el.get("href"):
            return urljoin(page_url, el.get("href"))
    for a in doc.iter("a"):
        if a.get("href") and _NEXT_TEXT.fullmatch((a.text_content() or "").strip()[:40]):
            return urljoin(page_url, a.get("href"))
    return None


def _apply_pattern(links: list[Link], pattern: str | None) -> list[Link]:
    if not pattern:
        return links
    matching = [l for l in links if urlsplit(l.url).path.startswith(pattern)]
    return matching or links  # site redesign: fall back, the pattern is relearned


def _entries(links: list[Link]) -> list[base.Entry]:
    return [base.Entry(url=l.url, title=l.title, excerpt=l.excerpt, published=l.published)
            for l in links]


def list_new(sources: list[dict], ctx: base.FetchContext) -> dict:
    out = {}
    for source in sources:
        cursor = dict(ctx.cursors.get(source["name"]) or {})
        limit = ctx.backfill.per_source.get(source["name"], 0) if ctx.backfill else 0
        gapfill = not ctx.backfill and source["name"] not in ctx.first_run
        max_pages = MAX_PAGES if limit else (GAPFILL_MAX_PAGES if gapfill else 1)
        url, pages, links, index, seen = source["url"], 0, [], None, set()
        all_new = False
        try:
            while url and pages < max_pages:
                try:
                    got = base.http_get(url, ctx)
                except (base.Blocked, base.FetchError):
                    if pages == 0:
                        raise
                    break  # a later page failed: keep what the earlier pages gave
                pages += 1
                index = index or got.url  # the index page or its redirect target
                found = [l for l in article_links(got.text, got.url, index)
                         if canonical_url(l.url) not in seen]
                seen.update(canonical_url(l.url) for l in found)
                fresh = [l for l in found if not ctx.known(canonical_url(l.url))]
                links += fresh
                all_new = bool(found) and len(fresh) == len(found)
                if limit and len(links) >= limit:
                    break
                url = next_page(got.text, got.url) if (limit or (gapfill and all_new)) else None
                if url and _domain(url) != _domain(index):
                    url = None  # never follow pagination to another site
        except base.Blocked as exc:
            out[source["name"]] = base.SourceResult(error=str(exc), blocked=True)
            continue
        except base.FetchError as exc:
            out[source["name"]] = base.SourceResult(error=str(exc))
            continue
        pattern = cursor.get("pattern") or learn_pattern([l.url for l in links])
        kept = _apply_pattern(links, pattern)
        if kept is links and cursor.get("pattern"):
            pattern = learn_pattern([l.url for l in links])
        if pattern:
            cursor["pattern"] = pattern
        gap = gapfill and all_new and pages >= GAPFILL_MAX_PAGES and bool(url)  # a next page was left unread
        out[source["name"]] = base.SourceResult(entries=_entries(kept[:limit] if limit else kept),
                                                cursor=cursor or None, possible_gap=gap)
    return out


def check(source, ctx=None) -> dict:
    ctx = ctx or base.FetchContext(cutoff=datetime.now(timezone.utc), client=base.make_client(),
                                   limiter=base.RateLimiter())
    out = {"name": source["name"], "type": TYPE, "url": source["url"], "status": "ok",
           "http_status": None, "entries": 0, "latest_entry_date": None, "detail": None,
           "article_links": 0, "sample_links": []}
    try:
        got = base.http_get(source["url"], ctx)
    except base.Blocked as exc:
        return {**out, "status": "blocked", "detail": str(exc)}
    except base.FetchError as exc:
        return {**out, "status": "error", "detail": str(exc)}
    links = article_links(got.text, got.url, got.url)  # filtered by the redirect target
    dates = [l.published for l in links if l.published]
    out.update(http_status=got.status, entries=len(links), article_links=len(links),
               sample_links=[l.url for l in links[:5]],
               latest_entry_date=max(dates).isoformat() if dates else None)
    if len(links) < MIN_ARTICLE_LINKS:
        out.update(status="few_links", detail=f"only {len(links)} article links found")
    return out


def full_text(url: str, ctx: base.FetchContext) -> base.FullText:
    return base.article_text(url, ctx)
