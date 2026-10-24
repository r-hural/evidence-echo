# tests/test_render_model.py
import dataclasses
import inspect
import json
from pathlib import Path
from datetime import datetime, timezone

from evidence_echo.core import interfaces, store
from evidence_echo.render import html, model, obsidian
from evidence_echo.stages import judge, match, report, verify
from tests.conftest import seed_new_study
from tests.test_judge import TEXT, VERDICT, _seed_extracted

NOW = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)
T2 = "Rankings fell 9% across forty sites in March."
T3 = "Click-through rose 31% on forty test pages in May."


def _study(db, n, *, text=TEXT, numbers=("4.2%", "12000"), quotes=VERDICT["quotes"],
           primary="https://one.example.com/a", skeptic="A", flags=(), note="skeptic note",
           source=None, facts=None, **over):
    iid = _seed_extracted(db, url=f"https://example.com/m{n}", text=text)
    if source:
        db.execute("UPDATE items SET source_name=? WHERE id=?", (source, iid))
        db.commit()
    f = dict(VERDICT["facts"], match_keys={"key_numbers": list(numbers)}, **(facts or {}))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid), facts=f,
                                                 primary_url=primary, quotes=quotes, **over)]}))
    match.fixed_pass(db)
    if store.get_item(db, iid)["study_id"] is None:     # a series candidate waits: a new study
        match.apply(db, json.dumps({"results": [{"id": interfaces.opaque_id(iid), "same_as": None,
                                                 "reason": "another edition"}]}))
    verify.apply(db, json.dumps({"results": [{"id": interfaces.opaque_id(iid), "tier": skeptic,
                                              "red_flags": list(flags), "note": note}]}))
    return store.get_item(db, iid)["study_id"]


def _render(db, tmp_path, topic, results=()):
    return report.render(db, json.dumps({"results": list(results)}), tmp_path, "en", topic)


# --- build_report -------------------------------------------------------------------------

def test_build_report_sections_and_order(db, topic):
    plain = _study(db, 1)
    disputed = _study(db, 2, text=T2, numbers=["9%"], quotes=None,
                      primary="https://two.example.com/b")              # no_quote dispute
    zeta = _study(db, 3, text=T3, numbers=["31%"], quotes=[{"number": "31%", "quote": T3}],
                  primary="https://three.example.com/c", facts={"series": "Zeta Survey",
                                                                "editions": ["2025"]})
    rep = model.build_report(db, topic, {plain: {"title": "Plain", "summary": "P"}}, NOW)
    assert (rep.topic_id, rep.topic_name, rep.date) == ("seo", topic.name, "2026-10-16")
    assert [e.study_id for e in rep.new] == [zeta, plain]               # series first
    assert [e.study_id for e in rep.disputed] == [disputed]
    assert rep.seen == [] and rep.source_warnings == []
    first = rep.new[1]
    assert (first.title, first.summary, first.tier) == ("Plain", "P", "A")
    assert first.coverage == [model.Link("Example Blog", "https://example.com/m1")]
    assert first.published == "2026-08-10T00:00:00+00:00"
    assert first.view["provenance"]["relation"] == "agency"
    assert first.canonical_name == "Title tag rewrites"                 # studies.canonical_name
    assert rep.new[0].series == "Zeta Survey" and rep.new[0].editions == ["2025"]
    d = rep.disputed[0]
    assert d.disputed and d.no_quote_only
    assert d.dispute_reason == "no quoted number found in the text"
    assert d.skeptic_notes == ["skeptic note"]
    assert d.title == "Title tag rewrites"                               # no entry: default text
    assert d.summary == "+4.2% organic clicks on variant group"


def test_build_report_grade_gap_carries_the_skeptic_tier(db, topic):
    sid = _study(db, 1, skeptic="C", note="no control group")
    [d] = model.build_report(db, topic, {}, NOW).disputed
    assert d.study_id == sid and d.skeptic_tiers == ["C"] and not d.no_quote_only
    assert d.dispute_reason == "grades two tiers apart"


