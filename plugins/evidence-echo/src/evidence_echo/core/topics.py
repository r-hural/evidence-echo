"""Topics: <data>/topics/<id>/topic.json, and the templates they start from.

A template lives in <plugin>/templates/<id>.json. Ids are matched against the
existing names before any path is built, so an id can never point elsewhere.
"""
import hashlib
import json as _json
import re
import shutil
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from evidence_echo.core import grading as grading_rules
from evidence_echo.core import settings
from evidence_echo.core.settings import ConfigError

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
TOPIC_FILE = "topic.json"
_TYPE_NAMES = {str: "a string", list: "a list"}
_REQUIRED = (("id", str), ("name", str), ("domain", str), ("scope", str), ("sources", list))
# the Apify actor for linkedin sources when a topic names none (templates no longer carry it)
APIFY_ACTOR = "harvestapi~linkedin-profile-posts"
_OPTIONAL = (("subtopics", list), ("metrics", list), ("linkedin_apify_actor", str), ("template", str))


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    domain: str
    scope: str
    sources: list = field(default_factory=list)
    subtopics: list = field(default_factory=list)
    metrics: list = field(default_factory=list)        # topic.json's list, else the template's (read-time default)
    metrics_in_file: bool = False                      # topic.json has its own `metrics` key
    linkedin_apify_actor: str = APIFY_ACTOR
    template: str = "blank"
    reports_dir: str | None = None
    output_language: str | None = None
    fetch_horizon_days: int | None = None
    scan_depth: str | None = None
    adaptive_fetch: str | None = None
    html_reports: str | None = None
    rejections_report: str | None = None
    relate: str | None = None
    primary_check: str | None = None
    template_subtopics_declined: str | None = None
    classify_batch: int | None = None
    regrade_batch: int | None = None
    grouping_mode: str | None = None
    findings_batch: int | None = None
    grading: dict = field(default_factory=grading_rules.generic)   # effective grading block
    grading_in_file: bool = False                      # topic.json has its own `grading` key
    grading_source: str = "generic"                    # "file" | "template" | "generic"


def _check_sources(sources: list, label: str) -> None:
    from evidence_echo import sources as source_kinds  # kinds import httpx etc.; load on use
    from evidence_echo.sources import base
    seen = set()
    for src in sources:
        if not isinstance(src, dict):
            raise ConfigError(f"{label}: each source must be an object")
        try:
            kind = source_kinds.get_kind(src.get("type"))
        except ConfigError as exc:
            raise ConfigError(f"{label}: {exc}") from None
        problem = kind.FIELDS["name"](src.get("name"))
        if problem:
            raise ConfigError(f"{label}: {kind.TYPE} source {problem}")
        for key, checker in {**kind.FIELDS, **base.COMMON_FIELDS}.items():
            problem = checker(src.get(key))
            if problem:
                raise ConfigError(f"{label}: {kind.TYPE} source '{src['name']}' {problem}")
        if src["name"] in seen:
            raise ConfigError(f"{label}: duplicate source name '{src['name']}'")
        seen.add(src["name"])


