"""Evidence Echo MCP entry point: the read-only evidence server, in the launcher's own interpreter.

Usage: started by scripts/ev_mcp (macOS, Linux) or scripts/ev_mcp.cmd (Windows), which .mcp.json
names by full path, so Claude Code never resolves a bare interpreter name (on Windows that lookup
searches the user's project folder first). The launcher picks the first Python 3.11+ on PATH.

Stdlib only (no venv: Claude Code starts the server at session start, and a venv build takes minutes),
and simple syntax on purpose: an old interpreter must get far enough to print the version message.
Nothing but JSON-RPC goes to stdout.
"""
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 11)


def python_version():
    return tuple(sys.version_info[:2])


def main(stdin=None, stdout=None):
    found = python_version()
    if found < MIN_PYTHON:
        print("Evidence Echo needs Python 3.11+ (found {}.{} at {})".format(
            found[0], found[1], sys.executable), file=sys.stderr)
        return 2
    sys.path.insert(0, str(PLUGIN_ROOT / "src"))
    from evidence_echo import mcp_server
    return mcp_server.serve(sys.stdin.buffer if stdin is None else stdin,
                            sys.stdout.buffer if stdout is None else stdout)


if __name__ == "__main__":
    sys.exit(main())
