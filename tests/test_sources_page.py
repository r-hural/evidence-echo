"""The per-topic Sources page: counts, last-report window, broken marker, links, order."""
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from evidence_echo.core import store
from evidence_echo.render import html as html_render
from evidence_echo.render import sources_page as sp
from tests.conftest import make_item
from tests.html_fixtures import entry, refs, report, topic_model, topic_study

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
R1, R2, R3 = "2026-10-01T09:00:00+00:00", "2026-10-08T09:00:00+00:00", "2026-10-15T09:00:00+00:00"


def _study(db):
    return store.create_study(db, "A", {}, None)


def _item(db, n, source, fetched="2026-10-02T00:00:00+00:00", study=None, kind="rss", state="reported"):
    url = f"https://e.com/{source}/{n}".replace(" ", "-")
    item_id = store.insert_item(db, make_item(url=url, source_name=source, source_type=kind,
                                              fetched_at=fetched))
    if study:
        db.execute("UPDATE items SET study_id=?, state=? WHERE id=?", (study, state, item_id))
        db.commit()
    return item_id


def _report(db, rendered_at, study_ids, new=True):
    cur = db.execute("INSERT INTO reports (path, rendered_at) VALUES (?, ?)",
                     (f"/r/{rendered_at}.md", rendered_at))
    for sid in study_ids:
        db.execute("INSERT INTO report_entries (report_id, study_id, title, summary) VALUES (?,?,?,?)",
                   (cur.lastrowid, sid, f"Study {sid}", "s" if new else None))
    db.commit()
    return cur.lastrowid


def _cfg(*sources):
    return SimpleNamespace(id="t", name="T", sources=list(sources))


def _rss(name, **extra):
    return {"type": "rss", "name": name, "url": f"https://e.com/{name}/feed", **extra}


def _rows(page):
    return {r.name: r for r in page.rows}


def test_all_reports_counts(db):
    s1, s2 = _study(db), _study(db)
    _item(db, 1, "X", study=s1)
    _item(db, 2, "X", study=s2)
    for n in (3, 4, 5):
        _item(db, n, "X")
    _item(db, 6, "X", study=_study(db))   # a study no report showed
    _report(db, R1, [s1, s2])
    row = _rows(sp.build_sources(db, _cfg(_rss("X")), NOW))["X"]
    assert (row.all_studies, row.all_fetched) == (2, 6)


def test_item_not_yet_on_the_topic_page_does_not_count(db):
    """A matched item (new coverage waiting for the next report) is not on the topic page yet, so the
    Sources page neither counts its study for that source nor links the source to an empty filter."""
    s1 = _study(db)
    _item(db, 1, "X", study=s1)
    _item(db, 2, "Y", fetched="2026-10-03T00:00:00+00:00", study=s1, state="matched")
    _report(db, R1, [s1])
    rows = _rows(sp.build_sources(db, _cfg(_rss("X"), _rss("Y")), NOW))
    assert rows["X"].all_studies == 1
    assert (rows["Y"].all_studies, rows["Y"].all_fetched) == (0, 1)


def test_last_report_counts_only_new_studies(db):
    s_old, s_new = _study(db), _study(db)
    _item(db, 1, "X", study=s_old)
    _report(db, R1, [s_old])
    _report(db, R2, [])
    _item(db, 2, "X", fetched="2026-10-12T00:00:00+00:00", study=s_old)   # new coverage of s_old
    _item(db, 3, "X", fetched="2026-10-12T00:00:00+00:00", study=s_new)
    r3 = _report(db, R3, [s_old, s_new])
    db.execute("UPDATE report_entries SET summary=NULL WHERE report_id=? AND study_id=?", (r3, s_old))
    db.commit()
    rows = _rows(sp.build_sources(db, _cfg(_rss("X"), _rss("Y")), NOW))
    assert rows["X"].last_studies == 1 and rows["X"].all_studies == 2
    assert rows["Y"].last_studies == 0


def test_seen_before_study_alone_is_not_a_last_report_study(db):
    s_old = _study(db)
    _item(db, 1, "X", study=s_old)
    _report(db, R1, [s_old])
    _item(db, 2, "X", fetched="2026-10-12T00:00:00+00:00", study=s_old)
    _report(db, R3, [s_old], new=False)
    row = _rows(sp.build_sources(db, _cfg(_rss("X")), NOW))["X"]
    assert (row.last_studies, row.all_studies) == (0, 1)


def test_merged_study_counts_once_through_its_survivor(db):
    a, b = _study(db), _study(db)
    _item(db, 1, "X", study=a)
    _item(db, 2, "X", study=b)
    _report(db, R1, [a, b])
    db.execute("UPDATE studies SET merged_into=? WHERE id=?", (a, b))
    db.commit()
    assert _rows(sp.build_sources(db, _cfg(_rss("X")), NOW))["X"].all_studies == 1


