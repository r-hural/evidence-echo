"""All SQLite access. Stages call these functions and never write SQL."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from evidence_echo.core import identity, knowledge
from evidence_echo.core.settings import ConfigError
from evidence_echo.core.urls import canonical_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  source_name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  title TEXT DEFAULT '',
  excerpt TEXT DEFAULT '',
  full_text TEXT,
  published TEXT,
  fetched_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'fetched',
  triage_pass INTEGER,
  triage_reason TEXT,
  verdict_pass INTEGER,
  evidence_tier TEXT,
  facts TEXT,
  primary_url TEXT,
  skeptic_agrees INTEGER,
  skeptic_tier TEXT,
  skeptic_note TEXT,
  disputed INTEGER NOT NULL DEFAULT 0,
  study_id INTEGER REFERENCES studies(id),
  canonical_url TEXT,
  content_hash TEXT,
  text_source TEXT,
  fetch_status TEXT,
  date_unknown INTEGER NOT NULL DEFAULT 0,
  first_study_id INTEGER,
  match_reason TEXT,
  quotes TEXT,
  quote_check TEXT,
  capped_from TEXT,
  judged_from TEXT,
  red_flags TEXT,
  dispute_reason TEXT,
  provenance TEXT,
  judge_tier TEXT,
  subtopic_choice TEXT,
  metric_code TEXT,
  direction TEXT,
  headline TEXT,
  data_date TEXT,
  entity_names TEXT,
  judge_reason TEXT,
  rejected_at TEXT
);
CREATE TABLE IF NOT EXISTS studies (
  id INTEGER PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  what_was_tested TEXT,
  findings TEXT,
  evidence_tier TEXT,
  research_timeframe TEXT,
  sample_scale TEXT,
  has_controls INTEGER,
  methodology_stated INTEGER,
  duration TEXT,
  match_keys TEXT,
  primary_url TEXT,
  series_id INTEGER REFERENCES series(id),
  editions TEXT,
  primary_canonical TEXT,
  fingerprint TEXT,
  merged_into INTEGER REFERENCES studies(id),
  metric TEXT,
  baseline TEXT,
  conducted_by TEXT,
  relation TEXT,
  conflict_of_interest TEXT,
  created_by_item INTEGER,
  created_at TEXT NOT NULL,
  subtopic_id INTEGER REFERENCES subtopics(id),
  metric_code TEXT,
  direction TEXT,
  headline_value TEXT,
  headline_label TEXT,
  headline_quote TEXT,
  headline_checked INTEGER,
  data_date TEXT,
  classified_version TEXT,
  question_id INTEGER REFERENCES questions(id),
  question_direction TEXT,
  grouped_version TEXT,
  question_by_owner INTEGER NOT NULL DEFAULT 0,
  entities_checked INTEGER,
  metric_id INTEGER REFERENCES metrics(id)
);
CREATE TABLE IF NOT EXISTS embeddings (
  item_id INTEGER NOT NULL REFERENCES items(id),
  model TEXT NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (item_id, model)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  stage TEXT NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  rendered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_entries (
  report_id INTEGER NOT NULL REFERENCES reports(id),
  study_id INTEGER NOT NULL REFERENCES studies(id),
  title TEXT NOT NULL,
  summary TEXT,
  PRIMARY KEY (report_id, study_id)
);
"""
SOURCES_STATE_DDL = """CREATE TABLE IF NOT EXISTS sources_state (
  name TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  added_at TEXT NOT NULL,
  last_checked TEXT,
  last_new_item TEXT,
  items_total INTEGER NOT NULL DEFAULT 0,
  consecutive_errors INTEGER NOT NULL DEFAULT 0,
  failing_since TEXT,
  last_error TEXT,
  blocked INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  backfilled_at TEXT,
  cursor TEXT
)"""
CANONICAL_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS items_canonical_url ON items(canonical_url)"
SERIES_DDL = """CREATE TABLE IF NOT EXISTS series (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
)"""
_LEGACY_THEMES_DDL = """CREATE TABLE IF NOT EXISTS themes (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  merged_into INTEGER REFERENCES themes(id),
  created_at TEXT NOT NULL,
  position INTEGER
)"""
_LEGACY_PAIR_EXPLANATIONS_DDL = """CREATE TABLE IF NOT EXISTS pair_explanations (
  id INTEGER PRIMARY KEY,
  study_a INTEGER NOT NULL REFERENCES studies(id),
  study_b INTEGER NOT NULL REFERENCES studies(id),
  theme_id INTEGER REFERENCES themes(id),
  metric TEXT NOT NULL,
  reason TEXT,
  quote_a TEXT,
  quote_b TEXT,
  note TEXT,
  status TEXT NOT NULL,
  inputs_hash TEXT NOT NULL,
  prompt_version TEXT,
  created_at TEXT NOT NULL,
  CHECK (study_a < study_b),
  UNIQUE (study_a, study_b, metric)
)"""
SUBTOPICS_DDL = """CREATE TABLE IF NOT EXISTS subtopics (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  merged_into INTEGER REFERENCES subtopics(id),
  created_at TEXT NOT NULL,
  position INTEGER
)"""
QUESTIONS_DDL = """CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY,
  subtopic_id INTEGER NOT NULL REFERENCES subtopics(id),
  text TEXT NOT NULL,
  measure TEXT NOT NULL,
  merged_into INTEGER REFERENCES questions(id),
  owner_touched INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  kind TEXT,
  comparison TEXT
)"""
METRICS_DDL = """CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  merged_into INTEGER REFERENCES metrics(id),
  position INTEGER,
  created_at TEXT NOT NULL
)"""
SPLIT_PROPOSALS_DDL = """CREATE TABLE IF NOT EXISTS split_proposals (
  id INTEGER PRIMARY KEY,
  target TEXT NOT NULL,
  source_id INTEGER NOT NULL,
  studies_at INTEGER NOT NULL,
  status TEXT NOT NULL,
  source_choice TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
)"""
SPLIT_GROUPS_DDL = """CREATE TABLE IF NOT EXISTS split_groups (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES split_proposals(id),
  mode TEXT NOT NULL,
  dest_id INTEGER NOT NULL,
  status TEXT NOT NULL
)"""
SPLIT_MOVES_DDL = """CREATE TABLE IF NOT EXISTS split_moves (
  group_id INTEGER NOT NULL REFERENCES split_groups(id),
  question_id INTEGER,
  study_id INTEGER
)"""
STEP11_DDL = (METRICS_DDL, SPLIT_PROPOSALS_DDL, SPLIT_GROUPS_DDL, SPLIT_MOVES_DDL)
_PAIR_EXPLANATIONS_COLUMNS = """
  id INTEGER PRIMARY KEY,
  study_a INTEGER NOT NULL REFERENCES studies(id),
  study_b INTEGER NOT NULL REFERENCES studies(id),
  subtopic_id INTEGER REFERENCES subtopics(id),
  question_id INTEGER REFERENCES questions(id),
  metric TEXT,
  reason TEXT,
  quote_a TEXT,
  quote_b TEXT,
  note TEXT,
  status TEXT NOT NULL,
  inputs_hash TEXT NOT NULL,
  prompt_version TEXT,
  created_at TEXT NOT NULL,
  CHECK (study_a < study_b),
  UNIQUE (study_a, study_b, question_id)
"""
PAIR_EXPLANATIONS_DDL = ("CREATE TABLE IF NOT EXISTS pair_explanations (" + _PAIR_EXPLANATIONS_COLUMNS + ")")
_PAIR_EXPLANATIONS_V8_DDL = """CREATE TABLE IF NOT EXISTS pair_explanations (
  id INTEGER PRIMARY KEY,
  study_a INTEGER NOT NULL REFERENCES studies(id),
  study_b INTEGER NOT NULL REFERENCES studies(id),
  subtopic_id INTEGER REFERENCES subtopics(id),
  metric TEXT NOT NULL,
  reason TEXT,
  quote_a TEXT,
  quote_b TEXT,
  note TEXT,
  status TEXT NOT NULL,
  inputs_hash TEXT NOT NULL,
  prompt_version TEXT,
  created_at TEXT NOT NULL,
  CHECK (study_a < study_b),
  UNIQUE (study_a, study_b, metric)
)"""
PRIMARY_SOURCES_DDL = """CREATE TABLE IF NOT EXISTS primary_sources (
  url TEXT NOT NULL PRIMARY KEY,
  text TEXT,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  fetched_at TEXT,
  last_error TEXT
)"""
PRIMARY_CHECKS_DDL = """CREATE TABLE IF NOT EXISTS primary_checks (
  item_id INTEGER PRIMARY KEY REFERENCES items(id),
  primary_url TEXT NOT NULL,
  numbers TEXT,
  missing TEXT,
  verdict TEXT,
  quote_article TEXT,
  quote_primary TEXT,
  note TEXT,
  status TEXT NOT NULL,
  prompt_version TEXT,
  checked_at TEXT NOT NULL
)"""
QUESTION_EXPLANATIONS_DDL = """CREATE TABLE IF NOT EXISTS question_explanations (
  question_id INTEGER PRIMARY KEY REFERENCES questions(id),
  status TEXT NOT NULL,
  summary TEXT,
  lines TEXT,
  main_result TEXT,
  inputs_hash TEXT NOT NULL,
  prompt_version TEXT,
  created_at TEXT NOT NULL
)"""
ENTITIES_DDL = """CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  merged_into INTEGER REFERENCES entities(id),
  created_at TEXT NOT NULL
)"""
STUDY_ENTITIES_DDL = """CREATE TABLE IF NOT EXISTS study_entities (
  study_id INTEGER NOT NULL REFERENCES studies(id),
  entity_id INTEGER NOT NULL REFERENCES entities(id),
  position INTEGER NOT NULL,
  PRIMARY KEY (study_id, entity_id)
)"""
_LEGACY_KNOWLEDGE_DDL = (_LEGACY_THEMES_DDL, _LEGACY_PAIR_EXPLANATIONS_DDL, PRIMARY_SOURCES_DDL,
                         PRIMARY_CHECKS_DDL)       # step 5 creates these (schema 5-6 names)
KNOWLEDGE_DDL = (SUBTOPICS_DDL, QUESTIONS_DDL, PAIR_EXPLANATIONS_DDL, PRIMARY_SOURCES_DDL, PRIMARY_CHECKS_DDL,
                 *STEP11_DDL, QUESTION_EXPLANATIONS_DDL, ENTITIES_DDL, STUDY_ENTITIES_DDL)
