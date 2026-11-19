import json
import os
import subprocess
from contextlib import closing

import pytest

from evidence_echo import mcp_server
from evidence_echo.core import store
from tests.conftest import PLUGIN_DIR, load_script
from tests.evidence_fixtures import add_study, seed_topic
from tests.import_guard import run_guarded
from tests.test_read_paths import snapshot

ev_mcp = load_script("scripts/ev_mcp.py")


LAUNCHERS = PLUGIN_DIR / "scripts"


def test_mcp_json_names_the_launcher_by_full_path():
    servers = json.loads((PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"evidence-echo"}
    entry = servers["evidence-echo"]
    # a bare name ("python") is looked up in the user's project folder first on Windows
    assert entry["command"] == "${CLAUDE_PLUGIN_ROOT}/scripts/ev_mcp"
    assert entry["args"] == []
    assert entry["env"] == {"EV_ECHO_DATA_DIR": "${user_config.data_dir}"}
    manifest = next(p for p in (PLUGIN_DIR / ".claude-plugin").glob("*.json")
                    if json.loads(p.read_text(encoding="utf-8")).get("name") == "evidence-echo")
    text = (PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8")
    for option in json.loads(manifest.read_text(encoding="utf-8"))["userConfig"]:
        assert (option in text) == (option == "data_dir")


def test_launchers_line_endings_and_lookup():
    sh = (LAUNCHERS / "ev_mcp").read_bytes()
    cmd = (LAUNCHERS / "ev_mcp.cmd").read_bytes()
    LF, CR = bytes([10]), bytes([13])
    assert sh.startswith(b"#!/bin/sh" + LF) and CR not in sh
    assert cmd.count(LF) == cmd.count(CR + LF)          # CRLF only
    assert b'set "NoDefaultCurrentDirectoryInExePath=1"' in cmd
    assert cmd.index(b"NoDefaultCurrentDirectoryInExePath") < cmd.index(b"for %%P")
    for text in (sh, cmd):
        assert b"ev_mcp.py" in text and b"(3, 11)" in text


def test_posix_launcher_is_executable_in_git():
    done = subprocess.run(["git", "-C", str(PLUGIN_DIR), "ls-files", "-s", "scripts/ev_mcp"],
                          capture_output=True, text=True)
    if done.returncode != 0 or not done.stdout:
        pytest.skip("not a git checkout")
    assert done.stdout.startswith("100755 "), done.stdout


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "serve", lambda i, o, *a: calls.append((i, o)) or 0)
    return calls


def test_main_serves(served):
    marker_in, marker_out = object(), object()
    assert ev_mcp.main(stdin=marker_in, stdout=marker_out) == 0
    assert served == [(marker_in, marker_out)]


def test_old_python_exits_2(monkeypatch, capsys, served):
    monkeypatch.setattr(ev_mcp, "python_version", lambda: (3, 9))
    assert ev_mcp.main(stdin=object(), stdout=object()) == 2
    captured = capsys.readouterr()
    assert "Evidence Echo needs Python 3.11+ (found 3.9" in captured.err and captured.out == ""
    assert served == []


# --- a whole session in a child interpreter that refuses third-party imports -----------------------
SESSION = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_topics", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "find_evidence", "arguments": {"question": "title tag clicks"}}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
     "params": {"name": "get_study", "arguments": {"topic": "seo", "study_id": "s1"}}},
]
CHILD = ("import json, runpy, sys\n"
         "code = 0\n"
         "try:\n"
         "    runpy.run_path({script!r}, run_name='__main__')\n"
         "except SystemExit as stop:\n"
         "    code = stop.code or 0\n"
         "sys.stderr.write('MODULES:' + json.dumps(sorted(sys.modules)) + '\\n')\n"
         "sys.exit(code)\n")


def workspace_for_session(tmp_path, monkeypatch):
    data = tmp_path / "Daten mit Leer"
    for topic_id, mode in (("seo", "questions"), ("quotes", "quotes"), ("finds", "findings")):
        with closing(store.connect(seed_topic(data, topic_id, mode=mode))) as conn:
            add_study(conn, f"Title tag study {topic_id}", "title tag clicks rose",
                      headline_quote="title tag clicks rose by five percent",
                      full_text="Before. title tag clicks rose by five percent. After.")
        page = data / "reports" / topic_id / f"{topic_id}.html"
        page.parent.mkdir(parents=True)
        page.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    return data


