import json

import pytest

from evidence_echo import cli
from evidence_echo.core import settings


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(tmp_path / "pd"))
    return tmp_path / "d"


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out else None), out.err


def test_setup_status_unconfigured(data, capsys):
    code, out, _ = _run(capsys, "setup", "status")
    assert code == 0
    assert out["configured"] is False and out["topics"] == [] and out["reports_dir"] is None
    assert "seo" in [t["id"] for t in out["templates"]] and out["hint_due"] is True


def test_config_set_creates_config_and_folder(data, tmp_path, capsys):
    target = tmp_path / "Reports"
    code, out, _ = _run(capsys, "config", "set", "reports_dir", str(target))
    assert code == 0 and target.is_dir()
    assert settings.load_global(data) == {"reports_dir": str(target)}
    assert _run(capsys, "setup", "status")[1]["configured"] is True


@pytest.mark.parametrize("key,value,msg", [
    ("fetch_horizon_days", "0", "fetch_horizon_days"), ("embedder", "x", "embedder"),
    ("colour", "red", "unknown"), ("reports_dir", "rel", "absolute")])
def test_config_set_validates(data, capsys, key, value, msg):
    code, _, err = _run(capsys, "config", "set", key, value)
    assert code == 2 and msg in err
    assert settings.load_global(data) is None


def test_config_show_origins_and_keys(data, capsys, monkeypatch):
    _run(capsys, "config", "set", "output_language", "de")
    monkeypatch.setenv("EV_ECHO_FETCH_HORIZON_DAYS", "3")
    (data.parent / "pd").mkdir()
    (data.parent / "pd" / "keys.json").write_text('{"ANTHROPIC_API_KEY": "sk-secret"}', encoding="utf-8")
    code, out, _ = _run(capsys, "config", "show")
    s = out["settings"]
    assert s["output_language"] == {"value": "de", "origin": "config"}
    assert s["fetch_horizon_days"] == {"value": 3, "origin": "env"}
    assert s["embedder"] == {"value": "auto", "origin": "default"}
    assert s["ask_on_disputes"] == {"value": "off", "origin": "default"}
    assert out["keys"] == {"anthropic": True, "voyage": False, "apify": False}
    assert "sk-secret" not in json.dumps(out)


def test_invalid_config_json_exit_2_but_setup_status_and_config_set_work(data, capsys):
    data.mkdir(parents=True)
    (data / "config.json").write_text('{"version": 1, "colour": 1}', encoding="utf-8")
    assert _run(capsys, "topic", "list")[0] == 2
    code, out, _ = _run(capsys, "setup", "status")
    assert code == 0 and "config.json" in out["config_error"]
    assert _run(capsys, "config", "set", "output_language", "de")[0] == 0
    assert settings.load_global(data) == {"output_language": "de"}


def test_ask_on_disputes_is_on_or_off(data, capsys):
    assert _run(capsys, "config", "set", "ask_on_disputes", "on")[0] == 0
    assert settings.load_global(data) == {"ask_on_disputes": "on"}
    code, _, err = _run(capsys, "config", "set", "ask_on_disputes", "yes")
    assert code == 2 and "off, on" in err
