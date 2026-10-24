"""CLI: argparse -> config -> api.* -> JSON on stdout.

Exit codes: 1 rejected results file / unreadable or unwritable file or database; 2 usage,
topic selection or invalid configuration; 4 not set up (run the setup skill).
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from evidence_echo import api
from evidence_echo.core import config, contracts
from evidence_echo.core.settings import ConfigError


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evidence-echo", description="Evidence Echo pipeline")
    p.add_argument("--topic", metavar="ID", help="topic to work on (needed with several topics)")
    sub = p.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fetch")
    fp.add_argument("--all", dest="fetch_all", action="store_true")
    fp.add_argument("--fresh", action="store_true")
    for name in api.LLM_STAGES:
        sp = sub.add_parser(name)
        sp.add_argument("--emit", action="store_true")
        if name == "split":      # `ev split` is the stage; the actions are the owner's decisions
            sa = sp.add_subparsers(dest="action")
            sa.add_parser("list")
            for action in ("accept", "reject"):
                sa.add_parser(action).add_argument("group", type=int)
            sr = sa.add_parser("rename")
            sr.add_argument("group", type=int); sr.add_argument("name")
            ss = sa.add_parser("source")
            ss.add_argument("proposal", type=int); ss.add_argument("choice", metavar="keep|retire")
    sub.add_parser("extract")
    bf = sub.add_parser("backfill")
    bf.add_argument("--source", metavar="NAME")
    bf.add_argument("--days", type=int, default=365)
    bf.add_argument("--max", dest="max_items", type=int, default=200)
    sub.add_parser("fetch-fallback").add_argument("action", choices=["emit"])
    ap = sub.add_parser("apply")
    ap.add_argument("--stage", required=True, choices=[*api.LLM_STAGES, "report", "fetch"])
    ap.add_argument("--ruling", action="store_true",
                    help="owner adjudication answers for verified items (verify only)")
    ap.add_argument("file")
    ap.add_argument("file_b", nargs="?", metavar="file_b",
                    help="the second blind answer (two-answer stages only)")
    sub.add_parser("embed")
    rp = sub.add_parser("report")
    rp.add_argument("--emit", action="store_true")
    rp.add_argument("--render", metavar="FILE")
    rp.add_argument("--rejections", action="store_true",
                    help="with --render: also write the rejections report")
    sub.add_parser("status")
    sub.add_parser("overview")
    rs = sub.add_parser("reset")
    rs.add_argument("--stage", required=True, choices=[*api.RESET_STAGES, "primary"])
    rs.add_argument("--url", help="with --stage primary: only this original")
    il = sub.add_parser("import-legacy")
    il.add_argument("cache_file")
    sub.add_parser("templates")
    tp = sub.add_parser("topic").add_subparsers(dest="action", required=True)
    tp.add_parser("list")
    ta = tp.add_parser("add")
    ta.add_argument("id")
    how = ta.add_mutually_exclusive_group(required=True)
    how.add_argument("--from", dest="template", metavar="TEMPLATE")
    how.add_argument("--blank", action="store_true")
    ta.add_argument("--name")
    ta.add_argument("--domain")
    ta.add_argument("--scope")
    tp.add_parser("show").add_argument("id")
    ts = tp.add_parser("set")
    ts.add_argument("id"); ts.add_argument("key"); ts.add_argument("value")
    tr = tp.add_parser("remove")
    tr.add_argument("id")
    tr.add_argument("--delete-data", action="store_true")

    sp = sub.add_parser("sources").add_subparsers(dest="action", required=True)
    sp.add_parser("list")
    sp.add_parser("doctor")
    sa = sp.add_parser("add")
    sa.add_argument("first", metavar="URL|rss|linkedin")
    sa.add_argument("rest", nargs="*", metavar="NAME TARGET")
    sa.add_argument("--kind", choices=["rss", "page", "once", "linkedin"])
    sa.add_argument("--name")
    sa.add_argument("--scan-depth", choices=["headlines", "full"])
    sa.add_argument("--fetch-every", metavar="DAYS")
    sa.add_argument("--site-url", metavar="URL")
    ss = sp.add_parser("set")
    ss.add_argument("name")
    ss.add_argument("--scan-depth", choices=["headlines", "full", "default"])
    ss.add_argument("--fetch-every", metavar="DAYS|default")
    ss.add_argument("--site-url", metavar="URL|default")
    sp.add_parser("reset").add_argument("name")
    sp.add_parser("remove").add_argument("name")

    st = sub.add_parser("studies").add_subparsers(dest="action", required=True)
    st.add_parser("list").add_argument("--all", dest="include_merged", action="store_true")
    st.add_parser("show").add_argument("id", type=int)
    st.add_parser("duplicates").add_argument("--count", dest="count_only", action="store_true")
    sm = st.add_parser("merge")
    sm.add_argument("keep", type=int)
    sm.add_argument("other", type=int)
    st.add_parser("unmerge").add_argument("other", type=int)
    sk = st.add_parser("metric")
    sk.add_argument("sid"); sk.add_argument("slug")
    se = st.add_parser("entity")
    se.add_argument("sid"); se.add_argument("entity_op", choices=("add", "remove")); se.add_argument("value")

    en = sub.add_parser("entities").add_subparsers(dest="action", required=True)
    en.add_parser("list")
    en.add_parser("reject").add_argument("slug")
    en.add_parser("restore").add_argument("slug")
    er = en.add_parser("rename")
    er.add_argument("slug"); er.add_argument("name")
    ea = en.add_parser("alias")
    ea.add_argument("slug"); ea.add_argument("name")
    eg = en.add_parser("merge")
    eg.add_argument("slug"); eg.add_argument("into")

    mt = sub.add_parser("metrics").add_subparsers(dest="action", required=True)
    mt.add_parser("list")
    mt.add_parser("accept").add_argument("slug")
    mt.add_parser("reject").add_argument("slug")
    mn = mt.add_parser("rename")
    mn.add_argument("slug"); mn.add_argument("name")
    mg = mt.add_parser("merge")
    mg.add_argument("slug"); mg.add_argument("into")

    th = sub.add_parser("subtopics").add_subparsers(dest="action", required=True)
    th.add_parser("list")
    th.add_parser("accept").add_argument("slug")
    tm = th.add_parser("map")
    tm.add_argument("slug"); tm.add_argument("target")
    th.add_parser("reject").add_argument("slug")
    tt = th.add_parser("template").add_mutually_exclusive_group(required=True)
    tt.add_argument("--adopt", action="store_true")
    tt.add_argument("--decline", action="store_true")
    tn = th.add_parser("rename")
    tn.add_argument("slug"); tn.add_argument("name")
    tg = th.add_parser("merge")
    tg.add_argument("slug"); tg.add_argument("into")

    qs = sub.add_parser("questions").add_subparsers(dest="action", required=True)
    ql = qs.add_parser("list")
    ql.add_argument("--subtopic", metavar="SLUG")
    qr = qs.add_parser("rename")
    qr.add_argument("qid"); qr.add_argument("text")
    qr.add_argument("--measure")
    qr.add_argument("--kind", help="difference, level or capability (clears the studies' directions)")
    qr.add_argument("--comparison", help="difference questions: what A is compared with")
    qg = qs.add_parser("merge")
    qg.add_argument("qid"); qg.add_argument("into")
    qm = qs.add_parser("move")
    qm.add_argument("sid")
    qm.add_argument("qid", nargs="?")
    qm.add_argument("--new", metavar="TEXT", help="put the study in a new question with this text")
    qm.add_argument("--measure", help="with --new: the new question's measure")
    qm.add_argument("--kind", help="with --new: difference, level or capability")
    qm.add_argument("--comparison", help="with --new --kind difference: what A is compared with")

    sub.add_parser("setup").add_argument("action", choices=["status"])
    cp = sub.add_parser("config").add_subparsers(dest="action", required=True)
    cp.add_parser("show")
    cs = cp.add_parser("set")
    cs.add_argument("key"); cs.add_argument("value")
    ex = sub.add_parser("export").add_subparsers(dest="target", required=True)
    ex.add_parser("obsidian").add_argument("--topic", dest="export_topic", metavar="ID",
                                           help="one topic (default: every topic)")
    return p


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _dispatch(args):
    if args.cmd == "templates":  # works before setup
        return api.list_templates()
    if args.cmd == "setup":  # works before setup and with an invalid config.json
        ws, error = config.workspace_lenient()
        return api.setup_status(ws, error)
    if args.cmd == "status":  # all topics; works before setup
        return api.status_all(config.workspace())
    if args.cmd == "overview":
        return api.overview(config.workspace())
    if args.cmd == "config" and args.action == "set":
        # an invalid config.json is replaced: this is the setup skill's repair path
        ws, _ = config.workspace_lenient()
        return api.config_set(ws, args.key, args.value)
    if args.cmd == "config":
        return api.config_show(config.workspace())
    if args.cmd == "topic":
        ws = config.workspace()
        if args.action == "list":
            return api.topic_list(ws)
        if args.action == "add":
            if args.blank and not (args.name and args.domain and args.scope):
                raise ConfigError("--blank needs --name, --domain and --scope")
            return api.topic_add(ws, args.id, args.template, args.name, args.domain, args.scope)
        if args.action == "show":
            return api.topic_show(ws, args.id)
        if args.action == "set":
            return api.topic_set(ws, args.id, args.key, args.value)
        return api.topic_remove(ws, args.id, args.delete_data)
    if args.cmd == "sources" and args.action != "doctor":
        ws = config.workspace()
        tid = config.select_topic(ws, args.topic)
        if args.action == "list":
            return api.sources_list(ws, tid)
        if args.action == "add":
            if args.first in ("rss", "linkedin") and len(args.rest) == 2:
                return api.sources_add_legacy(ws, tid, args.first, *args.rest, args.fetch_every)
            if args.rest:
                raise ConfigError("usage: sources add <url> [--kind K] [--name N]  or  "
                                  "sources add rss|linkedin <name> <url|slug>")
            return api.sources_add(ws, tid, args.first, args.kind, args.name, args.scan_depth,
                                   args.fetch_every, args.site_url)
        if args.action == "set":
            return api.sources_set(ws, tid, args.name, args.scan_depth, args.fetch_every,
                                   args.site_url)
        if args.action == "reset":
            return api.sources_reset(ws, tid, args.name)
        return api.sources_remove(ws, tid, args.name)
    if args.cmd == "export":  # "obsidian" is the only target; it needs setup (exit 4) and
        # picks its topics itself: --topic (either position), EV_ECHO_TOPIC, else every topic
        return api.export_obsidian(config.workspace(), args.export_topic or args.topic)
    # everything above works before setup; everything below needs config.json and a topic (exit 4)
    cfg = config.load(args.topic)
    if args.cmd == "studies":
        if args.action == "list":
            return api.studies_list(cfg, args.include_merged)
        if args.action == "show":
            return api.studies_show(cfg, args.id)
        if args.action == "duplicates":
            return api.studies_duplicates(cfg, args.count_only)
        if args.action == "merge":
            return api.studies_merge(cfg, args.keep, args.other)
        if args.action == "metric":
            return api.studies_metric(cfg, args.sid, args.slug)
        if args.action == "entity":
            if args.entity_op == "add":
                return api.studies_entity_add(cfg, args.sid, args.value)
            return api.studies_entity_remove(cfg, args.sid, args.value)
        return api.studies_unmerge(cfg, args.other)
    if args.cmd == "entities":
        if args.action == "list":
            return api.entities_list(cfg)
        if args.action == "rename":
            return api.entities_rename(cfg, args.slug, args.name)
        if args.action == "alias":
            return api.entities_alias(cfg, args.slug, args.name)
        if args.action == "reject":
            return api.entities_reject(cfg, args.slug)
        if args.action == "restore":
            return api.entities_restore(cfg, args.slug)
        return api.entities_merge(cfg, args.slug, args.into)
    if args.cmd == "metrics":
        if args.action == "list":
            return api.metrics_list(cfg)
        if args.action == "accept":
            return api.metrics_accept(cfg, args.slug)
        if args.action == "reject":
            return api.metrics_reject(cfg, args.slug)
        if args.action == "rename":
            return api.metrics_rename(cfg, args.slug, args.name)
        return api.metrics_merge(cfg, args.slug, args.into)
    if args.cmd == "subtopics":
        if args.action == "list":
            return api.subtopics_list(cfg)
        if args.action == "accept":
            return api.subtopics_accept(cfg, args.slug)
        if args.action == "map":
            return api.subtopics_map(cfg, args.slug, args.target)
        if args.action == "reject":
            return api.subtopics_reject(cfg, args.slug)
        if args.action == "rename":
            return api.subtopics_rename(cfg, args.slug, args.name)
        if args.action == "template":
            return api.subtopics_template(cfg, args.adopt)
        return api.subtopics_merge(cfg, args.slug, args.into)
    if args.cmd == "questions":
        if args.action == "list":
            return api.questions_list(cfg, args.subtopic)
        if args.action == "rename":
            return api.questions_rename(cfg, args.qid, args.text, args.measure, args.kind, args.comparison)
        if args.action == "merge":
            return api.questions_merge(cfg, args.qid, args.into)
        return api.questions_move(cfg, args.sid, args.qid, args.new, args.measure, args.kind,
                                 args.comparison)
    if args.cmd == "fetch":
        return api.fetch(cfg, args.fetch_all, args.fresh)
    if args.cmd == "backfill":
        return api.backfill(cfg, args.source, args.days, args.max_items)
    if args.cmd == "fetch-fallback":
        return api.fallback_emit(cfg)
    if args.cmd == "split" and getattr(args, "action", None):
        if args.action == "list":
            return api.split_list(cfg)
        if args.action == "accept":
            return api.split_accept(cfg, args.group)
        if args.action == "reject":
            return api.split_reject(cfg, args.group)
        if args.action == "rename":
            return api.split_rename(cfg, args.group, args.name)
        return api.split_source(cfg, args.proposal, args.choice)
    if args.cmd in api.LLM_STAGES:
        mode = cfg.triage_backend if args.cmd == "triage" else cfg.judgment_backend
        if args.emit or mode == "session":
            return api.emit(cfg, args.cmd)
        return api.run_api(cfg, args.cmd)
    if args.cmd == "extract":
        return api.extract(cfg)
    if args.cmd == "apply":
        if args.file_b is not None:
            try:
                same = os.path.samefile(args.file, args.file_b)
            except OSError:
                same = Path(args.file).resolve() == Path(args.file_b).resolve()
            if same:
                raise ConfigError(f"--stage {args.stage} needs two different results files, "
                                  "one per blind answer")
        return api.apply(cfg, args.stage, _read(args.file), ruling=args.ruling,
                         payload_b=None if args.file_b is None else _read(args.file_b))
    if args.cmd == "embed":
        return api.embed(cfg)
    if args.cmd == "report":
        if args.rejections and not args.render:
            raise ConfigError("--rejections goes only with --render")
        if args.render:
            return api.render_report(cfg, _read(args.render), rejections=args.rejections)
        return api.emit(cfg, "report")
    if args.cmd == "sources":  # "doctor" is the only action
        return api.sources_doctor(cfg)
    if args.cmd == "reset":
        if args.stage == "primary":
            return api.reset_primary(cfg, args.url)
        if args.url is not None:
            raise ConfigError("--url goes only with --stage primary")
        return api.reset(cfg, args.stage)
    return api.import_legacy(cfg, args.cache_file)  # "import-legacy"


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        out = _dispatch(args)
    except config.SetupRequired as exc:
        print(f"evidence-echo: {exc}", file=sys.stderr)
        return 4
    except ConfigError as exc:
        print(f"evidence-echo: {exc}", file=sys.stderr)
        return 2
    except (contracts.ContractError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"evidence-echo: {exc}", file=sys.stderr)
        return 1
    print(out if isinstance(out, str) else json.dumps(out, indent=2, ensure_ascii=False))
    return 0