def run_session(data):
    stdin = b"".join(json.dumps(m).encode("utf-8") + b"\n" for m in SESSION)
    code = CHILD.format(script=str(PLUGIN_DIR / "scripts" / "ev_mcp.py"))
    return run_guarded(code, stdin=stdin, env={**os.environ, "EV_ECHO_DATA_DIR": str(data),
                                               "PYTHONDONTWRITEBYTECODE": "1"})


def test_end_to_end_session_is_stdlib_only(tmp_path, monkeypatch):
    data = workspace_for_session(tmp_path, monkeypatch)
    done = run_session(data)
    err = done.stderr.decode("utf-8")
    assert done.returncode == 0, err
    assert "non-stdlib import" not in err
    replies = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]       # the notification gets no reply
    assert all(r["result"].get("isError", False) is False for r in replies)
    found = json.loads(replies[3]["result"]["content"][0]["text"])
    assert found["topics_searched"] == ["finds", "quotes", "seo"] and found["studies"]
    assert any(s["page"] and s["page"].startswith("file:///") for s in found["studies"])
    modules = json.loads(next(line for line in err.splitlines() if line.startswith("MODULES:"))[len("MODULES:"):])
    for name in ("httpx", "anthropic", "numpy", "courlan", "trafilatura"):
        assert name not in modules


def test_end_to_end_session_writes_nothing(tmp_path, monkeypatch):
    data = workspace_for_session(tmp_path, monkeypatch)
    import sqlite3
    old = seed_topic(data, "old")
    with closing(sqlite3.connect(old)) as raw:
        raw.execute("PRAGMA user_version = 13")
    before = snapshot(data)
    done = run_session(data)
    assert done.returncode == 0, done.stderr.decode("utf-8")
    assert snapshot(data) == before
    assert not [p for p in data.rglob("*") if p.suffix in (".db-journal", "-wal", "-shm") or p.name.endswith(
        ("-journal", "-wal", "-shm"))]
    with closing(sqlite3.connect(old)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 13
    found = json.loads(json.loads(done.stdout.decode("utf-8").splitlines()[3])["result"]["content"][0]["text"])
    assert {"topic": "old", "status": "needs_upgrade: run any Evidence Echo report"} in found["skipped"]


def test_stdout_is_only_json(tmp_path, monkeypatch):
    done = run_session(workspace_for_session(tmp_path, monkeypatch))
    lines = done.stdout.decode("utf-8").splitlines()
    assert lines and all(json.loads(line)["jsonrpc"] == "2.0" for line in lines)


def _start_launcher(cwd, stdin, env):
    """Start the launcher the way Claude Code does (cross-spawn runs a .cmd through cmd.exe)."""
    if os.name == "nt":
        cmd = 'cmd.exe /d /s /c ""{}""'.format(LAUNCHERS / "ev_mcp.cmd")
    else:
        cmd = [str(LAUNCHERS / "ev_mcp")]
    return subprocess.run(cmd, input=stdin, capture_output=True, cwd=cwd, env=env, timeout=120)


def test_launcher_never_runs_an_interpreter_from_the_current_folder(tmp_path, monkeypatch):
    if os.name != "nt" and not os.access(LAUNCHERS / "ev_mcp", os.X_OK):
        pytest.skip("checkout without the exec bit")
    data = workspace_for_session(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    marker = project / "shadow-ran.txt"
    for name in ("python.bat", "python.cmd", "python3.bat", "py.bat"):
        (project / name).write_bytes(b"@echo x> shadow-ran.txt\r\n")
    for name in ("python", "python3"):
        shadow = project / name
        shadow.write_text("#!/bin/sh\necho x > shadow-ran.txt\n", encoding="utf-8", newline="\n")
        shadow.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k.lower() != "nodefaultcurrentdirectoryinexepath"}
    env.update({"EV_ECHO_DATA_DIR": str(data), "PYTHONDONTWRITEBYTECODE": "1"})
    stdin = b"".join(json.dumps(m).encode("utf-8") + b"\n" for m in SESSION[:3])
    done = _start_launcher(project, stdin, env)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert not marker.exists()
    replies = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