def test_build_report_seen_before(db, tmp_path, topic):
    sid = _study(db, 1)
    _render(db, tmp_path, topic, [{"study_id": sid, "title": "First title", "summary": "S"}])
    _study(db, 2, skeptic="C", note="no control group")                 # joins: same primary url
    rep = model.build_report(db, topic, {}, NOW)
    assert rep.new == [] and rep.disputed == []
    [seen] = rep.seen
    assert (seen.study_id, seen.first_title) == (sid, "First title")
    assert seen.disputed and seen.skeptic_tiers == ["C"] and seen.skeptic_notes == ["no control group"]
    assert seen.coverage == [model.Link("Example Blog", "https://example.com/m2")]
    assert len(seen.first_reported) == 10


def test_build_report_source_warnings(db, topic):
    for _ in range(5):
        store.record_fetch(db, "Dead", "rss", new=0, error="HTTP 404")
    store.record_fetch(db, "Flaky", "rss", new=0, error="timeout")
    store.record_fetch(db, "Elsewhere", "rss", new=0, error="timeout")   # not this topic's
    t = dataclasses.replace(topic, sources=[{"type": "rss", "name": "Dead", "url": "https://d/"},
                                            {"type": "rss", "name": "Flaky", "url": "https://f/"}])
    warnings = model.build_report(db, t, {}, NOW).source_warnings
    assert len(warnings) == 2
    assert warnings[0].startswith("Dead (failing since ") and warnings[1] == "Flaky (timeout)"


# --- what render records --------------------------------------------------------------------

def test_render_records_summaries_for_new_studies_only(db, tmp_path, topic):
    a = _study(db, 1)
    b = _study(db, 2, text=T2, numbers=["9%"], quotes=[{"number": "9%", "quote": T2}],
               primary="https://two.example.com/b")
    _render(db, tmp_path, topic, [{"study_id": a, "title": "A title", "summary": "A summary"},
                                  {"study_id": b, "title": "rejected: no summary"}])
    _study(db, 3)                                                        # joins a: seen before
    seed_new_study(db)                                                   # a report needs a new study
    _render(db, tmp_path, topic)
    rows = [(r["study_id"], r["title"], r["summary"]) for r in store.report_history(db)]
    assert rows[:2] == [(a, "A title", "A summary"),
                        (b, "Title tag rewrites", "+4.2% organic clicks on variant group")]
    assert rows[-1] == (a, "A title", None) and rows[2][2] is not None   # new with summary, seen without


def test_render_with_model_returns_the_report(db, tmp_path, topic):
    sid = _study(db, 1)
    out, rep, previous = report.render_with_model(db, json.dumps({"results": [
        {"study_id": sid, "title": "T", "summary": "S"}]}), tmp_path, "en", topic)
    assert out["applied"] == 1 and [e.study_id for e in rep.new] == [sid]
    assert previous is None


# --- build_topic ----------------------------------------------------------------------------

def test_build_topic_empty(db, topic):
    t = model.build_topic(db, topic, NOW)
    assert (t.topic_id, t.topic_name, t.generated_at) == ("seo", topic.name, NOW.isoformat())
    assert t.studies == [] and t.sources == [] and t.series == []


def test_build_topic_every_reported_study_newest_first(db, tmp_path, topic, monkeypatch):
    monkeypatch.setattr(store, "_now", lambda: "2026-10-01T08:00:00+00:00")
    a = _study(db, 1)
    _render(db, tmp_path, topic, [{"study_id": a, "title": "A title", "summary": "A summary"}])
    monkeypatch.setattr(store, "_now", lambda: "2026-10-08T08:00:00+00:00")
    b = _study(db, 2, text=T2, numbers=["9%"], quotes=[{"number": "9%", "quote": T2}],
               primary="https://two.example.com/b")
    _study(db, 3)                                                        # more coverage of a
    _render(db, tmp_path, topic, [{"study_id": b, "title": "B title", "summary": "B summary"}])
    t = model.build_topic(db, topic, NOW)
    assert [s.entry.study_id for s in t.studies] == [b, a]
    sa = t.studies[1]
    assert (sa.entry.title, sa.entry.summary) == ("A title", "A summary")  # seen row has no summary
    assert (sa.first_reported, sa.last_reported) == ("2026-10-01", "2026-10-08")
    assert sa.reported_on == ["2026-10-01", "2026-10-08"]
    assert [c.url for c in sa.entry.coverage] == ["https://example.com/m1", "https://example.com/m3"]
    assert t.studies[0].reported_on == ["2026-10-08"]


