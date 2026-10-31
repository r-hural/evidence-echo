"""The report model (SP5a): what a report and a topic page show, read from the store.

No I/O beyond the connection it is given. The renderers (markdown, html, obsidian) read these
dataclasses and never touch the database. Field names and order are a contract between
branches: extend a dataclass only append-only (a new field with a default, at the end).
"""
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from evidence_echo.core import grading, knowledge, store
from evidence_echo.core.urls import canonical_url
from evidence_echo.render import clock
from evidence_echo.stages.judge import TEXT_LIMIT

REPORT_STATES = ("verified", "embedded")              # what today's report shows
TOPIC_STATES = ("reported", "verified", "embedded")   # what the topic page reads
DISPUTE_REASONS = {"grade_gap": "grades two tiers apart",
                   "no_quote": "no quoted number found in the text"}
_QC_ORDER = {"ok": 0, "partial": 1, "none": 2}


@dataclass(frozen=True)
class Link:
    name: str
    url: str


@dataclass(frozen=True)
class Entry:                      # one study as shown in a report or on the topic page
    study_id: int
    title: str
    summary: str
    tier: str | None              # the study's tier
    disputed: bool
    skeptic_tiers: list[str]      # tiers the skeptic proposed on disputed items (report's double grade)
    no_quote_only: bool           # every disputed item is a no_quote dispute
    dispute_reason: str | None    # text: "grades two tiers apart" | "no quoted number found in the text"
    skeptic_notes: list[str]      # notes of disputed items
    view: dict                    # grading_view(study, group)
    series: str | None
    editions: list[str]
    research_timeframe: str | None
    published: str | None         # first item's publication date (ISO)
    primary_url: str | None
    coverage: list[Link]          # source name + item url, one per item
    canonical_name: str = ""      # SP5a-0 append-only extension: studies.canonical_name (Obsidian slug)
    subtopic: dict | None = None     # append-only extension: knowledge.study_questions() of its subtopic and question
    findings: list | None = None     # quotes mode: FindingView list (None in questions mode)
    findings_state: str | None = None   # quotes mode: "done" | "waiting" | "failed"
    found_by_search: str | None = None  # the search question, when an item came from a search source
    found_by_search_run: str | None = None  # that search's run id: the card links its answer page


@dataclass(frozen=True)
class FindingView:               # one finding of a study (quotes mode)
    position: int
    text: str
    quote: str
    quote_checked: bool
    metric_slug: str | None
    metric_name: str | None
    measure: str | None


@dataclass(frozen=True)
class FindingRow:                # one line of the quotes-mode findings list
    study_id: int
    name: str
    tier: str | None
    labels: list      # grade labels (texts), the topic's order
    date: str | None
    reported: bool       # True when `date` is a first-reported fallback
    finding: FindingView | None   # None: the study has no findings (yet)
    state: str           # "done" | "waiting" | "failed"


@dataclass(frozen=True)
class FindingsSubtopic:
    key: str                   # the subtopic's slug, or knowledge.UNSORTED
    subtopic_id: int | None
    name: str
    rows: list


@dataclass(frozen=True)
class SeenEntry:                  # "More coverage of earlier studies"
    study_id: int
    first_title: str
    first_reported: str           # ISO date
    coverage: list[Link]
    disputed: bool
    skeptic_tiers: list[str]
    skeptic_notes: list[str]


@dataclass(frozen=True)
class Report:
    topic_id: str
    topic_name: str
    date: str                     # YYYY-MM-DD
    new: list[Entry]              # main section, in today's order
    disputed: list[Entry]
    seen: list[SeenEntry]
    source_warnings: list[str]    # the strings the Markdown footer joins
    label: str = ""                # append-only: this report's name in the topic's report list
    disclaimer: str | None = None  # append-only: the topic's grading disclaimer
    mode: str = "questions"        # append-only: the topic's grouping mode


