import json
import os
import time

import pytest

from evidence_echo.core import config, migrate, settings, topics
from tests.conftest import PLUGIN_DIR

TEMPLATES = PLUGIN_DIR / "templates"


def _old(root, edition="seo", db=b"db", sources=None, report=None, legacy=False):
    d = root if legacy else root / edition
    d.mkdir(parents=True, exist_ok=True)
    (d / "echo.db").write_bytes(db)
    if sources is not None:
        (d / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    if report:
        (d / "reports").mkdir(exist_ok=True)
        (d / "reports" / report).write_text("r", encoding="utf-8")
    return d


def _run(tmp_path):
    return migrate.run(tmp_path / "data", tmp_path / "pd", tmp_path / "home", TEMPLATES)


MINE = {"linkedin_apify_actor": "act", "sources": [
    {"type": "rss", "name": "Mine", "url": "https://mine.example/feed"}]}


def test_04_plugin_data_is_migrated(tmp_path):
    _old(tmp_path / "pd", sources=MINE, report="2026-10-01-report.md")
    msgs = _run(tmp_path)
    target = tmp_path / "data" / "topics" / "seo"
    assert (target / "echo.db").read_bytes() == b"db"
    t = topics.load_topic(tmp_path / "data" / "topics", "seo")
    assert t.sources == MINE["sources"] and t.linkedin_apify_actor == "act"
    assert t.domain == "SEO and AI search" and t.template == "seo"
    assert (tmp_path / "data" / "reports" / "seo" / "2026-10-01-report.md").exists()
    assert not (tmp_path / "pd" / "seo" / "echo.db").exists()
    assert settings.load_global(tmp_path / "data") == {}  # config.json written
    assert any("Data moved to" in m and "/evidence-echo:setup" in m for m in msgs)


def test_04_home_layout_inside_new_data_dir(tmp_path):
    home = tmp_path / "data"  # ~/.evidence-echo is both the old and the new root
    _old(home)
    msgs = migrate.run(home, tmp_path / "pd", home, TEMPLATES)
    assert (home / "topics" / "seo" / "echo.db").exists()
    assert not (home / "seo" / "echo.db").exists()
    assert msgs


def test_03_root_db_is_seo(tmp_path):
    _old(tmp_path / "home", legacy=True, sources=MINE)
    _run(tmp_path)
    assert (tmp_path / "data" / "topics" / "seo" / "echo.db").exists()
    assert topics.load_topic(tmp_path / "data" / "topics", "seo").sources == MINE["sources"]


def test_newer_candidate_wins_other_is_left_with_warning(tmp_path):
    old = _old(tmp_path / "home", db=b"old")
    new = _old(tmp_path / "pd", db=b"new")
    past = time.time() - 3600
    os.utime(old / "echo.db", (past, past))
    msgs = _run(tmp_path)
    assert (tmp_path / "data" / "topics" / "seo" / "echo.db").read_bytes() == b"new"
    assert (old / "echo.db").read_bytes() == b"old"
    assert any(str(old) in m for m in msgs)


def test_rerun_is_a_noop(tmp_path):
    _old(tmp_path / "pd")
    _run(tmp_path)
    assert _run(tmp_path) == []


def test_existing_topic_is_never_overwritten(tmp_path):
    _old(tmp_path / "pd", db=b"old")
    target = tmp_path / "data" / "topics" / "seo"
    target.mkdir(parents=True)
    (target / "echo.db").write_bytes(b"current")
    msgs = _run(tmp_path)
    assert (target / "echo.db").read_bytes() == b"current"
    assert (tmp_path / "pd" / "seo" / "echo.db").read_bytes() == b"old"
    assert any("left in place" in m for m in msgs)
    assert _run(tmp_path) == []  # warned once


def test_locked_db_is_retried(tmp_path, monkeypatch):
    _old(tmp_path / "pd")
    real = os.replace

    def locked(src, dst):
        if str(src).endswith("echo.db"):
            raise PermissionError("[WinError 32] file in use")
        return real(src, dst)

    monkeypatch.setattr(migrate.os, "replace", locked)
    msgs = _run(tmp_path)  # does not raise
    assert any("Could not move" in m and "will retry on the next run" in m for m in msgs)
    assert (tmp_path / "pd" / "seo" / "echo.db").exists()
    monkeypatch.setattr(migrate.os, "replace", real)
    _run(tmp_path)
    assert (tmp_path / "data" / "topics" / "seo" / "echo.db").exists()


def test_data_dir_is_legacy_root_with_reports_folder(tmp_path):
    home = tmp_path / "data"
    _old(home, legacy=True)
    (home / "reports").mkdir()
    (home / "reports" / "x.md").write_text("x", encoding="utf-8")
    (home / "reports" / "other").mkdir()
    (home / "reports" / "other" / "o.md").write_text("o", encoding="utf-8")
    migrate.run(home, tmp_path / "pd", home, TEMPLATES)
    assert (home / "reports" / "seo" / "x.md").exists()
    assert (home / "reports" / "other" / "o.md").exists()
    assert not (home / "reports" / "seo" / "seo").exists()
    assert (home / "topics" / "seo" / "echo.db").exists()


def test_skipped_report_is_reported(tmp_path):
    _old(tmp_path / "pd", report="r.md")
    target = tmp_path / "data" / "reports" / "seo"
    target.mkdir(parents=True)
    (target / "r.md").write_text("keep", encoding="utf-8")
    msgs = _run(tmp_path)
    assert (target / "r.md").read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "pd" / "seo" / "reports" / "r.md").exists()
    assert any("Report left in place" in m and "already exists" in m for m in msgs)


