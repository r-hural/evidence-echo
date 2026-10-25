"""The Markdown report (SP5a: moved here from stages/report.py; the text is unchanged).

The text helpers (tier_label, label_extras, grading_texts, series_text) are shared with the
HTML and Obsidian renderers so every front end shows the same words.
"""
import re

from evidence_echo.render.evidence_text import label_text
from evidence_echo.render.model import Entry, Report, SeenEntry

MORE_COVERAGE = "## More coverage of earlier studies"
DISPUTED_HEADING = "## Disputed (verdict and skeptic disagree; both grades shown)"
NO_ITEMS = "*No confirmed evidence-backed items this run.*"


def tier_label(e: Entry) -> str:
    """The tier, "?" when ungraded; a disputed entry (not a no_quote-only one) adds the skeptic's."""
    tier = e.tier or "?"
    if e.disputed and not e.no_quote_only:  # double grade: the verdict's tier and the skeptic's
        doubt = e.skeptic_tiers
        tier = f"{tier} / skeptic: {', '.join(doubt)}" if doubt else f"{tier} / skeptic: disputed"
    return tier


def label_extras(e: Entry) -> list[str]:
    """Relation (unless unknown), "capped from X" and, for an undisputed entry, the gap note."""
    relation = e.view["provenance"]["relation"]
    extras = [relation if relation and relation != "unknown" else None,
              f"capped from {e.view['capped_from']}" if e.view["capped_from"] else None,
              None if e.disputed else e.view["gap_note"]]
    return [x for x in extras if x]


def fact_pairs(view: dict) -> list[tuple[str, str]]:
    """The grading facts as (name, value) pairs, each only when there is something to say."""
    pairs = []
    if view["flags"]:
        pairs.append(("Red flags", ", ".join(c.replace("_", " ") for c in view["flags"])))
    if view["provenance"]["conflict_of_interest"]:
        pairs.append(("Conflict of interest", view["provenance"]["conflict_of_interest"]))
    if view["missing_numbers"]:
        pairs.append(("Numbers not found in the text", ", ".join(view["missing_numbers"])))
    if view["judged_from"] == "excerpt":
        pairs.append(("Graded from", "the excerpt only"))
    if view.get("entities"):
        pairs.append(("About", ", ".join(name for _, name in view["entities"])))
    measured = " · ".join(str(x) for x in (view.get("metric_name"), view["metric"]) if x)
    if measured:
        pairs.append(("Measured", measured))
    if view["baseline"]:
        pairs.append(("Baseline", str(view["baseline"])))
    original = primary_text(view.get("primary"))
    if original:
        pairs.append(("Original", re.sub(r"^Original:? ", "", original)))
    return pairs


def grading_texts(view: dict) -> list[str]:
    """The grading lines' texts, without Markdown line breaks (the Original value as plain text)."""
    return [f"{n}: {plain(v) if n in ('Original', 'Measured', 'About') else v}" for n, v in fact_pairs(view)]


