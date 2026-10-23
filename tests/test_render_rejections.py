from datetime import datetime, timedelta, timezone
from pathlib import Path

from evidence_echo.render import rejections as rj

NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
PREV = "2026-10-12T12:00:00.000000+00:00"


def item(**kw):
    base = dict(state="rejected", triage_pass=1, triage_reason="looks like a study",
                judge_reason="no data", rejected_at="2026-10-15T00:00:00.000+00:00",
                fetched_at="2026-10-14T00:00:00.000000+00:00", published="2026-10-13T08:00:00",
                title="T", url="https://x.test/a", source_name="S", text_source="full")
    base.update(kw)
    return base


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")


def test_window_since_previous():
    prev = datetime.fromisoformat(PREV)
    items = [item(title=n, rejected_at=iso(t)) for n, t in
             [("at", prev), ("after", prev + timedelta(seconds=1)), ("now", NOW),
              ("late", NOW + timedelta(seconds=1))]]
    assert sorted(r.title for r in rj.rows_in_window(items, PREV, NOW)) == ["after", "now"]


def test_window_first_report_lists_all():
    items = [item(rejected_at="2000-01-01T00:00:00.000+00:00"), item()]
    assert len(rj.rows_in_window(items, None, NOW)) == 2


def test_window_uses_fetched_at_fallback():
    it = item(rejected_at=None, fetched_at="2026-10-15T00:00:00.123456+00:00")
    assert len(rj.rows_in_window([it], PREV, NOW)) == 1


def test_window_bad_fallback_time():
    it = item(rejected_at=None, fetched_at="")
    assert rj.rows_in_window([it], PREV, NOW) == []
    assert len(rj.rows_in_window([it], None, NOW)) == 1


def test_stage_from_failed_check():
    t = rj.rows_in_window([item(triage_pass=0, triage_reason="off topic")], None, NOW)[0]
    assert (t.stage, t.reason, t.note) == ("triage", "off topic", "")
    j = rj.rows_in_window([item(judge_reason=None)], None, NOW)[0]
    assert (j.stage, j.reason) == ("judge", "not recorded")
    assert j.note == "passed triage: looks like a study"
    e = rj.rows_in_window([item(text_source="excerpt")], None, NOW)[0]
    assert e.note == "passed triage: looks like a study · judged from excerpt"


def test_row_falls_back_to_url_and_empty_date():
    r = rj.rows_in_window([item(title=None, published=None)], None, NOW)[0]
    assert r.title == "https://x.test/a" and r.date == ""


def test_paired_path():
    assert rj.paired_path(Path("d/2026-10-19-report.md")) == Path("d/2026-10-19-rejected.md")
    assert rj.paired_path(Path("d/2026-10-19-report-2.md")) == Path("d/2026-10-19-rejected-2.md")


def _three():
    return rj.rows_in_window([item(source_name="B"), item(source_name="A", triage_pass=0),
                              item(source_name="A", triage_pass=0, title="T2")], None, NOW)


def test_markdown_layout():
    md = rj.markdown("SEO", "2026-10-12T12:00:00+00:00", _three())
    assert md.startswith("# SEO — rejected since 2026-10-12")
    assert "3 rejected: 2 at triage, 1 by the judge" in md
    assert md.index("Rejected by the judge (1)") < md.index("Rejected at triage (2)")
    assert "Sources: A 2" in md and "| Source | Title | Date | Stage | Reason | Note |" in md
    assert "all rejections" in rj.markdown("SEO", None, _three())


def test_markdown_empty():
    assert "No rejections since the previous report." in rj.markdown("SEO", PREV, [])


def test_markdown_escapes_pipes_and_newlines():
    rows = rj.rows_in_window([item(title="a | b\nc")], None, NOW)
    lines = [ln for ln in rj.markdown("SEO", None, rows).splitlines() if "a \\| b c" in ln]
    assert len(lines) == 1


def test_html_page_links_report_and_escapes():
    rows = rj.rows_in_window([item(title="<script>x</script>")], None, NOW)
    out = rj.html_page("SEO", None, rows, "2026-10-19-report.html")
    assert 'href="2026-10-19-report.html"' in out
    assert "<script>x" not in out and "&lt;script&gt;" in out


def test_degraded_rejected_triage_counts_as_triage():
    for tp in (None, 1):
        it = item(state="rejected_triage", triage_pass=tp, triage_reason="fetch timed out",
                  judge_reason=None)
        r = rj.rows_in_window([it], None, NOW)[0]
        assert (r.stage, r.reason, r.note) == ("triage", "fetch timed out", "")


def test_row_sort_and_sources_order():
    items = [item(source_name="B", title="b-old", published="2026-10-01T00:00:00"),
             item(source_name="A", title="a-old", published="2026-10-02T00:00:00"),
             item(source_name="A", title="a-new", published="2026-10-09T00:00:00"),
             item(source_name="B", title="b-new", published="2026-10-08T00:00:00"),
             item(source_name="B", title="b-mid", published="2026-10-05T00:00:00")]
    rows = rj.rows_in_window(items, None, NOW)
    assert [r.title for r in rows] == ["a-new", "a-old", "b-new", "b-mid", "b-old"]
    md = rj.markdown("SEO", None, rows)
    assert "Sources: B 3 · A 2" in md


def test_rejections_page_crumbs():
    out = rj.html_page("SEO", None, [], "r.html", "../overview.html", "seo.html")
    assert '<nav class="ee-crumbs"' in out
    assert 'href="../overview.html">Evidence Echo</a>' in out and 'href="seo.html">SEO</a>' in out
    assert 'aria-current="page">Rejections</li>' in out
    assert '<nav class="ee-crumbs"' not in rj.html_page("SEO", None, [], "r.html")


def test_rejections_heading_date_is_local(monkeypatch):
    from evidence_echo.render import clock
    monkeypatch.setattr(clock, "ZONE", timezone(timedelta(hours=2)))
    previous = "2026-10-20T22:30:00+00:00"
    assert "SEO — rejected since 2026-10-21" in rj.markdown("SEO", previous, [])
    assert "SEO — rejected since 2026-10-21" in rj.html_page("SEO", previous, [], "r.html")


def test_html_short_columns_do_not_wrap():
    """Source, Date and Stage stay readable next to long reasons (they wrapped letter by letter)."""
    out = rj.html_page("SEO", None, _three(), "r.html")
    assert '<table class="ee-rej">' in out
    assert ".ee-rej th,.ee-rej td:nth-child(3),.ee-rej td:nth-child(4){white-space:nowrap}" in out
    assert rj.COLUMNS[2:4] == ("Date", "Stage")
