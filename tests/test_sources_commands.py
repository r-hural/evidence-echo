import json

import pytest

from evidence_echo import api
from evidence_echo.core import config, store
from evidence_echo.core.settings import ConfigError
from evidence_echo.sources import once, page, rss
from tests.conftest import make_item


@pytest.fixture
def ws_with_topic(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    ws = config.workspace()
    api.topic_add(ws, "t", None, "T", "d", "s")
    return ws, "t"


@pytest.fixture
def probes(monkeypatch):
    calls = {}

    def discover(url):
        ok = url.endswith("/feed")
        return {"url": url, "discovered_from": None,
                "check": {"status": "ok" if ok else "not_feed", "url": url}}
    monkeypatch.setattr(rss, "discover_feed", discover)
    monkeypatch.setattr(rss, "check", lambda s, ctx=None: {"status": "ok", "url": s["url"]})
    monkeypatch.setattr(page, "check", lambda s, ctx=None: {
        "status": "ok" if "blog" in s["url"] else "few_links", "article_links": 4 if "blog" in s["url"] else 1,
        "sample_links": [s["url"] + "post-one-two"], "url": s["url"]})
    monkeypatch.setattr(once, "check", lambda s, ctx=None: {"status": "ok", "title": "Yearly Report 2026", "url": s["url"]})
    return calls


@pytest.mark.parametrize("url,kind", [("https://a.com/feed", "rss"), ("https://a.com/blog/", "page"),
                                      ("https://a.com/report-2026", "once")])
def test_detect_kind(probes, url, kind):
    assert api.detect_kind(url)["kind"] == kind


def test_add_detected_page_with_samples(ws_with_topic, probes):
    ws, tid = ws_with_topic
    out = api.sources_add(ws, tid, "https://a.com/blog/")
    assert out["kind"] == "page" and out["detected"] is True
    assert out["sample_links"] == ["https://a.com/blog/post-one-two"]
    assert out["added"]["name"] == "a.com"
    assert api.sources_list(ws, tid)[-1]["type"] == "page"


def test_add_once_named_from_title(ws_with_topic, probes):
    ws, tid = ws_with_topic
    out = api.sources_add(ws, tid, "https://a.com/report-2026")
    assert out["added"] == {"type": "once", "name": "Yearly Report 2026", "url": "https://a.com/report-2026"}


def test_add_given_kind_and_scan_depth(ws_with_topic, probes):
    ws, tid = ws_with_topic
    out = api.sources_add(ws, tid, "https://a.com/blog/", kind="page", name="A", scan_depth="full")
    assert out["detected"] is False and out["added"]["scan_depth"] == "full"


def test_add_rejects_non_http(ws_with_topic):
    ws, tid = ws_with_topic
    with pytest.raises(api.ConfigError, match="http"):
        api.sources_add(ws, tid, "file:///etc/passwd")


def test_set_and_reset(ws_with_topic, probes):
    ws, tid = ws_with_topic
    api.sources_add(ws, tid, "https://a.com/report-2026", name="R")
    assert api.sources_set(ws, tid, "R", "full")["scan_depth"] == "full"
    assert "scan_depth" not in api.sources_set(ws, tid, "R", "default")
    assert api.sources_reset(ws, tid, "R") == {"reset": "R", "kind": "once", "cleared": "done"}


@pytest.mark.parametrize("target", ["jane-doe", "https://www.linkedin.com/in/jane-doe/",
                                    "https://linkedin.com/in/jane-doe", "http://uk.linkedin.com/in/jane-doe/?trk=x",
                                    "www.linkedin.com/in/jane-doe/"])
def test_add_linkedin_slug_or_profile_url(ws_with_topic, target):
    ws, tid = ws_with_topic
    out = api.sources_add(ws, tid, target, kind="linkedin")
    assert out["added"] == {"type": "linkedin", "name": "jane-doe", "slug": "jane-doe"}
    assert out["kind"] == "linkedin" and out["check"]["status"] == "skipped"
    assert api.sources_list(ws, tid)[-1]["slug"] == "jane-doe"


def test_add_linkedin_keeps_given_name(ws_with_topic):
    ws, tid = ws_with_topic
    out = api.sources_add(ws, tid, "https://www.linkedin.com/in/jane-doe/", kind="linkedin", name="Jane")
    assert out["added"] == {"type": "linkedin", "name": "Jane", "slug": "jane-doe"}


@pytest.mark.parametrize("target", ["https://www.linkedin.com/company/acme/", "https://evil.com/in/jane-doe",
                                    "https://www.linkedin.com/in/", "jane doe", "a/b", "",
                                    "https://notlinkedin.com/in/jane-doe"])
def test_add_linkedin_rejects_other_targets(ws_with_topic, target):
    ws, tid = ws_with_topic
    with pytest.raises(api.ConfigError, match="LinkedIn"):
        api.sources_add(ws, tid, target, kind="linkedin")


def test_add_linkedin_cli_exit_codes(ws_with_topic, capsys):
    from evidence_echo import cli
    assert cli.main(["--topic", "t", "sources", "add", "jane-doe", "--kind", "linkedin"]) == 0
    assert cli.main(["--topic", "t", "sources", "add", "https://www.linkedin.com/company/x/",
                     "--kind", "linkedin"]) == 2
    assert "LinkedIn" in capsys.readouterr().err


def test_default_names_unique_for_pages_on_one_host(ws_with_topic, probes):
    ws, tid = ws_with_topic
    names = [api.sources_add(ws, tid, url, kind="page")["added"]["name"]
             for url in ("https://a.com/blog/", "https://a.com/research/", "https://a.com/research/older/")]
    assert names == ["a.com", "a.com/research", "a.com/research (2)"]


def test_given_name_collision_still_refused(ws_with_topic, probes):
    ws, tid = ws_with_topic
    api.sources_add(ws, tid, "https://a.com/blog/", kind="page", name="A")
    with pytest.raises(api.ConfigError, match="duplicate"):
        api.sources_add(ws, tid, "https://a.com/research/", kind="page", name="A")


def test_readded_source_starts_with_fresh_state(ws_with_topic, probes):
    from contextlib import closing
    from evidence_echo.core import store
    ws, tid = ws_with_topic
    api.sources_add(ws, tid, "https://a.com/blog/", kind="page", name="A")
    with closing(store.connect(ws.topics_dir / tid / "echo.db")) as conn:
        store.record_fetch(conn, "A", "page", new=0, error="HTTP 500")
        store.set_source_fields(conn, "A", "page", cursor={"pattern": "/blog/"})
    api.sources_remove(ws, tid, "A")
    with closing(store.connect(ws.topics_dir / tid / "echo.db")) as conn:
        assert store.get_source_state(conn, "A") is not None   # spec Part 4: history kept
    api.sources_add(ws, tid, "https://a.com/report-2026", kind="once", name="A")
    with closing(store.connect(ws.topics_dir / tid / "echo.db")) as conn:
        assert store.get_source_state(conn, "A") is None


def _add_r(ws, tid, probes):
    api.sources_add(ws, tid, "https://a.com/feed", kind="rss", name="R")


def test_sources_set_fetch_every(ws_with_topic, probes):
    ws, tid = ws_with_topic
    _add_r(ws, tid, probes)
    assert api.sources_set(ws, tid, "R", fetch_every="7")["fetch_every"] == 7
    assert "fetch_every" not in api.sources_set(ws, tid, "R", fetch_every="default")
    with pytest.raises(ConfigError, match="fetch_every"):
        api.sources_set(ws, tid, "R", fetch_every="0")
    with pytest.raises(ConfigError, match="fetch_every"):
        api.sources_set(ws, tid, "R", fetch_every="2.5")
    with pytest.raises(ConfigError, match="--scan-depth or --fetch-every"):
        api.sources_set(ws, tid, "R")


def test_sources_add_fetch_every(ws_with_topic, probes):
    ws, tid = ws_with_topic
    out = api.sources_add(ws, tid, "https://a.com/feed", kind="rss", name="R", fetch_every="3")
    assert out["added"]["fetch_every"] == 3


def test_sources_list_schedule_fields(ws_with_topic, probes):
    ws, tid = ws_with_topic
    _add_r(ws, tid, probes)
    api.sources_set(ws, tid, "R", fetch_every="5")
    row = api.sources_list(ws, tid)[0]
    assert row["fetch_every"] == 5 and row["interval_days"] is None      # adaptive off: pin shown, not used
    api.topic_set(ws, tid, "adaptive_fetch", "on")
    row = api.sources_list(ws, tid)[0]
    assert row["interval_days"] == 5.0 and row["interval_source"] == "pinned"
    assert "capped" not in row
    assert row["next_due"] is None                                       # never checked


def test_sources_list_caps_pin_at_horizon(ws_with_topic, probes):
    ws, tid = ws_with_topic
    _add_r(ws, tid, probes)
    api.sources_set(ws, tid, "R", fetch_every="30")
    api.topic_set(ws, tid, "adaptive_fetch", "on")
    row = api.sources_list(ws, tid)[0]
    assert row["fetch_every"] == 30 and row["interval_days"] == 6.0       # default horizon 7
    assert row["interval_source"] == "pinned" and row["capped"] is True
    api.topic_set(ws, tid, "fetch_horizon_days", "20")
    assert api.sources_list(ws, tid)[0]["interval_days"] == 19.0


def test_sources_list_rounds_interval_and_skips_once(ws_with_topic, probes):
    from datetime import datetime, timedelta, timezone
    from contextlib import closing
    from evidence_echo.core import store
    ws, tid = ws_with_topic
    _add_r(ws, tid, probes)
    api.sources_add(ws, tid, "https://a.com/report-2026", kind="once", name="O")
    api.topic_set(ws, tid, "adaptive_fetch", "on")
    now = datetime.now(timezone.utc)
    with closing(store.connect(ws.topics_dir / tid / "echo.db")) as conn:
        for i, d in enumerate([0, 6.7, 13.4, 20.1]):                       # median gap 6.7 -> 3.35
            store.insert_item(conn, {"url": f"https://a.com/p{i}", "canonical_url": f"https://a.com/p{i}",
                                     "source_name": "R", "source_type": "rss", "title": "T", "excerpt": "",
                                     "published": (now - timedelta(days=d)).isoformat(),
                                     "fetched_at": now.isoformat(), "state": "seen", "date_unknown": 0})
        store.record_fetch(conn, "O", "once", new=0, error=None)
    rows = {r["name"]: r for r in api.sources_list(ws, tid)}
    assert rows["R"]["interval_days"] in (3.3, 3.4)
    assert rows["O"]["interval_days"] is None and rows["O"]["interval_source"] is None
    assert rows["O"]["next_due"] is None


def test_sources_set_validates_both_before_writing(ws_with_topic, probes):
    ws, tid = ws_with_topic
    _add_r(ws, tid, probes)
    with pytest.raises(ConfigError, match="fetch_every"):
        api.sources_set(ws, tid, "R", scan_depth="full", fetch_every="0")
    row = api.sources_list(ws, tid)[0]
    assert "scan_depth" not in row and "fetch_every" not in row


def test_sources_add_fetch_every_default_stores_nothing(ws_with_topic, probes):
    ws, tid = ws_with_topic
    out = api.sources_add(ws, tid, "https://a.com/feed", kind="rss", name="R", fetch_every="default")
    assert "fetch_every" not in out["added"]
    assert "fetch_every" not in api.sources_list(ws, tid)[0]
    out = api.sources_add(ws, tid, "jane-doe", kind="linkedin", fetch_every="default")
    assert "fetch_every" not in out["added"]


def test_legacy_add_accepts_fetch_every(ws_with_topic, probes, capsys):
    from evidence_echo import cli
    ws, tid = ws_with_topic
    assert cli.main(["--topic", "t", "sources", "add", "rss", "R", "https://a.com/feed",
                     "--fetch-every", "4"]) == 0
    assert cli.main(["--topic", "t", "sources", "add", "linkedin", "J", "jane-doe",
                     "--fetch-every", "3"]) == 0
    rows = {r["name"]: r for r in api.sources_list(ws, tid)}
    assert rows["R"]["fetch_every"] == 4 and rows["J"]["fetch_every"] == 3
    assert cli.main(["--topic", "t", "sources", "add", "rss", "R2", "https://a.com/feed",
                     "--fetch-every", "0"]) == 2


def test_sources_list_track_record_counts(ws_with_topic, probes):
    ws, tid = ws_with_topic
    _add_r(ws, tid, probes)
    row = api.sources_list(ws, tid)[0]                       # no database yet: zeros
    assert [row[k] for k in store.TRACK_KEYS] == [0, 0, 0, 0]
    conn = store.connect(ws.topics_dir / tid / "echo.db")
    for n, (flags, disputed) in enumerate([([], 0), (["no_baseline"], 1), (["misrepresented_primary"], 0)]):
        url = f"https://a.com/{n}"
        store.insert_items(conn, [make_item(url=url, source_name="R")])
        conn.execute("UPDATE items SET verdict_pass=1, red_flags=?, disputed=? WHERE url=?",
                     (json.dumps(flags), disputed, url))
    store.insert_items(conn, [make_item(url="https://a.com/x", source_name="R")])   # never judged
    conn.commit()
    conn.close()
    row = api.sources_list(ws, tid)[0]
    assert [row[k] for k in store.TRACK_KEYS] == [3, 1, 2, 1]