def test_migrated_topic_is_never_migrated_again(tmp_path):
    import shutil
    _old(tmp_path / "pd")
    _run(tmp_path)
    assert settings.read_state(tmp_path / "data")["migrated"] == ["seo"]
    shutil.rmtree(tmp_path / "data" / "topics" / "seo")
    _old(tmp_path / "pd")
    msgs = _run(tmp_path)
    assert not (tmp_path / "data" / "topics" / "seo").exists()
    assert len(msgs) == 1 and "left in place" in msgs[0]
    assert _run(tmp_path) == []


def test_unknown_edition_becomes_blank_topic_with_warning(tmp_path):
    _old(tmp_path / "pd", edition="gardening", sources=MINE)
    msgs = _run(tmp_path)
    t = topics.load_topic(tmp_path / "data" / "topics", "gardening")
    assert (t.template, t.name, t.domain, t.scope) == ("blank", "gardening", "gardening", "gardening")
    assert any("edit its name, domain and scope" in m for m in msgs)


def test_folders_that_are_not_editions_are_ignored(tmp_path):
    for name in ("venv", "cache", "reports", "topics", ".hidden"):
        (tmp_path / "pd" / name).mkdir(parents=True)
    assert _run(tmp_path) == []


def test_existing_config_is_kept(tmp_path):
    settings.save_global(tmp_path / "data", {"output_language": "de"})
    _old(tmp_path / "pd")
    _run(tmp_path)
    assert settings.load_global(tmp_path / "data") == {"output_language": "de"}


def test_workspace_runs_migration_and_prints_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pd"))
    monkeypatch.setattr(config, "HOME_DATA_DIR", tmp_path / "home")
    _old(tmp_path / "pd")
    ws = config.workspace()
    assert ws.configured
    assert "Data moved to" in capsys.readouterr().err
    config.workspace()
    assert capsys.readouterr().err == ""
    assert config.load().topic.id == "seo"


def _real_db(folder):
    import sqlite3
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "echo.db")
    conn.execute("CREATE TABLE t (x)")
    conn.executemany("INSERT INTO t VALUES (?)", [("row %d" % i,) for i in range(500)])
    conn.commit()
    conn.close()
    return (folder / "echo.db").read_bytes()


def _cross_device(monkeypatch):
    """os.replace of the old echo.db fails as it does between two drives."""
    import errno
    real = os.replace

    def replace(src, dst):
        if str(src).endswith(os.sep + "echo.db") and "topics" not in str(src):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst)

    monkeypatch.setattr(migrate.os, "replace", replace)


