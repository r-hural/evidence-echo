"""Found-by-search studies on the cards, the Source filter, the Sources page and the topic page's Searches list."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from evidence_echo import api, search
from evidence_echo.core import config, store
from evidence_echo.render import html as html_render
from evidence_echo.render import markdown, model, obsidian
from evidence_echo.render import sources_page as sp
from tests.conftest import make_item
from tests.evidence_fixtures import seed_topic
from tests.html_fixtures import entry, topic_model, topic_study
from tests.obsidian_fixtures import study as obsidian_study
from tests.obsidian_fixtures import topic as obsidian_topic
from tests.test_render_model import T2, _study

QUESTION = "Do AI Overviews differ by language?"
NOW = datetime(2026, 10, 26, 9, 5, tzinfo=timezone.utc)


def search_source(n, question=QUESTION):
    return {"type": "once", "name": f"Search: run-{n} #1", "url": f"https://e.com/s{n}", "found_by": "search",
            "search_run": f"run-{n}", "search_question": question}


# --- the card line ---------------------------------------------------------------------------

def test_card_shows_found_by_search():
    plain, found = entry(1), entry(2, found_by_search=QUESTION)
    assert f"<b>Found by search:</b> {QUESTION}" in html_render.card(found)
    assert "Found by search" not in html_render.card(plain)
    assert f"Found by search: {QUESTION}  " in markdown.entry_lines(found)
    assert not any("Found by search" in line for line in markdown.entry_lines(plain))


def test_obsidian_note_shows_found_by_search(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    obsidian.export(obsidian_topic([obsidian_study(1, found_by_search=QUESTION), obsidian_study(2)]), vault)
    notes = [p.read_text(encoding="utf-8") for p in (vault / "Evidence Echo").rglob("studies/*.md")]
    assert len(notes) == 2
    assert sum(f"Found by search: {QUESTION}" in t for t in notes) == 1
    assert sum("Found by search" in t for t in notes) == 1


def test_model_sets_found_by_search_from_the_items_source(db, topic):
    a = _study(db, 1, source="Search: run-1 #1")
    b = _study(db, 2, source="Example Blog", text=T2, numbers=["9%"], quotes=[{"number": "9%", "quote": T2}],
               primary="https://two.example.com/b")
    topic = replace(topic, sources=[*topic.sources, search_source(1)])
    assert model.search_questions(topic) == {"Search: run-1 #1": QUESTION}
    rep = model.build_report(db, topic, {}, NOW)
    by_id = {e.study_id: e for e in rep.new}
    assert by_id[a].found_by_search == QUESTION and by_id[b].found_by_search is None
    assert "Found by search" not in html_render.card(by_id[b])


# --- the Source filter -----------------------------------------------------------------------

def _filter_topic():
    studies = [topic_study(entry(n)) for n in (1, 2, 3, 4)]
    names = ["Example Blog", "Search: run-1 #1", "Search: run-2 #1", "Search: run-3 #1"]
    sources = [model.SourceSummary(name, [n], 0, 0) for n, name in enumerate(names, start=1)]
    return topic_model(studies, sources=sources, search_sources=frozenset(names[1:]))


def test_source_filter_groups_search_sources():
    t = _filter_topic()
    options = html_render._source_options(t)
    assert html_render.SEARCH_SOURCE_SLUG == "found-by-search"
    assert [v for v, _ in options] == ["found-by-search", "example-blog"]
    assert options[0][1] == "Search (3)"
    rows = {r["id"]: r["sources"] for r in html_render.filter_data(t)["studies"]}
    assert rows == {1: ["example-blog"], 2: ["found-by-search"], 3: ["found-by-search"], 4: ["found-by-search"]}
    page = html_render.topic_page(t, "o.html", "r.html")
    select = page.split('<select name="source">')[1].split("</select>")[0]
    assert '<option value="found-by-search">Search (3)</option>' in select and "Search: run-" not in select


def test_source_filter_without_search_sources_is_unchanged():
    t = topic_model([topic_study(entry(1)), topic_study(entry(2))],
                    sources=[model.SourceSummary("A", [1], 0, 0), model.SourceSummary("B", [2], 0, 0)])
    assert [v for v, _ in html_render._source_options(t)] == ["a", "b"]


# --- the Sources page ------------------------------------------------------------------------

def test_sources_page_one_search_row(db):
    s1, s2, s3 = (store.create_study(db, "A", {}, None) for _ in range(3))
    for n, (name, sid) in enumerate([("Blog", s1), ("Search: run-1 #1", s2), ("Search: run-2 #1", s3)], 1):
        iid = store.insert_item(db, make_item(url=f"https://e.com/{n}", source_name=name, source_type="once"))
        db.execute("UPDATE items SET study_id=?, state='reported' WHERE id=?", (sid, iid))
    db.execute("INSERT INTO reports (path, rendered_at) VALUES ('/r/1.md', '2026-10-20T09:00:00+00:00')")
    for sid in (s1, s2, s3):
        db.execute("INSERT INTO report_entries (report_id, study_id, title, summary) VALUES (1,?,?,?)",
                   (sid, "t", "s"))
    db.commit()
    cfg = SimpleNamespace(id="t", name="T", sources=[
        {"type": "rss", "name": "Blog", "url": "https://e.com/feed"}, search_source(1), search_source(2)])
    page = sp.build_sources(db, cfg, datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc))
    assert [r.name for r in page.rows] == ["Blog", "Found by search"]
    row = page.rows[1]
    assert (row.all_studies, row.all_fetched, row.last_studies, row.last_fetched) == (2, 2, 2, 2)
    out = sp.sources_html(page, "o.html", "t.html", "r.html")
    assert out.count("Found by search") == 1 and "Search: run-" not in out
    assert 'href="t.html#source=found-by-search"' in out


def test_sources_page_without_search_sources_has_no_row(db):
    cfg = SimpleNamespace(id="t", name="T", sources=[{"type": "rss", "name": "Blog", "url": "https://e.com/feed"}])
    assert [r.name for r in sp.build_sources(db, cfg, NOW).rows] == ["Blog"]


# --- the Searches list -----------------------------------------------------------------------

def _run(question, status, **over):
    return {"run": "20261026-0905-x", "topic": "seo", "question": question, "created": "2026-10-26T09:05:00+00:00",
            "status": status, "status_at": "2026-10-27T10:00:00+00:00", "studies": 2, "page": None, **over}


def test_topic_page_lists_searches(tmp_path):
    (tmp_path / "searches").mkdir()
    for name in ("a.html", "b.md", "b.html"):
        (tmp_path / "searches" / name).write_text("x")
    reports = [model.ReportLink("2026-10-20", str(tmp_path / "searches" / "a.html"))]
    runs = [_run("Open <q>?", "open", page=str(tmp_path / "searches" / "a.html")),
            _run("Kept one", "kept", page=str(tmp_path / "searches" / "b.md"), created="2026-10-28T09:05:00+00:00")]
    t = topic_model([topic_study(entry(1))], reports=reports, searches=runs)
    out = html_render.topic_page(t, "o.html", "r.html", "seo.html", tmp_path)
    assert "<summary>Searches (2)</summary>" in out and "<details open" not in out
    assert out.index("Reports (1)") < out.index("Searches (2)")
    assert 'href="searches/a.html">Open &lt;q&gt;?</a>' in out
    assert 'href="searches/b.html">Kept one</a>' in out
    assert out.index("Kept one") < out.index("Open &lt;q&gt;?")   # newest first, whatever the input order
    assert '<li><span class="meta">2026-10-28</span> <a href="searches/b.html">Kept one</a></li>' in out
    listed = out.split("<summary>Searches (2)</summary>")[1].split("</details>")[0]
    assert "Not in the topic yet" not in listed and "Kept on " not in listed   # date and question only


def test_topic_page_search_list_edge_cases(tmp_path):
    runs = [_run(None, "broken"), _run("Dropped one", "dropped", page=str(tmp_path / "gone.html"))]
    out = html_render.topic_page(topic_model([topic_study(entry(1))], searches=runs), "o", "r", "seo.html", tmp_path)
    assert "Searches (1)" in out and "<li><span class=\"meta\">2026-10-26</span> Dropped one</li>" in out
    assert "gone.html" not in out
    broken = topic_model([topic_study(entry(1))], searches=[_run(None, "broken")])
    assert "Searches (" not in html_render.topic_page(broken, "o", "r")
    assert "Searches (" not in html_render.topic_page(topic_model([topic_study(entry(1))]), "o", "r")


def test_topic_model_reads_runs(workspace):
    seed_topic(workspace, "seo")
    cfg = config.load("seo")
    assert api._topic_model(cfg).searches == []
    search.start(cfg, QUESTION, NOW)
    broken = workspace / config.SEARCHES_DIR / "seo" / "broken-run"
    broken.mkdir(parents=True)
    (broken / "search.json").write_text("{not json", encoding="utf-8")
    t = api._topic_model(cfg)
    assert t.searches == search.list_runs(cfg.data_dir, "seo") and len(t.searches) == 2
    assert {r["status"] for r in t.searches} == {"open", "broken"}
    assert t.search_sources == frozenset()


def test_card_links_the_answer_page():
    found = entry(2, found_by_search=QUESTION, found_by_search_run="20261026-0905-q")
    link = f'<a href="searches/20261026-0905-q.html">{QUESTION}</a>'
    assert f"<b>Found by search:</b> {link}" in html_render.card(found)
    assert f'<a href="20261026-0905-q.html">{QUESTION}</a>' in html_render.card(found, searches_href="")
    no_run = html_render.card(entry(3, found_by_search=QUESTION))   # a source without its run id: plain text
    assert f"<b>Found by search:</b> {QUESTION}</p>" in no_run


def test_model_sets_the_search_run(db, topic):
    a = _study(db, 1, source="Search: run-1 #1")
    topic = replace(topic, sources=[*topic.sources, search_source(1)])
    assert model.search_runs(topic) == {"Search: run-1 #1": "run-1"}
    assert {e.study_id: e for e in model.build_report(db, topic, {}, NOW).new}[a].found_by_search_run == "run-1"
