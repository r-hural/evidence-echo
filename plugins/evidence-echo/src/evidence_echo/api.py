"""Library API: every pipeline operation as a function of Config.

Returns dicts or JSON strings; never prints, never reads argv. Front ends (the
CLI today, MCP later) call these and present the results.
"""
import dataclasses
import json
import os
import re
from urllib.parse import urlsplit
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from evidence_echo.core import config, embedders, interfaces, knowledge, legacy, llm_api, overview as overview_mod, settings, store, topics
from evidence_echo.core.settings import ConfigError
from evidence_echo.core.urls import canonical_url
from evidence_echo.render import html as html_render
from evidence_echo.render import model as render_model
from evidence_echo.render import obsidian as obsidian_render
from evidence_echo.render import rejections as rejections_render
from evidence_echo.render import sources_page as sources_render
from evidence_echo.stages import embed as embed_stage
from evidence_echo.sources import cache as fetch_cache
from evidence_echo.stages import extract as extract_stage
from evidence_echo.sources import base as source_base
from evidence_echo.sources import get_kind
from evidence_echo.sources import once as once_kind
from evidence_echo.sources import page as page_kind
from evidence_echo.sources import rss as rss_kind
from evidence_echo.stages import fallback as fallback_stage
from evidence_echo.stages import classify
from evidence_echo.stages import group as group_stage
from evidence_echo.stages import fetch as fetch_stage
from evidence_echo.stages import judge, match, relate, report, schedule, triage, verify
from evidence_echo.stages import primary as primary_stage
from evidence_echo.stages import split as split_stage

LLM_STAGES = {"triage": triage, "judge": judge, "match": match, "verify": verify,
              "relate": relate, "primary": primary_stage, "classify": classify,
              "group": group_stage, "split": split_stage}
EMIT_STAGES = {**LLM_STAGES, "report": report}
# Stages answered twice, blind (SP3b): `apply --stage <stage> <file_a> <file_b>`. Each name is also
# an LLM_STAGES entry whose module has apply(conn, payload_a: str, payload_b: str) -> dict.
# Append only: SP3b-T adds "relate", SP3b-P adds "primary".
TWO_ANSWER_STAGES: tuple[str, ...] = ("relate", "primary")
RELATE_OFF = "relate is off (ev config set relate on, or EV_ECHO_RELATE=on)"
FRONT_END_ERRORS = Exception  # HTML / Obsidian run after the commit: any failure is reported, never raised
RESET_TARGET = {  # reset --stage X puts items of these states back one step
    "triage": (("triaged", "rejected_triage"), "fetched"),
    "judge": (("judged", "matched", "rejected"), "extracted"),
    "match": (("matched",), "judged"),
    "verify": (("verified",), "matched"),
    "report": (("reported",), "verified"),
}
RESET_STAGES = (*RESET_TARGET, "classify", "group", "split", "relate")   # classify and group re-arm studies; split drops proposals; relate asks every question again


def _connect(cfg):
    return closing(store.connect(cfg.db_path))


@contextmanager
def _topic_db(cfg):
    """The topic's database with its topic.json subtopics and metrics seeded (SP3b-T): every command
    that shows or uses them sees the lists the owner configured."""
    with _connect(cfg) as conn:
        store.seed_subtopics(conn, cfg.topic.subtopics)
        store.seed_metrics(conn, cfg.topic.metrics, topics.template_metrics(cfg.topic.template)[1])
        yield conn


PRIMARY_OFF = {"skipped": "primary_check is off"}  # ev primary with primary_check off: no fetch


def _pick(stage: str, table: dict):
    if stage not in table:
        raise ValueError(f"Unknown stage '{stage}'. Choose from: {', '.join(table)}")
    return table[stage]


def fetch(cfg, fetch_all: bool = False, fresh: bool = False) -> dict:
    with _connect(cfg) as conn:
        return fetch_stage.run(conn, cfg, fetch_all=fetch_all, fresh=fresh)


def _fetch_every(raw):
    """CLI text -> topic.json value: 'default' -> None (remove), digits -> int, else a clear error."""
    if raw is None or raw == "default":
        return None
    try:
        days = int(raw)
    except ValueError:
        raise ConfigError(f"fetch_every must be a whole number of days between 1 and "
                          f"{source_base.MAX_FETCH_EVERY} or 'default', got '{raw}'") from None
    problem = source_base.optional_fetch_every(days)
    if problem:
        raise ConfigError(f"{problem}, got '{raw}'")
    return days


def _pin(source: dict, raw) -> None:
    """Set a new source's fetch_every; 'default' (or nothing) leaves the key out."""
    days = _fetch_every(raw)
    if days is not None:
        source["fetch_every"] = days


def fallback_emit(cfg) -> str:
    with _connect(cfg) as conn:
        return fallback_stage.emit(conn, cfg.topic)


def backfill(cfg, source: str | None, days: int, max_items: int) -> dict:
    """Research spike: load older entries (up to `days` back, `max_items` in all) once."""
    if not 1 <= days <= settings.MAX_HORIZON_DAYS:
        raise ConfigError(f"--days must be between 1 and {settings.MAX_HORIZON_DAYS}")
    if not 1 <= max_items <= 1000:
        raise ConfigError("--max must be between 1 and 1000")
    sources = cfg.topic.sources
    if source is not None:
        sources = [s for s in sources if s["name"] == source]
        if not sources:
            raise ConfigError(f"no source named '{source}' in topic '{cfg.topic.id}'")
    plan = source_base.Backfill(days=days, max_items=max_items,
                                per_source=fetch_stage.backfill_plan(sources, max_items))
    narrowed = dataclasses.replace(cfg, topic=dataclasses.replace(cfg.topic, sources=sources))
    with _connect(cfg) as conn:
        return fetch_stage.run(conn, narrowed, backfill=plan)


def sources_doctor(cfg) -> dict:
    """Probe each source of the active topic (the list fetch reads), with its stored state."""
    rows = fetch_stage.doctor(cfg.topic.sources)
    with _connect(cfg) as conn:
        states = {s["name"]: s for s in store.all_source_states(conn)}
    for row in rows:
        st = states.get(row["name"]) or {}
        row.update({k: st.get(k) for k in _STATE_KEYS})
    return {"topic": cfg.topic.id, "sources": rows}