def test_cross_device_copy_failing_midway_keeps_source_and_retries(tmp_path, monkeypatch):
    original = _real_db(tmp_path / "pd" / "seo")
    _cross_device(monkeypatch)
    real_copy = migrate.shutil.copy2

    def half_copy(src, dst, **kw):
        with open(dst, "wb") as fh:
            fh.write(open(src, "rb").read()[:1000])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(migrate.shutil, "copy2", half_copy)
    msgs = _run(tmp_path)
    target = tmp_path / "data" / "topics" / "seo"
    assert (tmp_path / "pd" / "seo" / "echo.db").read_bytes() == original
    assert not (target / "echo.db").exists()
    assert not (target / "echo.db.migrating").exists()
    assert not (target / topics.TOPIC_FILE).exists()  # topic invisible until the db is there
    assert any("will retry on the next run" in m for m in msgs)
    assert not any("Delete" in m for m in msgs)

    monkeypatch.setattr(migrate.shutil, "copy2", real_copy)
    msgs = _run(tmp_path)
    assert (target / "echo.db").read_bytes() == original
    assert not (tmp_path / "pd" / "seo" / "echo.db").exists()
    assert not (target / "echo.db.migrating").exists()
    assert any("Data moved to" in m for m in msgs)


def test_cross_device_copy_that_fails_verification_is_not_used(tmp_path, monkeypatch):
    original = _real_db(tmp_path / "pd" / "seo")
    _cross_device(monkeypatch)

    def short_copy(src, dst, **kw):  # silently truncated (no exception)
        with open(dst, "wb") as fh:
            fh.write(open(src, "rb").read()[:1000])

    monkeypatch.setattr(migrate.shutil, "copy2", short_copy)
    msgs = _run(tmp_path)
    target = tmp_path / "data" / "topics" / "seo"
    assert (tmp_path / "pd" / "seo" / "echo.db").read_bytes() == original
    assert not (target / "echo.db").exists()
    assert not (target / "echo.db.migrating").exists()
    assert any("will retry on the next run" in m for m in msgs)


def test_stale_migrating_file_is_removed(tmp_path):
    _old(tmp_path / "pd")
    target = tmp_path / "data" / "topics" / "seo"
    target.mkdir(parents=True)
    (target / "echo.db.migrating").write_bytes(b"partial")
    _run(tmp_path)
    assert (target / "echo.db").read_bytes() == b"db"
    assert not (target / "echo.db.migrating").exists()


@pytest.mark.parametrize("journal", ["echo.db-journal", "echo.db-wal"])
def test_db_with_journal_is_skipped_and_retried(tmp_path, journal):
    src = _old(tmp_path / "pd")
    (src / journal).write_bytes(b"j")
    msgs = _run(tmp_path)
    target = tmp_path / "data" / "topics" / "seo"
    assert (src / "echo.db").exists()
    assert not (target / "echo.db").exists() and not (target / topics.TOPIC_FILE).exists()
    assert any(journal in m and "will retry on the next run" in m for m in msgs)
    (src / journal).unlink()
    _run(tmp_path)
    assert (target / "echo.db").read_bytes() == b"db"


def test_failed_move_removes_the_topic_file_it_created(tmp_path, monkeypatch):
    settings.save_global(tmp_path / "data", {})  # set up earlier: pipeline commands would run
    _old(tmp_path / "pd")
    real = os.replace

    def locked(src, dst):
        if str(src).endswith(os.sep + "echo.db"):
            raise PermissionError(13, "file in use")
        return real(src, dst)

    monkeypatch.setattr(migrate.os, "replace", locked)
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pd"))
    target = tmp_path / "data" / "topics" / "seo"
    with pytest.raises(settings.ConfigError, match="Unknown topic 'seo'"):
        config.load("seo")
    assert not (target / topics.TOPIC_FILE).exists()
    assert not (target / "echo.db").exists()  # no empty db created in its place
    assert (tmp_path / "pd" / "seo" / "echo.db").exists()


def test_failed_move_keeps_a_topic_file_it_did_not_create(tmp_path, monkeypatch):
    _old(tmp_path / "pd")
    topics.save_topic(tmp_path / "data" / "topics",
                      topics.load_template("seo", TEMPLATES))
    real = os.replace

    def locked(src, dst):
        if str(src).endswith(os.sep + "echo.db"):
            raise PermissionError(13, "file in use")
        return real(src, dst)

    monkeypatch.setattr(migrate.os, "replace", locked)
    _run(tmp_path)
    assert (tmp_path / "data" / "topics" / "seo" / topics.TOPIC_FILE).exists()


def test_leftover_of_a_topic_never_migrated_is_not_called_deletable(tmp_path):
    _old(tmp_path / "pd", db=b"old")
    target = tmp_path / "data" / "topics" / "seo"
    target.mkdir(parents=True)
    (target / "echo.db").write_bytes(b"current")
    msgs = _run(tmp_path)
    (msg,) = msgs
    assert str(tmp_path / "pd" / "seo") in msg and "not migrated" in msg
    assert "keep" in msg and "Delete" not in msg


