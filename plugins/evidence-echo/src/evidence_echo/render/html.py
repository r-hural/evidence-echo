# plugins/evidence-echo/src/evidence_echo/render/html.py
"""HTML pages (SP5a-H): a digest per report, a cumulative topic page with filters, an overview.

Self-contained files: inline CSS and JS, links only to http(s) sources and to sibling pages.
Every text value is escaped here; nothing else in the package emits HTML."""
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from evidence_echo.core import grading, knowledge
from evidence_echo.core import overview as overview_mod
from evidence_echo.render import clock
from evidence_echo.render import evidence_text as et
from evidence_echo.render import html_assets
from evidence_echo.render.markdown import (NOTE_MAX, TITLE_MAX, _cut, counts_text, fact_pairs, metric_text,
                                           quote_text,
                                           reported_value, research_pairs, subtopic_lines)
from evidence_echo.render.model import Entry, Report, SeenEntry, Topic, TopicStudy

_WEB_URL = re.compile(r"https?://[^\s\x00-\x1f\x7f]+", re.IGNORECASE)


def esc(value) -> str:
    return "" if value is None else html.escape(str(value), quote=True)


def link(url, text=None) -> str:
    """An <a> for an http(s) URL. Anything else is shown as escaped text, never as a link."""
    if isinstance(url, str) and _WEB_URL.fullmatch(url):
        return f'<a href="{esc(url)}" rel="noopener noreferrer">{esc(url if text is None else text)}</a>'
    if text is None:
        return esc(url)
    return f"{esc(text)} ({esc(url)})" if url else esc(text)


def json_script(data, element_id: str) -> str:
    """Data for the page's JS. <, > and & are escaped, so the text can never close the tag."""
    text = (json.dumps(data, ensure_ascii=False)
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))
    return f'<script type="application/json" id="{esc(element_id)}">{text}</script>'


TIER_CLASS = {"A": "tier-a", "B": "tier-b", "C": "tier-c", "D": "tier-d"}


def relation_of(e: Entry) -> str | None:
    relation = (e.view.get("provenance") or {}).get("relation")
    return relation if relation and relation != "unknown" else None


def tier_badge(tier) -> str:
    shown = tier if tier in TIER_CLASS else "?"
    return (f'<span class="tier {TIER_CLASS.get(tier, "tier-x")}">'
            f'<span class="sr">Tier </span>{esc(shown)}</span>')


def label_parts(e: Entry) -> tuple[str, list[str]]:
    """The Markdown label "[tier · extra · ...]" as (tier text, extras)."""
    tier = e.tier or "?"
    if e.disputed and not e.no_quote_only:  # double grade: the verdict's tier and the skeptic's
        tier += " / skeptic: " + (", ".join(e.skeptic_tiers) if e.skeptic_tiers else "disputed")
    view = e.view
    extras = [relation_of(e),
              f"capped from {view['capped_from']}" if view.get("capped_from") else None,
              None if e.disputed else view.get("gap_note")]
    return tier, [x for x in extras if x]


def series_text(e: Entry) -> str | None:
    if not e.series:
        return None
    return (f"Part of: {e.series} (editions {', '.join(e.editions)})" if e.editions
            else f"Part of: {e.series}")


def dispute_text(e: Entry) -> str:
    """Same words as the Markdown entry: the reason lead-in only when there is one."""
    prefix = f"Disputed: {e.dispute_reason}. " if e.dispute_reason else ""
    return f"{prefix}Skeptic's doubts: {'; '.join(e.skeptic_notes) or 'no reason given'}"


def subtopic_lines_with_echo(e: Entry) -> list[tuple[str, str]]:
    """The card's Subtopic and Question pairs; the study's echo line goes on the Question
    value, or on the Subtopic value when it has no question."""
    if not e.subtopic:
        return []
    pairs = subtopic_lines(e.subtopic)
    echo = e.subtopic.get("echo")
    if echo:
        name, value = pairs[-1]
        pairs[-1] = (name, f"{value} · {echo}")
    return pairs


LEGEND = ("✓ consistent: all independent studies point the same way · "
          "⇅ contested: some say up, some say down · ≈ varied: any other mix")
SKEPTIC_DOUBTS = "Skeptic's doubts"


WARN_FACTS = {"Red flags", "Numbers not found in the text", "Original"}  # set in the warning red


MARKS = {"not reachable": "∅", "numbers not found": "#"}   # printed marks of source-check problems