REJECTED_AT_TRIGGER = """CREATE TRIGGER IF NOT EXISTS items_rejected_at AFTER UPDATE OF state ON items
WHEN NEW.state IS NOT OLD.state
BEGIN
  UPDATE items SET rejected_at = CASE WHEN NEW.state IN ('rejected', 'rejected_triage')
    THEN strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now') ELSE NULL END
  WHERE id = NEW.id;
END"""
SCHEMA = (SCHEMA + SOURCES_STATE_DDL + ";\n" + SERIES_DDL + ";\n" + CANONICAL_INDEX + ";\n"
          + "".join(ddl + ";\n" for ddl in KNOWLEDGE_DDL)
          + REJECTED_AT_TRIGGER + ";\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


LATEST_VERSION = 14
FAILING_AFTER = 5


class MigrationError(sqlite3.DatabaseError):
    """A schema migration step failed; the database was left as it was."""


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _quick_check(conn, path, when: str) -> None:
    result = conn.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise MigrationError(f"{path}: integrity check {when} failed: {result}")


def _step1(conn) -> None:
    """0.5 -> 0.6: canonical URLs, text bookkeeping, sources_state replaces source_stats."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for name, decl in (("canonical_url", "TEXT"), ("content_hash", "TEXT"),
                       ("text_source", "TEXT"), ("fetch_status", "TEXT"),
                       ("date_unknown", "INTEGER NOT NULL DEFAULT 0")):
        if name not in cols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {decl}")
    seen = set()
    for item_id, url in conn.execute("SELECT id, url FROM items ORDER BY id").fetchall():
        canon = canonical_url(url)
        if canon in seen:
            canon = None  # an older row already has it; both are processed already
        if canon:
            seen.add(canon)
        conn.execute("UPDATE items SET canonical_url=? WHERE id=?", (canon, item_id))
    conn.execute(CANONICAL_INDEX)
    conn.execute("""UPDATE items SET text_source = CASE
                      WHEN full_text = excerpt THEN 'excerpt'
                      WHEN source_type = 'linkedin' THEN 'feed' ELSE 'page' END
                    WHERE full_text IS NOT NULL AND text_source IS NULL""")
    conn.execute(SOURCES_STATE_DDL)
    if _has_table(conn, "source_stats"):
        conn.execute(
            """INSERT OR IGNORE INTO sources_state
               (name, type, added_at, last_checked, last_error, consecutive_errors, failing_since)
               SELECT source_name, type, COALESCE(last_run, ?), last_run, last_error,
                      CASE WHEN last_error IS NULL THEN 0 ELSE 1 END,
                      CASE WHEN last_error IS NULL THEN NULL ELSE last_run END
               FROM source_stats""", (_now(),))
        conn.execute("DROP TABLE source_stats")


STUDY_IDENTITY_COLUMNS = (("series_id", "INTEGER REFERENCES series(id)"), ("editions", "TEXT"),
                          ("primary_canonical", "TEXT"), ("fingerprint", "TEXT"),
                          ("merged_into", "INTEGER REFERENCES studies(id)"))
ITEM_IDENTITY_COLUMNS = (("first_study_id", "INTEGER"), ("match_reason", "TEXT"))
GRADING_ITEM_COLUMNS = tuple((n, "TEXT") for n in (
    "quotes", "quote_check", "capped_from", "judged_from", "red_flags", "dispute_reason",
    "provenance", "judge_tier"))
GRADING_STUDY_COLUMNS = tuple((n, "TEXT") for n in (
    "metric", "baseline", "conducted_by", "relation", "conflict_of_interest")) + (
    ("created_by_item", "INTEGER"),)
_LEGACY_KNOWLEDGE_ITEM_COLUMNS = (("theme_choice", "TEXT"), ("metric_code", "TEXT"), ("direction", "TEXT"))
_LEGACY_KNOWLEDGE_STUDY_COLUMNS = (("theme_id", "INTEGER REFERENCES themes(id)"), ("metric_code", "TEXT"),
                           ("direction", "TEXT"))
WAVE4A_ITEM_COLUMNS = (("headline", "TEXT"), ("data_date", "TEXT"))
WAVE4A_STUDY_COLUMNS = (("headline_value", "TEXT"), ("headline_label", "TEXT"),
                        ("headline_quote", "TEXT"), ("headline_checked", "INTEGER"),
                        ("data_date", "TEXT"), ("classified_version", "TEXT"))
_LEGACY_WAVE4A_THEME_COLUMNS = (("position", "INTEGER"),)


def _add_columns(conn, table: str, columns) -> None:
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns:
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _loads(raw) -> dict:
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _numbers_of(facts_or_keys: dict, nested: bool, key: str = "match_keys") -> list:
    """key_numbers of a study's match_keys (nested=False) or of an item's facts (nested=True);
    key names the facts field (older schemas stored it under another name; step 2 passes that)."""
    keys = facts_or_keys.get(key) if nested else facts_or_keys
    keys = keys if isinstance(keys, dict) else {}
    values = keys.get("key_numbers")
    return values if isinstance(values, list) else []


def _step2(conn) -> None:
    """0.6 -> 0.7: study identity - series, match fields, merge bookkeeping. Merges nothing."""
    conn.execute(SERIES_DDL)
    _add_columns(conn, "items", ITEM_IDENTITY_COLUMNS)
    if not _has_table(conn, "studies"):
        return  # a 0.5 database: SCHEMA creates studies with the identity columns right after
    _add_columns(conn, "studies", STUDY_IDENTITY_COLUMNS)
    for sid, primary, entities in conn.execute(
            "SELECT id, primary_url, entities FROM studies").fetchall():
        values = list(_numbers_of(_loads(entities), nested=False))
        for (facts,) in conn.execute("SELECT facts FROM items WHERE study_id=?", (sid,)).fetchall():
            values += _numbers_of(_loads(facts), nested=True, key="entities")
        conn.execute("UPDATE studies SET primary_canonical=?, fingerprint=? WHERE id=?",
                     (canonical_url(primary) if primary else None,
                      json.dumps(identity.key_numbers(values)), sid))
    conn.execute("UPDATE items SET state='matched' WHERE state='judged' AND study_id IS NOT NULL")
    conn.execute("UPDATE items SET first_study_id=study_id WHERE study_id IS NOT NULL")


def _step3(conn) -> None:
    """0.7 -> 0.7.2: grading integrity fields (SP2). Existing rows keep everything; new columns stay null."""
    _add_columns(conn, "items", GRADING_ITEM_COLUMNS)
    if _has_table(conn, "studies"):
        _add_columns(conn, "studies", GRADING_STUDY_COLUMNS)


def _step4(conn) -> None:
    """0.7.2 SP2 -> SP5a: the summary a report showed. Rows from before stay null."""
    if _has_table(conn, "report_entries"):
        _add_columns(conn, "report_entries", (("summary", "TEXT"),))


def _step5(conn) -> None:
    """0.7.2 SP5a -> SP3b: themes, theme/metric/direction fields, pair explanations, primary checks.

    Every SP3b table and column at once; SP3b-T and SP3b-P add no step. Rows keep everything."""
    for ddl in _LEGACY_KNOWLEDGE_DDL:
        conn.execute(ddl)
    _add_columns(conn, "items", _LEGACY_KNOWLEDGE_ITEM_COLUMNS)
    if _has_table(conn, "studies"):
        _add_columns(conn, "studies", _LEGACY_KNOWLEDGE_STUDY_COLUMNS)


def _step6(conn) -> None:
    """SP3b -> wave 4a: headline, data date, classify bookkeeping, theme order. Rows keep everything."""
    _add_columns(conn, "items", WAVE4A_ITEM_COLUMNS)
    if _has_table(conn, "studies"):
        _add_columns(conn, "studies", WAVE4A_STUDY_COLUMNS)
    if _has_table(conn, "themes"):
        _add_columns(conn, "themes", _LEGACY_WAVE4A_THEME_COLUMNS)


def _has_column(conn, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


_STEP7_COLUMNS = (("studies", "theme_id", "subtopic_id"), ("pair_explanations", "theme_id", "subtopic_id"),
                  ("items", "theme_choice", "subtopic_choice"), ("studies", "entities", "match_keys"))


def _step7(conn) -> None:
    """Glossary rename: themes -> subtopics, theme_id/theme_choice -> subtopic_*, studies.entities
    and the facts key entities -> match_keys. Each rename only if the old name is still there; the
    renames also rewrite the foreign keys that point at the table. Rows keep everything."""
    if _has_table(conn, "themes") and not _has_table(conn, "subtopics"):
        conn.execute("ALTER TABLE themes RENAME TO subtopics")
    for table, old, new in _STEP7_COLUMNS:
        if _has_table(conn, table) and _has_column(conn, table, old) and not _has_column(conn, table, new):
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    if _has_table(conn, "items") and _has_column(conn, "items", "facts"):
        conn.execute("""UPDATE items SET facts = json_remove(
                          json_set(facts, '$.match_keys', json_extract(facts, '$.entities')), '$.entities')
                        WHERE CASE WHEN json_valid(facts) THEN json_type(facts, '$.entities') END IS NOT NULL""")


def _step8(conn) -> None:
    """Judge rejection reasons: judge_reason and rejected_at columns, the rejected_at trigger, and
    a re-judge of the judge fails (state rejected, no reason recorded, text fetched): they go back
    to extracted so the judge records a reason. Triage fails and legacy imports stay."""
    _add_columns(conn, "items", (("judge_reason", "TEXT"), ("rejected_at", "TEXT")))
    conn.execute(REJECTED_AT_TRIGGER)
    conn.execute("""UPDATE items SET state='extracted', verdict_pass=NULL
                    WHERE state='rejected' AND judge_reason IS NULL AND text_source IS NOT NULL""")


STEP9_STUDY_COLUMNS = (("question_id", "INTEGER REFERENCES questions(id)"), ("question_direction", "TEXT"),
                       ("grouped_version", "TEXT"), ("question_by_owner", "INTEGER NOT NULL DEFAULT 0"))


def _step9(conn) -> None:
    """Questions: the questions table, four study columns, and pair_explanations rebuilt with a
    per-question key (old rows are copied with question_id NULL and never shown or matched)."""
    conn.execute(SUBTOPICS_DDL)
    conn.execute(QUESTIONS_DDL)
    if _has_table(conn, "studies"):
        _add_columns(conn, "studies", STEP9_STUDY_COLUMNS)
    if _has_table(conn, "pair_explanations") and not _has_column(conn, "pair_explanations", "question_id"):
        conn.execute(PAIR_EXPLANATIONS_DDL.replace("pair_explanations", "pair_explanations_new", 1))
        conn.execute("""INSERT INTO pair_explanations_new (id, study_a, study_b, subtopic_id, question_id,
                          metric, reason, quote_a, quote_b, note, status, inputs_hash, prompt_version,
                          created_at)
                        SELECT id, study_a, study_b, subtopic_id, NULL, metric, reason, quote_a, quote_b,
                          note, status, inputs_hash, prompt_version, created_at FROM pair_explanations""")
        conn.execute("DROP TABLE pair_explanations")
        conn.execute("ALTER TABLE pair_explanations_new RENAME TO pair_explanations")


CLASSIFY_VERSION_AT_STEP10 = "classify-1"   # a test asserts it equals classify.PROMPT_VERSION


def _step10(conn) -> None:
    """Question kinds: kind and comparison on questions; old directions (read without a fixed
    comparison) are cleared; studies whose creating item was judged with the knowledge fields count
    as classified."""
    _add_columns(conn, "questions", (("kind", "TEXT"), ("comparison", "TEXT")))
    if _has_table(conn, "studies"):
        conn.execute("UPDATE studies SET question_direction=NULL WHERE question_direction IS NOT NULL")
        conn.execute("""UPDATE studies SET classified_version=? WHERE classified_version IS NULL
                        AND created_by_item IN (SELECT id FROM items
                          WHERE metric_code IS NOT NULL OR subtopic_choice IS NOT NULL)""",
                     (CLASSIFY_VERSION_AT_STEP10,))


def _step11(conn) -> None:
    """Metrics: the metrics and split tables and studies.metric_id. Nothing is filled here: the
    metric list depends on the topic, so seeding fills it (seed_metrics)."""
    for ddl in STEP11_DDL:
        conn.execute(ddl)
    if _has_table(conn, "studies"):
        _add_columns(conn, "studies", (("metric_id", "INTEGER REFERENCES metrics(id)"),))


def _step12(conn) -> None:
    """One explanation per question: the question_explanations table. pair_explanations stays as it is."""
    conn.execute(QUESTION_EXPLANATIONS_DDL)


def _step13(conn) -> None:
    """Entities: the entities and study_entities tables, a checked flag on studies, entity_names on items.
    Nothing is filled here: the judge and classify name entities."""
    conn.execute(ENTITIES_DDL)
    conn.execute(STUDY_ENTITIES_DDL)
    if _has_table(conn, "studies"):
        _add_columns(conn, "studies", (("entities_checked", "INTEGER"),))
    if _has_table(conn, "items"):
        _add_columns(conn, "items", (("entity_names", "TEXT"),))


def _step14(conn) -> None:
    """Study-card embeddings: vectors of the full-text embedder (model without '/card-') are dropped;
    the embed pass fills the card ones."""
    if _has_table(conn, "embeddings"):
        conn.execute("DELETE FROM embeddings WHERE model NOT LIKE '%/card-%'")


STEPS = {1: _step1, 2: _step2, 3: _step3, 4: _step4, 5: _step5, 6: _step6, 7: _step7, 8: _step8,
         9: _step9, 10: _step10, 11: _step11, 12: _step12, 13: _step13, 14: _step14}


def _migrate(conn, path) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target in range(version + 1, LATEST_VERSION + 1):
        _quick_check(conn, path, f"before schema version {target}")
        conn.execute("BEGIN")
        try:
            STEPS[target](conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise MigrationError(
                f"{path}: migration to schema version {target} failed ({exc}); "
                "the database was not changed") from exc
        _quick_check(conn, path, f"after schema version {target}")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > LATEST_VERSION:  # never re-stamp: a later upgrade would redo its steps
            raise MigrationError(
                f"{db_path}: database schema version {version} is newer than this engine "
                f"knows ({LATEST_VERSION}); update Evidence Echo")
        if _has_table(conn, "items"):
            _migrate(conn, db_path)
        conn.executescript(SCHEMA)  # creates what is missing (new database, 0.4 report tables)
        if version < LATEST_VERSION:
            conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


_ITEM_COLUMNS = ("url", "canonical_url", "source_name", "source_type", "title", "excerpt",
                 "published", "fetched_at", "state", "date_unknown", "full_text",
                 "text_source", "fetch_status", "content_hash")
_ITEM_SETTABLE = {"state", "full_text", "text_source", "fetch_status", "content_hash",
                  "triage_reason", "excerpt"}


def insert_item(conn, item: dict) -> int | None:
    row = {"state": "fetched", "date_unknown": 0, "full_text": None, "text_source": None,
           "fetch_status": None, "content_hash": None, **item}
    if not row.get("canonical_url"):
        row["canonical_url"] = canonical_url(row["url"])
    cols = ", ".join(_ITEM_COLUMNS)
    marks = ", ".join(":" + c for c in _ITEM_COLUMNS)
    cur = conn.execute(f"INSERT OR IGNORE INTO items ({cols}) VALUES ({marks})",
                       {c: row.get(c) for c in _ITEM_COLUMNS})
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def insert_items(conn, items: list[dict]) -> int:
    return sum(1 for it in items if insert_item(conn, it) is not None)


def find_by_canonical(conn, canonical: str) -> dict | None:
    row = conn.execute("SELECT * FROM items WHERE canonical_url = ?", (canonical,)).fetchone()
    return dict(row) if row else None


def set_item_fields(conn, item_id: int, **fields) -> None:
    bad = set(fields) - _ITEM_SETTABLE
    if bad:
        raise ValueError(f"not settable: {', '.join(sorted(bad))}")
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id))
    conn.commit()


def revive_seen(conn, item_id: int) -> None:
    conn.execute("UPDATE items SET state='fetched' WHERE id=? AND state='seen'", (item_id,))
    conn.commit()


def flagged_items(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE fetch_status = 'needs_session' ORDER BY id")]


def items_ready(conn, state: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE state = ? AND fetch_status IS NULL ORDER BY id", (state,))]


def items_in_state(conn, state: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM items WHERE state = ? ORDER BY id", (state,))
    return [dict(r) for r in rows]


def get_item(conn, item_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def set_state(conn, item_id: int, state: str) -> None:
    conn.execute("UPDATE items SET state = ? WHERE id = ?", (state, item_id))
    conn.commit()


def set_triage(conn, item_id: int, passed: bool, reason: str) -> None:
    state = "triaged" if passed else "rejected_triage"
    conn.execute(
        "UPDATE items SET triage_pass=?, triage_reason=?, state=? WHERE id=?",
        (int(passed), reason, state, item_id))
    conn.commit()


def set_full_text(conn, item_id: int, text: str) -> None:
    conn.execute("UPDATE items SET full_text=?, state='extracted' WHERE id=?",
                 (text, item_id))
    conn.commit()


def set_verdict(conn, item_id: int, passed: bool, tier: str | None, facts: dict,
                primary_url: str | None, *, quotes=None, quote_check=None, capped_from=None,
                judged_from=None, provenance=None, subtopic_choice=None, metric=None,
                headline: dict | None = None, entity_names: list | None = None,
                data_date: str | None = None, reason: str | None = None) -> None:
    """Judge result. A passing item waits for match, which gives it its study."""
    if _has_table(conn, "embeddings"):   # the card is built from the facts: a new verdict, a new vector
        conn.execute("DELETE FROM embeddings WHERE item_id=?", (item_id,))
    if not passed:
        conn.execute(
            """UPDATE items SET verdict_pass=0, state='rejected', study_id=NULL, first_study_id=NULL,
               match_reason=NULL, quotes=NULL, quote_check=NULL, capped_from=NULL,
               judged_from=NULL, provenance=NULL, judge_tier=NULL,
               subtopic_choice=NULL, metric_code=NULL, entity_names=NULL,
               headline=NULL, data_date=NULL, judge_reason=? WHERE id=?""", (reason, item_id))
        conn.commit()
        return
    conn.execute(
        """UPDATE items SET verdict_pass=1, evidence_tier=?, judge_tier=?, facts=?, primary_url=?,
           quotes=?, quote_check=?, capped_from=?, judged_from=?, provenance=?,
           subtopic_choice=?, metric_code=?, headline=?, data_date=?, entity_names=?,
           study_id=NULL, first_study_id=NULL, match_reason=NULL, judge_reason=NULL,
           state='judged' WHERE id=?""",
        (tier, tier, json.dumps(facts), primary_url,
         None if quotes is None else json.dumps(quotes), quote_check, capped_from, judged_from,
         None if provenance is None else json.dumps(provenance),
         None if subtopic_choice is None else json.dumps(subtopic_choice), metric,
         None if headline is None else json.dumps(headline), data_date,
         None if entity_names is None else json.dumps(clean_entity_names(entity_names)), item_id))
    conn.commit()


def get_study(conn, study_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM studies WHERE id = ?", (study_id,)).fetchone()
    return dict(row) if row else None


def _json_list(raw) -> list:
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def study_lists(study: dict) -> tuple[list, list]:
    return _json_list(study.get("editions")), _json_list(study.get("fingerprint"))


def _editions(facts: dict) -> list[str]:
    raw = facts.get("editions")
    return sorted({str(e).strip() for e in raw if str(e).strip()}) if isinstance(raw, list) else []


def get_or_create_series(conn, name) -> int | None:
    key = identity.series_key(name)
    if not key:
        return None
    row = conn.execute("SELECT id FROM series WHERE key=?", (key,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO series (name, key, created_at) VALUES (?,?,?)",
                        (name.strip(), key, _now())).lastrowid


def headline_columns(headline: dict | None) -> tuple:
    """(value, label, quote, checked 0/1) of a check_headline dict, or four Nones."""
    if not isinstance(headline, dict):
        return (None, None, None, None)
    return (headline.get("value"), headline.get("label"), headline.get("quote"),
            1 if headline.get("checked") else 0)


def create_study(conn, tier, facts: dict, primary_url, *, provenance=None,
                 created_by_item=None, subtopic_id=None, metric=None,
                 headline: dict | None = None, data_date: str | None = None,
                 entity_names: list | None = None) -> int:
    """entity_names: the creating item's entities (a list, even []: the study counts as asked and links
    them; None: judged before step 13, classify asks).
    metric: the judge's metric slug (or an old fixed code), stored as is in metric_code; the study's
    metric_id is set when it names an active metric (an old code resolves later, in seed_metrics'
    legacy fill). facts["metric"] stays SP2's free-text measure in studies.metric."""
    found = metric_for_slug(conn, metric)
    prov = provenance if isinstance(provenance, dict) else {}
    cur = conn.execute(
        """INSERT INTO studies
           (canonical_name, what_was_tested, findings, evidence_tier, research_timeframe,
            sample_scale, has_controls, methodology_stated, duration, match_keys, primary_url,
            series_id, editions, primary_canonical, fingerprint, metric, baseline, conducted_by,
            relation, conflict_of_interest, created_by_item, created_at,
            subtopic_id, metric_code, metric_id,
            headline_value, headline_label, headline_quote, headline_checked, data_date)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (facts.get("what_was_tested") or "(unnamed study)", facts.get("what_was_tested"),
         facts.get("findings"), tier, facts.get("research_timeframe"), facts.get("sample_scale"),
         None if facts.get("has_controls") is None else int(facts["has_controls"]),
         None if facts.get("methodology_stated") is None else int(facts["methodology_stated"]),
         facts.get("duration"), json.dumps(facts.get("match_keys") or {}), primary_url,
         get_or_create_series(conn, facts.get("series")), json.dumps(_editions(facts)),
         canonical_url(primary_url) if primary_url else None,
         json.dumps(identity.key_numbers(_numbers_of(facts, nested=True))),
         facts.get("metric"), facts.get("baseline"), prov.get("conducted_by"),
         prov.get("relation"), prov.get("conflict_of_interest"), created_by_item, _now(),
         subtopic_id, metric, found["id"] if found else None, *headline_columns(headline), data_date))
    conn.commit()
    if entity_names is not None:
        link_study_entities(conn, cur.lastrowid, entity_names)
    return cur.lastrowid


def item_entity_names(item: dict) -> list | None:
    """The judge's entity names stored on an item; None when it was judged before step 13."""
    raw = item.get("entity_names")
    return None if raw is None else _json_list(raw)


def assign_study(conn, item_id: int, study_id: int, reason: str) -> None:
    """The item joins the study; the study keeps its tier and gains editions, numbers, a primary url."""
    item = get_item(conn, item_id)
    facts = _loads(item["facts"])
    study = get_study(conn, study_id)
    editions, numbers = study_lists(study)
    editions = sorted(set(editions) | set(_editions(facts)))
    numbers = sorted(set(numbers) | set(identity.key_numbers(_numbers_of(facts, nested=True))))
    primary, canon = study["primary_url"], study["primary_canonical"]
    if not primary and item["primary_url"]:
        primary, canon = item["primary_url"], canonical_url(item["primary_url"])
    prov = _loads(item.get("provenance"))
    fill = {"metric": facts.get("metric"), "baseline": facts.get("baseline"),
            "conducted_by": prov.get("conducted_by"), "relation": prov.get("relation"),
            "conflict_of_interest": prov.get("conflict_of_interest"),
            "subtopic_id": subtopic_of_item(conn, item),             # SP3b-T: a joining item fills nulls only
            "metric_code": item.get("metric_code")}
    for col, value in fill.items():  # col comes from the fixed dict above, never from input
        if value is not None and study.get(col) is None:
            conn.execute(f"UPDATE studies SET {col}=? WHERE id=?", (value, study_id))
    joining = metric_for_slug(conn, item.get("metric_code"))
    if joining is not None and study_metric(conn, study) is None:
        conn.execute("UPDATE studies SET metric_id=? WHERE id=?", (joining["id"], study_id))
    if study.get("data_date") is None and item.get("data_date"):
        conn.execute("UPDATE studies SET data_date=? WHERE id=?", (item["data_date"], study_id))
    headline = _loads(item.get("headline"))
    if headline and (study.get("headline_quote") is None
                     or (not study.get("headline_checked") and headline.get("checked"))):
        conn.execute("""UPDATE studies SET headline_value=?, headline_label=?, headline_quote=?,
                        headline_checked=? WHERE id=?""", (*headline_columns(headline), study_id))
    conn.execute("""UPDATE studies SET editions=?, fingerprint=?, primary_url=?, primary_canonical=?
                    WHERE id=?""", (json.dumps(editions), json.dumps(numbers), primary, canon, study_id))
    joining_entities = item_entity_names(item)
    if study.get("entities_checked") is None and joining_entities is not None:   # a checked study never changes
        link_study_entities(conn, study_id, joining_entities)
    conn.execute("""UPDATE items SET study_id=?, first_study_id=COALESCE(first_study_id, ?),
                    match_reason=?, state='matched' WHERE id=?""",
                 (study_id, study_id, reason, item_id))
    conn.commit()


def resolve_study(conn, study_id: int) -> int:
    seen = set()
    while study_id not in seen:
        seen.add(study_id)
        row = conn.execute("SELECT merged_into FROM studies WHERE id=?", (study_id,)).fetchone()
        if not row or row["merged_into"] is None:
            return study_id
        study_id = row["merged_into"]
    return study_id  # a cycle (never written by this engine): stop where it closes


def active_studies(conn) -> list[dict]:
    rows = conn.execute("""SELECT s.*, r.key AS series_key, r.name AS series_name FROM studies s
                           LEFT JOIN series r ON r.id = s.series_id
                           WHERE s.merged_into IS NULL ORDER BY s.id""")
    out = []
    for row in rows:
        study = dict(row)
        study["editions"], study["fingerprint"] = study_lists(study)
        study["series_key"] = study["series_key"] or ""
        out.append(study)
    return out


DUPLICATE_RULES = ("same primary url", "same series and edition", "shared key numbers", "similar text")


def _pair_rule(a: dict, b: dict) -> str | None:
    if a["primary_canonical"] and a["primary_canonical"] == b["primary_canonical"]:
        return "same primary url"
    if a["series_key"] and a["series_key"] == b["series_key"] and set(a["editions"]) & set(b["editions"]):
        return "same series and edition"
    shared = sorted(set(a["fingerprint"]) & set(b["fingerprint"]))
    if len(shared) >= 2:
        return "shared key numbers: " + ", ".join(shared)
    return None


def duplicate_pairs(conn, vectors: dict | None = None, threshold: float = 0.93) -> list[dict]:
    """Likely duplicate study pairs. With vectors (study id -> unit vector), a pair no other rule caught
    and with cosine >= threshold is a "similar text" pair, ranked after every other rule."""
    studies = active_studies(conn)
    pairs = []
    for i, a in enumerate(studies):
        for b in studies[i + 1:]:
            rule, sim = _pair_rule(a, b), 0.0
            clash = bool(a["editions"]) and bool(b["editions"]) and not set(a["editions"]) & set(b["editions"])
            if not rule and not clash and vectors and a["id"] in vectors and b["id"] in vectors:
                sim = float(vectors[a["id"]] @ vectors[b["id"]])
                if sim >= threshold:
                    rule = f"similar text: {sim:.2f}"
            if rule:
                pairs.append({"keep": a["id"], "other": b["id"], "rule": rule,
                              "keep_study": a, "other_study": b, "_sim": sim})
    rank = {r: n for n, r in enumerate(DUPLICATE_RULES)}
    pairs.sort(key=lambda p: (rank[p["rule"].split(":")[0]], -p["_sim"], p["keep"], p["other"]))
    for p in pairs:
        del p["_sim"]
    return pairs


def _recompute_study(conn, study_id: int, primary: bool = False) -> None:
    """editions and fingerprint from the study's own match_keys and its items' facts.
    primary=True also drops a primary url no own item carries (one a merge copied in)."""
    study = get_study(conn, study_id)
    values, editions = list(_numbers_of(_loads(study["match_keys"]), nested=False)), set()
    own_urls = []
    for facts, url in conn.execute("SELECT facts, primary_url FROM items WHERE study_id=? ORDER BY id",
                                   (study_id,)).fetchall():
        facts = _loads(facts)
        values += _numbers_of(facts, nested=True)
        editions |= set(_editions(facts))
        if url:
            own_urls.append(url)
    if primary and study["primary_canonical"] not in {canonical_url(u) for u in own_urls}:
        url = own_urls[0] if own_urls else None
        conn.execute("UPDATE studies SET primary_url=?, primary_canonical=? WHERE id=?",
                     (url, canonical_url(url) if url else None, study_id))
    conn.execute("UPDATE studies SET editions=?, fingerprint=? WHERE id=?",
                 (json.dumps(sorted(editions)), json.dumps(identity.key_numbers(values)), study_id))


def merge_studies(conn, keep: int, other: int) -> dict:
    if keep == other:
        raise ConfigError("cannot merge a study with itself")
    a, b = get_study(conn, keep), get_study(conn, other)
    for sid, study in ((keep, a), (other, b)):
        if study is None:
            raise ConfigError(f"no study {sid}")
        if study["merged_into"] is not None:
            raise ConfigError(f"study {sid} is already merged into {study['merged_into']}")
    moved = conn.execute("UPDATE items SET study_id=? WHERE study_id=?", (keep, other)).rowcount
    ea, fa = study_lists(a)
    eb, fb = study_lists(b)
    primary, canon = a["primary_url"], a["primary_canonical"]
    if not primary and b["primary_url"]:
        primary, canon = b["primary_url"], b["primary_canonical"]
    conn.execute("""UPDATE studies SET editions=?, fingerprint=?, primary_url=?, primary_canonical=?
                    WHERE id=?""", (json.dumps(sorted(set(ea) | set(eb))),
                                    json.dumps(sorted(set(fa) | set(fb))), primary, canon, keep))
    for (entity_id,) in conn.execute("SELECT entity_id FROM study_entities WHERE study_id=? ORDER BY position",
                                     (other,)).fetchall():
        if not conn.execute("SELECT 1 FROM study_entities WHERE study_id=? AND entity_id=?",
                            (keep, entity_id)).fetchone():
            _append_link(conn, keep, entity_id)
    if a.get("entities_checked") or b.get("entities_checked"):
        conn.execute("UPDATE studies SET entities_checked=1 WHERE id=?", (keep,))
    conn.execute("UPDATE studies SET merged_into=? WHERE id=?", (keep, other))
    conn.commit()
    return {"kept": keep, "merged": other, "moved_items": moved}


def unmerge_study(conn, other: int) -> dict:
    study = get_study(conn, other)
    if study is None:
        raise ConfigError(f"no study {other}")
    if study["merged_into"] is None:
        raise ConfigError(f"study {other} was never merged")
    chained = conn.execute("SELECT MIN(id) FROM studies WHERE merged_into=?", (other,)).fetchone()[0]
    if chained is not None:
        raise ConfigError(f"study {chained} was merged into study {other}; unmerge study {chained} first")
    into = resolve_study(conn, study["merged_into"])
    moved = conn.execute("UPDATE items SET study_id=? WHERE first_study_id=? AND study_id=?",
                         (other, other, into)).rowcount
    conn.execute("UPDATE studies SET merged_into=NULL WHERE id=?", (other,))
    _recompute_study(conn, other)
    _recompute_study(conn, into, primary=True)
    conn.commit()
    return {"restored": other, "from": into, "moved_items": moved}


def first_reports(conn) -> dict[int, dict]:
    out = {}
    for row in conn.execute("""SELECT e.study_id, e.title, r.rendered_at FROM report_entries e
                               JOIN reports r ON r.id = e.report_id
                               ORDER BY r.rendered_at, r.id, e.rowid"""):
        out.setdefault(resolve_study(conn, row["study_id"]),
                       {"title": row["title"], "rendered_at": row["rendered_at"]})
    return out


def first_report_ids(conn) -> dict[int, int]:
    """study id (merges resolved) -> id of the report that first included it, in first_reports' order."""
    out: dict[int, int] = {}
    for row in conn.execute("""SELECT e.study_id, r.id AS report_id FROM report_entries e
                               JOIN reports r ON r.id = e.report_id
                               ORDER BY r.rendered_at, r.id, e.rowid"""):
        out.setdefault(resolve_study(conn, row["study_id"]), row["report_id"])
    return out


def source_item_stats(conn) -> list[dict]:
    """One row per source_name in the items table, name order:
    {"name", "type", "fetched_at": [iso, ...], "study_ids": set[int]}. study_ids holds the studies
    (merges resolved) the source's items belong to, only those some report included, and only through
    items the topic page reads (STUDY_ITEM_STATES), so both pages agree on a source's studies."""
    reported = first_report_ids(conn)
    by_name: dict[str, dict] = {}
    resolved: dict[int, int] = {}
    for row in conn.execute("SELECT source_name, source_type, fetched_at, study_id, state FROM items"):
        entry = by_name.setdefault(row["source_name"], {"name": row["source_name"],
                                                        "type": row["source_type"],
                                                        "fetched_at": [], "study_ids": set()})
        entry["fetched_at"].append(row["fetched_at"])
        if row["study_id"] is not None and row["state"] in STUDY_ITEM_STATES:
            if row["study_id"] not in resolved:
                resolved[row["study_id"]] = resolve_study(conn, row["study_id"])
            sid = resolved[row["study_id"]]
            if sid in reported:
                entry["study_ids"].add(sid)
    return [by_name[name] for name in sorted(by_name)]


def studies_overview(conn, include_merged: bool) -> list[dict]:
    first = first_reports(conn)
    where = "" if include_merged else "WHERE s.merged_into IS NULL"
    rows = conn.execute(f"""SELECT s.id, s.canonical_name, s.evidence_tier, s.editions, s.merged_into,
                                   r.name AS series, (SELECT COUNT(*) FROM items i WHERE i.study_id = s.id) AS coverage
                            FROM studies s LEFT JOIN series r ON r.id = s.series_id {where} ORDER BY s.id""")
    out = []
    for row in rows:
        study = dict(row)
        study["editions"] = _json_list(study["editions"])
        rep = first.get(study["id"])
        study["first_reported"] = rep["rendered_at"][:10] if rep else None
        out.append(study)
    return out


def set_skeptic(conn, item_id: int, agrees: bool, adjusted_tier: str | None,
                note: str, resolved: bool = False) -> None:
    """resolved=True (an owner ruling that settles a dispute) also clears dispute_reason."""
    conn.execute(
        """UPDATE items SET skeptic_agrees=?, skeptic_tier=?, skeptic_note=?,
           disputed=?, dispute_reason=CASE WHEN ? THEN NULL ELSE dispute_reason END,
           state='verified' WHERE id=?""",
        (int(agrees), adjusted_tier, note, int(not agrees), int(resolved), item_id))
    conn.commit()


def set_blind_grade(conn, item_id: int, tier: str, red_flags: list, note: str,
                    final_tier: str, disputed: bool, dispute_reason: str | None) -> None:
    """Blind skeptic result, compared by grading.compare. A study this item created takes the
    final tier; a study it joined keeps its own (SP3a rule). A pre-SP2 item (judge_tier NULL)
    keeps its old tier as judge_tier, so reset --stage verify can restore it."""
    item = get_item(conn, item_id)
    judge_tier = item["judge_tier"] or item["evidence_tier"]
    conn.execute(
        """UPDATE items SET skeptic_tier=?, skeptic_agrees=?, skeptic_note=?, red_flags=?,
           disputed=?, dispute_reason=?, judge_tier=COALESCE(judge_tier, evidence_tier),
           evidence_tier=?, state='verified' WHERE id=?""",
        (tier, int(tier == judge_tier), note, json.dumps(list(red_flags)), int(disputed),
         dispute_reason, final_tier, item_id))
    conn.execute("UPDATE studies SET evidence_tier=? WHERE created_by_item=? AND id=?",
                 (final_tier, item_id, item["study_id"]))
    conn.commit()


def clear_blind_grades(conn) -> None:
    """After reset --stage verify: skeptic fields cleared, the judge's tier restored."""
    conn.execute("""UPDATE studies SET evidence_tier=(SELECT i.judge_tier FROM items i
                      WHERE i.id=studies.created_by_item)
                    WHERE created_by_item IN (SELECT id FROM items WHERE state='matched'
                      AND skeptic_tier IS NOT NULL AND judge_tier IS NOT NULL)""")
    conn.execute("""UPDATE items SET evidence_tier=COALESCE(judge_tier, evidence_tier),
                      skeptic_tier=NULL, skeptic_agrees=NULL, skeptic_note=NULL, red_flags=NULL,
                      disputed=0, dispute_reason=NULL
                    WHERE state='matched'""")
    conn.commit()


def set_ruled_tier(conn, item_id: int, tier: str) -> None:
    """Owner ruling: the item's study takes this tier (it covers every source of the study)."""
    conn.execute("UPDATE items SET evidence_tier=? WHERE id=?", (tier, item_id))
    conn.execute("""UPDATE studies SET evidence_tier=?
                    WHERE id=(SELECT study_id FROM items WHERE id=?)""", (tier, item_id))
    conn.commit()


def save_vector(conn, item_id: int, model: str, vector: bytes) -> None:
    """Store an item's vector for one embedder tag; the item's state is not touched."""
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (item_id, model, vector) VALUES (?,?,?)",
        (item_id, model, vector))
    conn.commit()


