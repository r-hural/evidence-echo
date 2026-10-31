"""Search for studies on one question: a run is a folder with search.json and a scratch database that is
seeded from the topic and graded by the usual stages (`--search <run>`); only keep adds anything to the topic."""
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from evidence_echo import api, query
from evidence_echo.core import config, contracts, knowledge, settings, store, topics
from evidence_echo.core.settings import ConfigError
from evidence_echo.core.urls import canonical_url
from evidence_echo.render import clock, html, model, search_page
from evidence_echo.render import rejections as rejections_render
from evidence_echo.sources import base, once
from evidence_echo.stages import fetch

SCRATCH_DB = "echo.db"
META = "search.json"
STATUSES = ("open", "kept", "dropped")
MAX_QUESTION = 300
SLUG_MAX = 40
MAX_SUMMARY_WORDS = 200
KEEPABLE = ("verified", "embedded")
UNGRADED = ("seen", "fetched")
NOT_COPIED = {"id", "study_id", "first_study_id", "match_reason", "state", "rejected_at"}
SEED_TABLES = ("subtopics", "metrics", "entities", "split_proposals", "split_groups", "split_moves")


def _slug(question: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    if len(slug) > SLUG_MAX:
        head = slug[:SLUG_MAX + 1]
        slug = (head[:head.rfind("-")] if "-" in head else head[:SLUG_MAX]).strip("-")
    return slug or "search"


def run_id(question: str, now: datetime, taken: set[str]) -> str:
    """`<UTC yyyymmdd-hhmm>-<slug>`, with -2, -3, ... while the id is taken."""
    base = f"{now.astimezone(timezone.utc):%Y%m%d-%H%M}-{_slug(question)}"
    rid, n = base, 1
    while rid in taken:
        n += 1
        rid = f"{base}-{n}"
    return rid


def read_meta(run_dir: Path) -> dict:
    return settings.read_json_object(run_dir / META)


def write_meta(run_dir: Path, meta: dict) -> None:
    settings.write_json_atomic(run_dir / META, meta)


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(topic_db: Path, scratch: Path) -> None:
    """Copy the topic's subtopics, metrics, entities and split rows, same ids, into the scratch database."""
    with closing(query.open_ro(topic_db)) as src, closing(store.connect(scratch)) as dst:
        for table in SEED_TABLES:
            have = {r[1] for r in dst.execute(f"PRAGMA table_info({table})")}
            data = src.execute(f"SELECT * FROM {table}").fetchall()
            if not data:
                continue
            cols = [c for c in data[0].keys() if c in have]
            marks = ", ".join("?" for _ in cols)
            dst.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})",
                            [tuple(r[c] for c in cols) for r in data])
        dst.commit()


def start(cfg, question: str, now: datetime | None = None) -> dict:
    question = " ".join((question or "").split())
    if not question:
        raise ConfigError("a search needs a question")
    if len(question) > MAX_QUESTION:
        raise ConfigError(f"the question is {len(question)} characters; the limit is {MAX_QUESTION}")
    tid = cfg.topic.id
    paths = next((p for p in config.read_paths().topics if p.id == tid), None)
    reason = query.db_status(paths) if paths else query.STATUS_NO_DATA
    if reason:
        raise ConfigError(f"topic '{tid}' cannot be searched yet ({reason}); run a report first")
    now = now or datetime.now(timezone.utc)
    parent = cfg.data_dir / config.SEARCHES_DIR / tid
    taken = {p.name for p in parent.iterdir()} if parent.is_dir() else set()
    rid = run_id(question, now, taken)
    run_dir = config.search_dir(cfg.data_dir, tid, rid)
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        _seed(cfg.db_path, run_dir / SCRATCH_DB)
        write_meta(run_dir, {"run": rid, "topic": tid, "question": question, "created": _stamp(now),
                             "status": "open", "status_at": _stamp(now), "queries": [], "candidates": [],
                             "summary": None, "writeups": {}, "kept": None})
    except (OSError, sqlite3.Error):
        for f in run_dir.glob("*"):
            f.unlink(missing_ok=True)
        run_dir.rmdir()
        raise
    return {"run": rid, "dir": str(run_dir), "topic": tid}


def source_name(run: str, n: int) -> str:
    return f"Search: {run} #{n}"


def _forms(canon: str) -> tuple[str, str]:
    """The canonical URL without and with a trailing slash on the path: one page, two spellings."""
    parts = urlsplit(canon)
    path = parts.path.rstrip("/")
    return tuple(urlunsplit(parts._replace(path=p)) for p in (path or "/", (path or "") + "/"))


