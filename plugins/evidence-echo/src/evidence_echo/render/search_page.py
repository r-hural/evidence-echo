"""The answer page of a search: the question, what the graded studies say, the study cards of a report,
and what was turned down. Built from the report model (render.model) and the report card renderers; pure."""
from dataclasses import dataclass

from evidence_echo.render import html as h
from evidence_echo.render import markdown as md

NO_STUDIES = "No candidate was graded as a study."


@dataclass(frozen=True)
class SearchPage:
    run: str
    topic_id: str
    topic_name: str
    question: str
    date: str                       # local date the search started
    status: str                     # open | kept | dropped
    status_date: str | None         # local date of the last status change
    candidates: int
    graded: int
    rejected: list[tuple[str, str, str]]   # (url, title, reason)
    entries: list                   # the report model's new + disputed entries
    summary: str
    queries: list[str]
    disclaimer: str | None
    kept: int | None                # studies kept, once the run is kept
    age_note: str | None = None     # knowledge.age_note of the studies' data years: old data, said by the engine


def status_text(p: SearchPage) -> str:
    if p.status == "kept":
        some = f" ({p.kept} of {p.graded} studies)" if p.kept is not None and p.kept < p.graded else ""
        return f"Kept on {p.status_date or p.date}{some}"
    if p.status == "dropped":
        return "Dropped"
    return "Not in the topic yet"


def _meta(p: SearchPage) -> str:
    return (f"{p.date} · Search: {p.candidates} candidates, {p.graded} studies graded, "
            f"{len(p.rejected)} rejected")


def _paragraphs(text: str) -> list[str]:
    return [" ".join(part.split()) for part in (text or "").split("\n\n") if part.strip()]


def search_html(p: SearchPage, topic_href: str, overview_href: str) -> str:
    mast = h.masthead(h.crumbs([("Evidence Echo", overview_href), (p.topic_name, topic_href), ("Search", None)]),
                      [("All studies", topic_href)])
    body = [h.disclaimer_html(p.disclaimer),
            f'<header class="ee-head"><h1>{h.esc(p.question)}</h1>',
            f'<p class="meta">{h.esc(_meta(p))}</p>',
            f'<p class="meta">{h.esc(status_text(p))}</p></header>',
            "<section>", "<h2>What these say</h2>",
            *[f"<p>{h.esc(x)}</p>" for x in _paragraphs(p.summary)],
            *([f'<p class="ee-warn">{h.esc(p.age_note)}</p>'] if p.age_note else []), "</section>",
            h.ratings_chart(p.entries), "<section>", "<h2>Studies</h2>"]
    body += [h.card(e, searches_href="") for e in p.entries] if p.entries else [f'<p class="empty">{h.esc(NO_STUDIES)}</p>']
    body.append("</section>")
    if p.rejected:
        rows = "".join(f'<li>{h.link(url, title)}<br><span class="meta">{h.esc(url)} — {h.esc(reason)}</span></li>'
                       for url, title, reason in p.rejected)
        body.append(f'<details class="ee-rejected"><summary>Not studies ({len(p.rejected)})</summary>'
                    f"<ul>{rows}</ul></details>")
    if p.queries:
        body += ["<section>", "<h2>Queries</h2>", "<ul>", *[f"<li>{h.esc(q)}</li>" for q in p.queries],
                 "</ul>", "</section>"]
    return h.page(f"{p.question} - Search - {p.topic_name}", "\n".join(body), mast=mast)


def search_markdown(p: SearchPage) -> str:
    lines = [f"# {md.no_tags(p.question)}", "", _meta(p), "", status_text(p), ""]
    if p.disclaimer:
        lines += [f"*{p.disclaimer}*", ""]
    lines += ["## What these say", ""]
    for x in _paragraphs(p.summary):
        lines += [md.no_tags(x), ""]
    if p.age_note:
        lines += [f"**{md.no_tags(p.age_note)}**", ""]
    lines += ["## Studies", ""]
    body = [line for e in p.entries for line in md.entry_lines(e)]
    lines += body if body else [f"*{NO_STUDIES}*", ""]
    if p.rejected:
        lines += [f"## Not studies ({len(p.rejected)})", ""]
        for url, title, reason in p.rejected:
            target = md.web_url(url)
            shown = md.no_tags(title)
            lines.append(f"- [{shown}]({target}): {md.no_tags(reason)}" if target
                         else f"- {shown}: {md.no_tags(reason)}")
        lines.append("")
    if p.queries:
        lines += ["## Queries", "", *[f"- {md.no_tags(q)}" for q in p.queries], ""]
    return "\n".join(lines)