_SOURCE_SETTABLE = {"done", "backfilled_at", "cursor", "blocked", "consecutive_errors",
                    "failing_since", "last_error"}


def _ensure_source(conn, name: str, type_: str) -> None:
    conn.execute("INSERT OR IGNORE INTO sources_state (name, type, added_at) VALUES (?,?,?)",
                 (name, type_, _now()))


def forget_source(conn, name: str) -> None:
    conn.execute("DELETE FROM sources_state WHERE name=?", (name,))
    conn.commit()


def get_source_state(conn, name: str) -> dict | None:
    row = conn.execute("SELECT rowid AS rowid, * FROM sources_state WHERE name=?",
                       (name,)).fetchone()
    return dict(row) if row else None


def all_source_states(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT rowid AS rowid, * FROM sources_state ORDER BY name")]


def record_fetch(conn, name: str, type_: str, *, new: int, error: str | None,
                 blocked: bool = False, cursor: dict | None = None) -> None:
    _ensure_source(conn, name, type_)
    now = _now()
    if error:
        conn.execute("""UPDATE sources_state SET last_checked=?, last_error=?, blocked=?,
                          consecutive_errors=consecutive_errors+1,
                          failing_since=COALESCE(failing_since, ?) WHERE name=?""",
                     (now, error, int(blocked), now, name))
    else:
        conn.execute("""UPDATE sources_state SET last_checked=?, last_error=NULL, blocked=0,
                          consecutive_errors=0, failing_since=NULL,
                          items_total=items_total+?,
                          last_new_item=CASE WHEN ?>0 THEN ? ELSE last_new_item END
                        WHERE name=?""", (now, new, new, now, name))
    if cursor is not None:
        conn.execute("UPDATE sources_state SET cursor=? WHERE name=?", (json.dumps(cursor), name))
    conn.commit()


def set_source_fields(conn, name: str, type_: str, **fields) -> None:
    bad = set(fields) - _SOURCE_SETTABLE
    if bad:
        raise ValueError(f"not settable: {', '.join(sorted(bad))}")
    _ensure_source(conn, name, type_)
    values = [json.dumps(v) if k == "cursor" and isinstance(v, dict) else v
              for k, v in fields.items()]
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE sources_state SET {sets} WHERE name=?", (*values, name))
    conn.commit()


def log_run(conn, stage: str, detail: str = "") -> None:
    conn.execute("INSERT INTO runs (started_at, stage, detail) VALUES (?,?,?)",
                 (_now(), stage, detail))
    conn.commit()


def record_report(conn, path: str, entries: list[tuple]) -> int:
    """Which studies a rendered report showed, under which titles (for the overview), and the
    summary it showed: entries are (study_id, title) or (study_id, title, summary)."""
    cur = conn.execute("INSERT INTO reports (path, rendered_at) VALUES (?, ?)", (path, _now()))
    rows = [(cur.lastrowid, entry[0], entry[1], entry[2] if len(entry) > 2 else None)
            for entry in entries]
    conn.executemany("INSERT INTO report_entries (report_id, study_id, title, summary) "
                     "VALUES (?,?,?,?)", rows)
    conn.commit()
    return cur.lastrowid


def list_reports(conn) -> list[dict]:
    """Every recorded report {"id", "path", "rendered_at", "studies"}, oldest first; studies = the
    new studies it showed (entries with a summary; seen-before coverage has none)."""
    return [dict(r) for r in conn.execute(
        """SELECT r.id, r.path, r.rendered_at,
                  (SELECT COUNT(*) FROM report_entries e
                   WHERE e.report_id = r.id AND e.summary IS NOT NULL) AS studies
           FROM reports r ORDER BY r.rendered_at, r.id""")]


def latest_report_time(conn) -> str | None:
    return conn.execute("SELECT MAX(rendered_at) FROM reports").fetchone()[0]


def rejected_items(conn) -> list[dict]:
    """Every rejected item (triage or judge), every column."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM items WHERE state IN ('rejected_triage', 'rejected') ORDER BY id")]


def report_history(conn) -> list[dict]:
    """Every report entry, oldest report first (the order first_reports reads them in)."""
    return [dict(r) for r in conn.execute(
        """SELECT e.report_id, r.rendered_at, e.study_id, e.title, e.summary
           FROM report_entries e JOIN reports r ON r.id = e.report_id
           ORDER BY r.rendered_at, r.id, e.rowid""")]


def study_items(conn, study_id: int, states: tuple[str, ...]) -> list[dict]:
    marks = ",".join("?" * len(states))
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM items WHERE study_id=? AND state IN ({marks}) ORDER BY id",
        (study_id, *states))]


def series_name(conn, series_id) -> str | None:
    if not series_id:
        return None
    row = conn.execute("SELECT name FROM series WHERE id=?", (series_id,)).fetchone()
    return row["name"] if row else None


def latest_report(conn) -> dict | None:
    row = conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    entries = conn.execute(
        """SELECT e.study_id, e.title, s.evidence_tier AS tier,
                  (SELECT MIN(i.published) FROM items i WHERE i.study_id = e.study_id) AS published
           FROM report_entries e JOIN studies s ON s.id = e.study_id
           WHERE e.report_id = ? ORDER BY e.rowid""", (row["id"],))
    return {"path": row["path"], "rendered_at": row["rendered_at"],
            "entries": [dict(e) for e in entries]}


_TIER_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def recent_studies(conn, limit: int) -> dict:
    """The studies the overview lists for a topic: {"latest": [...], "earlier": [...], "more": int}.

    A study belongs to the report that first included it (merges resolved), so a seen-before study
    shown again as "More coverage" is never one of a later report's studies. Rows are
    {"study_id", "title", "tier", "report_path", "rendered_at", "card_id"}; card_id is the study id
    the report showed (its card is #s<card_id> there), before any later merge. latest = the newest report's studies
    (highest report id): all of them when limit == 0, else the best-graded `limit` (ties in report
    order) and `more` counts the rest. earlier = studies first reported by older reports, the
    newest first report first (ties by tier), filling `limit` studies in all; empty when limit == 0."""
    newest = conn.execute("SELECT MAX(id) FROM reports").fetchone()[0]
    if newest is None:
        return {"latest": [], "earlier": [], "more": 0}
    tiers = {r["id"]: r["evidence_tier"] for r in conn.execute("SELECT id, evidence_tier FROM studies")}
    reports = {r["id"]: (r["path"], r["rendered_at"], n) for n, r in enumerate(conn.execute(
        "SELECT id, path, rendered_at FROM reports ORDER BY rendered_at, id"))}
    first: dict[int, dict] = {}
    for order, row in enumerate(report_history(conn)):
        sid = resolve_study(conn, row["study_id"])
        if sid in first:
            continue
        path, rendered_at, position = reports[row["report_id"]]
        first[sid] = {"study_id": sid, "title": row["title"], "tier": tiers.get(sid),
                      "report_path": path, "rendered_at": rendered_at, "card_id": row["study_id"],
                      "_report": row["report_id"],
                      "_position": position, "_order": order}

    def tier_key(r):
        return _TIER_RANK.get(r["tier"], 9)

    def public(rows):
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    latest = sorted((r for r in first.values() if r["_report"] == newest), key=lambda r: r["_order"])
    if limit == 0:
        return {"latest": public(latest), "earlier": [], "more": 0}
    shown = sorted(latest, key=lambda r: (tier_key(r), r["_order"]))[:limit]
    older = sorted((r for r in first.values() if r["_report"] != newest),
                   key=lambda r: (-r["_position"], tier_key(r), r["_order"]))
    return {"latest": public(shown), "earlier": public(older[:max(0, limit - len(shown))]),
            "more": len(latest) - len(shown)}


# --- SP3b-T: subtopics ---------------------------------------------------------------------------
SUBTOPIC_NAME_MAX = 80
SUBTOPIC_ACTIVE = ("seeded", "confirmed")
SUBTOPIC_RETIRED = "retired"
NO_SUBTOPIC_STATUSES = ("rejected", SUBTOPIC_RETIRED)   # a choice that resolves here gives no subtopic
_SUBTOPIC_STATUS_ORDER = {"seeded": 0, "confirmed": 1, "proposed": 2, "rejected": 3, "retired": 4}


def subtopic_slug(name) -> str:
    """Lower-cased, every run of non-alphanumerics -> '-', trimmed ('' when nothing is left)."""
    return re.sub(r"[\W_]+", "-", str(name or "").lower()).strip("-")


def _clean_name(name) -> str:
    return " ".join(str(name or "").split())


def list_subtopics(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM subtopics ORDER BY id")]


def get_subtopic(conn, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM subtopics WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def _insert_subtopic(conn, name: str, status: str) -> int:
    return conn.execute("INSERT INTO subtopics (slug, name, status, created_at) VALUES (?,?,?,?)",
                        (subtopic_slug(name), name, status, _now())).lastrowid


def is_pending(subtopic: dict) -> bool:
    """A proposal still waiting for the owner (not accepted, mapped or rejected)."""
    return subtopic["status"] == "proposed" and subtopic["merged_into"] is None


def seed_subtopics(conn, names) -> int:
    """The topic's subtopics list as 'seeded' subtopics: every name whose slug the database lacks (on
    the first command, the whole list). A name is matched to a subtopic by slug or by name (case and
    spacing ignored), so a renamed subtopic is never duplicated. A pending proposal that is listed
    becomes 'seeded': the owner listed it. A rejected, retired or merged subtopic is left alone.
    Each listed subtopic gets its list position (display order). Returns how many subtopics were added
    or promoted."""
    subtopics = list_subtopics(conn)
    changed = 0
    for raw in names or []:
        name, slug = _clean_name(raw), subtopic_slug(raw)
        if not slug:
            continue
        subtopic = _listed_subtopic(subtopics, name)
        if subtopic is None:
            subtopics.append({"id": _insert_subtopic(conn, name, "seeded"), "slug": slug, "name": name,
                           "status": "seeded", "merged_into": None})
            changed += 1
        elif is_pending(subtopic) and _split_of(conn, "subtopics", subtopic) is None:
            # the topic's spelling replaces the judge's (a split name waits for ev split)
            conn.execute("UPDATE subtopics SET status='seeded', name=? WHERE id=?", (name, subtopic["id"]))
            subtopic.update(status="seeded", name=name)
            changed += 1
    if _set_positions(conn, names) or changed:
        conn.commit()
    return changed


def _listed_subtopic(subtopics, name: str) -> dict | None:
    """The subtopic a listed name stands for: its slug first, then its name ignoring case/spacing."""
    slug, key = subtopic_slug(name), _clean_name(name).casefold()
    return (next((t for t in subtopics if t["slug"] == slug), None)
            or next((t for t in subtopics if _clean_name(t["name"]).casefold() == key), None))


def _set_positions(conn, names, table: str = "subtopics") -> int:
    """<table>.position = index in the topic's list for the listed rows, NULL for all others.
    Only rows whose position changes are written; returns how many. `table` is subtopics or metrics."""
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
    wanted = {}
    for index, raw in enumerate(names or []):
        row = _listed_subtopic(rows, _clean_name(raw)) if subtopic_slug(raw) else None
        if row is not None:
            wanted[row["id"]] = index
    changed = 0
    for row in rows:
        if row["position"] != wanted.get(row["id"]):
            conn.execute(f"UPDATE {table} SET position=? WHERE id=?", (wanted.get(row["id"]), row["id"]))
            changed += 1
    return changed


def resolve_subtopic(conn, subtopic_id) -> dict | None:
    """The subtopic a (possibly merged or mapped) subtopic id ends at; None for no subtopic."""
    seen, row = set(), None
    while subtopic_id is not None and subtopic_id not in seen:
        seen.add(subtopic_id)
        row = conn.execute("SELECT * FROM subtopics WHERE id=?", (subtopic_id,)).fetchone()
        if row is None:
            return None
        subtopic_id = row["merged_into"]
    return dict(row) if row else None


def subtopic_order(subtopic: dict) -> tuple:
    """Display order: seeded subtopics in the topic's list order (those no longer listed after the
    listed ones), then confirmed ones, oldest first."""
    position = subtopic.get("position")
    listed = subtopic["status"] == "seeded" or (subtopic["status"] == "confirmed" and position is not None)
    return (0 if listed else 1, position if listed and position is not None else 10 ** 9, subtopic["id"])


def active_subtopics(conn) -> list[dict]:
    """Subtopics the judge may pick: seeded or confirmed, not merged away."""
    return sorted((t for t in list_subtopics(conn)
                   if t["status"] in SUBTOPIC_ACTIVE and t["merged_into"] is None), key=subtopic_order)


def _find_subtopic(conn, name: str) -> dict | None:
    """A subtopic with this name's slug, or the same name ignoring case and spacing."""
    slug, key = subtopic_slug(name), _clean_name(name).casefold()
    for subtopic in list_subtopics(conn):
        if subtopic["slug"] == slug or _clean_name(subtopic["name"]).casefold() == key:
            return subtopic
    return None


