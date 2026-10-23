"""Knowledge model (SP3b): fixed vocabularies, and the questions evidence model per subtopic.

Every LLM answer in SP3b picks from these tuples (or, for subtopics and metrics, from the topic's
own lists in the database); nothing outside them is stored.
"""

PAIR_REASONS = ("different_population", "different_timeframe", "different_measure",
                "different_intervention", "method_quality", "genuine_conflict", "not_comparable")
PRIMARY_VERDICTS = ("faithful", "overstated", "direction_flipped", "wrong_metric", "cherry_picked")
QUESTION_DIRECTIONS = ("up", "down", "no_change", "mixed")   # read on a question's measure
QUESTION_KINDS = ("difference", "level", "capability")
KIND_DEFINITIONS = {
    "difference": ("Is the measure different under A than under B? Experiments, case studies with "
                   "before/after, A/B or multi-arm comparisons (the question names the two arms it "
                   "compares), correlations, effects of an external event, measurement gaps."),
    "level": ("How much / how often / what share is the measure? Prevalence, shares, frequencies, "
              "benchmarks, rankings of options, trends over time without a tested cause."),
    "capability": ("Can system S do X / how does S behave? Pass rates of tools, agents or engines, "
                   "audits of how a system works."),
}
QUESTION_COMPARISON_MAX = 120
QUESTION_TEXT_MAX = 120
QUESTION_MEASURE_MAX = 80

# --- study groups: independent groups, labels, echoes (appended; the vocabularies above are SP3b-0's) ---
from dataclasses import dataclass, field, replace  # noqa: E402  (appended section)

UNSORTED = "~unsorted"       # the key of "Unsorted" (subtopic slugs never contain "~")
UNSORTED_NAME = "Unsorted"
_TIER_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}
_ACTIVE = ("seeded", "confirmed")


def _rank(study: dict) -> tuple:
    """Representative order: the best tier, then the newest."""
    return (_TIER_RANK.get(study.get("evidence_tier"), 9), -study["id"])


def _org(study: dict) -> str | None:
    name = " ".join(str(study.get("conducted_by") or "").split()).casefold()
    return name or None


def _resolve(subtopics: dict, subtopic_id) -> dict | None:
    """The active subtopic an id ends at after merges and maps; None = Unsorted."""
    seen = set()
    while subtopic_id in subtopics and subtopic_id not in seen:
        seen.add(subtopic_id)
        subtopic = subtopics[subtopic_id]
        if subtopic["merged_into"] is None:
            return subtopic if subtopic["status"] in _ACTIVE else None
        subtopic_id = subtopic["merged_into"]
    return None


def _studies_text(n: int) -> str:
    return f"{n} other independent stud{'y' if n == 1 else 'ies'}"


def _group_direction(directions: list) -> str:
    """A group's direction: its members' (representative first) when they all agree, else "mixed"."""
    return directions[0] if len(set(directions)) == 1 else "mixed"


def _echoes(groups: list, dirs: list, articles: int, kind: str | None = "difference") -> dict:
    """Echo lines; a member's echo compares its group's direction with the other groups'. Only
    difference questions corroborate: a level or capability question has no direction to agree on."""
    out = {}
    for gi, members in enumerate(groups):
        for s in members:
            if len(groups) == 1:
                if articles > 1:
                    out[s["id"]] = f"1 study · {articles} articles"
                continue
            if kind != "difference" or dirs[gi] is None:   # no direction to agree on, or not read yet
                continue
            others = sum(1 for gj, d in enumerate(dirs) if gj != gi and d == dirs[gi])
            if others:
                out[s["id"]] = f"corroborated by {_studies_text(others)}"
    return out


# --- wave 4a: data date -------------------------------------------------------------------------
import datetime as _dt  # noqa: E402  (appended section)
import re as _re  # noqa: E402

from evidence_echo.render import clock  # noqa: E402

