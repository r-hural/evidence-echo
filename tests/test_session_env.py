import json
import os
import subprocess
import sys

import pytest

from tests.conftest import PLUGIN_DIR, load_script

session_env = load_script("hooks/session_env.py")

FULL = {
    "CLAUDE_PLUGIN_DATA": "/data/ev",
    "CLAUDE_PLUGIN_OPTION_ANTHROPIC_API_KEY": "sk-ant-secret",
    "CLAUDE_PLUGIN_OPTION_VOYAGE_API_KEY": "pa-secret",
    "CLAUDE_PLUGIN_OPTION_APIFY_API_TOKEN": "apify_api_secret",
}

SECRETS = ("sk-ant-secret", "pa-secret", "apify_api_secret")


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    # hint() falls back to <home>/.evidence-echo; never touch the real one
    monkeypatch.setattr(session_env, "_default_home", lambda: tmp_path / "home")


def _env(tmp_path, **extra):
    return dict(FULL, CLAUDE_PLUGIN_DATA=str(tmp_path / "plugin-data"), **extra)


def _configured(tmp_path):
    d = tmp_path / "home" / ".evidence-echo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text('{"version": 1}', encoding="utf-8")


def _keys_file(tmp_path):
    return tmp_path / "plugin-data" / "keys.json"


def test_export_lines_plugin_data_only():
    assert session_env.export_lines(FULL) == ["export EV_ECHO_PLUGIN_DATA='/data/ev'"]


def test_export_lines_data_dir_option_expanded(tmp_path):
    lines = session_env.export_lines(dict(FULL, CLAUDE_PLUGIN_OPTION_DATA_DIR=str(tmp_path / "d")))
    assert f"export EV_ECHO_DATA_DIR='{tmp_path / 'd'}'" in lines
    home = session_env.export_lines(dict(FULL, CLAUDE_PLUGIN_OPTION_DATA_DIR="~/ee"))
    assert f"export EV_ECHO_DATA_DIR='{os.path.expanduser('~/ee')}'" in home


def test_relative_data_dir_option_not_exported():
    lines = session_env.export_lines(dict(FULL, CLAUDE_PLUGIN_OPTION_DATA_DIR="rel/dir"))
    assert not any("EV_ECHO_DATA_DIR" in l for l in lines)


def test_keys_are_never_exported():
    # exported values reach every Bash command of the session; keys go to a file
    text = "\n".join(session_env.export_lines(FULL))
    for secret in SECRETS:
        assert secret not in text
    assert "API_KEY" not in text and "API_TOKEN" not in text


def test_single_quotes_escaped():
    assert session_env.export_lines({"CLAUDE_PLUGIN_DATA": "/a'b"}) == \
        ["export EV_ECHO_PLUGIN_DATA='/a'\\''b'"]


def test_keys_written_to_plugin_data_file(tmp_path, capsys):
    _configured(tmp_path)
    assert session_env.main(_env(tmp_path)) == 0
    assert json.loads(_keys_file(tmp_path).read_text(encoding="utf-8")) == {
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "VOYAGE_API_KEY": "pa-secret",
        "APIFY_API_TOKEN": "apify_api_secret",
    }
    assert capsys.readouterr() == ("", "")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_keys_file_is_owner_only(tmp_path):
    session_env.main(_env(tmp_path))
    assert _keys_file(tmp_path).stat().st_mode & 0o777 == 0o600


def test_cleared_keys_remove_the_file(tmp_path):
    session_env.main(_env(tmp_path))
    cleared = {k: "" for k in FULL if k.endswith(("_API_KEY", "_API_TOKEN"))}
    session_env.main(_env(tmp_path, **cleared))
    assert not _keys_file(tmp_path).exists()


def test_only_set_keys_are_written(tmp_path):
    session_env.main(_env(tmp_path, CLAUDE_PLUGIN_OPTION_VOYAGE_API_KEY=""))
    assert set(json.loads(_keys_file(tmp_path).read_text(encoding="utf-8"))) == \
        {"ANTHROPIC_API_KEY", "APIFY_API_TOKEN"}


