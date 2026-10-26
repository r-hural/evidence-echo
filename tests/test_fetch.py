from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from evidence_echo.core import store
from evidence_echo.sources import base
from evidence_echo.stages import fetch

NOW = datetime.now(timezone.utc)


class FakeKind:
    TYPE, HAS_HEADLINES = "rss", True

    def __init__(self, entries, error=None, blocked=False):
        self.result = base.SourceResult(entries=entries, error=error, blocked=blocked)

    def list_new(self, sources, ctx):
        return {s["name"]: self.result for s in sources}

    def full_text(self, url, ctx):
        return base.FullText("Article text with data. " * 40, "ok")


def _cfg(sources, horizon=7, scan_depth="headlines", adaptive="off"):
    topic = SimpleNamespace(sources=sources, linkedin_apify_actor="", scan_depth=None)
    return SimpleNamespace(topic=topic, fetch_horizon_days=horizon, scan_depth=scan_depth,
                           adaptive_fetch=adaptive)


def _ctx(days=7):
    return base.FetchContext(cutoff=NOW - timedelta(days=days), client=None,
                             limiter=base.RateLimiter())


@pytest.fixture
def kinds(monkeypatch):
    registry = {}
    monkeypatch.setattr(fetch, "get_kind", lambda t: registry[t])
    monkeypatch.setattr(fetch, "_date_from_page", lambda url, ctx: None)
    return registry


SRC = {"type": "rss", "name": "Blog", "url": "https://e.com/feed"}


def test_horizon_old_entries_stored_as_seen(db, kinds):
    kinds["rss"] = FakeKind([base.Entry("https://e.com/new", "N", published=NOW - timedelta(days=1)),
                             base.Entry("https://e.com/old", "O", published=NOW - timedelta(days=30))])
    out = fetch.run(db, _cfg([SRC]), ctx=_ctx())
    assert out["sources"]["Blog"] == {"type": "rss", "found": 2, "new": 1, "seen": 1,
                                      "error": None, "blocked": False}
    assert [i["url"] for i in store.items_in_state(db, "fetched")] == ["https://e.com/new"]
    assert [i["url"] for i in store.items_in_state(db, "seen")] == ["https://e.com/old"]


def test_undated_first_run_baseline_then_all(db, kinds):
    entries = [base.Entry(f"https://e.com/p{i}", f"P{i}") for i in range(25)]
    kinds["rss"] = FakeKind(entries)
    fetch.run(db, _cfg([SRC]), ctx=_ctx())
    fetched = store.items_in_state(db, "fetched")
    assert len(fetched) == 20 and len(store.items_in_state(db, "seen")) == 5
    assert all(i["date_unknown"] == 1 and i["excerpt"].startswith("[date unknown] ") for i in fetched)
    kinds["rss"] = FakeKind([base.Entry(f"https://e.com/q{i}", "Q") for i in range(25)])
    fetch.run(db, _cfg([SRC]), ctx=_ctx())
    assert len(store.items_in_state(db, "fetched")) == 45   # later runs: every new undated entry


def test_idempotent_and_canonical_dedup(db, kinds):
    kinds["rss"] = FakeKind([base.Entry("https://e.com/a?utm_source=x", "A", published=NOW)])
    fetch.run(db, _cfg([SRC]), ctx=_ctx())
    kinds["rss"] = FakeKind([base.Entry("http://e.com/a", "A", published=NOW)])
    out = fetch.run(db, _cfg([SRC]), ctx=_ctx())
    assert out["sources"]["Blog"]["new"] == 0
    assert len(store.items_in_state(db, "fetched")) == 1


def test_non_http_entry_urls_dropped(db, kinds):
    kinds["rss"] = FakeKind([base.Entry("javascript:alert(1)", "X", published=NOW)])
    assert fetch.run(db, _cfg([SRC]), ctx=_ctx())["sources"]["Blog"]["new"] == 0