def subtopic_for_choice(conn, choice, create: bool = False) -> tuple[int | None, str | None]:
    """The judge's subtopic answer -> (subtopic id or None, error or None). {"slug": s} must name a
    stored subtopic. {"propose": name} reuses a subtopic with that slug or name; otherwise, with
    create=True, it adds a 'proposed' subtopic. The id is resolved through merges and maps; a
    rejected subtopic gives None (the study stays Unsorted). The shape is checked by contracts."""
    if not isinstance(choice, dict):
        return None, None
    if "slug" in choice:
        found = get_subtopic(conn, choice["slug"])
        if found is None:
            return None, f"subtopic: unknown slug {choice['slug']!r}"
    else:
        name = _clean_name(choice.get("propose"))
        found = _find_subtopic(conn, name)
        if found is not None and _split_of(conn, "subtopics", found) is not None:
            return None, None            # a split name waiting for the owner is never reused
        if found is None:
            if not create:
                return None, None
            subtopic_id = _insert_subtopic(conn, name, "proposed")
            conn.commit()
            return subtopic_id, None
    if _split_of(conn, "subtopics", found) is not None:
        return None, None
    final = resolve_subtopic(conn, found["id"])
    if final is None or final["status"] in NO_SUBTOPIC_STATUSES:
        return None, None
    return final["id"], None


def choice_is_split_name(conn, choice) -> bool:
    """The subtopic answer names a split name still waiting for the owner (it counts as no choice, and
    the study must stay in classify's queue)."""
    if not isinstance(choice, dict):
        return False
    found = (get_subtopic(conn, choice["slug"]) if "slug" in choice
             else _find_subtopic(conn, _clean_name(choice.get("propose"))))
    return found is not None and _split_of(conn, "subtopics", found) is not None


def subtopic_of_item(conn, item: dict) -> int | None:
    """The subtopic id the item's stored judge answer resolves to now (no subtopic is ever created)."""
    try:
        choice = json.loads(item.get("subtopic_choice") or "null")
    except ValueError:
        return None
    return subtopic_for_choice(conn, choice)[0]


def _subtopic_or_error(conn, slug: str) -> dict:
    subtopic = get_subtopic(conn, slug)
    if subtopic is None:
        raise ConfigError(f"no subtopic '{slug}' (ev subtopics list shows them)")
    return subtopic


def _merged_note(conn, subtopic: dict) -> str:
    into = conn.execute("SELECT slug FROM subtopics WHERE id=?", (subtopic["merged_into"],)).fetchone()
    return f", merged into '{into['slug']}'" if into else ""


def _pending_or_error(conn, slug: str) -> dict:
    subtopic = _subtopic_or_error(conn, slug)
    _refuse_split_name(conn, "subtopics", subtopic)
    if not is_pending(subtopic):
        raise ConfigError(f"subtopic '{slug}' is not a proposal waiting for review "
                          f"(status {subtopic['status']}{_merged_note(conn, subtopic)})")
    return subtopic


def _active_or_error(conn, slug: str) -> dict:
    subtopic = _subtopic_or_error(conn, slug)
    if subtopic["status"] not in SUBTOPIC_ACTIVE or subtopic["merged_into"] is not None:
        raise ConfigError(f"subtopic '{slug}' is not an active subtopic "
                          f"(status {subtopic['status']}{_merged_note(conn, subtopic)})")
    return subtopic


def _move_studies(conn, from_id: int, to_id: int | None) -> int:
    return conn.execute("UPDATE studies SET subtopic_id=? WHERE subtopic_id=?", (to_id, from_id)).rowcount


def accept_subtopic(conn, slug: str) -> dict:
    subtopic = _pending_or_error(conn, slug)
    conn.execute("UPDATE subtopics SET status='confirmed' WHERE id=?", (subtopic["id"],))
    conn.commit()
    return {"accepted": slug}


def map_subtopic(conn, slug: str, target: str) -> dict:
    """A proposal is an existing subtopic under another name: its studies move, and later proposals
    of the same name resolve to the target."""
    subtopic = _pending_or_error(conn, slug)
    into = _active_or_error(conn, target)
    moved = _move_studies(conn, subtopic["id"], into["id"])
    conn.execute("UPDATE subtopics SET merged_into=?, status='confirmed' WHERE id=?",
                 (into["id"], subtopic["id"]))
    conn.commit()
    return {"mapped": slug, "into": target, "moved_studies": moved}


def reject_subtopic(conn, slug: str) -> dict:
    subtopic = _pending_or_error(conn, slug)
    moved = _move_studies(conn, subtopic["id"], None)
    conn.execute("UPDATE subtopics SET status='rejected' WHERE id=?", (subtopic["id"],))
    conn.commit()
    return {"rejected": slug, "unsorted_studies": moved}


def rename_subtopic(conn, slug: str, name: str) -> dict:
    """A new display name; the slug (the subtopic's id for links and filters) never changes."""
    subtopic = _subtopic_or_error(conn, slug)
    _refuse_split_name(conn, "subtopics", subtopic)
    if subtopic["merged_into"] is not None:
        raise ConfigError(f"subtopic '{slug}' is merged into another subtopic{_merged_note(conn, subtopic)}")
    clean = _clean_name(name)
    if not clean or len(clean) > SUBTOPIC_NAME_MAX:
        raise ConfigError(f"a subtopic name needs 1 to {SUBTOPIC_NAME_MAX} characters")
    for other in list_subtopics(conn):
        if other["id"] != subtopic["id"] and _clean_name(other["name"]).casefold() == clean.casefold():
            raise ConfigError(f"subtopic '{other['slug']}' already has the name '{other['name']}'")
    conn.execute("UPDATE subtopics SET name=? WHERE id=?", (clean, subtopic["id"]))
    conn.commit()
    return {"renamed": slug, "name": clean}


def merge_subtopics(conn, slug: str, into: str) -> dict:
    if slug == into:
        raise ConfigError("cannot merge a subtopic with itself")
    _refuse_split_name(conn, "subtopics", _subtopic_or_error(conn, slug))
    subtopic, target = _active_or_error(conn, slug), _active_or_error(conn, into)
    moved = _move_studies(conn, subtopic["id"], target["id"])
    conn.execute("UPDATE subtopics SET merged_into=? WHERE id=?", (target["id"], subtopic["id"]))
    conn.commit()
    return {"merged": slug, "into": into, "moved_studies": moved}


def subtopics_overview(conn) -> dict:
    """ev subtopics list: every subtopic (active first, merged last) with its study count, up to three
    study names as examples, and how many proposals wait for the owner."""
    subtopics = list_subtopics(conn)
    slugs = {t["id"]: t["slug"] for t in subtopics}
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT subtopic_id, COUNT(*) FROM studies WHERE merged_into IS NULL AND subtopic_id IS NOT NULL "
        "GROUP BY subtopic_id")}
    rows = []
    for t in sorted(subtopics, key=lambda t: (t["merged_into"] is not None,
                                          _SUBTOPIC_STATUS_ORDER.get(t["status"], 9), subtopic_order(t))):
        examples = [r[0] for r in conn.execute(
            "SELECT canonical_name FROM studies WHERE subtopic_id=? AND merged_into IS NULL "
            "ORDER BY id DESC LIMIT 3", (t["id"],))]
        rows.append({"slug": t["slug"], "name": t["name"], "status": t["status"],
                     "merged_into": slugs.get(t["merged_into"]),
                     "studies": counts.get(t["id"], 0), "examples": examples,
                     "split": _split_of(conn, "subtopics", t)})
    return {"subtopics": rows, "proposals": sum(1 for t in subtopics
                                                 if is_pending(t) and _split_of(conn, "subtopics", t) is None)}


# --- metrics: the per-topic list of outcomes a study counted -------------------------------------
OTHER_METRIC = "other"
OTHER_METRIC_NAME = "Other"
METRIC_ACTIVE = SUBTOPIC_ACTIVE


