"""Read-only questions over a topic's database: which graded studies answer a question.

Used by the MCP server and `ev find`. Nothing here writes: databases open through `mode=ro` URIs and
never through store.connect (which creates and migrates). Stdlib and evidence_echo only.
"""
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path

from evidence_echo.core import grading, knowledge, store
from evidence_echo.core.settings import ConfigError
from evidence_echo.render import model

BUSY_TIMEOUT = 5          # seconds a reader waits for a report run's write lock

STATUS_NO_DATA = "no data yet: run a report"
STATUS_OLD = "needs_upgrade: run any Evidence Echo report"
STATUS_NEWER = "newer than this plugin: update Evidence Echo"

STOP_WORDS = frozenset("""a an and are as at be been but by can could did do does for from has have how i if in
into is it its of on or our should so than that the their then there these they this to was we were what when
where which who whom why will with would you your about any not no over per vs versus""".split())

FIELD_WEIGHTS = {"name": 2.0, "findings": 1.0, "question": 1.0, "subtopic": 1.0, "metric": 1.0, "entities": 1.0}
K1 = 1.2
B = 0.75

_SUFFIXES = (("ations", ""), ("ation", ""), ("ings", ""), ("ing", ""), ("ies", "y"), ("es", ""),
             ("ed", ""), ("ly", ""), ("s", ""))