def emit(cfg, stage: str) -> str:
    mod = _pick(stage, EMIT_STAGES)
    if stage == "relate" and cfg.relate == "off":
        return interfaces.envelope("relate", RELATE_OFF, [], cfg.topic)
    if mod is primary_stage and cfg.primary_check != "on":
        with _connect(cfg) as conn:
            primary_stage.restore_flags(conn)   # a verify reset drops the flag; the check row stays
        return primary_stage.envelope(cfg.topic, [], **PRIMARY_OFF)
    if mod is classify and cfg.classify_batch == 0:
        return json.dumps({**json.loads(interfaces.envelope("classify", classify.OFF, [], cfg.topic)),
                           "skipped": classify.OFF}, ensure_ascii=False, indent=2)
    with _topic_db(cfg) as conn:
        if mod is report:
            return report.emit(conn, cfg.topic, cfg.output_language)
        if mod is classify:
            return classify.emit(conn, cfg.topic, cap=cfg.classify_batch)
        if mod is primary_stage:
            return mod.emit(conn, cfg.topic, primary_stage.default_ctx(fetch_cache.for_config(cfg)))
        return mod.emit(conn, cfg.topic)


def apply(cfg, stage: str, payload: str, ruling: bool = False, payload_b: str | None = None) -> dict:
    """ruling=True: the owner's adjudication answers for verified items (verify only).

    A TWO_ANSWER_STAGES stage needs payload_b (the second blind answer); every other stage
    takes one payload."""
    if ruling and stage != "verify":
        raise ValueError("--ruling applies only to --stage verify")
    if stage in TWO_ANSWER_STAGES:
        if payload_b is None:
            raise ConfigError(f"--stage {stage} needs two results files, one per blind answer: "
                              f"apply --stage {stage} <file_a> <file_b>")
        mod = _pick(stage, LLM_STAGES)
        with _topic_db(cfg) as conn:
            return mod.apply(conn, payload, payload_b)
    if payload_b is not None:
        raise ConfigError(f"--stage {stage} takes one results file, got two")
    if stage == "report":
        return render_report(cfg, payload)
    if stage == "fetch":
        with _connect(cfg) as conn:
            return fallback_stage.apply(conn, cfg.topic, payload)
    mod = _pick(stage, LLM_STAGES)
    with _topic_db(cfg) as conn:
        if ruling:
            return verify.apply(conn, payload, ruling=True)
        return mod.apply(conn, payload)


def run_api(cfg, stage: str) -> dict:
    if stage == "relate" and cfg.relate == "off":
        return {"processed": 0, "rejected": [], "skipped": RELATE_OFF}
    mod = _pick(stage, LLM_STAGES)
    if mod is primary_stage and cfg.primary_check != "on":
        with _connect(cfg) as conn:
            primary_stage.restore_flags(conn)
        return {"processed": 0, "agreed": 0, "undecided": 0, "rejected": [], **PRIMARY_OFF}
    if mod is classify and cfg.classify_batch == 0:
        return {"processed": 0, "rejected": [], "waiting": 0, "skipped": classify.OFF}
    backend = llm_api.AnthropicBackend(cfg.anthropic_api_key)
    with _topic_db(cfg) as conn:
        if mod is classify:
            return classify.run_api(conn, backend, cfg.topic, cap=cfg.classify_batch)
        if mod is primary_stage:
            return mod.run_api(conn, backend, cfg.topic,
                               primary_stage.default_ctx(fetch_cache.for_config(cfg)))
        return mod.run_api(conn, backend, cfg.topic)


def extract(cfg) -> dict:
    with _connect(cfg) as conn:
        return extract_stage.run(conn, cache=fetch_cache.for_config(cfg))


def embed(cfg) -> dict:
    with _connect(cfg) as conn:
        return embed_stage.run(conn, embedders.build(cfg))


def _topic_model(cfg) -> render_model.Topic:
    with _connect(cfg) as conn:
        return render_model.build_topic(conn, cfg.topic, datetime.now(timezone.utc))


def _json_safe(result):
    """A renderer result the CLI can print: a non-JSON value raises here, inside the try."""
    return json.loads(json.dumps(result))


def render_report(cfg, payload: str, rejections: bool = False) -> dict:
    with _connect(cfg) as conn:
        out, rep, previous = report.render_with_model(conn, payload, cfg.reports_dir, cfg.output_language,
                                            cfg.topic, md_dir=cfg.md_dir)
    if out.get("skipped"):   # no new studies: nothing was written, so nothing to rebuild
        return out
    # the report is committed: no failure below may invite a re-render (exit code stays 0)
    topic_model = None
    if cfg.html_reports == "on":
        try:
            topic_model = _topic_model(cfg)
            with _connect(cfg) as conn:
                sources = sources_render.build_sources(conn, cfg.topic, datetime.now(timezone.utc),
                                                       cfg.fetch_horizon_days)
            written = _json_safe(html_render.write_report(rep, topic_model, cfg.reports_dir,
                                                          cfg.overview_dir,
                                                          cfg.reports_dir / Path(out["path"]).name,
                                                          sources_page=sources))
            if written:
                out["html"] = written
            more = _json_safe(html_render.write_overview(_summaries(config.workspace()),
                                                         cfg.overview_dir, datetime.now(timezone.utc)))
            if more:
                out.setdefault("html", {}).update(more)
        except FRONT_END_ERRORS as exc:
            out["html_error"] = str(exc)
    try:   # after the HTML page, so overview.md can link it when the report has no .md
        out["overview"] = overview(config.workspace(), cfg.overview_dir, html=False)["path"]
    except (OSError, sqlite3.Error) as exc:
        out["overview_error"] = str(exc)
    if cfg.obsidian_dir is not None:
        try:
            exported = _json_safe(obsidian_render.export(topic_model or _topic_model(cfg),
                                                         cfg.obsidian_dir))
            if exported:
                out["obsidian"] = exported
        except FRONT_END_ERRORS as exc:
            out["obsidian_error"] = str(exc)
    if rejections or cfg.rejections_report == "on":
        try:
            _write_rejections(cfg, out, previous)
        except FRONT_END_ERRORS as exc:   # FileExistsError included
            out["rejections_error"] = str(exc)
    return out