def list_metrics(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM metrics ORDER BY id")]


def get_metric(conn, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM metrics WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def _insert_metric(conn, name: str, status: str) -> int:
    return conn.execute("INSERT INTO metrics (slug, name, status, created_at) VALUES (?,?,?,?)",
                        (subtopic_slug(name), name, status, _now())).lastrowid


def _split_of(conn, table: str, row: dict) -> int | None:
    """The pending split proposal that created this subtopic or metric row (a 'split name'), else None."""
    target = "metric" if table == "metrics" else "subtopic"
    found = conn.execute("""SELECT p.id FROM split_groups g JOIN split_proposals p ON p.id = g.proposal_id
                            WHERE p.target=? AND g.mode='new' AND g.dest_id=? AND p.status='pending'
                            AND g.status='pending'""", (target, row["id"])).fetchone()
    return found[0] if found else None


def resolve_metric(conn, metric_id) -> dict | None:
    """The metric a (possibly merged) metric id ends at; None for no metric."""
    seen, row = set(), None
    while metric_id is not None and metric_id not in seen:
        seen.add(metric_id)
        row = conn.execute("SELECT * FROM metrics WHERE id=?", (metric_id,)).fetchone()
        if row is None:
            return None
        metric_id = row["merged_into"]
    return dict(row) if row else None


def _metric_order(metric: dict) -> tuple:
    return (metric["slug"] == OTHER_METRIC, *subtopic_order(metric))


def active_metrics(conn) -> list[dict]:
    """Metrics the judge and classify may pick: seeded or confirmed, not merged away; list order,
    Other last."""
    return sorted((m for m in list_metrics(conn)
                   if m["status"] in METRIC_ACTIVE and m["merged_into"] is None), key=_metric_order)


def metric_for_slug(conn, slug) -> dict | None:
    """The active metric a slug (or a merged-away slug) stands for; None when it is not one."""
    found = get_metric(conn, slug) if isinstance(slug, str) else None
    final = resolve_metric(conn, found["id"]) if found else None
    if final is None or final["status"] not in METRIC_ACTIVE or final["merged_into"] is not None:
        return None
    return final


def study_metric(conn, study: dict) -> dict | None:
    """The study's resolved active metric, None when it has none (unset, rejected, retired)."""
    if study.get("metric_id") is None:
        return None
    final = resolve_metric(conn, study["metric_id"])
    if final is None or final["status"] not in METRIC_ACTIVE or final["merged_into"] is not None:
        return None
    return final


def seed_metrics(conn, names, legacy: dict | None = None) -> None:
    """The topic's metric list as 'seeded' metrics, like seed_subtopics (matched by slug, then by
    name ignoring case and spacing; a pending non-split proposal that is listed becomes seeded;
    rejected, retired and merged rows are left alone; list position = index). Other is always
    present, seeded and last. Then the legacy fill (a study's old fixed code -> the metric the
    template's `legacy` map names, when that metric is active) and, when Other is the only active
    metric, the single-option fill (every study without a metric gets Other). Idempotent."""
    listed = [n for n in (names or []) if subtopic_slug(n) and subtopic_slug(n) != OTHER_METRIC]
    metrics = list_metrics(conn)
    for raw in listed:
        name = _clean_name(raw)
        metric = _listed_subtopic(metrics, name)
        if metric is None:
            metrics.append({"id": _insert_metric(conn, name, "seeded"), "slug": subtopic_slug(name),
                            "name": name, "status": "seeded", "merged_into": None})
        elif is_pending(metric) and _split_of(conn, "metrics", metric) is None:
            conn.execute("UPDATE metrics SET status='seeded', name=? WHERE id=?", (name, metric["id"]))
            metric.update(status="seeded", name=name)
    other = next((m for m in metrics if m["slug"] == OTHER_METRIC), None)
    if other is None:
        _insert_metric(conn, OTHER_METRIC_NAME, "seeded")
    elif other["status"] != "seeded" or other["merged_into"] is not None:
        conn.execute("UPDATE metrics SET status='seeded', merged_into=NULL WHERE id=?", (other["id"],))
    _set_positions(conn, [*listed, OTHER_METRIC_NAME], "metrics")
    _fill_study_metrics(conn, legacy or {})
    conn.commit()


def _fill_study_metrics(conn, legacy: dict) -> None:
    active = active_metrics(conn)
    by_slug = {m["slug"]: m for m in active}
    by_name = {_key(m["name"]): m for m in active}
    if len(active) == 1 and active[0]["slug"] == OTHER_METRIC:
        conn.execute("UPDATE studies SET metric_id=? WHERE merged_into IS NULL AND metric_id IS NULL",
                     (active[0]["id"],))
        return
    for study_id, code in conn.execute("SELECT id, metric_code FROM studies WHERE merged_into IS NULL "
                                       "AND metric_id IS NULL AND metric_code IS NOT NULL").fetchall():
        if code == OTHER_METRIC:
            continue       # an old 'other' is asked again against the new list
        target = by_slug.get(code)
        if target is None and code in legacy:
            target = by_name.get(_key(legacy[code])) or by_slug.get(subtopic_slug(legacy[code]))
        if target is not None:
            conn.execute("UPDATE studies SET metric_id=? WHERE id=?", (target["id"], study_id))


def _metric_or_error(conn, slug: str) -> dict:
    metric = get_metric(conn, slug)
    if metric is None:
        raise ConfigError(f"no metric '{slug}' (ev metrics list shows them)")
    return metric


def _not_other(metric: dict, action: str) -> None:
    if metric["slug"] == OTHER_METRIC:
        raise ConfigError(f'"{OTHER_METRIC_NAME}" cannot be {action}')


def _active_metric_or_error(conn, slug: str) -> dict:
    metric = _metric_or_error(conn, slug)
    if metric["status"] not in METRIC_ACTIVE or metric["merged_into"] is not None:
        raise ConfigError(f"metric '{slug}' is not an active metric (status {metric['status']})")
    return metric


def _pending_metric_or_error(conn, slug: str) -> dict:
    metric = _metric_or_error(conn, slug)
    _refuse_split_name(conn, "metrics", metric)
    if not is_pending(metric):
        raise ConfigError(f"metric '{slug}' is not a proposal waiting for review (status {metric['status']})")
    return metric


def accept_metric(conn, slug: str) -> dict:
    _not_other(_metric_or_error(conn, slug), "accepted")
    metric = _pending_metric_or_error(conn, slug)
    conn.execute("UPDATE metrics SET status='confirmed' WHERE id=?", (metric["id"],))
    conn.commit()
    return {"accepted": slug}


def reject_metric(conn, slug: str) -> dict:
    _not_other(_metric_or_error(conn, slug), "rejected")
    metric = _pending_metric_or_error(conn, slug)
    moved = conn.execute("UPDATE studies SET metric_id=NULL WHERE metric_id=?", (metric["id"],)).rowcount
    conn.execute("UPDATE metrics SET status='rejected' WHERE id=?", (metric["id"],))
    conn.commit()
    return {"rejected": slug, "studies_without_metric": moved}


def rename_metric(conn, slug: str, name: str) -> dict:
    """A new display name; the slug never changes."""
    metric = _metric_or_error(conn, slug)
    _refuse_split_name(conn, "metrics", metric)
    if metric["merged_into"] is not None:
        raise ConfigError(f"metric '{slug}' is merged into another metric")
    clean = _clean_name(name)
    if not clean or len(clean) > SUBTOPIC_NAME_MAX:
        raise ConfigError(f"a metric name needs 1 to {SUBTOPIC_NAME_MAX} characters")
    for other in list_metrics(conn):
        if other["id"] != metric["id"] and _clean_name(other["name"]).casefold() == clean.casefold():
            raise ConfigError(f"metric '{other['slug']}' already has the name '{other['name']}'")
    conn.execute("UPDATE metrics SET name=? WHERE id=?", (clean, metric["id"]))
    conn.commit()
    return {"renamed": slug, "name": clean}


def merge_metrics(conn, slug: str, into: str) -> dict:
    if slug == into:
        raise ConfigError("cannot merge a metric with itself")
    _not_other(_metric_or_error(conn, slug), "merged")
    _refuse_split_name(conn, "metrics", _metric_or_error(conn, slug))
    metric, target = _active_metric_or_error(conn, slug), _active_metric_or_error(conn, into)
    moved = conn.execute("UPDATE studies SET metric_id=? WHERE metric_id=?",
                         (target["id"], metric["id"])).rowcount
    conn.execute("UPDATE metrics SET merged_into=? WHERE id=?", (target["id"], metric["id"]))
    conn.commit()
    return {"merged": slug, "into": into, "moved_studies": moved}


def set_study_metric(conn, study_id: int, slug: str) -> dict:
    """Owner override: the study's metric becomes an active metric of the topic."""
    study = get_study(conn, study_id)
    if study is None or study["merged_into"] is not None:
        raise ConfigError(f"no active study s{study_id}")
    metric = metric_for_slug(conn, slug)
    if metric is None:
        raise ConfigError(f"metric '{slug}' is not an active metric (ev metrics list shows them)")
    conn.execute("UPDATE studies SET metric_id=? WHERE id=?", (metric["id"], study_id))
    conn.commit()
    return {"study": study_id, "metric": metric["slug"]}


def metrics_overview(conn) -> dict:
    """ev metrics list: every metric (active first, merged last) with its study count and up to three
    study names as examples. `split` is the pending split proposal that created the row, else None."""
    metrics = list_metrics(conn)
    slugs = {m["id"]: m["slug"] for m in metrics}
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT metric_id, COUNT(*) FROM studies WHERE merged_into IS NULL AND metric_id IS NOT NULL "
        "GROUP BY metric_id")}
    rows = []
    for m in sorted(metrics, key=lambda m: (m["merged_into"] is not None,
                                            _SUBTOPIC_STATUS_ORDER.get(m["status"], 9), _metric_order(m))):
        examples = [r[0] for r in conn.execute(
            "SELECT canonical_name FROM studies WHERE metric_id=? AND merged_into IS NULL "
            "ORDER BY id DESC LIMIT 3", (m["id"],))]
        rows.append({"slug": m["slug"], "name": m["name"], "status": m["status"],
                     "merged_into": slugs.get(m["merged_into"]), "studies": counts.get(m["id"], 0),
                     "examples": examples, "split": _split_of(conn, "metrics", m)})
    return {"metrics": rows, "proposals": 0}


# --- entities: the per-topic list of named things a study is about -------------------------------
ENTITY_NAME_MAX = 60
ENTITY_MAX_PER_STUDY = 5
ENTITY_PROMPT_MAX = 150
ENTITY_STATUSES = ("active", "rejected")


def entity_slug(name) -> str:
    """subtopic_slug with '+' spelled 'plus' and '#' spelled 'sharp' (C, C++ and C# stay apart)."""
    return subtopic_slug(str(name or "").replace("+", " plus ").replace("#", " sharp "))


def _clean_entity_names(raw, cap) -> list[str]:
    out: list[str] = []
    slugs: set = set()
    keys: set = set()
    for value in raw if isinstance(raw, list) else []:
        if not isinstance(value, str):
            continue
        name = _clean_name(value)
        slug = entity_slug(name)
        if not name or len(name) > ENTITY_NAME_MAX or not slug or slug in slugs or _key(name) in keys:
            continue
        slugs.add(slug)
        keys.add(_key(name))
        out.append(name)
        if cap is not None and len(out) >= cap:
            break
    return out


def clean_entity_names(raw) -> list[str]:
    """The judge's or classify's entity names: strings only, spaces collapsed, no blank, over-long or
    slugless name, no repeat (same slug or same name ignoring case), the first five. Never an error."""
    return _clean_entity_names(raw, ENTITY_MAX_PER_STUDY)


def get_entity(conn, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM entities WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def resolve_entity(conn, entity_id) -> dict | None:
    """The entity row an id (possibly merged or an alias) ends at; None when there is none."""
    seen, row = set(), None
    while entity_id is not None and entity_id not in seen:
        seen.add(entity_id)
        row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            return None
        entity_id = row["merged_into"]
    return dict(row) if row else None


def _entity_row_for_name(conn, name) -> dict | None:
    """The row (any status, merged or alias included) a name stands for: by slug, else by name."""
    clean = _clean_name(name)
    slug = entity_slug(clean)
    if not clean or len(clean) > ENTITY_NAME_MAX or not slug:
        return None
    found = get_entity(conn, slug)
    if found:
        return found
    key = _key(clean)
    for row in conn.execute("SELECT * FROM entities ORDER BY id"):
        if _key(row["name"]) == key:
            return dict(row)
    return None


def entity_for_name(conn, name, create: bool = False) -> dict | None:
    """The final active entity a name means (following merges and aliases). A rejected entity gives
    None (the name is dropped); an unknown name gives None, or a new active row with create=True."""
    found = _entity_row_for_name(conn, name)
    if found is not None:
        final = resolve_entity(conn, found["id"])
        return final if final is not None and final["status"] == "active" else None
    if not create:
        return None
    clean = _clean_name(name)
    if not clean or len(clean) > ENTITY_NAME_MAX or not entity_slug(clean):
        return None
    return _insert_entity(conn, clean)


def _insert_entity(conn, name: str, merged_into: int | None = None) -> dict:
    cur = conn.execute("INSERT INTO entities (slug, name, status, merged_into, created_at) VALUES (?,?,?,?,?)",
                       (entity_slug(name), name, "active", merged_into, _now()))
    return dict(conn.execute("SELECT * FROM entities WHERE id=?", (cur.lastrowid,)).fetchone())


def _append_link(conn, study_id: int, entity_id: int) -> None:
    position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM study_entities WHERE study_id=?",
                            (study_id,)).fetchone()[0]
    conn.execute("INSERT INTO study_entities (study_id, entity_id, position) VALUES (?,?,?)",
                 (study_id, entity_id, position))


def link_study_entities(conn, study_id: int, names: list[str],
                        cap: int | None = ENTITY_MAX_PER_STUDY) -> list[str]:
    """Link the named entities to the study (new names join the topic's list), at most `cap` links in
    all; names of rejected entities are dropped. The study counts as asked even when nothing linked.
    Returns the slugs newly linked."""
    linked: list[str] = []
    have = {r[0] for r in conn.execute("SELECT entity_id FROM study_entities WHERE study_id=?", (study_id,))}
    for name in _clean_entity_names(names, cap):
        if cap is not None and len(have) >= cap:
            break
        row = entity_for_name(conn, name, create=True)
        if row is None or row["id"] in have:
            continue
        _append_link(conn, study_id, row["id"])
        have.add(row["id"])
        linked.append(row["slug"])
    conn.execute("UPDATE studies SET entities_checked=1 WHERE id=?", (study_id,))
    conn.commit()
    return linked


def study_entities(conn, study_id: int) -> list[dict]:
    """The study's entities (final active rows), in the order they were named."""
    return [dict(r) for r in conn.execute(
        """SELECT e.* FROM study_entities se JOIN entities e ON e.id = se.entity_id
           WHERE se.study_id=? AND e.status='active' AND e.merged_into IS NULL
           ORDER BY se.position, e.id""", (study_id,))]


def _entity_study_counts(conn) -> dict[int, int]:
    return {r[0]: r[1] for r in conn.execute(
        """SELECT se.entity_id, COUNT(*) FROM study_entities se JOIN studies s ON s.id = se.study_id
           WHERE s.merged_into IS NULL GROUP BY se.entity_id""")}


def topic_entities(conn, limit: int | None = None) -> list[dict]:
    """Active unmerged entities with `studies` (active studies linked), most studies first, then name."""
    counts = _entity_study_counts(conn)
    rows = [dict(r, studies=counts.get(r["id"], 0)) for r in conn.execute(
        "SELECT * FROM entities WHERE status='active' AND merged_into IS NULL")]
    rows.sort(key=lambda e: (-e["studies"], _key(e["name"])))
    return rows if limit is None else rows[:limit]


def _entity_or_error(conn, slug: str) -> dict:
    entity = get_entity(conn, slug) if isinstance(slug, str) else None
    if entity is None:
        raise ConfigError(f"no entity '{slug}' (ev entities list shows them)")
    return entity


def _active_entity_or_error(conn, slug: str) -> dict:
    entity = _entity_or_error(conn, slug)
    if entity["status"] != "active" or entity["merged_into"] is not None:
        raise ConfigError(f"entity '{slug}' is not an active entity (ev entities list shows them)")
    return entity


def _entity_name_or_error(conn, name, own_id: int | None) -> tuple[str, str]:
    """(clean name, slug) of a name no other row uses, else ConfigError."""
    clean = _clean_name(name)
    if not clean or len(clean) > ENTITY_NAME_MAX:
        raise ConfigError(f"an entity name needs 1 to {ENTITY_NAME_MAX} characters")
    slug = entity_slug(clean)
    if not slug:
        raise ConfigError("an entity name needs a letter or digit")
    for row in conn.execute("SELECT id, slug, name FROM entities"):
        if row["id"] != own_id and (row["slug"] == slug or _key(row["name"]) == _key(clean)):
            raise ConfigError(f"entity '{row['slug']}' already has the name '{row['name']}' (use merge)")
    return clean, slug


def rename_entity(conn, slug: str, name: str) -> dict:
    """A new display name; the slug never changes."""
    entity = _entity_or_error(conn, slug)
    if entity["merged_into"] is not None:
        raise ConfigError(f"entity '{slug}' is merged into another entity")
    clean, _ = _entity_name_or_error(conn, name, entity["id"])
    conn.execute("UPDATE entities SET name=? WHERE id=?", (clean, entity["id"]))
    conn.commit()
    return {"renamed": slug, "name": clean}


