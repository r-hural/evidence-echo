"""The short summary of one report, in three renderings: plain text, Telegram HTML, email HTML.

Layout:
    Evidence Echo · SEO · weekly report · 2026-11-14
    6 new studies · best grade B · 1 disputed

    B  Title of the study — example.com
    ...                                 (top 5, grade order)

    Waiting for you: 2 split proposals, 3 possible duplicates (see the report; decide in Claude Code:
                     'review Evidence Echo duplicates')   (the hint only when duplicates wait)
    Source errors: <reason>: <sources>
    Full report attached · saved on this computer as <file name>
"""
from pathlib import PureWindowsPath
from urllib.parse import urlsplit

TELEGRAM_LIMIT = 4096
TOP_N = 5
TIER_ORDER = "ABCD"
TITLE_CHARS = 200
LINE_CHARS = 300
MAX_PROBLEM_LINES = 5
DECIDE_HINT = "(see the report; decide in Claude Code: 'review Evidence Echo duplicates')"
_TIER_COLORS = {"A": "#1a7f37", "B": "#4d7c0f", "C": "#b45309", "D": "#b91c1c"}


def _tier_rank(tier) -> int:
    return TIER_ORDER.index(tier) if isinstance(tier, str) and tier in TIER_ORDER else len(TIER_ORDER)


def from_report(rep, html_path: str | None, md_path: str) -> dict:
    """What the messages need from a rendered report (rep: render.model.Report)."""
    # confirmed studies first (grade order, then report order), then the disputed ones
    ranked = sorted(((e.disputed, _tier_rank(e.tier), n, e) for n, e in enumerate([*rep.new, *rep.disputed])),
                    key=lambda r: r[:3])
    top = []
    for _, _, _, e in ranked[:TOP_N]:
        url = e.coverage[0].url if e.coverage else (e.primary_url or "")
        top.append({"tier": e.tier or "", "title": e.title, "url": url, "disputed": bool(e.disputed)})
    tiers = [e.tier for e in (*rep.new, *rep.disputed) if isinstance(e.tier, str) and e.tier in TIER_ORDER]
    return {"topic": rep.topic_name, "topic_id": rep.topic_id, "date": rep.date,
            "new": len(rep.new) + len(rep.disputed), "disputed": len(rep.disputed),
            "best_tier": min(tiers, key=_tier_rank) if tiers else None, "top": top,
            "source_errors": list(rep.source_warnings), "report_file": html_path or md_path}


def pending(cfg, count_duplicates: bool = True) -> dict:
    """Decisions waiting for the owner: split proposals and possible duplicate studies."""
    from evidence_echo import api
    out = {"splits": 0, "duplicates": 0}
    try:
        out["splits"] = len(api.split_list(cfg).get("proposals", []))
    except Exception:
        pass
    if count_duplicates:
        try:
            out["duplicates"] = int(api.studies_duplicates(cfg, count_only=True).get("count", 0))
        except Exception:
            pass
    return out


def period_words(rule_kind: str) -> str:
    return {"daily": "today", "weekly": "this week", "biweekly": "in the last two weeks",
            "monthly": "this month"}.get(rule_kind, "since the last report")


def empty_text(topic: str, period: str = "since the last report") -> str:
    return f"{topic}: nothing new {period}."


def failed_text(topic: str, error: str) -> str:
    return f"{topic}: report failed — {(error or 'unknown error').strip()}"


def subject(s: dict) -> str:
    n = s["new"]
    best = f" (best grade {s['best_tier']})" if s.get("best_tier") else ""
    return f"Evidence Echo · {s['topic']}: {n} new stud{'y' if n == 1 else 'ies'}{best}"


# --- pieces -----------------------------------------------------------------------------------