def _write_rejections(cfg, out: dict, previous: str | None) -> None:
    """The paired rejections report (Markdown, and HTML when html_reports is on) for the window
    since the previous report; sets out["rejections"]. The Markdown sits next to the report's
    Markdown, the HTML next to its HTML page."""
    md = Path(out["path"])
    visible = cfg.reports_dir / md.name
    with _connect(cfg) as conn:
        rows = rejections_render.rows_in_window(store.rejected_items(conn), previous,
                                                datetime.now(timezone.utc))
    name = cfg.topic.name
    target = rejections_render.paired_path(md)
    with open(target, "x", encoding="utf-8") as fh:
        fh.write(rejections_render.markdown(name, previous, rows))
    info = {"path": str(target), "count": len(rows)}
    if cfg.html_reports == "on":
        html_target = rejections_render.paired_path(visible).with_suffix(".html")
        with open(html_target, "x", encoding="utf-8") as fh:
            topic_page = cfg.reports_dir / f"{cfg.topic.id}.html"
            fh.write(rejections_render.html_page(
                name, previous, rows, visible.with_suffix(".html").name,
                html_render.href(cfg.overview_dir / "overview.html", html_target.parent),
                html_render.href(topic_page, html_target.parent)))
        info["html"] = str(html_target)
    out["rejections"] = info


def reset(cfg, stage: str) -> dict:
    if stage == "group":      # unpinned studies are grouped again; owner moves and merges survive
        with _connect(cfg) as conn:
            return {"reset": "group", "studies": store.clear_grouping(conn)}
    if stage == "relate":     # every question is asked again on the next run
        with _connect(cfg) as conn:
            return {"reset": "relate", "questions": store.clear_question_explanations(conn)}
    if stage == "split":      # pending proposals go; decided ones and accepted moves stay
        with _connect(cfg) as conn:
            return {"reset": "split", **store.clear_pending_splits(conn)}
    if stage == "classify":   # a separate path: clears the studies' classified marks, no item state
        with _connect(cfg) as conn:
            return {"reset": "classify", "studies": store.clear_classified(conn)}
    states, target = _pick(stage, RESET_TARGET)
    with _connect(cfg) as conn:
        qmarks = ",".join("?" * len(states))
        if stage == "judge":  # back to extracted: the judge's fields, grading ones too, go
            conn.execute(f"""UPDATE items SET quotes=NULL, quote_check=NULL, capped_from=NULL,
                             judged_from=NULL, provenance=NULL, judge_tier=NULL,
                             subtopic_choice=NULL, metric_code=NULL, direction=NULL, entity_names=NULL,
                             headline=NULL, data_date=NULL, judge_reason=NULL
                             WHERE state IN ({qmarks})""", states)
        conn.execute(f"UPDATE items SET state=? WHERE state IN ({qmarks})",
                     (target, *states))
        if stage == "match":  # back to judged: match assigns the study again
            conn.execute("UPDATE items SET study_id=NULL, first_study_id=NULL, match_reason=NULL "
                         "WHERE state='judged'")
        if stage == "verify":  # back to matched: the skeptic grades again against the judge's tier
            store.clear_blind_grades(conn)
        conn.commit()
    return {"reset_to": target}


def import_legacy(cfg, path) -> dict:
    with _connect(cfg) as conn:
        return legacy.import_cache(conn, Path(path))


def list_templates() -> list[dict]:
    return topics.list_templates(config.TEMPLATES_DIR)


def _last_run(ws, topic_id) -> str | None:
    db = ws.topics_dir / topic_id / "echo.db"
    if not db.exists():
        return None
    with closing(store.connect(db)) as conn:
        row = conn.execute("SELECT MAX(started_at) m FROM runs").fetchone()
        return row["m"]


def _require_http(url: str) -> None:
    if not url.lower().startswith(("http://", "https://")):
        raise ConfigError(f"source URL must start with http:// or https://: {url}")


def topic_list(ws) -> list[dict]:
    out = []
    for tid in topics.topic_ids(ws.topics_dir):
        try:  # one broken topic.json or echo.db must not hide the others
            t = topics.load_topic(ws.topics_dir, tid)
        except ConfigError as exc:
            out.append({"id": tid, "name": tid, "sources": 0, "last_run": None, "error": str(exc)})
            continue
        try:
            last_run = _last_run(ws, tid)
        except sqlite3.Error as exc:
            out.append({"id": tid, "name": t.name, "sources": len(t.sources), "last_run": None,
                        "error": f"{ws.topics_dir / tid / 'echo.db'}: {exc}"})
            continue
        out.append({"id": t.id, "name": t.name, "sources": len(t.sources), "last_run": last_run})
    return out


def _states(db: Path) -> dict:
    if not db.exists():
        return {}
    with closing(store.connect(db)) as conn:
        return {r["state"]: r["c"] for r in conn.execute(
            "SELECT state, COUNT(*) c FROM items GROUP BY state")}


def status_all(ws) -> dict:
    out = []
    for tid in topics.topic_ids(ws.topics_dir):
        try:  # one broken topic.json or echo.db must not hide the others
            t = topics.load_topic(ws.topics_dir, tid)
        except ConfigError as exc:
            out.append({"id": tid, "name": tid, "states": {}, "last_run": None, "error": str(exc)})
            continue
        db = ws.topics_dir / tid / "echo.db"
        try:
            out.append({"id": tid, "name": t.name, "states": _states(db),
                        "last_run": _last_run(ws, tid), "error": None})
        except sqlite3.Error as exc:
            out.append({"id": tid, "name": t.name, "states": {}, "last_run": None,
                        "error": f"{db}: {exc}"})
    return {"data_dir": str(ws.data_dir), "topics": out}


def _summaries(ws) -> list:
    out = []
    depth = config.workspace_setting("overview_studies", ws)
    for tid in topics.topic_ids(ws.topics_dir):
        try:
            t = topics.load_topic(ws.topics_dir, tid)
        except ConfigError as exc:
            out.append(overview_mod.TopicSummary(tid, tid, error=str(exc)))
            continue
        db = ws.topics_dir / tid / "echo.db"
        report_row = recent = None
        if db.exists():
            try:
                with closing(store.connect(db)) as conn:
                    report_row = store.latest_report(conn)
                    recent = store.recent_studies(conn, depth) if report_row else None
            except sqlite3.Error as exc:
                out.append(overview_mod.TopicSummary(t.id, t.name, error=f"{db}: {exc}"))
                continue
        out.append(overview_mod.TopicSummary(t.id, t.name, report=report_row, recent=recent))
    return out