@dataclass(frozen=True)
class ReportLink:                 # one line of the topic page's report list
    label: str
    path: str                     # the .html sibling when it exists, else the recorded .md
    studies: int | None = None    # append-only: new studies the report showed (None: not counted)


@dataclass(frozen=True)
class TopicStudy:
    entry: Entry                  # latest title and summary, current tier and grading view
    first_reported: str           # ISO date
    last_reported: str
    reported_on: list[str]        # dates of every report that showed it, oldest first
    reports: int = 0              # distinct reports that showed it


@dataclass(frozen=True)
class SourceSummary:
    name: str
    study_ids: list[int]
    disputes: int                 # disputed items from this source
    red_flags: int                # items from this source with at least one red flag


@dataclass(frozen=True)
class SeriesSummary:
    name: str
    editions: list[tuple[str, int]]   # (edition label, study id), oldest first


@dataclass(frozen=True)
class Topic:
    topic_id: str
    topic_name: str
    generated_at: str             # ISO timestamp
    studies: list[TopicStudy]     # every reported, not merged-away study; newest last_reported first
    sources: list[SourceSummary]  # sorted by name
    series: list[SeriesSummary]   # sorted by name
    evidence: list = field(default_factory=list)   # questions: knowledge.SubtopicEvidence, display order
    waiting: int = 0              # questions: studies that wait to be grouped
    reports: list = field(default_factory=list)   # ReportLink, newest first
    metrics: list = field(default_factory=list)   # metrics: (slug, name) of the topic's active metrics, list order
    disclaimer: str | None = None  # append-only: the topic's grading disclaimer
    mode: str = "questions"       # append-only: the topic's grouping mode
    findings: list = field(default_factory=list)   # quotes mode: FindingsSubtopic, display order
    finding_count: int = 0        # quotes mode: findings listed on the page
    searches: list = field(default_factory=list)   # search.list_runs rows of the topic, newest first
    search_sources: frozenset = frozenset()   # names of the "found by search" sources that have studies


def report_labels(rows: list[dict]) -> dict[int, str]:
    """Report id -> label: the local date, plus " (N)" for the N-th (N >= 2) report of the topic
    on that local date, counted in rendered_at, id order."""
    seen: dict[str, int] = {}
    labels = {}
    for row in sorted(rows, key=lambda r: (r["rendered_at"], r["id"])):
        day = clock.local_date(row["rendered_at"])
        seen[day] = seen.get(day, 0) + 1
        labels[row["id"]] = day if seen[day] == 1 else f"{day} ({seen[day]})"
    return labels


def report_links(rows: list[dict]) -> list[ReportLink]:
    """The report list, newest first; a report whose files are both gone is left out."""
    labels = report_labels(rows)
    links = []
    for row in sorted(rows, key=lambda r: (r["rendered_at"], r["id"]), reverse=True):
        md = Path(row["path"])
        html = md.with_suffix(".html")
        if html.exists():
            links.append(ReportLink(labels[row["id"]], str(html), row.get("studies")))
        elif md.exists():
            links.append(ReportLink(labels[row["id"]], str(md), row.get("studies")))
    return links


def _json(raw, default):
    try:
        value = json.loads(raw) if raw else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _primary_rank(row: dict) -> int:
    """0 an agreed misrepresentation, 1 numbers not found, 2 unreachable, 3 the rest."""
    if row["status"] == "agreed" and row.get("verdict") not in (None, "faithful"):
        return 0
    if row["status"] != "unreachable" and _json(row.get("missing"), []):
        return 1
    return 2 if row["status"] == "unreachable" else 3


def primary_view(group: list[dict], checks: dict) -> dict | None:
    """The primary-source check (SP3b-P) shown for a study: its items' most telling check row
    (lowest item id on ties); None when no item of the group was checked."""
    rows = [checks[g["id"]] for g in group if g["id"] in checks]
    if not rows:
        return None
    row = min(rows, key=lambda r: (_primary_rank(r), r["item_id"]))
    agreed = row["status"] == "agreed"
    return {"numbers": row.get("numbers"), "missing": _json(row.get("missing"), []),
            "verdict": row.get("verdict") if agreed else None,
            "note": row.get("note") if agreed else None, "status": row["status"]}


