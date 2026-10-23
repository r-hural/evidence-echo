import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import PLUGIN_DIR, load_script

ev = load_script("scripts/ev.py")


@pytest.fixture
def plugin(tmp_path):
    root = tmp_path / "plugin dir with space"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    return root


@pytest.fixture
def root(tmp_path):
    """<data>/venv: holds one venv per pyproject digest, plus the build lock."""
    return tmp_path / "data" / "venv"


@pytest.fixture
def venv(root, plugin):
    return ev.venv_dir(root, _digest(plugin))


def _digest(plugin):
    return hashlib.sha256((plugin / "pyproject.toml").read_bytes()).hexdigest()


def _make_python(venv):
    py = ev.venv_python(venv)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    return py


def _stamp(venv, plugin):
    _make_python(venv)
    (venv / ".stamp").write_text(_digest(plugin), encoding="utf-8")


class FakeRun:
    """Stands in for ev._run: records commands; the venv/uv step creates the venv python."""

    def __init__(self, fail_on=None):
        self.fail_on, self.calls = fail_on, []

    def __call__(self, cmd, env=None):
        self.calls.append((cmd, env))
        if self.fail_on and self.fail_on in cmd:
            return subprocess.CompletedProcess(cmd, 1, "Collecting httpx\n", "ERROR: no network\n")
        if "venv" in cmd:
            _make_python(Path(cmd[-1]))
        return subprocess.CompletedProcess(cmd, 0, "Collecting httpx\nInstalled\n", "")


def test_venv_python_path(tmp_path):
    expected = (tmp_path / "Scripts" / "python.exe" if os.name == "nt"
                else tmp_path / "bin" / "python")
    assert ev.venv_python(tmp_path) == expected


def test_venv_dir_is_per_digest(root):
    assert ev.venv_dir(root, "ab" * 32) == root / ("ab" * 8)


def test_plugin_data_dir_precedence():
    assert ev.plugin_data_dir({"EV_ECHO_PLUGIN_DATA": "a", "CLAUDE_PLUGIN_DATA": "b"}) == Path("a")
    assert ev.plugin_data_dir({"CLAUDE_PLUGIN_DATA": "b"}) == Path("b")
    assert ev.plugin_data_dir({"EV_ECHO_DATA_DIR": "d"}) == Path("d") / "cache"
    assert ev.plugin_data_dir({}) == Path.home() / ".evidence-echo" / "cache"


def test_needs_build(plugin, venv):
    digest = ev.pyproject_digest(plugin)
    assert digest == _digest(plugin)
    assert ev.needs_build(venv, digest)                  # nothing yet
    _make_python(venv)
    assert ev.needs_build(venv, digest)                  # no stamp
    (venv / ".stamp").write_text(digest, encoding="utf-8")
    assert not ev.needs_build(venv, digest)
    assert ev.needs_build(venv, "other")                 # pyproject changed


def test_builds_with_venv_and_pip_without_uv(plugin, root, venv, monkeypatch, capsys):
    fake = FakeRun()
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_uv", lambda: None)
    assert ev.ensure_venv(root, plugin) == venv
    (venv_cmd, _), (pip_cmd, _) = fake.calls
    assert venv_cmd == [sys.executable, "-m", "venv", "--clear", str(venv)]
    assert pip_cmd == [str(ev.venv_python(venv)), "-m", "pip", "install", str(plugin)]
    assert (venv / ".stamp").read_text(encoding="utf-8") == _digest(plugin)
    assert "Setting up Evidence Echo engine (one-time, ~1 min)..." in capsys.readouterr().err
    assert not (root / ".lock").exists()


