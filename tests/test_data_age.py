"""Old data: each study's data year in the report payload, and the engine's age line on a search answer page."""
import json
from contextlib import closing
from datetime import date

from evidence_echo import search
from evidence_echo.core import knowledge, store
from evidence_echo.render import search_page
from evidence_echo.stages import report
from tests.test_search_page import NOW, SUMMARY, graded_run

TODAY = date(2026, 10, 30)


def test_data_year_prefers_data_date_then_timeframe_then_published():
    assert knowledge.data_year({"data_date": "2019-05"}, "2025-01-01", TODAY) == (2019, "data")
    assert knowledge.data_year({"research_timeframe": "Jan 2015 to Mar 2016"}, None, TODAY) == (2016, "data")
    assert knowledge.data_year({"research_timeframe": "12 weeks, 3500 pages"}, "2018-02-03T00:00:00",
                               TODAY) == (2018, "published")
    assert knowledge.data_year({"research_timeframe": "in 2099"}, None, TODAY) == (None, None)
    assert knowledge.data_year({}, None, TODAY) == (None, None)


def test_is_old_and_age_note():
    assert knowledge.is_old(2023, TODAY) and not knowledge.is_old(2024, TODAY) and not knowledge.is_old(None)
    assert knowledge.age_note([2025, 2024, None], TODAY) is None
    assert knowledge.age_note([2016, 2025, 2019, None], TODAY) == (
        "2 of 4 studies rest on data from 2016–2019 (3 or more years old); "
        "results that old may no longer hold.")
    assert knowledge.age_note([2014], TODAY).startswith("1 of 1 study rests on data from 2014 (")


def test_report_payload_carries_the_data_year(db, topic):
    from tests.evidence_fixtures import add_study
    old = add_study(db, "Old crawl test", "f", reported=False)
    new = add_study(db, "New crawl test", "f", reported=False)
    db.execute("UPDATE studies SET research_timeframe='2015' WHERE id=?", (old,))
    db.execute("UPDATE items SET state='verified'")
    db.commit()
    items = {i["study_id"]: i for i in json.loads(report.emit(db, topic, "en", TODAY))["items"]}
    assert (items[old]["data_year"], items[old]["data_basis"], items[old]["old_data"]) == (2015, "data", True)
    assert (items[new]["data_year"], items[new]["old_data"]) == (2024, False)
    assert "old_data is true" in json.loads(report.emit(db, topic, "en", TODAY))["instructions"]


def test_answer_page_warns_on_old_data(workspace):
    run, cfg, (a, b) = graded_run(workspace)
    with closing(store.connect(cfg.db_path)) as conn:
        conn.execute("UPDATE studies SET research_timeframe='March 2016' WHERE id=?", (a,))
        conn.commit()
    meta = search.read_meta(search.config.search_dir(cfg.data_dir, "seo", run))
    page = search._page_model(cfg, run, {**meta, "summary": SUMMARY}, NOW)
    assert page.age_note == ("1 of 2 studies rest on data from 2016 (3 or more years old); "
                             "results that old may no longer hold.")
    assert f'<p class="ee-warn">{page.age_note}</p>' in search_page.search_html(page, "t.html", "o.html")
    assert f"**{page.age_note}**" in search_page.search_markdown(page)
    with closing(store.connect(cfg.db_path)) as conn:
        conn.execute("UPDATE studies SET research_timeframe='2025' WHERE id=?", (a,))
        conn.commit()
    fresh = search._page_model(cfg, run, meta, NOW)
    assert fresh.age_note is None and "<p class=\"ee-warn\">" not in search_page.search_html(fresh, "t.html", "o.html")