def grading_view(study: dict, group: list[dict], checks: dict | None = None, metric: dict | None = None,
                 entities: list[dict] | None = None, rules: dict | None = None) -> dict:
    """What SP2 recorded for a study, from its items: shared by every renderer and report emit.
    checks: item id -> primary_checks row (SP3b-P); without it "primary" is None.
    metric: the study's resolved active metric (store.study_metric); metric_slug is its slug, metric_name
    its name unless it is Other (the card then shows the free text alone).
    entities: the study's entity rows (store.study_entities); the view lists (slug, name) pairs.
    rules: the topic's effective grading block (grading.rules_of); grade_labels are its labels for the
    study's study_type, peer_review and text_basis; cap_reasons are the creating item's, as display text."""
    codes = {c for g in group for c in _json(g.get("red_flags"), [])}
    quote_checks = [g["quote_check"] for g in group if g.get("quote_check")]
    graded_from = [g["judged_from"] for g in group if g.get("judged_from")]
    missing = []
    for g in group:
        if g.get("quote_check") == "partial":
            facts = _json(g.get("facts"), {})
            for n in grading.missing_numbers(_json(g.get("quotes"), []), grading.fact_numbers(facts),
                                             (g.get("full_text") or "")[:TEXT_LIMIT]):
                if n not in missing:
                    missing.append(n)
    # the label notes come from the item that created the study: a joined item's grades
    # never set the study's tier (none for a legacy study without a creating item)
    creator = next((g for g in group if g["id"] == study.get("created_by_item")), None)
    gap_note = capped_from = None
    cap_reasons: list[str] = []
    if creator:
        capped_from = creator.get("capped_from")
        reasons = _json(creator.get("cap_reasons"), [])
        cap_reasons = [grading.cap_text([r]) for r in reasons if isinstance(r, str)] if capped_from else []
        jt, st = creator.get("judge_tier"), creator.get("skeptic_tier")
        if (not creator.get("disputed") and jt and st and jt != st
                and abs(grading.TIERS.index(jt) - grading.TIERS.index(st)) < 2):  # >=2: a ruling
            gap_note = f"judge: {jt}" if creator["evidence_tier"] != jt else f"skeptic: {st}"
    return {
        "flags": [c for c in grading.ALL_RED_FLAGS if c in codes],
        "provenance": {k: study.get(k) for k in ("conducted_by", "relation", "conflict_of_interest")},
        "quote_check": max(quote_checks, key=_QC_ORDER.get) if quote_checks else None,
        "missing_numbers": missing,
        "capped_from": capped_from,
        "cap_reasons": cap_reasons,
        "grade_labels": grading.grade_labels({f: study.get(f) for f in grading.FACT_VALUES},
                                             rules or grading.generic()),
        "judged_from": (None if not graded_from else
                        "excerpt" if all(j == "excerpt" for j in graded_from) else "full_text"),
        "metric": study.get("metric"),
        "metric_slug": metric["slug"] if metric else None,
        "metric_name": metric["name"] if metric and metric["slug"] != store.OTHER_METRIC else None,
        "baseline": study.get("baseline"),
        "entities": [(e["slug"], e["name"]) for e in entities or []],
        "gap_note": gap_note,
        "primary": primary_view(group, checks or {}),
    }


def skeptic_tiers(group: list[dict]) -> list[str]:
    """Tiers the skeptic proposed where it disagreed (empty: it disagreed without one)."""
    return sorted({g["skeptic_tier"] for g in group if g["disputed"] and g["skeptic_tier"]})


