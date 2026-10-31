# plugins/evidence-echo/src/evidence_echo/render/rejections.py
"""The rejections report: items the pipeline turned down since the previous report.

Pure module: takes item dicts (store.rejected_items) and the previous report's time; the caller
reads the database and writes the files."""
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from evidence_echo.render import clock
from evidence_echo.render.html import _WEB_URL, crumbs, disclaimer_html, esc, link, masthead, page
from evidence_echo.render.markdown import plain

NOT_RECORDED = "not recorded"
EMPTY = "No rejections since the previous report."
STAGE_TITLES = {"judge": "Rejected by the judge", "triage": "Rejected at triage"}
COLUMNS = ("Source", "Title", "Date", "Stage", "Reason", "Note")


@dataclass(frozen=True)
class RejectionRow:
    source: str
    title: str
    url: str
    date: str
    stage: str  # "judge" | "triage"
    reason: str
    note: str


def _parse(value) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _row(it: dict) -> RejectionRow:
    triage = it.get("state") == "rejected_triage" or it.get("triage_pass") == 0
    reason = (it.get("triage_reason") if triage else it.get("judge_reason")) or NOT_RECORDED
    note = ""
    if not triage:
        note = f"passed triage: {it.get('triage_reason') or NOT_RECORDED}"
        if it.get("text_source") == "excerpt":
            note += " · judged from excerpt"
    url = it.get("url") or ""
    return RejectionRow(source=it.get("source_name") or "", title=it.get("title") or url, url=url,
                        date=(it.get("published") or "")[:10], stage="triage" if triage else "judge",
                        reason=reason, note=note)


def rows_in_window(items: list[dict], previous: str | None, now: datetime) -> list[RejectionRow]:
    """Rejections with previous < time <= now (all up to now when previous is None)."""
    prev = _parse(previous) if previous else None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    kept = []
    for it in items:
        t = _parse(it.get("rejected_at") or it.get("fetched_at"))
        if t is None:
            if previous is not None:
                continue
        elif (prev is not None and t <= prev) or t > now:
            continue
        kept.append(it)
    rows = [_row(it) for it in kept]
    rows.sort(key=lambda r: r.date, reverse=True)       # date descending ...
    rows.sort(key=lambda r: r.source)                    # ... within source ...
    rows.sort(key=lambda r: r.stage != "judge")          # ... judge first (stable sorts)
    return rows


def paired_path(report_md: Path) -> Path:
    stem = re.sub(r"-report(-\d+)?$", lambda m: "-rejected" + (m.group(1) or ""), report_md.stem)
    return report_md.with_name(stem + ".md")


def _heading(topic_name: str, previous: str | None) -> str:
    return (f"{topic_name} — rejected since {clock.local_date(previous)}" if previous
            else f"{topic_name} — all rejections")


def _sections(rows: list[RejectionRow]):
    """(stage, section title, rows, sources line) per non-empty stage, judge first."""
    for stage in ("judge", "triage"):
        part = [r for r in rows if r.stage == stage]
        if not part:
            continue
        counts = Counter(r.source for r in part)
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        yield (stage, f"{STAGE_TITLES[stage]} ({len(part)})", part,
               "Sources: " + " · ".join(f"{name} {n}" for name, n in ranked))


def _count_line(rows: list[RejectionRow]) -> str:
    t = sum(r.stage == "triage" for r in rows)
    return f"{len(rows)} rejected: {t} at triage, {len(rows) - t} by the judge"


def _md_cell(text: str) -> str:
    return plain(" ".join(str(text).split())).replace("|", "\\|")


def _md_title(r: RejectionRow) -> str:
    title = _md_cell(r.title)
    if _WEB_URL.fullmatch(r.url):
        return f"[{title}]({r.url.replace('(', '%28').replace(')', '%29')})"
    return title


def markdown(topic_name: str, previous: str | None, rows: list[RejectionRow],
             disclaimer: str | None = None) -> str:
    out = [f"# {_heading(topic_name, previous)}", ""]
    if disclaimer:
        out += [f"*{disclaimer}*", ""]
    if not rows:
        return "\n".join(out + [EMPTY, ""])
    out += [_count_line(rows), ""]
    for _stage, title, part, sources in _sections(rows):
        out += [f"## {title}", "", sources, "",
                "| " + " | ".join(COLUMNS) + " |", "|" + " --- |" * len(COLUMNS)]
        for r in part:
            cells = [_md_cell(r.source), _md_title(r), _md_cell(r.date), r.stage,
                     _md_cell(r.reason), _md_cell(r.note)]
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)


def html_page(topic_name: str, previous: str | None, rows: list[RejectionRow],
              report_href: str, overview_href: str | None = None,
              topic_href: str | None = None, disclaimer: str | None = None) -> str:
    heading = _heading(topic_name, previous)
    trail = (crumbs([("Evidence Echo", overview_href), (topic_name, topic_href),
                     ("Rejections", None)]) if overview_href and topic_href else "")
    mast = masthead(trail, [("Report", report_href)])
    body = [disclaimer_html(disclaimer), f'<header class="ee-head"><h1>{esc(heading)}</h1></header>']
    if not rows:
        body.append(f'<p class="empty">{esc(EMPTY)}</p>')
        return page(heading, "\n".join(body), mast=mast)
    body.append(f"<p>{esc(_count_line(rows))}</p>")
    for _stage, title, part, sources in _sections(rows):
        body += ["<section>", f"<h2>{esc(title)}</h2>", f'<p class="meta">{esc(sources)}</p>',
                 '<div class="table-wrap">', '<table class="ee-rej">', "<thead><tr>" + "".join(f"<th>{c}</th>" for c in COLUMNS) + "</tr></thead>",
                 "<tbody>"]
        for r in part:
            cells = [esc(r.source), link(r.url, r.title), esc(r.date), esc(r.stage),
                     esc(r.reason), esc(r.note)]
            body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        body += ["</tbody>", "</table>", "</div>", "</section>"]
    return page(heading, "\n".join(body), mast=mast)
