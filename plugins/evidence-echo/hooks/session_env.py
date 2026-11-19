"""SessionStart hook: hand plugin settings to the engine for this session.

- Keys go to <plugin data>/keys/<session id>.json (owner-only where the OS supports
  it), never to CLAUDE_ENV_FILE: everything there reaches every command of the
  session. One file per session, so each session uses exactly its own settings (a
  key cleared there is gone) and sessions starting together never overwrite each
  other. Files untouched for KEYS_KEEP_DAYS are removed; the engine touches its
  file on every read. The shared keys.json of 0.16.4 and earlier is deleted.
- CLAUDE_ENV_FILE gets EV_ECHO_PLUGIN_DATA (where the keys and venvs live),
  EV_ECHO_KEYS_ID (the session's keys file name, no key values) and, when the
  "Data folder" setting is set, EV_ECHO_DATA_DIR (never a folder inside the
  plugin data, which is deleted on uninstall).
- Until Evidence Echo is set up, the first session shows a one-line hint, once.
- When scheduled runs exist (<data>/scheduling/), it refreshes runtime.json (where the plugin and
  `claude` are now, for the OS task's launcher) and shows one reminder line from status.json when a
  report is overdue, the last scheduled run failed or the scheduler seems stopped. Small files only.
Values are never printed. Always exits 0: a failing hook must not block the session.
"""
import json
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEYS = ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "APIFY_API_TOKEN", "TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD")
KEYS_FILE = "keys.json"   # the shared file of 0.16.4 and earlier; deleted
KEYS_DIR = "keys"
KEYS_KEEP_DAYS = 30
SCHEDULER_KEYS = ("TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD")
SCHEDULER_KEYS_FILE = "scheduler-keys.json"
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


def export_lines(environ, keys_id=None):
    pairs = [("EV_ECHO_PLUGIN_DATA", environ.get("CLAUDE_PLUGIN_DATA", "").strip()),
             ("EV_ECHO_KEYS_ID", keys_id or ""),
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


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _absolute(path):
    """The path when it is absolute (POSIX or Windows form), else ""."""
    import ntpath
    import posixpath
    ok = isinstance(path, str) and (posixpath.isabs(path) or ntpath.isabs(path))
    return path if ok else ""


def find_claude():
    """The absolute path of `claude` on PATH, never one from the current folder (the user's project):
    Windows looks there first unless NoDefaultCurrentDirectoryInExePath is set, and Python before
    3.12 always does. None when only such a match exists."""
    os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"
    cwd = os.path.normcase(os.path.abspath(os.getcwd()))
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d.strip() and d.strip() != "." and os.path.isabs(d)
            and os.path.normcase(os.path.abspath(d)) != cwd]
    found = shutil.which("claude", path=os.pathsep.join(dirs))
    if not found or not _absolute(found):
        return None
    if os.path.normcase(os.path.dirname(os.path.abspath(found))) == cwd:
        return None
    return found