def _skeptic_notes(group: list[dict]) -> list[str]:
    return [g["skeptic_note"] for g in group if g["disputed"] and g["skeptic_note"]]


def series_name(conn, study: dict) -> str | None:
    return store.series_name(conn, study.get("series_id"))


def verified_by_study(conn) -> dict[int, list[dict]]:
    groups: dict[int, list[dict]] = {}
    for state in REPORT_STATES:
        for it in store.items_in_state(conn, state):
            groups.setdefault(it["study_id"], []).append(it)
    return groups


def split(conn) -> tuple[dict, dict, dict]:
    """(new groups, seen-before groups, first reports) - seen-before = an earlier report showed it."""
    first = store.first_reports(conn)
    groups = verified_by_study(conn)
    new = {sid: g for sid, g in groups.items() if sid not in first}
    seen = {sid: g for sid, g in groups.items() if sid in first}
    return new, seen, first


def not_checked_view(conn, study: dict, group: list[dict], checks: dict) -> dict | None:
    """SP3b-P: the primary field of a study whose original could not be fetched yet: an item
    without a check row, a study original other than the item's own URL, and a failed fetch row
    (computed at render time, nothing stored). None otherwise."""
    raw = (study.get("primary_url") or "").strip()
    canon = canonical_url(raw) if raw else None
    src = store.primary_source(conn, canon) if canon else None
    if not src or src["status"] != "failed":
        return None
    for g in group:
        if g["id"] not in checks and canon != (g.get("canonical_url") or canonical_url(g["url"])):
            return {"numbers": None, "missing": [], "verdict": None, "note": None,
                    "status": "not_checked"}
    return None


def search_questions(topic) -> dict[str, str]:
    """Source name -> the search question, for the topic's sources a search added (found_by "search")."""
    return {s["name"]: s.get("search_question") or "" for s in getattr(topic, "sources", [])
            if s.get("found_by") == "search"}


def search_runs(topic) -> dict[str, str]:
    """Source name -> the run id of the search that added it (found_by "search")."""
    return {s["name"]: s["search_run"] for s in getattr(topic, "sources", [])
            if s.get("found_by") == "search" and s.get("search_run")}


def entry(conn, study: dict, group: list[dict], title: str, summary: str,
          subtopic: dict | None = None, rules: dict | None = None,
          search_questions: dict[str, str] | None = None, search_runs: dict[str, str] | None = None) -> Entry:
    disputed = [g for g in group if g["disputed"]]
    checks = store.primary_checks_for(conn, [g["id"] for g in group])
    view = grading_view(study, group, checks, store.study_metric(conn, study),
                        store.study_entities(conn, study["id"]), rules)
    if view["primary"] is None:
        view["primary"] = not_checked_view(conn, study, group, checks)
    return Entry(
        study_id=study["id"], title=title, summary=summary, tier=study["evidence_tier"],
        disputed=bool(disputed), skeptic_tiers=skeptic_tiers(group),
        no_quote_only=bool(disputed) and all(g.get("dispute_reason") == "no_quote"
                                              for g in disputed),
        dispute_reason=next((DISPUTE_REASONS[g["dispute_reason"]] for g in disputed
                             if g.get("dispute_reason") in DISPUTE_REASONS), None),
        skeptic_notes=_skeptic_notes(group),
        view=view,
        series=series_name(conn, study), editions=store.study_lists(study)[0],
        research_timeframe=study["research_timeframe"],
        published=group[0]["published"] if group else None,
        primary_url=study["primary_url"],
        coverage=[Link(g["source_name"], g["url"]) for g in group],
        canonical_name=study["canonical_name"] or "", subtopic=subtopic,
        found_by_search=next((search_questions[g["source_name"]] for g in group
                              if g["source_name"] in (search_questions or {})), None),
        found_by_search_run=next((search_runs[g["source_name"]] for g in group
                                  if g["source_name"] in (search_runs or {})), None))


