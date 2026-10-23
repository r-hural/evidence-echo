"""SessionStart hook: hand plugin settings to the engine for this session.

- Keys go to <plugin data>/keys.json (owner-only where the OS supports it), never
  to CLAUDE_ENV_FILE: everything there reaches every command of the session.
- CLAUDE_ENV_FILE gets EV_ECHO_PLUGIN_DATA (where keys.json and venvs live) and,
  when the "Data folder" setting is set, EV_ECHO_DATA_DIR (never a folder inside
  the plugin data, which is deleted on uninstall).
- Until Evidence Echo is set up, the first session shows a one-line hint, once.
Values are never printed. Always exits 0: a failing hook must not block the session.
"""
import json
import os
import re
import sys
from pathlib import Path

KEYS = ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "APIFY_API_TOKEN")
KEYS_FILE = "keys.json"
HINT = ('Evidence Echo is installed but not set up — say "set up Evidence Echo" '
        'or run /evidence-echo:setup.')


def _quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def _expand(raw):
    text = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), raw.strip())
    text = os.path.expanduser(os.path.expandvars(text))
    return text if text and os.path.isabs(text) and "%" not in text and "$" not in text else ""


def _default_home():
    return Path.home()


def _in_plugin_data(path, environ, home=None):
    """The plugin data folders are deleted on uninstall: never a data folder."""
    claude_dir = environ.get("CLAUDE_CONFIG_DIR", "").strip() or         str(Path(home or _default_home()) / ".claude")
    roots = [environ.get(v, "").strip() for v in ("EV_ECHO_PLUGIN_DATA", "CLAUDE_PLUGIN_DATA")]
    roots = [r for r in roots if r] + [os.path.join(claude_dir, "plugins", "data")]
    p = os.path.normcase(os.path.realpath(path))
    for root in roots:
        r = os.path.normcase(os.path.realpath(root))
        if p == r or p.startswith(r.rstrip(os.sep) + os.sep):
            return True
    return False


def _chosen_data_dir(environ, home=None):
    chosen = _expand(environ.get("CLAUDE_PLUGIN_OPTION_DATA_DIR", ""))
    return "" if chosen and _in_plugin_data(chosen, environ, home) else chosen


def export_lines(environ):
    pairs = [("EV_ECHO_PLUGIN_DATA", environ.get("CLAUDE_PLUGIN_DATA", "").strip()),
             ("EV_ECHO_DATA_DIR", _chosen_data_dir(environ))]
    return ["export {}={}".format(name, _quote(value)) for name, value in pairs if value]


def _data_dir(environ, home):
    chosen = _chosen_data_dir(environ, home)
    if chosen:
        return Path(chosen)
    env = environ.get("EV_ECHO_DATA_DIR", "").strip()
    if env and not _in_plugin_data(env, environ, home):
        return Path(env)
    return Path(home or _default_home()) / ".evidence-echo"


_NOT_EDITIONS = {"topics", "reports", "cache", "venv"}


def _old_data(environ, data, home):
    """0.3/0.4 data waiting for migration: the engine moves it and writes config.json itself."""
    roots = []
    plugin = environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if plugin:
        roots.append(Path(plugin))
    roots.append(data)
    roots.append(Path(home or _default_home()) / ".evidence-echo")
    for root in roots:
        try:
            if (root / "echo.db").exists():
                return True
            for sub in root.iterdir():
                if (sub.is_dir() and sub.name not in _NOT_EDITIONS
                        and not sub.name.startswith(".") and (sub / "echo.db").exists()):
                    return True
        except OSError:
            continue
    return False


def hint(environ, home=None):
    data = _data_dir(environ, home)
    if (data / "config.json").exists() or _old_data(environ, data, home):
        return None
    state_path = data / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        state = {}
    if state.get("hint_shown"):
        return None
    state["hint_shown"] = True  # read-modify-write: the engine keeps its keys here too
    data.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name("state.json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, state_path)
    return HINT


def write_keys(environ):
    data = environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if not data:
        return
    keys = {}
    for name in KEYS:
        value = environ.get("CLAUDE_PLUGIN_OPTION_" + name, "").strip()
        if value:
            keys[name] = value
    path = os.path.join(data, KEYS_FILE)
    if not keys:  # keys cleared in the settings: drop the old file
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(data, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(keys, fh)
    os.chmod(tmp, 0o600)  # in case the file already existed with wider bits
    os.replace(tmp, path)


def main(environ=None, home=None):
    environ = os.environ if environ is None else environ
    try:
        write_keys(environ)
    except Exception:
        pass  # never block the session, never echo values
    try:
        env_file = environ.get("CLAUDE_ENV_FILE", "")
        lines = export_lines(environ) if env_file else []
        if lines:
            with open(env_file, "a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
    except Exception:
        pass
    try:
        text = hint(environ, home)
        if text:
            print(json.dumps({"systemMessage": text,
                              "hookSpecificOutput": {"hookEventName": "SessionStart",
                                                     "additionalContext": text}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
