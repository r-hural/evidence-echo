"""overview.md: every topic's latest report at a glance (no LLM).

rank() is the one place that orders findings; grading changes swap it.
"""
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from evidence_echo.render import clock

TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


@dataclass(frozen=True)
class TopicSummary:
    id: str
    name: str
    report: dict | None = None
    error: str | None = None
    recent: dict | None = None   # store.recent_studies(...); None: derive from the report's entries


def rank(entries: list[dict], limit: int = 3) -> list[dict]:
    by_date = sorted(entries, key=lambda e: e.get("published") or "", reverse=True)
    return sorted(by_date, key=lambda e: TIER_ORDER.get(e.get("tier"), 9))[:limit]


def select(s: TopicSummary) -> tuple[list[dict], list[dict], int]:
    """(latest, earlier, more): the studies one topic lists, as store.recent_studies rows. Without
    `recent` (a summary built from the report alone) the report's entries, ranked, are the latest."""
    if s.recent is not None:
        return s.recent["latest"], s.recent["earlier"], s.recent["more"]
    entries = s.report["entries"] if s.report else []
    latest = [{"study_id": e.get("study_id"), "title": e.get("title"), "tier": e.get("tier"),
               "report_path": s.report["path"], "rendered_at": s.report["rendered_at"]}
              for e in rank(entries, limit=len(entries))]
    return latest, [], 0


def new_count(s: TopicSummary) -> int:
    """The studies the newest report showed first (the header's 'N new findings')."""
    latest, _, more = select(s)
    return len(latest) + more


def _link(path: Path, out_dir: Path) -> str:
    if not path.exists() and path.with_suffix(".html").exists():   # md_reports off: the HTML page
        path = path.with_suffix(".html")
    try:
        return Path(os.path.relpath(path, out_dir)).as_posix() if \
            path.resolve().is_relative_to(out_dir.resolve()) else path.as_uri()
    except ValueError:  # another drive on Windows
        return path.as_uri()


def _section(s: TopicSummary, out_dir: Path) -> list[str]:
    if s.error:
        return [f"## {s.name}  ·  setup error: {s.error}", ""]
    if not s.report:
        return [f"## {s.name}  ·  not run yet", ""]
    link = _link(Path(s.report["path"]), out_dir)
    latest, earlier, more = select(s)
    n = len(latest) + more
    lines = [f"## {s.name}  ·  last run {clock.local_date(s.report['rendered_at'])}  ·  "
             f"{n} new finding{'' if n == 1 else 's'}"]

    def line(e):
        return f"- **[{e.get('tier') or '?'}]** {e['title']} — [report]({_link(Path(e['report_path']), out_dir)})"
    lines += [line(e) for e in latest]
    if more:
        lines.append(f"- [+{more} more in the report]({link})")
    if earlier:
        lines += ["", "Earlier", ""] + [line(e) for e in earlier]
    return lines + ["", f"[Latest report]({link})", ""]


def render(summaries: list[TopicSummary], out_dir: Path, now: datetime) -> str:
    lines = ["# Evidence Echo — Overview", f"Updated {clock.local_minute(now)}", ""]
    if not summaries:
        return "\n".join(lines + ["*No topics yet — run /evidence-echo:setup.*", ""])
    for s in summaries:
        lines += _section(s, out_dir)
    return "\n".join(lines)