def test_source_error_recorded_and_others_continue(db, kinds):
    other = {"type": "page", "name": "Other", "url": "https://o.com/"}
    kinds["rss"] = FakeKind([], error="HTTP 500")
    kinds["page"] = FakeKind([base.Entry("https://o.com/x", "X", published=NOW)])
    out = fetch.run(db, _cfg([SRC, other]), ctx=_ctx())
    assert out["sources"]["Blog"]["error"] == "HTTP 500"
    assert out["sources"]["Other"]["new"] == 1
    assert store.get_source_state(db, "Blog")["consecutive_errors"] == 1


def test_failed_first_run_keeps_baseline(db, kinds):
    kinds["rss"] = FakeKind([], error="HTTP 500")
    fetch.run(db, _cfg([SRC]), ctx=_ctx())
    kinds["rss"] = FakeKind([base.Entry(f"https://e.com/p{i}", f"P{i}") for i in range(25)])
    fetch.run(db, _cfg([SRC]), ctx=_ctx())
    assert len(store.items_in_state(db, "fetched")) == 20 and len(store.items_in_state(db, "seen")) == 5


class ExplodingKind(FakeKind):
    """list_new raises for the source named "Bad" (an unexpected bug in a kind)."""

    def list_new(self, sources, ctx):
        if any(s["name"] == "Bad" for s in sources):
            raise ValueError("boom in kind")
        return super().list_new(sources, ctx)


def test_kind_exception_isolated_to_its_source(db, kinds):
    bad = {"type": "rss", "name": "Bad", "url": "https://bad.com/feed"}
    kinds["rss"] = ExplodingKind([base.Entry("https://e.com/x", "X", published=NOW)])
    out = fetch.run(db, _cfg([bad, SRC]), ctx=_ctx())
    assert "boom in kind" in out["sources"]["Bad"]["error"]
    assert out["sources"]["Blog"]["new"] == 1
    assert store.get_source_state(db, "Bad")["consecutive_errors"] == 1


def test_store_exception_isolated_to_its_source(db, kinds, monkeypatch):
    other = {"type": "page", "name": "Other", "url": "https://o.com/"}
    kinds["rss"] = FakeKind([base.Entry("https://e.com/undated", "U")])
    kinds["page"] = FakeKind([base.Entry("https://o.com/x", "X", published=NOW)])

    def explode(url, ctx):
        raise RuntimeError("date lookup crashed")
    monkeypatch.setattr(fetch, "_date_from_page", explode)
    out = fetch.run(db, _cfg([SRC, other]), ctx=_ctx())
    assert "date lookup crashed" in out["sources"]["Blog"]["error"]
    assert out["sources"]["Other"]["new"] == 1
    assert store.get_source_state(db, "Blog")["last_error"]


def test_malformed_entry_host_does_not_crash_store(db):
    """An undated entry on an IDNA-invalid host: the date lookup fails quietly, the item is kept."""
    import httpx
    ctx = base.FetchContext(cutoff=NOW - timedelta(days=7), limiter=base.RateLimiter(interval=0),
                            client=httpx.Client(transport=httpx.MockTransport(
                                lambda r: httpx.Response(200, text="<html></html>"))))
    result = base.SourceResult(entries=[base.Entry("https://xn--/post-1", "P"),
                                        base.Entry("https://e.com/ok", "OK", published=NOW)])
    counts = fetch.store_entries(db, SRC, FakeKind([]), result, ctx, first_run=False)
    assert counts["error"] is None and counts["new"] >= 1


SRC2 = {"type": "rss", "name": "Quiet", "url": "https://q.com/feed"}


def _seed_dates(db, name, days):
    for i, d in enumerate(days):
        store.insert_item(db, {"url": f"https://{name}.example/p{i}", "canonical_url": f"https://{name}.example/p{i}",
                               "source_name": name, "source_type": "rss", "title": "T", "excerpt": "",
                               "published": (NOW - timedelta(days=d)).isoformat(),
                               "fetched_at": NOW.isoformat(), "state": "seen", "date_unknown": 0})


