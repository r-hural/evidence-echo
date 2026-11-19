"""OS scheduler installers: one task that starts the stable launcher at the wake times.

Windows: Task Scheduler (schtasks + an XML definition), run only while the user is logged on,
`StartWhenAvailable` so a missed trigger runs when the machine is back. macOS: a launchd agent.
Linux: a systemd user timer with `Persistent=true`. No admin rights. Nothing here talks to the OS
except through the injectable `run` function.
"""
import getpass
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from evidence_echo.core.settings import ConfigError
from evidence_echo.scheduling import spec, status, wake
from evidence_echo.scheduling.wake import Trigger

TASK_NAME = "Evidence Echo"
PLIST_LABEL = "com.evidence-echo.run-due"
SYSTEMD_UNIT = "evidence-echo"
LAUNCHER_SOURCE = Path(__file__).resolve().parents[3] / "scripts" / "run_due_launcher.py"
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_SYSTEMD_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def launcher_path(data_dir: Path) -> Path:
    return Path(data_dir) / "scheduling" / "run-due.py"


def _default_run(argv: list[str]):
    return subprocess.run(argv, capture_output=True, text=True)


def stable_python(data_dir: Path, platform: str) -> str:
    """An interpreter that survives plugin updates: the one the SessionStart hook recorded in
    runtime.json, else this venv's base interpreter (the engine venv is deleted after updates).
    On Windows the windowless pythonw.exe next to it, so no console opens at each wake."""
    from evidence_echo.scheduling.runner import load_runtime
    recorded = load_runtime(data_dir).get("python")
    candidates = [recorded, getattr(sys, "_base_executable", None), sys.executable]
    python = next((c for c in candidates if c and Path(c).is_file()), sys.executable)
    if platform == "win32":
        quiet = Path(python).with_name("pythonw.exe")
        if quiet.is_file():
            return str(quiet)
    return python


def _hhmm(t) -> str:
    return t.strftime("%H:%M")


# --- Windows ------------------------------------------------------------------------------------

def _win_trigger(t: Trigger) -> str:
    start = f"2026-01-01T{_hhmm(t.time)}:00"
    head = f"<CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>"
    if t.kind == "daily":
        return head + "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
    if t.kind == "weekly":
        return (head + f"<ScheduleByWeek><DaysOfWeek><{_WEEKDAYS[t.weekday]} /></DaysOfWeek>"
                "<WeeksInterval>1</WeeksInterval></ScheduleByWeek></CalendarTrigger>")
    if t.kind == "monthly":
        months = "".join(f"<{m} />" for m in _MONTHS)
        return (head + f"<ScheduleByMonth><DaysOfMonth><Day>{t.day}</Day></DaysOfMonth>"
                f"<Months>{months}</Months></ScheduleByMonth></CalendarTrigger>")
    return (head.replace("<Enabled>", f"<Repetition><Interval>PT{t.hours}H</Interval><Duration>P1D</Duration>"
                                      "</Repetition><Enabled>")
            + "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>")


def windows_user() -> str:
    """DOMAIN\\user of the account registering the task (a machine name for a local account)."""
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def render_windows(triggers: list[Trigger], launcher: Path, python: str, user: str | None = None) -> str:
    """A logon trigger without a user means "any user" and needs admin rights to register; bound
    to the registering account it does not."""
    body = "".join(_win_trigger(t) for t in triggers)
    who = f"<UserId>{escape(user)}</UserId>" if user else ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo><Description>Evidence Echo: starts the scheduled fetch and report runs"
        "</Description></RegistrationInfo>\n"
        f"  <Triggers><LogonTrigger><Enabled>true</Enabled>{who}</LogonTrigger>{body}</Triggers>\n"
        f'  <Principals><Principal id="Author">{who}<LogonType>InteractiveToken</LogonType>'
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n"
        "  <Settings><StartWhenAvailable>true</StartWhenAvailable>"
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
        "<ExecutionTimeLimit>PT3H</ExecutionTimeLimit><Enabled>true</Enabled></Settings>\n"
        f'  <Actions Context="Author"><Exec><Command>{escape(python)}</Command>'
        f"<Arguments>-I {escape(chr(34) + str(launcher) + chr(34))}</Arguments></Exec></Actions>\n"
        "</Task>\n")