def _find(conn, canon: str) -> bool:
    return any(store.find_by_canonical(conn, f) for f in _forms(canon))


def _held(candidates: list[dict]) -> list[dict]:
    return [c for c in candidates if c.get("outcome") in ("added", "blocked")]


def _last_n(conn, run: str, candidates: list[dict]) -> int:
    """The highest `#n` of the run so far, in the scratch items or in search.json (an add killed before
    search.json was written leaves items it does not list)."""
    prefix = source_name(run, "")
    names = [r[0] for r in conn.execute("SELECT DISTINCT source_name FROM items")]
    ns = [int(name[len(prefix):]) for name in names if name.startswith(prefix) and name[len(prefix):].isdigit()]
    return max([0, *ns, *(c["n"] for c in candidates if isinstance(c.get("n"), int))])


def _public(url: str) -> bool:
    """False for a URL whose host the fetchers refuse (private, loopback, link-local, unresolvable)."""
    try:
        base.check_public_host(url)
    except base.FetchError:
        return False
    return True


def add(cfg, run: str, urls: list[str], queries: list[str] = (), ctx=None) -> dict:
    """Store each new candidate URL as a one-time item in the scratch database; say what became of every URL."""
    run_dir = config.search_dir(cfg.data_dir, cfg.topic.id, run)
    meta = read_meta(run_dir)
    if meta.get("status") != "open":
        raise ConfigError(f"search '{run}' is {meta.get('status')}")
    out = {"added": [], "known": [], "duplicate": [], "over_limit": [], "invalid": [], "blocked": []}
    candidates = meta.setdefault("candidates", [])
    held = {_forms(canonical_url(c["url"]))[0] for c in _held(candidates)}
    ctx = ctx or fetch.context_for(cfg)
    topic_db = cfg.topic_dir / "echo.db"
    try:
        with closing(query.open_ro(topic_db)) as known, closing(store.connect(cfg.db_path)) as conn:
            for url in urls:
                canon = canonical_url(url)
                if canon is None or not _public(url):
                    outcome = "invalid"
                elif _find(known, canon):
                    outcome = "known"
                elif _forms(canon)[0] in held or _find(conn, canon):
                    outcome = "duplicate"
                elif len(_held(candidates)) >= cfg.search_max_candidates:
                    outcome = "over_limit"
                else:
                    outcome = "added"
                record = {"url": url, "outcome": outcome}
                if outcome == "added":
                    n = _last_n(conn, run, candidates) + 1
                    source = {"type": "once", "name": source_name(run, n), "url": url}
                    result = once.list_new([source], ctx)[source["name"]]
                    if result.error and result.error.startswith("refused"):   # host policy: never retried
                        record = {"url": url, "outcome": "invalid"}
                        out["invalid"].append(url)
                        candidates.append(record)
                        continue
                    if result.error:   # unreachable: the session may still be able to read it
                        result = base.SourceResult(entries=[base.Entry(url=url, title=url, date_unknown=True)],
                                                   blocked=True)
                    fetch._store_once(conn, source, result, ctx)
                    held.add(_forms(canon)[0])
                    record["n"] = n
                    out["added"].append({"n": n, "url": url})
                    if result.blocked:
                        record["outcome"] = "blocked"
                        out["blocked"].append(url)
                else:
                    out[outcome].append(url)
                candidates.append(record)
    finally:
        seen = meta.setdefault("queries", [])
        seen.extend(q for q in dict.fromkeys(queries) if q not in seen)
        write_meta(run_dir, meta)
    return out


def _new_studies(conn) -> dict:
    return model.split(conn)[0]


def render(cfg, run: str, summary: str, writeups: str) -> dict:
    """Store the answer text and the study write-ups in search.json, then write the answer page."""
    summary = (summary or "").strip()
    words = len(summary.split())
    if not words:
        raise ValueError("the summary is empty; fix the file and run again")
    if words > MAX_SUMMARY_WORDS:
        raise ValueError(f"the summary is {words} words; the limit is {MAX_SUMMARY_WORDS}; "
                         "fix the file and run again")
    run_dir = config.search_dir(cfg.data_dir, cfg.topic.id, run)
    meta = read_meta(run_dir)
    with closing(store.connect(cfg.db_path)) as conn:
        expected = set(_new_studies(conn))
    accepted, rejected = contracts.check_payload("report", writeups, expected)
    meta["summary"] = summary
    meta["writeups"] = {str(e["study_id"]): {"title": e["title"], "summary": e["summary"]} for e in accepted}
    write_meta(run_dir, meta)
    out = render_page(cfg, run)
    out["rejected_writeups"] = rejected
    return out