def validate(data: dict, label: str, expected_id: str | None = None,
             template: bool = False, check_sources: bool = True) -> Topic:
    known = ({k for k, _ in _REQUIRED + _OPTIONAL} | set(settings.OVERRIDES)
             | {"template_subtopics_declined", "grading"}
             | ({"subtopic_renames", "metric_legacy"} if template else set()))
    for key in data:
        if key not in known:
            raise ConfigError(f"{label}: unknown field '{key}'")
    for key, typ in _REQUIRED:
        if key not in data:
            raise ConfigError(f"{label}: field '{key}' missing")
        if not isinstance(data[key], typ):
            raise ConfigError(f"{label}: field '{key}' must be {_TYPE_NAMES[typ]}")
    for key, typ in _OPTIONAL:
        if key in data and not isinstance(data[key], typ):
            raise ConfigError(f"{label}: field '{key}' must be {_TYPE_NAMES[typ]}")
    declined = data.get("template_subtopics_declined")
    if declined is not None and not isinstance(declined, str):
        raise ConfigError(f"{label}: field 'template_subtopics_declined' must be a string or null")
    renames = data.get("subtopic_renames", {})
    if not (isinstance(renames, dict) and all(
            isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
            for k, v in renames.items())):
        raise ConfigError(f"{label}: field 'subtopic_renames' must map subtopic names to new names")
    if not all(isinstance(t, str) for t in data.get("subtopics", [])):
        raise ConfigError(f"{label}: field 'subtopics' must be a list of strings")
    if not all(isinstance(t, str) for t in data.get("metrics", [])):
        raise ConfigError(f"{label}: field 'metrics' must be a list of strings")
    legacy = data.get("metric_legacy", {})
    if not (isinstance(legacy, dict) and all(isinstance(k, str) and k.strip() and isinstance(v, str)
                                             for k, v in legacy.items())):
        raise ConfigError(f"{label}: field 'metric_legacy' must map old metric codes to metric names")
    if any(v not in data.get("metrics", []) for v in legacy.values()):
        raise ConfigError(f"{label}: every 'metric_legacy' value must be a name in 'metrics'")
    if expected_id is not None and data["id"] != expected_id:
        raise ConfigError(f"{label}: field 'id' is '{data['id']}', "
                          f"must equal the folder name '{expected_id}'")
    if not ID_RE.match(data["id"]):
        raise ConfigError(f"{label}: field 'id' must be lowercase letters, digits and "
                          "hyphens (max 40), starting with a letter or digit")
    if check_sources:
        _check_sources(data["sources"], label)
    overrides = {k: (None if data.get(k) is None else settings.check_value(k, data[k], label))
                 for k in settings.OVERRIDES}
    template_id = data.get("template", "blank")
    metrics = (data["metrics"] if "metrics" in data
               else ([] if template else template_metrics(template_id)[0]))
    block, source = _grading_of(data, label, template, template_id)
    return Topic(id=data["id"], name=data["name"], domain=data["domain"],
                 scope=data["scope"], sources=data["sources"],
                 subtopics=data.get("subtopics", []), metrics=list(metrics),
                 metrics_in_file="metrics" in data,
                 linkedin_apify_actor=data.get("linkedin_apify_actor") or APIFY_ACTOR,
                 template=data.get("template", "blank"),
                 template_subtopics_declined=declined, grading=block,
                 grading_in_file=source == "file" and not template,
                 grading_source=source, **overrides)


def _grading_of(data: dict, label: str, template: bool, template_id: str) -> tuple[dict, str]:
    """(effective block, source): the file's own block, else the template's, else the generic one."""
    raw, source = data.get("grading"), "file"
    if raw is None:
        raw = None if template else template_grading(template_id)
        source = "template" if raw is not None else "generic"
    elif template:
        source = "template"
    errors = grading_rules.rules_errors(raw)
    if errors:
        raise ConfigError(f"{label}: field 'grading': {'; '.join(errors)}")
    return grading_rules.effective(raw), source


def to_dict(topic: Topic) -> dict:
    """The topic as topic.json stores it: `metrics` only when the file has its own list."""
    data = asdict(topic)
    if not data.pop("metrics_in_file"):
        data.pop("metrics")
    if not data.pop("grading_in_file"):
        data.pop("grading")
    data.pop("grading_source")
    return data


def template_ids(templates_dir: Path) -> list[str]:
    return sorted(p.stem for p in templates_dir.glob("*.json"))


def load_template(template_id: str, templates_dir: Path) -> Topic:
    ids = template_ids(templates_dir)
    if template_id not in ids:
        raise ConfigError(f"Unknown template '{template_id}'. Available: {', '.join(ids)}")
    data = settings.read_json_object(templates_dir / f"{template_id}.json")
    data.pop("grouping_mode", None)      # only a suggestion: a topic gets it when the owner accepts it
    return validate({**data, "template": template_id}, f"{template_id}.json", template_id,
                    template=True)


