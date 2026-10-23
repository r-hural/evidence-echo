from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from evidence_echo import api
from evidence_echo.core import store
from evidence_echo.core.settings import ConfigError
from evidence_echo.sources import base
from evidence_echo.stages import fetch

NOW = datetime.now(timezone.utc)


class Kind:
    TYPE, HAS_HEADLINES = "rss", True

    def __init__(self, n, age_days=100):
        self.entries = [base.Entry(f"https://e.com/p{i}", f"P{i}", published=NOW - timedelta(days=age_days))
                        for i in range(n)]

    def list_new(self, sources, ctx):
        return {s["name"]: base.SourceResult(entries=list(self.entries)) for s in sources}


def _cfg(names):
    return SimpleNamespace(topic=SimpleNamespace(
        sources=[{"type": "rss", "name": n, "url": f"https://{n}.com/feed"} for n in names],
        id="t", linkedin_apify_actor="", scan_depth=None), fetch_horizon_days=7, scan_depth="headlines", adaptive_fetch="off",
        apify_api_token="")


def test_backfill_plan_even_split():
    srcs = [{"type": "rss", "name": n} for n in "abc"] + [{"type": "once", "name": "d"}]
    assert fetch.backfill_plan(srcs, 200) == {"a": 67, "b": 67, "c": 66}


def test_normal_run_stores_old_as_seen_backfill_revives(db, monkeypatch):
    monkeypatch.setattr(fetch, "get_kind", lambda t: Kind(5))
    fetch.run(db, _cfg(["a"]), ctx=base.FetchContext(cutoff=NOW - timedelta(days=7), client=None,
                                                      limiter=base.RateLimiter()))
    assert len(store.items_in_state(db, "seen")) == 5
    bf = base.Backfill(days=365, max_items=200, per_source={"a": 200})
    out = fetch.run(db, _cfg(["a"]), backfill=bf, ctx=base.FetchContext(
        cutoff=NOW - timedelta(days=365), client=None, limiter=base.RateLimiter(), backfill=bf))
    assert out["sources"]["a"]["new"] == 5 and len(store.items_in_state(db, "fetched")) == 5
    assert store.get_source_state(db, "a")["backfilled_at"]


def test_backfill_cap_per_source(db, monkeypatch):
    monkeypatch.setattr(fetch, "get_kind", lambda t: Kind(50))
    bf = base.Backfill(days=365, max_items=10, per_source={"a": 10})
    fetch.run(db, _cfg(["a"]), backfill=bf, ctx=base.FetchContext(
        cutoff=NOW - timedelta(days=365), client=None, limiter=base.RateLimiter(), backfill=bf))
    assert len(store.items_in_state(db, "fetched")) == 10


@pytest.mark.parametrize("days,max_items", [(0, 200), (3651, 200), (365, 0), (365, 1001)])
def test_backfill_limits(days, max_items):
    with pytest.raises(ConfigError):
        api.backfill(_cfg(["a"]), None, days, max_items)


def test_backfill_unknown_source():
    with pytest.raises(ConfigError, match="no source named 'x'"):
        api.backfill(_cfg(["a"]), "x", 365, 200)


def test_backfill_plan_max_below_source_count():
    srcs = [{"type": "rss", "name": n} for n in "abcde"]
    assert fetch.backfill_plan(srcs, 3) == {"a": 1, "b": 1, "c": 1}


def test_backfill_skips_unplanned_sources(db, monkeypatch):
    monkeypatch.setattr(fetch, "get_kind", lambda t: Kind(50))
    bf = base.Backfill(days=365, max_items=1, per_source={"a": 1})
    fetch.run(db, _cfg(["a", "b"]), backfill=bf, ctx=base.FetchContext(
        cutoff=NOW - timedelta(days=365), client=None, limiter=base.RateLimiter(), backfill=bf))
    assert len(store.items_in_state(db, "fetched")) == 1


def test_backfill_skips_entries_whose_date_stays_unknown(db, monkeypatch):
    """Owner decision 2026-10-20: a research spike stores only what it can place in its window."""
    class Undated(Kind):
        def __init__(self):
            self.entries = [base.Entry(f"https://e.com/u{i}", f"U{i}", published=None) for i in range(3)]
    monkeypatch.setattr(fetch, "get_kind", lambda t: Undated())
    monkeypatch.setattr(fetch, "_date_from_page", lambda url, ctx: None)
    bf = base.Backfill(days=90, max_items=10, per_source={"a": 10})
    out = fetch.run(db, _cfg(["a"]), backfill=bf, ctx=base.FetchContext(
        cutoff=NOW - timedelta(days=90), client=None, limiter=base.RateLimiter(), backfill=bf))
    assert out["sources"]["a"]["new"] == 0
    assert store.items_in_state(db, "fetched") == [] and store.items_in_state(db, "seen") == []