def entry_order(e: Entry) -> tuple:
    """Today's report order: series studies by series name, then the rest; study id within."""
    return (e.series is None, e.series or "", e.study_id)


def source_warnings(conn, topic) -> list[str]:
    names = {s["name"] for s in getattr(topic, "sources", [])}
    states = [s for s in store.all_source_states(conn) if s["name"] in names]
    failing = [f"{s['name']} (failing since {(s['failing_since'] or '')[:10]} — check or remove)"
               for s in states if s["consecutive_errors"] >= store.FAILING_AFTER]
    erring = [f"{s['name']} ({(s['last_error'] or '')[:40]})" for s in states
              if s["last_error"] and s["consecutive_errors"] < store.FAILING_AFTER]
    return failing + erring


def build_report(conn, topic, entries_by_study: dict, now: datetime, mode: str = "questions") -> Report:
    """Today's report. entries_by_study: study id -> the accepted {"title", "summary"} entry;
    a study without one shows its canonical name and findings. In quotes mode every entry carries
    its findings and no question."""
    new, seen, first = split(conn)
    quotes = mode != "questions"
    if mode == "findings":
        subtopics = findings_mode_subtopics(conn, knowledge.evidence(conn, "findings"))
    else:
        subtopics = quotes_subtopics(conn) if quotes else knowledge.study_questions(knowledge.evidence(conn))
    by_study = store.findings_by_study(conn) if quotes else {}
    rules = grading.rules_of(topic)
    asked, runs = search_questions(topic), search_runs(topic)
    built = []
    for study_id, group in new.items():
        study = store.get_study(conn, study_id)
        e = entries_by_study.get(study_id)
        title = e["title"] if e else (study["canonical_name"] or "(untitled)")
        summary = e["summary"] if e else (study["findings"] or "")
        built.append(with_findings(entry(conn, study, group, title, summary, subtopics.get(study_id), rules, asked, runs),
                                   study, by_study) if quotes else
                     entry(conn, study, group, title, summary, subtopics.get(study_id), rules, asked, runs))
    built.sort(key=entry_order)
    seen_entries = [SeenEntry(
        study_id=sid, first_title=first[sid]["title"],
        first_reported=clock.local_date(first[sid]["rendered_at"]),
        coverage=[Link(g["source_name"], g["url"]) for g in group],
        disputed=any(g["disputed"] for g in group), skeptic_tiers=skeptic_tiers(group),
        skeptic_notes=_skeptic_notes(group)) for sid, group in seen.items()]
    label = report_labels([*store.list_reports(conn),
                           {"id": float("inf"), "rendered_at": now.isoformat()}])[float("inf")]
    return Report(topic_id=topic.id, topic_name=topic.name, date=clock.local_date(now), label=label,
                  new=[e for e in built if not e.disputed],
                  disputed=[e for e in built if e.disputed],
                  seen=seen_entries, source_warnings=source_warnings(conn, topic),
                  disclaimer=rules.get("disclaimer"), mode=mode)


# --- quotes mode: findings instead of questions ---------------------------------------------------
def findings_state(study: dict, rows: list) -> str:
    """"done" when the study has findings, "failed" when the stage gave up on it, else "waiting"."""
    if rows:
        return "done"
    return "failed" if str(study.get("findings_version") or "").endswith(":failed") else "waiting"


def finding_view(row: dict) -> FindingView:
    return FindingView(position=row["position"], text=row["text"], quote=row["quote"],
                       quote_checked=bool(row["quote_checked"]), metric_slug=row["metric_slug"],
                       metric_name=row["metric_name"], measure=row["measure"])


def with_findings(e: Entry, study: dict, by_study: dict) -> Entry:
    rows = by_study.get(study["id"], [])
    return replace(e, findings=[finding_view(r) for r in rows], findings_state=findings_state(study, rows))