def test_build_topic_falls_back_to_findings_for_pre_08_rows(db):
    sid = store.create_study(db, "B", {"what_was_tested": "x", "findings": "+9% clicks"}, None)
    store.record_report(db, "/r/old.md", [(sid, "Old title")])            # 0.7 shape: no summary
    from evidence_echo.core import config, topics
    t = model.build_topic(db, topics.load_template("seo", config.TEMPLATES_DIR), NOW)
    [only] = t.studies
    assert (only.entry.title, only.entry.summary) == ("Old title", "+9% clicks")
    assert only.entry.canonical_name == "x"                              # not the report title
    assert only.entry.coverage == [] and only.entry.published is None and only.entry.tier == "B"


def test_build_topic_resolves_merged_studies(db, tmp_path, topic, monkeypatch):
    monkeypatch.setattr(store, "_now", lambda: "2026-10-01T08:00:00+00:00")
    a = _study(db, 1)
    b = _study(db, 2, text=T2, numbers=["9%"], quotes=[{"number": "9%", "quote": T2}],
               primary="https://two.example.com/b")
    _render(db, tmp_path, topic, [{"study_id": a, "title": "A", "summary": "SA"},
                                  {"study_id": b, "title": "B", "summary": "SB"}])
    monkeypatch.setattr(store, "_now", lambda: "2026-10-05T08:00:00+00:00")
    store.merge_studies(db, a, b)
    t = model.build_topic(db, topic, NOW)
    [only] = t.studies
    assert only.entry.study_id == a
    assert only.reported_on == ["2026-10-01"]                            # one report, deduped
    assert sorted(c.url for c in only.entry.coverage) == ["https://example.com/m1",
                                                          "https://example.com/m2"]


def test_build_topic_skips_a_row_without_a_study(db, topic):
    store.record_report(db, "/r/x.md", [(999, "Gone", "S")])
    assert model.build_topic(db, topic, NOW).studies == []


def test_build_topic_source_and_series_summaries(db, tmp_path, topic):
    a = _study(db, 1, source="Alpha Blog", flags=["no_baseline"],
               facts={"series": "Zeta Survey", "editions": ["2025"]})
    b = _study(db, 2, text=T2, numbers=["9%"], quotes=None, source="Beta News",
               primary="https://two.example.com/b",
               facts={"series": "Zeta Survey", "editions": ["2024"]})        # no_quote dispute
    c = _study(db, 3, text=T3, numbers=["31%"], quotes=[{"number": "31%", "quote": T3}],
               source="Alpha Blog", primary="https://three.example.com/c",
               facts={"series": "Zeta Survey"})                               # no edition label
    _render(db, tmp_path, topic, [{"study_id": c, "title": "C title", "summary": "C"}])
    t = model.build_topic(db, topic, NOW)
    assert t.sources == [model.SourceSummary("Alpha Blog", sorted([a, c]), 0, 1),
                         model.SourceSummary("Beta News", [b], 1, 0)]
    assert t.series == [model.SeriesSummary("Zeta Survey", [("2024", b), ("2025", a),
                                                            ("C title", c)])]


def test_no_quote_only_is_false_for_an_undisputed_entry(db, topic):
    sid = _study(db, 1)
    [e] = model.build_report(db, topic, {}, NOW).new
    assert e.study_id == sid and e.disputed is False and e.no_quote_only is False