DATA_DATE_FIRST_YEAR = 1990
_DATA_DATE = _re.compile(r"^\s*(\d{4})(?:[-/.](\d{1,2}))?\s*$")


def normalise_data_date(raw, today: "_dt.date | None" = None) -> "str | None":
    """'2024-5' -> '2024-05'; only YYYY or YYYY-MM in 1990..next year, else None."""
    m = _DATA_DATE.match(raw) if isinstance(raw, str) else None
    if not m:
        return None
    year = int(m.group(1))
    if not DATA_DATE_FIRST_YEAR <= year <= (today or _dt.date.today()).year + 1:
        return None
    if m.group(2) is None:
        return f"{year:04d}"
    month = int(m.group(2))
    return f"{year:04d}-{month:02d}" if 1 <= month <= 12 else None


# --- wave 4a: study rows and change lines ---------------------------------------------------------
@dataclass(frozen=True)
class StudyRow:
    study_id: int
    name: str
    date: str | None          # data date (YYYY or YYYY-MM), or the first-reported month
    reported: bool            # True when `date` is a first-reported fallback
    headline: str | None      # the article's own token; only when the code check passed
    headline_label: str | None
    direction: str
    tier: str | None
    series: str | None
    edition: int | None       # position among the series' studies in the cell, when 2 or more


@dataclass(frozen=True)
class ChangeLine:
    series: str
    points: list              # [(headline, year)], oldest first


def label_key(label: str) -> str:
    return _re.sub(r"[\W_]+", " ", str(label or "").casefold()).strip()


def _row_date(study: dict, published: dict, reported: dict) -> tuple:
    if study.get("data_date"):
        return study["data_date"], False
    first = published.get(study["id"])
    if first:
        return first[:7], True
    entry = reported.get(study["id"])
    if entry and entry.get("rendered_at"):
        return clock.local_date(entry["rendered_at"])[:7], True
    created = study.get("created_at")
    return (clock.local_date(created)[:7], True) if created else (None, False)


def _rows(studies: list, published: dict, reported: dict, unsorted: bool) -> tuple[list, list]:
    """The cell's studies as rows, newest first (undated last), and a change line per repeated
    series. Editions are numbered oldest first, before the rows are reordered."""
    drafts = []
    for s in studies:
        date, is_reported = _row_date(s, published, reported)
        checked = s.get("headline_checked") == 1
        drafts.append((s, date, is_reported, s["headline_value"] if checked else None,
                       s["headline_label"] if checked else None))
    drafts.sort(key=lambda d: (d[1] is None, d[1] or "", _TIER_RANK.get(d[0].get("evidence_tier"), 9),
                               d[0]["id"]))
    per_series: dict = {}
    for s, *_ in drafts:
        if s.get("series_key"):
            per_series[s["series_key"]] = per_series.get(s["series_key"], 0) + 1
    seen: dict = {}
    rows = []
    for s, date, is_reported, headline, label in drafts:
        edition = None
        if s.get("series_key") and per_series[s["series_key"]] >= 2:
            seen[s["series_key"]] = seen.get(s["series_key"], 0) + 1
            edition = seen[s["series_key"]]
        rows.append(StudyRow(study_id=s["id"], name=s["canonical_name"], date=date,
                             reported=is_reported, headline=headline, headline_label=label,
                             direction=s["direction"], tier=s.get("evidence_tier"),
                             series=s.get("series_name") or None, edition=edition))
    rows = sorted([r for r in rows if r.date], key=lambda r: r.date, reverse=True) + [
        r for r in rows if not r.date]
    return rows, ([] if unsorted else _changes(drafts, per_series))