def quotes_subtopics(conn) -> dict[int, dict]:
    """study id -> the subtopic line a card shows in quotes mode (no question); Unsorted studies have none."""
    out = {}
    placed, _ = knowledge.placed_studies(conn)
    for study, subtopic, _question in placed:
        if subtopic is not None:
            out[study["id"]] = {"key": subtopic["slug"], "name": subtopic["name"], "question": None,
                                "label": None, "independent": 0, "echo": None}
    return out


def findings_mode_subtopics(conn, evidence: list) -> dict[int, dict]:
    """study id -> the subtopic line a card shows in findings mode: the study's findings subtopic (its
    position-1 finding's), no single question, and `questions`, one entry per question its findings joined
    (knowledge.finding_questions); `waiting` is True while some finding is not grouped yet."""
    asked = knowledge.finding_questions(evidence)
    waiting = {r.study_id for e in evidence for r in e.waiting}
    out = {}
    for s in store.active_studies(conn):
        own = store.study_subtopic(conn, s, "findings")
        questions = asked.get(s["id"], [])
        if own is None and not questions:
            continue
        key, name = (own["slug"], own["name"]) if own else (knowledge.UNSORTED, knowledge.UNSORTED_NAME)
        out[s["id"]] = {"key": key, "name": name, "question": None, "label": None, "independent": 0,
                        "echo": None, "questions": questions, "waiting": s["id"] in waiting}
    return out


def findings_subtopics(conn, rules: dict | None = None) -> list[FindingsSubtopic]:
    """Every counting study in its study-level subtopic (the subtopic order of knowledge.evidence, Unsorted
    last, empty ones left out); one row per finding, or one row with finding=None and the study's state.
    Studies newest first by the row date (undated last, then study id descending)."""
    rules = rules or grading.generic()
    placed, _ = knowledge.placed_studies(conn)
    by_study = store.findings_by_study(conn)
    published, reported = store.study_first_published(conn), store.first_reports(conn)
    buckets: dict = {}
    for study, subtopic, _question in placed:
        bucket = buckets.setdefault(subtopic["slug"] if subtopic else knowledge.UNSORTED,
                                    {"subtopic": subtopic, "studies": []})
        bucket["studies"].append(study)
    out = []
    for key, bucket in sorted(buckets.items(), key=lambda e: (2, 0) if e[1]["subtopic"] is None
                              else store.subtopic_order(e[1]["subtopic"])):
        subtopic = bucket["subtopic"]
        dated = []
        for s in bucket["studies"]:
            date, is_reported = knowledge.row_date(s, published, reported)
            dated.append((s, date, is_reported))
        with_date = sorted([d for d in dated if d[1]], key=lambda d: (d[1], d[0]["id"]), reverse=True)
        undated = sorted([d for d in dated if not d[1]], key=lambda d: d[0]["id"], reverse=True)
        rows = []
        for s, date, is_reported in with_date + undated:
            labels = grading.grade_labels({f: s.get(f) for f in grading.FACT_VALUES}, rules)
            found = by_study.get(s["id"], [])
            state = findings_state(s, found)
            base = dict(study_id=s["id"], name=s["canonical_name"] or "", tier=s.get("evidence_tier"),
                        labels=labels, date=date, reported=is_reported, state=state)
            if found:
                rows += [FindingRow(finding=finding_view(f), **base) for f in found]
            else:
                rows.append(FindingRow(finding=None, **base))
        out.append(FindingsSubtopic(key=key, subtopic_id=subtopic["id"] if subtopic else None,
                                    name=subtopic["name"] if subtopic else knowledge.UNSORTED_NAME, rows=rows))
    return out


def _series_editions(studies: list[TopicStudy]) -> list[SeriesSummary]:
    by_name: dict[str, list[tuple]] = {}
    for ts in studies:
        e = ts.entry
        if e.series is None:
            continue
        rows = by_name.setdefault(e.series, [])
        if e.editions:
            rows += [((ts.first_reported, label, e.study_id), (label, e.study_id))
                     for label in e.editions]
        else:  # no edition label: the study's title stands in for it
            rows.append(((ts.first_reported, e.title, e.study_id), (e.title, e.study_id)))
    return [SeriesSummary(name, [pair for _, pair in sorted(rows)])
            for name, rows in sorted(by_name.items())]


