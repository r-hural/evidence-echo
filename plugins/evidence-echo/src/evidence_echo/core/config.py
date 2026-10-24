# plugins/evidence-echo/src/evidence_echo/core/config.py
"""Configuration: the workspace (data folder, topics, config.json) and the
effective settings for one topic.

Data folder: EV_ECHO_DATA_DIR > ~/.evidence-echo (CLAUDE_PLUGIN_DATA is deleted on
uninstall, so it only holds disposable things: keys.json and venvs; an
EV_ECHO_DATA_DIR inside it is ignored).
Settings: EV_ECHO_<NAME> > topic.json override > config.json > default.
"""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from evidence_echo.core import migrate, settings, topics
from evidence_echo.core.settings import ConfigError
from evidence_echo.core.topics import Topic

PLUGIN_ROOT = Path(__file__).resolve().parents[3]  # plugins/evidence-echo/ (core -> evidence_echo -> src -> root)
TEMPLATES_DIR = PLUGIN_ROOT / "templates"
HOME_DATA_DIR = Path.home() / ".evidence-echo"
KEYS_FILE = "keys.json"  # written by hooks/session_env.py into the plugin data dir
NOT_SET_UP = "Evidence Echo is not set up — run the setup (/evidence-echo:setup)"
NO_TOPICS = "no topics yet — run the setup (/evidence-echo:setup)"


class SetupRequired(Exception):
    """No config.json or no topic yet; the CLI exits 4 and the skill runs the setup."""


@dataclass(frozen=True)
class Workspace:
    data_dir: Path
    plugin_data_dir: Path
    topics_dir: Path
    settings: dict | None

    @property
    def configured(self) -> bool:
        return self.settings is not None


@dataclass(frozen=True)
class Config:
    data_dir: Path
    plugin_data_dir: Path
    topic: Topic
    topic_dir: Path
    db_path: Path
    reports_dir: Path
    overview_dir: Path
    triage_backend: str
    judgment_backend: str
    embedder: str
    output_language: str
    fetch_horizon_days: int
    scan_depth: str
    adaptive_fetch: str
    anthropic_api_key: str
    voyage_api_key: str
    apify_api_token: str
    html_reports: str
    rejections_report: str
    obsidian_dir: Path | None
    relate: str
    primary_check: str
    classify_batch: int = 40
    fetch_cache_minutes: int = 60
    md_dir: Path | None = None   # where the Markdown report goes; None: reports_dir


def _env(var: str) -> str:
    return os.environ.get(var, "").strip()