def _mark(e: Entry) -> str:
    """The printed mark of the study's source-check problem, so the state is not carried by red alone."""
    symbol = MARKS.get(original_cell(e)[0])
    return f'<span class="ee-mark" aria-hidden="true">{symbol}</span>' if symbol else ""


def _field(name: str, value_html: str, mark: str = "") -> str:
    if name in WARN_FACTS:
        value_html = f'<span class="ee-warn">{mark}{value_html}</span>'
    return f'<p class="ee-f"><b>{esc(name)}:</b> {value_html}</p>'


def _dispute_html(e: Entry) -> str:
    parts = ([f"<b>Disputed:</b> {esc(e.dispute_reason)}."] if e.dispute_reason else [])
    doubts = "; ".join(e.skeptic_notes) or "no reason given"
    parts.append(f"<b>{esc(SKEPTIC_DOUBTS)}:</b> {esc(doubts)}")
    return '<p class="dispute">' + "<br>".join(parts) + "</p>"


def card(e: Entry, reported: str | None = None) -> str:
    """One study as a write-up in three columns: the rating (tier, relation, skeptic) in the
    margin, title and summary, then the named facts on one name column.
    reported is the topic page's plain 'Reported' value (escaped here)."""
    tier, extras = label_parts(e)
    rest = (tier[len(e.tier or "?"):] + "".join(f" · {x}" for x in extras)).strip(" ·/")
    rate = [f'<p class="label">{tier_badge(e.tier)}'
            + (f"<span>{esc(rest)}</span>" if rest else "") + "</p>"]
    if e.disputed:
        rate.append('<span class="ee-warn"><span class="ee-mark" aria-hidden="true">≠</span>disputed</span>')
    main = [f"<h3>{esc(e.title)}</h3>"]
    if e.summary:
        main.append(f"<p>{esc(e.summary)}</p>")
    side = []
    if e.disputed:
        side.append(_dispute_html(e))
    side += [_field(n, esc(v), _mark(e) if n == "Original" else "") for n, v in fact_pairs(e.view)]
    side += [_field(n, esc(v)) for n, v in subtopic_lines_with_echo(e)]
    if e.series:
        side.append(_field("Part of", esc(series_text(e).removeprefix("Part of: "))))
    side += [_field(n, esc(v)) for n, v in research_pairs(e)]
    if e.primary_url:
        side.append(_field("Primary source", link(e.primary_url)))
    if e.coverage:
        side.append(_field("Coverage", " · ".join(link(c.url, c.name) for c in e.coverage)))
    if reported:
        side.append(_field("Reported", esc(reported)))
    return "\n".join([f'<article class="card" id="s{e.study_id}" data-study="{e.study_id}">'
                      '<div class="ee-card">',
                      '<div class="ee-card-rate">', *rate, "</div>",
                      '<div class="ee-card-main">', *main, "</div>",
                      '<div class="ee-card-side">', *side, "</div>", "</div></article>"])


def href(target: Path, from_dir: Path) -> str:
    """A link from a page in from_dir to a local page: relative, or a file URI on another drive."""
    try:
        return quote(Path(os.path.relpath(target, from_dir)).as_posix(), safe="/")
    except ValueError:  # another drive on Windows
        return Path(target).resolve().as_uri()


BRAND = "Evidence Echo"


def masthead(trail: str = "", links: list[tuple[str, str]] = ()) -> str:
    """The black bar on top of every page: the breadcrumb trail (its first crumb is the bar's
    name), or the plain name on a page without crumbs, and the page's own links."""
    lead = trail or f'<span class="ee-brand">{esc(BRAND)}</span>'
    nav = ('<nav class="ee-links" aria-label="Pages">'
           + "".join(f'<a href="{esc(target)}">{esc(text)}</a>' for text, target in links)
           + "</nav>") if links else ""
    return f'<header class="ee-mast" id="ee-top"><div class="ee-mast-in">{lead}{nav}</div></header>\n'


def page_footer(now: datetime | None = None) -> str:
    """The end of every page: a link back to the top and when the page was generated (local time)."""
    when = clock.local_minute(now or datetime.now(timezone.utc))
    return ('<footer class="ee-foot"><div class="ee-foot-in">'
            '<a href="#ee-top">Back to top <span aria-hidden="true">↑</span></a>'
            f'<span>Generated {esc(when)}</span></div></footer>\n')


