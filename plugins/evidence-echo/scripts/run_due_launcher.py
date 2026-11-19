"""Stable launcher the OS task starts: <data>/scheduling/run-due.py is a copy of this file.

It never points at the plugin cache path (that changes on every plugin update). It reads
runtime.json next to it (written by the SessionStart hook on every session start), sets the
data folder and the plugin data folder, and runs `ev.py run-due` from the current plugin root.
If that root is gone it logs one line and exits 0: the next Claude Code session refreshes it.
Stdlib only.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent


def _log_failure(message):
    try:
        row = {"topic": None, "kind": "scheduler", "trigger": "scheduled", "slot": None,
               "started": datetime.now(timezone.utc).isoformat(),
               "finished": datetime.now(timezone.utc).isoformat(),
               "outcome": "failed", "error": message}
        with open(HERE / "runs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def main():
    try:
        runtime = json.loads((HERE / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        runtime = {}
    root = runtime.get("plugin_root") or ""
    ev = Path(root) / "scripts" / "ev.py" if root else None
    if ev is None or not ev.is_file():
        _log_failure("plugin folder moved — open Claude Code once to refresh")
        return 0
    env = dict(os.environ)
    env["EV_ECHO_DATA_DIR"] = str(DATA_DIR)
    if runtime.get("plugin_data"):
        env["EV_ECHO_PLUGIN_DATA"] = runtime["plugin_data"]
    try:
        subprocess.run([sys.executable, str(ev), "run-due"], env=env, check=False,
                       stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        _log_failure(f"could not start the engine: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
