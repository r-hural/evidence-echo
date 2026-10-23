"""Settings relate and primary_check (SP3b-0): on/off, per topic, EV_ECHO_RELATE / EV_ECHO_PRIMARY_CHECK."""
from dataclasses import replace

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, settings, topics
from evidence_echo.core.settings import ConfigError

KEYS = [("relate", "EV_ECHO_RELATE"), ("primary_check", "EV_ECHO_PRIMARY_CHECK")]


@pytest.mark.parametrize("key, _env", KEYS)
def test_defaults_choices_and_scope(key, _env):
    assert settings.GLOBAL_DEFAULTS[key] == "on"
    assert settings.CHOICES[key] == ("on", "off")
    assert key in settings.OVERRIDES
    assert settings.check_value(key, "off", "f") == "off"
    with pytest.raises(ConfigError, match=key):
        settings.check_value(key, "yes", "f")


@pytest.mark.parametrize("key, env", KEYS)
def test_precedence_env_topic_config_default(key, env, workspace, monkeypatch):
    assert getattr(config.load(), key) == "on"                         # default
    settings.save_global(workspace, {key: "off"})
    assert getattr(config.load(), key) == "off"                        # config.json
    t = topics.load_topic(workspace / "topics", "seo")
    topics.save_topic(workspace / "topics", replace(t, **{key: "on"}))
    assert getattr(config.load(), key) == "on"                         # topic beats config.json
    monkeypatch.setenv(env, "off")
    assert getattr(config.load(), key) == "off"                        # env beats topic


@pytest.mark.parametrize("key, env", KEYS)
def test_an_invalid_env_value_names_the_variable(key, env, workspace, monkeypatch, capsys):
    monkeypatch.setenv(env, "maybe")
    with pytest.raises(ConfigError, match=env):
        config.load()
    assert cli.main(["extract"]) == 2
    assert env in capsys.readouterr().err


@pytest.mark.parametrize("key, _env", KEYS)
def test_topic_set_and_null_restore_the_global_value(key, _env, workspace):
    ws = config.workspace()
    api.topic_set(ws, "seo", key, "off")
    assert getattr(config.load(), key) == "off"
    api.topic_set(ws, "seo", key, "null")
    assert getattr(config.load(), key) == "on"
    with pytest.raises(ConfigError, match=key):
        api.topic_set(ws, "seo", key, "sometimes")


@pytest.mark.parametrize("key, _env", KEYS)
def test_config_set_and_show(key, _env, workspace):
    assert api.config_set(config.workspace(), key, "off") == {"set": key, "value": "off"}
    shown = api.config_show(config.workspace())["settings"][key]
    assert shown == {"value": "off", "origin": "config"}
    with pytest.raises(ConfigError, match=key):
        api.config_set(config.workspace(), key, "1")


def test_a_topic_file_without_the_keys_still_loads(workspace):
    path = workspace / "topics" / "seo" / "topic.json"
    data = settings.read_json_object(path)
    data.pop("relate", None)
    data.pop("primary_check", None)
    settings.write_json_atomic(path, data)
    cfg = config.load()
    assert (cfg.relate, cfg.primary_check) == ("on", "on")
    assert (cfg.topic.relate, cfg.topic.primary_check) == (None, None)
