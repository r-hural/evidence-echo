import importlib.util
import json
import os
from pathlib import Path

import pytest

from evidence_echo.core import config, store

# The real home, captured at import time (before any fixture changes the environment).
REAL_DATA_DIR = Path.home() / ".evidence-echo"


def _real_data_snapshot() -> list[str]:
    if not REAL_DATA_DIR.exists():
        return []
    return sorted(str(p.relative_to(REAL_DATA_DIR)) for p in REAL_DATA_DIR.rglob("*"))


def write_plugin_keys(plugin_data: Path, **keys) -> None:
    """The keys file the SessionStart hook writes from the plugin settings."""
    plugin_data.mkdir(parents=True, exist_ok=True)
    (plugin_data / "keys.json").write_text(json.dumps(keys), encoding="utf-8")


@pytest.fixture(autouse=True, scope="session")
def real_home_untouched():
    """Fails the run if any test (or a child process it started) changed ~/.evidence-echo."""
    before = _real_data_snapshot()
    yield
    assert _real_data_snapshot() == before, f"a test changed the real {REAL_DATA_DIR}"


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugins" / "evidence-echo"

ENGINE_ENV_VARS = (
    "EV_ECHO_DATA_DIR", "EV_ECHO_TOPIC", "EV_ECHO_PLUGIN_DATA", "EV_ECHO_KEYS_ID", "EV_ECHO_REPORTS_DIR",
    "EV_ECHO_TRIAGE_BACKEND",
    "EV_ECHO_JUDGMENT_BACKEND", "EV_ECHO_EMBEDDER", "EV_ECHO_OUTPUT_LANGUAGE",
    "EV_ECHO_FETCH_HORIZON_DAYS", "EV_ECHO_SCAN_DEPTH", "EV_ECHO_ASK_ON_DISPUTES", "EV_ECHO_ADAPTIVE_FETCH", "EV_ECHO_HTML_REPORTS", "EV_ECHO_REJECTIONS_REPORT", "EV_ECHO_OBSIDIAN_DIR", "EV_ECHO_RELATE", "EV_ECHO_PRIMARY_CHECK", "ANTHROPIC_API_KEY", "VOYAGE_API_KEY",
    "APIFY_API_TOKEN", "EV_ECHO_ANTHROPIC_API_KEY", "EV_ECHO_VOYAGE_API_KEY",
    "EV_ECHO_APIFY_API_TOKEN", "CLAUDE_PLUGIN_DATA", "CLAUDE_ENV_FILE", "PYTHONPATH",
    "CLAUDE_CONFIG_DIR",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Tests never see the developer's real keys, data dir or plugin options."""
    # child processes inherit os.environ: every home lookup in them goes to a temp folder
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(fake_home))
    for var in ("HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(var, raising=False)
    # migration reads HOME_DATA_DIR; a real ~/.evidence-echo must never be touched
    monkeypatch.setattr(config, "HOME_DATA_DIR", tmp_path / "no-home" / ".evidence-echo")
    for var in ENGINE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in [v for v in os.environ if v.startswith("CLAUDE_PLUGIN_OPTION_")]:
        monkeypatch.delenv(var)


@pytest.fixture(autouse=True)
def utc_display_zone(monkeypatch):
    """Displayed times never depend on the machine's zone; tests of local time set their own."""
    from datetime import timezone
    from evidence_echo.render import clock
    monkeypatch.setattr(clock, "ZONE", timezone.utc)


@pytest.fixture(autouse=True)
def fake_dns(request, monkeypatch):
    """The SSRF guard resolves every host; offline tests get one public address for any name,
    and no real connection: a test that reaches the network fails at once instead of waiting
    for a timeout."""
    if request.node.get_closest_marker("network"):
        return
    import httpcore
    from evidence_echo.sources import base
    monkeypatch.setattr(base, "resolve_host", lambda host: ["93.184.215.14"])

    def offline(self, host, port, **kw):
        raise httpcore.ConnectError(f"offline test: no real connection to {host}:{port}")
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", offline)


def load_script(relpath: str):
    """Import a plugin script (not part of the package) by its path under PLUGIN_DIR."""
    path = PLUGIN_DIR / relpath
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TEST_METRICS = ["Clicks", "Impressions", "Rankings", "Organic traffic", "CTR", "Other"]


@pytest.fixture
def bare_db(tmp_path):
    """A fresh database with no metric list (a topic connection seeds it)."""
    conn = store.connect(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture
def db(tmp_path):
    """A fresh database with a small metric list, so a judge answer can pick `clicks`, `ctr`, ..."""
    conn = store.connect(tmp_path / "test.db")
    store.seed_metrics(conn, TEST_METRICS)
    yield conn
    conn.close()


def make_item(**over):
    base = {
        "url": "https://example.com/post-1",
        "source_name": "Example Blog",
        "source_type": "rss",
        "title": "A title-tag experiment",
        "excerpt": "We ran a split test on 12k pages...",
        "published": "2026-08-10T00:00:00+00:00",
        "fetched_at": "2026-08-15T00:00:00+00:00",
    }
    base.update(over)
    return base


class FakeBackend:
    """LLMBackend test double: returns queued responses in order."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def complete(self, prompt, max_tokens=500):
        self.prompts.append(prompt)
        return self.responses.pop(0)


@pytest.fixture
def topic():
    from evidence_echo.core import config, topics
    return topics.load_template("seo", config.TEMPLATES_DIR)


def make_workspace(data_dir, topic_ids=("seo",), **global_values):
    """A configured workspace: config.json plus one topic per id (copied from the seo template)."""
    from dataclasses import replace
    from evidence_echo.core import config, settings, topics
    settings.save_global(data_dir, global_values)
    seo = topics.load_template("seo", config.TEMPLATES_DIR)
    for tid in topic_ids:
        topics.save_topic(data_dir / "topics", replace(seo, id=tid, name=tid.upper()))
    return data_dir


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """EV_ECHO_DATA_DIR = tmp_path/data, configured with the topic 'seo'."""
    data = tmp_path / "data"
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    return make_workspace(data)


_new_study_count = [0]


def seed_new_study(db, url=None) -> int:
    """One new verified study (judged, matched, skeptic agrees), so a report has something to show:
    a run without new studies writes no report. Returns the item id."""
    import json
    from evidence_echo.core import interfaces
    from evidence_echo.stages import judge, match, verify
    from tests.test_judge import VERDICT, _seed_extracted
    _new_study_count[0] += 1
    n = _new_study_count[0]
    item_id = _seed_extracted(db, url=url or f"https://example.com/new-{n}")
    facts = dict(VERDICT["facts"], match_keys={"key_numbers": [f"{n}.{n}%"]})   # its own study, never a match
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(item_id), facts=facts,
                                                 primary_url=f"https://example.com/original-{n}")]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(item_id), "tier": "A", "red_flags": [], "note": "ok"}]}))
    return item_id


def seed_topic_study(topic_id="seo") -> int:
    """seed_new_study in a workspace topic's database (EV_ECHO_DATA_DIR set by the workspace fixture)."""
    from contextlib import closing
    from evidence_echo.core import config, store
    with closing(store.connect(config.workspace().topics_dir / topic_id / "echo.db")) as db:
        return seed_new_study(db)