def _overview_dir(ws) -> Path:
    env = os.environ.get("EV_ECHO_REPORTS_DIR", "").strip()
    raw = env or (ws.settings or {}).get("reports_dir")
    return settings.expand_path(raw, "reports_dir") if raw else ws.data_dir / "reports"


def overview(ws, overview_dir: Path | None = None, html: bool | None = None) -> dict:
    """overview.md, and overview.html when html (None: the workspace's html_reports) is on."""
    out_dir = overview_dir or _overview_dir(ws)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_dir = config.markdown_dir(ws, out_dir, config.workspace_setting("html_reports", ws))
    if md_dir != out_dir:   # md_reports off: no overview.md in the reports folder
        md_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "overview.md").unlink(missing_ok=True)
    path = md_dir / "overview.md"
    summaries, now = _summaries(ws), datetime.now(timezone.utc)
    path.write_text(overview_mod.render(summaries, md_dir, now), encoding="utf-8")
    out = {"path": str(path)}
    if html is None:
        html = config.workspace_setting("html_reports", ws) == "on"
    if html:
        try:
            written = html_render.write_overview(summaries, out_dir, now)
            if written:
                out["html"] = written
        except FRONT_END_ERRORS as exc:
            out["html_error"] = str(exc)
    return out


def topic_add(ws, topic_id, template=None, name=None, domain=None, scope=None) -> dict:
    if template:
        t = topics.create_from_template(ws.topics_dir, topic_id, template, config.TEMPLATES_DIR)
    else:
        t = topics.create_blank(ws.topics_dir, topic_id, name, domain, scope)
    return topics.to_dict(t)


def topic_show(ws, topic_id) -> dict:
    return topics.to_dict(topics.load_topic(ws.topics_dir, topic_id))


def topic_set(ws, topic_id, key, value) -> dict:
    return topics.to_dict(topics.set_field(ws.topics_dir, topic_id, key, value))


def topic_remove(ws, topic_id, delete_data=False) -> dict:
    return topics.remove(ws.topics_dir, topic_id, delete_data)


_STATE_KEYS = ("last_checked", "last_new_item", "consecutive_errors", "last_error", "done",
               "backfilled_at")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or url).removeprefix("www.")


def _default_name(preferred: str, url: str, taken: set) -> str:
    """The title or host; then host + first path segment; then a numbered suffix."""
    seg = next((p for p in urlsplit(url).path.split("/") if p), "")
    candidates = [preferred] + ([f"{_host(url)}/{seg}"] if seg else [])
    for candidate in candidates:
        if candidate not in taken:
            return candidate
    n = 2
    while f"{candidates[-1]} ({n})" in taken:
        n += 1
    return f"{candidates[-1]} ({n})"


def detect_kind(url: str) -> dict:
    _require_http(url)
    found = rss_kind.discover_feed(url)
    if found["check"]["status"] == "ok":
        _require_http(found["url"])
        return {"kind": "rss", "url": found["url"], "discovered_from": found["discovered_from"],
                "check": found["check"], "sample_links": [], "title": None}
    probe = page_kind.check({"name": url, "url": url})
    if probe.get("article_links", 0) >= page_kind.MIN_ARTICLE_LINKS:
        return {"kind": "page", "url": url, "discovered_from": None, "check": probe,
                "sample_links": probe.get("sample_links", []), "title": None}
    doc = once_kind.check({"name": url, "url": url})
    return {"kind": "once", "url": url, "discovered_from": None, "check": doc,
            "sample_links": [], "title": doc.get("title")}


_LINKEDIN_SLUG = re.compile(r"[A-Za-z0-9_%-]+")


def linkedin_slug(target: str) -> str:
    """A profile slug, given bare or as a linkedin.com/in/<slug> URL."""
    raw = (target or "").strip()
    if _LINKEDIN_SLUG.fullmatch(raw):
        return raw
    parts = urlsplit(raw if "://" in raw else "https://" + raw)
    host = (parts.hostname or "").lower()
    path = [p for p in parts.path.split("/") if p]
    if (parts.scheme in ("http", "https") and (host == "linkedin.com" or host.endswith(".linkedin.com"))
            and len(path) == 2 and path[0] == "in" and _LINKEDIN_SLUG.fullmatch(path[1])):
        return path[1]
    raise ConfigError(f"a LinkedIn source needs a profile slug or a linkedin.com/in/<slug> URL, "
                      f"got '{target}'")


def _add_source(ws, topic_id, source: dict) -> None:
    topics.add_source(ws.topics_dir, topic_id, source)
    db = ws.topics_dir / topic_id / "echo.db"
    if db.exists():  # a removed source's state row is history; a new source starts fresh
        with closing(store.connect(db)) as conn:
            store.forget_source(conn, source["name"])


def sources_add(ws, topic_id, target, kind=None, name=None, scan_depth=None,
                fetch_every=None, site_url=None) -> dict:
    if site_url is not None:
        problem = source_base.optional_site_url(site_url)
        if problem:
            raise ConfigError(f"{problem}, got '{site_url}'")
    if kind == "linkedin":
        slug = linkedin_slug(target)
        source = {"type": "linkedin", "name": name or slug, "slug": slug}
        if site_url:
            source["site_url"] = site_url
        if scan_depth:
            source["scan_depth"] = scan_depth
        _pin(source, fetch_every)
        _add_source(ws, topic_id, source)
        return {"added": source, "kind": "linkedin", "detected": False, "discovered_from": None,
                "check": {"status": "skipped"}, "sample_links": []}
    _require_http(target)
    if kind:
        get_kind(kind)  # unknown kind -> ConfigError
        url = rss_kind.discover_feed(target)["url"] if kind == "rss" else target
        _require_http(url)
        check = get_kind(kind).check({"name": name or url, "url": url})
        info = {"kind": kind, "url": url, "discovered_from": None if url == target else target,
                "check": check, "sample_links": check.get("sample_links", []),
                "title": check.get("title")}
        detected = False
    else:
        info, detected = detect_kind(target), True
    if not name:
        taken = {s["name"] for s in topics.load_topic(ws.topics_dir, topic_id).sources}
        name = _default_name(info["title"] or _host(info["url"]), info["url"], taken)
    source = {"type": info["kind"], "name": name, "url": info["url"]}
    site = site_url or info["discovered_from"]
    if site:
        source["site_url"] = site
    if scan_depth:
        source["scan_depth"] = scan_depth
    _pin(source, fetch_every)
    _add_source(ws, topic_id, source)
    out = {"added": source, "kind": info["kind"], "detected": detected,
           "discovered_from": info["discovered_from"], "check": info["check"],
           "sample_links": info["sample_links"]}
    if info["check"].get("status") not in ("ok", "skipped"):
        out["warning"] = (f"the check reported '{info['check'].get('status')}'; "
                          "the source was added anyway")
    return out