def template_grouping_mode(template_id: str, templates_dir: Path | None = None) -> str | None:
    """The grouping mode a template suggests; None for 'blank', an unknown id or a template without one."""
    if templates_dir is None:
        from evidence_echo.core import config    # config imports topics: load on use
        templates_dir = config.TEMPLATES_DIR
    if template_id not in template_ids(templates_dir):
        return None
    load_template(template_id, templates_dir)    # validates the file first
    value = settings.read_json_object(templates_dir / f"{template_id}.json").get("grouping_mode")
    return settings.check_value("grouping_mode", value, f"{template_id}.json") if value is not None else None


def list_templates(templates_dir: Path) -> list[dict]:
    return [{"id": t.id, "name": t.name, "domain": t.domain,
             "grouping_mode": template_grouping_mode(t.id, templates_dir)}
            for t in (load_template(i, templates_dir) for i in template_ids(templates_dir))]


def template_subtopics(template_id: str, templates_dir: Path) -> tuple[list[str], dict[str, str]] | None:
    """(subtopics, subtopic_renames) of a template; None for 'blank' or an id with no template file."""
    if template_id not in template_ids(templates_dir):
        return None
    load_template(template_id, templates_dir)            # validates the file first
    data = settings.read_json_object(templates_dir / f"{template_id}.json")
    return list(data.get("subtopics", [])), dict(data.get("subtopic_renames", {}))


def template_metrics(template_id: str, templates_dir: Path | None = None) -> tuple[list[str], dict[str, str]]:
    """(metrics, metric_legacy) of a template; ([], {}) for 'blank' or an id with no template file."""
    if templates_dir is None:
        from evidence_echo.core import config    # config imports topics: load on use
        templates_dir = config.TEMPLATES_DIR
    if template_id not in template_ids(templates_dir):
        return [], {}
    data = settings.read_json_object(templates_dir / f"{template_id}.json")
    names, legacy = data.get("metrics", []), data.get("metric_legacy", {})
    if not (isinstance(names, list) and isinstance(legacy, dict)):
        return [], {}
    return [str(n) for n in names], {str(k): str(v) for k, v in legacy.items()}


def template_grading(template_id: str, templates_dir: Path | None = None) -> dict | None:
    """The template file's raw `grading` block; None for 'blank', no block or an unknown id."""
    if templates_dir is None:
        from evidence_echo.core import config    # config imports topics: load on use
        templates_dir = config.TEMPLATES_DIR
    if template_id not in template_ids(templates_dir):
        return None
    block = settings.read_json_object(templates_dir / f"{template_id}.json").get("grading")
    return block if isinstance(block, dict) else None


def subtopic_list_hash(names: list[str], renames: dict) -> str:
    """Identifies a template's subtopic list, so a declined switch is asked again only after it changes."""
    raw = _json.dumps({"subtopics": names, "renames": renames}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def update_topic(topics_dir: Path, topic_id: str, **fields) -> Topic:
    topic = load_topic(topics_dir, topic_id)
    updated = validate({**to_dict(topic), **fields}, f"{topic_id}/{TOPIC_FILE}", topic_id)
    save_topic(topics_dir, updated)
    return updated


def topic_ids(topics_dir: Path) -> list[str]:
    if not topics_dir.is_dir():
        return []
    return sorted(p.name for p in topics_dir.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / TOPIC_FILE).exists())


def load_topic(topics_dir: Path, topic_id: str, check_sources: bool = True) -> Topic:
    ids = topic_ids(topics_dir)
    if topic_id not in ids:
        known = ", ".join(ids) if ids else "none yet"
        raise ConfigError(f"Unknown topic '{topic_id}'. Topics: {known}")
    data = settings.read_json_object(topics_dir / topic_id / TOPIC_FILE)
    return validate(data, f"{topic_id}/{TOPIC_FILE}", topic_id, check_sources=check_sources)


def save_topic(topics_dir: Path, topic: Topic) -> Path:
    path = topics_dir / topic.id / TOPIC_FILE
    settings.write_json_atomic(path, to_dict(topic))
    return path


_SETTABLE = {"name", "domain", "scope", "subtopics", "metrics", "grading", *settings.OVERRIDES}


def _new_topic_dir(topics_dir: Path, topic_id: str) -> None:
    if not ID_RE.match(topic_id):
        raise ConfigError(f"topic id '{topic_id}' must be lowercase letters, digits and "
                          "hyphens (max 40), starting with a letter or digit")
    if (topics_dir / topic_id).exists():
        raise ConfigError(f"topic '{topic_id}' already exists")