def _changes(drafts: list, per_series: dict) -> list:
    """One line per series with two or more studies whose labels agree (the commonest label)."""
    lines = []
    for key, count in per_series.items():
        if count < 2:
            continue
        by_label: dict = {}
        for position, (s, date, _, headline, label) in enumerate(drafts):
            if s.get("series_key") == key and headline and date:
                by_label.setdefault(label_key(label), []).append((position, s, headline, date[:4]))
        best = max(by_label.values(), key=len, default=[])    # first-seen label wins a tie
        if len(best) >= 2:
            lines.append((best[0][0], ChangeLine(best[0][1].get("series_name") or "",
                                                 [(h, y) for _, _, h, y in best])))
    return [line for _, line in sorted(lines, key=lambda t: t[0])]


# --- questions: the evidence model per subtopic and question -------------------------------------
@dataclass(frozen=True)
class QuestionGroup:
    question_id: int
    text: str
    measure: str
    subtopic_key: str
    label: str | None         # consistent | contested | varied; None with one group or a missing direction
    independent: int          # independent groups (same conducted_by = one)
    articles: int
    best_tier: str | None
    groups: list              # [[study id, ...], ...]: representative first, groups by representative
    directions: dict          # study id -> its direction on the question's measure (None: not read yet)
    echoes: dict              # study id -> echo line, for studies that have one
    kind: str | None = None   # difference | level | capability; None = not decided yet
    comparison: str | None = None   # difference questions: what A is compared with
    rows: list = field(default_factory=list)      # StudyRow, newest first
    changes: list = field(default_factory=list)   # ChangeLine for repeated studies
    explanation: dict | None = None   # {summary, main_result, lines}: the current agreed one

    def group_dirs(self) -> list:
        """The direction of each independent group, in `groups` order (what labels, echoes and relate use)."""
        return [None if any(self.directions[sid] is None for sid in g)
                else _group_direction([self.directions[sid] for sid in g]) for g in self.groups]


@dataclass(frozen=True)
class OtherStudy:
    row: StudyRow
    question: str | None      # the question text; None = not grouped yet
    echo: str | None = None


@dataclass(frozen=True)
class SubtopicEvidence:
    key: str                  # the subtopic's slug, or UNSORTED
    subtopic_id: int | None
    name: str
    questions: list           # [QuestionGroup]: labelled first, then multi-study questions
    others: list              # [OtherStudy], newest first


def _question_label(directions: list) -> str | None:
    """Label over the group directions; None with fewer than 2 groups or a missing direction."""
    if len(directions) < 2 or None in directions:
        return None
    if "up" in directions and "down" in directions:
        return "contested"
    return "consistent" if len(set(directions)) == 1 else "varied"


def _question_group(question: dict, key: str, studies: list, articles_of: dict,
                    published: dict, reported: dict) -> QuestionGroup:
    studies = [{**s, "direction": s["question_direction"]} for s in studies]
    by_org: dict = {}
    for s in studies:
        by_org.setdefault(_org(s) or ("study", s["id"]), []).append(s)
    groups = sorted((sorted(m, key=_rank) for m in by_org.values()), key=lambda m: _rank(m[0]))
    dirs = [None if any(s["direction"] is None for s in m)
            else _group_direction([s["direction"] for s in m]) for m in groups]
    tiers = [s["evidence_tier"] for s in studies if s["evidence_tier"] in _TIER_RANK]
    ids = {s["id"] for s in studies}
    articles = sum(articles_of[s["id"]] for s in studies)
    rows, changes = _rows(studies, published, reported, False)
    kind = question.get("kind")
    label = _question_label(dirs) if kind == "difference" else None     # a label only where directions belong
    return QuestionGroup(
        question_id=question["id"], text=question["text"], measure=question["measure"], subtopic_key=key,
        label=label, independent=len(groups), articles=articles,
        best_tier=min(tiers, key=_TIER_RANK.get) if tiers else None,
        groups=[[s["id"] for s in m] for m in groups],
        directions={s["id"]: s["direction"] for s in studies},
        echoes=_echoes(groups, dirs, articles, kind),
        rows=rows, changes=changes, kind=kind, comparison=question.get("comparison"))


