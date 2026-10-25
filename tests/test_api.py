import json
from pathlib import Path

import pytest

from evidence_echo import api
from evidence_echo.core import config, llm_api, store
from evidence_echo.stages import fetch as fetch_stage
from tests.conftest import FakeBackend, make_item, make_workspace, seed_topic_study
from tests.test_legacy import LEGACY


@pytest.fixture
def cfg(workspace):
    return config.load()


def _seed_fetched(cfg, n=1):
    conn = store.connect(cfg.db_path)
    store.insert_items(conn, [make_item(url=f"https://example.com/a{i}") for i in range(n)])
    conn.close()


def _triage_all(cfg):
    oids = [it["id"] for it in json.loads(api.emit(cfg, "triage"))["items"]]
    return api.apply(cfg, "triage", json.dumps(
        {"results": [{"id": o, "pass": True, "reason": "r"} for o in oids]}))


def test_status(cfg):
    _seed_fetched(cfg, 2)
    assert api._states(cfg.db_path) == {"fetched": 2}


def test_fetch_passes_cfg_and_token(cfg, monkeypatch):
    seen = {}

    def fake_run(conn, c, fetch_all=False, fresh=False):
        seen.update(cfg=c, fetch_all=fetch_all, fresh=fresh)
        return {"rss": {}, "linkedin": {}}

    monkeypatch.setattr(fetch_stage, "run", fake_run)
    assert api.fetch(cfg) == {"rss": {}, "linkedin": {}}
    assert seen == {"cfg": cfg, "fetch_all": False, "fresh": False}
    api.fetch(cfg, True)
    assert seen["fetch_all"] is True
    api.fetch(cfg, fresh=True)
    assert seen["fresh"] is True


def test_emit_apply_roundtrip(cfg):
    _seed_fetched(cfg)
    payload = json.loads(api.emit(cfg, "triage"))
    assert payload["topic"]["id"] == "seo"
    assert _triage_all(cfg) == {"applied": 1, "rejected": []}
    assert api._states(cfg.db_path) == {"triaged": 1}


def test_unknown_stage_raises(cfg):
    with pytest.raises(ValueError, match="Unknown stage 'embed'"):
        api.emit(cfg, "embed")
    with pytest.raises(ValueError, match="Unknown stage 'report'"):
        api.run_api(cfg, "report")
    with pytest.raises(ValueError, match="Unknown stage 'fetch'"):
        api.reset(cfg, "fetch")


def test_run_api_uses_anthropic_backend(cfg, monkeypatch):
    _seed_fetched(cfg)
    fake = FakeBackend(['{"pass": false, "reason": "opinion"}'])
    keys = []
    monkeypatch.setattr(llm_api, "AnthropicBackend", lambda key: keys.append(key) or fake)
    assert api.run_api(cfg, "triage") == {"processed": 1, "rejected": []}
    assert keys == [cfg.anthropic_api_key]
    assert api._states(cfg.db_path) == {"rejected_triage": 1}


def test_extract_and_embed_with_nothing_queued(cfg):
    assert api.extract(cfg) == {"extracted": 0, "degraded": 0, "needs_session": 0}
    assert api.embed(cfg) == {"embedded": 0, "skipped": 0}


def test_emit_report_carries_output_language(cfg):
    assert "Output language: en" in json.loads(api.emit(cfg, "report"))["instructions"]


def test_render_report_writes_into_topic_reports_dir(cfg):
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, '{"results": []}')
    path = Path(out["path"])
    assert path.parent == cfg.reports_dir
    assert path.read_text(encoding="utf-8").startswith("# SEO Report - ")
    assert api.apply(cfg, "report", '{"results": []}')["skipped"] == "no_new_studies"   # nothing new left


def test_reset(cfg):
    _seed_fetched(cfg)
    _triage_all(cfg)
    assert api.reset(cfg, "triage") == {"reset_to": "fetched"}
    assert api._states(cfg.db_path) == {"fetched": 1}


def test_import_legacy(cfg, tmp_path):
    path = tmp_path / "posts_cache.json"
    path.write_text(json.dumps(LEGACY), encoding="utf-8")
    assert api.import_legacy(cfg, path) == {"verified": 1, "rejected": 1, "fetched": 1}


def test_sources_doctor_reads_topic_sources(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr(fetch_stage, "doctor", lambda sources, ctx=None: seen.setdefault("s", sources))
    out = api.sources_doctor(cfg)
    assert out["topic"] == "seo" and seen["s"] == cfg.topic.sources


def test_list_templates():
    ids = [t["id"] for t in api.list_templates()]
    # the public tree ships fewer templates than the private one (release/allowlist.txt)
    assert ids == sorted(ids) and {"ai-coding", "cro", "exercise", "seo"} <= set(ids)


def test_status_all_lists_topics_and_isolates_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("diet", "seo"))
    (tmp_path / "topics" / "diet" / "topic.json").write_text('{"id": "diet"}', encoding="utf-8")
    out = api.status_all(config.workspace())
    by_id = {t["id"]: t for t in out["topics"]}
    assert "field 'name' missing" in by_id["diet"]["error"]
    assert by_id["seo"]["states"] == {} and by_id["seo"]["error"] is None


def test_overview_written_to_reports_root(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("seo",), reports_dir=str(tmp_path / "R"))
    out = api.overview(config.workspace())
    text = (tmp_path / "R" / "overview.md").read_text(encoding="utf-8")
    assert out["path"] == str(tmp_path / "R" / "overview.md")
    assert "## SEO  ·  not run yet" in text