def sources_add_legacy(ws, topic_id, kind, name, target, fetch_every=None) -> dict:
    if kind == "linkedin":
        source = {"type": "linkedin", "name": name, "slug": linkedin_slug(target)}
        _pin(source, fetch_every)
        _add_source(ws, topic_id, source)
        return {"source": source, "check": {"status": "skipped"}}
    _require_http(target)  # feedparser would read a local path or file: URL
    found = rss_kind.discover_feed(target)
    _require_http(found["url"])
    source = {"type": "rss", "name": name, "url": found["url"]}
    _pin(source, fetch_every)
    _add_source(ws, topic_id, source)
    out = {"source": source, "discovered_from": found["discovered_from"], "check": found["check"]}
    if found["check"]["status"] != "ok":
        out["warning"] = (f"the feed check failed ({found['check']['status']}); "
                          "it was added anyway")
    return out


def sources_set(ws, topic_id, name, scan_depth=None, fetch_every=None, site_url=None) -> dict:
    if scan_depth is None and fetch_every is None and site_url is None:
        raise ConfigError("sources set needs --scan-depth, --fetch-every or --site-url")
    days = _fetch_every(fetch_every)  # validate all before writing any
    if site_url is not None and site_url != "default":
        problem = source_base.optional_site_url(site_url)
        if problem:
            raise ConfigError(f"{problem}, got '{site_url}'")
    if site_url is not None:
        topics.set_source_option(ws.topics_dir, topic_id, name, "site_url",
                                 None if site_url == "default" else site_url)
    if scan_depth is not None:
        value = None if scan_depth == "default" else scan_depth
        topics.set_source_option(ws.topics_dir, topic_id, name, "scan_depth", value)
    if fetch_every is not None:
        topics.set_source_option(ws.topics_dir, topic_id, name, "fetch_every", days)
    topic = topics.load_topic(ws.topics_dir, topic_id)
    return next(s for s in topic.sources if s["name"] == name)


def sources_reset(ws, topic_id, name) -> dict:
    topic = topics.load_topic(ws.topics_dir, topic_id)
    src = next((s for s in topic.sources if s["name"] == name), None)
    if src is None:
        raise ConfigError(f"no source named '{name}' in topic '{topic_id}'")
    db = ws.topics_dir / topic_id / "echo.db"
    with closing(store.connect(db)) as conn:
        if src["type"] == "once":
            store.set_source_fields(conn, name, "once", done=0)
            cleared = "done"
        elif src["type"] == "page":
            store.set_source_fields(conn, name, "page", cursor={})
            cleared = "pattern"
        else:
            store.set_source_fields(conn, name, src["type"], consecutive_errors=0,
                                    failing_since=None, last_error=None)
            cleared = "errors"
    return {"reset": name, "kind": src["type"], "cleared": cleared}


def _source_row(source: dict, state: dict | None, depth_setting: str,
                dates: list | None = None, horizon_days: int = 7,
                record: dict | None = None) -> dict:
    kind = get_kind(source["type"])
    depth = "full" if not kind.HAS_HEADLINES else source.get("scan_depth") or depth_setting
    st = state or {}
    days, origin, capped = (schedule.interval(source, dates, schedule.interval_cap(source["type"], horizon_days))
                            if dates is not None and source["type"] != "once"  # once is never skipped
                            else (None, None, False))
    due = schedule.next_due(state, days)
    row = {**source, "effective_scan_depth": depth, **{k: st.get(k) for k in _STATE_KEYS},
           "interval_days": round(days, 1) if days is not None else None, "interval_source": origin,
           "next_due": due.isoformat() if due else None}
    row.update(record or dict.fromkeys(store.TRACK_KEYS, 0))
    if capped:
        row["capped"] = True
    return row


def sources_list(ws, topic_id) -> list[dict]:
    topic = topics.load_topic(ws.topics_dir, topic_id)
    db = ws.topics_dir / topic_id / "echo.db"
    adaptive = config._setting("adaptive_fetch", ws, topic) == "on"
    states, dates, records = {}, {}, {}
    if db.exists():
        with closing(store.connect(db)) as conn:
            states = {s["name"]: s for s in store.all_source_states(conn)}
            records = store.source_track_records(conn)
            if adaptive:
                dates = {s["name"]: fetch_stage.recent_dates(conn, s["name"]) for s in topic.sources}
    depth_setting = config._setting("scan_depth", ws, topic)  # env > topic > config.json
    horizon = config._setting("fetch_horizon_days", ws, topic)
    return [_source_row(s, states.get(s["name"]), depth_setting,
                        (dates.get(s["name"], []) if adaptive else None), horizon,
                        records.get(s["name"])) for s in topic.sources]


def sources_remove(ws, topic_id, name) -> dict:
    topics.remove_source(ws.topics_dir, topic_id, name)
    return {"removed": name}