def _plugin_data_roots() -> list[Path]:
    """Folders Claude Code deletes on uninstall: never a data folder."""
    claude_dir = Path(_env("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    roots = [Path(_env(v)) for v in ("EV_ECHO_PLUGIN_DATA", "CLAUDE_PLUGIN_DATA") if _env(v)]
    return roots + [claude_dir / "plugins" / "data"]


def _inside(path: Path, root: Path) -> bool:
    path, root = (Path(os.path.normcase(p.resolve())) for p in (path, root))
    return path == root or root in path.parents


def _data_dir() -> Path:
    raw = _env("EV_ECHO_DATA_DIR")
    if not raw:
        return HOME_DATA_DIR
    chosen = settings.expand_path(raw, "EV_ECHO_DATA_DIR")
    if any(_inside(chosen, root) for root in _plugin_data_roots()):
        # 0.4 exported the plugin data folder under this name; a session started
        # under 0.4 still carries it
        print(f"evidence-echo: EV_ECHO_DATA_DIR={chosen} is inside the plugin data folder "
              f"(deleted on uninstall) and is ignored; using {HOME_DATA_DIR}. "
              "Start a new Claude Code session to drop it.", file=sys.stderr)
        return HOME_DATA_DIR
    return chosen


def _plugin_data_dir(data_dir: Path) -> Path:
    for var in ("EV_ECHO_PLUGIN_DATA", "CLAUDE_PLUGIN_DATA"):
        if _env(var):
            return Path(_env(var))
    return data_dir / "cache"


def workspace_lenient() -> tuple[Workspace, str | None]:
    """Like workspace(), but an invalid config.json is reported instead of raised."""
    try:
        return workspace(), None
    except ConfigError as exc:
        data_dir = _data_dir()
        return Workspace(data_dir, _plugin_data_dir(data_dir), data_dir / "topics", None), str(exc)


def workspace() -> Workspace:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    plugin_data = _plugin_data_dir(data_dir)
    for message in migrate.run(data_dir, plugin_data, HOME_DATA_DIR, TEMPLATES_DIR):
        print(message, file=sys.stderr)
    return Workspace(data_dir=data_dir, plugin_data_dir=plugin_data,
                     topics_dir=data_dir / "topics",
                     settings=settings.load_global(data_dir))


def select_topic(ws: Workspace, requested: str | None) -> str:
    wanted = requested or _env("EV_ECHO_TOPIC")
    if wanted:
        return wanted  # load_topic reports an unknown id with the list
    ids = topics.topic_ids(ws.topics_dir)
    if not ids:
        raise SetupRequired(NO_TOPICS)
    if len(ids) > 1:
        raise ConfigError(f"several topics — pass --topic <id>: {', '.join(ids)}")
    return ids[0]


KEY_NAMES = ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "APIFY_API_TOKEN")


def _read_keys(plugin_data: Path) -> dict:
    """Keys the SessionStart hook wrote from the plugin settings (never exported)."""
    path = plugin_data / KEYS_FILE
    if not path.exists():
        return {}
    try:
        keys = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON ({exc.msg}); re-save the plugin's "
                         "key settings or delete the file") from None
    if not isinstance(keys, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return {k: v for k, v in keys.items() if isinstance(v, str)}


def _key(name: str, keys: dict) -> str:
    """EV_ECHO_<name> (explicit) > plugin settings (keys file) > plain <name> (shell)."""
    return _env("EV_ECHO_" + name) or keys.get(name, "").strip() or _env(name)


def _setting(name: str, ws: Workspace, topic: Topic | None):
    env = _env("EV_ECHO_" + name.upper())
    if env and env != "auto":
        return settings.check_value(name, env, f"EV_ECHO_{name.upper()}")
    if topic is not None and name in settings.OVERRIDES and getattr(topic, name) is not None:
        return getattr(topic, name)
    value = (ws.settings or {}).get(name)
    return settings.GLOBAL_DEFAULTS[name] if value is None else value


def workspace_setting(name: str, ws: Workspace):
    """A setting outside any topic (ev overview): EV_ECHO_<NAME> > config.json > default."""
    return _setting(name, ws, None)


def markdown_dir(ws: Workspace, visible: Path, html_reports: str, topic_id: str | None = None) -> Path:
    """Where Markdown files go: `visible` (the reports folder), or <data>/markdown[/<topic>] when
    md_reports is off and HTML is on (with HTML off too, the Markdown stays the visible report)."""
    if workspace_setting("md_reports", ws) == "on" or html_reports == "off":
        return visible
    hidden = ws.data_dir / "markdown"
    return hidden / topic_id if topic_id else hidden


def _obsidian_dir(ws: Workspace) -> Path | None:
    """EV_ECHO_OBSIDIAN_DIR > config.json; empty means no export. Existence is checked on use."""
    env = _env("EV_ECHO_OBSIDIAN_DIR")
    if env:
        return settings.expand_path(env, "EV_ECHO_OBSIDIAN_DIR")
    raw = (ws.settings or {}).get("obsidian_dir") or ""
    return settings.expand_path(raw, "obsidian_dir") if raw.strip() else None


def _reports_dirs(ws: Workspace, topic: Topic) -> tuple[Path, Path]:
    env = _env("EV_ECHO_REPORTS_DIR")  # a base folder; outranks the topic's own folder
    if env:
        base = settings.expand_path(env, "EV_ECHO_REPORTS_DIR")
        return base / topic.id, base
    base_raw = (ws.settings or {}).get("reports_dir")
    base = (settings.expand_path(base_raw, "reports_dir") if base_raw
            else ws.data_dir / "reports")
    if topic.reports_dir:
        return settings.expand_path(topic.reports_dir, f"{topic.id}: reports_dir"), base
    return base / topic.id, base


def load(topic_id: str | None = None) -> Config:
    ws = workspace()
    if not ws.configured:
        raise SetupRequired(NOT_SET_UP)
    topic = topics.load_topic(ws.topics_dir, select_topic(ws, topic_id))
    reports_dir, overview_dir = _reports_dirs(ws, topic)
    keys = _read_keys(ws.plugin_data_dir)
    anthropic_key = _key("ANTHROPIC_API_KEY", keys)
    voyage_key = _key("VOYAGE_API_KEY", keys)
    llm_default = "api" if anthropic_key else "session"

    def choice(name, auto):
        value = _setting(name, ws, topic)
        return auto if value == "auto" else value

    topic_dir = ws.topics_dir / topic.id
    html_reports = _setting("html_reports", ws, topic)
    return Config(
        data_dir=ws.data_dir, plugin_data_dir=ws.plugin_data_dir, topic=topic,
        topic_dir=topic_dir, db_path=topic_dir / "echo.db",
        reports_dir=reports_dir, overview_dir=overview_dir,
        triage_backend=choice("triage_backend", llm_default),
        judgment_backend=choice("judgment_backend", llm_default),
        embedder=choice("embedder", "voyage" if voyage_key else "none"),
        output_language=_setting("output_language", ws, topic),
        fetch_horizon_days=_setting("fetch_horizon_days", ws, topic),
        scan_depth=_setting("scan_depth", ws, topic),
        adaptive_fetch=_setting("adaptive_fetch", ws, topic),
        anthropic_api_key=anthropic_key, voyage_api_key=voyage_key,
        apify_api_token=_key("APIFY_API_TOKEN", keys),
        html_reports=html_reports,
        rejections_report=_setting("rejections_report", ws, topic),
        obsidian_dir=_obsidian_dir(ws),
        relate=_setting("relate", ws, topic),
        primary_check=_setting("primary_check", ws, topic),
        classify_batch=_setting("classify_batch", ws, topic),
        fetch_cache_minutes=_setting("fetch_cache_minutes", ws, topic),
        md_dir=markdown_dir(ws, reports_dir, html_reports, topic.id),
    )
