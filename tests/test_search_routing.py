import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone

import pytest

from evidence_echo import api, cli, search
from evidence_echo.core import config, interfaces, store
from evidence_echo.core.settings import ConfigError
from evidence_echo.core.urls import canonical_url
from tests.conftest import make_item
from tests.evidence_fixtures import seed_topic
from tests.test_judge import TEXT, VERDICT

NOW = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)
TOP, SCR = "ZTOP", "ZSCR"   # marks in titles and URLs: which database a payload item came from
FACTS = {"what_was_tested": "x", "findings": "f", "research_timeframe": "t",
         "match_keys": {"key_numbers": ["44%", "79%"]}}


def put(conn, tag, name, state, **cols):
    """One item in `state`, titled and addressed with `tag`; cols are set on top."""
    url = f"https://example.com/{tag.lower()}-{name}"
    store.insert_items(conn, [make_item(url=url, title=f"{tag} {name}")])
    item_id = conn.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    cols = {"state": state, **cols}
    conn.execute(f"UPDATE items SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*cols.values(), item_id))
    conn.commit()
    return item_id


def plant(conn, tag, stage):
    """The items `stage` works on, marked with `tag`."""
    if stage == "triage":
        put(conn, tag, "t", "fetched")
    elif stage == "judge":
        put(conn, tag, "j", "extracted", full_text=TEXT)
    elif stage == "match":   # b waits for a's study
        for name in "ab":
            put(conn, tag, name, "judged", verdict_pass=1, evidence_tier="B", facts=json.dumps(FACTS))
    elif stage == "verify":
        put(conn, tag, "v", "matched", full_text=TEXT)
    elif stage in ("primary", "report"):
        original = f"https://orig.example/{tag.lower()}"
        sid = store.create_study(conn, "B", FACTS, original)
        put(conn, tag, "r", "verified", study_id=sid, full_text=TEXT, facts=json.dumps(FACTS),
            evidence_tier="B", skeptic_tier="B", skeptic_agrees=1)
        store.record_primary_fetch(conn, canonical_url(original), text="The original study text.")
    else:   # fetch-fallback
        put(conn, tag, "f", "fetched", fetch_status="needs_session")


def snapshot(root):
    """Every file under root with a hash of its bytes."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def topic_bytes(data):
    folder = data / "topics" / "seo"
    return (folder / "echo.db").read_bytes(), (folder / "topic.json").read_bytes()


def started(workspace, stages=()):
    """Topic 'seo' with items of `stages` marked TOP, a search run with the same items marked SCR."""
    topic_db = seed_topic(workspace, "seo")
    with closing(store.connect(topic_db)) as conn:
        for stage in stages:
            plant(conn, TOP, stage)
    run = search.start(config.load("seo"), "Do title tags matter?", NOW)["run"]
    scratch = config.search_dir(workspace, "seo", run) / search.SCRATCH_DB
    with closing(store.connect(scratch)) as conn:
        for stage in stages:
            plant(conn, SCR, stage)
    return run, scratch


def call(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def marks(text):
    """Which tags the payload's items carry."""
    items = json.dumps(json.loads(text)["items"]).lower()   # URLs carry the tag in lower case
    return {t for t in (TOP, SCR) if t.lower() in items}


def test_load_with_search_points_at_scratch(workspace):
    run, scratch = started(workspace)
    plain, routed = config.load("seo"), config.load("seo", run)
    assert routed.db_path == scratch == config.search_dir(workspace, "seo", run) / "echo.db"
    assert routed.search_run == run and plain.search_run is None
    assert plain.db_path == workspace / "topics" / "seo" / "echo.db"
    assert routed.topic == plain.topic and routed.grouping_mode == plain.grouping_mode
    assert routed.topic.grading == plain.topic.grading
    assert routed.reports_dir == plain.reports_dir and routed.output_language == plain.output_language
    assert routed.primary_check == plain.primary_check and routed.topic_dir == plain.topic_dir


EMITS = {   # stage -> the cli words that make its session payload
    "triage": ["triage", "--emit"], "judge": ["judge", "--emit"], "match": ["match", "--emit"],
    "verify": ["verify", "--emit"], "primary": ["primary", "--emit"], "report": ["report"],
    "fetch-fallback": ["fetch-fallback", "emit"],
}


@pytest.mark.parametrize("stage", list(EMITS))
def test_each_allowed_stage_reads_scratch(workspace, monkeypatch, capsys, stage):
    monkeypatch.setenv("EV_ECHO_PRIMARY_CHECK", "on")
    run, _ = started(workspace, [stage])
    code, out, err = call(capsys, "--topic", "seo", "--search", run, *EMITS[stage])
    assert code == 0, err
    assert marks(out) == {SCR}
    code, out, err = call(capsys, "--topic", "seo", *EMITS[stage])
    assert code == 0, err
    assert marks(out) == {TOP}


def test_apply_with_search_writes_scratch_only(workspace, capsys, tmp_path):
    run, scratch = started(workspace, ["judge"])
    before = topic_bytes(workspace)
    answer = tmp_path / "judge.json"
    answer.write_text(json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(1))]}), encoding="utf-8")
    code, out, err = call(capsys, "--topic", "seo", "--search", run, "apply", "--stage", "judge", str(answer))
    assert code == 0, err
    with closing(store.connect(scratch)) as conn:
        assert store.get_item(conn, 1)["state"] == "judged"
    assert topic_bytes(workspace) == before
    with closing(store.connect(workspace / "topics" / "seo" / "echo.db")) as conn:
        assert store.get_item(conn, 1)["state"] == "extracted"