def build_topic(conn, topic, now: datetime, mode: str = "questions", searches=()) -> Topic:
    """Every study a report showed, resolved through merges; see the SP5a spec, The model.
    In quotes mode the page lists findings: no evidence, no waiting studies, no questions. In findings
    mode it lists questions built from findings (each study under every question its findings joined)."""
    quotes = mode != "questions"
    history: dict[int, list[dict]] = {}
    for row in store.report_history(conn):
        history.setdefault(store.resolve_study(conn, row["study_id"]), []).append(row)
    evidence = knowledge.evidence(conn, "findings") if mode == "findings" else [] if quotes else knowledge.evidence(conn)
    if mode == "findings":
        subtopics = findings_mode_subtopics(conn, evidence)
    else:
        subtopics = quotes_subtopics(conn) if quotes else knowledge.study_questions(evidence)
    rules = grading.rules_of(topic)
    asked, runs = search_questions(topic), search_runs(topic)
    by_study = store.findings_by_study(conn) if quotes else {}
    ranked, groups = [], {}
    for sid, rows in history.items():
        study = store.get_study(conn, sid)
        if study is None:
            continue
        group = store.study_items(conn, sid, TOPIC_STATES)
        summarised = [r for r in rows if r["summary"] is not None]
        if summarised:
            title, summary = summarised[-1]["title"], summarised[-1]["summary"]
        else:
            title, summary = rows[-1]["title"], study["findings"] or ""
        dates = list(dict.fromkeys(clock.local_date(r["rendered_at"]) for r in rows))
        e = entry(conn, study, group, title, summary, subtopics.get(sid), rules, asked, runs)
        ts = TopicStudy(entry=with_findings(e, study, by_study) if quotes else e,
                        first_reported=dates[0], last_reported=dates[-1], reported_on=dates,
                        reports=len({r["report_id"] for r in rows}))
        ranked.append(((rows[-1]["rendered_at"], rows[-1]["report_id"], sid), ts))
        groups[sid] = group
    studies = [ts for _, ts in sorted(ranked, key=lambda t: t[0], reverse=True)]
    by_source: dict[str, dict] = {}
    for sid, group in groups.items():
        for g in group:
            s = by_source.setdefault(g["source_name"], {"ids": set(), "disputes": 0, "flags": 0})
            s["ids"].add(sid)
            s["disputes"] += 1 if g["disputed"] else 0
            s["flags"] += 1 if _json(g.get("red_flags"), []) else 0
    sources = [SourceSummary(name, sorted(v["ids"]), v["disputes"], v["flags"])
               for name, v in sorted(by_source.items())]
    listed = findings_subtopics(conn, rules) if mode == "quotes" else []
    if mode == "findings":
        count = sum(len(by_study.get(ts.entry.study_id, [])) for ts in studies)
    else:
        count = sum(1 for s in listed for r in s.rows if r.finding is not None)
    waiting = (0 if mode == "quotes" else knowledge.waiting_count(conn, "findings") if mode == "findings"
               else knowledge.waiting_count(conn))
    return Topic(topic_id=topic.id, topic_name=topic.name, generated_at=now.isoformat(),
                 studies=studies, sources=sources, series=_series_editions(studies),
                 evidence=evidence, waiting=waiting,
                 reports=report_links(store.list_reports(conn)),
                 metrics=[(m["slug"], m["name"]) for m in store.active_metrics(conn)],
                 disclaimer=rules.get("disclaimer"), mode=mode,
                 findings=listed, finding_count=count, searches=list(searches),
                 search_sources=frozenset(v.name for v in sources if v.name in asked))
