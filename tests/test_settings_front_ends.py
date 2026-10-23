from dataclasses import replace

import pytest

from evidence_echo import api
from evidence_echo.core import config, settings, topics
from evidence_echo.core.settings import ConfigError


@pytest.mark.parametrize("value", ["on", "off"])
def test_html_reports_accepts(value):
    assert settings.check_value("html_reports", value, "f") == value


def test_html_reports_rejects():
    with pytest.raises(ConfigError, match="html_reports"):
        settings.check_value("html_reports", "yes", "f")


def test_defaults_and_scope():
    assert settings.GLOBAL_DEFAULTS["html_reports"] == "on"
    assert settings.GLOBAL_DEFAULTS["obsidian_dir"] is None
    assert "html_reports" in settings.OVERRIDES and "obsidian_dir" not in settings.OVERRIDES


def test_obsidian_dir_value_check(tmp_path):
    assert settings.check_value("obsidian_dir", str(tmp_path), "f") == str(tmp_path)
    assert settings.check_value("obsidian_dir", "", "f") == ""
    with pytest.raises(ConfigError, match="obsidian_dir"):
        settings.check_value("obsidian_dir", "relative/vault", "f")
    with pytest.raises(ConfigError, match="obsidian_dir"):
        settings.check_value("obsidian_dir", 3, "f")


def test_html_reports_precedence(workspace, monkeypatch):
    cfg = config.load()
    assert cfg.html_reports == "on"                                    # default
    settings.save_global(workspace, {"html_reports": "off"})
    assert config.load().html_reports == "off"                         # config.json
    t = topics.load_topic(workspace / "topics", "seo")
    topics.save_topic(workspace / "topics", replace(t, html_reports="on"))
    assert config.load().html_reports == "on"                          # topic beats config
    monkeypatch.setenv("EV_ECHO_HTML_REPORTS", "off")
    assert config.load().html_reports == "off"                         # env beats topic


def test_html_reports_env_invalid_names_variable(workspace, monkeypatch):
    monkeypatch.setenv("EV_ECHO_HTML_REPORTS", "maybe")
    with pytest.raises(ConfigError, match="EV_ECHO_HTML_REPORTS"):
        config.load()


def test_html_reports_topic_set_and_workspace_setting(workspace):
    ws = config.workspace()
    assert config.workspace_setting("html_reports", ws) == "on"
    api.topic_set(ws, "seo", "html_reports", "off")
    assert config.load().html_reports == "off"
    assert config.workspace_setting("html_reports", config.workspace()) == "on"  # no topic
    with pytest.raises(ConfigError, match="html_reports"):
        api.topic_set(ws, "seo", "html_reports", "sometimes")


def test_obsidian_dir_unset_by_default(workspace):
    assert config.load().obsidian_dir is None


def test_obsidian_dir_config_then_env(workspace, tmp_path, monkeypatch):
    vault, other = tmp_path / "vault", tmp_path / "other"
    vault.mkdir()
    other.mkdir()
    out = api.config_set(config.workspace(), "obsidian_dir", str(vault))
    assert out == {"set": "obsidian_dir", "value": str(vault)}
    assert config.load().obsidian_dir == vault
    monkeypatch.setenv("EV_ECHO_OBSIDIAN_DIR", str(other))
    assert config.load().obsidian_dir == other                         # env beats config.json
    shown = api.config_show(config.workspace())["settings"]["obsidian_dir"]
    assert shown == {"value": str(other), "origin": "env"}


def test_obsidian_dir_tilde_is_expanded(workspace, tmp_path):
    vault = tmp_path / "home" / "Vault"                                # conftest: HOME = tmp/home
    vault.mkdir(parents=True)
    api.config_set(config.workspace(), "obsidian_dir", "~/Vault")
    assert config.load().obsidian_dir == vault


def test_config_set_rejects_a_missing_folder(workspace, tmp_path):
    with pytest.raises(ConfigError, match="not an existing folder"):
        api.config_set(config.workspace(), "obsidian_dir", str(tmp_path / "missing"))
    assert not (tmp_path / "missing").exists()                         # never created
    assert config.load().obsidian_dir is None


def test_config_set_rejects_a_file(workspace, tmp_path):
    f = tmp_path / "file.md"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="not an existing folder"):
        api.config_set(config.workspace(), "obsidian_dir", str(f))


def test_config_set_empty_turns_the_export_off(workspace, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    api.config_set(config.workspace(), "obsidian_dir", str(vault))
    assert api.config_set(config.workspace(), "obsidian_dir", "") == {
        "set": "obsidian_dir", "value": None}
    assert config.load().obsidian_dir is None


def test_a_vault_deleted_after_set_does_not_break_loading(workspace, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    api.config_set(config.workspace(), "obsidian_dir", str(vault))
    vault.rmdir()
    assert config.load().obsidian_dir == vault          # the export reports it, not every command


def test_rejections_report_default_off(workspace):
    assert settings.GLOBAL_DEFAULTS["rejections_report"] == "off"
    assert "rejections_report" in settings.OVERRIDES
    assert config.load().rejections_report == "off"


def test_rejections_report_rejects_bad_value():
    with pytest.raises(ConfigError, match="rejections_report"):
        settings.check_value("rejections_report", "yes", "f")


def test_rejections_report_precedence(workspace, monkeypatch):
    assert config.load().rejections_report == "off"                    # default
    settings.save_global(workspace, {"rejections_report": "on"})
    assert config.load().rejections_report == "on"                     # config.json
    t = topics.load_topic(workspace / "topics", "seo")
    topics.save_topic(workspace / "topics", replace(t, rejections_report="off"))
    assert config.load().rejections_report == "off"                    # topic beats config
    monkeypatch.setenv("EV_ECHO_REJECTIONS_REPORT", "on")
    assert config.load().rejections_report == "on"                     # env beats topic


def test_rejections_report_topic_set(workspace):
    ws = config.workspace()
    api.topic_set(ws, "seo", "rejections_report", "on")
    assert config.load().rejections_report == "on"
    assert config.workspace_setting("rejections_report", config.workspace()) == "off"  # no topic
    with pytest.raises(ConfigError, match="rejections_report"):
        api.topic_set(ws, "seo", "rejections_report", "yes")