# --- stubs ------------------------------------------------------------------------------------

def test_signatures_and_remaining_stub_results(tmp_path):
    tmp_path = tmp_path / "out"
    tmp_path.mkdir()
    assert list(inspect.signature(html.write_report).parameters) == [
        "report", "topic", "reports_dir", "overview_dir", "md_path", "sources_page"]
    assert list(inspect.signature(html.write_overview).parameters) == [
        "summaries", "overview_dir", "now"]
    assert list(inspect.signature(obsidian.export).parameters) == ["topic", "vault_dir"]
    rep = model.Report("seo", "SEO", "2026-10-16", [], [], [], [])
    top = model.Topic("seo", "SEO", NOW.isoformat(), [], [], [])
    out = html.write_report(rep, top, tmp_path, tmp_path, tmp_path / "r.md")
    assert set(out) == {"report", "topic"}                                # real pages (SP5a-H)
    assert {p.name for p in tmp_path.iterdir()} == {"r.html", "seo.html"}
    assert html.write_overview([], tmp_path, NOW) == {"overview": str(tmp_path / "overview.html")}
    assert set(obsidian.export(top, tmp_path)) == {"dir", "written", "removed"}  # real export (SP5a-O)
    assert {p.name for p in tmp_path.iterdir()} == {"r.html", "seo.html", "overview.html", "Evidence Echo"}


def test_model_field_order_is_the_contract():
    fields = {cls.__name__: [f.name for f in dataclasses.fields(cls)] for cls in (
        model.Link, model.Entry, model.SeenEntry, model.Report, model.TopicStudy,
        model.SourceSummary, model.SeriesSummary, model.Topic)}
    assert fields == {
        "Link": ["name", "url"],
        "Entry": ["study_id", "title", "summary", "tier", "disputed", "skeptic_tiers",
                  "no_quote_only", "dispute_reason", "skeptic_notes", "view", "series", "editions",
                  "research_timeframe", "published", "primary_url", "coverage", "canonical_name",
                      "subtopic"],
        "SeenEntry": ["study_id", "first_title", "first_reported", "coverage", "disputed",
                      "skeptic_tiers", "skeptic_notes"],
        "Report": ["topic_id", "topic_name", "date", "new", "disputed", "seen", "source_warnings",
                   "label"],
        "TopicStudy": ["entry", "first_reported", "last_reported", "reported_on", "reports"],
        "SourceSummary": ["name", "study_ids", "disputes", "red_flags"],
        "SeriesSummary": ["name", "editions"],
        "Topic": ["topic_id", "topic_name", "generated_at", "studies", "sources", "series",
                  "evidence", "waiting", "reports", "metrics"],
    }


def test_series_editions_are_oldest_reported_first():
    from types import SimpleNamespace as NS

    def ts(sid, reported, editions, title="T"):
        return NS(entry=NS(series="S", editions=editions, study_id=sid, title=title),
                  first_reported=reported)

    studies = [ts(1, "2026-09-01", ["March 2026"]),              # reported first, sorts last by text
               ts(2, "2026-10-01", ["February 2026"]),
               ts(3, "2026-10-05", [], title="Unlabelled")]
    [s] = model._series_editions(studies)
    assert s.editions == [("March 2026", 1), ("February 2026", 2), ("Unlabelled", 3)]


# --- local time (evidence-ui) ---------------------------------------------------------------

def test_report_after_local_midnight_keeps_utc_file_name(db, tmp_path, topic, monkeypatch):
    from datetime import timedelta
    from evidence_echo.render import clock
    monkeypatch.setattr(clock, "ZONE", timezone(timedelta(hours=2)))
    sid = _study(db, 1)
    out, rep, _ = report.render_with_model(
        db, json.dumps({"results": [{"study_id": sid, "title": "T", "summary": "S"}]}),
        tmp_path, "en", topic, now=datetime(2026, 10, 20, 22, 30, tzinfo=timezone.utc))
    assert rep.date == "2026-10-21"
    assert out["path"].endswith("2026-10-20-report.md")