def config_show(ws) -> dict:
    out = {}
    for key, default in settings.GLOBAL_DEFAULTS.items():
        env = os.environ.get("EV_ECHO_" + key.upper(), "").strip()
        if env and env != "auto":
            out[key] = {"value": settings.check_value(key, env, "EV_ECHO_" + key.upper()),
                        "origin": "env"}
        elif (ws.settings or {}).get(key) is not None:
            out[key] = {"value": ws.settings[key], "origin": "config"}
        else:
            out[key] = {"value": default, "origin": "default"}
    keys = config._read_keys(ws.plugin_data_dir)
    present = {n: bool(config._key(n, keys)) for n in config.KEY_NAMES}
    return {"settings": out, "data_dir": str(ws.data_dir), "plugin_data_dir": str(ws.plugin_data_dir),
            "keys": {"anthropic": present["ANTHROPIC_API_KEY"], "voyage": present["VOYAGE_API_KEY"],
                     "apify": present["APIFY_API_TOKEN"]}}


def config_set(ws, key, value) -> dict:
    if key not in settings.GLOBAL_DEFAULTS:
        raise ConfigError(f"unknown setting '{key}'. Settable: {', '.join(settings.GLOBAL_DEFAULTS)}")
    checked = settings.check_value(key, value, "config set")
    if key == "reports_dir":
        settings.expand_path(checked, "reports_dir").mkdir(parents=True, exist_ok=True)
    if key == "obsidian_dir":
        if not checked.strip():
            checked = None                    # empty: no export
        elif not settings.expand_path(checked, "obsidian_dir").is_dir():
            raise ConfigError(f"obsidian_dir: '{checked}' is not an existing folder "
                              "(use your Obsidian vault's folder; Evidence Echo never creates one)")
    values = {**(ws.settings or {}), key: checked}
    settings.save_global(ws.data_dir, values)
    return {"set": key, "value": checked}


def setup_status(ws, config_error=None) -> dict:
    state = settings.read_state(ws.data_dir)
    return {"configured": ws.configured, "config_error": config_error,
            "data_dir": str(ws.data_dir),
            "reports_dir": (ws.settings or {}).get("reports_dir"),
            "topics": topic_list(ws) if not config_error else [],
            "templates": list_templates(),
            "hint_due": not ws.configured and not state.get("hint_shown", False)}


def _template_update(cfg) -> dict | None:
    """The template's subtopic list when the topic's differs and the owner has not declined it."""
    found = topics.template_subtopics(cfg.topic.template, config.TEMPLATES_DIR)
    if found is None:
        return None
    names, renames = found
    if ([store._key(n) for n in cfg.topic.subtopics] == [store._key(n) for n in names]
            or cfg.topic.template_subtopics_declined == topics.subtopic_list_hash(names, renames)):
        return None
    with _topic_db(cfg) as conn:
        plan = store.template_plan(conn, names, renames)
    return {"template": cfg.topic.template, "count": len(names), **plan}


def subtopics_list(cfg) -> dict:
    with _topic_db(cfg) as conn:
        out = store.subtopics_overview(conn)
    update = _template_update(cfg)
    if update:
        out["template_update"] = update
    return out