# --- macOS --------------------------------------------------------------------------------------

def render_launchd(triggers: list[Trigger], launcher: Path, python: str) -> str:
    calendar, intervals = [], []
    for t in triggers:
        if t.kind == "interval":
            intervals.append(t.hours * 3600)
            continue
        keys = [("Hour", t.time.hour), ("Minute", t.time.minute)]
        if t.kind == "weekly":
            keys.append(("Weekday", (t.weekday + 1) % 7))   # launchd: 0 and 7 are Sunday, 1 Monday
        if t.kind == "monthly":
            keys.append(("Day", t.day))
        calendar.append("<dict>" + "".join(f"<key>{k}</key><integer>{v}</integer>" for k, v in keys) + "</dict>")
    extra = ""
    if calendar:
        extra += "  <key>StartCalendarInterval</key>\n  <array>" + "".join(calendar) + "</array>\n"
    if intervals:
        extra += f"  <key>StartInterval</key>\n  <integer>{min(intervals)}</integer>\n"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"  <key>Label</key>\n  <string>{PLIST_LABEL}</string>\n"
        "  <key>ProgramArguments</key>\n  <array>"
        f"<string>{escape(python)}</string><string>-I</string><string>{escape(str(launcher))}</string></array>\n"
        "  <key>RunAtLoad</key>\n  <true/>\n"
        f"{extra}</dict>\n</plist>\n")


# --- Linux --------------------------------------------------------------------------------------

def _on_calendar(t: Trigger) -> str:
    at = f"{t.time.hour:02d}:{t.time.minute:02d}:00"
    if t.kind == "daily":
        return f"*-*-* {at}"
    if t.kind == "weekly":
        return f"{_SYSTEMD_DAYS[t.weekday]} *-*-* {at}"
    if t.kind == "monthly":
        return f"*-*-{t.day:02d} {at}"
    return "hourly" if t.hours == 1 else f"*-*-* 00/{t.hours}:00:00"


def render_systemd(triggers: list[Trigger], launcher: Path, python: str) -> tuple[str, str]:
    service = ("[Unit]\nDescription=Evidence Echo scheduled runs\n\n"
               f'[Service]\nType=oneshot\nExecStart="{python}" -I "{launcher}"\n')
    lines = "".join(f"OnCalendar={_on_calendar(t)}\n" for t in triggers)
    timer = ("[Unit]\nDescription=Evidence Echo wake times\n\n[Timer]\n"
             f"{lines}OnStartupSec=120\nPersistent=true\nUnit={SYSTEMD_UNIT}.service\n\n"
             "[Install]\nWantedBy=timers.target\n")
    return service, timer


# --- install / uninstall / status ---------------------------------------------------------------

def _platform(platform: str | None) -> str:
    p = platform or sys.platform
    return "win32" if p.startswith("win") else "darwin" if p == "darwin" else "linux"


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _check(proc, what: str) -> None:
    if getattr(proc, "returncode", 0) != 0:
        detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
        raise OSError(f"{what} failed: {detail or 'exit code ' + str(proc.returncode)}")


def _paths(platform: str, data_dir: Path, home: Path) -> dict:
    return {"xml": Path(data_dir) / "scheduling" / "task.xml",
            "plist": home / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist",
            "service": home / ".config" / "systemd" / "user" / f"{SYSTEMD_UNIT}.service",
            "timer": home / ".config" / "systemd" / "user" / f"{SYSTEMD_UNIT}.timer"}


