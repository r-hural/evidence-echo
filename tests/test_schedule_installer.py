import json
import plistlib
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from evidence_echo import cli
from evidence_echo.scheduling import installer, runlog, spec, wake
from evidence_echo.scheduling.wake import Trigger
from tests.conftest import PLUGIN_DIR

UTC = ZoneInfo("UTC")
LAUNCHER = Path("/data/scheduling/run-due.py")
DAILY = Trigger("daily", time(7, 0))
WEEKLY = Trigger("weekly", time(8, 0), weekday=0)
EVERY6 = Trigger("interval", time(0, 0), hours=6)


def schedule(**topics) -> spec.ScheduleFile:
    sf = spec.ScheduleFile(timezone="UTC")
    for tid, (fetch, report, *rest) in topics.items():
        sf.topics[tid] = spec.TopicSchedule(fetch=spec.parse_rule(fetch, "fetch"), report=spec.parse_rule(report, "report"),
                                            paused=bool(rest and rest[0]))
    return sf


@pytest.fixture(autouse=True)
def machine_on_utc(monkeypatch):
    """The machine's clock rules match the UTC schedules here, so no extra hour-later wakes."""
    from datetime import timedelta
    monkeypatch.setattr(wake, "_machine_offset", lambda naive: timedelta(0))


# --- wake times -------------------------------------------------------------------------------

def test_auto_wake_is_one_trigger_per_distinct_slot_time():
    sf = schedule(a=("daily 07:00", "off"), b=("off", "weekly mon 08:00"), c=("daily 07:00", "per_run"))
    assert wake.times(sf, UTC) == [DAILY, WEEKLY]


def test_wake_forms():
    sf = schedule(a=("every 6h", "biweekly tue 09:30"), b=("hourly", "monthly last 10:00"), c=("off", "monthly 15 11:15"))
    got = {t.label() for t in wake.times(sf, UTC)}
    assert got == {"every 6h", "every 1h", "weekly tue 09:30", "daily 10:00", "monthly 15 11:15"}


def test_paused_and_desktop_topics_add_no_wake():
    sf = schedule(a=("daily 07:00", "off", True))
    sf.topics["b"] = spec.TopicSchedule(report=spec.parse_rule("daily 08:00", "report"), desktop_task=True)
    assert wake.times(sf, UTC) == []


def test_fixed_interval_wake_replaces_the_slot_triggers():
    sf = schedule(a=("daily 07:00", "off"))
    sf.wake = "every 3h"
    assert wake.times(sf, UTC) == [Trigger("interval", time(0, 0), hours=3)]


def test_times_convert_to_the_machines_zone():
    sf = schedule(a=("daily 07:00", "weekly mon 02:00"))
    sf.timezone = "Asia/Kolkata"           # UTC+5:30, no DST
    got = wake.times(sf, UTC)
    assert Trigger("daily", time(1, 30)) in got
    assert Trigger("weekly", time(20, 30), weekday=6) in got      # Monday 02:00 there is Sunday evening in UTC


# --- renderers --------------------------------------------------------------------------------

def test_render_windows():
    text = installer.render_windows([DAILY, WEEKLY, EVERY6], Path("D:/data/me/.evidence-echo/scheduling/run-due.py"),
                                    "C:/Python/python.exe")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(text.split("\n", 1)[1])
    assert len(root.findall(".//t:CalendarTrigger", ns)) == 3 and root.find(".//t:LogonTrigger", ns) is not None
    assert root.find(".//t:StartWhenAvailable", ns).text == "true"
    assert root.find(".//t:RunLevel", ns).text == "LeastPrivilege"
    assert root.find(".//t:LogonType", ns).text == "InteractiveToken"
    assert root.find(".//t:Command", ns).text == "C:/Python/python.exe"
    assert root.find(".//t:Arguments", ns).text == '-I "' + str(Path("D:/data/me/.evidence-echo/scheduling/run-due.py")) + '"'
    assert root.find(".//t:ScheduleByWeek/t:DaysOfWeek/t:Monday", ns) is not None
    assert root.find(".//t:Repetition/t:Interval", ns).text == "PT6H"
    starts = [e.text for e in root.findall(".//t:StartBoundary", ns)]
    assert "2026-01-01T07:00:00" in starts and "2026-01-01T08:00:00" in starts


