"""ev search keep and ev search drop: kept studies join the topic as one-time items at `judged`."""
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone

import httpx
import pytest

from evidence_echo import api, cli, search
from evidence_echo.core import config, store, topics
from evidence_echo.core.settings import ConfigError
from evidence_echo.sources import base
from evidence_echo.stages import match, verify
from tests.conftest import make_item
from tests.evidence_fixtures import add_study, seed_topic

NOW = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)
QUESTION = "Do title tags matter?"
FINDINGS = [{"text": "Rewrites gained clicks", "quote": "gained 61% clicks", "quote_checked": True,
             "metric": "clicks", "measure": "clicks"}]
GRADED = ("evidence_tier", "judge_tier", "skeptic_tier", "skeptic_agrees", "skeptic_note", "red_flags", "quote_check",
          "quotes", "facts", "headline", "full_text", "findings", "disputed", "dispute_reason", "primary_url",
          "judge_tier_raw", "skeptic_tier_raw", "caps_hash", "study_type", "graded_rules", "entity_names",
          "metric_code", "subtopic_choice", "provenance", "title", "source_name", "source_type")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(db_path, sql, *args):
    with closing(store.connect(db_path)) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def graded(conn, run, n, name, tier, primary, **cols):
    """One scratch study with one verified item as a graded search candidate: findings, a red flag, a quote
    check, a primary check and a primary source. Returns (study id, item id)."""
    sid = add_study(conn, name, f"{name} findings", tier=tier, reported=False)
    item_id = conn.execute("SELECT id FROM items WHERE study_id=?", (sid,)).fetchone()[0]
    facts = {"what_was_tested": name, "findings": "f", "research_timeframe": "t",
             "match_keys": {"key_numbers": [f"{n}1%"]}}
    values = {"source_name": search.source_name(run, n), "source_type": "once", "title": name,
              "judge_tier": "A", "skeptic_tier": tier, "evidence_tier": tier, "skeptic_agrees": 1,
              "skeptic_note": "fine", "red_flags": json.dumps(["small_sample"]),
              "quote_check": json.dumps({"checked": 2, "missing": 0}), "quotes": json.dumps(["gained 61% clicks"]),
              "facts": json.dumps(facts), "headline": json.dumps({"value": "+61%", "label": "clicks"}),
              "findings": json.dumps(FINDINGS), "primary_url": primary, "judge_tier_raw": "A",
              "skeptic_tier_raw": tier, "caps_hash": "h1", "study_type": "experiment", "graded_rules": "{}",
              "entity_names": json.dumps(["Google"]), "metric_code": "clicks", "full_text": f"{name} full text",
              "subtopic_choice": json.dumps({"slug": "title-tags"}), **cols}
    conn.execute(f"UPDATE items SET {', '.join(c + '=?' for c in values)} WHERE id=?", (*values.values(), item_id))
    store.save_primary_check(conn, item_id, primary_url=primary, status="agreed", prompt_version="p1",
                             numbers="61%", verdict="matches", note="ok")
    store.record_primary_fetch(conn, store.canonical_url(primary), text=f"original of {name}")
    conn.commit()
    return sid, item_id


def make_run(workspace, topic="seo", **first):
    """A started run with two graded scratch studies (s<a>, s<b>) and one rejected item."""
    seed_topic(workspace, topic)
    run = search.start(config.load(topic), QUESTION, NOW)["run"]
    cfg = config.load(topic, run)
    with closing(store.connect(cfg.db_path)) as conn:
        a = graded(conn, run, 1, "Title tag test", "B", "https://orig.example/a", **first)
        b = graded(conn, run, 2, "H1 test", "C", "https://orig.example/b")
        store.insert_items(conn, [make_item(url="https://x.example/opinion", source_name=search.source_name(run, 3),
                                            source_type="once")])
        conn.execute("UPDATE items SET state='rejected', judge_reason='opinion' WHERE url=?",
                     ("https://x.example/opinion",))
        conn.commit()
    return run, cfg, a, b


def keep(run, *extra, topic="seo"):
    return cli.main(["--topic", topic, "search", "keep", run, *extra])


def topic_items(workspace, topic="seo"):
    return rows(workspace / "topics" / topic / "echo.db", "SELECT * FROM items ORDER BY id")


def topic_sources(workspace, topic="seo"):
    return topics.load_topic(workspace / "topics", topic).sources