def test_last_report_fetched_window(db):
    _report(db, R1, [])
    _report(db, R2, [])
    _item(db, 1, "X", fetched="2026-10-03T00:00:00+00:00")     # before the previous report
    _item(db, 2, "X", fetched="2026-10-09T00:00:00+00:00")     # between R2 and R3
    _item(db, 3, "X", fetched="2026-10-20T00:00:00+00:00")     # after the newest report
    _report(db, R3, [])
    row = _rows(sp.build_sources(db, _cfg(_rss("X")), NOW))["X"]
    assert row.last_fetched == 1 and row.all_fetched == 3


def test_last_report_fetched_with_one_report_counts_everything_up_to_it(db):
    _item(db, 1, "X", fetched="2026-09-01T00:00:00+00:00")
    _item(db, 2, "X", fetched="2026-09-30T00:00:00+00:00")
    _item(db, 3, "X", fetched="2026-10-20T00:00:00+00:00")
    _report(db, R1, [])
    assert _rows(sp.build_sources(db, _cfg(_rss("X")), NOW))["X"].last_fetched == 2


def test_study_from_two_sources_counts_for_both(db):
    s = _study(db)
    _item(db, 1, "X", study=s)
    _item(db, 2, "Y", study=s)
    _report(db, R1, [s])
    rows = _rows(sp.build_sources(db, _cfg(_rss("X"), _rss("Y")), NOW))
    assert (rows["X"].all_studies, rows["Y"].all_studies) == (1, 1)
    assert (rows["X"].last_studies, rows["Y"].last_studies) == (1, 1)


def _state(db, name, kind="rss", **fields):
    store.record_fetch(db, name, kind, new=0, error=None)
    for key, value in fields.items():
        db.execute(f"UPDATE sources_state SET {key}=? WHERE name=?", (value, name))
    db.commit()


def _html(page, topic_href="t.html"):
    return sp.sources_html(page, "../overview.html", topic_href, "report.html")


def test_broken_marker_only_for_failures(db):
    _state(db, "Blocked", blocked=1, last_error="HTTP 403", consecutive_errors=1)
    _state(db, "Failing", consecutive_errors=2, failing_since=R1, last_error="HTTP 500")
    _state(db, "Quiet")
    _state(db, "Once", kind="once", done=1)
    _item(db, 1, "Fine", fetched="2026-10-22T00:00:00+00:00")
    _state(db, "Fine")
    cfg = _cfg(_rss("Blocked"), _rss("Failing"), _rss("Quiet"),
               {"type": "once", "name": "Once", "url": "https://e.com/o"}, _rss("Fine"))
    page = sp.build_sources(db, cfg, NOW, horizon_days=7)
    rows = _rows(page)
    assert rows["Blocked"].broken == "HTTP 403" and rows["Failing"].broken == "HTTP 500"
    assert rows["Quiet"].broken is None and rows["Quiet"].quiet_days == 7
    assert rows["Once"].broken is None and rows["Once"].quiet_days is None
    assert rows["Fine"].broken is None and rows["Fine"].quiet_days is None
    text = _html(page)
    assert text.count("∅") == 2 and "HTTP 403" in text and "no entries in 7 days" in text
    quiet = re.search(r"<tr><td><b>Quiet</b>.*?</tr>", text, re.S).group(0)
    assert "ee-broken" not in quiet and "no entries in 7 days" in quiet


def test_removed_source_listed_last_greyed(db):
    s = _study(db)
    _item(db, 1, "Gone", study=s)
    _item(db, 2, "Kept")
    _report(db, R1, [s])
    page = sp.build_sources(db, _cfg(_rss("Kept"), _rss("Fresh")), NOW)
    assert [r.name for r in page.rows] == ["Kept", "Fresh", "Gone"]
    assert _rows(page)["Gone"].removed and not _rows(page)["Kept"].removed
    text = _html(page)
    assert text.index("No longer in the topic") < text.index(">Gone</a></b>")
    assert 'class="ee-gone"' in text


def test_links(db):
    s1, s2 = _study(db), _study(db)
    _item(db, 1, "Field Notes (Jo Example)", study=s1)
    _item(db, 2, "Plain", study=s2)
    _report(db, R1, [s1, s2])
    cfg = _cfg(_rss("Field Notes (Jo Example)", site_url="https://field-notes.example/"),
               _rss("Plain", site_url="https://e.com/Plain/feed"), _rss("Nolink"))
    text = _html(sp.build_sources(db, cfg, NOW))
    # a name opens the topic page filtered by the source; without studies it is plain text
    assert '<td><b><a href="t.html#source=field-notes-jo-example">Field Notes (Jo Example)</a></b></td>' in text
    assert "<td><b>Nolink</b></td>" in text and "#source=nolink" not in text
    notes = re.search(r"<tr><td><b><a[^>]*>Field Notes.*?</tr>", text, re.S).group(0)
    assert re.search(r'<td class="ee-url"><span class="ee-site"><a [^>]*>field-notes\.example</a></span>'
                     r'<span class="ee-feed">', notes) and "ee-none" not in notes
    plain = re.search(r"<tr><td><b><a[^>]*>Plain</a></b>.*?</tr>", text, re.S).group(0)
    assert plain.count("<a ") == 2 and plain.count('class="ee-none"') == 1   # one URL: Feed only
    assert re.search(r'ee-site"><span class="ee-none">—</span></span>'
                     r'<span class="ee-feed"><a [^>]*>e\.com/Plain/feed</a>', plain)


