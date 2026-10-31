import json
import os
import subprocess
from contextlib import closing
from types import SimpleNamespace

import pytest

from evidence_echo import mcp_server
from evidence_echo.core import store
from tests.conftest import PLUGIN_DIR, load_script
from tests.evidence_fixtures import add_study, seed_topic
from tests.import_guard import run_guarded
from tests.test_read_paths import snapshot

ev_mcp = load_script("scripts/ev_mcp.py")


def test_mcp_json_has_both_entries():
    servers = json.loads((PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"evidence-echo", "evidence-echo-py3"}
    assert servers["evidence-echo"]["command"] == "python" and servers["evidence-echo-py3"]["command"] == "python3"
    for entry in servers.values():
        first = entry["args"][0]
        assert first.startswith("${CLAUDE_PLUGIN_ROOT}") and first.endswith("scripts/ev_mcp.py")
        assert entry["env"] == {"EV_ECHO_DATA_DIR": "${user_config.data_dir}"}
    assert servers["evidence-echo"]["args"][1:] == []
    assert servers["evidence-echo-py3"]["args"][1:] == ["--defer-to-python"]
    manifest = next(p for p in (PLUGIN_DIR / ".claude-plugin").glob("*.json")
                    if json.loads(p.read_text(encoding="utf-8")).get("name") == "evidence-echo")
    text = (PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8")
    for option in json.loads(manifest.read_text(encoding="utf-8"))["userConfig"]:
        assert (option in text) == (option == "data_dir")


def fake_run(stdout=b"True", returncode=0, raises=None):
    def run(cmd, **kwargs):
        if raises:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout)
    return run


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "serve", lambda i, o, *a: calls.append((i, o)) or 0)
    return calls


def test_defer_when_python_works(served):
    assert ev_mcp.main([ev_mcp.DEFER_FLAG], which=lambda name: "/usr/bin/python", run=fake_run(),
                       stdin=object(), stdout=object()) == 0
    assert served == []


@pytest.mark.parametrize("which,run", [
    (lambda name: None, fake_run()),
    (lambda name: "/usr/bin/python", fake_run(raises=subprocess.TimeoutExpired("python", 5))),
    (lambda name: "/usr/bin/python", fake_run(raises=FileNotFoundError())),
    (lambda name: "/usr/bin/python", fake_run(stdout=b"False")),
    (lambda name: "/usr/bin/python", fake_run(returncode=1)),
])
def test_serve_when_python_missing_or_broken(served, which, run):
    marker_in, marker_out = object(), object()
    assert ev_mcp.main([ev_mcp.DEFER_FLAG], which=which, run=run, stdin=marker_in, stdout=marker_out) == 0
    assert served == [(marker_in, marker_out)]


def test_plain_entry_never_defers(served):
    ev_mcp.main([], which=lambda name: "python", run=fake_run(), stdin=object(), stdout=object())
    assert len(served) == 1


def test_interpreter_found_in_the_current_folder_is_not_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ran = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        return SimpleNamespace(returncode=0, stdout=b"True")

    assert ev_mcp.python_is_usable(str(tmp_path / "python.exe"), run=run) is False
    assert ran == []


def test_python_is_usable_edges():
    assert ev_mcp.python_is_usable(None) is False
    assert ev_mcp.python_is_usable("/usr/bin/python", run=fake_run(stdout="True\n")) is True


def test_old_python_exits_2(monkeypatch, capsys, served):
    monkeypatch.setattr(ev_mcp, "python_version", lambda: (3, 9))
    assert ev_mcp.main([], stdin=object(), stdout=object()) == 2
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