def test_keep_copies_items_with_grades(workspace, capsys):
    run, cfg, (sa, ia), (sb, ib) = make_run(workspace)
    scratch = {r["url"]: r for r in rows(cfg.db_path, "SELECT * FROM items")}
    checks = {r["item_id"]: r for r in rows(cfg.db_path, "SELECT * FROM primary_checks")}
    assert keep(run) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kept"] == [f"s{sa}", f"s{sb}"] and out["items"] == 2
    assert out["sources"] == [search.source_name(run, 1), search.source_name(run, 2)]
    items = {r["url"]: r for r in topic_items(workspace)}
    assert len(items) == 2 and "https://x.example/opinion" not in items
    for url, item in items.items():
        old = scratch[url]
        assert item["state"] == "judged" and item["study_id"] is None and item["match_reason"] is None
        for col in GRADED:
            assert item[col] == old[col], col
    with closing(store.connect(workspace / "topics" / "seo" / "echo.db")) as conn:
        for url, old in ((u, scratch[u]) for u in items):
            new_id = items[url]["id"]
            check = store.primary_check(conn, new_id)
            was = checks[old["id"]]
            assert check and all(check[k] == was[k] for k in was if k not in ("item_id", "checked_at"))
            assert conn.execute("SELECT text FROM primary_sources WHERE url=?",
                                (store.canonical_url(old["primary_url"]),)).fetchone() is not None
        state = {r["name"]: r["done"] for r in conn.execute("SELECT name, done FROM sources_state")}
    sources = [s for s in topic_sources(workspace) if s.get("found_by") == "search"]
    assert [s["name"] for s in sources] == out["sources"]
    assert all(s["type"] == "once" and s["search_run"] == run and s["search_question"] == QUESTION
               and s["url"] in items for s in sources)
    assert all(state[s["name"]] == 1 for s in sources)
    meta = search.read_meta(config.search_dir(workspace, "seo", run))
    assert meta["status"] == "kept" and meta["kept"] == {"studies": [f"s{sa}", f"s{sb}"], "of": 2, "items": 2}
    assert not (config.search_dir(workspace, "seo", run) / search.SCRATCH_DB).exists()
    assert "Kept on" in cfg.reports_dir.joinpath("searches", f"{run}.html").read_text(encoding="utf-8")


def test_next_run_finishes_kept_items(workspace):
    run, cfg, (sa, ia), (sb, ib) = make_run(workspace)
    assert keep(run) == 0
    tcfg = config.load("seo")
    with api._topic_db(tcfg) as conn:
        assert match.fixed_pass(conn) == []
        topic = tcfg.topic
        assert json.loads(verify.emit(conn, topic))["items"] == []
        items = store.items_in_state(conn, "verified")
        assert len(items) == 2
        tiers = {r["canonical_name"] or r["what_was_tested"]: r["evidence_tier"]
                 for r in map(dict, conn.execute("SELECT * FROM studies"))}
        assert sorted(tiers.values()) == ["B", "C"]
        for item in items:
            assert item["study_id"] is not None
            assert conn.execute("SELECT COUNT(*) FROM findings WHERE study_id=?", (item["study_id"],)).fetchone()[0] == 1
    listed = json.loads(api.emit(tcfg, "report"))["items"]
    assert sorted(i["study_id"] for i in listed) == sorted(i["study_id"] for i in items)


def test_kept_source_never_fetched(workspace, monkeypatch):
    run, cfg, *_ = make_run(workspace)
    assert keep(run) == 0
    seen = []

    def fail(request):
        seen.append(str(request.url))
        return httpx.Response(500)

    monkeypatch.setattr(base, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(fail)))
    api.fetch(config.load("seo"))
    kept = {i["url"] for i in topic_items(workspace)}
    assert len(kept) == 2 and not kept & set(seen)   # the template's feeds are asked, the kept pages are not
    assert not [u for u in seen if "orig.example" in u]


def test_keep_joins_existing_study(workspace):
    run, cfg, (sa, ia), (sb, ib) = make_run(workspace)
    path = workspace / "topics" / "seo" / "echo.db"
    with closing(store.connect(path)) as conn:
        existing = store.create_study(conn, "A", {"what_was_tested": "T", "findings": "f", "research_timeframe": "t",
                                                  "match_keys": {"key_numbers": ["9%"]}},
                                      "https://orig.example/a")
        conn.commit()
    assert keep(run) == 0
    tcfg = config.load("seo")
    with api._topic_db(tcfg) as conn:
        match.fixed_pass(conn)
        store.promote_pregraded(conn)
        joined = conn.execute("SELECT study_id FROM items WHERE title='Title tag test'").fetchone()[0]
        assert joined == existing
        assert conn.execute("SELECT COUNT(*) FROM studies").fetchone()[0] == 2
        assert store.get_study(conn, existing)["evidence_tier"] == "A"


