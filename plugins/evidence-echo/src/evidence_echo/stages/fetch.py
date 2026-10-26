"""Fetch: ask each source kind for new entries; apply horizon, dating, baseline; store items."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from evidence_echo.core import store
from evidence_echo.core.urls import canonical_url
from evidence_echo.sources import base, get_kind, linkedin
from evidence_echo.sources import cache as fetch_cache
from evidence_echo.stages import schedule

FIRST_RUN_UNDATED = 20   # R1
MAX_DATE_LOOKUPS = 20    # R6
MIN_GAP_ENTRIES = 5      # a feed this small is rarely cut off: all-new is just news
UNKNOWN_PREFIX = "[date unknown] "
DEGRADED_REASON = "text unavailable (blocked, not fetched in session)"


def effective_scan_depth(source: dict, cfg) -> str:
    # cfg.scan_depth is resolved like every setting: env > topic override > config.json
    return source.get("scan_depth") or cfg.scan_depth or "headlines"


def degrade_unresolved(conn) -> int:
    """Items the session did not fetch since the last run fall back to their excerpt."""
    count = 0
    for it in store.flagged_items(conn):
        excerpt = it["excerpt"] or ""
        if not excerpt.strip() or excerpt == UNKNOWN_PREFIX:
            store.set_item_fields(conn, it["id"], fetch_status=None, state="rejected_triage",
                                  triage_reason=DEGRADED_REASON)
        else:
            state = "extracted" if it["state"] == "triaged" else it["state"]
            store.set_item_fields(conn, it["id"], fetch_status=None, full_text=excerpt,
                                  text_source="excerpt", state=state)
        count += 1
    return count


def _load_full_texts(conn, kind, ctx, item_ids: list[int]) -> None:
    for item_id in item_ids:
        item = store.get_item(conn, item_id)
        if item["full_text"]:
            continue
        got = kind.full_text(item["url"], ctx)
        if got.status == "ok":
            store.set_item_fields(conn, item_id, full_text=got.text, text_source="page")
        elif got.status in ("blocked", "short"):
            store.set_item_fields(conn, item_id, fetch_status="needs_session")
        # "error": triage reads the excerpt; extract tries again after triage


def context_for(cfg, backfill=None, fresh: bool = False) -> base.FetchContext:
    days = backfill.days if backfill else cfg.fetch_horizon_days
    return base.FetchContext(cutoff=datetime.now(timezone.utc) - timedelta(days=days),
                             client=base.make_client(), limiter=base.RateLimiter(),
                             apify_token=cfg.apify_api_token,
                             apify_actor=cfg.topic.linkedin_apify_actor, backfill=backfill,
                             cache=fetch_cache.for_config(cfg, fresh))


def _date_from_page(url: str, ctx) -> datetime | None:
    try:
        page = base.http_get(url, ctx)
    except (base.Blocked, base.FetchError):
        return None
    return base.find_date(page.text, page.url)


def backfill_plan(sources: list[dict], max_items: int) -> dict:
    names = [s["name"] for s in sources if s["type"] != "once"]
    if not names:
        return {}
    share, extra = divmod(max_items, len(names))
    plan = {n: share + (1 if i < extra else 0) for i, n in enumerate(names)}
    return {n: k for n, k in plan.items() if k > 0}  # --max below the source count: first sources only


def is_first_run(state) -> bool:
    """No successful fetch yet (a failed first attempt still counts as the first run)."""
    return (state is None or state["last_checked"] is None
            or (state["last_error"] is not None and state["items_total"] == 0
                and state["last_new_item"] is None))


def recent_dates(conn, source_name: str) -> list[datetime]:
    rows = conn.execute(
        """SELECT published FROM items WHERE source_name=? AND published IS NOT NULL
           AND date_unknown=0 ORDER BY published DESC LIMIT ?""",
        (source_name, schedule.SAMPLE_SIZE * 4)).fetchall()  # text order only narrows the rows
    out = []
    for (raw,) in rows:
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue  # one unreadable stored date must not stop the fetch
        out.append((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc))
    return sorted(out, reverse=True)[:schedule.SAMPLE_SIZE]  # as dates: offsets differ in the text


def _not_due(conn, source, state, now, horizon_days: int) -> dict | None:
    """The skipped-source entry when adaptive fetch says wait; None when the source is due."""
    if source["type"] == "once":
        return None  # once keeps its own rule (done or not)
    days, origin, capped = schedule.interval(
        source, recent_dates(conn, source["name"]),
        schedule.interval_cap(source["type"], horizon_days))
    if schedule.is_due(state, days, now):
        return None
    out = {"type": source["type"], "skipped": "not due", "interval_days": round(days, 1),
           "interval_source": origin, "next_due": schedule.next_due(state, days).isoformat()}
    if capped:
        out["capped"] = True
    return out


def _possible_gap(kind, source, result, ctx) -> bool:
    if result.possible_gap:
        return True
    if (getattr(kind, "CAN_PAGE", False) or source["type"] == "once" or result.error
            or ctx.backfill or source["name"] in ctx.first_run):
        return False
    if len(result.entries) < (linkedin.MAX_POSTS if source["type"] == "linkedin" else MIN_GAP_ENTRIES):
        return False
    canons = [c for c in (canonical_url(e.url) for e in result.entries) if c]
    return bool(canons) and not any(ctx.known(c) for c in canons)


def store_entries(conn, source, kind, result, ctx, first_run: bool,
                  date_lookups: bool = True) -> dict:
    counts = {"type": source["type"], "found": len(result.entries), "new": 0, "seen": 0,
              "error": result.error, "blocked": result.blocked}
    if result.error:
        store.record_fetch(conn, source["name"], source["type"], new=0, error=result.error,
                           blocked=result.blocked)
        return counts
    fetched_at = datetime.now(timezone.utc).isoformat()
    lookups = undated_kept = 0
    new_ids = []
    backfill = ctx.backfill
    cap = backfill.per_source.get(source["name"], 0) if backfill else 0
    for entry in result.entries:
        if cap and counts["new"] >= cap:
            break
        canon = canonical_url(entry.url)
        if not canon:
            continue
        known = store.find_by_canonical(conn, canon)
        if known:
            if backfill and known["state"] == "seen" and (
                    known["published"] is None
                    or datetime.fromisoformat(known["published"]) >= ctx.cutoff):
                store.revive_seen(conn, known["id"])  # an old entry the normal run only noted
                counts["new"] += 1
                new_ids.append(known["id"])
            continue
        published = entry.published
        if (date_lookups and published is None and not entry.date_unknown
                and lookups < MAX_DATE_LOOKUPS):
            lookups += 1
            published = _date_from_page(entry.url, ctx)
        if backfill and (published is None or published < ctx.cutoff):
            continue  # a research spike stores only what it can place inside its window
        undated = published is None
        if undated:
            undated_kept += 1
            state = "seen" if first_run and undated_kept > FIRST_RUN_UNDATED else "fetched"
        else:
            state = "seen" if published < ctx.cutoff else "fetched"
        excerpt = (UNKNOWN_PREFIX if undated else "") + entry.excerpt
        item_id = store.insert_item(conn, {
            "url": entry.url, "canonical_url": canon, "source_name": source["name"],
            "source_type": source["type"], "title": entry.title, "excerpt": excerpt,
            "published": published.isoformat() if published else None,
            "fetched_at": fetched_at, "state": state, "date_unknown": int(undated),
            "full_text": entry.text, "text_source": "feed" if entry.text else None})
        if item_id is not None:
            counts["new" if state == "fetched" else "seen"] += 1
            if state == "fetched":
                new_ids.append(item_id)
    store.record_fetch(conn, source["name"], source["type"], new=counts["new"], error=None,
                       cursor=result.cursor)
    counts["_new_ids"] = new_ids
    return counts


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _store_once(conn, source, result, ctx) -> dict:
    # The horizon does not apply to `once`: a yearly report is wanted whatever its date.
    counts = {"type": "once", "found": len(result.entries), "new": 0, "seen": 0,
              "error": result.error, "blocked": result.blocked, "unchanged": False}
    if result.error or not result.entries:
        store.record_fetch(conn, source["name"], "once", new=0, error=result.error)
        return counts
    entry = result.entries[0]
    canon = canonical_url(entry.url)
    versions = [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE source_name=? AND source_type='once' ORDER BY id", (source["name"],))]
    digest = content_hash(entry.text) if entry.text else None
    latest = versions[-1] if versions else None
    if latest and latest["content_hash"] is None and entry.text:   # R5: never got text - refresh it
        store.set_item_fields(conn, latest["id"], full_text=entry.text, text_source="page",
                              fetch_status=None, content_hash=digest, state="fetched")
        counts["new"] = 1
    elif latest and latest["content_hash"] is None and digest is None:
        # still blocked after a reset: the same item waits for the session again
        store.set_item_fields(conn, latest["id"], full_text=None, text_source=None,
                              fetch_status="needs_session", state="fetched", triage_reason=None)
        counts["new"] = 1
    elif latest and digest == latest["content_hash"]:
        counts["unchanged"] = True
    elif not latest or digest:
        n = len(versions) + 1
        url = entry.url if n == 1 else f"{entry.url}#v{n}"
        store.insert_item(conn, {
            "url": url, "canonical_url": canon if n == 1 else f"{canon}#v{n}",
            "source_name": source["name"], "source_type": "once", "title": entry.title,
            "excerpt": (UNKNOWN_PREFIX if entry.date_unknown else "") + entry.excerpt,
            "published": entry.published.isoformat() if entry.published else None,
            "fetched_at": datetime.now(timezone.utc).isoformat(), "state": "fetched",
            "date_unknown": int(entry.date_unknown), "full_text": entry.text,
            "text_source": "page" if entry.text else None,
            "fetch_status": None if entry.text else "needs_session", "content_hash": digest})
        counts["new"] = 1
    store.record_fetch(conn, source["name"], "once", new=counts["new"], error=None)
    store.set_source_fields(conn, source["name"], "once", done=1)
    return counts


def _cursor(state) -> dict:
    try:
        return json.loads(state["cursor"]) if state and state["cursor"] else {}
    except ValueError:
        return {}


def run(conn, cfg, apify_token: str = "", backfill=None, ctx=None, fetch_all: bool = False,
        fresh: bool = False) -> dict:
    ctx = ctx or context_for(cfg, backfill, fresh)
    if apify_token:
        ctx.apify_token = apify_token
    store.log_run(conn, "fetch")
    by_kind = defaultdict(list)
    for source in cfg.topic.sources:
        by_kind[source["type"]].append(source)
    out = {"sources": {}, "degraded": degrade_unresolved(conn), "skipped": 0, "possible_gaps": []}
    adaptive = cfg.adaptive_fetch == "on" and not fetch_all and not ctx.backfill
    now = datetime.now(timezone.utc)
    for type_, group in by_kind.items():
        if ctx.backfill:  # a spike touches only the sources it planned a share for
            group = [s for s in group if s["name"] in ctx.backfill.per_source]
            if not group:
                continue
        kind = get_kind(type_)
        states = {s["name"]: store.get_source_state(conn, s["name"]) for s in group}
        if adaptive:
            for source in group:
                waiting = _not_due(conn, source, states[source["name"]], now,
                                   cfg.fetch_horizon_days)
                if waiting:
                    out["sources"][source["name"]] = waiting
                    out["skipped"] += 1
            group = [s for s in group if s["name"] not in out["sources"]]
            if not group:
                continue
        ctx.first_run = {n for n, st in states.items() if is_first_run(st)}
        ctx.cursors = {n: _cursor(st) for n, st in states.items()}
        if ctx.backfill:  # entries only noted as "seen" are worth another look
            ctx.known = lambda canon: (store.find_by_canonical(conn, canon) or {}).get("state", "seen") != "seen"
        else:
            ctx.known = lambda canon: store.find_by_canonical(conn, canon) is not None
        if type_ == "once":
            for source in group:
                st = states[source["name"]]
                if st and st["done"]:
                    out["sources"][source["name"]] = {
                        "type": "once", "found": 0, "new": 0, "seen": 0, "error": None,
                        "blocked": False, "done": True}
            group = [s for s in group if s["name"] not in out["sources"]]
            if not group:
                continue
        results = _list_new_isolated(kind, group, ctx)
        for source in group:
            result = results.get(source["name"], base.SourceResult())
            gap = _possible_gap(kind, source, result, ctx)
            try:
                out["sources"][source["name"]] = _store_source(
                    conn, cfg, source, kind, result, ctx, states[source["name"]])
                if gap:
                    out["sources"][source["name"]]["possible_gap"] = True
                    out["possible_gaps"].append(source["name"])
            except Exception as exc:  # odd data or a bug in one source never stops the others
                conn.rollback()
                error = _internal_error(exc)
                store.record_fetch(conn, source["name"], source["type"], new=0, error=error)
                out["sources"][source["name"]] = {
                    "type": source["type"], "found": len(result.entries), "new": 0, "seen": 0,
                    "error": error, "blocked": False}
    return out


def _internal_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _list_new_isolated(kind, group: list[dict], ctx) -> dict:
    """A kind that crashes on a batch is asked again per source, so only the culprit fails."""
    try:
        return kind.list_new(group, ctx)
    except Exception as exc:
        if len(group) == 1:
            return {group[0]["name"]: base.SourceResult(error=_internal_error(exc))}
    out = {}
    for source in group:
        try:
            out.update(kind.list_new([source], ctx))
        except Exception as exc:
            out[source["name"]] = base.SourceResult(error=_internal_error(exc))
    return out


def _store_source(conn, cfg, source, kind, result, ctx, state) -> dict:
    if source["type"] == "once":
        return _store_once(conn, source, result, ctx)
    first_run = is_first_run(state) and not ctx.backfill
    counts = store_entries(conn, source, kind, result, ctx, first_run)
    new_ids = counts.pop("_new_ids", [])
    if new_ids and (not kind.HAS_HEADLINES or effective_scan_depth(source, cfg) == "full"):
        _load_full_texts(conn, kind, ctx, new_ids)
    if ctx.backfill and not counts["error"]:
        store.set_source_fields(conn, source["name"], source["type"],
                                backfilled_at=datetime.now(timezone.utc).isoformat())
    return counts


def doctor(sources: list[dict], ctx=None) -> list[dict]:
    return [get_kind(s["type"]).check(s, ctx) for s in sources]
