"""ev search add: candidate URLs become one-time items in the scratch database."""
import hashlib
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from evidence_echo import cli, search
from evidence_echo.core import config, settings, store
from evidence_echo.core.settings import ConfigError
from evidence_echo.sources import base
from tests.conftest import make_item
from tests.evidence_fixtures import seed_topic

NOW = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)
DOC = "<html><head><title>Title tag study</title></head><body><article>" + \
      "<p>We tested 4,000 title tags: 61% saw more clicks. " * 40 + "</p></article></body></html>"


def ctx(body=DOC, status=200):
    return base.FetchContext(
        cutoff=datetime.now(timezone.utc) - timedelta(days=7), limiter=base.RateLimiter(interval=0),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=body))))


def started(workspace):
    topic_db = seed_topic(workspace, "seo")
    cfg = config.load("seo")
    run = search.start(cfg, "Do title tags matter?", NOW)["run"]
    return topic_db, run, config.load("seo", run)


def scratch_items(cfg):
    with closing(store.connect(cfg.db_path)) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM items ORDER BY id")]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_add_stores_once_items_in_scratch(workspace):
    topic_db, run, cfg = started(workspace)
    before = digest(topic_db)
    out = search.add(cfg, run, ["https://a.example/one", "https://b.example/two"], ctx=ctx())
    assert out["added"] == [{"n": 1, "url": "https://a.example/one"}, {"n": 2, "url": "https://b.example/two"}]
    assert all(out[k] == [] for k in ("known", "duplicate", "over_limit", "invalid", "blocked"))
    items = scratch_items(cfg)
    assert [i["source_type"] for i in items] == ["once", "once"]
    assert [i["source_name"] for i in items] == [f"Search: {run} #1", f"Search: {run} #2"]
    assert all(i["state"] == "fetched" and "61% saw more clicks" in i["full_text"] for i in items)
    assert digest(topic_db) == before


def test_known_url_in_other_form(workspace):
    topic_db, run, cfg = started(workspace)
    with closing(store.connect(topic_db)) as conn:
        store.insert_items(conn, [make_item(url="https://example.com/study")])
    before = digest(topic_db)
    urls = ["http://example.com/study?utm_source=x#top", "https://EXAMPLE.com/study/"]
    out = search.add(cfg, run, urls, ctx=ctx())
    assert out["known"] == urls and out["added"] == []
    assert scratch_items(cfg) == [] and digest(topic_db) == before


def test_duplicate_and_limit(workspace, monkeypatch):
    _, run, cfg = started(workspace)
    url = "https://a.example/one"
    out = search.add(cfg, run, [url, url], ctx=ctx())
    assert [a["url"] for a in out["added"]] == [url] and out["duplicate"] == [url]
    out = search.add(cfg, run, ["https://a.example/one/?utm_medium=x"], ctx=ctx())
    assert out["added"] == [] and out["duplicate"] == ["https://a.example/one/?utm_medium=x"]
    assert len(scratch_items(cfg)) == 1
    monkeypatch.setenv("EV_ECHO_SEARCH_MAX_CANDIDATES", "2")
    cfg = config.load("seo", run)
    urls = [f"https://c.example/{i}" for i in range(4)]
    out = search.add(cfg, run, urls, ctx=ctx())
    assert [a["n"] for a in out["added"]] == [2] and out["over_limit"] == urls[1:]
    assert len(scratch_items(cfg)) == 2


