"""Evidence Echo launcher: run the engine from a private venv, building it on first use.

Usage: python "${CLAUDE_PLUGIN_ROOT}/scripts/ev.py" <engine args...>

Stdlib only, and simple syntax on purpose: an old interpreter must get far
enough to print the version message. Build output is captured so the engine's
JSON is the only thing on stdout.

Each pyproject.toml + constraints.txt version gets its own venv,
<plugin data>/venv/<digest>/, so a plugin update never wipes a venv another
session is running from: every launch touches its venv's stamp, and a build
removes only venvs idle for a day. Builds
are serialised by a lock directory; a launcher that waited re-checks before building.
constraints.txt pins every installed package exactly (passed to pip/uv with -c).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)
SETUP_MESSAGE = "Setting up Evidence Echo engine (one-time, ~1 min)..."
WAIT_MESSAGE = "Waiting for another Evidence Echo setup to finish..."
TAIL_LINES = 15
LOCK_NAME = ".lock"
CONSTRAINTS = "constraints.txt"
LOCK_STALE_SECONDS = 15 * 60
LOCK_POLL_SECONDS = 2
VENV_IDLE_SECONDS = 24 * 60 * 60


class BuildError(Exception):
    def __init__(self, tool, output):
        super().__init__(tool)
        self.tool = tool
        self.output = output


def python_version():
    return tuple(sys.version_info[:2])


def plugin_data_dir(environ=None):
    """Where venvs live: disposable, so Claude Code's plugin data folder is fine."""
    environ = os.environ if environ is None else environ
    for var in ("EV_ECHO_PLUGIN_DATA", "CLAUDE_PLUGIN_DATA"):
        if environ.get(var):
            return Path(environ[var])
    return Path(environ.get("EV_ECHO_DATA_DIR") or Path.home() / ".evidence-echo") / "cache"


def venv_python(venv):
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def pyproject_digest(plugin):
    """Covers pyproject.toml and, when shipped, constraints.txt: a pins-only update rebuilds."""
    digest = hashlib.sha256((plugin / "pyproject.toml").read_bytes())
    constraints = plugin / CONSTRAINTS
    if constraints.exists():
        digest.update(b"\0" + CONSTRAINTS.encode() + b"\0" + constraints.read_bytes())
    return digest.hexdigest()


def _constraint_args(plugin):
    constraints = plugin / CONSTRAINTS
    return ["-c", str(constraints)] if constraints.exists() else []


def venv_dir(root, digest):
    return root / digest[:16]


def needs_build(venv, digest):
    stamp = venv / ".stamp"
    if not venv_python(venv).exists() or not stamp.exists():
        return True
    return stamp.read_text(encoding="utf-8").strip() != digest


def _find_uv():
    return shutil.which("uv")


def _sleep(seconds):
    time.sleep(seconds)


def _run(cmd, env=None):
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True,
                              errors="replace")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def _check(tool, proc):
    if proc.returncode != 0:
        raise BuildError(tool, (proc.stdout or "") + (proc.stderr or ""))


def build(venv, plugin):
    uv = _find_uv()
    if uv:  # never `uv sync --project`: it writes uv.lock into the plugin cache
        _check("uv venv", _run([uv, "venv", "--allow-existing", "--python", sys.executable,
                                str(venv)]))
        _check("uv pip install", _run([uv, "pip", "install", "--python",
                                       str(venv_python(venv)), str(plugin)]
                                      + _constraint_args(plugin)))
        return
    _check("python -m venv", _run([sys.executable, "-m", "venv", "--clear", str(venv)]))
    _check("pip install",
           _run([str(venv_python(venv)), "-m", "pip", "install", str(plugin)]
                + _constraint_args(plugin)))


def fallback_command(venv, plugin):
    pins = ' -c "{}"'.format(plugin / CONSTRAINTS) if (plugin / CONSTRAINTS).exists() else ""
    return '"{}" -m venv "{}" && "{}" -m pip install "{}"{}'.format(
        sys.executable, venv, venv_python(venv), plugin, pins)


def _acquire_lock(lock):
    """mkdir is atomic: whoever creates the lock directory builds; others wait."""
    waited = False
    while True:
        try:
            lock.mkdir(parents=True)
            return
        except FileExistsError:
            pass
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            continue  # released between our mkdir and stat
        if age > LOCK_STALE_SECONDS:  # its owner died; take over
            try:
                lock.rmdir()
            except OSError:
                pass
            continue
        if not waited:
            print(WAIT_MESSAGE, file=sys.stderr)
            waited = True
        _sleep(LOCK_POLL_SECONDS)


def _release_lock(lock):
    try:
        lock.rmdir()
    except OSError:
        pass


def _idle(venv):
    stamp = venv / ".stamp"
    try:
        return time.time() - stamp.stat().st_mtime > VENV_IDLE_SECONDS
    except OSError:
        return True  # no stamp: a failed build


def _remove_other_venvs(root, keep):
    """Best effort: files in use stay behind and that venv is rebuilt if needed again.
    A venv used within a day stays: a session on another plugin version may run from it."""
    for path in root.iterdir():
        if path.is_dir() and path != keep and path.name != LOCK_NAME and _idle(path):
            shutil.rmtree(path, ignore_errors=True)


def _touch(venv):
    try:
        os.utime(venv / ".stamp")
    except OSError:
        pass


def ensure_venv(root, plugin):
    digest = pyproject_digest(plugin)
    venv = venv_dir(root, digest)
    if not needs_build(venv, digest):
        _touch(venv)
        return venv
    lock = root / LOCK_NAME
    _acquire_lock(lock)
    try:
        if needs_build(venv, digest):  # another launcher may have built it while we waited
            print(SETUP_MESSAGE, file=sys.stderr)
            build(venv, plugin)  # raises BuildError; the stamp is written only on success
            (venv / ".stamp").write_text(digest, encoding="utf-8")
            _remove_other_venvs(root, venv)
    finally:
        _release_lock(lock)
    return venv


def _passthrough(cmd, env):
    return subprocess.run(cmd, env=env).returncode


def run_engine(venv, args, plugin):
    env = dict(os.environ)
    src = str(plugin / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return _passthrough([str(venv_python(venv)), "-m", "evidence_echo"] + list(args), env)


def main(argv=None, plugin=None):
    args = sys.argv[1:] if argv is None else argv
    plugin = PLUGIN_ROOT if plugin is None else plugin
    found = python_version()
    if found < MIN_PYTHON:
        print("Evidence Echo needs Python 3.11+ (found {}.{} at {})".format(
            found[0], found[1], sys.executable), file=sys.stderr)
        return 2
    root = plugin_data_dir() / "venv"
    try:
        venv = ensure_venv(root, plugin)
    except BuildError as exc:
        tail = "\n".join(exc.output.strip().splitlines()[-TAIL_LINES:])
        target = venv_dir(root, pyproject_digest(plugin))
        print("Evidence Echo setup failed: {} did not succeed.\n{}\nManual fallback: {}".format(
            exc.tool, tail, fallback_command(target, plugin)), file=sys.stderr)
        return 3
    return run_engine(venv, args, plugin)


if __name__ == "__main__":
    sys.exit(main())