def test_adaptive_skips_source_not_due(db, kinds):
    kinds["rss"] = FakeKind([])
    _seed_dates(db, "Quiet", [0, 10, 20, 30])            # learned interval: 5 days
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)   # checked just now (real timestamp)
    store.record_fetch(db, "Blog", "rss", new=0, error=None)
    before = store.get_source_state(db, "Quiet")["last_checked"]
    out = fetch.run(db, _cfg([SRC, SRC2], adaptive="on"), ctx=_ctx())
    assert out["skipped"] == 1
    q = out["sources"]["Quiet"]
    assert q["skipped"] == "not due" and q["interval_days"] == 5.0 and q["interval_source"] == "learned"
    assert q["next_due"] > NOW.isoformat()
    assert "skipped" not in out["sources"]["Blog"]        # fewer than 3 dates: always due
    assert store.get_source_state(db, "Quiet")["last_checked"] == before


def test_fetch_all_and_adaptive_off_fetch_everything(db, kinds):
    kinds["rss"] = FakeKind([])
    _seed_dates(db, "Quiet", [0, 20, 40, 60])
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    assert fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=_ctx(), fetch_all=True)["skipped"] == 0
    assert fetch.run(db, _cfg([SRC2]), ctx=_ctx())["skipped"] == 0


def test_failing_source_always_due(db, kinds):
    kinds["rss"] = FakeKind([])
    _seed_dates(db, "Quiet", [0, 20, 40, 60])
    store.record_fetch(db, "Quiet", "rss", new=0, error="HTTP 500")
    assert fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=_ctx())["skipped"] == 0


def test_all_sources_skipped(db, kinds):
    kinds["rss"] = FakeKind([base.Entry("https://e.com/x", "X", published=NOW)])
    _seed_dates(db, "Quiet", [0, 20, 40, 60])
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    out = fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=_ctx())
    assert out["skipped"] == 1 and list(out["sources"]) == ["Quiet"] and "degraded" in out
    assert store.items_in_state(db, "fetched") == []


def test_possible_gap_when_every_entry_new(db, kinds):
    kinds["rss"] = FakeKind([base.Entry("https://e.com/a", "A", published=NOW)])
    fetch.run(db, _cfg([SRC]), ctx=_ctx())                       # first run: never flagged
    kinds["rss"] = FakeKind([base.Entry(f"https://e.com/n{i}", "N", published=NOW)
                             for i in range(fetch.MIN_GAP_ENTRIES)])
    out = fetch.run(db, _cfg([SRC]), ctx=_ctx())
    assert out["sources"]["Blog"]["possible_gap"] is True and out["possible_gaps"] == ["Blog"]


def test_no_gap_when_any_entry_known_or_empty(db, kinds):
    kinds["rss"] = FakeKind([base.Entry("https://e.com/a", "A", published=NOW)])
    out = fetch.run(db, _cfg([SRC]), ctx=_ctx())
    assert "possible_gap" not in out["sources"]["Blog"] and out["possible_gaps"] == []
    kinds["rss"] = FakeKind([base.Entry("https://e.com/a", "A", published=NOW),
                             base.Entry("https://e.com/d", "D", published=NOW)])
    assert fetch.run(db, _cfg([SRC]), ctx=_ctx())["possible_gaps"] == []
    kinds["rss"] = FakeKind([])
    assert fetch.run(db, _cfg([SRC]), ctx=_ctx())["possible_gaps"] == []


def _set_checked(db, name, days_ago):
    db.execute("UPDATE sources_state SET last_checked=? WHERE name=?",
               ((NOW - timedelta(days=days_ago)).isoformat(), name))
    db.commit()