def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _host(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _safe_url(url: str) -> str:
    return url if str(url).lower().startswith(("http://", "https://")) else ""


def _counts(s: dict) -> str:
    parts = [f"{s['new']} new stud{'y' if s['new'] == 1 else 'ies'}"]
    if s.get("best_tier"):
        parts.append(f"best grade {s['best_tier']}")
    if s.get("disputed"):
        parts.append(f"{s['disputed']} disputed")
    return " · ".join(parts)


def _waiting(pending_counts: dict | None) -> str | None:
    p = pending_counts or {}
    parts = []
    if p.get("splits"):
        parts.append(f"{p['splits']} split proposal{'s' if p['splits'] != 1 else ''}")
    if p.get("duplicates"):
        parts.append(f"{p['duplicates']} possible duplicate{'s' if p['duplicates'] != 1 else ''}")
    if not parts:
        return None
    hint = f" {DECIDE_HINT}" if p.get("duplicates") else ""
    return "Waiting for you: " + ", ".join(parts) + hint


def _problem_lines(problems: list[str]) -> list[str]:
    lines = [_clip(p, LINE_CHARS) for p in problems[:MAX_PROBLEM_LINES]]
    if len(problems) > MAX_PROBLEM_LINES:
        lines.append(f"+{len(problems) - MAX_PROBLEM_LINES} more in the report")
    return lines


def _header(s: dict, period_label: str) -> str:
    return f"Evidence Echo · {_clip(s['topic'], 80)} · {period_label}"


def _footer(s: dict) -> str:
    name = PureWindowsPath(s.get("report_file") or "").name   # the name only: a full path shows the user name
    return f"Full report attached · saved on this computer as {_clip(name, 160)}"


# --- plain text -------------------------------------------------------------------------------

def text(s: dict, period_label: str, problems: list[str], pending_counts: dict | None = None) -> str:
    lines = [_header(s, period_label), _counts(s), ""]
    for item in s["top"]:
        host = _host(item["url"])
        lines.append(f"{item['tier'] or '-'}  {_clip(item['title'], TITLE_CHARS)}" + (f" — {host}" if host else "")
                     + (" (disputed)" if item.get("disputed") else ""))
    if len(s["top"]) < s["new"]:
        lines.append(f"+{s['new'] - len(s['top'])} more in the report")
    lines.append("")
    waiting = _waiting(pending_counts)
    if waiting:
        lines.append(waiting)
    lines += [f"Source errors: {p}" for p in _problem_lines(problems)]
    lines.append(_footer(s))
    return "\n".join(lines)


# --- Telegram HTML ----------------------------------------------------------------------------

def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(text: str) -> str:
    return _esc(text).replace('"', "&quot;")


def _tg_item(item: dict) -> str:
    tier = _esc(item["tier"] or "-")
    title = _esc(_clip(item["title"], TITLE_CHARS))
    url, host = _safe_url(item["url"]), _host(item["url"])
    link = f' — <a href="{_esc_attr(url)}">{_esc(host or "source")}</a>' if url else ""
    return f"<b>{tier}</b>  {title}{link}" + (" (disputed)" if item.get("disputed") else "")


def telegram_html(s: dict, period_label: str, problems: list[str], pending_counts: dict | None = None,
                  limit: int = TELEGRAM_LIMIT) -> str:
    """Valid Telegram HTML of at most `limit` characters: items drop from the bottom ("+N more in the
    report"), then problem lines; nothing is ever cut in the middle of a tag."""
    items = [_tg_item(i) for i in s["top"]]
    total = max(s["new"], len(items))
    waiting = _waiting(pending_counts)
    problem_lines = [f"Source errors: {_esc(p)}" for p in _problem_lines(problems)]

    def assemble(n_items: int, n_problems: int) -> str:
        parts = [f"<b>{_esc(_header(s, period_label))}</b>", _esc(_counts(s)), ""]
        parts += items[:n_items]
        if n_items < total:
            parts.append(f"+{total - n_items} more in the report")
        parts.append("")
        if waiting:
            parts.append(_esc(waiting))
        parts += problem_lines[:n_problems]
        parts.append(_esc(_footer(s)))
        return "\n".join(parts)

    for n_problems in range(len(problem_lines), -1, -1):
        for n_items in range(len(items), -1, -1):
            message = assemble(n_items, n_problems)
            if len(message) <= limit:
                return message
    return _esc(_header(s, period_label))[:limit]   # unreachable with bounded pieces


def telegram_plain(message: str, limit: int = TELEGRAM_LIMIT) -> str:
    """A short plain message (empty / failed) as Telegram HTML."""
    return _esc(message)[:limit]


# --- email HTML (inline CSS only) -------------------------------------------------------------

def email_html(s: dict, period_label: str, problems: list[str], pending_counts: dict | None = None) -> str:
    font = "font-family:Georgia,serif;color:#1c1917;"
    rows = []
    for item in s["top"]:
        tier = item["tier"] or "-"
        color = _TIER_COLORS.get(tier, "#57534e")
        url, host = _safe_url(item["url"]), _host(item["url"])
        link = (f' — <a href="{_esc_attr(url)}" style="color:#57534e;">{_esc(host or "source")}</a>'
                if url else "")
        rows.append(
            f'<tr><td style="padding:6px 12px 6px 0;vertical-align:top;font-weight:bold;color:{color};">'
            f'{_esc(tier)}</td><td style="padding:6px 0;">{_esc(_clip(item["title"], TITLE_CHARS))}{link}</td></tr>')
    more = ""
    if len(s["top"]) < s["new"]:
        more = f'<p style="margin:8px 0;color:#57534e;">+{s["new"] - len(s["top"])} more in the report</p>'
    notes = []
    waiting = _waiting(pending_counts)
    if waiting:
        notes.append(f'<p style="margin:8px 0;">{_esc(waiting)}</p>')
    notes += [f'<p style="margin:8px 0;color:#b45309;">Source errors: {_esc(p)}</p>'
              for p in _problem_lines(problems)]
    return (f'<div style="{font}max-width:640px;margin:0 auto;padding:16px;">'
            f'<h2 style="margin:0 0 4px 0;font-size:18px;">{_esc(_header(s, period_label))}</h2>'
            f'<p style="margin:0 0 12px 0;color:#57534e;">{_esc(_counts(s))}</p>'
            f'<table style="border-collapse:collapse;width:100%;">{"".join(rows)}</table>{more}'
            f'{"".join(notes)}'
            f'<p style="margin:16px 0 0 0;color:#57534e;font-size:13px;">{_esc(_footer(s))}</p></div>')


def email_html_plain(message: str) -> str:
    return (f'<div style="font-family:Georgia,serif;color:#1c1917;max-width:640px;margin:0 auto;'
            f'padding:16px;"><p>{_esc(message)}</p></div>')