def _page_model(cfg, run: str, meta: dict, now: datetime) -> search_page.SearchPage:
    with closing(store.connect(cfg.db_path)) as conn:
        entries = {int(sid): w for sid, w in (meta.get("writeups") or {}).items()}
        rep = model.build_report(conn, cfg.topic, entries, now, cfg.grouping_mode)
        candidates = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        rejected = rejections_render.rows_in_window(store.rejected_items(conn), None, now)
        shown = sorted(rep.new + rep.disputed, key=model.entry_order)
        years = [knowledge.data_year(store.get_study(conn, e.study_id) or {}, e.published, now.date())[0] for e in shown]
    return search_page.SearchPage(
        run=run, topic_id=cfg.topic.id, topic_name=cfg.topic.name, question=meta.get("question", ""),
        date=clock.local_date(meta.get("created")), status=meta.get("status", "open"),
        status_date=clock.local_date(meta.get("status_at")) or None, candidates=candidates,
        graded=len(shown), rejected=[(r.url, r.title, r.reason) for r in rejected], entries=shown,
        summary=meta.get("summary") or "", queries=list(meta.get("queries") or []),
        disclaimer=rep.disclaimer,
        kept=len(meta["kept"]["studies"]) if meta.get("kept") else None,
        age_note=knowledge.age_note(years, now.date()))


def render_page(cfg, run: str, now: datetime | None = None) -> dict:
    """Write the answer page (HTML when html_reports is on, Markdown when it is visible) from the scratch
    database and search.json. Nothing of the topic is touched."""
    now = now or datetime.now(timezone.utc)
    run_dir = config.search_dir(cfg.data_dir, cfg.topic.id, run)
    meta = read_meta(run_dir)
    page = _page_model(cfg, run, meta, now)
    target = cfg.reports_dir / "searches"
    out = {"page": None, "markdown": None, "studies": page.graded}
    if cfg.html_reports == "on":
        path = target / f"{run}.html"
        topic_path = cfg.reports_dir / f"{cfg.topic.id}.html"
        overview = cfg.overview_dir / "overview.html"
        html._write(path, search_page.search_html(page, html.href(topic_path, path.parent),
                                                  html.href(overview, path.parent)))
        out["page"] = str(path)
    if cfg.md_dir == cfg.reports_dir:
        path = target / f"{run}.md"
        html._write(path, search_page.search_markdown(page))
        out["markdown"] = str(path)
    meta["page"] = out["page"] or out["markdown"]
    meta["studies"] = page.graded
    write_meta(run_dir, meta)
    # the topic page's Searches list links the answer page now, not only after the next report run
    try:   # topic.json as it is now: a keep has just added its search sources
        topic = topics.load_topic(cfg.topic_dir.parent, cfg.topic.id)
    except (ConfigError, OSError, ValueError) as exc:
        out["topic_page_error"] = str(exc)
        return out
    out.update(api.refresh_topic_page(replace(cfg, db_path=cfg.topic_dir / "echo.db", search_run=None,
                                              topic=topic)))
    return out


def list_runs(data_dir: Path, topic_id: str | None = None) -> list[dict]:
    """Every search run (of one topic, or of all), newest first; a run with an unreadable search.json is
    listed as broken, after the others."""
    root = data_dir / config.SEARCHES_DIR
    folders = [root / topic_id] if topic_id else sorted(root.iterdir()) if root.is_dir() else []
    rows = []
    for topic_dir in folders:
        for run_dir in sorted(topic_dir.iterdir()) if topic_dir.is_dir() else []:
            if not run_dir.is_dir():
                continue
            try:
                meta = read_meta(run_dir)
                rows.append({"run": run_dir.name, "topic": topic_dir.name, "question": meta["question"],
                             "created": meta["created"], "status": meta["status"],
                             "status_at": meta.get("status_at"), "studies": meta.get("studies"),
                             "page": meta.get("page")})
            except (ConfigError, OSError, KeyError, TypeError, ValueError):
                rows.append({"run": run_dir.name, "topic": topic_dir.name, "question": None, "created": None,
                             "status": "broken", "status_at": None, "studies": None, "page": None})
    rows.sort(key=lambda r: (r["created"] is not None, r["created"] or "", r["run"]), reverse=True)
    return rows


def parse_only(text: str) -> list[str]:
    """`--only s1,s3` -> ["s1", "s3"]; anything that is not a list of scratch study ids is refused."""
    ids = [part.strip() for part in text.split(",")]
    if not all(re.fullmatch(r"s\d+", i) for i in ids):
        raise ConfigError(f"--only takes study ids like s1,s3 (got {text!r})")
    return list(dict.fromkeys(ids))


