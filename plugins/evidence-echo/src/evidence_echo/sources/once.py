"""once kind: one document (a yearly report, a study page), analysed once."""
from datetime import datetime, timezone

import trafilatura
from lxml import html as lxml_html

from evidence_echo.sources import base

TYPE = "once"
FIELDS = {"name": base.required_text("name"), "url": base.http_url}
HAS_HEADLINES = False


def _title(html: str) -> str:
    try:
        t = lxml_html.fromstring(html).findtext(".//title")
    except (ValueError, lxml_html.etree.ParserError):
        return ""
    return " ".join((t or "").split())


def _read(url, ctx):
    """(status, text, title, published)"""
    try:
        got = base.http_get(url, ctx)
    except base.Blocked as exc:
        return "blocked", str(exc), "", None
    except base.FetchError as exc:
        return "error", str(exc), "", None
    text = (trafilatura.extract(got.text, url=got.url, include_links=True) or "")[:base.MAX_TEXT_CHARS]
    status = "short" if base.looks_like_junk(text) else "ok"
    return status, text, _title(got.text), base.find_date(got.text, got.url)


def check(source, ctx=None) -> dict:
    ctx = ctx or base.FetchContext(cutoff=datetime.now(timezone.utc), client=base.make_client(),
                                   limiter=base.RateLimiter())
    status, text, title, published = _read(source["url"], ctx)
    return {"name": source["name"], "type": TYPE, "url": source["url"], "status": status,
            "http_status": None, "entries": 1 if status == "ok" else 0,
            "latest_entry_date": published.isoformat() if published else None,
            "detail": None if status == "ok" else text[:200], "title": title}


def list_new(sources, ctx) -> dict:
    out = {}
    for source in sources:
        status, text, title, published = _read(source["url"], ctx)
        if status == "error":
            out[source["name"]] = base.SourceResult(error=text)
            continue
        entry = base.Entry(url=source["url"], title=title or source["name"],
                           excerpt=text[:800] if status == "ok" else "",
                           published=published, date_unknown=published is None,
                           text=text if status == "ok" else None)
        out[source["name"]] = base.SourceResult(entries=[entry], blocked=status != "ok")
    return out


def full_text(url, ctx) -> base.FullText:
    return base.article_text(url, ctx)