def test_builds_with_uv_when_available(plugin, root, venv, monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_uv", lambda: "/tools/uv")
    ev.ensure_venv(root, plugin)
    # uv venv + uv pip install: never writes uv.lock or an editable install into the
    # (versioned, read-mostly) plugin cache, unlike `uv sync --project <plugin>`
    (venv_cmd, _), (pip_cmd, _) = fake.calls
    assert venv_cmd == ["/tools/uv", "venv", "--allow-existing", "--python", sys.executable,
                        str(venv)]
    assert pip_cmd == ["/tools/uv", "pip", "install", "--python",
                       str(ev.venv_python(venv)), str(plugin)]
    assert (venv / ".stamp").exists()


def test_build_passes_constraints_to_pip_and_uv(plugin, root, monkeypatch):
    constraints = plugin / "constraints.txt"
    constraints.write_text("httpx==0.28.1\n", encoding="utf-8")
    for uv in (None, "/tools/uv"):  # plain pip (macOS/Linux without uv) and uv
        fake = FakeRun()
        monkeypatch.setattr(ev, "_run", fake)
        monkeypatch.setattr(ev, "_find_uv", lambda uv=uv: uv)
        ev.build(root / "v", plugin)
        _, (pip_cmd, _) = fake.calls
        assert pip_cmd[-3:] == [str(plugin), "-c", str(constraints)]
    assert "-c" in ev.fallback_command(root / "v", plugin)


def test_build_without_constraints_file_passes_no_c(plugin, root, monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_uv", lambda: None)
    ev.build(root / "v", plugin)
    _, (pip_cmd, _) = fake.calls
    assert "-c" not in pip_cmd


def test_digest_changes_with_constraints(plugin):
    bare = ev.pyproject_digest(plugin)
    assert bare == _digest(plugin)                       # no constraints file: pyproject only
    (plugin / "constraints.txt").write_text("httpx==0.28.1\n", encoding="utf-8")
    pinned = ev.pyproject_digest(plugin)
    assert pinned != bare
    (plugin / "constraints.txt").write_text("httpx==0.28.2\n", encoding="utf-8")
    assert ev.pyproject_digest(plugin) not in (bare, pinned)  # a pins-only update rebuilds


def test_shipped_constraints_pin_every_dependency_exactly():
    lines = [line.strip() for line in
             (PLUGIN_DIR / "constraints.txt").read_text(encoding="utf-8").splitlines()]
    pins = [line for line in lines if line and not line.startswith("#")]
    assert pins and all("==" in line for line in pins)
    names = {line.split("==")[0].lower() for line in pins}
    for dep in ("feedparser", "httpx", "httpcore", "anthropic", "python-dateutil",
                "trafilatura", "numpy"):
        assert dep in names


def test_no_rebuild_when_stamp_matches(plugin, root, venv, monkeypatch, capsys):
    _stamp(venv, plugin)
    fake = FakeRun()
    monkeypatch.setattr(ev, "_run", fake)
    assert ev.ensure_venv(root, plugin) == venv
    assert fake.calls == []
    assert capsys.readouterr().err == ""


def test_update_builds_new_venv_and_never_clears_the_old_one_first(plugin, root, venv, monkeypatch):
    old = root / "0123456789abcdef"
    _make_python(old)
    (old / ".stamp").write_text("old digest", encoding="utf-8")
    _age(old / ".stamp", ev.VENV_IDLE_SECONDS + 60)
    fake = FakeRun()
    monkeypatch.setattr(ev, "_run", fake)
    monkeypatch.setattr(ev, "_find_uv", lambda: None)
    ev.ensure_venv(root, plugin)
    assert all(str(old) not in cmd for cmd, _ in fake.calls)  # a running engine keeps its venv
    assert (venv / ".stamp").exists()
    assert not old.exists()                                    # tidied after the new build


def _age(path, seconds):
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_update_keeps_a_venv_another_session_used_recently(plugin, root, venv, monkeypatch):
    old = root / "0123456789abcdef"
    _make_python(old)
    (old / ".stamp").write_text("old digest", encoding="utf-8")  # fresh: a session on the old version
    monkeypatch.setattr(ev, "_run", FakeRun())
    monkeypatch.setattr(ev, "_find_uv", lambda: None)
    ev.ensure_venv(root, plugin)
    assert old.exists()                                        # no delete/rebuild loop between versions


def test_reusing_a_venv_marks_it_as_used(plugin, root, venv, monkeypatch):
    _stamp(venv, plugin)
    _age(venv / ".stamp", ev.VENV_IDLE_SECONDS + 60)
    monkeypatch.setattr(ev, "_run", FakeRun())
    ev.ensure_venv(root, plugin)
    assert time.time() - (venv / ".stamp").stat().st_mtime < 60


def test_waits_for_another_launcher_and_reuses_its_build(plugin, root, venv, monkeypatch, capsys):
    (root / ".lock").mkdir(parents=True)
    fake = FakeRun()
    monkeypatch.setattr(ev, "_run", fake)

    def other_launcher_finishes(seconds):
        _stamp(venv, plugin)
        (root / ".lock").rmdir()

    monkeypatch.setattr(ev, "_sleep", other_launcher_finishes)
    assert ev.ensure_venv(root, plugin) == venv
    assert fake.calls == []
    err = capsys.readouterr().err
    assert "Waiting for another Evidence Echo setup to finish..." in err
    assert "Setting up" not in err


def test_stale_lock_is_taken_over(plugin, root, venv, monkeypatch):
    lock = root / ".lock"
    lock.mkdir(parents=True)
    old = time.time() - ev.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    monkeypatch.setattr(ev, "_run", FakeRun())
    monkeypatch.setattr(ev, "_find_uv", lambda: None)
    monkeypatch.setattr(ev, "_sleep", lambda s: pytest.fail("must not wait on a stale lock"))
    ev.ensure_venv(root, plugin)
    assert (venv / ".stamp").exists()
    assert not lock.exists()


def test_first_run_keeps_stdout_clean(plugin, root, venv, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(root.parent))
    monkeypatch.setattr(ev, "_run", FakeRun())
    monkeypatch.setattr(ev, "_find_uv", lambda: None)
    seen = {}

    def passthrough(cmd, env):
        seen.update(cmd=cmd, env=env)
        return 0

    monkeypatch.setattr(ev, "_passthrough", passthrough)
    assert ev.main(["status"], plugin=plugin) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert "Collecting" not in err
    assert seen["cmd"] == [str(ev.venv_python(venv)), "-m", "evidence_echo", "status"]
    assert seen["env"]["PYTHONPATH"] == str(plugin / "src")


def test_existing_pythonpath_kept_after_plugin_src(plugin, root, venv, monkeypatch):
    _stamp(venv, plugin)
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(root.parent))
    monkeypatch.setenv("PYTHONPATH", "elsewhere")
    seen = {}
    monkeypatch.setattr(ev, "_passthrough", lambda cmd, env: seen.update(env=env) or 0)
    ev.main(["status"], plugin=plugin)
    assert seen["env"]["PYTHONPATH"] == str(plugin / "src") + os.pathsep + "elsewhere"


def test_engine_exit_code_propagates(plugin, root, venv, monkeypatch):
    _stamp(venv, plugin)
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(root.parent))
    monkeypatch.setattr(ev, "_passthrough", lambda cmd, env: 7)
    assert ev.main(["status"], plugin=plugin) == 7