def merge_entities(conn, slug: str, into: str) -> dict:
    """Move the links of `slug` to `into` and let `slug` stand for `into` from now on."""
    if slug == into:
        raise ConfigError("cannot merge an entity with itself")
    entity, target = _active_entity_or_error(conn, slug), _active_entity_or_error(conn, into)
    moved = 0
    for sid, merged_away in conn.execute(
            "SELECT l.study_id, s.merged_into IS NOT NULL FROM study_entities l JOIN studies s ON s.id = l.study_id "
            "WHERE l.entity_id=? ORDER BY l.study_id", (entity["id"],)).fetchall():
        conn.execute("DELETE FROM study_entities WHERE study_id=? AND entity_id=?", (sid, entity["id"]))
        if conn.execute("SELECT 1 FROM study_entities WHERE study_id=? AND entity_id=?",
                        (sid, target["id"])).fetchone():
            continue
        _append_link(conn, sid, target["id"])
        moved += 0 if merged_away else 1         # counted like `entities list`: active studies only
    conn.execute("UPDATE entities SET merged_into=? WHERE id=?", (target["id"], entity["id"]))
    conn.commit()
    return {"merged": slug, "into": into, "moved_studies": moved}


def alias_entity(conn, slug: str, name: str) -> dict:
    """Future mentions of `name` mean the entity `slug`."""
    entity = _active_entity_or_error(conn, slug)
    clean, alias_slug = _entity_name_or_error(conn, name, None)
    _insert_entity(conn, clean, merged_into=entity["id"])
    conn.commit()
    return {"alias": alias_slug, "name": clean, "of": slug}


def reject_entity(conn, slug: str) -> dict:
    entity = _active_entity_or_error(conn, slug)
    removed = conn.execute("SELECT COUNT(*) FROM study_entities l JOIN studies s ON s.id = l.study_id "
                           "WHERE l.entity_id=? AND s.merged_into IS NULL", (entity["id"],)).fetchone()[0]
    conn.execute("DELETE FROM study_entities WHERE entity_id=?", (entity["id"],))
    conn.execute("UPDATE entities SET status='rejected' WHERE id=?", (entity["id"],))
    conn.commit()
    return {"rejected": slug, "removed_links": removed}


def restore_entity(conn, slug: str) -> dict:
    entity = _entity_or_error(conn, slug)
    if entity["status"] != "rejected" or entity["merged_into"] is not None:
        raise ConfigError(f"entity '{slug}' is not a rejected entity")
    conn.execute("UPDATE entities SET status='active' WHERE id=?", (entity["id"],))
    conn.commit()
    return {"restored": slug}


def _active_study_or_error(conn, study_id: int) -> dict:
    study = get_study(conn, study_id)
    if study is None or study["merged_into"] is not None:
        raise ConfigError(f"no active study s{study_id}")
    return study


def add_study_entity(conn, study_id: int, name: str) -> dict:
    """Owner: the study is also about `name` (a new name joins the list; a rejected one is refused)."""
    _active_study_or_error(conn, study_id)
    clean = _clean_name(name)
    if not clean or len(clean) > ENTITY_NAME_MAX or not entity_slug(clean):
        raise ConfigError(f"an entity name needs 1 to {ENTITY_NAME_MAX} characters with a letter or digit")
    found = _entity_row_for_name(conn, clean)
    final = resolve_entity(conn, found["id"]) if found else None
    if final is not None and final["status"] != "active":
        raise ConfigError(f"entity '{final['slug']}' is rejected; restore it first (ev entities restore)")
    row = final or entity_for_name(conn, clean, create=True)
    if not conn.execute("SELECT 1 FROM study_entities WHERE study_id=? AND entity_id=?",
                        (study_id, row["id"])).fetchone():
        _append_link(conn, study_id, row["id"])
    conn.execute("UPDATE studies SET entities_checked=1 WHERE id=?", (study_id,))
    conn.commit()
    return {"study": study_id, "entity": row["slug"], "name": row["name"]}


def remove_study_entity(conn, study_id: int, slug: str) -> dict:
    """Owner: the study is not about this entity (it stays checked, so nothing re-adds it)."""
    _active_study_or_error(conn, study_id)
    final = resolve_entity(conn, _entity_or_error(conn, slug)["id"])
    removed = conn.execute("DELETE FROM study_entities WHERE study_id=? AND entity_id=?",
                           (study_id, final["id"] if final else -1)).rowcount
    if not removed:
        raise ConfigError(f"study s{study_id} is not linked to entity '{slug}'")
    conn.execute("UPDATE studies SET entities_checked=1 WHERE id=?", (study_id,))
    conn.commit()
    return {"study": study_id, "removed": final["slug"]}


def entities_overview(conn) -> dict:
    """ev entities list: active entities (most studies first, then name), rejected ones, then merged
    and alias rows; each with its active study count and up to three study names."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM entities")]
    slugs = {e["id"]: e["slug"] for e in rows}
    counts = _entity_study_counts(conn)

    def order(e):
        group = 2 if e["merged_into"] is not None else (1 if e["status"] == "rejected" else 0)
        return (group, -counts.get(e["id"], 0), _key(e["name"]))

    out = []
    for e in sorted(rows, key=order):
        examples = [r[0] for r in conn.execute(
            """SELECT s.canonical_name FROM study_entities se JOIN studies s ON s.id = se.study_id
               WHERE se.entity_id=? AND s.merged_into IS NULL ORDER BY s.id DESC LIMIT 3""", (e["id"],))]
        out.append({"slug": e["slug"], "name": e["name"], "status": e["status"],
                    "merged_into": slugs.get(e["merged_into"]), "studies": counts.get(e["id"], 0),
                    "examples": examples})
    return {"entities": out}


# --- splits: proposals to split an overloaded subtopic or a bloated "other" metric ---------------
def split_state(conn) -> dict:
    """(target, source id) -> {"pending": a proposal waits for the owner, "studies_at": the source's
    count when its latest decided proposal was made (None: never decided)}."""
    out: dict = {}
    for row in conn.execute("SELECT target, source_id, status, studies_at FROM split_proposals ORDER BY id"):
        entry = out.setdefault((row["target"], row["source_id"]), {"pending": False, "studies_at": None})
        if row["status"] == "pending":
            entry["pending"] = True
        else:
            entry["studies_at"] = row["studies_at"]
    return out


def used_names(conn) -> list[str]:
    """Every name and slug of a subtopic or metric, whatever its status (a split may not reuse them)."""
    out: list[str] = []
    for table in ("subtopics", "metrics"):
        for row in conn.execute(f"SELECT slug, name FROM {table} ORDER BY id"):
            out += [v for v in (row["name"], row["slug"]) if v not in out]
    return out


def split_name_error(conn, kind: str, name, own_id: int | None = None) -> str | None:
    """Why `name` cannot be a new subtopic (kind 'subtopic') or metric (kind 'metric') made by a split,
    else None: 1 to 80 characters with a letter or digit, not "Other", and neither its slug nor its
    name (ignoring case and spacing) matches a row of any status. `own_id`: the row being renamed."""
    clean = _clean_name(name)
    if not clean or len(clean) > SUBTOPIC_NAME_MAX:
        return f"a name needs 1 to {SUBTOPIC_NAME_MAX} characters"
    slug = subtopic_slug(clean)
    if not slug:
        return "a name needs a letter or digit"
    if slug == OTHER_METRIC:
        return f'the name "{OTHER_METRIC_NAME}" is reserved'
    table = "subtopics" if kind == "subtopic" else "metrics"
    for row in conn.execute(f"SELECT id, slug, name FROM {table}"):
        if row["id"] != own_id and (row["slug"] == slug or _clean_name(row["name"]).casefold() == clean.casefold()):
            return f"the name {clean!r} is already used by {kind} '{row['slug']}'"
    return None


def create_split_proposal(conn, target: str, source_id: int, studies_at: int, groups: list[dict]) -> int:
    """One pending proposal in one transaction: its groups, a 'proposed' subtopic or metric per new group
    (a split name) and the moves. A group is {"mode": "new", "name"} or {"mode": "into", "dest_id"}, plus
    "questions" (question ids) and "studies" (study ids). Nothing else changes: nothing moves."""
    if target not in ("subtopic", "metric"):
        raise ValueError(f"unknown split target {target!r}")
    try:
        pid = conn.execute("""INSERT INTO split_proposals (target, source_id, studies_at, status, created_at)
                              VALUES (?,?,?,'pending',?)""", (target, source_id, studies_at, _now())).lastrowid
        for group in groups:
            if group["mode"] == "new":
                dest = (_insert_subtopic if target == "subtopic" else _insert_metric)(conn, group["name"], "proposed")
            else:
                dest = group["dest_id"]
            gid = conn.execute("INSERT INTO split_groups (proposal_id, mode, dest_id, status) VALUES (?,?,?,'pending')",
                               (pid, group["mode"], dest)).lastrowid
            for qid in group.get("questions") or []:
                conn.execute("INSERT INTO split_moves (group_id, question_id) VALUES (?,?)", (gid, qid))
            for sid in group.get("studies") or []:
                conn.execute("INSERT INTO split_moves (group_id, study_id) VALUES (?,?)", (gid, sid))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return pid


def clear_pending_splits(conn) -> dict:
    """ev reset --stage split: pending proposals, their groups and moves, and the split names that are
    still 'proposed'. Decided proposals stay; moves already accepted are not undone. A pending proposal
    with an accepted group is closed instead ('decided', no source answer; only its undecided groups
    go), so its accepted parts and its count for the re-arm rule stay."""
    open_ids = [r[0] for r in conn.execute("SELECT id FROM split_proposals WHERE status='pending'")]
    closed = [pid for pid in open_ids if conn.execute(
        "SELECT 1 FROM split_groups WHERE proposal_id=? AND status='accepted'", (pid,)).fetchone()]
    pending = [pid for pid in open_ids if pid not in closed]
    marks = ",".join("?" * len(pending))
    names = groups = 0
    if open_ids:
        omarks = ",".join("?" * len(open_ids))
        group_ids = [r[0] for r in conn.execute(
            f"SELECT id FROM split_groups WHERE proposal_id IN ({omarks}) AND (status='pending' "
            f"OR proposal_id IN ({marks}))", (*open_ids, *pending))]
        gmarks = ",".join("?" * len(group_ids))
        for target, table in (("subtopic", "subtopics"), ("metric", "metrics")):
            names += conn.execute(
                f"""DELETE FROM {table} WHERE status='proposed' AND id IN (
                      SELECT g.dest_id FROM split_groups g JOIN split_proposals p ON p.id = g.proposal_id
                      WHERE g.mode='new' AND p.target=? AND g.id IN ({gmarks}))""",
                (target, *group_ids)).rowcount if group_ids else 0
        if group_ids:
            conn.execute(f"DELETE FROM split_moves WHERE group_id IN ({gmarks})", group_ids)
            groups = conn.execute(f"DELETE FROM split_groups WHERE id IN ({gmarks})", group_ids).rowcount
        conn.execute(f"DELETE FROM split_proposals WHERE id IN ({marks})", pending)
        for pid in closed:
            conn.execute("UPDATE split_proposals SET status='decided', decided_at=? WHERE id=?", (_now(), pid))
    conn.commit()
    return {"proposals": len(pending), "closed": len(closed), "groups": groups, "names": names}


# --- split decisions: the owner accepts, renames or rejects each proposed part ------------------
def _refuse_split_name(conn, table: str, row: dict) -> None:
    if _split_of(conn, table, row) is not None:
        raise ConfigError(f'"{row["slug"]}" is a split proposal: use ev split')


def _split_group_or_error(conn, group_id: int) -> tuple[dict, dict]:
    row = conn.execute("SELECT * FROM split_groups WHERE id=?", (group_id,)).fetchone()
    if row is None:
        raise ConfigError(f"no split group {group_id} (ev split list shows them)")
    group = dict(row)
    if group["status"] != "pending":
        raise ConfigError(f"split group {group_id} is already decided ({group['status']})")
    proposal = dict(conn.execute("SELECT * FROM split_proposals WHERE id=?", (group["proposal_id"],)).fetchone())
    return group, proposal


def _dest_row(conn, proposal: dict, group: dict) -> dict:
    table = "subtopics" if proposal["target"] == "subtopic" else "metrics"
    return dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (group["dest_id"],)).fetchone())


def _split_source_row(conn, proposal: dict) -> dict | None:
    return (resolve_subtopic if proposal["target"] == "subtopic" else resolve_metric)(conn, proposal["source_id"])


def _group_plan(conn, proposal: dict, group: dict) -> dict:
    """What accepting the group would still move: {"questions": [(question, [study ids])], "studies":
    [ids], "count": studies moved}. A move holds only while the world is as it was proposed (spec 6):
    subtopic targets - the question (after merges) and the study still resolve to the source subtopic;
    metric targets - the study's metric still resolves to the source (Other)."""
    source = _split_source_row(conn, proposal)
    plan = {"questions": [], "studies": [], "count": 0}
    if source is None:
        return plan
    moves = conn.execute("SELECT question_id, study_id FROM split_moves WHERE group_id=? ORDER BY rowid",
                         (group["id"],)).fetchall()
    studies = [dict(r) for r in conn.execute(
        "SELECT id, subtopic_id, question_id, metric_id FROM studies WHERE merged_into IS NULL ORDER BY id")]
    by_id = {s["id"]: s for s in studies}
    subtopics: dict = {}

    def subtopic_of(subtopic_id):
        if subtopic_id not in subtopics:
            subtopics[subtopic_id] = resolve_subtopic(conn, subtopic_id)
        return subtopics[subtopic_id]

    def in_source(subtopic_id) -> bool:
        found = subtopic_of(subtopic_id)
        return found is not None and found["id"] == source["id"]

    if proposal["target"] == "metric":
        for move in moves:
            study = by_id.get(move["study_id"])
            found = study_metric(conn, study) if study else None
            if found is not None and found["id"] == source["id"]:
                plan["studies"].append(study["id"])
        plan["count"] = len(plan["studies"])
        return plan
    resolved_q: dict = {}
    taken: set = set()
    for move in moves:
        if move["question_id"] is not None:
            question = resolve_question(conn, move["question_id"])
            if question is None or question["id"] in taken or not in_source(question["subtopic_id"]):
                continue
            taken.add(question["id"])
            members = []
            for study in studies:
                if study["question_id"] is None or not in_source(study["subtopic_id"]):
                    continue
                if study["question_id"] not in resolved_q:
                    found = resolve_question(conn, study["question_id"])
                    resolved_q[study["question_id"]] = found["id"] if found else None
                if resolved_q[study["question_id"]] == question["id"]:
                    members.append(study["id"])
            plan["questions"].append((question, members))
        else:
            study = by_id.get(move["study_id"])
            if study is None or not in_source(study["subtopic_id"]):
                continue
            question = resolve_question(conn, study["question_id"]) if study["question_id"] is not None else None
            if question is not None and in_source(question["subtopic_id"]):
                continue                    # grouped under a question of the source since: not loose any more
            plan["studies"].append(study["id"])
    plan["count"] = sum(len(m) for _, m in plan["questions"]) + len(plan["studies"])
    return plan


def _settle(conn, proposal_id: int) -> bool:
    """After a group decision: True when the owner must now be asked about the source (subtopic targets
    with every group decided and one accepted); marks the proposal decided when nothing is left to ask."""
    proposal = dict(conn.execute("SELECT * FROM split_proposals WHERE id=?", (proposal_id,)).fetchone())
    states = [r[0] for r in conn.execute("SELECT status FROM split_groups WHERE proposal_id=?", (proposal_id,))]
    if "pending" in states:
        return False
    if proposal["target"] == "subtopic" and "accepted" in states and proposal["source_choice"] is None:
        return True
    conn.execute("UPDATE split_proposals SET status='decided', decided_at=? WHERE id=?", (_now(), proposal_id))
    return False


def accept_split_group(conn, group_id: int) -> dict:
    """Accept one proposed part: its split name becomes confirmed and the moves that still hold are
    applied (an owner pin does not block them: the owner accepted the move)."""
    group, proposal = _split_group_or_error(conn, group_id)
    kind, dest = proposal["target"], _dest_row(conn, proposal, group)
    if group["mode"] == "into":
        final = (resolve_subtopic if kind == "subtopic" else resolve_metric)(conn, dest["id"])
        if final is None or final["status"] not in SUBTOPIC_ACTIVE or final["merged_into"] is not None:
            raise ConfigError(f"'{dest['slug']}' is no longer an active {kind}; leave this group (ev split reject)")
        dest = final
    source = _split_source_row(conn, proposal)
    plan = _group_plan(conn, proposal, group)
    try:
        if kind == "subtopic":
            for question, members in plan["questions"]:
                conn.execute("UPDATE questions SET subtopic_id=? WHERE id=?", (dest["id"], question["id"]))
                for sid in members:
                    conn.execute("UPDATE studies SET subtopic_id=? WHERE id=?", (dest["id"], sid))
            for sid in plan["studies"]:
                conn.execute("UPDATE studies SET subtopic_id=? WHERE id=?", (dest["id"], sid))
        else:
            for sid in plan["studies"]:
                conn.execute("UPDATE studies SET metric_id=? WHERE id=?", (dest["id"], sid))
        if group["mode"] == "new":
            conn.execute(f"UPDATE {'subtopics' if kind == 'subtopic' else 'metrics'} SET status='confirmed' "
                         "WHERE id=?", (dest["id"],))
        conn.execute("UPDATE split_groups SET status='accepted' WHERE id=?", (group_id,))
        ask = _settle(conn, proposal["id"])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"accepted": group_id, "kind": kind, "name": dest["name"], "mode": group["mode"],
            "source_name": source["name"] if source else None, "source_slug": source["slug"] if source else None,
            "moved_questions": len(plan["questions"]),
            "moved_studies": plan["count"], "ask_source": ask}


