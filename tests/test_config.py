# tests/test_config.py
import json
import os
from pathlib import Path

import pytest

from evidence_echo.core import config, settings, topics
from evidence_echo.core.settings import ConfigError
from tests.conftest import make_workspace


def _env(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_data_dir_env_then_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pd"))
    assert config.workspace().data_dir == config.HOME_DATA_DIR  # plugin data is never the data dir
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "d"))
    ws = config.workspace()
    assert ws.data_dir == tmp_path / "d" and ws.data_dir.is_dir()
    assert ws.topics_dir == tmp_path / "d" / "topics"


@pytest.mark.parametrize("var", ["CLAUDE_PLUGIN_DATA", "EV_ECHO_PLUGIN_DATA"])
@pytest.mark.parametrize("sub", ["", "seo"])
def test_data_dir_env_inside_plugin_data_is_ignored(tmp_path, monkeypatch, capsys, var, sub):
    # a stale 0.4 export pointed EV_ECHO_DATA_DIR at the plugin data (deleted on uninstall)
    monkeypatch.setenv(var, str(tmp_path / "pd"))
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "pd" / sub))
    assert config.workspace().data_dir == config.HOME_DATA_DIR
    assert "EV_ECHO_DATA_DIR" in capsys.readouterr().err
    assert not (tmp_path / "pd" / "topics").exists()


def test_data_dir_env_under_claude_plugins_data_is_ignored(tmp_path, monkeypatch):
    # the 0.4 session case: no plugin data variable reaches the engine
    stale = Path.home() / ".claude" / "plugins" / "data" / "evidence-echo-evidence-echo"
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(stale))
    assert config.workspace().data_dir == config.HOME_DATA_DIR
    ws, _ = config.workspace_lenient()
    assert ws.data_dir == config.HOME_DATA_DIR


def test_data_dir_env_beside_plugin_data_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "pd"))
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "pd-data"))
    assert config.workspace().data_dir == tmp_path / "pd-data"


def test_data_dir_env_must_be_absolute(monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", "relative/dir")
    with pytest.raises(ConfigError, match="EV_ECHO_DATA_DIR"):
        config.workspace()


def test_plugin_data_dir_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "d"))
    assert config.workspace().plugin_data_dir == tmp_path / "d" / "cache"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "cpd"))
    assert config.workspace().plugin_data_dir == tmp_path / "cpd"
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(tmp_path / "epd"))
    assert config.workspace().plugin_data_dir == tmp_path / "epd"


def test_unconfigured_workspace_and_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    ws = config.workspace()
    assert ws.settings is None and not ws.configured
    with pytest.raises(config.SetupRequired, match="not set up"):
        config.load()


def test_configured_without_topics_needs_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    settings.save_global(tmp_path, {})
    with pytest.raises(config.SetupRequired, match="no topics yet"):
        config.load()


def test_single_topic_is_selected(workspace):
    cfg = config.load()
    assert cfg.topic.id == "seo"
    assert cfg.topic_dir == workspace / "topics" / "seo"
    assert cfg.db_path == workspace / "topics" / "seo" / "echo.db"


def test_several_topics_need_a_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    make_workspace(tmp_path, ("seo", "diet"))
    with pytest.raises(ConfigError, match=r"several topics — pass --topic <id>: diet, seo"):
        config.load()
    assert config.load("diet").topic.id == "diet"
    monkeypatch.setenv("EV_ECHO_TOPIC", "seo")
    assert config.load().topic.id == "seo"
    assert config.load("diet").topic.id == "diet"  # --topic beats EV_ECHO_TOPIC


def test_unknown_topic(workspace):
    with pytest.raises(ConfigError, match=r"Unknown topic 'nope'\. Topics: seo"):
        config.load("nope")


def test_defaults(workspace):
    cfg = config.load()
    assert (cfg.triage_backend, cfg.judgment_backend, cfg.embedder) == ("session", "session", "none")
    assert (cfg.output_language, cfg.fetch_horizon_days) == ("en", 7)
    assert cfg.reports_dir == workspace / "reports" / "seo"
    assert cfg.overview_dir == workspace / "reports"


