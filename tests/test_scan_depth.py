from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from evidence_echo.core import store
from evidence_echo.sources import base
from evidence_echo.stages import extract, fetch, triage
from tests.conftest import make_item

NOW = datetime.now(timezone.utc)
GOOD = "Article with measured data: 12% more clicks. " * 30


class Kind:
    TYPE, HAS_HEADLINES = "page", True

    def __init__(self, status="ok"):
        self.status, self.calls = status, []

    def list_new(self, sources, ctx):
        return {s["name"]: base.SourceResult(entries=[base.Entry("https://e.com/a", "A", "ex", NOW)])
                for s in sources}

    def full_text(self, url, ctx):
        self.calls.append(url)
        return base.FullText(GOOD if self.status == "ok" else "Just a moment", self.status)


def _cfg(global_depth="headlines", topic_depth=None, source_depth=None):
    src = {"type": "page", "name": "P", "url": "https://e.com/"}
    if source_depth:
        src["scan_depth"] = source_depth
    return SimpleNamespace(topic=SimpleNamespace(sources=[src], linkedin_apify_actor="", scan_depth=topic_depth),
                           fetch_horizon_days=7, scan_depth=global_depth, adaptive_fetch="off")


def _ctx():
    return base.FetchContext(cutoff=NOW - timedelta(days=7), client=None, limiter=base.RateLimiter())


TOPIC = SimpleNamespace(id="t", name="T", domain="d", scope="s", subtopics=[])


@pytest.mark.parametrize("resolved,s,expected", [
    ("headlines", None, "headlines"), ("full", None, "full"),
    ("headlines", "full", "full"), ("full", "headlines", "headlines"),
])
def test_effective_scan_depth(resolved, s, expected):
    """cfg.scan_depth is already resolved (env > topic > config.json); the source's own wins."""
    cfg = _cfg(resolved, None, s)
    assert fetch.effective_scan_depth(cfg.topic.sources[0], cfg) == expected


@pytest.fixture
def ws_topic(tmp_path, monkeypatch):
    from evidence_echo import api
    from evidence_echo.core import config
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "data"))
    ws = config.workspace()
    api.config_set(ws, "scan_depth", "headlines")
    ws = config.workspace()
    api.topic_add(ws, "t", None, "T", "d", "s")
    api.topic_set(ws, "t", "scan_depth", "headlines")
    api.sources_add_legacy(ws, "t", "linkedin", "L", "jane-doe")
    topic_file = ws.topics_dir / "t" / "topic.json"
    import json
    data = json.loads(topic_file.read_text(encoding="utf-8"))
    data["sources"] += [{"type": "page", "name": "P", "url": "https://e.com/"},
                        {"type": "page", "name": "Q", "url": "https://q.com/", "scan_depth": "headlines"}]
    topic_file.write_text(json.dumps(data), encoding="utf-8")
    return ws


def test_env_scan_depth_beats_topic_override_like_other_settings(ws_topic, monkeypatch):
    from evidence_echo import api
    from evidence_echo.core import config
    monkeypatch.setenv("EV_ECHO_SCAN_DEPTH", "full")
    cfg = config.load("t")
    by_name = {s["name"]: s for s in cfg.topic.sources}
    assert fetch.effective_scan_depth(by_name["P"], cfg) == "full"       # env > topic override
    assert fetch.effective_scan_depth(by_name["Q"], cfg) == "headlines"  # the source's own wins
    rows = {r["name"]: r["effective_scan_depth"] for r in api.sources_list(config.workspace(), "t")}
    assert rows == {"L": "full", "P": "full", "Q": "headlines"}
    monkeypatch.delenv("EV_ECHO_SCAN_DEPTH")
    rows = {r["name"]: r["effective_scan_depth"] for r in api.sources_list(config.workspace(), "t")}
    assert rows == {"L": "full", "P": "headlines", "Q": "headlines"}


def test_full_mode_loads_text_before_triage(db, monkeypatch):
    kind = Kind()
    monkeypatch.setattr(fetch, "get_kind", lambda t: kind)
    fetch.run(db, _cfg("full"), ctx=_ctx())
    item = store.items_in_state(db, "fetched")[0]
    assert item["full_text"] == GOOD and item["text_source"] == "page"
    emitted = triage.emit(db, TOPIC)
    assert "12% more clicks" in emitted


def test_full_mode_blocked_text_waits_for_session(db, monkeypatch):
    monkeypatch.setattr(fetch, "get_kind", lambda t: Kind("blocked"))
    fetch.run(db, _cfg("full"), ctx=_ctx())
    assert store.flagged_items(db)[0]["url"] == "https://e.com/a"
    assert '"items": []' in triage.emit(db, TOPIC)


def test_unresolved_flag_degrades_at_next_fetch(db, monkeypatch):
    monkeypatch.setattr(fetch, "get_kind", lambda t: Kind("blocked"))
    fetch.run(db, _cfg("full"), ctx=_ctx())
    out = fetch.run(db, _cfg("full"), ctx=_ctx())
    assert out["degraded"] == 1
    item = store.items_in_state(db, "fetched")[0]
    assert (item["fetch_status"], item["text_source"], item["full_text"]) == (None, "excerpt", "ex")


def test_degrade_without_excerpt_rejects(db):
    store.insert_item(db, dict(make_item(url="https://r.com/doc", source_type="once", excerpt=""),
                               fetch_status="needs_session"))
    assert fetch.degrade_unresolved(db) == 1
    item = store.items_in_state(db, "rejected_triage")[0]
    assert item["triage_reason"] == "text unavailable (blocked, not fetched in session)"


def test_extract_reuses_text_and_flags_blocked(db, monkeypatch):
    store.insert_item(db, dict(make_item(url="https://e.com/has"), state="triaged",
                               full_text=GOOD, text_source="page"))
    store.insert_item(db, dict(make_item(url="https://e.com/blocked"), state="triaged"))
    kind = Kind("blocked")
    monkeypatch.setattr(extract, "get_kind", lambda t: kind)
    out = extract.run(db, _ctx())
    assert out == {"extracted": 1, "degraded": 0, "needs_session": 1}
    assert kind.calls == ["https://e.com/blocked"]
    assert store.flagged_items(db)[0]["url"] == "https://e.com/blocked"