def page(title: str, body: str, script: str = "", mast: str | None = None,
         now: datetime | None = None) -> str:
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{esc(title)}</title>\n<style>{html_assets.CSS}</style>\n</head>\n"
            f"<body>\n{masthead() if mast is None else mast}<main>\n{body}\n</main>\n"
            f"{page_footer(now)}{script}</body>\n</html>\n")


def _write(path: Path, text: str) -> Path:
    """Write via <name>.tmp and an atomic replace: a failed run never leaves half a page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def seen_item(s: SeenEntry, topic_href: str) -> str:
    """One "More coverage of earlier studies" line, as in the Markdown report."""
    links = ", ".join(link(c.url, c.name) for c in s.coverage)
    title = f'<a href="{esc(topic_href)}#s{s.study_id}">{esc(s.first_title)}</a>'
    text = f"{title} (first reported {esc((s.first_reported or '')[:10])}): {links}"
    if s.disputed:
        text += esc(f" — disputed: skeptic says {', '.join(s.skeptic_tiers)}" if s.skeptic_tiers
                    else " — disputed by skeptic")
        if s.skeptic_notes:
            text += esc("; " + "; ".join(s.skeptic_notes))
    return f"<li>{text}</li>"


def crumbs(trail: list[tuple[str, str | None]]) -> str:
    """The breadcrumb trail; the last item is the current page and not a link."""
    items = []
    for i, (text, target) in enumerate(trail):
        if i == len(trail) - 1 or target is None:
            items.append(f'<li aria-current="page">{esc(text)}</li>')
        else:
            items.append(f'<li><a href="{esc(target)}">{esc(text)}</a></li>')
    return '<nav class="ee-crumbs" aria-label="Breadcrumb"><ol>' + "".join(items) + "</ol></nav>"


def original_cell(e: Entry) -> tuple[str, bool]:
    """The chart's short word for the primary-source check, and whether it is a problem."""
    primary = e.view.get("primary") or {}
    status, verdict = primary.get("status"), primary.get("verdict")
    if status == "unreachable":
        return "not reachable", True
    if status == "not_checked":
        return "not checked", False
    if verdict and verdict != "faithful":
        return verdict.replace("_", " "), True
    if primary.get("missing"):
        return "numbers not found", True
    if verdict == "faithful":
        return "faithful", False
    return ("—" if e.primary_url else "no primary source"), False


def _chart_row(e: Entry) -> str:
    """One study's row in the ratings chart: grade, skeptic, study (a link to its write-up),
    red flags, original. A problem is set in red and named in words, never by colour alone."""
    if e.disputed:
        skeptic = ('<span class="ee-warn"><span class="ee-mark" aria-hidden="true">≠</span>'
                   + esc(", ".join(e.skeptic_tiers) or "disputed") + "</span>")
    elif str(e.view.get("gap_note") or "").startswith("skeptic: "):   # one tier apart, not a dispute
        skeptic = tier_badge(e.view["gap_note"].removeprefix("skeptic: "))
    else:
        skeptic = '<span class="ee-none">—</span>'
    flags = [c.replace("_", " ") for c in e.view.get("flags") or []]
    if e.view.get("missing_numbers"):
        flags.append("numbers not in text")
    flags_html = (f'<span class="ee-warn">{esc(", ".join(flags))}</span>' if flags
                  else '<span class="ee-none">none</span>')
    original, bad = original_cell(e)
    original_html = (f'<span class="ee-warn">{_mark(e)}{esc(original)}</span>' if bad
                     else f'<span class="ee-none">{esc(original)}</span>' if original == "—"
                     else esc(original))
    return (f'<tr><td class="ee-c-grade">{tier_badge(e.tier)}</td>'
            f'<td class="ee-c-study"><a href="#s{e.study_id}">{esc(e.title)}</a></td>'
            f'<td data-name="Skeptic">{skeptic}</td>'
            f'<td data-name="Red flags">{flags_html}</td>'
            f'<td data-name="Original">{original_html}</td></tr>')


def _key_mark(symbol: str, text: str) -> str:
    return f'<span class="ee-key-g"><span class="ee-warn"><span class="ee-mark">{symbol}</span></span>{text}</span>'