def test_blocked_page_waits_for_session(workspace, capsys):
    _, run, cfg = started(workspace)
    out = search.add(cfg, run, ["https://a.example/wall"], ctx=ctx("", 403))
    assert out["blocked"] == ["https://a.example/wall"]
    assert out["added"] == [{"n": 1, "url": "https://a.example/wall"}]
    item = scratch_items(cfg)[0]
    assert item["fetch_status"] == "needs_session" and item["full_text"] is None
    assert cli.main(["--topic", "seo", "--search", run, "fetch-fallback", "emit"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [(i["url"], i["want"]) for i in payload["items"]] == [("https://a.example/wall", "text")]


def test_invalid_urls(workspace):
    _, run, cfg = started(workspace)
    out = search.add(cfg, run, ["ftp://x", "not a url"], ctx=ctx())
    assert out["invalid"] == ["ftp://x", "not a url"] and out["added"] == []
    assert scratch_items(cfg) == []


def test_private_hosts_are_invalid_not_blocked(workspace):
    _, run, cfg = started(workspace)
    urls = ["http://127.0.0.1/x", "http://169.254.169.254/latest", "http://localhost:8080/a"]
    out = search.add(cfg, run, urls, ctx=ctx())
    assert out["invalid"] == urls and out["blocked"] == [] and out["added"] == []
    assert scratch_items(cfg) == []


def test_queries_recorded(workspace):
    _, run, cfg = started(workspace)
    run_dir = config.search_dir(workspace, "seo", run)
    search.add(cfg, run, [], queries=["a", "b"], ctx=ctx())
    search.add(cfg, run, ["https://a.example/one"], queries=["b", "c"], ctx=ctx())
    meta = search.read_meta(run_dir)
    assert meta["queries"] == ["a", "b", "c"]
    assert [c["url"] for c in meta["candidates"]] == ["https://a.example/one"]
    assert meta["candidates"][0]["outcome"] == "added"


def test_add_via_cli_refuses_closed_run(workspace, capsys):
    _, run, _ = started(workspace)
    run_dir = config.search_dir(workspace, "seo", run)
    meta = search.read_meta(run_dir)
    search.write_meta(run_dir, dict(meta, status="dropped"))
    assert cli.main(["--topic", "seo", "search", "add", run, "https://a.example/one"]) == 2


def test_search_max_candidates_setting(workspace):
    assert settings.GLOBAL_DEFAULTS["search_max_candidates"] == 15
    assert "search_max_candidates" not in settings.OVERRIDES
    assert config.load().search_max_candidates == 15
    assert settings.check_value("search_max_candidates", "50", "f") == 50
    for bad in (0, 51, "x", True, "1.5"):
        with pytest.raises(ConfigError, match="search_max_candidates"):
            settings.check_value("search_max_candidates", bad, "f")


def test_add_numbers_past_interrupted_add(workspace):
    _, run, cfg = started(workspace)
    search.add(cfg, run, ["https://a.example/one", "https://a.example/two"], ctx=ctx())
    run_dir = config.search_dir(workspace, "seo", run)
    meta = search.read_meta(run_dir)   # an add killed before search.json was written: only #1 is listed
    search.write_meta(run_dir, dict(meta, candidates=meta["candidates"][:1]))
    out = search.add(cfg, run, ["https://b.example/three"], ctx=ctx())
    assert out["added"] == [{"n": 3, "url": "https://b.example/three"}]
    assert [i["source_name"] for i in scratch_items(cfg)] == [search.source_name(run, n) for n in (1, 2, 3)]


def test_add_reads_urls_from_a_file(workspace, monkeypatch, capsys):
    _, run, _ = started(workspace)
    seen = []
    monkeypatch.setattr(search, "add", lambda cfg, r, urls, queries, ctx=None: seen.append((r, urls, queries)) or {})
    urls = workspace / "urls.txt"
    urls.write_text("https://a.example/one\n\n# a note\n  https://b.example/a$(x)  \n", encoding="utf-8")
    assert cli.main(["--topic", "seo", "search", "add", run, "--urls-file", str(urls), "--query", "q"]) == 0
    assert seen == [(run, ["https://a.example/one", "https://b.example/a$(x)"], ["q"])]
    empty = workspace / "empty.txt"
    empty.write_text("\n", encoding="utf-8")
    assert cli.main(["--topic", "seo", "search", "add", run, "--urls-file", str(empty)]) == 2
    assert "needs URLs" in capsys.readouterr().err