def test_topic_dates_are_local(db, tmp_path, topic, monkeypatch):
    from datetime import timedelta
    from evidence_echo.render import clock
    monkeypatch.setattr(clock, "ZONE", timezone(timedelta(hours=2)))
    monkeypatch.setattr(store, "_now", lambda: "2026-10-20T23:10:00+00:00")
    a = _study(db, 1)
    _render(db, tmp_path, topic, [{"study_id": a, "title": "A", "summary": "S"}])
    t = model.build_topic(db, topic, NOW)
    assert t.studies[0].first_reported == "2026-10-21"


# --- report labels and the report list (evidence-ui) ----------------------------------------

def _rows(*specs):
    return [{"id": i, "path": p, "rendered_at": at} for i, p, at in specs]


def test_report_label_counts_only_this_topic():
    rows = _rows((1, "x/2026-10-20-report-2.md", "2026-10-20T08:00:00+00:00"))
    assert model.report_labels(rows) == {1: "2026-10-20"}


def test_second_report_same_local_day_gets_2(monkeypatch):
    from datetime import timedelta
    from evidence_echo.render import clock
    monkeypatch.setattr(clock, "ZONE", timezone(timedelta(hours=2)))
    rows = _rows((1, "a.md", "2026-10-20T08:00:00+00:00"), (2, "b.md", "2026-10-20T20:00:00+00:00"),
                 (3, "c.md", "2026-10-20T22:30:00+00:00"))
    assert model.report_labels(rows) == {1: "2026-10-20", 2: "2026-10-20 (2)", 3: "2026-10-21"}


def test_report_list_skips_missing_and_falls_back_to_md(tmp_path):
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "a.html").write_text("x")
    (tmp_path / "b.md").write_text("x")
    rows = _rows((1, str(tmp_path / "a.md"), "2026-10-18T08:00:00+00:00"),
                 (2, str(tmp_path / "b.md"), "2026-10-19T08:00:00+00:00"),
                 (3, str(tmp_path / "c.md"), "2026-10-20T08:00:00+00:00"))
    links = model.report_links(rows)
    assert [(link.label, Path(link.path).name) for link in links] == [
        ("2026-10-19", "b.md"), ("2026-10-18", "a.html")]


def test_build_report_label_counts_the_recorded_reports(db, tmp_path, topic, monkeypatch):
    monkeypatch.setattr(store, "_now", lambda: "2026-10-20T08:00:00+00:00")
    a = _study(db, 1)
    _render(db, tmp_path, topic, [{"study_id": a, "title": "A", "summary": "S"}])
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    rep = model.build_report(db, topic, {}, now)
    assert rep.label == "2026-10-20 (2)"
    # the report's own label (its crumb) is the one its line in the topic page's list gets
    monkeypatch.setattr(store, "_now", lambda: now.isoformat())
    seed_new_study(db)
    _render(db, tmp_path, topic)
    assert model.build_topic(db, topic, NOW).reports[0].label == rep.label


def test_list_reports_counts_the_new_studies_each_report_showed(db, tmp_path, topic):
    seed_new_study(db)
    seed_new_study(db)
    _render(db, tmp_path, topic)
    assert [r["studies"] for r in store.list_reports(db)] == [2]
    assert [link.studies for link in model.report_links(store.list_reports(db))] == [2]


def test_two_reports_one_day_count_two(db, tmp_path, topic, monkeypatch):
    monkeypatch.setattr(store, "_now", lambda: "2026-10-01T08:00:00+00:00")
    a = _study(db, 1)
    _render(db, tmp_path, topic, [{"study_id": a, "title": "A", "summary": "SA"}])
    monkeypatch.setattr(store, "_now", lambda: "2026-10-01T18:00:00+00:00")
    store.record_report(db, "/r/second.md", [(a, "A", "SA")])
    t = model.build_topic(db, topic, NOW)
    [only] = t.studies
    assert only.reported_on == ["2026-10-01"] and only.reports == 2