def reject_split_group(conn, group_id: int) -> dict:
    """Reject one proposed part: its split name becomes rejected and its moves are dropped."""
    group, proposal = _split_group_or_error(conn, group_id)
    dest = _dest_row(conn, proposal, group)
    try:
        if group["mode"] == "new":
            conn.execute(f"UPDATE {'subtopics' if proposal['target'] == 'subtopic' else 'metrics'} "
                         "SET status='rejected' WHERE id=?", (dest["id"],))
        conn.execute("DELETE FROM split_moves WHERE group_id=?", (group_id,))
        conn.execute("UPDATE split_groups SET status='rejected' WHERE id=?", (group_id,))
        ask = _settle(conn, proposal["id"])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"rejected": group_id, "kind": proposal["target"], "name": dest["name"], "mode": group["mode"],
            "ask_source": ask}


def rename_split_group(conn, group_id: int, name: str) -> dict:
    """A new name for a proposed part that is still pending (the name was never in use, so its slug
    follows the new name); the same rules as the proposal's names."""
    group, proposal = _split_group_or_error(conn, group_id)
    if group["mode"] != "new":
        raise ConfigError(f"split group {group_id} moves into an existing {proposal['target']}; "
                          "only a new name can be renamed")
    clean = _clean_name(name)
    problem = split_name_error(conn, proposal["target"], clean, own_id=group["dest_id"])
    if problem:
        raise ConfigError(problem)
    table = "subtopics" if proposal["target"] == "subtopic" else "metrics"
    conn.execute(f"UPDATE {table} SET name=?, slug=? WHERE id=?", (clean, subtopic_slug(clean), group["dest_id"]))
    conn.commit()
    return {"renamed": group_id, "name": clean}


def _asks_source(conn, proposal: dict) -> bool:
    if proposal["status"] != "pending" or proposal["target"] != "subtopic" or proposal["source_choice"] is not None:
        return False
    states = [r[0] for r in conn.execute("SELECT status FROM split_groups WHERE proposal_id=?", (proposal["id"],))]
    return "pending" not in states and "accepted" in states


def set_split_source(conn, proposal_id: int, choice: str) -> dict:
    """The owner's answer about the source subtopic after an accepted split: keep it for the studies
    that fit none of the parts, or retire it (its studies go back to classify)."""
    if choice not in ("keep", "retire"):
        raise ConfigError("the source answer is keep or retire")
    row = conn.execute("SELECT * FROM split_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise ConfigError(f"no split proposal {proposal_id} (ev split list shows them)")
    proposal = dict(row)
    if not _asks_source(conn, proposal):
        raise ConfigError(f"split proposal {proposal_id} is not asked about its source now "
                          "(every group must be decided and one accepted)")
    source = _split_source_row(conn, proposal)
    if source is None:
        raise ConfigError(f"the source of split proposal {proposal_id} no longer exists")
    resort = 0
    try:
        if choice == "retire":
            if source["slug"] == "other":
                raise ConfigError(f'"{OTHER_METRIC_NAME}" cannot be retired')
            conn.execute("UPDATE subtopics SET status=? WHERE id=?", (SUBTOPIC_RETIRED, source["id"]))
            for sid, subtopic_id in conn.execute("SELECT id, subtopic_id FROM studies WHERE merged_into IS NULL "
                                                 "AND subtopic_id IS NOT NULL").fetchall():
                final = resolve_subtopic(conn, subtopic_id)
                if final is not None and final["id"] == source["id"]:
                    conn.execute("UPDATE studies SET subtopic_id=NULL, classified_version=NULL WHERE id=?", (sid,))
                    resort += 1
        conn.execute("UPDATE split_proposals SET source_choice=?, status='decided', decided_at=? WHERE id=?",
                     (choice, _now(), proposal_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"proposal": proposal_id, "source": source["slug"], "source_name": source["name"],
            "choice": choice, "studies_to_resort": resort}


def split_overview(conn) -> dict:
    """ev split list: the pending proposals with their groups (the moves that still hold are counted),
    up to three example studies per group, and whether the source question is due."""
    placed, _ = knowledge.placed_studies(conn)
    out = []
    for row in conn.execute("SELECT * FROM split_proposals WHERE status='pending' ORDER BY id").fetchall():
        proposal = dict(row)
        table = "subtopics" if proposal["target"] == "subtopic" else "metrics"
        source = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (proposal["source_id"],)).fetchone())
        groups = []
        for g in conn.execute("SELECT * FROM split_groups WHERE proposal_id=? ORDER BY id", (proposal["id"],)):
            group = dict(g)
            dest = _dest_row(conn, proposal, group)
            studies, examples = None, []
            if group["status"] == "pending":
                plan = _group_plan(conn, proposal, group)
                ids = [i for _, members in plan["questions"] for i in members] + plan["studies"]
                studies = plan["count"]
                examples = [r[0] for r in conn.execute(
                    f"SELECT canonical_name FROM studies WHERE id IN ({','.join('?' * len(ids))}) "
                    "ORDER BY id DESC LIMIT 3", ids)] if ids else []
            groups.append({"id": group["id"], "mode": group["mode"], "slug": dest["slug"], "name": dest["name"],
                           "status": group["status"], "studies": studies, "examples": examples})
        final = _split_source_row(conn, proposal)
        remaining = sum(1 for _, sub, _ in placed if final and sub is not None and sub["id"] == final["id"]) \
            if proposal["target"] == "subtopic" else None
        out.append({"id": proposal["id"], "target": proposal["target"],
                    "source": {"slug": source["slug"], "name": source["name"]},
                    "studies_at": proposal["studies_at"], "source_choice": proposal["source_choice"],
                    "ask_source": _asks_source(conn, proposal), "source_studies": remaining, "groups": groups})
    return {"proposals": out}


# --- wave 4a: template subtopic switch and classify ----------------------------------------------
def _key(name) -> str:
    return _clean_name(name).casefold()


def _match_template(conn, names, renames) -> tuple[list, list]:
    """(matches, rest): matches = [(name to keep, subtopic dict or None)] in template order, each
    subtopic matched at most once (same name or slug, then renames, then a merged-away subtopic with
    that slug); rest = the subtopics no template name matched. The name to keep is the template's,
    except for a slug-only match: there the subtopic keeps its current (maybe owner-given) name."""
    subtopics = list_subtopics(conn)
    used, matches = set(), []
    for raw in names or []:
        name = _clean_name(raw)
        if not subtopic_slug(name):
            continue
        olds = {_key(old) for old, new in (renames or {}).items() if _key(new) == _key(name)}
        free = [t for t in subtopics if t["id"] not in used]
        found = (next((t for t in free if t["merged_into"] is None
                       and (_key(t["name"]) == _key(name) or t["slug"] == subtopic_slug(name))), None)
                 or next((t for t in free if t["merged_into"] is None and _key(t["name"]) in olds), None)
                 or next((t for t in free if t["merged_into"] is not None
                          and t["slug"] == subtopic_slug(name)), None))
        if found is not None:
            used.add(found["id"])
            if (found["merged_into"] is None and _key(found["name"]) != _key(name)
                    and _key(found["name"]) not in olds):
                name = found["name"]     # matched by slug only: the owner's own name stays
        matches.append((name, found))
    return matches, [t for t in subtopics if t["id"] not in used]


def _template_changes(matches, rest) -> tuple[dict, list]:
    add, carry = [], {}
    for name, subtopic in matches:
        if subtopic is None or subtopic["status"] not in SUBTOPIC_ACTIVE or subtopic["merged_into"] is not None:
            add.append(name)
        if subtopic is not None and subtopic["name"] != name:
            carry[subtopic["name"]] = name
    retire = [t for t in rest if t["status"] == "seeded" and t["merged_into"] is None]
    return {"add": add, "retire": [t["name"] for t in retire], "carry": carry}, retire


def template_plan(conn, names, renames) -> dict:
    """What adopting the template's subtopic list would do. Writes nothing."""
    return _template_changes(*_match_template(conn, names, renames))[0]


def adopt_template_subtopics(conn, names, renames) -> dict:
    """Switch to the template's subtopic list, in one transaction (see template_plan)."""
    matches, rest = _match_template(conn, names, renames)
    plan, retire = _template_changes(matches, rest)
    try:
        for name, subtopic in matches:
            if subtopic is None:
                _insert_subtopic(conn, name, "seeded")
            else:
                conn.execute("UPDATE subtopics SET status='seeded', merged_into=NULL, name=? WHERE id=?",
                             (name, subtopic["id"]))
        for subtopic in retire:
            conn.execute("UPDATE subtopics SET status=? WHERE id=?", (SUBTOPIC_RETIRED, subtopic["id"]))
        resort = 0
        for sid, subtopic_id, merged in conn.execute(
                "SELECT id, subtopic_id, merged_into FROM studies WHERE subtopic_id IS NOT NULL").fetchall():
            final = resolve_subtopic(conn, subtopic_id)
            if final is not None and final["status"] == SUBTOPIC_RETIRED:
                conn.execute("UPDATE studies SET subtopic_id=NULL, classified_version=NULL WHERE id=?", (sid,))
                resort += merged is None
        _set_positions(conn, names)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"added": plan["add"], "carried": plan["carry"], "retired": plan["retire"],
            "studies_to_resort": resort}