RATINGS_KEY = ('<span class="ee-key-g">' + " ".join(tier_badge(t) for t in ("A", "B", "C", "D"))
               + " strongest to weakest evidence</span>"
               + f'<span class="ee-key-g">{tier_badge(None)} ungraded</span>'
               + '<span class="ee-key-g">Skeptic: the blind second grade, — when it agrees</span>'
               + _key_mark("≠", "disputed") + _key_mark("#", "numbers not found in the original")
               + _key_mark("∅", "original not reachable"))


def ratings_chart(entries: list[Entry]) -> str:
    """The run at a glance, before the write-ups. Empty without entries."""
    if not entries:
        return ""
    head = "".join(f'<th scope="col">{x}</th>'
                   for x in ("Grade", "Study", "Skeptic", "Red flags", "Original"))
    return ('<table class="ee-chart"><caption>This run’s ratings</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>"
            + "".join(_chart_row(e) for e in entries) + "</tbody></table>"
            + f'<p class="meta ee-key">{RATINGS_KEY}</p>')


def report_page(r: Report, topic_href: str, overview_href: str) -> str:
    title = f"{r.topic_name} Report - {r.date}"
    counts = [f"{len(r.new)} new {'study' if len(r.new) == 1 else 'studies'}"]
    if r.disputed:
        counts.append(f"{len(r.disputed)} disputed")
    if r.seen:
        counts.append(f"{len(r.seen)} more coverage of earlier studies")
    mast = masthead(crumbs([("Evidence Echo", overview_href), (r.topic_name, topic_href),
                            (f"Report {r.label or r.date}", None)]),
                    [("All studies", topic_href)])
    body = [f'<header class="ee-head"><h1>{esc(title)}</h1>',
            f'<p class="meta">{esc(" · ".join(counts))}</p></header>',
            ratings_chart(r.new + r.disputed),
            "<section>", "<h2>New studies</h2>"]
    body += ([card(e) for e in r.new] if r.new
             else ['<p class="empty">No confirmed evidence-backed items this run.</p>'])
    body.append("</section>")
    if r.disputed:
        body += ["<section>", "<h2>Disputed (verdict and skeptic disagree; both grades shown)</h2>",
                 *[card(e) for e in r.disputed], "</section>"]
    if r.seen:
        body += ["<section>", "<h2>More coverage of earlier studies</h2>", '<ul class="seen">',
                 *[seen_item(s, topic_href) for s in r.seen], "</ul>", "</section>"]
    if r.source_warnings:
        body.append(f'<footer><p class="warn">{esc("Source warnings: " + ", ".join(r.source_warnings))}'
                    "</p></footer>")
    return page(title, "\n".join(body), mast=mast)


def _study_ref(study_id: int, titles: dict) -> str:
    if study_id in titles:
        return f'<a href="#s{study_id}">{esc(titles[study_id])}</a>'
    return esc(f"study {study_id}")


def _row_item(r, titles: dict, lead: str = "") -> str:
    """One study entry: the tier mark, then [the question on its own line,] name · headline
    [date] [(edition)]. The whole entry links to the study's card when the page has one."""
    edition = et.edition_text(r)
    name = esc(titles.get(r.study_id, r.name))
    line = (f'<span class="ee-row-line">{name} · {esc(et.headline_text(r))} '
            f'<span class="ee-date">[{esc(et.date_text(r))}]</span>'
            + (f' <span class="meta">({esc(edition)})</span>' if edition else "") + "</span>")
    body = tier_badge(r.tier) + (f'<span class="ee-q">{esc(lead)}</span>' if lead else "") + line
    if r.study_id in titles:
        return f'<li><a class="ee-row" href="#s{r.study_id}">{body}</a></li>'
    return f'<li><span class="ee-row">{body}</span></li>'


def study_rows(c, titles: dict, summary_html: str) -> str:
    """The question's studies, newest first, in a closed <details> (works without JS) whose summary is
    summary_html (the question), plus a change line per repeated series. Empty without rows."""
    if not c.rows:
        return ""
    items = "".join(_row_item(r, titles) for r in c.rows)
    changes = "".join(f'<p class="meta ee-change">{esc(et.change_text(x))}</p>' for x in c.changes)
    return (f'<details class="ee-studies"><summary>{summary_html}</summary>'
            f"<ol>{items}</ol>{changes}</details>")