def test_without_plugin_data_no_keys_file_and_still_exits_0(tmp_path, capsys):
    _configured(tmp_path)
    env_file = tmp_path / "env.sh"
    env = {k: v for k, v in FULL.items() if k != "CLAUDE_PLUGIN_DATA"}
    assert session_env.main(dict(env, CLAUDE_ENV_FILE=str(env_file))) == 0
    assert not env_file.exists()
    assert not list(tmp_path.rglob("keys.json"))
    assert capsys.readouterr() == ("", "")


def test_appends_to_env_file_without_printing(tmp_path, capsys):
    _configured(tmp_path)
    env_file = tmp_path / "env.sh"
    env_file.write_text("export OTHER='1'\n", encoding="utf-8")
    assert session_env.main(_env(tmp_path, CLAUDE_ENV_FILE=str(env_file))) == 0
    text = env_file.read_text(encoding="utf-8")
    assert text.startswith("export OTHER='1'\n")
    assert f"export EV_ECHO_PLUGIN_DATA='{tmp_path / 'plugin-data'}'\n" in text
    for secret in SECRETS:
        assert secret not in text
    assert capsys.readouterr() == ("", "")


def test_unwritable_env_file_still_exits_0(tmp_path, capsys):
    _configured(tmp_path)
    assert session_env.main(_env(tmp_path, CLAUDE_ENV_FILE=str(tmp_path))) == 0  # a directory
    assert capsys.readouterr() == ("", "")


def test_runs_as_hook_script(tmp_path):
    _configured(tmp_path)
    env_file = tmp_path / "env.sh"
    home = str(tmp_path / "home")
    env = {**os.environ, **_env(tmp_path), "CLAUDE_ENV_FILE": str(env_file),
           "HOME": home, "USERPROFILE": home}
    env.pop("EV_ECHO_DATA_DIR", None)
    proc = subprocess.run([sys.executable, str(PLUGIN_DIR / "hooks" / "session_env.py")],
                          env=env, capture_output=True, text=True)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert "export EV_ECHO_PLUGIN_DATA=" in env_file.read_text(encoding="utf-8")
    assert _keys_file(tmp_path).exists()


def test_hint_once_when_not_set_up(tmp_path):
    env = _env(tmp_path)
    assert session_env.hint(env, home=tmp_path / "home") == session_env.HINT
    assert session_env.hint(env, home=tmp_path / "home") is None
    state = json.loads((tmp_path / "home" / ".evidence-echo" / "state.json").read_text(encoding="utf-8"))
    assert state["hint_shown"] is True


def test_hint_keeps_other_state_keys(tmp_path):
    data = tmp_path / "home" / ".evidence-echo"
    data.mkdir(parents=True)
    (data / "state.json").write_text('{"migrated": ["0.4"]}', encoding="utf-8")
    session_env.hint(_env(tmp_path), home=tmp_path / "home")
    assert json.loads((data / "state.json").read_text(encoding="utf-8")) == \
        {"migrated": ["0.4"], "hint_shown": True}


def test_no_hint_when_configured(tmp_path):
    _configured(tmp_path)
    assert session_env.hint(_env(tmp_path), home=tmp_path / "home") is None


def test_no_hint_when_old_data_will_be_migrated(tmp_path):
    (tmp_path / "plugin-data" / "seo").mkdir(parents=True)
    (tmp_path / "plugin-data" / "seo" / "echo.db").write_bytes(b"db")
    assert session_env.hint(_env(tmp_path), home=tmp_path / "home") is None


def test_hint_uses_data_dir_option(tmp_path):
    d = tmp_path / "custom"
    env = _env(tmp_path, CLAUDE_PLUGIN_OPTION_DATA_DIR=str(d))
    assert session_env.hint(env, home=tmp_path / "home") == session_env.HINT
    assert (d / "state.json").exists()


def test_hint_default_home_is_the_faked_one(tmp_path):
    assert session_env.hint(_env(tmp_path)) == session_env.HINT
    assert (tmp_path / "home" / ".evidence-echo" / "state.json").exists()


def test_main_prints_hint_json_once(tmp_path, capsys):
    env = _env(tmp_path)
    assert session_env.main(env, home=tmp_path / "home") == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"systemMessage": session_env.HINT,
                   "hookSpecificOutput": {"hookEventName": "SessionStart",
                                          "additionalContext": session_env.HINT}}
    assert session_env.main(env, home=tmp_path / "home") == 0
    assert capsys.readouterr().out == ""