def test_leftover_of_a_migrated_topic_may_be_deleted(tmp_path):
    old = _old(tmp_path / "home", db=b"old")
    _old(tmp_path / "pd", db=b"new")
    past = time.time() - 3600
    os.utime(old / "echo.db", (past, past))
    msgs = _run(tmp_path)
    (msg,) = [m for m in msgs if str(old) in m]
    assert "Delete it when not needed" in msg


def test_rename_retired_settings(tmp_path):
    data = tmp_path / "data"
    (data / "topics" / "seo").mkdir(parents=True)
    (data / "topics" / "blog").mkdir(parents=True)
    (data / "config.json").write_text('{"version": 1, "lookback_days": 14}', encoding="utf-8")
    (data / "topics" / "seo" / "topic.json").write_text(
        '{"id": "seo", "lookback_days": null, "name": "S"}', encoding="utf-8")
    (data / "topics" / "blog" / "topic.json").write_text(
        '{"id": "blog", "research_horizon_days": 30, "lookback_days": 9}', encoding="utf-8")
    messages = migrate.rename_retired_settings(data)

    def read(p):
        return json.loads(p.read_text(encoding="utf-8"))
    assert read(data / "config.json") == {"version": 1, "fetch_horizon_days": 14}
    assert list(read(data / "topics" / "seo" / "topic.json")) == ["id", "fetch_horizon_days", "name"]
    assert read(data / "topics" / "blog" / "topic.json") == {"id": "blog", "fetch_horizon_days": 30}
    assert len(messages) == 3 and all("lookback_days" in m for m in messages)
    assert migrate.rename_retired_settings(data) == []          # second run: nothing to do


def _topic_json(tmp_path, text):
    data = tmp_path / "data"
    (data / "topics" / "t").mkdir(parents=True)
    (data / "topics" / "t" / "topic.json").write_text(text, encoding="utf-8")
    return data, data / "topics" / "t" / "topic.json"


def test_rename_retired_settings_glossary_keys(tmp_path):
    data, path = _topic_json(tmp_path, json.dumps({
        "id": "t", "name": "T", "domain": "d", "scope": "s", "sources": [],
        "themes": ["A", "B"], "research_horizon_days": 14,
        "template_themes_declined": "abc"}))
    messages = migrate.rename_retired_settings(data)
    got = json.loads(path.read_text(encoding="utf-8"))
    assert list(got) == ["id", "name", "domain", "scope", "sources", "subtopics",
                         "fetch_horizon_days", "template_subtopics_declined"]
    assert got["subtopics"] == ["A", "B"] and got["fetch_horizon_days"] == 14
    assert got["template_subtopics_declined"] == "abc"
    assert len(messages) == 1


def test_rename_retired_settings_lookback_goes_straight_to_fetch_horizon(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text('{"lookback_days": 14}', encoding="utf-8")
    migrate.rename_retired_settings(data)
    assert json.loads((data / "config.json").read_text(encoding="utf-8")) == {"fetch_horizon_days": 14}


def test_rename_retired_settings_new_key_wins(tmp_path):
    data, path = _topic_json(tmp_path, '{"research_horizon_days": 7, "fetch_horizon_days": 30}')
    migrate.rename_retired_settings(data)
    assert json.loads(path.read_text(encoding="utf-8")) == {"fetch_horizon_days": 30}


def test_rename_retired_settings_research_horizon_beats_lookback(tmp_path):
    data, path = _topic_json(tmp_path, '{"lookback_days": 9, "research_horizon_days": 30}')
    migrate.rename_retired_settings(data)
    assert json.loads(path.read_text(encoding="utf-8")) == {"fetch_horizon_days": 30}


def test_rename_retired_settings_leaves_broken_file(tmp_path):
    data = tmp_path / "data"
    (data / "topics" / "bad").mkdir(parents=True)
    (data / "topics" / "bad" / "topic.json").write_text('{"lookback_days": 9', encoding="utf-8")
    assert migrate.rename_retired_settings(data) == []
    assert (data / "topics" / "bad" / "topic.json").read_text(encoding="utf-8") == '{"lookback_days": 9'