def plain(text: str) -> str:
    """Untrusted text (the primary line holds LLM-written strings) as plain text in Markdown:
    no link, code span or emphasis can form and no tag gets through (< becomes &lt;; & stays as
    written, so no entity shows in the .md). The HTML and Obsidian renderers escape the raw
    primary_text on their own."""
    for ch in ('\\', "`", "*", "_", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text.replace("<", "&lt;")


def no_tags(text) -> str:
    """Feed or LLM text (titles, summaries, skeptic notes) with no tag, link or image a viewer
    could render: \\ [ ] are backslash-escaped and < becomes &lt;. Emphasis stays as written."""
    text = str(text or "")
    for ch in ("\\", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text.replace("<", "&lt;")


_WEB = re.compile(r"https?://\S+", re.IGNORECASE)


def web_url(url) -> str | None:
    """An http(s) URL safe as a Markdown link target or <autolink>; anything else (javascript:,
    file:, spaces, < >, `) -> None. Brackets, parentheses and | are percent-encoded, so the URL
    can never close the link it sits in (Markdown report, Obsidian notes)."""
    text = str(url or "").strip()
    if not _WEB.fullmatch(text) or set(text) & set("<>`"):
        return None
    for char, code in (("(", "%28"), (")", "%29"), ("[", "%5B"), ("]", "%5D"), ("|", "%7C")):
        text = text.replace(char, code)
    text = re.sub(r":(?=:)", "%3A", text)
    text = re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", text)  # a lone % can never pair into %%
    while "%%" in text:  # final guard; unreachable after the line above
        text = text.replace("%%", "%25%")
    return text


def _link(link) -> str:
    """[name](url), or the bare name when the URL is not a safe web URL."""
    url = web_url(link.url)
    return f"[{link.name}]({url})" if url else link.name


def primary_text(primary) -> str | None:
    """The primary-source check line (SP3b-P), shared by the three renderers: an agreed verdict
    other than faithful, numbers not found in the original, or the original not reachable."""
    if not primary:
        return None
    if primary.get("status") == "unreachable":
        return "Original not reachable"
    if primary.get("status") == "not_checked":
        return "Original not checked (could not be fetched)"
    parts = []
    verdict = primary.get("verdict")
    if verdict and verdict != "faithful":
        note = " ".join(str(primary.get("note") or "").split())
        parts.append(verdict.replace("_", " ") + (f" — {note}" if note else ""))
    if primary.get("missing"):
        parts.append("numbers not found — " + ", ".join(primary["missing"]))
    return "Original: " + "; ".join(parts) if parts else None


def series_text(e: Entry) -> str | None:
    if not e.series:
        return None
    if e.editions:
        return f"Part of: {e.series} (editions {', '.join(e.editions)})"
    return f"Part of: {e.series}"


def dispute_text(e: Entry) -> str:
    prefix = f"Disputed: {e.dispute_reason}. " if e.dispute_reason else ""
    return f"{prefix}Skeptic's doubts: {'; '.join(e.skeptic_notes) or 'no reason given'}"


def metric_text(metric) -> str:
    return str(metric or "").replace("_", " ")


def independent_text(n: int) -> str:
    return f"{n} independent stud{'y' if n == 1 else 'ies'}"


def subtopic_lines(subtopic: dict) -> list[tuple[str, str]]:
    """The card's Subtopic pair, then a Question pair 'text · <label> (<n> independent studies)'
    when the study has a question (the label part only when the question has one)."""
    pairs = [("Subtopic", subtopic["name"])]
    if subtopic.get("question"):
        text = subtopic["question"]
        if subtopic.get("label"):
            text += f" · {label_text(subtopic['label'])} ({independent_text(subtopic['independent'])})"
        pairs.append(("Question", text))
    return pairs


def research_pairs(e: Entry) -> list[tuple[str, str]]:
    pairs = [("Research", e.research_timeframe or "unstated")]
    if e.published:
        pairs.append(("Published", e.published[:10]))
    return pairs


def _reports(n: int) -> str:
    return f"in {n} report{'' if n == 1 else 's'}"


def reported_value(first: str, last: str, n: int) -> str:
    if first == last:
        return f"{first} · {_reports(n)}"
    return f"first {first} · last {last} · {_reports(n)}"


def counts_text(group, best: bool = True) -> str:
    """A question's counts for the topic page and subtopic notes: '3 independent studies · 11 articles
    · best A' (the HTML page leaves the best tier out: best=False)."""
    n = group.articles
    text = f"{independent_text(group.independent)} · {n} article{'' if n == 1 else 's'}"
    return text + (f" · best {group.best_tier or '?'}" if best else "")


NOTE_MAX = 300   # characters of an explanation's note that are shown, "…" included
TITLE_MAX = 60   # characters of a study title in an explanation line's link


def _cut(text: str, limit: int) -> str:
    """Text of at most `limit` characters: cut on a word boundary (a hard cut when one word is
    longer) and end with "…" when anything was cut."""
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    if text[limit - 1] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip() + "…"


def quote_text(quote) -> str:
    """A checked quote as shown under its explanation: one line in curly quotes."""
    return f"“{' '.join(str(quote or '').split())}”"


def entry_lines(e: Entry) -> list[str]:
    label = " · ".join([tier_label(e)] + label_extras(e))
    lines = [f"### [{label}] {no_tags(e.title)}", "", no_tags(e.summary)]
    if e.disputed:  # the doubt sits right under the summary
        lines.append(f"> {no_tags(dispute_text(e))}")
    lines.append("")
    grading = [text + "  " for text in grading_texts(e.view)]
    if grading:
        lines += grading + [""]
    if e.subtopic:
        for name, value in subtopic_lines(e.subtopic):
            lines.append(f"{name}: {plain(value) if name == 'Question' else value}  ")   # LLM question text: plain
    series = series_text(e)
    if series:
        lines.append(f"*{series}*  ")
    lines += [f"*{name}: {value}*  " for name, value in research_pairs(e)]
    if web_url(e.primary_url):
        lines.append(f"Primary source: <{web_url(e.primary_url)}>  ")
    for link in e.coverage:
        lines.append(f"Coverage: {_link(link)}  ")
    lines.append("")
    return lines


def seen_line(s: SeenEntry) -> str:
    links = ", ".join(_link(c) for c in s.coverage)
    line = f"- {no_tags(s.first_title)} (first reported {s.first_reported}): {links}"
    if s.disputed:
        doubt = s.skeptic_tiers
        line += (f" — disputed: skeptic says {', '.join(doubt)}" if doubt else " — disputed by skeptic")
        notes = "; ".join(s.skeptic_notes)
        if notes:
            line += f"; {no_tags(notes)}"
    return line


def report_markdown(report: Report) -> str:
    lines = [f"# {report.topic_name} Report - {report.date}", ""]
    main = [line for e in report.new for line in entry_lines(e)]
    lines += main if main else [NO_ITEMS, ""]
    if report.disputed:
        lines += ["---", "", DISPUTED_HEADING, ""]
        lines += [line for e in report.disputed for line in entry_lines(e)]
    if report.seen:
        lines += ["---", "", MORE_COVERAGE, ""] + [seen_line(s) for s in report.seen] + [""]
    if report.source_warnings:
        lines += ["---", "", "*Source warnings: " + ", ".join(report.source_warnings) + "*"]
    return "\n".join(lines)
