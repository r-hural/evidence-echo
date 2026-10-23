import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, store
from evidence_echo.core.settings import ConfigError
from evidence_echo.stages import primary, report
from tests.primary_fixtures import ORIGINAL_URL, fake_ctx, original_html, report_item

OTHER = "https://other.example.org/paper"


@pytest.fixture
def cfg(workspace):
    return config.load()


def _unreachable(conn) -> int:
    iid = report_item(conn)
    for _ in range(3):
        primary.prepare(conn, fake_ctx({ORIGINAL_URL: 404}))
    return iid


def test_reset_clears_an_unreachable_original_and_its_check_rows(db):
    iid = _unreachable(db)
    assert store.reset_primary_sources(db) == {"sources": 1, "checks": 1, "already_reported": 0}
    assert store.primary_source(db, ORIGINAL_URL) is None
    assert store.primary_check(db, iid) is None


def test_the_next_run_fetches_again(db):
    iid = _unreachable(db)
    store.reset_primary_sources(db)
    calls = []
    counts = primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html()}, calls))
    assert calls == [ORIGINAL_URL] and counts["fetched"] == 1 and counts["ready"] == 1
    assert store.primary_source(db, ORIGINAL_URL)["status"] == "ok"
    assert [p.item["id"] for p in primary.pending(db)] == [iid]


def test_reset_clears_a_failed_original(db):
    report_item(db)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: 404}))         # one attempt: failed
    assert store.reset_primary_sources(db) == {"sources": 1, "checks": 0, "already_reported": 0}
    assert store.primary_source(db, ORIGINAL_URL) is None


def test_reset_keeps_fetched_originals_and_decided_checks(db):
    iid = report_item(db)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html()}))
    store.save_primary_check(db, iid, primary_url=ORIGINAL_URL, status="undecided",
                             prompt_version="primary-1")
    assert store.reset_primary_sources(db) == {"sources": 0, "checks": 0, "already_reported": 0}
    assert store.primary_source(db, ORIGINAL_URL)["status"] == "ok"
    assert store.primary_check(db, iid)["status"] == "undecided"


def test_reset_one_url_leaves_the_others(db):
    _unreachable(db)
    for _ in range(3):
        store.record_primary_fetch(db, OTHER, error="404")
    assert store.reset_primary_sources(db, OTHER) == {"sources": 1, "checks": 0, "already_reported": 0}
    assert store.primary_source(db, ORIGINAL_URL)["status"] == "unreachable"
    assert store.primary_source(db, OTHER) is None


def test_reset_counts_rows_of_already_reported_items(db, tmp_path, topic):
    _unreachable(db)
    report.render(db, '{"results": []}', tmp_path / "reports", "English", topic)
    assert store.reset_primary_sources(db) == {"sources": 1, "checks": 1, "already_reported": 1}


def test_api_reset_primary_all(cfg):
    conn = store.connect(cfg.db_path); _unreachable(conn); conn.close()
    assert api.reset_primary(cfg) == {"reset": "primary", "sources": 1, "checks": 1,
                                      "already_reported": 0}


def test_api_reset_primary_canonicalises_the_url(cfg):
    conn = store.connect(cfg.db_path); _unreachable(conn); conn.close()
    out = api.reset_primary(cfg, "https://ORIG.example.com/study?utm_source=x")
    assert out == {"reset": "primary", "url": ORIGINAL_URL, "sources": 1, "checks": 1,
                   "already_reported": 0}


def test_api_reset_primary_unknown_url_resets_nothing(cfg):
    assert api.reset_primary(cfg, OTHER) == {"reset": "primary", "url": OTHER, "sources": 0,
                                             "checks": 0, "already_reported": 0}


def test_api_reset_primary_rejects_a_non_web_url(cfg):
    with pytest.raises(ConfigError, match="--url must be an http"):
        api.reset_primary(cfg, "ftp://x/y")


def test_cli_reset_primary(cfg, capsys):
    conn = store.connect(cfg.db_path); _unreachable(conn); conn.close()
    assert cli.main(["reset", "--stage", "primary", "--url", ORIGINAL_URL]) == 0
    assert json.loads(capsys.readouterr().out)["sources"] == 1


def test_cli_url_needs_the_primary_stage(cfg, capsys):
    assert cli.main(["reset", "--stage", "triage", "--url", ORIGINAL_URL]) == 2
    assert "--url goes only with --stage primary" in capsys.readouterr().err


def test_cli_other_resets_still_work(cfg, capsys):
    assert cli.main(["reset", "--stage", "triage"]) == 0
    assert json.loads(capsys.readouterr().out) == {"reset_to": "fetched"}


def test_primary_is_not_an_item_state_reset():
    assert "primary" not in api.RESET_TARGET
