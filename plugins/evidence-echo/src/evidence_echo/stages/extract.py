"""Extract: full article text for triage-passed items, through each item's source kind.

Items that already carry text (full scan depth, LinkedIn, session) are only advanced.
Blocked or junk pages wait for the session fallback; other failures degrade to the excerpt.
"""
import trafilatura

from evidence_echo.core import store
from evidence_echo.sources import base, get_kind

MAX_TEXT_CHARS = base.MAX_TEXT_CHARS


def text_from_html(html: str, url: str = "") -> str:
    text = trafilatura.extract(html, url=url, include_links=True) or ""
    return text[:MAX_TEXT_CHARS]


def run(conn, ctx=None) -> dict:
    extracted = degraded = waiting = 0
    for it in store.items_ready(conn, "triaged"):
        if it["full_text"]:
            store.set_item_fields(conn, it["id"], state="extracted")
            extracted += 1
            continue
        ctx = ctx or base.FetchContext(cutoff=None, client=base.make_client(),
                                       limiter=base.RateLimiter())
        kind = get_kind(it["source_type"])
        try:
            got = kind.full_text(it["url"], ctx)
        except Exception as exc:  # one odd URL never stops extract for the other items
            got = base.FullText(str(exc), "error")
        if got.status == "ok":
            store.set_item_fields(conn, it["id"], full_text=got.text, text_source="page",
                                  state="extracted")
            extracted += 1
        elif got.status in ("blocked", "short"):
            store.set_item_fields(conn, it["id"], fetch_status="needs_session")
            waiting += 1
        else:
            store.set_item_fields(conn, it["id"], full_text=it["excerpt"], text_source="excerpt",
                                  state="extracted")
            extracted += 1
            degraded += 1
    store.log_run(conn, "extract", f"extracted={extracted} degraded={degraded} needs_session={waiting}")
    return {"extracted": extracted, "degraded": degraded, "needs_session": waiting}
