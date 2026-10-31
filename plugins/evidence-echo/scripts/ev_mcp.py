"""Evidence Echo MCP entry point: the read-only evidence server, in the launcher's own interpreter.

Usage: python "${CLAUDE_PLUGIN_ROOT}/scripts/ev_mcp.py" [--defer-to-python]

Stdlib only (no venv: Claude Code starts the server at session start, and a venv build takes minutes),
and simple syntax on purpose: an old interpreter must get far enough to print the version message.
The python3 entry in .mcp.json passes --defer-to-python and yields when `python` works, so exactly
one server runs. Nothing but JSON-RPC goes to stdout.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)
DEFER_FLAG = "--defer-to-python"


def python_version():
    return tuple(sys.version_info[:2])


def python_is_usable(path, run=subprocess.run):
    """True when `path` is an interpreter of 3.11 or newer."""
    if not path:
        return False
    try:  # Windows also searches the current folder (the user's project): never run what sits there
        if os.path.dirname(os.path.abspath(path)).lower() == os.getcwd().lower():
            return False
    except OSError:
        return False
    try:
        done = run([path, "-c", "import sys; print(sys.version_info >= (3, 11))"],
                   timeout=5, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return False
    out = done.stdout
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return done.returncode == 0 and str(out).strip() == "True"


def main(argv=None, which=shutil.which, run=subprocess.run, stdin=None, stdout=None):
    args = sys.argv[1:] if argv is None else argv
    found = python_version()
    if found < MIN_PYTHON:
        print("Evidence Echo needs Python 3.11+ (found {}.{} at {})".format(
            found[0], found[1], sys.executable), file=sys.stderr)
        return 2
    if DEFER_FLAG in args and python_is_usable(which("python"), run):
        print("evidence-echo-py3: 'python' works; the evidence-echo server runs instead", file=sys.stderr)
        return 0
    sys.path.insert(0, str(PLUGIN_ROOT / "src"))
    from evidence_echo import mcp_server
    return mcp_server.serve(sys.stdin.buffer if stdin is None else stdin,
                            sys.stdout.buffer if stdout is None else stdout)


if __name__ == "__main__":
    sys.exit(main())
