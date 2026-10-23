"""rss kind: RSS/Atom feeds via feedparser, plus feed discovery for a web page."""
import io
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
from dateutil import parser as dateutil_parser

from evidence_echo.sources import base

TYPE = "rss"
FIELDS = {"name": base.required_text("name"), "url": base.http_url}
HAS_HEADLINES = True


def _parse_date(entry):
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        s = getattr(entry, attr, None)
        if s:
            try:
                dt = dateutil_parser.parse(s)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _default_ctx() -> base.FetchContext:
    return base.FetchContext(cutoff=datetime.now(timezone.utc), client=base.make_client(),
                             limiter=base.RateLimiter())


def _read(url: str, ctx: base.FetchContext):
    """Fetch through base.http_get (public hosts only on every hop, size cap, rate limit),
    then parse the bytes: feedparser never opens a URL itself. Raises Blocked / FetchError.
    The body goes in as a stream: given str or bytes, feedparser treats a body that looks like
    a URL or a file path as one and opens it."""
    got = base.http_get(url, ctx)
    feed = feedparser.parse(io.BytesIO(got.content or got.text.encode("utf-8")))
    feed["status"], feed["href"] = got.status, got.url
    return feed


_HTTP_ERROR = re.compile(r"HTTP (\d{3})")


def check(source: dict, ctx=None) -> dict:
    """Probe one RSS source the way list_new reads it."""
    out = {"name": source["name"], "type": "rss", "url": source["url"], "status": "ok",
           "http_status": None, "entries": 0, "latest_entry_date": None, "detail": None}
    try:
        feed = _read(source["url"], ctx or _default_ctx())
    except base.Blocked as exc:
        return {**out, "status": "blocked", "detail": str(exc)}
    except base.FetchError as exc:
        status = _HTTP_ERROR.fullmatch(str(exc))
        if status:
            return {**out, "status": "http_error", "http_status": int(status[1])}
        return {**out, "status": "error", "detail": str(exc)}
    except Exception as exc:
        return {**out, "status": "error", "detail": str(exc)}
    out["http_status"] = feed.get("status")
    out["entries"] = len(feed.entries)
    problem = str(feed.get("bozo_exception", "")) or None
    dates = [d for d in map(_parse_date, feed.entries) if d]
    if dates:
        out["latest_entry_date"] = max(dates).isoformat()
    if feed.entries:  # a bozo feed that still yields entries reads fine (e.g. leading BOM)
        href = feed.get("href")
        return {**out, "detail": f"redirected to {href}" if href and href != source["url"]
                else None}
    if not feed.get("version"):
        return {**out, "status": "not_feed", "detail": problem or "no RSS/Atom feed found"}
    return {**out, "status": "empty"}


def list_new(sources: list[dict], ctx: base.FetchContext) -> dict:
    out = {}
    for source in sources:
        try:
            feed = _read(source["url"], ctx)
        except base.Blocked as exc:
            out[source["name"]] = base.SourceResult(error=str(exc), blocked=True)
            continue
        except base.FetchError as exc:  # HTTP errors, refused hosts, oversized responses
            out[source["name"]] = base.SourceResult(error=str(exc))
            continue
        except Exception as exc:  # feedparser raises only on programming errors; keep the run alive
            out[source["name"]] = base.SourceResult(error=str(exc))
            continue
        if feed.bozo and not feed.entries:
            out[source["name"]] = base.SourceResult(
                error=str(getattr(feed, "bozo_exception", "parse error")))
            continue
        entries = []
        for e in feed.entries:
            url = e.get("link", "") or e.get("id", "")
            if url:
                entries.append(base.Entry(url=url, title=e.get("title", "").strip(),
                                          excerpt=e.get("summary", "")[:800].strip(),
                                          published=_parse_date(e)))
        out[source["name"]] = base.SourceResult(entries=entries)
    return out


def full_text(url: str, ctx: base.FetchContext) -> base.FullText:
    return base.article_text(url, ctx)


_ALTERNATE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_FEED_TYPES = ("application/rss+xml", "application/atom+xml")


def advertised_feed(html: str, page_url: str) -> str | None:
    for tag in _ALTERNATE.findall(html):
        attrs = dict(re.findall(r'(\w+)\s*=\s*["\']([^"\']*)["\']', tag))
        if attrs.get("rel", "").lower() == "alternate" and \
                attrs.get("type", "").lower() in _FEED_TYPES and attrs.get("href"):
            resolved = urljoin(page_url, attrs["href"])
            # never hand a file:/ftp: href to feedparser (it would read local files)
            if urlparse(resolved).scheme in ("http", "https"):
                return resolved
    return None


def discover_feed(url: str, ctx=None) -> dict:
    """A feed URL is kept; for a web page, use the feed it advertises (rel=alternate).
    The page goes through base.http_get, and an advertised feed on a non-public host is
    never used (it would be saved and fetched on every run)."""
    ctx = ctx or _default_ctx()
    result = check({"name": url, "url": url}, ctx)
    if result["status"] == "ok":
        return {"url": url, "discovered_from": None, "check": result}
    try:
        page = base.http_get(url, ctx)
        found = advertised_feed(page.text, page.url)
        if found:
            base.check_public_host(found)
    except Exception:
        found = None
    if found:
        return {"url": found, "discovered_from": url,
                "check": check({"name": found, "url": found}, ctx)}
    return {"url": url, "discovered_from": None, "check": result}