def test_render_windows_monthly_and_escaping():
    text = installer.render_windows([Trigger("monthly", time(9, 0), day=15)], Path("C:/a&b/run-due.py"), "py<thon>")
    root = ET.fromstring(text.split("\n", 1)[1])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:Day", ns).text == "15" and len(root.findall(".//t:Months/*", ns)) == 12
    assert root.find(".//t:Command", ns).text == "py<thon>"


def test_render_launchd():
    plist = plistlib.loads(installer.render_launchd([DAILY, WEEKLY, EVERY6], Path("/d/run-due.py"), "/usr/bin/python3").encode())
    assert plist["Label"] == "com.evidence-echo.run-due" and plist["RunAtLoad"] is True
    assert plist["ProgramArguments"] == ["/usr/bin/python3", "-I", str(Path("/d/run-due.py"))]
    assert plist["StartCalendarInterval"] == [{"Hour": 7, "Minute": 0}, {"Hour": 8, "Minute": 0, "Weekday": 1}]
    assert plist["StartInterval"] == 21600


def test_render_systemd():
    service, timer = installer.render_systemd([DAILY, WEEKLY, EVERY6, Trigger("monthly", time(9, 5), day=15),
                                               Trigger("interval", time(0, 0), hours=1)],
                                              Path("/d/run-due.py"), "/usr/bin/python3")
    assert f'ExecStart="/usr/bin/python3" -I "{Path("/d/run-due.py")}"' in service and "Type=oneshot" in service
    lines = [l for l in timer.splitlines() if l.startswith("OnCalendar=")]
    assert lines == ["OnCalendar=*-*-* 07:00:00", "OnCalendar=Mon *-*-* 08:00:00", "OnCalendar=*-*-* 00/6:00:00",
                     "OnCalendar=*-*-15 09:05:00", "OnCalendar=hourly"]
    assert "Persistent=true" in timer and "WantedBy=timers.target" in timer


# --- install / uninstall ----------------------------------------------------------------------

class FakeRun:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, argv):
        self.calls.append(argv)
        code = 1 if self.fail_on and argv[:2] == self.fail_on else 0
        return subprocess.CompletedProcess(argv, code, "", "access denied" if code else "")


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    sf = schedule(seo=("daily 07:00", "weekly mon 08:00"))
    spec.save(d, sf)
    return d


def test_install_windows(data, tmp_path):
    run = FakeRun()
    out = installer.install(data, run=run, platform="win32", home=tmp_path / "home", python="C:/Python/python.exe")
    xml_path = data / "scheduling" / "task.xml"
    assert run.calls == [["schtasks", "/Create", "/XML", str(xml_path), "/TN", "Evidence Echo", "/F"]]
    assert "<Command>C:/Python/python.exe</Command>" in xml_path.read_text(encoding="utf-16")
    launcher = data / "scheduling" / "run-due.py"
    assert launcher.read_bytes() == (PLUGIN_DIR / "scripts" / "run_due_launcher.py").read_bytes()
    assert spec.load(data).installed is True and out["installed"] and len(out["wake_times"]) == 2
    assert (data / "scheduling" / "status.json").exists()


def test_install_failure_raises_and_is_not_marked_installed(data, tmp_path):
    with pytest.raises(OSError, match="access denied"):
        installer.install(data, run=FakeRun(fail_on=["schtasks", "/Create"]), platform="win32", home=tmp_path)
    assert spec.load(data).installed is False