def open_ro(db_path: Path) -> sqlite3.Connection:
    """A read-only connection: writes fail, and nothing is created or migrated."""
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=BUSY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def db_status(tp) -> str | None:
    """None when the topic can be searched, else a plain reason."""
    if tp.error:
        return tp.error
    if not tp.db_path.exists():
        return STATUS_NO_DATA
    conn = open_ro(tp.db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    if version < store.LATEST_VERSION:
        return STATUS_OLD
    if version > store.LATEST_VERSION:
        return STATUS_NEWER
    return None


def stem(word: str) -> str:
    """A light English stem: the same word in its common forms meets itself."""
    for suffix, replacement in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[:-len(suffix)] + replacement
            break
    if word.endswith("e") and len(word) >= 4:
        word = word[:-1]
    return word


_AB = re.compile(r"\ba\s*[/-]\s*b\b")


def words(text: str) -> list[str]:
    out = []
    text = _AB.sub("ab", (text or "").lower())    # "A/B test": the letters alone would be dropped
    for token in re.findall(r"\w+", text):
        if token in STOP_WORDS or (len(token) == 1 and not token.isdigit()):
            continue
        out.append(stem(token) if token.isascii() else token)
    return out


def _counted_studies(conn) -> list[dict]:
    counts = store.study_item_counts(conn)
    return [s for s in store.active_studies(conn) if counts.get(s["id"])]


def _has_rows(conn, table: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None


def study_bundles(conn, mode: str) -> dict[int, dict[str, list[str]]]:
    """Per counted study, the searchable words of each field."""
    by_study = store.findings_by_study(conn) if _has_rows(conn, "findings") else {}
    aliases: dict[int, list[str]] = {}
    for row in conn.execute("SELECT merged_into, name FROM entities WHERE merged_into IS NOT NULL"):
        aliases.setdefault(row["merged_into"], []).append(row["name"])
    bundles = {}
    for study in _counted_studies(conn):
        sid = study["id"]
        texts = by_study.get(sid, [])
        questions = []
        if mode == "findings":
            ids = {f["question_id"] for f in texts if f.get("question_id") is not None}
        elif mode == "questions" and study.get("question_id") is not None:
            ids = {study["question_id"]}
        else:
            ids = set()
        for qid in sorted(ids):
            question = store.resolve_question(conn, qid)
            if question:
                questions.append(question["text"])
        subtopic = store.study_subtopic(conn, study, mode)
        metric = store.study_metric(conn, study)
        names = []
        for entity in store.study_entities(conn, sid):
            names.append(entity["name"])
            names.extend(aliases.get(entity["id"], []))
        bundles[sid] = {
            "name": words(f"{study['canonical_name']} {study.get('what_was_tested') or ''}"),
            "findings": words(" ".join([study.get("findings") or "", *(f["text"] for f in texts)])),
            "question": words(" ".join(questions)),
            "subtopic": words(subtopic["name"] if subtopic else ""),
            "metric": words(metric["name"] if metric else ""),
            "entities": words(" ".join(names)),
        }
    return bundles


def rank(bundles: dict, question_words: list[str]) -> list[tuple[float, int]]:
    """BM25 over the weighted fields; a study matches only when it holds enough distinct query words
    (one for a question of one or two words, else two). Best first."""
    terms = list(dict.fromkeys(question_words))
    if not terms or not bundles:
        return []
    needed = 1 if len(terms) <= 2 else 2
    weighted = {}
    for sid, fields in bundles.items():
        tf = Counter()
        for field, tokens in fields.items():
            weight = FIELD_WEIGHTS.get(field, 1.0)
            for token in tokens:
                tf[token] += weight
        weighted[sid] = tf
    lengths = {sid: sum(tf.values()) for sid, tf in weighted.items()}
    average = (sum(lengths.values()) / len(lengths)) or 1.0
    total = len(weighted)
    df = {t: sum(1 for tf in weighted.values() if t in tf) for t in terms}
    scored = []
    for sid, tf in weighted.items():
        held = [t for t in terms if t in tf]
        if len(held) < needed:
            continue
        score = 0.0
        for t in held:
            idf = math.log(1 + (total - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * lengths[sid] / average))
        scored.append((score, sid))
    return sorted(scored, key=lambda pair: (-pair[0], -pair[1]))


# --- study views ------------------------------------------------------------------------------------
def question_index(conn, mode: str) -> dict[int, list[dict]]:
    """Study id -> the questions it answers in `mode`, with the label as the topic page shows it."""
    if mode == "quotes":
        return {}
    index: dict[int, list[dict]] = {}
    for sub in knowledge.evidence(conn, mode):
        for g in sub.questions:
            summary = (g.explanation or {}).get("summary")
            for sid in g.directions:
                index.setdefault(sid, []).append({
                    "id": f"q{g.question_id}", "text": g.text, "kind": g.kind, "label": g.label,
                    "independent": g.independent, "explanation": summary})
        for other in sub.others:
            if other.question_id is None:
                continue
            question = store.get_question(conn, other.question_id) or {}
            index.setdefault(other.row.study_id, []).append({
                "id": f"q{other.question_id}", "text": other.question, "kind": question.get("kind"),
                "label": None, "independent": 1, "explanation": None})
    return index


def _latest_summary(conn, study_id: int) -> str | None:
    row = conn.execute(
        """WITH RECURSIVE ids(id) AS (SELECT ? UNION SELECT s.id FROM studies s JOIN ids ON s.merged_into = ids.id)
           SELECT e.summary FROM report_entries e JOIN reports r ON r.id = e.report_id
           WHERE e.study_id IN (SELECT id FROM ids) AND e.summary IS NOT NULL AND e.summary != ''
           ORDER BY r.rendered_at DESC, r.id DESC LIMIT 1""", (study_id,)).fetchone()
    return row[0] if row else None


def page_link(tp, study_id: int, reported: bool) -> str | None:
    """A file:// link to the study's card on the topic page; None when the page is not there or the
    study is not on it (the page shows reported studies only)."""
    if not reported or tp.reports_dir is None:
        return None
    page = tp.reports_dir / f"{tp.id}.html"
    return page.resolve().as_uri() + f"#s{study_id}" if page.is_file() else None


def study_view(conn, tp, study: dict, questions: dict) -> dict:
    if study.get("merged_into") is not None:       # a merged-away id stands for the study it ended in
        study = store.get_study(conn, store.resolve_study(conn, study["id"]))
    sid = study["id"]
    group = store.study_items(conn, sid, store.STUDY_ITEM_STATES)
    creator = next((g for g in group if g["id"] == study.get("created_by_item")), None)
    checks = store.primary_checks_for(conn, [g["id"] for g in group])
    metric = store.study_metric(conn, study)
    entities = store.study_entities(conn, sid)
    view = model.grading_view(study, group, checks, metric, entities, grading.rules_of(tp.topic))
    subtopic = store.study_subtopic(conn, study, tp.grouping_mode)
    rows = store.study_findings(conn, sid)
    reported = store.first_reports(conn).get(sid)
    out = {
        "id": f"s{sid}", "topic": tp.id, "name": study["canonical_name"],
        "conducted_by": study.get("conducted_by"), "relation": study.get("relation"),
        "tier": study["evidence_tier"], "skeptic_tier": creator["skeptic_tier"] if creator else None,
        "disputed": any(g["disputed"] for g in group),
        "capped_from": view["capped_from"], "cap_reasons": view["cap_reasons"],
        "labels": view["grade_labels"],
        "red_flags": [{"code": c, "text": grading.ALL_RED_FLAGS[c]} for c in view["flags"]],
        "summary": _latest_summary(conn, sid) or study.get("findings"),
        "headline": {"value": study.get("headline_value"), "label": study.get("headline_label"),
                     "quote": study.get("headline_quote"),
                     "checked": None if study.get("headline_checked") is None
                     else bool(study["headline_checked"])},
        "questions": [{"id": q["id"], "text": q["text"], "label": q["label"]}
                      for q in questions.get(sid, [])],
        "subtopic": subtopic["name"] if subtopic else None,
        "metric": metric["name"] if metric else None,
        "entities": [e["name"] for e in entities],
        "data_date": study.get("data_date"),
        "reported_at": reported["rendered_at"] if reported else None,
        "url": (creator or (group[0] if group else {})).get("url"),
        "primary_url": study.get("primary_url"),
        "page": page_link(tp, sid, reported is not None),
    }
    if rows:
        out["findings"] = [{"text": f["text"], "quote": f["quote"], "checked": bool(f["quote_checked"])}
                           for f in rows]
    return out


def _page_uri(tp) -> str | None:
    if tp.reports_dir is None:
        return None
    page = tp.reports_dir / f"{tp.id}.html"
    return page.resolve().as_uri() if page.is_file() else None


def list_topics(paths) -> list[dict]:
    out = []
    for tp in paths.topics:
        try:
            status = db_status(tp)
        except sqlite3.Error as exc:
            status = f"cannot be read right now: {exc}"
        row = {"id": tp.id, "name": tp.topic.name if tp.topic else tp.id,
               "domain": tp.topic.domain if tp.topic else None, "studies": 0, "questions": 0, "findings": 0,
               "grouping_mode": tp.grouping_mode, "last_report": None, "topic_page": _page_uri(tp),
               "disclaimer": tp.disclaimer, "status": status or "ok"}
        if status is None:
            try:
                conn = open_ro(tp.db_path)
                try:
                    counted = {s["id"] for s in _counted_studies(conn)}
                    asked = set()
                    for sid_questions in question_index(conn, tp.grouping_mode).values():
                        asked.update(q["id"] for q in sid_questions)
                    row["studies"], row["questions"] = len(counted), len(asked)
                    row["findings"] = sum(len(rows) for sid, rows in store.findings_by_study(conn).items()
                                          if sid in counted)
                    row["last_report"] = store.latest_report_time(conn)
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                row["status"] = f"cannot be read right now: {exc}"
        out.append(row)
    return out


# --- find_evidence and get_study --------------------------------------------------------------------
MAX_LIMIT = 20
DEFAULT_LIMIT = 8
EXCERPT_MAX = 600
_TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def coverage(studies: list[dict], searchable: bool = True) -> dict:
    """How well the matched studies answer: how many, their best grade, how many independent groups.
    `studies` carry `tier`, `conducted_by` and an `id` that is unique across topics."""
    n = len(studies)
    tiers = [s["tier"] for s in studies if s.get("tier") in _TIER_ORDER]
    best = min(tiers, key=_TIER_ORDER.get) if tiers else None
    independent = len({knowledge._org(s) or ("study", s["id"]) for s in studies})
    if not searchable:
        note = "no searchable words in the question"
    elif n == 0:
        note = "no studies found"
    else:
        note = f"{n} stud{'y' if n == 1 else 'ies'}, best grade {best or 'unknown'}"
    return {"studies": n, "best_tier": best, "independent": independent,
            "thin": n == 0 or independent < 2 or best in ("C", "D"), "note": note}


def _order(hits: list[dict]) -> list[dict]:
    """Best score first, then the better grade, then the newer report or data date."""
    hits.sort(key=lambda h: h["date"], reverse=True)
    hits.sort(key=lambda h: (-h["score"], _TIER_ORDER.get(h["tier"], 9)))
    return hits


def _search_topic(tp, qwords: list[str], limit: int) -> dict:
    """One topic's matches: light records for all, full views for the best `limit`, and its questions."""
    conn = open_ro(tp.db_path)
    try:
        ranked = rank(study_bundles(conn, tp.grouping_mode), qwords)
        index = question_index(conn, tp.grouping_mode)
        reported = store.first_reports(conn)
        light = []
        for score, sid in ranked:
            study = store.get_study(conn, sid)
            light.append({"id": f"{tp.id}/{sid}", "sid": sid, "tier": study["evidence_tier"],
                          "conducted_by": study.get("conducted_by"), "score": score,
                          "date": (reported.get(sid) or {}).get("rendered_at") or study.get("data_date") or ""})
        for h in _order(light)[:limit]:
            h["view"] = study_view(conn, tp, store.get_study(conn, h["sid"]), index)
        questions: dict[str, dict] = {}
        for sid, entries in index.items():
            for q in entries:
                found = questions.setdefault(q["id"], {**q, "topic": tp.id, "study_ids": []})
                found["study_ids"].append(sid)
        return {"light": light, "questions": list(questions.values())}
    finally:
        conn.close()


def find_evidence(paths, question: str, topic: str | None = None, limit: int = DEFAULT_LIMIT) -> dict:
    limit = max(1, min(int(limit), MAX_LIMIT))
    chosen = paths.topics
    if topic:
        chosen = [t for t in paths.topics if t.id == topic]
        if not chosen:
            known = ", ".join(t.id for t in paths.topics) or "none yet"
            raise ConfigError(f"Unknown topic '{topic}'. Topics: {known}")
    qwords = words(question)
    needed = 1 if len(set(qwords)) <= 2 else 2
    searched, skipped, light, questions = [], [], [], []
    for tp in chosen:
        try:
            status = db_status(tp)
            if status:
                skipped.append({"topic": tp.id, "status": status})
                continue
            found = _search_topic(tp, qwords, limit)
        except sqlite3.Error as exc:
            skipped.append({"topic": tp.id, "status": f"cannot be read right now: {exc}"})
            continue
        searched.append(tp.id)
        light.extend(found["light"])
        questions.extend(found["questions"])
    returned = _order(light)[:limit]
    shown = {(h["view"]["topic"], h["sid"]) for h in returned}
    wanted = set(qwords)
    matched = []
    for q in questions:
        contains = any((q["topic"], sid) in shown for sid in q["study_ids"])
        if contains or (wanted and len(wanted & set(words(q["text"]))) >= needed):
            matched.append((not contains, q))
    matched.sort(key=lambda pair: pair[0])
    return {
        "topics_searched": searched, "skipped": skipped,
        "coverage": coverage(light, searchable=bool(qwords)),
        "questions": [{"topic": q["topic"], "id": q["id"], "text": q["text"], "kind": q["kind"],
                       "label": q["label"], "independent": q["independent"],
                       "studies": [f"s{sid}" for sid in sorted(q["study_ids"])],
                       "explanation": q["explanation"]} for _, q in matched[:MAX_LIMIT]],
        "studies": [h["view"] for h in returned],
    }


def _study_number(study_id) -> int:
    text = str(study_id).strip().lower()
    if text.startswith("s"):
        text = text[1:]
    if not text.isdigit():
        raise ConfigError(f"Unknown study '{study_id}': use an id like s12")
    return int(text)


def _excerpt(text: str | None, quote: str | None) -> str | None:
    """Up to EXCERPT_MAX characters of the article around the headline quote; None without the quote."""
    if not text or not quote:
        return None
    flat, needle = grading._norm(text), grading._norm(quote)
    at = flat.find(needle) if needle else -1
    if at < 0:
        return None
    start = max(0, min(at + len(needle) // 2 - EXCERPT_MAX // 2, len(flat) - EXCERPT_MAX))
    start = min(start, at)           # a long quote is shown from its start
    return flat[start:start + EXCERPT_MAX]


def get_study(paths, topic: str, study_id) -> dict:
    tp = next((t for t in paths.topics if t.id == topic), None)
    if tp is None:
        known = ", ".join(t.id for t in paths.topics) or "none yet"
        raise ConfigError(f"Unknown topic '{topic}'. Topics: {known}")
    number = _study_number(study_id)
    try:
        status = db_status(tp)
        if status:
            raise ConfigError(f"Topic '{topic}' cannot be searched: {status}")
        conn = open_ro(tp.db_path)
    except sqlite3.Error as exc:
        raise ConfigError(f"Topic '{topic}' cannot be read right now: {exc}") from None
    try:
        study = store.get_study(conn, store.resolve_study(conn, number))
        group = store.study_items(conn, study["id"], store.STUDY_ITEM_STATES) if study else []
        if not group:
            raise ConfigError(f"Unknown study 's{number}' in topic '{topic}'")
        sid = study["id"]
        mode = tp.grouping_mode
        evidence = knowledge.evidence(conn, mode) if mode != "quotes" else []
        view = study_view(conn, tp, study, question_index(conn, mode))
        creator = next((g for g in group if g["id"] == study.get("created_by_item")), group[0])
        primary = model.primary_view(group, store.primary_checks_for(conn, [g["id"] for g in group]))
        lines = []
        for sub in evidence:
            for g in sub.questions:
                for line in (g.explanation or {}).get("lines", []):
                    if line.get("study") == sid:
                        lines.append({"question": g.text, "reason": line.get("reason"),
                                      "quote": line.get("quote"), "note": line.get("note")})
        view.update({
            "items": [{"source": g["source_name"], "url": g["url"], "date": g["published"]} for g in group],
            "primary": {k: primary[k] for k in ("status", "verdict", "note")} if primary else None,
            "explanation_lines": lines,
            "excerpt": _excerpt(creator.get("full_text"), study.get("headline_quote")),
        })
        return view
    except sqlite3.Error as exc:
        raise ConfigError(f"Topic '{topic}' cannot be read right now: {exc}") from None
    finally:
        conn.close()
