"""Settings files: path rules, value checks, config.json and state.json.

config.json holds global settings written by `ev config set` (and by migration);
state.json is engine bookkeeping. Both are written atomically.
"""
import json
import os
import re
from pathlib import Path

CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
VERSION = 1
MAX_HORIZON_DAYS = 3650
CLASSIFY_BATCH_MAX = 500

GLOBAL_DEFAULTS = {"reports_dir": None, "triage_backend": "auto",
                   "judgment_backend": "auto", "embedder": "auto",
                   "output_language": "en", "fetch_horizon_days": 7,
                   "scan_depth": "headlines", "ask_on_disputes": "off",
                   "adaptive_fetch": "off", "html_reports": "on", "rejections_report": "off",
                   "obsidian_dir": None,
                   "relate": "on", "primary_check": "on", "classify_batch": 40}
OVERRIDES = ("reports_dir", "output_language", "fetch_horizon_days", "scan_depth",
             "adaptive_fetch", "html_reports", "rejections_report", "relate", "primary_check", "classify_batch")
CHOICES = {"triage_backend": ("auto", "api", "session"),
           "judgment_backend": ("auto", "api", "session"),
           "embedder": ("auto", "voyage", "none"),
           "ask_on_disputes": ("off", "on"),
           "scan_depth": ("headlines", "full"),
           "adaptive_fetch": ("off", "on"),
           "html_reports": ("on", "off"),
           "rejections_report": ("off", "on"),
           "relate": ("on", "off"),
           "primary_check": ("on", "off")}
# Renamed once in existing files (migrate). When several old keys map to one new key, the first
# listed here wins; an existing new key beats them all.
RETIRED = {"research_horizon_days": "fetch_horizon_days", "lookback_days": "fetch_horizon_days",
           "themes": "subtopics", "template_themes_declined": "template_subtopics_declined"}


class ConfigError(ValueError):
    """Invalid configuration (a file, a setting or a topic choice); the CLI exits 2."""


def expand_path(raw: str, setting: str) -> Path:
    text = os.path.expanduser(os.path.expandvars(
        re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), raw.strip())))
    path = Path(text)
    if not text or "%" in text or "$" in text or not path.is_absolute():
        raise ConfigError(f"{setting}: '{raw}' is not an absolute path "
                          "(use a full path; ~ and environment variables are expanded)")
    return path


def _horizon(value, where: str) -> int:
    try:
        number = None if isinstance(value, bool) else float(value)
    except (TypeError, ValueError, OverflowError):
        number = None
    if number is None or not number.is_integer() or not 1 <= number <= MAX_HORIZON_DAYS:
        raise ConfigError(f"{where}: field 'fetch_horizon_days' must be a whole number of days "
                          f"between 1 and {MAX_HORIZON_DAYS}, got '{value}'")
    return int(number)


def _classify_batch(value, where: str) -> int:
    number = int(value) if isinstance(value, int) and not isinstance(value, bool) else (
        int(value) if isinstance(value, str) and value.isascii() and value.isdigit() else None)
    if number is None or not 0 <= number <= CLASSIFY_BATCH_MAX:
        raise ConfigError(f"{where}: field 'classify_batch' must be a whole number from 0 to "
                          f"{CLASSIFY_BATCH_MAX} (0 = off), got '{value}'")
    return number


def check_value(key: str, value, where: str):
    if key in CHOICES:
        if value not in CHOICES[key]:
            raise ConfigError(f"{where}: field '{key}' must be one of "
                              f"{', '.join(CHOICES[key])}, got '{value}'")
        return value
    if key == "fetch_horizon_days":
        return _horizon(value, where)
    if key == "classify_batch":
        return _classify_batch(value, where)
    if key == "output_language":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{where}: field 'output_language' must be a language "
                              "name or code, e.g. en")
        return value.strip()
    if key == "reports_dir":
        if not isinstance(value, str):
            raise ConfigError(f"{where}: field 'reports_dir' must be a path")
        expand_path(value, f"{where}: field 'reports_dir'")
        return value
    if key == "obsidian_dir":  # empty = no export; whether the folder exists is checked on set
        if not isinstance(value, str):
            raise ConfigError(f"{where}: field 'obsidian_dir' must be a path or empty")
        if value.strip():
            expand_path(value, f"{where}: field 'obsidian_dir'")
        return value
    raise ConfigError(f"{where}: unknown field '{key}'")


def read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path.name}: cannot read ({exc})") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name}: invalid JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: expected a JSON object")
    return data


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_global(data_dir: Path) -> dict | None:
    path = data_dir / CONFIG_FILE
    if not path.exists():
        return None
    data = read_json_object(path)
    if data.get("version") != VERSION:
        raise ConfigError(f"{CONFIG_FILE}: field 'version' must be {VERSION}")
    values = {}
    for key, value in data.items():
        if key == "version":
            continue
        if key not in GLOBAL_DEFAULTS:
            raise ConfigError(f"{CONFIG_FILE}: unknown field '{key}'")
        values[key] = None if value is None else check_value(key, value, CONFIG_FILE)
    return values


def save_global(data_dir: Path, values: dict) -> None:
    write_json_atomic(data_dir / CONFIG_FILE, {"version": VERSION, **values})


def read_state(data_dir: Path) -> dict:
    try:
        data = json.loads((data_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update_state(data_dir: Path, **changes) -> dict:
    state = {**read_state(data_dir), **changes}
    write_json_atomic(data_dir / STATE_FILE, state)
    return state
