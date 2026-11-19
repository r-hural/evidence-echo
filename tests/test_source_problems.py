from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from evidence_echo.core import source_problems as sp
from evidence_echo.core import store
from evidence_echo.sources import base, linkedin
from evidence_echo.stages import fetch

NO_KEY = SimpleNamespace(apify_api_token="")
HAS_KEY = SimpleNamespace(apify_api_token="tok")


def _topic(*sources):
    return SimpleNamespace(sources=list(sources))


def _li(n):
    return {"type": "linkedin", "name": f"Person {n}", "slug": f"p{n}"}


def _rss(name):
    return {"type": "rss", "name": name, "url": f"https://{name.lower()}.example/feed"}


def test_groups_by_reason(db):
    groups = sp.problems(db, _topic(*[_li(i) for i in range(7)]), NO_KEY)
    assert len(groups) == 1
    assert groups[0].reason == "Apify token missing (plugin settings)"
    assert len(groups[0].sources) == 7


def test_no_key_groups_without_cfg(db):
    assert sp.problems(db, _topic(_li(1)), None) == []
    assert sp.problems(db, _topic(_li(1)), HAS_KEY) == []


def test_failing_and_erring_grouped(db):
    for name in ("A", "B"):
        for _ in range(5):
            store.record_fetch(db, name, "rss", new=0, error="HTTP 404")
    for name in ("C", "D"):
        store.record_fetch(db, name, "rss", new=0, error="timeout")
    groups = sp.problems(db, _topic(*[_rss(n) for n in "ABCD"]), HAS_KEY)
    assert [(g.reason, len(g.sources)) for g in groups] == [
        ("Failing for 5+ runs — check or remove", 2), ("timeout", 2)]
    assert groups[0].sources[0].startswith("A (since ")
    assert sp.format_group(groups[1]) == "timeout: C, D"


def test_missing_key_not_listed_twice(db):
    for _ in range(5):
        store.record_fetch(db, "Person 1", "linkedin", new=0, error=linkedin.NO_TOKEN)
    groups = sp.problems(db, _topic(_li(1)), NO_KEY)
    assert [g.reason for g in groups] == [linkedin.NO_TOKEN]


def test_fetch_skips_keyless_without_error(db):
    """Review Focus 3: ten keyless fetch runs never drift a source towards failing."""
    cfg = SimpleNamespace(topic=SimpleNamespace(sources=[_li(1)], linkedin_apify_actor="",
                                                scan_depth=None),
                          fetch_horizon_days=7, scan_depth="headlines", adaptive_fetch="off")
    ctx = base.FetchContext(cutoff=datetime.now(timezone.utc) - timedelta(days=7), client=None,
                            limiter=base.RateLimiter())
    for _ in range(10):
        out = fetch.run(db, cfg, ctx=ctx)
    state = store.get_source_state(db, "Person 1")
    assert state is None or state["consecutive_errors"] == 0
    assert out["sources"]["Person 1"] == {"type": "linkedin", "skipped": "key missing",
                                          "reason": linkedin.NO_TOKEN}
    assert out["skipped"] == 1