def study_first_published(conn) -> dict[int, str]:
    """study id -> the earliest publication date among its items."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT study_id, MIN(published) FROM items WHERE study_id IS NOT NULL "
        "AND published IS NOT NULL AND published != '' GROUP BY study_id")}


def subtopic_missing(conn, study: dict) -> bool:
    """The study has no subtopic: none set, one that does not resolve, or one rejected or retired."""
    if study.get("subtopic_id") is None:
        return True
    final = resolve_subtopic(conn, study["subtopic_id"])
    return final is None or final["status"] in NO_SUBTOPIC_STATUSES


def studies_to_classify(conn, version: str) -> list[dict]:
    """Active studies with an item on the page that lack a subtopic, metric, data date, entities check or
    checked headline, and were not asked under this prompt version (or whose subtopic is retired).
    Newest first."""
    marks = ",".join("?" * len(STUDY_ITEM_STATES))
    out = []
    for row in conn.execute(
            "SELECT * FROM studies s WHERE s.merged_into IS NULL AND EXISTS "
            f"(SELECT 1 FROM items i WHERE i.study_id = s.id AND i.state IN ({marks})) "
            "ORDER BY s.id DESC", STUDY_ITEM_STATES).fetchall():
        study = dict(row)
        no_subtopic = subtopic_missing(conn, study)
        resolved = resolve_subtopic(conn, study["subtopic_id"]) if study["subtopic_id"] is not None else None
        retired = resolved is not None and resolved["status"] == SUBTOPIC_RETIRED
        lacking = (no_subtopic or study_metric(conn, study) is None
                   or study["data_date"] is None or study["headline_checked"] != 1
                   or study["entities_checked"] is None)
        if lacking and (study["classified_version"] != version or retired):
            out.append(study)
    return out


def classify_source_item(conn, study: dict) -> dict | None:
    """The item classify reads: the creating item, else the study's earliest item with full text,
    else its earliest item."""
    if study.get("created_by_item") is not None:
        item = get_item(conn, study["created_by_item"])
        if item:
            return item
    for sql in ("SELECT * FROM items WHERE study_id=? AND full_text IS NOT NULL AND full_text != '' "
                "ORDER BY id LIMIT 1", "SELECT * FROM items WHERE study_id=? ORDER BY id LIMIT 1"):
        row = conn.execute(sql, (study["id"],)).fetchone()
        if row:
            return dict(row)
    return None


def fill_study_knowledge(conn, study_id: int, version: str, *, subtopic_id=None, metric_id: int | None = None,
                         data_date=None, headline: dict | None = None,
                         entity_names: list | None = None) -> list[str]:
    """Fill only what the study lacks (never a tier, facts, an owner's subtopic or a metric it has,
    or the entities of a study that was already asked),
    then mark it classified under `version`. Returns the names filled."""
    study = get_study(conn, study_id)
    filled = []
    if subtopic_id is not None and subtopic_missing(conn, study):
        conn.execute("UPDATE studies SET subtopic_id=? WHERE id=?", (subtopic_id, study_id))
        filled.append("subtopic")
    if metric_id is not None and study_metric(conn, study) is None:
        conn.execute("UPDATE studies SET metric_id=? WHERE id=?", (metric_id, study_id))
        filled.append("metric")
    if data_date is not None and study["data_date"] is None:
        conn.execute("UPDATE studies SET data_date=? WHERE id=?", (data_date, study_id))
        filled.append("data_date")
    if headline and (study["headline_quote"] is None
                     or (not study["headline_checked"] and headline.get("checked"))):
        conn.execute("""UPDATE studies SET headline_value=?, headline_label=?, headline_quote=?,
                        headline_checked=? WHERE id=?""", (*headline_columns(headline), study_id))
        filled.append("headline")
    if entity_names is not None and study["entities_checked"] is None:
        link_study_entities(conn, study_id, entity_names)
        filled.append("entities")
    conn.execute("UPDATE studies SET classified_version=? WHERE id=?", (version, study_id))
    conn.commit()
    return filled


def mark_classified(conn, study_id: int, version: str) -> None:
    conn.execute("UPDATE studies SET classified_version=? WHERE id=?", (version, study_id))
    conn.commit()


def clear_classified(conn) -> int:
    count = conn.execute("UPDATE studies SET classified_version=NULL "
                         "WHERE classified_version IS NOT NULL").rowcount
    conn.commit()
    return count


TRACK_KEYS = ("items", "disputed", "red_flagged", "misrepresented")


def source_track_records(conn) -> dict[str, dict]:
    """Per source: items that passed the judge, and how many of them were disputed, carry a red
    flag, or were flagged as misrepresenting their original. Computed on read; reports never show it."""
    out: dict[str, dict] = {}
    for row in conn.execute("SELECT source_name, disputed, red_flags FROM items WHERE verdict_pass=1"):
        rec = out.setdefault(row["source_name"], dict.fromkeys(TRACK_KEYS, 0))
        flags = _json_list(row["red_flags"])
        rec["items"] += 1
        rec["disputed"] += 1 if row["disputed"] else 0
        rec["red_flagged"] += 1 if flags else 0
        rec["misrepresented"] += 1 if "misrepresented_primary" in flags else 0
    return out


STUDY_ITEM_STATES = ("verified", "embedded", "reported")   # items that count for a study (on the page)


def study_item_counts(conn) -> dict[int, int]:
    marks = ",".join("?" * len(STUDY_ITEM_STATES))
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT study_id, COUNT(*) FROM items WHERE study_id IS NOT NULL AND state IN ({marks}) "
        "GROUP BY study_id", STUDY_ITEM_STATES)}


def pair_explanations(conn, status: str | None = None) -> list[dict]:
    if status is None:
        rows = conn.execute("SELECT * FROM pair_explanations ORDER BY id")
    else:
        rows = conn.execute("SELECT * FROM pair_explanations WHERE status=? ORDER BY id", (status,))
    return [dict(r) for r in rows]


def get_pair_explanation(conn, study_a: int, study_b: int, question_id: int) -> dict | None:
    """The stored explanation of a pair on a question (rows from before questions are never matched)."""
    row = conn.execute("SELECT * FROM pair_explanations WHERE study_a=? AND study_b=? AND question_id=?",
                       (study_a, study_b, question_id)).fetchone()
    return dict(row) if row else None


def save_pair_explanation(conn, *, study_a: int, study_b: int, subtopic_id, question_id: int, status: str,
                          reason, quote_a, quote_b, note, inputs_hash: str, prompt_version: str) -> None:
    """One row per pair and question (study_a < study_b); a new answer replaces the old one."""
    if study_a >= study_b:
        raise ValueError("study_a must be the smaller study id")
    conn.execute(
        """INSERT INTO pair_explanations (study_a, study_b, question_id, subtopic_id, reason, quote_a,
               quote_b, note, status, inputs_hash, prompt_version, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(study_a, study_b, question_id) DO UPDATE SET subtopic_id=excluded.subtopic_id,
               reason=excluded.reason, quote_a=excluded.quote_a, quote_b=excluded.quote_b,
               note=excluded.note, status=excluded.status, inputs_hash=excluded.inputs_hash,
               prompt_version=excluded.prompt_version, created_at=excluded.created_at""",
        (study_a, study_b, question_id, subtopic_id, reason, quote_a, quote_b, note, status, inputs_hash,
         prompt_version, _now()))
    conn.commit()


def _decode_question_explanation(row) -> dict:
    out = dict(row)
    out["lines"] = json.loads(out["lines"]) if out["lines"] else []
    return out


def save_question_explanation(conn, *, question_id: int, status: str, summary, lines: list, main_result: str,
                              inputs_hash: str, prompt_version: str) -> None:
    """One row per question; a new answer replaces the old one."""
    conn.execute(
        """INSERT INTO question_explanations (question_id, status, summary, lines, main_result, inputs_hash,
               prompt_version, created_at) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(question_id) DO UPDATE SET status=excluded.status, summary=excluded.summary,
               lines=excluded.lines, main_result=excluded.main_result, inputs_hash=excluded.inputs_hash,
               prompt_version=excluded.prompt_version, created_at=excluded.created_at""",
        (question_id, status, summary, json.dumps(lines, ensure_ascii=False), main_result, inputs_hash,
         prompt_version, _now()))
    conn.commit()


def question_explanations(conn, status: str | None = None) -> list[dict]:
    if status is None:
        rows = conn.execute("SELECT * FROM question_explanations ORDER BY question_id")
    else:
        rows = conn.execute("SELECT * FROM question_explanations WHERE status=? ORDER BY question_id", (status,))
    return [_decode_question_explanation(r) for r in rows]


def get_question_explanation(conn, question_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM question_explanations WHERE question_id=?", (question_id,)).fetchone()
    return _decode_question_explanation(row) if row else None


def clear_question_explanations(conn) -> int:
    count = conn.execute("DELETE FROM question_explanations").rowcount
    conn.commit()
    return count


# --- SP3b-P: primary-source check (tables from schema step 5) ---
PRIMARY_UNREACHABLE_AFTER = 3
PRIMARY_CHECK_STATUSES = ("agreed", "undecided", "unreachable")


def primary_source(conn, url: str) -> dict | None:
    row = conn.execute("SELECT * FROM primary_sources WHERE url=?", (url,)).fetchone()
    return dict(row) if row else None


def record_primary_fetch(conn, url: str, text: str | None = None,
                         error: str | None = None) -> dict:
    """One fetch of an original (url: its canonical URL). Success stores the text; a failure
    counts an attempt, and the third failed attempt makes the URL unreachable."""
    if (text is None) == (error is None):
        raise ValueError("record_primary_fetch needs exactly one of text or error")
    now = _now()
    if text is not None:
        conn.execute("""INSERT INTO primary_sources (url, text, status, attempts, fetched_at, last_error)
                        VALUES (?, ?, 'ok', 0, ?, NULL)
                        ON CONFLICT(url) DO UPDATE SET text=excluded.text, status='ok',
                          fetched_at=excluded.fetched_at, last_error=NULL""", (url, text, now))
    else:
        old = primary_source(conn, url) or {}
        attempts = (old.get("attempts") or 0) + 1
        status = "unreachable" if attempts >= PRIMARY_UNREACHABLE_AFTER else "failed"
        conn.execute("""INSERT INTO primary_sources (url, text, status, attempts, fetched_at, last_error)
                        VALUES (?, NULL, ?, ?, ?, ?)
                        ON CONFLICT(url) DO UPDATE SET status=excluded.status,
                          attempts=excluded.attempts, fetched_at=excluded.fetched_at,
                          last_error=excluded.last_error""", (url, status, attempts, now, error))
    conn.commit()
    return primary_source(conn, url)


def primary_check(conn, item_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM primary_checks WHERE item_id=?", (item_id,)).fetchone()
    return dict(row) if row else None


def primary_checks_for(conn, item_ids) -> dict[int, dict]:
    ids = list(item_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["item_id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM primary_checks WHERE item_id IN ({marks})", ids)}


def save_primary_check(conn, item_id: int, *, primary_url: str, status: str, prompt_version: str,
                       numbers: str | None = None, missing=(), verdict: str | None = None,
                       quote_article: str | None = None, quote_primary: str | None = None,
                       note: str | None = None) -> None:
    if status not in PRIMARY_CHECK_STATUSES:
        raise ValueError(f"unknown primary check status {status!r}")
    conn.execute("""INSERT OR REPLACE INTO primary_checks (item_id, primary_url, numbers, missing,
                      verdict, quote_article, quote_primary, note, status, prompt_version, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (item_id, primary_url, numbers, json.dumps(list(missing)), verdict, quote_article,
                  quote_primary, note, status, prompt_version, _now()))
    conn.commit()


def add_item_red_flag(conn, item_id: int, code: str) -> bool:
    """Append a red-flag code to an item's list once; True when it was added."""
    row = conn.execute("SELECT red_flags FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        return False
    flags = _json_list(row["red_flags"])
    if code in flags:
        return False
    conn.execute("UPDATE items SET red_flags=? WHERE id=?", (json.dumps(flags + [code]), item_id))
    conn.commit()
    return True


# --- wave 4a plan B: primary reset ---
def reset_primary_sources(conn, url: str | None = None) -> dict:
    """Forget failed and unreachable originals (url: one canonical URL, or None for all) and the
    'unreachable' check rows that point at them, so the next run fetches them again. Fetched
    originals and agreed/undecided checks are never touched. already_reported counts deleted
    check rows whose item is already reported (a reset alone does not check those again)."""
    where, args = "status IN ('failed', 'unreachable')", ()
    if url is not None:
        where, args = where + " AND url=?", (url,)
    urls = [row[0] for row in conn.execute(f"SELECT url FROM primary_sources WHERE {where}", args)]
    checks = reported = 0
    if urls:
        marks = ",".join("?" * len(urls))
        check_where = f"status='unreachable' AND primary_url IN ({marks})"
        checks = conn.execute(f"SELECT COUNT(*) FROM primary_checks WHERE {check_where}",
                              urls).fetchone()[0]
        reported = conn.execute(
            f"""SELECT COUNT(*) FROM primary_checks pc JOIN items i ON i.id = pc.item_id
                WHERE pc.status='unreachable' AND pc.primary_url IN ({marks})
                AND i.state='reported'""", urls).fetchone()[0]
        conn.execute(f"DELETE FROM primary_checks WHERE {check_where}", urls)
        conn.execute(f"DELETE FROM primary_sources WHERE url IN ({marks})", urls)
    conn.commit()
    return {"sources": len(urls), "checks": checks, "already_reported": reported}


# --- questions (schema step 9) ---------------------------------------------------------------------
def _clean_text(value, what: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"the question {what} is empty")
    if len(text) > limit:
        raise ValueError(f"the question {what} is {len(text)} characters; the limit is {limit}")
    return text


def _check_kind(kind: str | None, comparison: str | None) -> str | None:
    """Validate a question's kind and comparison; returns the cleaned comparison (None unless difference).
    A None kind (not decided yet) takes no comparison."""
    if kind is None:
        if comparison:
            raise ValueError("a comparison needs a kind")
        return None
    if kind not in knowledge.QUESTION_KINDS:
        raise ValueError(f"kind must be one of {', '.join(knowledge.QUESTION_KINDS)}")
    if kind != "difference":
        if comparison:
            raise ValueError(f"a {kind} question has no comparison")
        return None
    return _clean_text(comparison, "comparison", knowledge.QUESTION_COMPARISON_MAX)


def create_question(conn, subtopic_id: int, text: str, measure: str, kind: str | None = None,
                    comparison: str | None = None) -> int:
    text = _clean_text(text, "text", knowledge.QUESTION_TEXT_MAX)
    measure = _clean_text(measure, "measure", knowledge.QUESTION_MEASURE_MAX)
    comparison = _check_kind(kind, comparison)
    cur = conn.execute("""INSERT INTO questions (subtopic_id, text, measure, kind, comparison, created_at)
                          VALUES (?,?,?,?,?,?)""", (subtopic_id, text, measure, kind, comparison, _now()))
    conn.commit()
    return cur.lastrowid


def get_question(conn, question_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
    return dict(row) if row else None


def resolve_question(conn, question_id) -> dict | None:
    """The question an id ends at after owner merges; None when it does not exist."""
    seen, row = set(), None
    while question_id is not None and question_id not in seen:
        seen.add(question_id)
        row = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
        if row is None:
            return None
        question_id = row["merged_into"]
    return dict(row) if row else None


def list_questions(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM questions ORDER BY id")]


def _check_direction(direction: str) -> None:
    if direction not in knowledge.QUESTION_DIRECTIONS:
        raise ValueError(f"direction must be one of {', '.join(knowledge.QUESTION_DIRECTIONS)}")


def set_study_question(conn, study_id: int, question_id: int, direction: str | None, version: str, *,
                       by_owner: bool = False) -> None:
    """File a study under a question (its subtopic follows the question's). A direction belongs to
    difference questions only: required there, refused for the other kinds."""
    question = resolve_question(conn, question_id) if get_question(conn, question_id) else None
    if question is None:
        raise ValueError(f"no question q{question_id}")
    if direction is None:
        if question["kind"] == "difference":
            raise ValueError("a direction is required under a difference question")
    else:
        _check_direction(direction)
        if question["kind"] in ("level", "capability"):
            raise ValueError(f"q{question['id']} is a {question['kind']} question; it has no direction")
    subtopic = resolve_subtopic(conn, question["subtopic_id"])
    conn.execute("""UPDATE studies SET question_id=?, question_direction=?, grouped_version=?,
                    question_by_owner=?, subtopic_id=COALESCE(?, subtopic_id) WHERE id=?""",
                 (question_id, direction, version, 1 if by_owner else 0,
                  subtopic["id"] if subtopic else None, study_id))
    conn.commit()


def set_question_direction(conn, study_id: int, direction: str | None, version: str) -> None:
    """Record a direction (None: the study was answered for its question's kind only)."""
    if direction is not None:
        _check_direction(direction)
    conn.execute("UPDATE studies SET question_direction=?, grouped_version=? WHERE id=?",
                 (direction, version, study_id))
    conn.commit()


def set_question_kind(conn, question_id: int, kind: str, comparison: str | None) -> None:
    """Decide the kind of a question that has none yet (the owner changes it later with rename).
    Level and capability questions have no directions."""
    question = _question_or_error(conn, question_id)
    if question["kind"] is not None:
        raise ValueError(f"q{question_id} already has kind {question['kind']}")
    if kind is None:
        raise ValueError("kind is required")
    comparison = _check_kind(kind, comparison)
    conn.execute("UPDATE questions SET kind=?, comparison=? WHERE id=?", (kind, comparison, question_id))
    if kind != "difference":
        _clear_directions(conn, question_id)
    conn.commit()


def _clear_directions(conn, question_id: int) -> int:
    cleared = 0
    for sid in _studies_of(conn, question_id):
        cleared += conn.execute("UPDATE studies SET question_direction=NULL WHERE id=? "
                                "AND question_direction IS NOT NULL", (sid,)).rowcount
    return cleared


def _studies_of(conn, question_id: int) -> list[int]:
    """Study ids whose question resolves to this question."""
    return [r["id"] for r in conn.execute("SELECT id, question_id FROM studies WHERE question_id IS NOT NULL")
            if (resolve_question(conn, r["question_id"]) or {}).get("id") == question_id]


def _question_or_error(conn, question_id: int) -> dict:
    question = get_question(conn, question_id)
    if question is None:
        raise ValueError(f"no question q{question_id}")
    return question


def _active_question_or_error(conn, question_id: int) -> dict:
    question = _question_or_error(conn, question_id)
    if question["merged_into"] is not None:
        raise ValueError(f"q{question_id} was merged into q{question['merged_into']}")
    return question


def _same_comparison(a, b) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def rename_question(conn, question_id: int, text: str, measure: str | None = None,
                    kind: str | None = None, comparison: str | None = None) -> dict:
    question = _active_question_or_error(conn, question_id)
    text = _clean_text(text, "text", knowledge.QUESTION_TEXT_MAX)
    new_measure = (_clean_text(measure, "measure", knowledge.QUESTION_MEASURE_MAX)
                   if measure is not None else question["measure"])
    new_kind = kind if kind is not None else question["kind"]
    new_comparison = comparison if comparison is not None else (
        question["comparison"] if new_kind == "difference" else None)
    new_comparison = _check_kind(new_kind, new_comparison)
    changed = (new_measure != question["measure"] or new_kind != question["kind"]
               or not _same_comparison(new_comparison, question["comparison"]))
    cleared = _clear_directions(conn, question_id) if changed else 0
    conn.execute("UPDATE questions SET text=?, measure=?, kind=?, comparison=?, owner_touched=1 WHERE id=?",
                 (text, new_measure, new_kind, new_comparison, question_id))
    conn.commit()
    return {"renamed": f"q{question_id}", "text": text, "measure": new_measure, "kind": new_kind,
            "comparison": new_comparison, "directions_cleared": cleared}


def _active_subtopic_of_question(conn, question: dict) -> dict:
    subtopic = resolve_subtopic(conn, question["subtopic_id"])
    if subtopic is None or subtopic["status"] not in ("seeded", "confirmed"):
        raise ValueError(f"q{question['id']} is in an inactive subtopic")
    return subtopic


def merge_questions(conn, question_id: int, into: int) -> dict:
    """Merge a question into another, across subtopics: its studies follow into the target's subtopic."""
    if question_id == into:
        raise ValueError("a question cannot be merged into itself")
    source = _active_question_or_error(conn, question_id)
    target = _active_question_or_error(conn, into)
    if source["kind"] and target["kind"] and source["kind"] != target["kind"]:
        raise ValueError(f"q{question_id} is a {source['kind']} question, q{into} a {target['kind']} question")
    _active_subtopic_of_question(conn, source)
    subtopic = _active_subtopic_of_question(conn, target)
    moved = _studies_of(conn, question_id)
    if target["kind"] is None and source["kind"] is not None:
        conn.execute("UPDATE questions SET kind=?, comparison=? WHERE id=?",
                     (source["kind"], source["comparison"], into))
        target = {**target, "kind": source["kind"], "comparison": source["comparison"]}
    cleared = 0
    if target["kind"] in ("level", "capability"):    # no directions under these kinds (spec ruling 2)
        for sid in moved + _studies_of(conn, into):
            cleared += conn.execute("UPDATE studies SET question_direction=NULL WHERE id=? "
                                    "AND question_direction IS NOT NULL", (sid,)).rowcount
    elif target["kind"] == "difference" and not _same_comparison(source["comparison"], target["comparison"]):
        for sid in moved:
            cleared += conn.execute("UPDATE studies SET question_direction=NULL WHERE id=? "
                                    "AND question_direction IS NOT NULL", (sid,)).rowcount
    for sid in moved:
        conn.execute("UPDATE studies SET subtopic_id=? WHERE id=?", (subtopic["id"], sid))
    conn.execute("UPDATE questions SET merged_into=? WHERE id=?", (into, question_id))
    conn.execute("UPDATE questions SET owner_touched=1 WHERE id=?", (into,))
    conn.commit()
    return {"merged": f"q{question_id}", "into": f"q{into}", "directions_cleared": cleared}


def move_study(conn, study_id: int, question_id: int) -> dict:
    """Put a study under a question of any active subtopic; its subtopic follows the question's."""
    study = get_study(conn, study_id)
    if study is None:
        raise ValueError(f"no study s{study_id}")
    final = resolve_question(conn, question_id)
    if final is None:
        raise ValueError(f"no question q{question_id}")
    subtopic = _active_subtopic_of_question(conn, final)
    conn.execute("""UPDATE studies SET question_id=?, question_direction=NULL, question_by_owner=1,
                    subtopic_id=? WHERE id=?""", (final["id"], subtopic["id"], study_id))
    conn.commit()
    return {"moved": f"s{study_id}", "into": f"q{final['id']}"}


def is_pinned(conn, study: dict) -> bool:
    """The owner placed the study, or its question is one the owner renamed or merged into: the LLM
    never moves it."""
    if study["question_by_owner"]:
        return True
    question = resolve_question(conn, study["question_id"]) if study["question_id"] is not None else None
    return bool(question and question["owner_touched"])


def clear_grouping(conn) -> int:
    """Unpinned studies go back to ungrouped (question, direction and version cleared)."""
    count = 0
    for row in conn.execute("SELECT * FROM studies WHERE question_id IS NOT NULL "
                            "OR question_direction IS NOT NULL OR grouped_version IS NOT NULL").fetchall():
        study = dict(row)
        if is_pinned(conn, study):
            continue
        conn.execute("UPDATE studies SET question_id=NULL, question_direction=NULL, grouped_version=NULL "
                     "WHERE id=?", (study["id"],))
        count += 1
    conn.commit()
    return count


GROUP_VERSION = "group-2"     # the group stage's prompt version; a test asserts group.PROMPT_VERSION equals it


def studies_to_group(conn) -> list[dict]:
    """Counting studies in an active subtopic that the group stage should answer, newest first:
    - no counting question yet (never grouped, or their subtopic changed): `fixed_question` None;
    - grouped under an older prompt version and not pinned by the owner: asked once more (re-group);
    - in a question that lacks what only an answer can give: its kind (NULL), or the study's own
      direction under a difference question. `fixed_question` names the question; it is kept.
    A pinned study (placed by the owner, or in a question the owner renamed or merged into) is
    selected only for those gaps. Level and capability questions need no direction."""
    placed, _ = knowledge.placed_studies(conn)
    out = []
    for study, subtopic, question in placed:
        if subtopic is None:
            continue
        gap = question is not None and (question["kind"] is None or (
            question["kind"] == "difference" and study["question_direction"] is None))
        if question is None:
            out.append({**study, "fixed_question": None})
        elif is_pinned(conn, study):
            if gap:
                out.append({**study, "fixed_question": question["id"]})
        elif study["grouped_version"] != GROUP_VERSION:
            out.append({**study, "fixed_question": None})
        elif gap:
            out.append({**study, "fixed_question": question["id"]})
    return sorted(out, key=lambda s: -s["id"])