def write_runtime(environ, data):
    """<data>/scheduling/runtime.json: what the OS task's launcher needs to find the engine. Only
    when scheduling is in use (the folder exists); a hand-set `runner_args` is kept."""
    folder = Path(data) / "scheduling"
    if not folder.is_dir():
        return
    path = folder / "runtime.json"
    old = _read_json(path)
    runtime = {"plugin_root": environ.get("CLAUDE_PLUGIN_ROOT", "").strip(),
               "plugin_data": environ.get("CLAUDE_PLUGIN_DATA", "").strip(),
               "python": sys.executable, "claude": find_claude() or _absolute(old.get("claude")),
               "runner_args": old.get("runner_args") if isinstance(old.get("runner_args"), list) else []}
    if not runtime["plugin_root"]:
        return   # not started by Claude Code: never overwrite a good file with an empty one
    tmp = folder / "runtime.json.tmp"
    tmp.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _when(raw):
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ago(delta):
    hours = int(delta.total_seconds() // 3600)
    return "%d day%s" % (hours // 24, "" if hours // 24 == 1 else "s") if hours >= 48 else "%d hours" % hours


RUNLOG_TAIL_BYTES = 16384


def _scheduler_failure(data, last_wake):
    """The newest launcher failure (kind "scheduler") in the run log's tail that is newer than the
    last time run-due itself ran; None otherwise. The launcher writes it when it cannot start the
    engine, so status.json is never refreshed in that case."""
    try:
        with open(data / "scheduling" / "runs.jsonl", "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - RUNLOG_TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("kind") != "scheduler":
            continue
        at = _when(row.get("finished") or row.get("started"))
        if row.get("outcome") == "failed" and at and (last_wake is None or at > last_wake):
            return str(row.get("error") or "unknown error")[:160]
        return None
    return None


def reminder(data, now=None, detail=True):
    """One reminder text from <data>/scheduling/status.json, or None. Without `detail` the error texts
    are left out: they can echo web content, so they go to the user, never into the model's context. Reads one small file and does
    no schedule math (status.json holds UTC timestamps), so it is fast and needs no zone data."""
    status = _read_json(Path(data) / "scheduling" / "status.json")
    if not status:
        return None
    now = now or datetime.now(timezone.utc)
    topics = status.get("topics") if isinstance(status.get("topics"), dict) else {}
    overdue, failed = [], []
    for tid, info in sorted(topics.items()):
        if not isinstance(info, dict):
            continue
        ok = _when(info.get("last_report_ok"))
        due = _when(info.get("next_report"))
        if due and now - due > timedelta(hours=12) and (ok is None or ok < due):
            overdue.append((tid, now - due))
        bad = info.get("last_failure") if isinstance(info.get("last_failure"), dict) else None
        at = _when(bad.get("at")) if bad else None
        kind = bad.get("kind") if bad and bad.get("kind") in ("fetch", "notify") else "report"
        if at and (kind != "report" or ok is None or at > ok):
            failed.append((tid, kind, str(bad.get("error") or "unknown error")[:160]))
    lines = []
    if len(overdue) == 1:
        lines.append("Evidence Echo: %s report is %s overdue \u2014 run /evidence-echo:report?"
                     % (overdue[0][0], _ago(overdue[0][1])))
    elif overdue:
        lines.append("Evidence Echo: %s reports are overdue \u2014 run /evidence-echo:report?"
                     % ", ".join("%s (%s)" % (t, _ago(d)) for t, d in overdue))
    for tid, kind, error in failed:
        if kind == "notify":
            lines.append("Evidence Echo: the %s report message could not be sent%s" % (tid, _detail(error, detail)))
        else:
            lines.append("Evidence Echo: last scheduled %s %s failed%s" % (tid, kind, _detail(error, detail)))
    problem = _scheduler_failure(Path(data), _when(status.get("last_wake")))
    if problem:
        lines.append("Evidence Echo: the scheduler could not run%s" % _detail(problem, detail))
    wake_due, last = _when(status.get("next_wake")), _when(status.get("last_wake"))
    if (status.get("installed") and wake_due and now - wake_due > timedelta(hours=2)
            and (last is None or last < wake_due)):
        lines.append("Evidence Echo: the scheduler last ran %s" % (last.date().isoformat() if last else "never"))
    return "\n".join(lines) or None


def _detail(error, detail):
    return ": %s" % error if detail else " (`ev schedule status` shows why)"


def keys_id(session_id):
    """The session's keys file name: its session id reduced to safe characters, else random."""
    clean = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id or ""))[:64]
    return clean or uuid.uuid4().hex


def _prune(folder, now):
    for path in folder.glob("*.json"):
        try:
            if now - path.stat().st_mtime > KEYS_KEEP_DAYS * 86400:
                path.unlink()
        except OSError:
            continue


def write_keys(environ, kid, now=None):
    """Writes this session's keys to <plugin data>/keys/<kid>.json, or removes that file
    when the session's settings hold none."""
    data = environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if not data:
        return
    keys = {}
    for name in KEYS:
        value = environ.get("CLAUDE_PLUGIN_OPTION_" + name, "").strip()
        if value:
            keys[name] = value
    legacy = os.path.join(data, KEYS_FILE)
    if os.path.exists(legacy):
        os.remove(legacy)  # shared by every session: a cleared key stayed in use
    folder = Path(data) / KEYS_DIR
    path = folder / (kid + ".json")
    if folder.is_dir():
        _prune(folder, time.time() if now is None else now)
    if not keys:
        if path.exists():
            path.unlink()
        return
    folder.mkdir(parents=True, exist_ok=True)
    _write_private(path, keys)


def _write_private(path, keys):
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(keys, fh)
    os.chmod(tmp, 0o600)  # in case the file already existed with wider bits
    os.replace(tmp, path)


def write_scheduler_keys(environ):
    """The delivery secrets for runs the OS starts, which have no session: only what a failure
    message needs, refreshed by every session and removed once the settings hold none."""
    data = environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if not data:
        return
    keys = {}
    for name in SCHEDULER_KEYS:
        value = environ.get("CLAUDE_PLUGIN_OPTION_" + name, "").strip()
        if value:
            keys[name] = value
    path = Path(data) / SCHEDULER_KEYS_FILE
    if keys:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_private(path, keys)
    elif path.exists():
        path.unlink()


def _session_id(stdin):
    """The session id from the hook's JSON input; None when run by hand (a terminal)."""
    try:
        if stdin is None or stdin.isatty():
            return None
        payload = json.loads(stdin.read() or "{}")
    except (OSError, ValueError):
        return None
    return payload.get("session_id") if isinstance(payload, dict) else None


def main(environ=None, home=None, session_id=None):
    environ = os.environ if environ is None else environ
    kid = keys_id(session_id)
    try:
        write_keys(environ, kid)
    except Exception:
        pass  # never block the session, never echo values
    try:
        write_scheduler_keys(environ)
    except Exception:
        pass
    try:
        env_file = environ.get("CLAUDE_ENV_FILE", "")
        # exported even when writing failed: the engine then finds no session file and runs
        # without keys, never on the shared keys.json of older versions
        has_data = bool(environ.get("CLAUDE_PLUGIN_DATA", "").strip())
        lines = export_lines(environ, kid if has_data else None) if env_file else []
        if lines:
            with open(env_file, "a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
    except Exception:
        pass
    try:
        write_runtime(environ, _data_dir(environ, home))
    except Exception:
        pass
    try:
        first = hint(environ, home)
        data = _data_dir(environ, home)
        texts = [t for t in (first, reminder(data)) if t]
        model = [t for t in (first, reminder(data, detail=False)) if t]
    except Exception:
        texts, model = [], []
    try:
        text = "\n".join(texts)
        if text:
            print(json.dumps({"systemMessage": text,
                              "hookSpecificOutput": {"hookEventName": "SessionStart",
                                                     "additionalContext": "\n".join(model)}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(session_id=_session_id(sys.stdin)))