def single_item(o, titles: dict, unsorted: bool) -> str:
    """A study alone on its question (or not grouped yet) as a question entry on the same level as
    the questions with 2+ studies: the question, then its one study, always shown. Unsorted studies
    have no question part."""
    head = ""
    if not unsorted:
        cls = "ee-single-q" + ("" if o.question else " ee-ungrouped")
        head = f'<span class="{cls}">{esc(o.question or et.NOT_GROUPED)}</span>'
    return (f'<li class="ee-question ee-single">{head}'
            f'<ol class="ee-single-row">{_row_item(o.row, titles)}</ol></li>')


def question_explanation(g, titles: dict) -> str:
    """The question's one explanation as a closed <details>: the synthesis, the main result, and a line
    per study outside it (title linked to its card, direction, reason, note, the checked quote)."""
    x = g.explanation
    if not x:
        return ""
    names = {r.study_id: r.name for r in g.rows}
    direction = {members[0]: d for members, d in zip(g.groups, g.group_dirs())}
    parts = ['<details class="ee-why"><summary>Why the results differ</summary>']
    if x.get("summary"):
        parts.append(f"<p>{esc(x['summary'])}</p>")
    parts.append(f'<p class="meta">Main result: {esc(et.direction_text(x.get("main_result")))}</p><ul>')
    for line in x["lines"]:
        sid = line["study"]
        title = titles.get(sid) or names.get(sid) or f"study {sid}"
        shown = esc(_cut(title, TITLE_MAX))
        ref = f'<a href="#s{sid}" title="{esc(title)}">{shown}</a>' if sid in titles else shown
        note = _cut(" ".join(str(line.get("note") or "").split()), NOTE_MAX)
        parts.append(f"<li>{ref} ({esc(et.direction_text(direction.get(sid, g.directions.get(sid))))}) — "
                     f"{esc(metric_text(line.get('reason')))}"
                     + (f"<p>{esc(note)}</p>" if note else "")
                     + f"<blockquote>{esc(quote_text(line.get('quote')))}</blockquote></li>")
    return "".join(parts) + "</ul></details>"


def question_item(g, titles: dict) -> str:
    """A question with 2+ studies: the question, its counts with the label after them (a secondary
    signal) and, for a difference question, its comparison; they open and close its study list (a
    closed <details>, no JS), then its explanation."""
    label = ""
    if g.label:
        symbol = et.LABEL_SYMBOLS.get(g.label, "")
        label = (f' · <span class="ee-label ee-label-{esc(g.label)}">'
                 f'<span aria-hidden="true">{esc(symbol)}</span> {esc(g.label)}</span>')
    compared = et.comparison_text(g)
    head = (f'<strong>{esc(g.text)}</strong> '
            f'<span class="meta">{esc(counts_text(g, best=False))}{label}</span>'
            + (f'<span class="ee-cmp">{esc(compared)}</span>' if compared else ""))
    head = study_rows(g, titles, head) or head
    return (f'<li class="ee-question ee-multi ee-q-{esc(g.label or "none")}" data-question="q{g.question_id}">'
            + head + question_explanation(g, titles) + "</li>")


def _study_count(sub) -> int:
    return len({sid for g in sub.questions for grp in g.groups for sid in grp}
               | {r.study_id for g in sub.questions for r in g.rows}
               | {o.row.study_id for o in sub.others})


def _subtopic_counts(sub, unsorted: bool) -> str:
    """'N questions · M studies' for a subtopic's summary; Unsorted has no questions."""
    n = len(sub.questions) + sum(1 for o in sub.others if o.question)
    m = _study_count(sub)
    studies = f'{m} {"study" if m == 1 else "studies"}'
    if unsorted or not n:
        return studies
    return f'{n} {"question" if n == 1 else "questions"} · {studies}'


def subtopic_index(t: Topic) -> str:
    """The subtopics as a contents list, each with its study count drawn as a rule in proportion
    to the largest subtopic. Empty with fewer than two subtopics."""
    counts = [(sub, _study_count(sub)) for sub in t.evidence]
    if len(counts) < 2:
        return ""
    top = max(n for _, n in counts) or 1
    items = "".join(
        f'<li><a href="#sub-{esc(sub.key)}">{esc(sub.name)}</a><span class="ee-n">{n}</span>'
        f'<span class="ee-extent" aria-hidden="true"><span style="--w:{round(100 * n / top)}%"></span>'
        "</span></li>" for sub, n in counts)
    return f'<nav class="ee-index" aria-label="Subtopics"><ol>{items}</ol></nav>'