def test_install_macos(data, tmp_path):
    run, home = FakeRun(), tmp_path / "home"
    installer.install(data, run=run, platform="darwin", home=home, python="/usr/bin/python3", uid=501)
    plist = home / "Library" / "LaunchAgents" / "com.evidence-echo.run-due.plist"
    assert run.calls == [["launchctl", "bootout", "gui/501", str(plist)], ["launchctl", "bootstrap", "gui/501", str(plist)]]
    assert plistlib.loads(plist.read_bytes())["RunAtLoad"] is True


def test_install_linux(data, tmp_path):
    run, home = FakeRun(), tmp_path / "home"
    installer.install(data, run=run, platform="linux", home=home, python="/usr/bin/python3")
    assert run.calls == [["systemctl", "--user", "daemon-reload"],
                         ["systemctl", "--user", "enable", "--now", "evidence-echo.timer"]]
    assert (home / ".config" / "systemd" / "user" / "evidence-echo.timer").read_text(encoding="utf-8").count("OnCalendar=") == 2


def test_uninstall_and_status(data, tmp_path):
    run = FakeRun()
    installer.install(data, run=run, platform="win32", home=tmp_path, python="p")
    report = installer.status_report(data, run=run, platform="win32")
    expected = [t.label() for t in wake.times(spec.load(data), installer._local_tz())]   # shown in the machine's zone
    assert report["installed"] and report["registered_with_os"] and report["wake_times"] == expected and len(expected) == 2
    out = installer.uninstall(data, run=run, platform="win32", home=tmp_path)
    assert out["installed"] is False and spec.load(data).installed is False
    assert run.calls[-1] == ["schtasks", "/Delete", "/TN", "Evidence Echo", "/F"]
    assert not (data / "scheduling" / "run-due.py").exists()


def test_sync_only_touches_the_os_when_installed(data, tmp_path):
    run = FakeRun()
    installer.sync(data, run=run, platform="win32", home=tmp_path)
    assert run.calls == []
    installer.install(data, run=run, platform="win32", home=tmp_path, python="p")
    run.calls.clear()
    installer.sync(data, run=run, platform="win32", home=tmp_path)
    assert len(run.calls) == 1


def test_sync_failure_keeps_the_saved_schedule_and_warns(data, tmp_path, capsys):
    installer.install(data, run=FakeRun(), platform="win32", home=tmp_path, python="p")
    installer.sync(data, run=FakeRun(fail_on=["schtasks", "/Create"]), platform="win32", home=tmp_path)
    assert "could not update the OS task" in capsys.readouterr().err


def test_cli_install_status_uninstall_wiring(workspace, capsys, monkeypatch):
    monkeypatch.setattr(installer, "install", lambda d: {"installed": True})
    monkeypatch.setattr(installer, "status_report", lambda d: {"installed": True, "wake_times": []})
    monkeypatch.setattr(installer, "uninstall", lambda d: {"installed": False})
    for action, key, value in (("install", "installed", True), ("status", "wake_times", []), ("uninstall", "installed", False)):
        assert cli.main(["schedule", action]) == 0
        assert json.loads(capsys.readouterr().out)[key] == value


# --- the launcher -----------------------------------------------------------------------------

def _launcher_copy(tmp_path):
    folder = tmp_path / "data" / "scheduling"
    folder.mkdir(parents=True)
    target = folder / "run-due.py"
    target.write_bytes((PLUGIN_DIR / "scripts" / "run_due_launcher.py").read_bytes())
    return target


def test_launcher_with_a_missing_plugin_root_logs_and_exits_0(tmp_path):
    launcher = _launcher_copy(tmp_path)
    (launcher.parent / "runtime.json").write_text(json.dumps({"plugin_root": str(tmp_path / "gone")}), encoding="utf-8")
    assert subprocess.run([sys.executable, str(launcher)], capture_output=True).returncode == 0
    [row] = runlog.read_all(tmp_path / "data")
    assert row["outcome"] == "failed" and "plugin folder moved" in row["error"]


