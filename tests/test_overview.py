from datetime import datetime
from pathlib import Path

from evidence_echo.core import overview
from evidence_echo.core.overview import TopicSummary

NOW = datetime(2026, 10, 14, 10, 30)


def _entry(title, tier, published, sid=1):
    return {"study_id": sid, "title": title, "tier": tier, "published": published}


def test_rank_by_tier_then_newest():
    entries = [_entry("d", "D", "2026-10-13"), _entry("b-old", "B", "2026-09-01"),
               _entry("b-new", "B", "2026-10-10"), _entry("a", "A", "2026-08-01"),
               _entry("none", None, "2026-10-14")]
    assert [e["title"] for e in overview.rank(entries)] == ["a", "b-new", "b-old"]
    assert [e["title"] for e in overview.rank(entries, limit=10)][-1] == "none"


def test_render_topic_with_report_relative_links(tmp_path):
    out = tmp_path / "R"
    report = {"path": str(out / "seo" / "2026-10-14-report.md"), "rendered_at": "2026-10-14T09:00:00+00:00",
              "entries": [_entry("Title A", "A", "2026-10-12"), _entry("Title C", "C", "2026-10-11")]}
    text = overview.render([TopicSummary("seo", "SEO", report=report)], out, NOW)
    assert text.startswith("# Evidence Echo — Overview\nUpdated 2026-10-14 10:30\n")
    assert "## SEO  ·  last run 2026-10-14  ·  2 new findings" in text
    assert "- **[A]** Title A — [report](seo/2026-10-14-report.md)" in text
    assert "[Latest report](seo/2026-10-14-report.md)" in text


def test_render_report_outside_overview_dir_uses_file_uri(tmp_path):
    report = {"path": str(tmp_path / "vault" / "r.md"), "rendered_at": "2026-10-14T09:00:00+00:00",
              "entries": []}
    text = overview.render([TopicSummary("w", "W", report=report)], tmp_path / "R", NOW)
    assert (tmp_path / "vault" / "r.md").as_uri() in text
    assert "0 new findings" in text


def test_render_not_run_and_error_topics(tmp_path):
    text = overview.render([TopicSummary("a", "Alpha"),
                            TopicSummary("b", "b", error="b/topic.json: field 'name' missing")],
                           tmp_path, NOW)
    assert "## Alpha  ·  not run yet" in text
    assert "## b  ·  setup error: b/topic.json: field 'name' missing" in text


def test_render_no_topics(tmp_path):
    assert "No topics yet" in overview.render([], tmp_path, NOW)


def test_overview_updated_is_local(monkeypatch):
    from datetime import timedelta, timezone
    from evidence_echo.render import clock
    monkeypatch.setattr(clock, "ZONE", timezone(timedelta(hours=2)))
    text = overview.render([], Path("."), datetime(2026, 10, 14, 10, 30, tzinfo=timezone.utc))
    assert "Updated 2026-10-14 12:30" in text