def evidence_section(t: Topic) -> str:
    """'What the evidence says': per subtopic (seed order, then confirmed, then Unsorted) a closed
    <details> whose title opens one flat list of questions: those with 2+ studies first (labelled
    ones first, highlighted, each with counts, agreed explanations and a closed study list), then
    every question with one study (shown under it) and the studies not grouped yet. Empty without
    studies."""
    if not t.evidence and not t.waiting:
        return ""
    titles = {ts.entry.study_id: ts.entry.title for ts in t.studies}
    parts = ['<section id="ee-evidence">', "<h2>What the evidence says</h2>"]
    if any(g.label for sub in t.evidence for g in sub.questions):
        parts.append(f'<p class="meta ee-legend">{esc(LEGEND)}</p>')
    note = et.waiting_text(t.waiting)
    if note:
        parts.append(f'<p class="meta ee-waiting">{esc(note)}</p>')
    parts.append(subtopic_index(t))
    for sub in t.evidence:
        unsorted = sub.key == knowledge.UNSORTED
        parts.append(f'<div class="subtopic" id="sub-{esc(sub.key)}" data-subtopic="{esc(sub.key)}">'
                     f'<details class="ee-sub"><summary><h3>{esc(sub.name)}</h3>'
                     f'<span class="meta">{esc(_subtopic_counts(sub, unsorted))}</span></summary>')
        parts += ['<ul class="ee-questions">', *[question_item(g, titles) for g in sub.questions],
                  *[single_item(o, titles, unsorted) for o in sub.others], "</ul></details></div>"]
    parts.append("</section>")
    return "\n".join(parts)


def _report_list(t: Topic, from_dir: Path) -> list[str]:
    if not t.reports:
        return []
    items = []
    for link in t.reports:
        target = Path(link.path)
        html_sibling = target.with_suffix(".html")
        if target.suffix == ".md" and html_sibling.exists():
            target = html_sibling   # written after the topic model was built
        count = ("" if link.studies is None else
                 f' <span class="meta">{link.studies} {"study" if link.studies == 1 else "studies"}</span>')
        items.append(f'<li><a href="{esc(href(target, from_dir))}">{esc(link.label)}</a>{count}</li>')
    return [f'<details class="ee-reports"><summary>Reports ({len(t.reports)})</summary>'
            f"<ul>{''.join(items)}</ul></details>"]


def _topic_header(t: Topic, overview_href: str, report_href: str,
                  topic_href: str) -> tuple[str, list[str]]:
    """The masthead and the page head."""
    updated = clock.local_minute(t.generated_at)
    mast = masthead(crumbs([("Evidence Echo", overview_href), (t.topic_name, topic_href),
                            ("All studies", None)]),
                    [("Latest report", report_href)])
    return mast, [f'<header class="ee-head"><h1>{esc(t.topic_name)} — all studies</h1>',
                  f'<p class="meta">Updated {esc(updated)}</p></header>']


def _subtopic_keys(t: Topic) -> dict[int, tuple[str, str]]:
    """study id -> (subtopic key, name) for every study the evidence section lists, Unsorted too."""
    out = {}
    for sub in t.evidence:
        ids = [o.row.study_id for o in sub.others] + [r.study_id for g in sub.questions for r in g.rows]
        out.update({sid: (sub.key, sub.name) for sid in ids})
    return out


def filter_data(t: Topic) -> dict:
    """What the topic page's JS filters on; pos = position in the newest-first list."""
    rows = []
    where = _subtopic_keys(t)
    for pos, ts in enumerate(t.studies):
        e = ts.entry
        row = {"id": e.study_id, "pos": pos, "tier": e.tier,
               "flags": list(e.view.get("flags") or []), "relation": relation_of(e),
               "series": e.series, "disputed": bool(e.disputed),
               "text": f"{e.title} {e.summary}".lower(),
               "first_reported": ts.first_reported, "last_reported": ts.last_reported}
        key = e.subtopic["key"] if e.subtopic else (where.get(e.study_id) or (None,))[0]
        if key:
            row["subtopic"] = key
        if e.view.get("metric_slug"):
            row["metric"] = e.view["metric_slug"]
        names = e.view.get("entities") or []
        if names:
            row["entities"] = [slug for slug, _ in names]
            row["text"] += " " + " ".join(name.lower() for _, name in names)
        rows.append(row)
    return {"studies": rows}