def _require_open(run: str, meta: dict) -> None:
    if meta.get("status") != "open":
        raise ConfigError(f"search '{run}' is {meta.get('status')}; its page is "
                          f"{meta.get('page') or '(none was written)'}")


def _keepable(conn) -> dict[str, list[dict]]:
    """The scratch studies that can be kept (an item of theirs is graded and verified), id order."""
    out: dict[str, list[dict]] = {}
    rows = conn.execute("SELECT * FROM items WHERE study_id IS NOT NULL AND state IN (?, ?) "
                        "ORDER BY study_id, id", KEEPABLE)
    for row in rows:
        out.setdefault(f"s{row['study_id']}", []).append(dict(row))
    return out


def _copy_primary(src, dst, old_id: int, new_id: int) -> None:
    """The item's original-source check and the fetched text of its original, under the new item id."""
    check = src.execute("SELECT * FROM primary_checks WHERE item_id=?", (old_id,)).fetchone()
    if check is None:
        return
    cols = [c for c in check.keys() if c != "item_id"]
    dst.execute(f"INSERT OR REPLACE INTO primary_checks (item_id, {', '.join(cols)}) "
                f"VALUES (?{', ?' * len(cols)})", (new_id, *(check[c] for c in cols)))
    for url in {check["primary_url"], canonical_url(check["primary_url"])} - {None}:
        row = src.execute("SELECT * FROM primary_sources WHERE url=?", (url,)).fetchone()
        if row is not None:
            names = row.keys()
            dst.execute(f"INSERT OR IGNORE INTO primary_sources ({', '.join(names)}) "
                        f"VALUES ({', '.join('?' for _ in names)})", tuple(row))


def _mapped(dst, item: dict) -> dict:
    """The item's topic-dependent answers as the topic knows them now: a metric merged since start maps
    to its survivor, a rejected or unknown one to `other`; a subtopic proposal reuses the subtopic of that
    name or adds a proposed one; a choice naming an unknown subtopic is dropped."""
    out = {"state": "judged"}
    code = item.get("metric_code")
    if code is not None:
        metric = store.metric_for_slug(dst, code)
        out["metric_code"] = metric["slug"] if metric else store.OTHER_METRIC
    try:
        choice = json.loads(item.get("subtopic_choice") or "null")
    except ValueError:
        choice = None
    if isinstance(choice, dict):
        _, error = store.subtopic_for_choice(dst, choice, create=True)
        if error:
            out["subtopic_choice"] = None
    return out


def _copy_item(src, dst, item: dict, over: int | None = None) -> None:
    """One kept item into the topic database, with its primary check, in one transaction; `over` is the
    id of an ungraded topic item of the same page and source that it replaces."""
    have = {r[1] for r in dst.execute("PRAGMA table_info(items)")}
    values = {c: v for c, v in item.items() if c in have and c not in NOT_COPIED}
    values.update(_mapped(dst, item))
    try:
        if over is None:
            new_id = dst.execute(f"INSERT INTO items ({', '.join(values)}) VALUES "
                                 f"({', '.join('?' for _ in values)})", tuple(values.values())).lastrowid
        else:
            dst.execute(f"UPDATE items SET {', '.join(c + '=?' for c in values)} WHERE id=?",
                        (*values.values(), over))
            new_id = over
        _copy_primary(src, dst, item["id"], new_id)
        dst.commit()
    except BaseException:
        dst.rollback()
        raise