@pytest.mark.parametrize("argv", [
    ["classify", "--emit"], ["group", "--emit"], ["split"], ["relate", "--emit"], ["regrade", "--emit"],
    ["embed"], ["fetch"], ["studies", "list"], ["report", "--render", "f.json"],
    ["apply", "--stage", "report", "f.json"], ["apply", "--stage", "classify", "f.json"],
    ["reset", "--stage", "judge"], ["status"], ["topic", "list"], ["search", "start", "--question", "q"],
])
def test_refused_commands(workspace, capsys, argv):
    run, _ = started(workspace)
    before = snapshot(workspace)
    code, out, err = call(capsys, "--topic", "seo", "--search", run, *argv)
    assert code == 2 and out == ""
    assert "does not run on a search" in err and "allowed:" in err
    assert all(word in err for word in ("triage", "judge", "match", "verify", "primary"))
    assert snapshot(workspace) == before


def test_refused_before_any_workspace_call(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "nothing"))
    code, _, err = call(capsys, "--search", "20261026-0905-x", "classify")
    assert code == 2 and "does not run on a search" in err
    assert not (tmp_path / "nothing").exists()


def test_render_is_refused_by_the_api_too(workspace):
    run, _ = started(workspace)
    with pytest.raises(ConfigError, match="search"):
        api.render_report(config.load("seo", run), json.dumps({"results": []}))


def test_closed_or_unknown_run_refused(workspace, capsys):
    run, scratch = started(workspace)
    run_dir = config.search_dir(workspace, "seo", run)
    meta = search.read_meta(run_dir)
    search.write_meta(run_dir, {**meta, "status": "dropped", "page": str(workspace / "reports" / "p.html")})
    code, _, err = call(capsys, "--topic", "seo", "--search", run, "triage", "--emit")
    assert code == 2 and "dropped" in err and "p.html" in err
    with pytest.raises(ConfigError, match="dropped"):
        config.load("seo", run)
    for unknown in ("20261026-0905-nothing", "../x"):
        code, _, err = call(capsys, "--topic", "seo", "--search", unknown, "triage", "--emit")
        assert code == 2 and err.startswith("evidence-echo:")
    with pytest.raises(ConfigError, match="no search '20261026-0905-nothing' in topic 'seo'"):
        config.load("seo", "20261026-0905-nothing")


def test_run_of_another_topic_refused(workspace, capsys):
    run, _ = started(workspace)
    seed_topic(workspace, "cro")
    code, _, err = call(capsys, "--topic", "cro", "--search", run, "triage", "--emit")
    assert code == 2 and f"no search '{run}' in topic 'cro'" in err


def test_topic_untouched_through_the_chain(workspace, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("EV_ECHO_PRIMARY_CHECK", "on")
    topic_db = seed_topic(workspace, "seo")
    with closing(store.connect(topic_db)) as conn:
        for stage in ("judge", "verify", "report"):
            plant(conn, TOP, stage)
    before = topic_bytes(workspace)
    before_all = {k: v for k, v in snapshot(workspace).items() if k.startswith("topics")}
    run = search.start(config.load("seo"), "Do title tags matter?", NOW)["run"]
    scratch = config.search_dir(workspace, "seo", run) / search.SCRATCH_DB
    with closing(store.connect(scratch)) as conn:
        put(conn, SCR, "chain", "extracted", full_text=TEXT)
        store.record_primary_fetch(conn, canonical_url(VERDICT["primary_url"]), text="The original study.")
    base = ["--topic", "seo", "--search", run]

    answer = tmp_path / "judge.json"
    answer.write_text(json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(1))]}), encoding="utf-8")
    assert call(capsys, *base, "apply", "--stage", "judge", str(answer))[0] == 0
    assert call(capsys, *base, "match", "--emit")[0] == 0
    with closing(store.connect(scratch)) as conn:
        assert store.get_item(conn, 1)["state"] == "matched"
    verdict = tmp_path / "verify.json"
    verdict.write_text(json.dumps({"results": [{"id": interfaces.opaque_id(1), "tier": "A",
                                                "red_flags": [], "note": "ok"}]}), encoding="utf-8")
    assert call(capsys, *base, "apply", "--stage", "verify", str(verdict))[0] == 0
    code, out, err = call(capsys, *base, "primary", "--emit")
    assert code == 0, err
    assert [i["title"] for i in json.loads(out)["items"]] == [f"{SCR} chain"]
    code, out, err = call(capsys, *base, "report")
    assert code == 0, err
    assert [i["sources"][0]["url"] for i in json.loads(out)["items"]] == [f"https://example.com/{SCR.lower()}-chain"]
    assert call(capsys, *base, "fetch-fallback", "emit")[0] == 0

    assert topic_bytes(workspace) == before
    assert {k: v for k, v in snapshot(workspace).items() if k.startswith("topics")} == before_all
    assert not (workspace / "reports").exists()