def test_launcher_without_runtime_json_logs_and_exits_0(tmp_path):
    launcher = _launcher_copy(tmp_path)
    assert subprocess.run([sys.executable, str(launcher)], capture_output=True).returncode == 0
    assert "plugin folder moved" in runlog.read_all(tmp_path / "data")[0]["error"]


def test_launcher_runs_the_current_plugin_root(tmp_path):
    launcher = _launcher_copy(tmp_path)
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    marker = tmp_path / "marker.json"
    (root / "scripts" / "ev.py").write_text(
        "import json, os, sys\n"
        f"open({str(marker)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'data': os.environ['EV_ECHO_DATA_DIR'], "
        "'plugin_data': os.environ.get('EV_ECHO_PLUGIN_DATA')}))\n", encoding="utf-8")
    (launcher.parent / "runtime.json").write_text(json.dumps({"plugin_root": str(root), "plugin_data": "PD"}), encoding="utf-8")
    assert subprocess.run([sys.executable, str(launcher)], capture_output=True).returncode == 0
    seen = json.loads(marker.read_text(encoding="utf-8"))
    assert seen == {"argv": ["run-due"], "data": str(tmp_path / "data"), "plugin_data": "PD"}


def test_stable_python_prefers_the_recorded_interpreter_and_pythonw(tmp_path):
    bin_dir = tmp_path / "py"
    bin_dir.mkdir()
    (bin_dir / "python.exe").write_text("")
    (bin_dir / "pythonw.exe").write_text("")
    data = tmp_path / "data"
    (data / "scheduling").mkdir(parents=True)
    (data / "scheduling" / "runtime.json").write_text(json.dumps({"python": str(bin_dir / "python.exe")}))
    assert installer.stable_python(data, "win32") == str(bin_dir / "pythonw.exe")
    assert installer.stable_python(data, "linux") == str(bin_dir / "python.exe")


def test_stable_python_never_picks_a_missing_interpreter(tmp_path):
    data = tmp_path / "data"
    (data / "scheduling").mkdir(parents=True)
    (data / "scheduling" / "runtime.json").write_text(json.dumps({"python": str(tmp_path / "gone.exe")}))
    assert Path(installer.stable_python(data, "linux")).is_file()


def test_zones_that_shift_apart_are_detected():
    berlin = ZoneInfo("Europe/Berlin")
    tokyo_machine = lambda naive: naive.replace(tzinfo=ZoneInfo("Asia/Tokyo")).utcoffset()
    berlin_machine = lambda naive: naive.replace(tzinfo=berlin).utcoffset()
    assert wake._shifts_apart(berlin, 2026, tokyo_machine) is True
    assert wake._shifts_apart(berlin, 2026, berlin_machine) is False


def test_a_second_wake_an_hour_later_when_the_zones_shift_apart(monkeypatch):
    monkeypatch.setattr(wake, "_machine_offset", lambda naive: naive.replace(tzinfo=ZoneInfo("Asia/Tokyo")).utcoffset())
    sf = schedule(seo=("off", "daily 07:00"))
    sf.timezone = "Europe/Berlin"
    first, second = wake.times(sf, ZoneInfo("Asia/Tokyo"))
    assert first.kind == second.kind == "daily" and second.time.hour == first.time.hour + 1


def test_render_windows_binds_the_logon_trigger_to_the_user():
    text = installer.render_windows([DAILY], Path("C:/x/run-due.py"), "pythonw.exe", "NITRO17\\me&co")
    root = ET.fromstring(text.split("\n", 1)[1])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", ns).text == "NITRO17\\me&co"
    assert root.find("t:Principals/t:Principal/t:UserId", ns).text == "NITRO17\\me&co"
    assert "<UserId>" not in installer.render_windows([DAILY], Path("C:/x/run-due.py"), "pythonw.exe")

