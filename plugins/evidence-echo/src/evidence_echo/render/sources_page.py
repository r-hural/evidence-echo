"""The per-topic Sources page: for every source, the studies it produced and the entries fetched.

A study belongs to the report that first included it (store.first_report_ids), so a seen-before
study shown again as "More coverage" never counts as a later report's study. HTML only.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from evidence_echo.core import store
from evidence_echo.render import clock
from evidence_echo.render import html as h


@dataclass(frozen=True)
class SourceRow:
    name: str
    kind: str
    url: str | None            # what the engine fetches (the feed, the listing page, the profile)
    site_url: str | None       # what a person opens
    all_studies: int           # studies from this source in any report
    all_fetched: int           # every item of this source in the topic database
    last_studies: int          # studies first included by the newest report
    last_fetched: int          # items fetched after the previous report, up to the newest one
    last_fetched_at: str | None   # ISO time of the last fetch run ("we tried"), else the newest entry
    broken: str | None = None     # the error of a real failure (blocked, failing); None when fine
    quiet_days: int | None = None  # fetched fine but nothing new in this many days (not an error)
    removed: bool = False         # in the database, no longer in topic.json
    last_is_entry: bool = False   # last_fetched_at is the newest entry, there is no fetch record


@dataclass(frozen=True)
class SourcesPage:
    topic_id: str
    topic_name: str
    generated_at: str
    rows: list


def _when(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _broken(state: dict | None, kind: str) -> str | None:
    if not state or (kind == "once" and state.get("done")):
        return None
    if state.get("blocked") or (state.get("consecutive_errors") or 0) > 0 or state.get("failing_since"):
        return state.get("last_error") or ("blocked" if state.get("blocked") else "error")
    return None


def _feed_url(source: dict) -> str | None:
    if source.get("type") == "linkedin":
        return f"https://www.linkedin.com/in/{source.get('slug', '')}/"
    return source.get("url")


def build_sources(conn, topic_cfg, now: datetime, horizon_days: int = 7) -> SourcesPage:
    reports = store.list_reports(conn)                       # oldest first: (rendered_at, id)
    newest = max(reports, key=lambda r: r["id"]) if reports else None
    previous = None
    if newest:
        order = [r["id"] for r in reports]
        i = order.index(newest["id"])
        previous = reports[i - 1] if i > 0 else None
    newest_at = _when(newest["rendered_at"]) if newest else None
    previous_at = _when(previous["rendered_at"]) if previous else None
    first_ids = store.first_report_ids(conn)
    stats = {row["name"]: row for row in store.source_item_stats(conn)}
    states = {row["name"]: row for row in store.all_source_states(conn)}
    horizon_start = now - timedelta(days=horizon_days)
    configured = {s["name"]: s for s in topic_cfg.sources}

    def row_for(name: str, source: dict | None) -> SourceRow:
        stat = stats.get(name) or {"fetched_at": [], "study_ids": set(), "type": ""}
        state = states.get(name)
        kind = (source or {}).get("type") or (state or {}).get("type") or stat["type"]
        times = [t for t in map(_when, stat["fetched_at"]) if t]
        last_fetched = 0
        if newest_at:
            last_fetched = sum(1 for t in times
                               if t <= newest_at and (previous_at is None or t > previous_at))
        last_at, is_entry = (state or {}).get("last_checked"), False
        if not last_at and times:
            last_at, is_entry = max(times).isoformat(), True
        broken = _broken(state, kind) if source else None
        quiet = None
        if (source and not broken and state and kind != "once"
                and not any(t >= horizon_start for t in times)):
            quiet = horizon_days
        return SourceRow(
            name=name, kind=kind, url=_feed_url(source) if source else None,
            site_url=(source or {}).get("site_url"),
            all_studies=len(stat["study_ids"]), all_fetched=len(stat["fetched_at"]),
            last_studies=sum(1 for sid in stat["study_ids"]
                             if newest and first_ids.get(sid) == newest["id"]),
            last_fetched=last_fetched, last_fetched_at=last_at, broken=broken, quiet_days=quiet,
            removed=source is None, last_is_entry=is_entry)

    def order(row: SourceRow):
        return (-row.all_studies, -row.all_fetched, row.name.casefold(), row.name)

    rows = sorted((row_for(name, s) for name, s in configured.items()), key=order)
    gone = sorted((row_for(name, None) for name in stats if name not in configured
                   and stats[name]["fetched_at"]), key=order)
    return SourcesPage(topic_id=topic_cfg.id, topic_name=topic_cfg.name,
                       generated_at=now.isoformat(), rows=rows + gone)


def _links(row: SourceRow) -> str:
    if row.url and row.site_url and row.site_url.rstrip("/") != row.url.rstrip("/"):
        return (f'<span class="ee-src-links">{h.link(row.site_url, "Site")} · '
                f'{h.link(row.url, "Feed")}</span>')
    only = row.url or row.site_url
    if not only:
        return ""
    label = "Feed" if row.kind in ("rss", "linkedin") else "Page"
    return f'<span class="ee-src-links">{h.link(only, label)}</span>'


def _fetched_cell(row: SourceRow) -> str:
    parts = []
    if row.last_fetched_at:
        text = clock.local_date(row.last_fetched_at)
        parts.append(f'{h.esc(text)} <span class="meta">(last entry)</span>' if row.last_is_entry
                     else h.esc(text))
    else:
        parts.append('<span class="meta">never</span>')
    if row.broken:
        parts.append(f'<span class="ee-broken"><span aria-hidden="true">∅</span> '
                     f'{h.esc(row.broken)}</span>')
    elif row.quiet_days:
        parts.append(f'<span class="meta">no entries in {row.quiet_days} days</span>')
    return "<br>".join(parts)


def _row_html(row: SourceRow, topic_href: str, slug: str | None) -> str:
    name = (f'<a href="{h.esc(h.source_href(topic_href, slug))}">{h.esc(row.name)}</a>'
            if slug else h.esc(row.name))
    cls = ' class="ee-gone"' if row.removed else ""
    return (f"<tr{cls}><td>{name}<br>{_links(row)}</td><td>{h.esc(row.kind)}</td>"
            f'<td class="num">{row.all_studies} / {row.all_fetched}</td>'
            f'<td class="num">{row.last_studies} / {row.last_fetched}</td>'
            f"<td>{_fetched_cell(row)}</td></tr>")


def sources_html(p: SourcesPage, overview_href: str, topic_href: str, report_href: str) -> str:
    title = f"{p.topic_name} — sources"
    mast = h.masthead(h.crumbs([("Evidence Echo", overview_href), (p.topic_name, topic_href),
                                ("Sources", None)]),
                      [("All studies", topic_href), ("Latest report", report_href)])
    live = [r for r in p.rows if not r.removed]
    gone = [r for r in p.rows if r.removed]
    never = sum(1 for r in live if not r.all_studies)
    noun = "source" if len(live) == 1 else "sources"
    slugs = h.source_slugs(r.name for r in p.rows if r.all_studies)
    body = [f'<header class="ee-head"><h1>{h.esc(title)}</h1>',
            f'<p class="meta">{len(live)} {noun} · {never} never produced a study</p>',
            f'<p class="meta">Updated {h.esc(clock.local_minute(p.generated_at))}</p></header>']
    if not p.rows:
        body.append('<p class="empty">No sources in this topic yet.</p>')
        return h.page(title, "\n".join(body), mast=mast)
    head = ("<thead><tr><th>Source</th><th>Kind</th>"
            '<th class="num">All reports<br><span class="ee-sub-h">studies / fetched</span></th>'
            '<th class="num">Last report<br><span class="ee-sub-h">studies / fetched</span></th>'
            "<th>Last fetched</th></tr></thead>")
    rows = [_row_html(r, topic_href, slugs.get(r.name)) for r in live]
    if gone:
        rows.append('<tr class="ee-gone-head"><th colspan="5">No longer in the topic</th></tr>')
        rows += [_row_html(r, topic_href, slugs.get(r.name)) for r in gone]
    body.append(f'<div class="table-wrap"><table class="ee-src">{head}<tbody>{"".join(rows)}</tbody>'
                "</table></div>")
    body.append('<p class="meta ee-key">Studies count a study once, in the report that first showed it; '
                "fetched counts every entry the source delivered.</p>")
    return h.page(title, "\n".join(body), mast=mast)