def _select(name: str, label: str, first: str, options: list[tuple[str, str]]) -> str:
    opts = f'<option value="">{esc(first)}</option>' + "".join(
        f'<option value="{esc(v)}">{esc(text)}</option>' for v, text in options)
    return f'<label>{esc(label)} <select name="{name}">{opts}</select></label>'


def _metric_options(t: Topic, entries: list) -> list[tuple[str, str]]:
    """(slug, 'name (count)') for the metrics the page's studies use, in the topic's list order (Other
    last); empty unless two or more metrics are in use."""
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for e in entries:
        slug = e.view.get("metric_slug")
        if slug:
            counts[slug] = counts.get(slug, 0) + 1
            names[slug] = e.view.get("metric_name") or "Other"
    if len(counts) < 2:
        return []
    listed = [(slug, name) for slug, name in (t.metrics or []) if slug in counts]
    rest = sorted(((s, names[s]) for s in counts if s not in {x for x, _ in listed}),
                  key=lambda p: (p[0] == "other", p[1].casefold()))
    ordered = listed + rest
    ordered.sort(key=lambda p: p[0] == "other")           # Other is always last
    return [(slug, f"{name} ({counts[slug]})") for slug, name in ordered]


def _entity_options(entries: list) -> list[tuple[str, str]]:
    """(slug, 'name (count)') for the entities the page's studies are about, most studies first, then name;
    empty when no study has one."""
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for e in entries:
        for slug, name in e.view.get("entities") or []:
            counts[slug] = counts.get(slug, 0) + 1
            names[slug] = name
    ordered = sorted(counts, key=lambda s: (-counts[s], names[s].casefold()))
    return [(slug, f"{names[slug]} ({counts[slug]})") for slug in ordered]


def filters_form(t: Topic) -> str:
    """Hidden without JS; every control sits inside a <label>. Closed by default in a <details>;
    the reset button shows only while a field has a value (the page's script)."""
    entries = [ts.entry for ts in t.studies]
    tiers = list(TIER_CLASS) + (["?"] if any(e.tier not in TIER_CLASS for e in entries) else [])
    boxes = "".join(f'<label><input type="checkbox" name="tier" value="{x}" checked> '
                    f'{"Ungraded" if x == "?" else x}</label>' for x in tiers)
    used = {c for e in entries for c in (e.view.get("flags") or [])}
    flags = [c for c in grading.RED_FLAGS if c in used]
    relations = sorted({r for e in entries if (r := relation_of(e))})
    series = sorted({e.series for e in entries if e.series})
    listed = {(e.subtopic["key"], e.subtopic["name"]) for e in entries if e.subtopic}
    listed |= {(key, name) for sid, (key, name) in _subtopic_keys(t).items()
               if any(e.study_id == sid and not e.subtopic for e in entries)}
    subtopics = [(sub.key, sub.name) for sub in t.evidence if (sub.key, sub.name) in listed]
    metrics = _metric_options(t, entries)
    entities = _entity_options(entries)
    return ('<details class="ee-filter-box" id="ee-filter-box" hidden>'
            '<summary>Filter and sort <span id="ee-active"></span></summary>'
            '<form id="ee-filters" class="filters" hidden>'
            f"<fieldset><legend>Tier</legend>{boxes}</fieldset>"
            + _select("flag", "Red flags", "All studies",
                      [("*", "Any red flag")] + [(c, c.replace("_", " ")) for c in flags])
            + _select("relation", "Relation", "Any relation", [(r, r) for r in relations])
            + _select("series", "Series", "Any series", [(s, s) for s in series])
            + (_select("subtopic", "Subtopic", "Any subtopic", subtopics) if subtopics else "")
            + (_select("metric", "Metric", "Any metric", metrics) if metrics else "")
            + (_select("entity", "Entity", "Any entity", entities) if entities else "")
            + '<label><input type="checkbox" name="disputed"> Disputed only</label>'
            + '<label>Search <input type="search" name="q" placeholder="title or summary"></label>'
            + '<label>Sort <select name="sort"><option value="newest" selected>Newest</option>'
              '<option value="tier">Tier</option>'
              '<option value="first">First reported (oldest first)</option></select></label>'
            + '<button type="button" id="ee-reset" hidden>Reset filters</button>'
            + "</form></details>")


def _reported(ts: TopicStudy) -> str:
    return reported_value((ts.first_reported or "")[:10], (ts.last_reported or "")[:10], ts.reports)