def install(data_dir: Path, run=None, platform: str | None = None, home: Path | None = None,
            python: str | None = None, uid: int | None = None) -> dict:
    run = run or _default_run
    plat, home = _platform(platform), Path(home or Path.home())
    data_dir = Path(data_dir)
    python = python or stable_python(data_dir, plat)
    launcher = launcher_path(data_dir)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LAUNCHER_SOURCE, launcher)
    sf = spec.load(data_dir)
    triggers = wake.times(sf, _local_tz())
    paths = _paths(plat, data_dir, home)
    uid = os.getuid() if uid is None and hasattr(os, "getuid") else (uid or 0)
    if plat == "win32":
        paths["xml"].write_text(render_windows(triggers, launcher, python, windows_user()),
                                    encoding="utf-16")
        _check(run(["schtasks", "/Create", "/XML", str(paths["xml"]), "/TN", TASK_NAME, "/F"]), "schtasks /Create")
        where = TASK_NAME
    elif plat == "darwin":
        paths["plist"].parent.mkdir(parents=True, exist_ok=True)
        paths["plist"].write_text(render_launchd(triggers, launcher, python), encoding="utf-8")
        run(["launchctl", "bootout", f"gui/{uid}", str(paths["plist"])])   # fine when not loaded yet
        _check(run(["launchctl", "bootstrap", f"gui/{uid}", str(paths["plist"])]), "launchctl bootstrap")
        where = str(paths["plist"])
    else:
        service, timer = render_systemd(triggers, launcher, python)
        paths["service"].parent.mkdir(parents=True, exist_ok=True)
        paths["service"].write_text(service, encoding="utf-8")
        paths["timer"].write_text(timer, encoding="utf-8")
        _check(run(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
        _check(run(["systemctl", "--user", "enable", "--now", f"{SYSTEMD_UNIT}.timer"]), "systemctl enable")
        where = str(paths["timer"])
    sf.installed = True
    spec.save(data_dir, sf)
    status.refresh(data_dir, sf, datetime.now(timezone.utc))
    return {"installed": True, "platform": plat, "task": where, "launcher": str(launcher),
            "wake_times": [t.label() for t in triggers]}


def uninstall(data_dir: Path, run=None, platform: str | None = None, home: Path | None = None,
              uid: int | None = None) -> dict:
    run = run or _default_run
    plat, home = _platform(platform), Path(home or Path.home())
    paths = _paths(plat, data_dir, home)
    uid = os.getuid() if uid is None and hasattr(os, "getuid") else (uid or 0)
    if plat == "win32":
        run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        paths["xml"].unlink(missing_ok=True)
    elif plat == "darwin":
        run(["launchctl", "bootout", f"gui/{uid}", str(paths["plist"])])
        paths["plist"].unlink(missing_ok=True)
    else:
        run(["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNIT}.timer"])
        paths["service"].unlink(missing_ok=True)
        paths["timer"].unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"])
    launcher_path(data_dir).unlink(missing_ok=True)
    sf = spec.load(data_dir)
    sf.installed = False
    spec.save(data_dir, sf)
    status.refresh(data_dir, sf, datetime.now(timezone.utc))
    return {"installed": False, "platform": plat}


def status_report(data_dir: Path, run=None, platform: str | None = None) -> dict:
    run = run or _default_run
    plat = _platform(platform)
    sf = spec.load(data_dir)
    probe = {"win32": ["schtasks", "/Query", "/TN", TASK_NAME],
             "darwin": ["launchctl", "print", f"gui/{os.getuid() if hasattr(os, 'getuid') else 0}/{PLIST_LABEL}"],
             "linux": ["systemctl", "--user", "is-enabled", f"{SYSTEMD_UNIT}.timer"]}[plat]
    try:
        registered = getattr(run(probe), "returncode", 1) == 0
    except OSError:
        registered = False
    snapshot = status.read(data_dir)
    return {"installed": sf.installed, "registered_with_os": registered, "platform": plat,
            "wake": sf.wake, "wake_times": [t.label() for t in wake.times(sf, _local_tz())],
            "next_wake": snapshot.get("next_wake"), "last_wake": snapshot.get("last_wake")}


def sync(data_dir: Path, run=None, platform: str | None = None, home: Path | None = None) -> None:
    """Re-render and re-register the OS task after a schedule change (only when installed)."""
    try:
        if spec.load(data_dir).installed:
            install(data_dir, run=run, platform=platform, home=home)
    except (OSError, ConfigError) as exc:   # a changed schedule is saved even if the OS refuses
        print(f"evidence-echo: could not update the OS task ({exc}); run `ev schedule install`",
              file=sys.stderr)
