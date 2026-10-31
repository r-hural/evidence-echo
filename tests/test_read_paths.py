import json
import os
from dataclasses import replace

import pytest

from evidence_echo.core import config, settings, store, topics
from tests.conftest import make_workspace
from tests.import_guard import run_guarded


def snapshot(root):
    out = {}
    for p in sorted(root.rglob("*")):
        out[str(p.relative_to(root))] = (None, p.stat().st_mtime_ns) if p.is_dir() else (
            p.read_bytes(), p.stat().st_mtime_ns)
    return out


def with_db(data_dir, topic_id):
    store.connect(data_dir / "topics" / topic_id / "echo.db").close()


def test_read_paths_creates_nothing(tmp_path, monkeypatch):
    data = make_workspace(tmp_path / "data", ("seo", "cro"))
    with_db(data, "seo")
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    before = snapshot(data)
    paths = config.read_paths()
    assert [t.id for t in paths.topics] == ["cro", "seo"]
    assert paths.topics[1].db_path == data / "topics" / "seo" / "echo.db"
    assert snapshot(data) == before
    gone = tmp_path / "nowhere"
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(gone))
    with pytest.raises(config.SetupRequired):
        config.read_paths()
    assert not gone.exists()


@pytest.mark.parametrize("value", ["${user_config.data_dir}", ""])
def test_read_paths_unset_placeholder_means_default(tmp_path, monkeypatch, value):
    make_workspace(config.HOME_DATA_DIR, ("seo",))
    monkeypatch.setenv("EV_ECHO_DATA_DIR", value)
    assert config.read_paths().data_dir == config.HOME_DATA_DIR


def test_read_paths_broken_topic_is_listed(tmp_path, monkeypatch):
    data = make_workspace(tmp_path / "data", ("seo", "cro"))
    (data / "topics" / "cro" / "topic.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    by_id = {t.id: t for t in config.read_paths().topics}
    assert by_id["cro"].topic is None and "topic.json" in by_id["cro"].error
    assert by_id["seo"].topic is not None and by_id["seo"].error is None


def test_read_paths_grouping_mode_and_disclaimer(tmp_path, monkeypatch):
    data = make_workspace(tmp_path / "data", ("seo",), grouping_mode="quotes")
    ex = topics.load_template("exercise", config.TEMPLATES_DIR)
    topics.save_topic(data / "topics", replace(ex, id="fit", grouping_mode="findings"))
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    by_id = {t.id: t for t in config.read_paths().topics}
    assert by_id["seo"].grouping_mode == "quotes" and by_id["seo"].disclaimer is None
    assert by_id["fit"].grouping_mode == "findings"
    assert by_id["fit"].disclaimer == ex.grading["disclaimer"] and ex.grading["disclaimer"]


def test_read_path_modules_are_stdlib_only(tmp_path, monkeypatch):
    data = make_workspace(tmp_path / "data", ("seo",))
    env = {**os.environ, "EV_ECHO_DATA_DIR": str(data)}
    code = ("import evidence_echo.core.config, evidence_echo.core.store, evidence_echo.core.knowledge, "
            "evidence_echo.render.model\n"
            "from evidence_echo.core import config\nconfig.read_paths()\n")
    result = run_guarded(code, env=env)
    assert result.returncode == 0, result.stderr.decode()
    assert b"non-stdlib import" not in result.stderr


def test_a_bad_setting_lists_the_topic_with_its_error(tmp_path, monkeypatch):
    data = make_workspace(tmp_path / "data", ("seo", "cro"))
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    monkeypatch.setenv("EV_ECHO_GROUPING_MODE", "bogus")
    paths = config.read_paths()
    assert [t.id for t in paths.topics] == ["cro", "seo"]
    assert all(t.error and t.topic is None for t in paths.topics)