def topic_page(t: Topic, overview_href: str, report_href: str, topic_href: str | None = None,
               reports_dir: Path | None = None) -> str:
    title = f"{t.topic_name} — all studies"
    mast, body = _topic_header(t, overview_href, report_href, topic_href or f"{t.topic_id}.html")
    body += _report_list(t, reports_dir or Path("."))
    n = len(t.studies)
    if not n:
        body.append('<p class="empty">No studies reported yet.</p>')
        return page(title, "\n".join(body), mast=mast)
    section = evidence_section(t)
    body += ([section] if section else []) + [
        '<section id="ee-all"><h2>Studies</h2>',
        f'<p id="ee-count" class="meta" aria-live="polite">{n} {"study" if n == 1 else "studies"}</p>',
        filters_form(t),
        '<div id="ee-studies">', *[card(ts.entry, _reported(ts)) for ts in t.studies], "</div>",
        "</section>"]
    script = json_script(filter_data(t), "ee-data") + f"\n<script>{html_assets.JS}</script>\n"
    return page(title, "\n".join(body), script, mast=mast)


def write_report(report: Report, topic: Topic, reports_dir, overview_dir, md_path) -> dict:
    """<md stem>.html next to the Markdown report, and the topic page <topic_id>.html."""
    md_path, reports_dir = Path(md_path), Path(reports_dir)
    report_path = md_path.with_suffix(".html")
    topic_path = reports_dir / f"{topic.topic_id}.html"
    overview_path = Path(overview_dir) / "overview.html"
    for other in (overview_path, report_path):
        if topic_path.resolve() == other.resolve():
            raise ValueError(f"the topic page {topic_path} would overwrite {other.name}; "
                             "give the topic its own reports_dir")
    _write(report_path, report_page(report, href(topic_path, report_path.parent),
                                    href(overview_path, report_path.parent)))
    _write(topic_path, topic_page(topic, href(overview_path, topic_path.parent),
                                  href(report_path, topic_path.parent), topic_path.name,
                                  topic_path.parent))
    return {"report": str(report_path), "topic": str(topic_path)}


def _topic_card(s, out_dir: Path) -> str:
    head = f'<section class="card"><h2>{esc(s.name)}</h2>'
    if s.error:
        return f'{head}<p class="warn">{esc("setup error: " + s.error)}</p></section>'
    if not s.report:
        return f'{head}<p class="meta">not run yet</p></section>'
    md = Path(s.report["path"])
    entries = s.report["entries"]
    n = len(entries)
    parts = [head, f'<p class="meta">{esc("last run " + clock.local_date(s.report["rendered_at"]))}'
                   f" · {n} new finding{'' if n == 1 else 's'}</p>"]
    top = overview_mod.rank(entries)
    if top:
        parts.append("<ul>" + "".join(f"<li>{tier_badge(e.get('tier'))} {esc(e.get('title'))}</li>"
                                      for e in top) + "</ul>")
    report_html, topic_html = md.with_suffix(".html"), md.parent / f"{s.id}.html"
    nav = [f'<a href="{esc(href(report_html, out_dir))}">Latest report</a>' if report_html.exists()
           else f'<a href="{esc(href(md, out_dir))}">Latest report (Markdown)</a>']
    if topic_html.exists():
        nav.append(f'<a href="{esc(href(topic_html, out_dir))}">All studies</a>')
    parts.append("<nav>" + "".join(nav) + "</nav></section>")
    return "\n".join(parts)


def overview_page(summaries: list, overview_dir: Path, now: datetime) -> str:
    """One card per topic: last run, top three findings, links to the latest report and topic page."""
    title = "Evidence Echo — Overview"
    body = [f'<header class="ee-head"><h1>{esc(title)}</h1>',
            f'<p class="meta">Updated {esc(clock.local_minute(now))}</p></header>']
    if not summaries:
        body.append('<p class="empty">No topics yet — run /evidence-echo:setup.</p>')
    else:
        body += ['<div class="ee-topics">', *[_topic_card(s, Path(overview_dir)) for s in summaries],
                 "</div>"]
    return page(title, "\n".join(body))


def write_overview(summaries: list, overview_dir, now: datetime) -> dict:
    """Write <overview_dir>/overview.html; returns {"overview": path}."""
    path = Path(overview_dir) / "overview.html"
    _write(path, overview_page(summaries, Path(overview_dir), now))
    return {"overview": str(path)}