def _with_explanation(conn, group: QuestionGroup, row: "dict | None") -> QuestionGroup:
    """The group with its stored explanation, only while the row matches the question's state now:
    same inputs hash, and only lines about studies still in the question."""
    if row is None:
        return group
    state = explain_state(conn, group)
    if state is None or state.inputs_hash != row["inputs_hash"]:
        return group
    here = set(group.directions)
    lines = [x for x in row["lines"] if x["study"] in here]
    if not lines:
        return group
    return replace(group, explanation={"summary": row["summary"], "main_result": row["main_result"], "lines": lines})


def placed_studies(conn):
    """Counting studies (active, an item on the page) as (study, resolved subtopic or None, question
    or None). The question counts only when it resolves into the study's own resolved subtopic."""
    from evidence_echo.core import store
    subtopics = {t["id"]: t for t in store.list_subtopics(conn)}
    articles = store.study_item_counts(conn)
    out = []
    for s in store.active_studies(conn):
        if not articles.get(s["id"]):
            continue
        subtopic = _resolve(subtopics, s.get("subtopic_id"))
        question = None
        if subtopic is not None and s.get("question_id") is not None:
            found = store.resolve_question(conn, s["question_id"])
            if found and (_resolve(subtopics, found["subtopic_id"]) or {}).get("id") == subtopic["id"]:
                question = found
        out.append((s, subtopic, question))
    return out, articles


def evidence(conn) -> list[SubtopicEvidence]:
    """Per subtopic (seeded order, then confirmed, Unsorted last; empty ones left out): its questions
    with 2+ studies (labelled ones first) and every other study."""
    from evidence_echo.core import store
    placed, articles = placed_studies(conn)
    explained = {x["question_id"]: x for x in store.question_explanations(conn, status="agreed")}
    published, reported = store.study_first_published(conn), store.first_reports(conn)
    buckets: dict = {}
    for s, subtopic, question in placed:
        bucket = buckets.setdefault(subtopic["slug"] if subtopic else UNSORTED,
                                    {"subtopic": subtopic, "by_question": {}, "loose": []})
        if question is None:
            bucket["loose"].append(s)
        else:
            bucket["by_question"].setdefault(question["id"], (question, []))[1].append(s)
    result = []
    for key, bucket in sorted(buckets.items(), key=lambda e: (2, 0) if e[1]["subtopic"] is None
                              else store.subtopic_order(e[1]["subtopic"])):
        subtopic = bucket["subtopic"]
        groups = {qid: _question_group(question, key, studies, articles, published, reported)
                  for qid, (question, studies) in bucket["by_question"].items()}
        groups = {qid: _with_explanation(conn, g, explained.get(qid)) for qid, g in groups.items()}
        newest = {qid: max(s["id"] for s in studies) for qid, (_, studies) in bucket["by_question"].items()}
        multi = [g for g in groups.values() if len(g.rows) >= 2]
        multi.sort(key=lambda g: (g.label is None, -g.independent if g.label else 0, -newest[g.question_id]))
        singles = [(bucket["by_question"][qid][0], bucket["by_question"][qid][1][0], g)
                   for qid, g in groups.items() if len(g.rows) < 2]
        loose = [{**s, "direction": None} for s in bucket["loose"]]
        rows, _ = _rows(loose + [{**s, "direction": s["question_direction"]} for _, s, _ in singles],
                        published, reported, True)
        text_of = {s["id"]: None for s in bucket["loose"]}
        echo_of: dict = {}
        for question, s, g in singles:
            text_of[s["id"]] = question["text"]
            echo_of[s["id"]] = g.echoes.get(s["id"])
        result.append(SubtopicEvidence(
            key=key, subtopic_id=subtopic["id"] if subtopic else None,
            name=subtopic["name"] if subtopic else UNSORTED_NAME, questions=[] if subtopic is None else multi,
            others=[OtherStudy(r, text_of[r.study_id], echo_of.get(r.study_id)) for r in rows]))
    return result


