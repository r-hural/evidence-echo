"""The search answer page, ev search render and ev search list."""
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone

import pytest

from evidence_echo import cli, search
from evidence_echo.core import config, store
from evidence_echo.render import search_page
from tests.conftest import make_item
from tests.evidence_fixtures import add_study, seed_topic

NOW = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)
QUESTION = "Do <b>title</b> tags & H1s differ?"
SUMMARY = "Two studies measured clicks.\n\nThey disagree on size & scope."


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reject(conn, url, state, reason, title="A page"):
    store.insert_items(conn, [make_item(url=url, title=title)])
    col = "triage_reason" if state == "rejected_triage" else "judge_reason"
    conn.execute(f"UPDATE items SET state=?, {col}=? WHERE url=?", (state, reason, url))
    conn.commit()


def graded_run(workspace, topic="seo", template="seo", question=QUESTION, when=NOW):
    """A started run whose scratch DB holds two studies (one disputed) and two rejected items."""
    seed_topic(workspace, topic, template=template)
    run = search.start(config.load(topic), question, when)["run"]
    cfg = config.load(topic, run)
    with closing(store.connect(cfg.db_path)) as conn:
        a = add_study(conn, "Title tag test", "Rewrites gained clicks", tier="B", reported=False)
        b = add_study(conn, "H1 test", "H1s changed nothing", tier="C", reported=False)
        conn.execute("UPDATE items SET disputed=1, skeptic_tier='D', dispute_reason='tier_gap', "
                     "skeptic_note='sample too small' WHERE study_id=?", (b,))
        conn.commit()
        reject(conn, "https://x.example/opinion", "rejected", "an opinion piece, no data", "Opinion <i>piece</i>")
        reject(conn, "https://y.example/news", "rejected_triage", "news, not a study")
    return run, cfg, (a, b)


def files(tmp_path, sids, summary=SUMMARY):
    s = tmp_path / "summary.txt"
    s.write_text(summary, encoding="utf-8")
    w = tmp_path / "writeups.json"
    w.write_text(json.dumps({"results": [{"study_id": sid, "title": f"Write-up {sid}", "summary": f"Summary {sid}."}
                                         for sid in sids]}), encoding="utf-8")
    return s, w


def render(tmp_path, run, sids, summary=SUMMARY, topic="seo"):
    s, w = files(tmp_path, sids, summary)
    return cli.main(["--topic", topic, "search", "render", run, "--summary", str(s), "--writeups", str(w)])


def page_path(cfg, run, ext="html"):
    return cfg.reports_dir / "searches" / f"{run}.{ext}"


def test_render_writes_page_and_stores_texts(workspace, tmp_path, capsys):
    run, cfg, sids = graded_run(workspace)
    assert render(tmp_path, run, sids) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["page"] == str(page_path(cfg, run)) and page_path(cfg, run).is_file()
    assert out["studies"] == 2 and out["markdown"] == str(page_path(cfg, run, "md")) and out["rejected_writeups"] == []
    meta = search.read_meta(config.search_dir(cfg.data_dir, "seo", run))
    assert meta["summary"] == SUMMARY and meta["page"] == out["page"]
    assert {int(k): v["title"] for k, v in meta["writeups"].items()} == {s: f"Write-up {s}" for s in sids}


def test_page_content(workspace, tmp_path):
    run, cfg, (a, b) = graded_run(workspace)
    with closing(store.connect(cfg.db_path)) as conn:   # a third candidate that is still waiting
        store.insert_items(conn, [make_item(url="https://z.example/new")])
    s, w = files(tmp_path, (a, b))
    search.add(cfg, run, [], ["title tag study", "h1 study"])
    search.render(cfg, run, s.read_text(encoding="utf-8"), w.read_text(encoding="utf-8"))
    text = page_path(cfg, run).read_text(encoding="utf-8")
    assert "<h1>Do &lt;b&gt;title&lt;/b&gt; tags &amp; H1s differ?</h1>" in text
    assert "Search: 5 candidates, 2 studies graded, 2 rejected" in text
    assert "What these say" in text and "<p>Two studies measured clicks.</p>" in text
    assert "<p>They disagree on size &amp; scope.</p>" in text
    assert f'href="#s{a}"' in text and f'href="#s{b}"' in text and f'id="s{b}"' in text
    assert "Write-up" in text and "sample too small" in text and "D" in text   # disputed: both grades shown
    assert '<details class="ee-rejected"><summary>Not studies (2)</summary>' in text
    assert "https://x.example/opinion" in text and "an opinion piece, no data" in text
    assert "Opinion &lt;i&gt;piece&lt;/i&gt;" in text and "news, not a study" in text
    assert "Queries" in text and "title tag study" in text and "h1 study" in text
    assert "Not in the topic yet" in text
    assert "Evidence Echo" in text and "SEO" in text and "Search" in text
    assert text.count("<details class=\"ee-rejected\" open") == 0


def test_exercise_topic_disclaimer(workspace, tmp_path):
    run, cfg, sids = graded_run(workspace, "fit", "exercise")
    assert render(tmp_path, run, sids, topic="fit") == 0
    text = page_path(cfg, run).read_text(encoding="utf-8")
    assert cfg.topic.grading["disclaimer"] and 'class="ee-disclaimer"' in text


def test_summary_word_limit(workspace, tmp_path, capsys):
    run, cfg, sids = graded_run(workspace)
    assert render(tmp_path, run, sids, summary=" ".join(["word"] * 201)) == 1
    assert "fix the file and run again" in capsys.readouterr().err
    assert render(tmp_path, run, sids, summary="  \n ") == 1
    assert not page_path(cfg, run).exists()
    assert search.read_meta(config.search_dir(cfg.data_dir, "seo", run))["summary"] is None
    assert render(tmp_path, run, sids, summary=" ".join(["word"] * 200)) == 0