def test_render_report_rebuilds_overview(cfg):
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, json.dumps({"results": []}))
    assert Path(out["overview"]).name == "overview.md"
    assert Path(out["overview"]).exists()


def test_unwritable_overview_dir_is_oserror(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    make_workspace(tmp_path, ("seo",), reports_dir=str(blocker))
    with pytest.raises(OSError):
        api.overview(config.workspace())


@pytest.mark.parametrize("error", [OSError("overview.md is locked"),
                                   __import__("sqlite3").OperationalError("database is locked")])
def test_overview_failure_after_render_is_reported_not_raised(cfg, monkeypatch, error):
    def broken(*a, **kw):
        raise error

    monkeypatch.setattr(api, "overview", broken)
    seed_topic_study()   # a report needs a new study
    out = api.render_report(cfg, json.dumps({"results": []}))
    assert Path(out["path"]).exists()  # the report is committed
    assert out["overview_error"] == str(error)
    assert "overview" not in out


def _corrupt(data_dir, tid):
    (data_dir / "topics" / tid / "echo.db").write_bytes(b"this is not a database" * 50)


def test_topic_list_tolerates_a_corrupt_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("diet", "seo"))
    _corrupt(tmp_path, "diet")
    by_id = {t["id"]: t for t in api.topic_list(config.workspace())}
    assert "not a database" in by_id["diet"]["error"]
    assert by_id["seo"]["last_run"] is None and "error" not in by_id["seo"]


def test_status_all_tolerates_a_corrupt_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("diet", "seo"))
    _corrupt(tmp_path, "diet")
    by_id = {t["id"]: t for t in api.status_all(config.workspace())["topics"]}
    assert "not a database" in by_id["diet"]["error"] and by_id["diet"]["states"] == {}
    assert by_id["seo"]["error"] is None


def test_overview_tolerates_a_corrupt_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("diet", "seo"))
    _corrupt(tmp_path, "diet")
    text = Path(api.overview(config.workspace())["path"]).read_text(encoding="utf-8")
    assert "## DIET" in text and "not a database" in text
    assert "## SEO  ·  not run yet" in text


def test_reset_match_clears_study_fields_and_unmerge_still_restores(cfg):
    from tests.test_match import F, judged, study_of
    from evidence_echo.stages import match
    conn = store.connect(cfg.db_path)
    x = judged(conn, "https://e.com/x", F(["44%", "79%"]))
    match.fixed_pass(conn)
    assert store.get_item(conn, x)["first_study_id"] == study_of(conn, x)
    conn.close()
    assert api.reset(cfg, "match") == {"reset_to": "judged"}
    conn = store.connect(cfg.db_path)
    item = store.get_item(conn, x)
    assert (item["state"], item["study_id"], item["first_study_id"], item["match_reason"]) == \
        ("judged", None, None, None)
    plan = match.fixed_pass(conn)                    # old study is a candidate now
    assert [e["item"]["id"] for e in plan] == [x]
    match.apply(conn, json.dumps({"results": [{"id": f"i{x}", "same_as": None, "reason": "new"}]}))
    c = study_of(conn, x)
    d = store.create_study(conn, "B", F(["5%"]), None)
    store.merge_studies(conn, d, c)
    assert study_of(conn, x) == d
    assert store.unmerge_study(conn, c)["moved_items"] == 1
    assert study_of(conn, x) == c
    conn.close()


def test_reset_judge_from_matched_clears_study_fields(cfg):
    from tests.test_match import F, judged
    from evidence_echo.stages import match
    conn = store.connect(cfg.db_path)
    x = judged(conn, "https://e.com/x", F(["44%"]))
    match.fixed_pass(conn)
    conn.close()
    assert api.reset(cfg, "judge") == {"reset_to": "extracted"}
    conn = store.connect(cfg.db_path)
    store.set_verdict(conn, x, True, "B", F(["44%"]), None)      # re-judged
    item = store.get_item(conn, x)
    assert (item["state"], item["study_id"], item["first_study_id"], item["match_reason"]) == \
        ("judged", None, None, None)
    conn.close()


def test_rejudge_failure_clears_study_fields(db):
    from tests.test_match import F, judged
    from evidence_echo.stages import match
    x = judged(db, "https://e.com/x", F(["44%"]))
    match.fixed_pass(db)
    store.set_verdict(db, x, False, None, {}, None)
    item = store.get_item(db, x)
    assert (item["state"], item["study_id"], item["first_study_id"], item["match_reason"]) == \
        ("rejected", None, None, None)


def test_api_match_passes_embedder_only_with_key(cfg, monkeypatch):
    from evidence_echo.core import embedders
    from evidence_echo.stages import match
    seen = []
    monkeypatch.setattr(match, "emit", lambda conn, topic, embedder=None: seen.append(embedder) or "{}")
    api.emit(cfg, "match")
    from tests.conftest import write_plugin_keys
    write_plugin_keys(cfg.plugin_data_dir, VOYAGE_API_KEY="voyage-test-key")
    api.emit(config.load(), "match")
    assert seen[0] is None and isinstance(seen[1], embedders.VoyageEmbedder)


def test_reset_judge_drops_vectors(cfg):
    import numpy as np
    from tests.test_match import F, judged
    conn = store.connect(cfg.db_path)
    x = judged(conn, "https://e.com/x", F())
    store.save_vector(conn, x, "t/card-v1", np.ones(4, dtype=np.float32).tobytes())
    conn.close()
    api.reset(cfg, "judge")
    conn = store.connect(cfg.db_path)
    assert not conn.execute("SELECT 1 FROM embeddings WHERE item_id=?", (x,)).fetchall()
    conn.close()