def study_questions(evidence_list: list) -> dict[int, dict]:
    """study id -> what a report card shows for the study's question (render.model.Entry.subtopic).
    Unsorted studies are left out: they have no subtopic line."""
    out = {}
    for e in evidence_list:
        if e.key == UNSORTED:
            continue
        for g in e.questions:
            for sid in g.directions:
                out[sid] = {"key": e.key, "name": e.name, "question": g.text, "label": g.label,
                            "independent": g.independent, "echo": g.echoes.get(sid)}
        for o in e.others:
            out[o.row.study_id] = {"key": e.key, "name": e.name, "question": o.question, "label": None,
                                   "independent": 1 if o.question else 0, "echo": o.echo}
    return out


def waiting_count(conn) -> int:
    """Counting studies in an active subtopic that have no counting question yet."""
    placed, _ = placed_studies(conn)
    return sum(1 for _, subtopic, question in placed if subtopic is not None and question is None)


# --- explain state of a question (relate) ---------------------------------------------------------
import hashlib  # noqa: E402  (appended section)
import json  # noqa: E402

RELATE_HASH_TOKEN = "relate-4"
EXPLAIN_RECENT = 12           # representatives sent when a question has more groups than this ...
EXPLAIN_MAX_STUDIES = 20      # ... plus every one to explain, at most this many in total


@dataclass(frozen=True)
class ExplainState:
    question_id: int
    main_result: str
    studies: list             # representative ids sent to the comparer, newest first
    directions: dict          # representative id -> its group's direction
    explain: list             # representatives outside the main result, newest first
    inputs_hash: str
    truncated: bool = False   # more representatives were outside the main result than explain holds


def main_result(group: QuestionGroup, tiers: dict) -> str | None:
    """The direction most groups hold (up, down, no_change; never mixed). A tie goes to the direction
    whose best representative has the best tier, then to the one with the newest representative."""
    held: dict = {}
    for members, direction in zip(group.groups, group.group_dirs()):
        if direction in (None, "mixed"):
            continue
        rep = members[0]
        held.setdefault(direction, []).append((_TIER_RANK.get(tiers.get(rep), 9), rep))
    if not held:
        return None
    return min(held, key=lambda d: (-len(held[d]), min(r for r, _ in held[d]), -max(i for _, i in held[d])))


def _study_inputs(conn, study: dict) -> dict:
    from evidence_echo.core import store
    items = store.study_items(conn, study["id"], store.STUDY_ITEM_STATES)
    return {"findings": study.get("findings"), "what_was_tested": study.get("what_was_tested"),
            "research_timeframe": study.get("research_timeframe"),
            "sample_scale": study.get("sample_scale"), "items": [i["id"] for i in items]}


def explain_state(conn, group: QuestionGroup) -> "ExplainState | None":
    """What relate asks about a difference question labelled contested or varied; None for any other."""
    from evidence_echo.core import store
    if group.kind != "difference" or group.label not in ("contested", "varied"):
        return None
    reps = {members[0]: direction for members, direction in zip(group.groups, group.group_dirs())}
    rows = {rep: store.get_study(conn, rep) for rep in reps}
    main = main_result(group, {rep: row.get("evidence_tier") for rep, row in rows.items()})
    if main is None:
        return None
    explain = sorted((rep for rep, d in reps.items() if d != main), reverse=True)
    if not explain:
        return None
    truncated = len(explain) > EXPLAIN_MAX_STUDIES - 4
    explain = explain[:EXPLAIN_MAX_STUDIES - 4]
    tier = {rep: _TIER_RANK.get(row.get("evidence_tier"), 9) for rep, row in rows.items()}
    best_main = min((rep for rep, d in reps.items() if d == main), key=lambda r: (tier[r], -r))
    keep = set(explain) | {best_main}                       # the comparer always sees a main-result study
    sent = set(keep)
    for rep in sorted(reps, reverse=True)[:EXPLAIN_RECENT]:
        sent.add(rep)
    for rep in sorted(sent - keep):                          # over the cap: the oldest context goes first
        if len(sent) <= EXPLAIN_MAX_STUDIES:
            break
        sent.discard(rep)
    studies = sorted(sent, reverse=True)
    raw = json.dumps({"question": group.text, "measure": group.measure, "comparison": group.comparison,
                      "main_result": main,
                      "studies": [{"id": sid, "direction": reps[sid], **_study_inputs(conn, rows[sid])}
                                  for sid in studies],
                      "prompt_version": RELATE_HASH_TOKEN}, sort_keys=True, ensure_ascii=False)
    return ExplainState(question_id=group.question_id, main_result=main, studies=studies,
                        directions={sid: reps[sid] for sid in studies}, explain=explain,
                        inputs_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(), truncated=truncated)