def test_interval_capped_below_horizon_item_not_lost(db, kinds):
    kinds["rss"] = FakeKind([base.Entry("https://q.com/mid", "Mid", published=NOW - timedelta(days=6))])
    _seed_dates(db, "Quiet", [0, 30, 60, 90])               # learned 14 days, horizon 7 -> capped to 6
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    _set_checked(db, "Quiet", 5)
    out = fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=_ctx())
    q = out["sources"]["Quiet"]
    assert q["skipped"] == "not due" and q["interval_days"] == 6.0 and q["capped"] is True
    assert q["interval_source"] == "learned"
    _set_checked(db, "Quiet", 6.5)                          # due well inside the horizon
    out = fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=_ctx())
    assert out["sources"]["Quiet"]["new"] == 1 and out["sources"]["Quiet"]["seen"] == 0
    assert [i["url"] for i in store.items_in_state(db, "fetched")] == ["https://q.com/mid"]


def test_uncapped_skip_has_no_capped_key(db, kinds):
    kinds["rss"] = FakeKind([])
    _seed_dates(db, "Quiet", [0, 8, 16, 24])               # learned 4 days
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    q = fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=_ctx())["sources"]["Quiet"]
    assert q["interval_days"] == 4.0 and "capped" not in q


def test_pinned_interval_capped_by_horizon(db, kinds):
    kinds["rss"] = FakeKind([])
    src = {**SRC2, "fetch_every": 30}
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    _set_checked(db, "Quiet", 3)
    q = fetch.run(db, _cfg([src], adaptive="on"), ctx=_ctx())["sources"]["Quiet"]
    assert q["interval_days"] == 6.0 and q["interval_source"] == "pinned" and q["capped"] is True


def test_linkedin_interval_capped_at_six_days(db, kinds):
    kinds["linkedin"] = FakeKind([])
    src = {"type": "linkedin", "name": "Person", "slug": "person", "fetch_every": 20}
    store.record_fetch(db, "Person", "linkedin", new=0, error=None)
    _set_checked(db, "Person", 2)
    q = fetch.run(db, _cfg([src], horizon=30, adaptive="on"), ctx=_ctx(30))["sources"]["Person"]
    assert q["interval_days"] == 6.0 and q["capped"] is True


def _prime(db, kinds):
    kinds["rss"] = FakeKind([base.Entry("https://e.com/a", "A", published=NOW)])
    fetch.run(db, _cfg([SRC]), ctx=_ctx())               # first run


def test_no_gap_for_small_feed(db, kinds):
    _prime(db, kinds)
    kinds["rss"] = FakeKind([base.Entry("https://e.com/b", "B", published=NOW)])   # 1 entry, all new
    out = fetch.run(db, _cfg([SRC]), ctx=_ctx())
    assert out["possible_gaps"] == [] and "possible_gap" not in out["sources"]["Blog"]


def test_gap_for_rss_at_min_entries(db, kinds):
    _prime(db, kinds)
    kinds["rss"] = FakeKind([base.Entry(f"https://e.com/n{i}", "N", published=NOW)
                             for i in range(fetch.MIN_GAP_ENTRIES)])
    assert fetch.run(db, _cfg([SRC]), ctx=_ctx())["possible_gaps"] == ["Blog"]


def test_rss_gap_not_set_in_backfill(db, kinds):
    _prime(db, kinds)
    kinds["rss"] = FakeKind([base.Entry(f"https://e.com/n{i}", "N", published=NOW)
                             for i in range(fetch.MIN_GAP_ENTRIES)])
    ctx = _ctx()
    ctx.backfill = SimpleNamespace(per_source={"Blog": 10})
    assert fetch.run(db, _cfg([SRC]), ctx=ctx)["possible_gaps"] == []


LI = {"type": "linkedin", "name": "Person", "slug": "person"}