def subtopics_accept(cfg, slug: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.accept_subtopic(conn, slug)


def subtopics_map(cfg, slug: str, target: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.map_subtopic(conn, slug, target)


def subtopics_reject(cfg, slug: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.reject_subtopic(conn, slug)


def subtopics_rename(cfg, slug: str, name: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.rename_subtopic(conn, slug, name)


def subtopics_merge(cfg, slug: str, into: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.merge_subtopics(conn, slug, into)


def subtopics_template(cfg, adopt: bool) -> dict:
    found = topics.template_subtopics(cfg.topic.template, config.TEMPLATES_DIR)
    if found is None:
        raise ConfigError(f"topic '{cfg.topic.id}' has no template to switch to")
    names, renames = found
    if not adopt:
        digest = topics.subtopic_list_hash(names, renames)
        topics.update_topic(cfg.topic_dir.parent, cfg.topic.id, template_subtopics_declined=digest)
        return {"declined": cfg.topic.template, "hash": digest}
    with _topic_db(cfg) as conn:
        out = store.adopt_template_subtopics(conn, names, renames)
    topics.update_topic(cfg.topic_dir.parent, cfg.topic.id, subtopics=names, template_subtopics_declined=None)
    return out


def metrics_list(cfg) -> dict:
    with _topic_db(cfg) as conn:
        return store.metrics_overview(conn)


def metrics_accept(cfg, slug: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.accept_metric(conn, slug)


def metrics_reject(cfg, slug: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.reject_metric(conn, slug)


def metrics_rename(cfg, slug: str, name: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.rename_metric(conn, slug, name)


def metrics_merge(cfg, slug: str, into: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.merge_metrics(conn, slug, into)


def entities_list(cfg) -> dict:
    with _topic_db(cfg) as conn:
        return store.entities_overview(conn)


def entities_rename(cfg, slug: str, name: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.rename_entity(conn, slug, name)


def entities_merge(cfg, slug: str, into: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.merge_entities(conn, slug, into)


def entities_alias(cfg, slug: str, name: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.alias_entity(conn, slug, name)


def entities_reject(cfg, slug: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.reject_entity(conn, slug)


def entities_restore(cfg, slug: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.restore_entity(conn, slug)


def split_list(cfg) -> dict:
    with _topic_db(cfg) as conn:
        return store.split_overview(conn)


def split_accept(cfg, group_id: int) -> dict:
    """Accept one proposed part. A new name joins topic.json right after its source (metrics: before
    Other), so seeding keeps it next to the source."""
    with _topic_db(cfg) as conn:
        out = store.accept_split_group(conn, group_id)
    if out["mode"] == "new":
        topic_metric_insert(cfg, out["kind"], out["name"],
                            after=out["source_name"] if out["kind"] == "subtopic" else None,
                            after_slug=out["source_slug"] if out["kind"] == "subtopic" else None)
        if out["kind"] == "subtopic":
            _keep_own_subtopics(cfg)
    return out


def _keep_own_subtopics(cfg) -> None:
    """A split decision made topic.json's subtopic list the owner's own: the template's current list
    counts as declined, so the template-switch question (which would undo the split) is not raised
    until the template's list changes."""
    found = topics.template_subtopics(cfg.topic.template, config.TEMPLATES_DIR)
    if found is not None:
        topics.update_topic(cfg.topic_dir.parent, cfg.topic.id,
                            template_subtopics_declined=topics.subtopic_list_hash(*found))


def split_reject(cfg, group_id: int) -> dict:
    with _topic_db(cfg) as conn:
        return store.reject_split_group(conn, group_id)


def split_rename(cfg, group_id: int, name: str) -> dict:
    with _topic_db(cfg) as conn:
        return store.rename_split_group(conn, group_id, name)


def split_source(cfg, proposal_id: int, choice: str) -> dict:
    with _topic_db(cfg) as conn:
        out = store.set_split_source(conn, proposal_id, choice)
    if choice == "retire":
        topic_list_remove(cfg, "subtopic", out["source_name"], out["source"])
        _keep_own_subtopics(cfg)
    return out


def studies_metric(cfg, sid, slug: str) -> dict:
    study_id = _owner_id(sid, "s", "the study id")
    with _topic_db(cfg) as conn:
        return store.set_study_metric(conn, study_id, slug)


def studies_entity_add(cfg, sid, name: str) -> dict:
    study_id = _owner_id(sid, "s", "the study id")
    with _topic_db(cfg) as conn:
        return store.add_study_entity(conn, study_id, name)


def studies_entity_remove(cfg, sid, slug: str) -> dict:
    study_id = _owner_id(sid, "s", "the study id")
    with _topic_db(cfg) as conn:
        return store.remove_study_entity(conn, study_id, slug)


_LIST_KINDS = {"subtopic": "subtopics", "metric": "metrics"}


def _list_kind(kind: str) -> str:
    if kind not in _LIST_KINDS:
        raise ValueError(f"unknown list kind {kind!r}: subtopic or metric")
    return _LIST_KINDS[kind]


def _list_index(names: list, name, slug: str | None = None) -> int | None:
    """Where `name` is listed; with `slug`, also the entry with that slug (topic.json keeps the listed
    name after an owner rename, while the slug never changes)."""
    key = store._key(name)
    return next((i for i, n in enumerate(names)
                 if store._key(n) == key or (slug and store.subtopic_slug(n) == slug)), None)


def _fresh_topic(cfg):
    """topic.json as it is now (an earlier accept in the same session may have changed the lists)."""
    return topics.load_topic(cfg.topic_dir.parent, cfg.topic.id)


def topic_metric_insert(cfg, kind: str, name: str, after: str | None, after_slug: str | None = None) -> None:
    """Put `name` into the topic.json list of subtopics or metrics right after the entry `after`;
    appended when `after` is absent (before a trailing Other). Metrics, and parts of the Other subtopic,
    go before Other. The list is written in full, from the topic's effective list when the key is absent."""
    field = _list_kind(kind)
    names = list(getattr(_fresh_topic(cfg), field))
    if _list_index(names, name) is not None:
        return
    at = None if after is None else _list_index(names, after, after_slug)
    other = _list_index(names, "Other")
    if kind == "metric" or at is None or at == other:
        at = len(names) if other is None else other
    else:
        at += 1
    names.insert(at, name)
    topics.update_topic(cfg.topic_dir.parent, cfg.topic.id, **{field: names})


def topic_list_remove(cfg, kind: str, name: str, slug: str | None = None) -> None:
    """Take `name` (or the entry with `slug`) out of the topic.json list of subtopics or metrics
    (nothing when it is not there)."""
    field = _list_kind(kind)
    names = list(getattr(_fresh_topic(cfg), field))
    at = _list_index(names, name, slug)
    if at is None:
        return
    del names[at]
    topics.update_topic(cfg.topic_dir.parent, cfg.topic.id, **{field: names})


def studies_list(cfg, include_merged: bool = False) -> list[dict]:
    with _connect(cfg) as conn:
        return store.studies_overview(conn, include_merged)


def studies_show(cfg, study_id: int) -> dict:
    with _connect(cfg) as conn:
        study = store.get_study(conn, study_id)
        if study is None:
            raise ConfigError(f"no study {study_id}")
        items = [{"id": r["id"], "url": r["url"], "source": r["source_name"], "tier": r["evidence_tier"],
                  "match_reason": r["match_reason"]}
                 for r in conn.execute("SELECT * FROM items WHERE study_id=? ORDER BY id", (study_id,))]
        editions, fingerprint = store.study_lists(study)
        resolves_to = store.resolve_study(conn, study_id) if study["merged_into"] is not None else None
        return {**study, "editions": editions, "fingerprint": fingerprint, "items": items,
                "resolves_to": resolves_to}


def studies_duplicates(cfg, count_only: bool = False):
    with _connect(cfg) as conn:
        pairs = store.duplicate_pairs(conn)
    if count_only:
        return {"count": len(pairs)}
    keep = ("id", "canonical_name", "findings", "research_timeframe", "evidence_tier",
            "editions", "series_name", "primary_url")
    return {"pairs": [{"keep": p["keep"], "other": p["other"], "rule": p["rule"],
                       "keep_study": {k: p["keep_study"][k] for k in keep},
                       "other_study": {k: p["other_study"][k] for k in keep}} for p in pairs]}


def _owner_id(value, prefix: str, what: str) -> int:
    """'q12' or '12' (prefix q) / 's7' or '7' (prefix s) -> the number; anything else is a usage error."""
    text = str(value).strip()
    if text[:1].lower() == prefix:
        text = text[1:]
    if not text.isdigit():
        raise ConfigError(f"{what} must look like {prefix}12 or 12, got {value!r}")
    return int(text)


def _owner_call(fn, *args, **kwargs):
    """A store refusal (ValueError with an owner-readable message) becomes a usage error: exit 2."""
    try:
        return fn(*args, **kwargs)
    except ConfigError:
        raise
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def questions_list(cfg, subtopic: str | None = None) -> dict:
    """Every question with its study count, independent groups and label (merged ones show 0 studies)."""
    with _topic_db(cfg) as conn:
        if subtopic is not None and store.get_subtopic(conn, subtopic) is None:
            raise ConfigError(f"no subtopic '{subtopic}' (ev subtopics list shows them)")
        groups = {g.question_id: g for sub in knowledge.evidence(conn) for g in sub.questions}
        placed, _ = knowledge.placed_studies(conn)
        alone = {question["id"] for _, _, question in placed if question is not None} - set(groups)
        out = []
        for question in store.list_questions(conn):
            resolved = store.resolve_subtopic(conn, question["subtopic_id"])
            slug = resolved["slug"] if resolved else None
            if subtopic is not None and slug != subtopic:
                continue
            group = groups.get(question["id"])
            studies = len(group.rows) if group else (1 if question["id"] in alone else 0)
            out.append({"id": f"q{question['id']}", "subtopic": slug, "text": question["text"],
                        "measure": question["measure"], "kind": question["kind"],
                        "comparison": question["comparison"], "studies": studies,
                        "independent": group.independent if group else studies,
                        "label": group.label if group else None,
                        "merged_into": f"q{question['merged_into']}" if question["merged_into"] else None,
                        "owner_touched": bool(question["owner_touched"])})
        return {"questions": out}


def questions_rename(cfg, qid, text: str, measure: str | None = None, kind: str | None = None,
                     comparison: str | None = None) -> dict:
    number = _owner_id(qid, "q", "the question id")
    with _topic_db(cfg) as conn:
        return _owner_call(store.rename_question, conn, number, text, measure, kind, comparison)


def questions_merge(cfg, qid, into) -> dict:
    a, b = _owner_id(qid, "q", "the question id"), _owner_id(into, "q", "the target question id")
    with _topic_db(cfg) as conn:
        return _owner_call(store.merge_questions, conn, a, b)


def questions_move(cfg, sid, qid=None, new: str | None = None, measure: str | None = None,
                   kind: str | None = None, comparison: str | None = None) -> dict:
    """Place a study in a question of any subtopic (its subtopic follows), or in a new question of
    its own subtopic (--new TEXT --measure M --kind K [--comparison C])."""
    study_id = _owner_id(sid, "s", "the study id")
    if (qid is None) == (new is None):
        raise ConfigError("give either a question id or --new TEXT --measure MEASURE")
    if new is None and (measure is not None or kind is not None or comparison is not None):
        raise ConfigError("--measure, --kind and --comparison go with --new")
    if new is not None and measure is None:
        raise ConfigError("--new needs --measure")
    if new is not None and kind is None:
        raise ConfigError("--new needs --kind (difference, level or capability)")
    if new is not None and kind == "difference" and not comparison:
        raise ConfigError("a difference question needs --comparison")
    if new is not None and kind != "difference" and comparison:
        raise ConfigError(f"a {kind} question has no --comparison")
    number = None if qid is None else _owner_id(qid, "q", "the question id")
    with _topic_db(cfg) as conn:
        created = None
        if number is None:
            study = store.get_study(conn, study_id)
            subtopic = store.resolve_subtopic(conn, study["subtopic_id"]) if study and study["subtopic_id"] else None
            if study is None:
                raise ConfigError(f"no study s{study_id}")
            if subtopic is None:
                raise ConfigError(f"s{study_id} has no subtopic yet, so it cannot have a question")
            number = created = _owner_call(store.create_question, conn, subtopic["id"], new, measure, kind,
                                    comparison)
        try:
            out = _owner_call(store.move_study, conn, study_id, number)
        except ConfigError:
            if created is not None:      # a refused move leaves no empty question behind
                conn.execute("DELETE FROM questions WHERE id=?", (created,))
                conn.commit()
            raise
        return {**out, "created": f"q{created}"} if created is not None else out


def studies_merge(cfg, keep: int, other: int) -> dict:
    with _connect(cfg) as conn:
        return store.merge_studies(conn, keep, other)


def studies_unmerge(cfg, other: int) -> dict:
    with _connect(cfg) as conn:
        return store.unmerge_study(conn, other)


OBSIDIAN_NOT_SET = ("obsidian_dir is not set — run: ev config set obsidian_dir <vault folder> "
                    "(or set EV_ECHO_OBSIDIAN_DIR)")


def _obsidian_vault(cfg) -> Path:
    if cfg.obsidian_dir is None:
        raise ConfigError(OBSIDIAN_NOT_SET)
    if not cfg.obsidian_dir.is_dir():
        raise FileNotFoundError(f"obsidian_dir {cfg.obsidian_dir} is not an existing folder; "
                                "Evidence Echo does not create vaults")
    return cfg.obsidian_dir


def export_obsidian(ws, topic_id: str | None = None) -> dict:
    """ev export obsidian: the chosen topic (--topic, else EV_ECHO_TOPIC) or every topic.

    With every topic, one broken topic (topic.json, echo.db) becomes an error row and the others
    are still exported; a topic that never ran is skipped (no database is created)."""
    if not ws.configured:
        raise config.SetupRequired(config.NOT_SET_UP)
    chosen = topic_id or os.environ.get("EV_ECHO_TOPIC", "").strip() or None
    ids = [chosen] if chosen else topics.topic_ids(ws.topics_dir)
    if not ids:
        raise config.SetupRequired(config.NO_TOPICS)
    vault, rows = None, []
    for tid in ids:
        try:
            cfg = config.load(tid)
        except ConfigError as exc:
            if chosen:
                raise
            rows.append({"topic": tid, "error": str(exc)})
            continue
        vault = _obsidian_vault(cfg)  # a global setting: not set or gone fails the whole command
        if not cfg.db_path.exists():
            rows.append({"topic": tid, "skipped": "no data yet"})
            continue
        try:
            with _connect(cfg) as conn:
                model_topic = render_model.build_topic(conn, cfg.topic, datetime.now(timezone.utc))
            rows.append({"topic": tid, **obsidian_render.export(model_topic, vault)})
        except (OSError, sqlite3.Error, ValueError) as exc:
            if chosen:
                raise
            rows.append({"topic": tid, "error": str(exc)})
    return {"obsidian_dir": str(vault) if vault else None, "topics": rows}


# --- wave 4a plan B: primary reset ---
def reset_primary(cfg, url: str | None = None) -> dict:
    """ev reset --stage primary [--url URL]: failed and unreachable originals are fetched again
    on the next run (all, or the one URL)."""
    canon = None
    if url is not None:
        canon = canonical_url(url)
        if not canon:
            raise ConfigError(f"--url must be an http(s) URL, got '{url}'")
    with _connect(cfg) as conn:
        counts = store.reset_primary_sources(conn, canon)
    return {"reset": "primary", **({"url": canon} if canon else {}), **counts}