def test_global_then_topic_then_env(workspace, tmp_path, monkeypatch):
    settings.save_global(workspace, {"output_language": "de", "fetch_horizon_days": 14,
                                     "reports_dir": str(tmp_path / "R")})
    cfg = config.load()
    assert (cfg.output_language, cfg.fetch_horizon_days) == ("de", 14)
    assert cfg.reports_dir == tmp_path / "R" / "seo"
    assert cfg.overview_dir == tmp_path / "R"
    t = topics.load_topic(workspace / "topics", "seo")
    from dataclasses import replace
    topics.save_topic(workspace / "topics", replace(
        t, output_language="fr", reports_dir=str(tmp_path / "vault")))
    cfg = config.load()
    assert cfg.output_language == "fr"
    assert cfg.reports_dir == tmp_path / "vault"          # topic folder used as is
    assert cfg.overview_dir == tmp_path / "R"
    _env(monkeypatch, EV_ECHO_OUTPUT_LANGUAGE="it", EV_ECHO_FETCH_HORIZON_DAYS="3")
    cfg = config.load()
    assert (cfg.output_language, cfg.fetch_horizon_days) == ("it", 3)


def test_env_horizon_invalid_names_variable(workspace, monkeypatch):
    monkeypatch.setenv("EV_ECHO_FETCH_HORIZON_DAYS", "3651")
    with pytest.raises(ConfigError, match="EV_ECHO_FETCH_HORIZON_DAYS"):
        config.load()


def test_key_aware_auto(workspace, tmp_path, monkeypatch):
    from tests.conftest import write_plugin_keys
    write_plugin_keys(tmp_path / "plugin-data", ANTHROPIC_API_KEY="sk", VOYAGE_API_KEY="pa")
    cfg = config.load()
    assert (cfg.triage_backend, cfg.judgment_backend, cfg.embedder) == ("api", "api", "voyage")
    settings.save_global(workspace, {"judgment_backend": "session", "embedder": "auto"})
    cfg = config.load()
    assert (cfg.triage_backend, cfg.judgment_backend, cfg.embedder) == ("api", "session", "voyage")
    monkeypatch.setenv("EV_ECHO_TRIAGE_BACKEND", "session")
    assert config.load().triage_backend == "session"


def test_keys_come_only_from_the_plugin_settings(workspace, tmp_path, monkeypatch):
    for name in config.KEY_NAMES:  # a key in the user's environment is never picked up
        monkeypatch.setenv(name, "shell")
        monkeypatch.setenv("EV_ECHO_" + name, "explicit")
    cfg = config.load()
    assert (cfg.anthropic_api_key, cfg.voyage_api_key, cfg.apify_api_token) == ("", "", "")
    assert (cfg.judgment_backend, cfg.embedder) == ("session", "none")
    pd = tmp_path / "plugin-data"
    pd.mkdir()
    (pd / "keys.json").write_text(json.dumps({"ANTHROPIC_API_KEY": "plugin"}), encoding="utf-8")
    assert config.load().anthropic_api_key == "plugin"


def test_session_keys_file_is_read_by_its_id(workspace, tmp_path, monkeypatch):
    pd = tmp_path / "plugin-data"
    (pd / "keys").mkdir(parents=True)
    (pd / "keys" / "s1.json").write_text(json.dumps({"VOYAGE_API_KEY": "mine"}), encoding="utf-8")
    (pd / "keys.json").write_text(json.dumps({"ANTHROPIC_API_KEY": "stale"}), encoding="utf-8")
    monkeypatch.setenv("EV_ECHO_KEYS_ID", "s1")
    cfg = config.load()
    assert (cfg.voyage_api_key, cfg.anthropic_api_key) == ("mine", "")  # never the shared file
    monkeypatch.setenv("EV_ECHO_KEYS_ID", "s2")  # a session without keys has no file
    assert config.load().voyage_api_key == ""


def test_session_keys_file_is_touched_on_read(workspace, tmp_path, monkeypatch):
    path = tmp_path / "plugin-data" / "keys" / "s1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"VOYAGE_API_KEY": "mine"}), encoding="utf-8")
    os.utime(path, (1_000_000, 1_000_000))
    monkeypatch.setenv("EV_ECHO_KEYS_ID", "s1")
    config.load()
    assert path.stat().st_mtime > 1_000_000