def test_linkedin_gap_only_at_max_posts(db, kinds):
    kinds["linkedin"] = FakeKind([base.Entry("https://l.com/0", "x", published=NOW)])
    fetch.run(db, _cfg([LI]), ctx=_ctx())                # first run
    few = [base.Entry(f"https://l.com/f{i}", "x", published=NOW) for i in range(29)]
    kinds["linkedin"] = FakeKind(few)
    assert fetch.run(db, _cfg([LI]), ctx=_ctx())["possible_gaps"] == []
    full = [base.Entry(f"https://l.com/g{i}", "x", published=NOW) for i in range(30)]
    kinds["linkedin"] = FakeKind(full)
    assert fetch.run(db, _cfg([LI]), ctx=_ctx())["possible_gaps"] == ["Person"]


def test_bad_stored_date_does_not_abort_fetch(db, kinds):
    kinds["rss"] = FakeKind([])
    _seed_dates(db, "Quiet", [0, 20, 40, 60])
    db.execute("UPDATE items SET published='garbage' WHERE url LIKE '%p0'")
    db.commit()
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    store.record_fetch(db, "Blog", "rss", new=0, error=None)
    out = fetch.run(db, _cfg([SRC, SRC2], adaptive="on"), ctx=_ctx())
    assert "Blog" in out["sources"] and "Quiet" in out["sources"]


def test_bad_last_checked_means_due(db, kinds):
    kinds["rss"] = FakeKind([])
    _seed_dates(db, "Quiet", [0, 20, 40, 60])
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    db.execute("UPDATE sources_state SET last_checked='not a date' WHERE name='Quiet'")
    db.commit()
    assert fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=_ctx())["skipped"] == 0


def test_naive_last_checked_is_utc(db, kinds):
    kinds["rss"] = FakeKind([])
    _seed_dates(db, "Quiet", [0, 20, 40, 60])
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    db.execute("UPDATE sources_state SET last_checked=? WHERE name='Quiet'",
               (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),))
    db.commit()
    assert fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=_ctx())["skipped"] == 1


def test_backfill_ignores_adaptive_skip(db, kinds):
    kinds["rss"] = FakeKind([])
    _seed_dates(db, "Quiet", [0, 20, 40, 60])
    store.record_fetch(db, "Quiet", "rss", new=0, error=None)
    ctx = _ctx()
    ctx.backfill = SimpleNamespace(per_source={"Quiet": 10})
    assert fetch.run(db, _cfg([SRC2], adaptive="on"), ctx=ctx)["skipped"] == 0


def test_recent_dates_orders_as_dates_not_text(db):
    for i, published in enumerate(["2026-10-20T23:30:00-05:00", "2026-10-21T02:00:00+00:00"]):
        store.insert_item(db, {"url": f"https://tz.example/p{i}", "canonical_url": f"https://tz.example/p{i}",
                               "source_name": "TZ", "source_type": "rss", "title": "T", "excerpt": "",
                               "published": published, "fetched_at": NOW.isoformat(),
                               "state": "seen", "date_unknown": 0})
    dates = fetch.recent_dates(db, "TZ")
    assert dates[0] == datetime(2026, 10, 21, 4, 30, tzinfo=timezone.utc)  # text order puts it second
    assert dates[1] == datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)


def test_study_first_published_compares_dates_not_text(db):
    for i, published in enumerate(["2026-10-21T02:00:00+00:00", "2026-10-20T23:30:00-05:00"]):
        item_id = store.insert_item(db, {
            "url": f"https://fp.example/p{i}", "canonical_url": f"https://fp.example/p{i}",
            "source_name": "FP", "source_type": "rss", "title": "T", "excerpt": "",
            "published": published, "fetched_at": NOW.isoformat(), "state": "seen", "date_unknown": 0})
        db.execute("UPDATE items SET study_id = 7 WHERE id = ?", (item_id,))
    assert store.study_first_published(db) == {7: "2026-10-21T02:00:00+00:00"}  # the -05:00 one is later