# --- splits: an overloaded subtopic, a bloated Other subtopic or a bloated "other" metric --------------
SPLIT_MIN_STUDIES = 15        # an overloaded subtopic holds at least this many counted studies ...
SPLIT_SUBTOPIC_SHARE = 0.30   # ... and more than this share of all counted studies
SPLIT_OTHER_MIN = 10          # the Other subtopic / the "other" metric: at least this many ...
SPLIT_OTHER_SHARE = 0.20      # ... and more than this share
SPLIT_REARM = 1.25            # after a decided proposal: again at this many times its studies_at
SPLIT_MAX_STUDIES = 150       # studies shown to the model for a metric split
SPLIT_MIN_GROUP = 3           # studies a proposed group must hold
SPLIT_SUBTOPIC_GROUPS = (2, 8)
SPLIT_METRIC_GROUPS = (1, 10)


def _more_than(count: int, share: float, total: int) -> bool:
    return count > round(share * total, 9)


def split_targets(conn) -> list[dict]:
    """What should be split now: at most one subtopic (the largest that qualifies) and the "other"
    metric, as [{"target": "subtopic"|"metric", "source_id", "slug", "name", "count"}]. A source with a
    pending proposal is skipped, and so is one with a decided proposal until its count has grown to
    SPLIT_REARM times what it was then. (Lives here, not in store, because it counts with placed_studies.)"""
    from evidence_echo.core import store
    placed, _ = placed_studies(conn)
    total = len(placed)
    state = store.split_state(conn)

    def open_(target: str, source_id: int, count: int) -> bool:
        known = state.get((target, source_id))
        if known is None:
            return True
        return not known["pending"] and (known["studies_at"] is None
                                         or count >= SPLIT_REARM * known["studies_at"])

    counts: dict = {}
    for _, subtopic, _ in placed:
        if subtopic is not None:
            counts[subtopic["id"]] = [subtopic, counts.get(subtopic["id"], [None, 0])[1] + 1]
    found = []
    for subtopic, count in counts.values():
        if subtopic["slug"] == "other":                 # the Other subtopic
            fires = count >= SPLIT_OTHER_MIN and _more_than(count, SPLIT_OTHER_SHARE, total)
        else:
            fires = count >= SPLIT_MIN_STUDIES and _more_than(count, SPLIT_SUBTOPIC_SHARE, total)
        if fires and open_("subtopic", subtopic["id"], count):
            found.append((count, -subtopic["id"], subtopic))
    out = []
    if found:
        count, _, subtopic = max(found, key=lambda f: f[:2])
        out.append({"target": "subtopic", "source_id": subtopic["id"], "slug": subtopic["slug"],
                    "name": subtopic["name"], "count": count})
    other = store.get_metric(conn, store.OTHER_METRIC)
    if other is not None:
        count = sum(1 for study, _, _ in placed
                    if (store.study_metric(conn, study) or {}).get("id") == other["id"])
        if (count >= SPLIT_OTHER_MIN and _more_than(count, SPLIT_OTHER_SHARE, total)
                and open_("metric", other["id"], count)):
            out.append({"target": "metric", "source_id": other["id"], "slug": other["slug"],
                        "name": other["name"], "count": count})
    return out