@pytest.mark.parametrize("kid", ["../keys", "a/b", "x" * 65])
def test_unsafe_keys_id_reads_no_keys(workspace, tmp_path, monkeypatch, kid):
    pd = tmp_path / "plugin-data"
    pd.mkdir()
    (pd / "keys.json").write_text(json.dumps({"ANTHROPIC_API_KEY": "stale"}), encoding="utf-8")
    monkeypatch.setenv("EV_ECHO_KEYS_ID", kid)
    assert config.load().anthropic_api_key == ""


def test_invalid_keys_file_names_the_file(workspace, tmp_path):
    (tmp_path / "plugin-data").mkdir()
    (tmp_path / "plugin-data" / "keys.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError, match="keys.json"):
        config.load()


def test_broken_topic_names_file(workspace):
    (workspace / "topics" / "seo" / "topic.json").write_text('{"id": "seo"}', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"seo/topic\.json: field 'name' missing"):
        config.load()


def test_templates_dir_constant():
    assert config.TEMPLATES_DIR == config.PLUGIN_ROOT / "templates"
    assert (config.TEMPLATES_DIR / "seo.json").exists()


def test_home_data_dir_constant():
    import importlib.util
    spec = importlib.util.spec_from_file_location("config_copy", config.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert fresh.HOME_DATA_DIR == Path.home() / ".evidence-echo"


def test_env_reports_dir_outranks_topic_and_global(workspace, tmp_path, monkeypatch):
    from dataclasses import replace
    env_base = tmp_path / "envR"
    monkeypatch.setenv("EV_ECHO_REPORTS_DIR", str(env_base))
    cfg = config.load()
    assert cfg.reports_dir == env_base / "seo" and cfg.overview_dir == env_base
    t = topics.load_topic(workspace / "topics", "seo")
    topics.save_topic(workspace / "topics", replace(t, reports_dir=str(tmp_path / "vault")))
    settings.save_global(workspace, {"reports_dir": str(tmp_path / "R")})
    cfg = config.load()
    assert cfg.reports_dir == env_base / "seo" and cfg.overview_dir == env_base


def test_workspace_open_renames_lookback_days(workspace):
    (workspace / "config.json").write_text('{"version": 1, "lookback_days": 21}', encoding="utf-8")
    assert config.workspace().settings == {"fetch_horizon_days": 21}


def test_adaptive_fetch_precedence(workspace, monkeypatch):
    from dataclasses import replace
    assert config.load().adaptive_fetch == "off"                       # default
    settings.save_global(workspace, {"adaptive_fetch": "on"})
    assert config.load().adaptive_fetch == "on"                        # config.json
    t = topics.load_topic(workspace / "topics", "seo")
    topics.save_topic(workspace / "topics", replace(t, adaptive_fetch="off"))
    assert config.load().adaptive_fetch == "off"                       # topic beats config
    _env(monkeypatch, EV_ECHO_ADAPTIVE_FETCH="on")
    assert config.load().adaptive_fetch == "on"                        # env beats topic


def test_fetch_cache_minutes_default_and_validation(workspace, monkeypatch):
    assert config.load().fetch_cache_minutes == 60
    monkeypatch.setenv("EV_ECHO_FETCH_CACHE_MINUTES", "0")
    assert config.load().fetch_cache_minutes == 0
    for bad in ("-1", "x", "10081"):
        monkeypatch.setenv("EV_ECHO_FETCH_CACHE_MINUTES", bad)
        with pytest.raises(ConfigError, match="EV_ECHO_FETCH_CACHE_MINUTES"):
            config.load()
    monkeypatch.delenv("EV_ECHO_FETCH_CACHE_MINUTES")
    for bad in (-1, "x", True):
        with pytest.raises(ConfigError, match="fetch_cache_minutes"):
            settings.check_value("fetch_cache_minutes", bad, "f")
    assert "fetch_cache_minutes" not in settings.OVERRIDES