def test_page_and_linkedin_go_in_site_column(db):
    cfg = _cfg({"type": "linkedin", "name": "Jane", "slug": "jane-doe"},
               {"type": "page", "name": "Listing", "url": "https://e.com/list"})
    text = _html(sp.build_sources(db, cfg, NOW))
    for name, short in (("Jane", "linkedin.com/in/jane-doe"), ("Listing", "e.com/list")):
        row = re.search(rf"<tr><td><b>{name}</b>.*?</tr>", text, re.S).group(0)
        assert re.search(rf'ee-site"><a [^>]*>{short}</a></span><span class="ee-feed"><span class="ee-none">',
                         row)


def test_linkedin_row_shows_profile_url(db):
    cfg = _cfg({"type": "linkedin", "name": "Jane", "slug": "jane-doe"})
    page = sp.build_sources(db, cfg, NOW)
    assert page.rows[0].url == "https://www.linkedin.com/in/jane-doe/"


def test_order_and_header(db):
    sa, sb = _study(db), _study(db)
    _item(db, 1, "B", study=sa)
    _item(db, 2, "B", study=sb)
    _item(db, 3, "A", study=_study(db))
    _item(db, 4, "A")
    _item(db, 5, "C")
    _item(db, 6, "C")
    _item(db, 7, "C")
    _report(db, R1, [sa, sb])
    page = sp.build_sources(db, _cfg(_rss("A"), _rss("B"), _rss("C"), _rss("D")), NOW)
    assert [r.name for r in page.rows] == ["B", "C", "A", "D"]
    assert "4 sources · 3 never produced a study" in _html(page)


def test_no_reports_yet(db):
    _item(db, 1, "X")
    text = _html(sp.build_sources(db, _cfg(_rss("X")), NOW))
    assert "0 / 1" in text and "1 source · 1 never produced a study" in text
    db.execute("DELETE FROM items")
    db.commit()
    empty = _html(sp.build_sources(db, _cfg(), NOW))
    assert "No sources in this topic yet." in empty


def test_page_links_back_to_overview_and_topic(db):
    found = refs(_html(sp.build_sources(db, _cfg(_rss("X")), NOW)))
    assert "../overview.html" in found.hrefs and "t.html" in found.hrefs


def test_topic_page_and_report_link_to_sources_page(tmp_path):
    reports_dir = tmp_path / "R" / "seo"
    page = sp.SourcesPage("seo", "SEO", "2026-10-16T09:00:00+00:00", [])
    t = topic_model([topic_study(entry(1))])
    out = html_render.write_report(report(), t, reports_dir, tmp_path / "R",
                                   reports_dir / "2026-10-16-report.md", sources_page=page)
    assert out["sources"] == str(reports_dir / "seo-sources.html")
    assert (reports_dir / "seo-sources.html").exists()
    for name in ("seo.html", "2026-10-16-report.html"):
        text = (reports_dir / name).read_text(encoding="utf-8")
        assert '<a href="seo-sources.html">Sources</a>' in text
    sources = (reports_dir / "seo-sources.html").read_text(encoding="utf-8")
    assert '<a href="seo.html">All studies</a>' in sources and "../overview.html" in sources


def test_without_a_sources_page_no_link_is_written(tmp_path):
    reports_dir = tmp_path / "R" / "seo"
    out = html_render.write_report(report(), topic_model([topic_study(entry(1))]), reports_dir,
                                   tmp_path / "R", reports_dir / "2026-10-16-report.md")
    assert "sources" not in out
    assert "Sources" not in (reports_dir / "seo.html").read_text(encoding="utf-8")


def test_one_site_feed_column_and_short_columns_do_not_wrap(db):
    cfg = _cfg(_rss("Field Notes", site_url="https://field-notes.example/"))
    text = _html(sp.build_sources(db, cfg, NOW))
    assert "<th>Site/Feed</th>" in text and "<th>Site</th>" not in text and "<th>Feed</th>" not in text
    assert '<th class="ee-nw">Kind</th>' in text and '<th class="ee-nw">Last fetched</th>' in text
    assert '<td class="ee-nw">rss</td>' in text