def test_keep_only(workspace, capsys):
    run, cfg, (sa, ia), (sb, ib) = make_run(workspace)
    topic_db, topic_json = workspace / "topics" / "seo" / "echo.db", workspace / "topics" / "seo" / "topic.json"
    before = (digest(topic_db), digest(topic_json))
    assert keep(run, "--only", f"s{sb}x") == 2 and keep(run, "--only", "s9999") == 2
    assert (digest(topic_db), digest(topic_json)) == before
    assert keep(run, "--only", "") == 2
    capsys.readouterr()
    assert keep(run, "--only", f"s{sa}") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kept"] == [f"s{sa}"] and len(out["sources"]) == 1 and out["items"] == 1
    assert [r["title"] for r in topic_items(workspace)] == ["Title tag test"]
    meta = search.read_meta(config.search_dir(workspace, "seo", run))
    assert meta["kept"] == {"studies": [f"s{sa}"], "of": 2, "items": 1}
    page = cfg.reports_dir.joinpath("searches", f"{run}.html").read_text(encoding="utf-8")
    assert "(1 of 2 studies)" in page and "Kept on" in page


def test_keep_maps_changed_topic(workspace):
    path = seed_topic(workspace, "seo")
    with closing(store.connect(path)) as conn:
        clicks = conn.execute("SELECT id FROM metrics WHERE slug='clicks'").fetchone()[0]
        for slug in ("ctr", "rankings"):
            if not conn.execute("SELECT 1 FROM metrics WHERE slug=?", (slug,)).fetchone():
                conn.execute("INSERT INTO metrics (slug, name, status, position, created_at) "
                             "VALUES (?, ?, 'seeded', 9, 'x')", (slug, slug))
        conn.execute("INSERT INTO entities (slug, name, status, created_at) VALUES ('spam', 'Spam', 'active', 'x')")
        conn.commit()
    run = search.start(config.load("seo"), QUESTION, NOW)["run"]
    cfg = config.load("seo", run)
    with closing(store.connect(cfg.db_path)) as conn:
        graded(conn, run, 1, "Title tag test", "B", "https://orig.example/a", metric_code="ctr",
               subtopic_choice=json.dumps({"propose": "Snippet rewrites"}), entity_names=json.dumps(["Spam", "Google"]))
        graded(conn, run, 2, "H1 test", "C", "https://orig.example/b", metric_code="rankings",
               subtopic_choice=json.dumps({"slug": "title-tags"}))
    with closing(store.connect(path)) as conn:   # the topic moves on after start
        conn.execute("UPDATE metrics SET status='merged', merged_into=? WHERE slug='ctr'", (clicks,))
        conn.execute("UPDATE metrics SET status='rejected' WHERE slug='rankings'")
        conn.execute("UPDATE entities SET status='rejected' WHERE slug='spam'")
        conn.execute("INSERT INTO subtopics (slug, name, status, position, created_at) "
                     "VALUES ('snippet-rewrites', 'Snippet rewrites', 'confirmed', 9, 'x')")
        conn.commit()
        subtopics_before = conn.execute("SELECT COUNT(*) FROM subtopics").fetchone()[0]
    assert keep(run) == 0
    items = {r["title"]: r for r in topic_items(workspace)}
    assert items["Title tag test"]["metric_code"] == "clicks" and items["H1 test"]["metric_code"] == "other"
    with closing(store.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM subtopics").fetchone()[0] == subtopics_before
        tcfg = config.load("seo")
        with api._topic_db(tcfg) as c2:
            match.fixed_pass(c2)
            study = store.get_study(c2, store.get_item(c2, items["Title tag test"]["id"])["study_id"])
            assert study["subtopic_id"] == store.get_subtopic(c2, "snippet-rewrites")["id"]
            assert [e["name"] for e in store.study_entities(c2, study["id"])] == ["Google"]


def test_keep_refusals(workspace, capsys):
    run, cfg, *_ = make_run(workspace)
    run2 = search.start(config.load("seo"), "Another question?", NOW)["run"]
    assert keep(run) == 0
    capsys.readouterr()
    before = topic_items(workspace)
    page = cfg.reports_dir / "searches" / f"{run}.html"
    assert keep(run) == 2
    err = capsys.readouterr().err
    assert "kept" in err and str(page) in err
    assert cli.main(["--topic", "seo", "search", "drop", run]) == 2
    assert "kept" in capsys.readouterr().err
    assert cli.main(["--topic", "seo", "search", "render", run, "--summary", "x", "--writeups", "x"]) == 2
    assert topic_items(workspace) == before
    assert cli.main(["--topic", "seo", "search", "drop", run2]) == 0
    capsys.readouterr()
    assert keep(run2) == 2 and "dropped" in capsys.readouterr().err
    assert cli.main(["--topic", "seo", "search", "drop", run2]) == 2
    assert cli.main(["--topic", "seo", "search", "render", run2, "--summary", "x", "--writeups", "x"]) == 2
    assert topic_items(workspace) == before


def test_keep_resumes_after_failure(workspace, monkeypatch, capsys):
    run, cfg, *_ = make_run(workspace)
    real, calls = search._copy_primary, []

    def flaky(*args, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk went away")
        return real(*args, **kw)

    monkeypatch.setattr(search, "_copy_primary", flaky)
    with pytest.raises(RuntimeError):
        keep(run)
    meta = search.read_meta(config.search_dir(workspace, "seo", run))
    assert meta["status"] == "open" and (config.search_dir(workspace, "seo", run) / search.SCRATCH_DB).exists()
    assert len(topic_items(workspace)) == 1
    capsys.readouterr()
    assert keep(run) == 0
    items = topic_items(workspace)
    assert len(items) == 2 and len({i["url"] for i in items}) == 2
    names = [s["name"] for s in topic_sources(workspace) if s.get("found_by") == "search"]
    assert len(names) == 2 == len(set(names))
    with closing(store.connect(workspace / "topics" / "seo" / "echo.db")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM primary_checks").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM sources_state WHERE done=1 AND name LIKE 'Search:%'").fetchone()[0] == 2
    assert search.read_meta(config.search_dir(workspace, "seo", run))["status"] == "kept"


def test_drop_deletes_scratch_keeps_page(workspace, capsys):
    run, cfg, *_ = make_run(workspace)
    topic_db, topic_json = workspace / "topics" / "seo" / "echo.db", workspace / "topics" / "seo" / "topic.json"
    before = (digest(topic_db), digest(topic_json))
    assert cli.main(["--topic", "seo", "search", "drop", run]) == 0
    out = json.loads(capsys.readouterr().out)
    run_dir = config.search_dir(workspace, "seo", run)
    page = cfg.reports_dir / "searches" / f"{run}.html"
    assert out == {"dropped": run, "page": str(page)}
    assert not (run_dir / search.SCRATCH_DB).exists() and not list(run_dir.glob("*-journal"))
    assert search.read_meta(run_dir)["status"] == "dropped" and (run_dir / search.META).is_file()
    assert "Dropped" in page.read_text(encoding="utf-8")
    assert (digest(topic_db), digest(topic_json)) == before


def test_validate_search_source_keys(workspace):
    seed_topic(workspace, "seo")
    topic = topics.load_topic(workspace / "topics", "seo")
    good = {"type": "once", "name": "Search: x #1", "url": "https://a.example/x", "found_by": "search",
            "search_run": "20261026-0905-q", "search_question": "Why?"}
    topics.update_topic(workspace / "topics", "seo", sources=[*topic.sources, good])
    for bad in ({"found_by": "web"}, {"search_run": "../x"}, {"search_question": ""},
                {"search_question": "x" * 301}):
        with pytest.raises(ConfigError):
            topics.update_topic(workspace / "topics", "seo",
                                sources=[*topic.sources, {**good, "name": "Search: x #2", **bad}])
    with pytest.raises(ConfigError, match="duplicate"):
        topics.update_topic(workspace / "topics", "seo", sources=[*topic.sources, good, dict(good)])


def test_interrupted_keep_never_leaves_a_fetchable_source(workspace, monkeypatch, capsys):
    run, cfg, *_ = make_run(workspace)
    real, calls = search._copy_item, []

    def flaky(*args, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk went away")
        return real(*args, **kw)

    monkeypatch.setattr(search, "_copy_item", flaky)
    with pytest.raises(RuntimeError):
        keep(run)
    monkeypatch.setattr(search, "_copy_item", real)
    seen = []

    def fail(request):
        seen.append(str(request.url))
        return httpx.Response(500)

    monkeypatch.setattr(base, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(fail)))
    api.fetch(config.load("seo"))
    urls = {r["url"] for r in rows(cfg.db_path, "SELECT url FROM items WHERE state='verified'")}
    assert len(urls) == 2 and not urls & set(seen)
    assert len(topic_items(workspace)) == 1
    capsys.readouterr()
    assert keep(run) == 0
    items = topic_items(workspace)
    assert len(items) == 2 and all(i["state"] == "judged" and i["evidence_tier"] for i in items)


def test_retried_keep_replaces_ungraded_same_named_item(workspace, capsys):
    run, cfg, *_ = make_run(workspace)
    scratch = rows(cfg.db_path, "SELECT * FROM items WHERE source_name=?", search.source_name(run, 2))[0]
    with closing(store.connect(workspace / "topics" / "seo" / "echo.db")) as conn:   # fetched as a raw item
        store.insert_items(conn, [make_item(url=scratch["url"], source_name=scratch["source_name"],
                                            source_type="once")])
    assert keep(run) == 0
    items = {i["url"]: i for i in topic_items(workspace)}
    item = items[scratch["url"]]
    assert len(items) == 2 and item["state"] == "judged"
    assert all(item[c] == scratch[c] for c in GRADED)
    with closing(store.connect(workspace / "topics" / "seo" / "echo.db")) as conn:
        assert store.primary_check(conn, item["id"]) is not None


def test_failed_rerender_keeps_scratch_for_render(workspace, monkeypatch, capsys):
    run, cfg, *_ = make_run(workspace)
    run_dir = config.search_dir(workspace, "seo", run)
    real = search.render_page
    monkeypatch.setattr(search, "render_page", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert keep(run) == 0
    out = json.loads(capsys.readouterr().out)
    assert "disk full" in out["page_error"] and len(topic_items(workspace)) == 2
    assert search.read_meta(run_dir)["status"] == "kept" and (run_dir / search.SCRATCH_DB).exists()
    monkeypatch.setattr(search, "render_page", real)
    assert cli.main(["--topic", "seo", "search", "render", run, "--summary", "x", "--writeups", "x"]) == 2
    capsys.readouterr()
    assert cli.main(["--topic", "seo", "search", "render", run]) == 0
    out = json.loads(capsys.readouterr().out)
    page = cfg.reports_dir / "searches" / f"{run}.html"
    assert out["page"] == str(page) and "Kept on" in page.read_text(encoding="utf-8")
    assert not (run_dir / search.SCRATCH_DB).exists()
    assert cli.main(["--topic", "seo", "search", "render", run]) == 2


def test_scratch_held_open_is_a_warning(workspace, monkeypatch, capsys):
    run, cfg, *_ = make_run(workspace)

    def held(run_dir):
        raise PermissionError("file in use")

    monkeypatch.setattr(search, "_delete_scratch", held)
    assert cli.main(["--topic", "seo", "search", "drop", run]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "file in use" in out["warning"]
    assert search.read_meta(config.search_dir(workspace, "seo", run))["status"] == "dropped"


def _with_report(workspace, cfg):
    """A recorded report whose HTML page exists, so the topic has a topic page to write."""
    report = cfg.reports_dir / "2026-10-25-report.html"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("x", encoding="utf-8")
    with closing(store.connect(workspace / "topics" / "seo" / "echo.db")) as conn:
        conn.execute("INSERT INTO reports (path, rendered_at) VALUES (?, '2026-10-25T09:00:00+00:00')",
                     (str(report.with_suffix(".md")),))
        conn.commit()
    return cfg.reports_dir / "seo.html"


@pytest.mark.parametrize("action", ["keep", "drop"])
def test_closing_a_run_links_its_page_from_the_topic_page(workspace, capsys, action):
    run, cfg, *_ = make_run(workspace)
    topic_page = _with_report(workspace, cfg)
    assert cli.main(["--topic", "seo", "search", action, run]) == 0
    text = topic_page.read_text(encoding="utf-8")
    assert "Searches (1)" in text and f'href="searches/{run}.html">{QUESTION}</a>' in text
    assert 'href="2026-10-25-report.html"' in text   # Latest report: the newest in the list


def test_render_links_an_open_run_and_skips_a_topic_without_reports(workspace, capsys):
    run, cfg, *_ = make_run(workspace)
    out = search.render_page(cfg, run)
    assert "topic" not in out and not (cfg.reports_dir / "seo.html").exists()   # no report yet: no page
    topic_page = _with_report(workspace, cfg)
    assert search.render_page(cfg, run)["topic"] == str(topic_page)
    assert f'href="searches/{run}.html">{QUESTION}</a>' in topic_page.read_text(encoding="utf-8")