def test_build_failure_exits_3_then_retries(plugin, root, venv, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(root.parent))
    monkeypatch.setattr(ev, "_find_uv", lambda: None)
    monkeypatch.setattr(ev, "_passthrough", lambda cmd, env: 0)
    monkeypatch.setattr(ev, "_run", FakeRun(fail_on="pip"))
    assert ev.main(["status"], plugin=plugin) == 3
    err = capsys.readouterr().err
    assert "pip install" in err and "ERROR: no network" in err
    assert "Manual fallback:" in err and str(plugin) in err
    assert not (venv / ".stamp").exists()
    assert not (root / ".lock").exists()                 # released despite the failure

    retry = FakeRun()
    monkeypatch.setattr(ev, "_run", retry)
    assert ev.main(["status"], plugin=plugin) == 0
    assert len(retry.calls) == 2                         # rebuilt, not run half-built


def test_old_python_exits_2(plugin, monkeypatch, capsys):
    monkeypatch.setattr(ev, "python_version", lambda: (3, 10))
    calls = []
    monkeypatch.setattr(ev, "_run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(ev, "_passthrough", lambda *a, **k: calls.append(a))
    assert ev.main(["status"], plugin=plugin) == 2
    assert (f"Evidence Echo needs Python 3.11+ (found 3.10 at {sys.executable})"
            in capsys.readouterr().err)
    assert calls == []


def test_missing_tool_is_a_failed_step():
    proc = ev._run(["definitely-not-a-real-tool-4711"])
    assert proc.returncode != 0


@pytest.mark.slow
def test_real_build_and_run(tmp_path):
    env = {**os.environ, "EV_ECHO_PLUGIN_DATA": str(tmp_path)}
    cmd = [sys.executable, str(PLUGIN_DIR / "scripts" / "ev.py"), "templates"]
    first = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)[0]["id"] == "seo"
    assert "Setting up Evidence Echo engine" in first.stderr
    second = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    assert second.returncode == 0
    assert "Setting up" not in second.stderr


@pytest.mark.slow
def test_two_first_launches_at_once_both_succeed(tmp_path):
    env = {**os.environ, "EV_ECHO_PLUGIN_DATA": str(tmp_path)}
    cmd = [sys.executable, str(PLUGIN_DIR / "scripts" / "ev.py"), "templates"]
    first = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(3)
    second = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for proc in (first, second):
        out, err = proc.communicate(timeout=900)
        assert proc.returncode == 0, err
        assert json.loads(out)[0]["id"] == "seo"
