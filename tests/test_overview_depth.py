"""overview_studies: the N most recently reported studies per topic, last report first."""
from datetime import datetime
from pathlib import Path

import pytest

from evidence_echo import api
from evidence_echo.core import config
from evidence_echo.core import overview, settings, store
from evidence_echo.core.overview import TopicSummary
from evidence_echo.core.settings import ConfigError
from evidence_echo.render import html as html_render
from tests.conftest import make_workspace

NOW = datetime(2026, 10, 24, 10, 30)
DAYS = ["2026-10-01", "2026-10-08", "2026-10-15", "2026-10-22"]


def _study(db, tier="B"):
    return store.create_study(db, tier, {}, None)


def _report(db, day, studies, new=True):
    path = f"/r/seo/{day}-report.md"
    cur = db.execute("INSERT INTO reports (path, rendered_at) VALUES (?, ?)", (path, f"{day}T09:00:00+00:00"))
    for sid, title in studies:
        db.execute("INSERT INTO report_entries (report_id, study_id, title, summary) VALUES (?,?,?,?)",
                   (cur.lastrowid, sid, title, "s" if new else None))
    db.commit()


def _summary(db, limit, tmp_path=None):
    return TopicSummary("seo", "SEO", report=store.latest_report(db), recent=store.recent_studies(db, limit))


def _titles(rows):
    return [r["title"] for r in rows]


def test_overview_studies_default_10_and_validation():
    assert settings.GLOBAL_DEFAULTS["overview_studies"] == 10
    assert "overview_studies" not in settings.OVERRIDES
    assert settings.check_value("overview_studies", "0", "f") == 0
    assert settings.check_value("overview_studies", 100, "f") == 100
    for bad in (-1, "x", True, 101, "1.5"):
        with pytest.raises(ConfigError, match="overview_studies"):
            settings.check_value("overview_studies", bad, "f")