def test_hint_text():
    assert session_env.HINT == ('Evidence Echo is installed but not set up — say "set up '
                                'Evidence Echo" or run /evidence-echo:setup.')


def test_hooks_json_registers_session_start():
    data = json.loads((PLUGIN_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    [entry] = data["hooks"]["SessionStart"]
    assert entry["hooks"] == [{
        "type": "command",
        # python first: on Windows python3 is often the Microsoft Store alias, which hangs;
        # py -3 covers a python.org install that is not on PATH
        "command": 'python "${CLAUDE_PLUGIN_ROOT}/hooks/session_env.py" 2>/dev/null'
                   ' || py -3 "${CLAUDE_PLUGIN_ROOT}/hooks/session_env.py" 2>/dev/null'
                   ' || python3 "${CLAUDE_PLUGIN_ROOT}/hooks/session_env.py"',
        "timeout": 5,
    }]


def test_plugin_user_config():
    manifest = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
    cfg = json.loads(manifest.read_text(encoding="utf-8"))["userConfig"]
    assert set(cfg) == {"data_dir", "anthropic_api_key", "voyage_api_key", "apify_api_token"}
    assert cfg["data_dir"]["type"] == "directory" and "default" not in cfg["data_dir"]
    assert "cloud-synced" in cfg["data_dir"]["description"]
    for key in ("anthropic_api_key", "voyage_api_key", "apify_api_token"):
        assert cfg[key]["type"] == "string" and cfg[key]["sensitive"] is True


def test_no_hint_when_old_data_in_home_folder(tmp_path):
    (tmp_path / "home" / ".evidence-echo" / "seo").mkdir(parents=True)
    (tmp_path / "home" / ".evidence-echo" / "seo" / "echo.db").write_bytes(b"db")
    env = {k: v for k, v in _env(tmp_path).items() if k != "CLAUDE_PLUGIN_DATA"}
    assert session_env.hint(env, home=tmp_path / "home") is None


def test_no_hint_when_old_data_in_chosen_data_folder(tmp_path):
    d = tmp_path / "custom"
    (d / "seo").mkdir(parents=True)
    (d / "seo" / "echo.db").write_bytes(b"db")
    env = _env(tmp_path, CLAUDE_PLUGIN_OPTION_DATA_DIR=str(d))
    assert session_env.hint(env, home=tmp_path / "home") is None


def test_hint_shown_when_only_topics_folder_has_db(tmp_path):
    t = tmp_path / "home" / ".evidence-echo" / "topics" / "x"
    t.mkdir(parents=True)
    (t / "echo.db").write_bytes(b"db")
    assert session_env.hint(_env(tmp_path), home=tmp_path / "home") == session_env.HINT


def test_hint_shown_for_venv_and_cache_folders(tmp_path):
    for name in ("venv", "cache"):
        (tmp_path / "home" / ".evidence-echo" / name).mkdir(parents=True)
    assert session_env.hint(_env(tmp_path), home=tmp_path / "home") == session_env.HINT


@pytest.mark.parametrize("sub", ["", "data"])
def test_data_dir_option_inside_plugin_data_not_exported(tmp_path, sub):
    env = _env(tmp_path, CLAUDE_PLUGIN_OPTION_DATA_DIR=str(tmp_path / "plugin-data" / sub))
    assert not any("EV_ECHO_DATA_DIR" in l for l in session_env.export_lines(env))


def test_data_dir_option_under_claude_plugins_data_not_exported(tmp_path):
    chosen = tmp_path / "home" / ".claude" / "plugins" / "data" / "x"
    env = dict(FULL, CLAUDE_PLUGIN_OPTION_DATA_DIR=str(chosen))
    assert not any("EV_ECHO_DATA_DIR" in l for l in session_env.export_lines(env))


def test_hint_ignores_data_dir_inside_plugin_data(tmp_path):
    env = _env(tmp_path, EV_ECHO_DATA_DIR=str(tmp_path / "plugin-data"))
    assert session_env.hint(env, home=tmp_path / "home") == session_env.HINT
    assert (tmp_path / "home" / ".evidence-echo" / "state.json").exists()
    assert not (tmp_path / "plugin-data" / "state.json").exists()