def create_from_template(topics_dir, topic_id, template_id, templates_dir) -> Topic:
    _new_topic_dir(topics_dir, topic_id)
    topic = replace(load_template(template_id, templates_dir), id=topic_id)
    save_topic(topics_dir, topic)
    return topic


def create_blank(topics_dir, topic_id, name, domain, scope) -> Topic:
    _new_topic_dir(topics_dir, topic_id)
    topic = validate({"id": topic_id, "name": name, "domain": domain, "scope": scope,
                      "sources": [], "template": "blank"}, f"{topic_id}/{TOPIC_FILE}")
    save_topic(topics_dir, topic)
    return topic


def set_field(topics_dir, topic_id, key, raw_value: str) -> Topic:
    if key == "id":
        raise ConfigError("a topic's id cannot be changed (create a new topic instead)")
    if key not in _SETTABLE:
        raise ConfigError(f"unknown topic setting '{key}'. Settable: {', '.join(sorted(_SETTABLE))}")
    topic = load_topic(topics_dir, topic_id)
    if raw_value == "null" and key in settings.OVERRIDES:
        value = None
    elif key in ("subtopics", "metrics"):
        try:
            value = _json.loads(raw_value)
        except ValueError:
            raise ConfigError(f"{key} must be a JSON list of strings, e.g. [\"A\",\"B\"]") from None
    elif key == "grading":
        if raw_value == "null":
            value = None
        else:
            try:
                value = _json.loads(raw_value)
            except ValueError:
                value = None
            if not isinstance(value, dict):
                raise ConfigError("grading must be a JSON object (or null to remove the topic's own block)")
    else:
        value = raw_value
    data = {**to_dict(topic), key: value}
    if key == "grading" and value is None:
        data.pop("grading")
    updated = validate(data, f"{topic_id}/{TOPIC_FILE}", topic_id)
    save_topic(topics_dir, updated)
    return updated


def remove(topics_dir, topic_id, delete_data: bool) -> dict:
    load_topic(topics_dir, topic_id)  # unknown id -> error with the list
    folder = topics_dir / topic_id
    if delete_data:
        shutil.rmtree(folder)
        return {"removed": topic_id, "deleted": True}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    target = topics_dir / f".removed-{topic_id}-{stamp}"
    folder.rename(target)
    return {"removed": topic_id, "moved_to": target.name}


def add_source(topics_dir, topic_id, source: dict) -> Topic:
    topic = load_topic(topics_dir, topic_id)
    updated = validate({**to_dict(topic), "sources": topic.sources + [source]},
                       f"{topic_id}/{TOPIC_FILE}", topic_id)
    save_topic(topics_dir, updated)
    return updated


SOURCE_OPTIONS = ("scan_depth", "fetch_every", "site_url")


def set_source_option(topics_dir, topic_id, name, key, value) -> Topic:
    if key not in SOURCE_OPTIONS:
        raise ConfigError(f"unknown source option '{key}'. Settable: {', '.join(SOURCE_OPTIONS)}")
    topic = load_topic(topics_dir, topic_id)
    if not any(s["name"] == name for s in topic.sources):
        raise ConfigError(f"no source named '{name}' in topic '{topic_id}'")
    sources = []
    for s in topic.sources:
        s = dict(s)
        if s["name"] == name:
            s.pop(key, None)
            if value is not None:
                s[key] = value
        sources.append(s)
    updated = validate({**to_dict(topic), "sources": sources}, f"{topic_id}/{TOPIC_FILE}", topic_id)
    save_topic(topics_dir, updated)
    return updated


def remove_source(topics_dir, topic_id, name) -> Topic:
    topic = load_topic(topics_dir, topic_id)
    kept = [s for s in topic.sources if s["name"] != name]
    if len(kept) == len(topic.sources):
        raise ConfigError(f"no source named '{name}' in topic '{topic_id}'")
    updated = replace(topic, sources=kept)
    save_topic(topics_dir, updated)
    return updated