def test_overview_studies_env_and_config_reach_the_setting(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    make_workspace(data, overview_studies=3)
    ws = config.workspace()
    assert config.workspace_setting("overview_studies", ws) == 3
    monkeypatch.setenv("EV_ECHO_OVERVIEW_STUDIES", "0")
    assert config.workspace_setting("overview_studies", ws) == 0


def _history(db):
    """Reports 1-3 with 4 studies each (newest tier last), the newest with 3 new studies."""
    for day, count in zip(DAYS[:3], (4, 4, 4)):
        _report(db, day, [(_study(db, "ABCD"[i]), f"{day} {'ABCD'[i]}") for i in range(count)])
    _report(db, DAYS[3], [(_study(db, "A"), "new A"), (_study(db, "C"), "new C"), (_study(db, "B"), "new B")])


def test_recent_latest_fewer_than_limit_fills_from_earlier(db):
    _history(db)
    got = store.recent_studies(db, 10)
    assert _titles(got["latest"]) == ["new A", "new B", "new C"] and got["more"] == 0
    assert _titles(got["earlier"]) == ["2026-10-15 A", "2026-10-15 B", "2026-10-15 C", "2026-10-15 D",
                                       "2026-10-08 A", "2026-10-08 B", "2026-10-08 C"]
    assert got["latest"][0].keys() == {"study_id", "title", "tier", "report_path", "rendered_at", "card_id"}
    assert got["earlier"][0]["report_path"] == "/r/seo/2026-10-15-report.md"
    text = overview.render([_summary(db, 10)], Path("/r"), NOW)
    lines = text.splitlines()
    assert lines[lines.index("Earlier") - 2].startswith("- **[C]** new C")
    assert sum(1 for line in lines if line.startswith("- **[")) == 10
    assert lines.index("Earlier") > 4 and "3 new findings" in text


def test_recent_limit_zero_shows_latest_only(db):
    _history(db)
    got = store.recent_studies(db, 0)
    assert _titles(got["latest"]) == ["new A", "new C", "new B"]          # report order, all of them
    assert got["earlier"] == [] and got["more"] == 0
    assert "Earlier" not in overview.render([_summary(db, 0)], Path("/r"), NOW)


def test_recent_latest_more_than_limit(db):
    _report(db, DAYS[0], [(_study(db), "old")])
    _report(db, DAYS[1], [(_study(db, "ABCD"[i % 4]), f"n{i:02d}") for i in range(14)])
    got = store.recent_studies(db, 10)
    assert len(got["latest"]) == 10 and got["more"] == 4 and got["earlier"] == []
    assert [r["tier"] for r in got["latest"]] == sorted(r["tier"] for r in got["latest"])
    text = overview.render([_summary(db, 10)], Path("/r"), NOW)
    assert "- [+4 more in the report](seo/2026-10-08-report.md)" in text
    assert "14 new findings" in text and "Earlier" not in text


def test_seen_before_not_in_latest(db):
    s_old, s_new = _study(db, "A"), _study(db, "B")
    _report(db, DAYS[0], [(s_old, "first sight")])
    _report(db, DAYS[2], [(s_new, "fresh"), (s_old, "first sight again")], new=True)
    db.execute("UPDATE report_entries SET summary=NULL WHERE study_id=? AND report_id=2", (s_old,))
    db.commit()
    got = store.recent_studies(db, 10)
    assert _titles(got["latest"]) == ["fresh"]
    assert _titles(got["earlier"]) == ["first sight"]                     # its first report's title
    assert got["earlier"][0]["report_path"] == "/r/seo/2026-10-01-report.md"
    assert "1 new finding\n" in overview.render([_summary(db, 10)], Path("/r"), NOW)


def test_merged_study_listed_once_under_its_survivor(db):
    a, b = _study(db, "A"), _study(db, "B")
    _report(db, DAYS[0], [(a, "survivor")])
    _report(db, DAYS[1], [(b, "merged away")])
    db.execute("UPDATE studies SET merged_into=? WHERE id=?", (a, b))
    db.commit()
    got = store.recent_studies(db, 10)
    assert got["latest"] == [] and _titles(got["earlier"]) == ["survivor"]


def test_card_id_is_the_id_the_report_showed(db):
    a, b = _study(db, "A"), _study(db, "B")
    _report(db, DAYS[0], [(b, "shown as b")])
    db.execute("UPDATE studies SET merged_into=? WHERE id=?", (a, b))   # b later merged into a
    db.commit()
    row = store.recent_studies(db, 10)["latest"][0]
    assert row["study_id"] == a and row["card_id"] == b                 # the report's card is #s<b>


def test_earlier_links_to_first_report(db, tmp_path):
    out = tmp_path / "R"
    (out / "seo").mkdir(parents=True)
    s1, s2 = _study(db), _study(db)
    first, second = out / "seo" / "2026-10-01-report.md", out / "seo" / "2026-10-22-report.md"
    _report(db, DAYS[0], [(s1, "older study")])
    _report(db, DAYS[3], [(s2, "newer study")])
    db.execute("UPDATE reports SET path=? WHERE id=1", (str(first),))
    db.execute("UPDATE reports SET path=? WHERE id=2", (str(second),))
    db.commit()
    text = overview.render([_summary(db, 10)], out, NOW)
    assert "- **[B]** newer study — [report](seo/2026-10-22-report.md)" in text
    assert "- **[B]** older study — [report](seo/2026-10-01-report.md)" in text


def test_html_card_same_rule(db, tmp_path):
    out = tmp_path / "R"
    (out / "seo").mkdir(parents=True)
    s1, s2 = _study(db, "A"), _study(db, "C")
    first, second = out / "seo" / "2026-10-01-report.md", out / "seo" / "2026-10-22-report.md"
    for p in (first, second):
        p.write_text("#", encoding="utf-8")
    first.with_suffix(".html").write_text("r", encoding="utf-8")
    _report(db, DAYS[0], [(s1, "older study")])
    _report(db, DAYS[3], [(s2, "newer study")])
    db.execute("UPDATE reports SET path=? WHERE id=1", (str(first),))
    db.execute("UPDATE reports SET path=? WHERE id=2", (str(second),))
    db.commit()
    page = html_render.overview_page([_summary(db, 10)], out, NOW)
    assert page.index("newer study") < page.index('<h3 class="ee-earlier">Earlier</h3>') < page.index("older study")
    assert f'<a href="seo/2026-10-01-report.html#s{s1}">older study</a>' in page   # its card in its first report
    assert '<a href="seo/2026-10-22-report.md">newer study</a>' in page        # no HTML sibling: Markdown, no anchor
    assert "1 new finding</p>" in page
    zero = html_render.overview_page([_summary(db, 0)], out, NOW)
    assert "Earlier" not in zero and "older study" not in zero


def test_html_more_link(db, tmp_path):
    out = tmp_path / "R"
    (out / "seo").mkdir(parents=True)
    _report(db, DAYS[1], [(_study(db), f"n{i}") for i in range(12)])
    db.execute("UPDATE reports SET path=?", (str(out / "seo" / "2026-10-08-report.md"),))
    db.commit()
    page = html_render.overview_page([_summary(db, 10)], out, NOW)
    assert "+2 more in the report</a>" in page and "12 new findings" in page


def test_no_reports_yet_and_one_report(db):
    assert store.recent_studies(db, 10) == {"latest": [], "earlier": [], "more": 0}
    _report(db, DAYS[0], [(_study(db), "only")])
    got = store.recent_studies(db, 10)
    assert _titles(got["latest"]) == ["only"] and got["earlier"] == []


def test_summaries_fill_recent_from_the_setting(workspace, monkeypatch):
    from tests.conftest import seed_topic_study
    seed_topic_study()
    cfg = config.load()
    with api._connect(cfg) as conn:
        store.record_report(conn, str(cfg.reports_dir / "r.md"), [(1, "Seeded", "s")])
    rows = api._summaries(config.workspace())
    assert rows[0].recent is not None and rows[0].recent["more"] == 0
    monkeypatch.setenv("EV_ECHO_OVERVIEW_STUDIES", "0")
    assert api._summaries(config.workspace())[0].recent["earlier"] == []