def _delete_scratch(run_dir: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        (run_dir / (SCRATCH_DB + suffix)).unlink(missing_ok=True)


def _rerender(cfg, run: str, run_dir: Path) -> dict:
    """Re-render the answer page of a closed run from its scratch database, then delete the database. A
    failed render keeps the database (`ev search render <run>` tries again) and is reported as page_error;
    a database that cannot be deleted (Windows: a file held open) is a warning."""
    out = {}
    try:
        render_page(cfg, run)
    except Exception as exc:   # the status flip stands; the page can be written again
        out["page_error"] = f"{exc}; run `ev search render {run}` to write the page again"
        return out
    try:
        _delete_scratch(run_dir)
    except OSError as exc:
        out["warning"] = f"the scratch database could not be deleted ({exc}); delete {run_dir / SCRATCH_DB}"
    return out


def _close_run(cfg, run: str, run_dir: Path, meta: dict, status: str, kept: dict | None) -> tuple[dict, dict]:
    """Flip the status, re-render the answer page from the scratch database with it (before the database
    goes), then delete the database. search.json and the pages stay."""
    meta.update(status=status, status_at=_stamp(datetime.now(timezone.utc)), kept=kept)
    write_meta(run_dir, meta)
    out = _rerender(cfg, run, run_dir)
    return read_meta(run_dir), out


def is_closed(cfg, run: str) -> bool:
    run_dir = config.search_dir(cfg.data_dir, cfg.topic.id, run)
    return (run_dir / META).is_file() and read_meta(run_dir).get("status") in ("kept", "dropped")


def render_closed(cfg, run: str) -> dict:
    """`ev search render <run>` on a kept or dropped run whose page failed to render: write it again from
    search.json and the scratch database, which then goes. `cfg` is the topic's."""
    run_dir = config.search_dir(cfg.data_dir, cfg.topic.id, run)
    meta = read_meta(run_dir)
    scratch = run_dir / SCRATCH_DB
    if not scratch.is_file():
        raise ConfigError(f"search '{run}' is {meta.get('status')}; its page is "
                          f"{meta.get('page') or '(none was written)'}")
    out = _rerender(replace(cfg, db_path=scratch, search_run=run), run, run_dir)
    if "page_error" in out:
        raise RuntimeError(out["page_error"])
    return {"page": read_meta(run_dir).get("page"), **out}


def keep(cfg, run: str, only: list[str] | None = None) -> dict:
    """Copy the kept studies' items into the topic as `judged` items with their blind grade, each under a
    new one-time source marked found by search. A run that failed half-way can be run again."""
    run_dir = config.search_dir(cfg.data_dir, cfg.topic.id, run)
    meta = read_meta(run_dir)
    _require_open(run, meta)
    if only is not None and not only:
        raise ConfigError("nothing to keep; use `ev search drop` to discard the run")
    topic_cfg = replace(cfg, db_path=cfg.topic_dir / "echo.db", search_run=None)
    with closing(store.connect(cfg.db_path)) as scratch:
        studies = _keepable(scratch)
        if not studies:
            raise ConfigError(f"search '{run}' has no graded studies to keep; use `ev search drop`")
        chosen = sorted(studies if only is None else only, key=lambda s: int(s[1:]))
        unknown = [s for s in chosen if s not in studies]
        if unknown:
            raise ConfigError(f"no such study in this search: {', '.join(unknown)} "
                              f"(keepable: {', '.join(studies)})")
        items = sorted((i for s in chosen for i in studies[s]), key=lambda i: i["id"])
        topics_dir = cfg.topic_dir.parent
        with api._topic_db(topic_cfg) as dst:
            todo, wanted = [], []   # todo: (item, ungraded topic item it replaces); wanted: every kept item
            for item in items:
                canon = item["canonical_url"] or canonical_url(item["url"])
                there = next(filter(None, (store.find_by_canonical(dst, f) for f in _forms(canon))), None)
                if there is None:
                    todo.append((item, None))
                elif there["source_name"] != item["source_name"]:
                    continue   # the topic already has this page: it joins its study at match
                elif there["state"] in UNGRADED:   # fetched raw after an interrupted keep
                    todo.append((item, there["id"]))
                wanted.append(item)
            sources = [{"type": "once", "name": i["source_name"], "url": i["url"], "found_by": "search",
                        "search_run": run, "search_question": meta["question"]} for i in wanted]
            for source in sources:   # done before the source is in topic.json: fetch never lists it
                store.set_source_fields(dst, source["name"], "once", done=1)
            topic = topics.load_topic(topics_dir, cfg.topic.id)
            have = {s["name"] for s in topic.sources}
            if any(s["name"] not in have for s in sources):
                topics.update_topic(topics_dir, cfg.topic.id,
                                    sources=[*topic.sources, *(s for s in sources if s["name"] not in have)])
            for item, over in todo:
                _copy_item(scratch, dst, item, over)
    meta, closed = _close_run(cfg, run, run_dir, meta, "kept",
                              {"studies": chosen, "of": len(studies), "items": len(wanted)})
    return {"kept": chosen, "items": len(wanted), "sources": [s["name"] for s in sources],
            "page": meta.get("page"), **closed}


def drop(cfg, run: str) -> dict:
    """Discard the scratch database; the answer page stays and says Dropped."""
    run_dir = config.search_dir(cfg.data_dir, cfg.topic.id, run)
    meta = read_meta(run_dir)
    _require_open(run, meta)
    meta, closed = _close_run(cfg, run, run_dir, meta, "dropped", None)
    return {"dropped": run, "page": meta.get("page"), **closed}
