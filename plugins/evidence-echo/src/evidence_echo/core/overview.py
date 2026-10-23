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


def rank(entries: list[dict], limit: int = 3) -> list[dict]:
    by_date = sorted(entries, key=lambda e: e.get("published") or "", reverse=True)
    return sorted(by_date, key=lambda e: TIER_ORDER.get(e.get("tier"), 9))[:limit]


def _link(path: Path, out_dir: Path) -> str:
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
    n = len(s.report["entries"])
    lines = [f"## {s.name}  ·  last run {clock.local_date(s.report['rendered_at'])}  ·  "
             f"{n} new finding{'' if n == 1 else 's'}"]
    lines += [f"- **[{e.get('tier') or '?'}]** {e['title']} — [report]({link})"
              for e in rank(s.report["entries"])]
    return lines + [f"[Latest report]({link})", ""]


def render(summaries: list[TopicSummary], out_dir: Path, now: datetime) -> str:
    lines = ["# Evidence Echo — Overview", f"Updated {clock.local_minute(now)}", ""]
    if not summaries:
        return "\n".join(lines + ["*No topics yet — run /evidence-echo:setup.*", ""])
    for s in summaries:
        lines += _section(s, out_dir)
    return "\n".join(lines)