def test_bad_writeups_are_reported_not_applied(workspace, tmp_path, capsys):
    run, cfg, (a, b) = graded_run(workspace)
    assert render(tmp_path, run, (a, 9999)) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in out["rejected_writeups"]] == ["9999"]
    text = page_path(cfg, run).read_text(encoding="utf-8")
    assert f"Write-up {a}" in text and "Write-up 9999" not in text


def test_markdown_twin_rules(workspace, tmp_path, monkeypatch):
    run, cfg, sids = graded_run(workspace)
    assert render(tmp_path, run, sids) == 0                      # default (md_reports on): .md next to the .html
    md = page_path(cfg, run, "md")
    assert md.is_file() and page_path(cfg, run).is_file()
    text = md.read_text(encoding="utf-8")
    assert text.startswith("# Do &lt;b>title&lt;/b> tags & H1s differ?")
    assert "<b>" not in text and "Not in the topic yet" in text and "Not studies (2)" in text
    assert "https://x.example/opinion" in text and "Write-up" in text
    page_path(cfg, run).unlink()
    md.unlink()
    monkeypatch.setenv("EV_ECHO_MD_REPORTS", "off")              # md_reports off, HTML on: only .html
    assert render(tmp_path, run, sids) == 0
    assert page_path(cfg, run).is_file() and not md.exists()
    page_path(cfg, run).unlink()
    monkeypatch.setenv("EV_ECHO_HTML_REPORTS", "off")            # html off: only .md
    assert render(tmp_path, run, sids) == 0
    assert md.is_file() and not page_path(cfg, run).exists()
    meta = search.read_meta(config.search_dir(cfg.data_dir, "seo", run))
    assert meta["page"] == str(md)


def test_links_from_disk(workspace, tmp_path):
    run, cfg, sids = graded_run(workspace)
    assert render(tmp_path, run, sids) == 0
    text = page_path(cfg, run).read_text(encoding="utf-8")
    assert 'href="../seo.html"' in text
    assert (page_path(cfg, run).parent / "../seo.html").resolve() == (cfg.reports_dir / "seo.html").resolve()
    assert "overview.html" in text


def test_list_runs(workspace, capsys):
    seed_topic(workspace, "seo")
    seed_topic(workspace, "cro")
    r1 = search.start(config.load("seo"), "first question", NOW)["run"]
    r2 = search.start(config.load("seo"), "second question", datetime(2026, 10, 27, 9, 5, tzinfo=timezone.utc))["run"]
    r3 = search.start(config.load("cro"), "cro question", datetime(2026, 10, 28, 9, 5, tzinfo=timezone.utc))["run"]
    broken = workspace / config.SEARCHES_DIR / "seo" / "broken-run"
    broken.mkdir()
    (broken / search.META).write_text("{not json", encoding="utf-8")
    assert cli.main(["search", "list"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["run"] for r in rows] == [r3, r2, r1, "broken-run"]
    assert rows[0] == {"run": r3, "topic": "cro", "question": "cro question", "created": "2026-10-28T09:05:00Z",
                       "status": "open", "status_at": "2026-10-28T09:05:00Z", "studies": None, "page": None}
    assert rows[3]["status"] == "broken" and rows[3]["topic"] == "seo"
    assert cli.main(["--topic", "seo", "search", "list"]) == 0
    assert [r["run"] for r in json.loads(capsys.readouterr().out)] == [r2, r1, "broken-run"]
    assert cli.main(["search", "list", "--topic", "cro"]) == 0
    assert [r["run"] for r in json.loads(capsys.readouterr().out)] == [r3]
    assert search.list_runs(workspace / "nowhere") == []


def test_list_shows_page_and_studies_after_render(workspace, tmp_path, capsys):
    run, cfg, sids = graded_run(workspace)
    assert render(tmp_path, run, sids) == 0
    capsys.readouterr()
    row = search.list_runs(cfg.data_dir, "seo")[0]
    assert row["studies"] == 2 and row["page"] == str(page_path(cfg, run))


def test_topic_untouched_by_render(workspace, tmp_path):
    run, cfg, sids = graded_run(workspace)
    topic_dir = workspace / "topics" / "seo"
    before = (digest(topic_dir / "echo.db"), digest(topic_dir / "topic.json"))
    assert render(tmp_path, run, sids) == 0
    assert (digest(topic_dir / "echo.db"), digest(topic_dir / "topic.json")) == before
    assert not (cfg.reports_dir / "seo.html").exists()
    assert sorted(p.name for p in cfg.reports_dir.rglob("*") if p.is_file()) == [f"{run}.html", f"{run}.md"]


def test_status_text():
    base = dict(run="r", topic_id="seo", topic_name="SEO", question="q", date="2026-10-26", status="open",
                status_date=None, candidates=3, graded=3, rejected=[], entries=[], summary="s", queries=[],
                disclaimer=None, kept=None)
    P = search_page.SearchPage
    assert search_page.status_text(P(**base)) == "Not in the topic yet"
    assert search_page.status_text(P(**{**base, "status": "kept", "status_date": "2026-10-27", "kept": 3})) \
        == "Kept on 2026-10-27"
    assert search_page.status_text(P(**{**base, "status": "kept", "status_date": "2026-10-27", "kept": 2})) \
        == "Kept on 2026-10-27 (2 of 3 studies)"
    assert search_page.status_text(P(**{**base, "status": "dropped", "status_date": "2026-10-27"})) == "Dropped"
