import json
import os
from pathlib import Path

import pytest

from evidence_echo.core import settings
from evidence_echo.core.settings import ConfigError


def test_expand_path_tilde():
    assert settings.expand_path("~/x", "reports_dir") == Path.home() / "x"


def test_expand_path_percent_and_dollar_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("EVTEST", str(tmp_path))
    assert settings.expand_path("%EVTEST%/r", "reports_dir") == tmp_path / "r"
    assert settings.expand_path("$EVTEST/r", "reports_dir") == tmp_path / "r"


@pytest.mark.parametrize("raw", ["reports", "./r", "%NOPE_UNSET_VAR%/r", ""])
def test_expand_path_refuses_relative_or_unresolved(raw):
    with pytest.raises(ConfigError, match="reports_dir"):
        settings.expand_path(raw, "reports_dir")


@pytest.mark.parametrize("key,value,expected", [
    ("triage_backend", "api", "api"), ("embedder", "none", "none"),
    ("fetch_horizon_days", 14, 14), ("fetch_horizon_days", 61, 61), ("fetch_horizon_days", "14", 14), ("fetch_horizon_days", 14.0, 14),
    ("output_language", "de", "de")])
def test_check_value_accepts(key, value, expected):
    assert settings.check_value(key, value, "config.json") == expected


@pytest.mark.parametrize("key,value", [
    ("triage_backend", "fast"), ("embedder", "openai"), ("fetch_horizon_days", 0),
    ("fetch_horizon_days", "2.5"), ("fetch_horizon_days", True),
    ("output_language", ""), ("output_language", 3)])
def test_check_value_rejects_naming_field(key, value):
    with pytest.raises(ConfigError, match=rf"config\.json: field '{key}'"):
        settings.check_value(key, value, "config.json")


def test_check_value_reports_dir_keeps_raw_text(tmp_path):
    assert settings.check_value("reports_dir", "~/r", "x") == "~/r"
    with pytest.raises(ConfigError):
        settings.check_value("reports_dir", "rel", "x")


def test_load_global_missing_is_none(tmp_path):
    assert settings.load_global(tmp_path) is None


def test_save_and_load_global_roundtrip(tmp_path):
    settings.save_global(tmp_path, {"output_language": "de"})
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == \
        {"version": 1, "output_language": "de"}
    assert settings.load_global(tmp_path)["output_language"] == "de"


def test_load_global_unknown_field_is_error(tmp_path):
    (tmp_path / "config.json").write_text('{"version": 1, "lookback": 7}', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"config\.json: unknown field 'lookback'"):
        settings.load_global(tmp_path)


def test_load_global_bad_version_and_bad_json(tmp_path):
    (tmp_path / "config.json").write_text('{"version": 2}', encoding="utf-8")
    with pytest.raises(ConfigError, match="version"):
        settings.load_global(tmp_path)
    (tmp_path / "config.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ConfigError, match="config.json: invalid JSON"):
        settings.load_global(tmp_path)


def test_write_json_atomic_leaves_no_temp(tmp_path):
    settings.write_json_atomic(tmp_path / "a" / "f.json", {"x": 1})
    assert [p.name for p in (tmp_path / "a").iterdir()] == ["f.json"]


def test_state_is_tolerant_and_merges(tmp_path):
    assert settings.read_state(tmp_path) == {}
    (tmp_path / "state.json").write_text("{broken", encoding="utf-8")
    assert settings.read_state(tmp_path) == {}
    settings.update_state(tmp_path, hint_shown=True)
    settings.update_state(tmp_path, migrated=["0.4"])
    assert settings.read_state(tmp_path) == {"hint_shown": True, "migrated": ["0.4"]}


def test_load_global_accepts_bom(tmp_path):
    (tmp_path / "config.json").write_bytes(b'\xef\xbb\xbf{"version": 1, "fetch_horizon_days": 9}')
    assert settings.load_global(tmp_path) == {"fetch_horizon_days": 9}


def test_read_json_object_unreadable_is_config_error(tmp_path):
    with pytest.raises(ConfigError, match=r"^config\.json: cannot read"):
        settings.read_json_object(tmp_path / "config.json")
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe\x00{")
    with pytest.raises(ConfigError, match=r"^bad\.json: cannot read"):
        settings.read_json_object(bad)


def test_horizon_overflow_and_bool_show_original_value():
    with pytest.raises(ConfigError, match="fetch_horizon_days"):
        settings.check_value("fetch_horizon_days", 10**400, "f")
    with pytest.raises(ConfigError, match="True"):
        settings.check_value("fetch_horizon_days", True, "f")


@pytest.mark.parametrize("key,value,expected", [
    ("fetch_horizon_days", "3650", 3650), ("fetch_horizon_days", 1, 1),
    ("scan_depth", "full", "full"), ("scan_depth", "headlines", "headlines"),
])
def test_new_settings_accept(key, value, expected):
    assert settings.check_value(key, value, "f") == expected


@pytest.mark.parametrize("key,value", [
    ("fetch_horizon_days", 0), ("fetch_horizon_days", 3651), ("fetch_horizon_days", "2.5"),
    ("fetch_horizon_days", True), ("scan_depth", "deep"), ("lookback_days", 7),
])
def test_new_settings_reject(key, value):
    with pytest.raises(ConfigError):
        settings.check_value(key, value, "f")


def test_lookback_days_is_unknown_in_config_json(tmp_path):
    (tmp_path / "config.json").write_text('{"version": 1, "lookback_days": 9}', encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown field 'lookback_days'"):
        settings.load_global(tmp_path)


@pytest.mark.parametrize("value", ["off", "on"])
def test_adaptive_fetch_accepts(value):
    assert settings.check_value("adaptive_fetch", value, "f") == value


def test_adaptive_fetch_rejects():
    with pytest.raises(ConfigError, match="adaptive_fetch"):
        settings.check_value("adaptive_fetch", "yes", "f")


def test_adaptive_fetch_default_and_override():
    assert settings.GLOBAL_DEFAULTS["adaptive_fetch"] == "off"
    assert "adaptive_fetch" in settings.OVERRIDES
