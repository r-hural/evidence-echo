"""Stage result contracts: field schemas for LLM results, and payload checks.

Session payloads and API-mode LLM responses are untrusted. Every result is
validated here before it reaches the store: a malformed payload raises
ContractError; a bad item is rejected on its own and keeps its state.
"""
import json
import re
from dataclasses import dataclass

from evidence_echo.core import grading, interfaces, knowledge
from evidence_echo.core.store import SUBTOPIC_NAME_MAX   # one definition (store; it imports no contracts)

TIERS = grading.TIERS
RELATIONS = ("vendor", "independent", "agency", "platform", "unknown")
OPAQUE_ID = re.compile(r"i[0-9]+")


class ContractError(ValueError):
    """The payload as a whole is unusable (not JSON, or no "results" list)."""


@dataclass(frozen=True)
class Field:
    name: str
    type: type
    required: bool = True
    choices: tuple | None = None
    nullable: bool = False
    nested: list | None = None


_TYPE_NAMES = {str: "string", bool: "bool", int: "int", dict: "object", list: "list"}
_JSON_TYPES = {str: "string", bool: "boolean", int: "integer", dict: "object", list: "array"}

FACTS = [
    Field("what_was_tested", str),
    Field("findings", str),
    Field("research_timeframe", str),
    Field("sample_scale", str, required=False, nullable=True),
    Field("duration", str, required=False, nullable=True),
    Field("has_controls", bool, required=False, nullable=True),
    Field("methodology_stated", bool, required=False, nullable=True),
    Field("match_keys", dict, required=False, nullable=True),
    Field("series", str, required=False, nullable=True),
    Field("editions", list, required=False, nullable=True),
    Field("metric", str, required=False, nullable=True),
    Field("baseline", str, required=False, nullable=True),
]
TRIAGE = [Field("id", str), Field("pass", bool), Field("reason", str)]
PROVENANCE = [
    Field("conducted_by", str, required=False, nullable=True),
    Field("relation", str, required=False, choices=RELATIONS, nullable=True),
    Field("conflict_of_interest", str, required=False, nullable=True),
]
JUDGE = [
    Field("id", str),
    Field("pass", bool),
    Field("tier", str, required=False, choices=TIERS, nullable=True),
    Field("facts", dict, required=False, nullable=True, nested=FACTS),
    Field("primary_url", str, required=False, nullable=True),
    Field("quotes", list, required=False, nullable=True),
    Field("provenance", dict, required=False, nullable=True, nested=PROVENANCE),
    Field("subtopic", dict, required=False, nullable=True),
    Field("metric", str, required=False, nullable=True),     # a slug of the topic's metric list
    Field("entities", list, required=False, nullable=True),  # names; the judge stage requires it when pass
    Field("data_date", str, required=False, nullable=True),
    Field("headline", dict, required=False, nullable=True),
    Field("reason", str, required=False, nullable=True),
]
VERIFY = [  # blind skeptic: its own tier, no view of the judge's
    Field("id", str),
    Field("tier", str, choices=TIERS),
    Field("red_flags", list, required=False, nullable=True),
    Field("note", str),
]
RULING = [  # owner adjudication (apply --stage verify --ruling): the pre-SP2 verify shape
    Field("id", str),
    Field("agrees", bool),
    Field("adjusted_tier", str, required=False, choices=TIERS, nullable=True),
    Field("note", str),
]
REPORT = [Field("study_id", int), Field("title", str), Field("summary", str)]
MATCH = [Field("id", str), Field("same_as", str, nullable=True), Field("reason", str)]
RELATE = [Field("id", str), Field("summary", str), Field("studies", list)]

PRIMARY = [  # SP3b-P: one comparer answer (two blind answers per item are compared in code)
    Field("id", str),
    Field("verdict", str, choices=tuple(knowledge.PRIMARY_VERDICTS)),
    Field("quote_article", str),
    Field("quote_primary", str),
    Field("note", str),
]

CLASSIFY = [  # wave 4a: the judge's knowledge fields for an older study (id "s<study id>")
    Field("id", str),
    Field("subtopic", dict, required=False, nullable=True),
    Field("metric", str),
    Field("entities", list),
    Field("data_date", str, required=False, nullable=True),
    Field("headline", dict, required=False, nullable=True),
]

GROUP = [  # questions: a study's question (an existing "q<id>" or a new_question key) and direction
    Field("id", str),
    Field("question", str, required=False, nullable=True),
    Field("new_question", dict, required=False, nullable=True),
    Field("question_kind", dict, required=False, nullable=True),
    Field("direction", str, choices=knowledge.QUESTION_DIRECTIONS, required=False, nullable=True),
]

SPLIT = [Field("id", str), Field("groups", list)]   # one answer per target id; the groups are checked in stages/split

FETCH = [Field("id", str),
         Field("text", str, required=False),
         Field("links", list, required=False)]

SCHEMAS = {"triage": TRIAGE, "judge": JUDGE, "verify": VERIFY, "ruling": RULING, "report": REPORT, "fetch": FETCH,
           "match": MATCH, "relate": RELATE, "primary": PRIMARY, "classify": CLASSIFY, "group": GROUP, "split": SPLIT}
ID_KEYS = {"triage": "id", "judge": "id", "verify": "id", "ruling": "id", "report": "study_id", "fetch": "id",
           "match": "id", "relate": "id", "primary": "id", "classify": "id", "group": "id", "split": "id"}
ID_PATTERNS = {"fetch": re.compile(r"[is][0-9]+"),  # i<item rowid>, s<source rowid>
               "classify": re.compile(r"s[0-9]+"),  # s<study id>
               "group": re.compile(r"s[0-9]+"),     # s<study id>
               "split": re.compile(r"subtopic:\S+|metric:other"),
               "relate": re.compile(r"q[0-9]+")}  # q<question id>


def _is_type(value, typ) -> bool:
    if typ is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, typ)


def validate(obj, schema: list[Field], prefix: str = "") -> list[str]:
    if not isinstance(obj, dict):
        return [f"{prefix[:-1] or 'result'}: expected object"]
    errors = []
    for f in schema:
        key = prefix + f.name
        if f.name not in obj:
            if f.required:
                errors.append(f"{key}: missing")
            continue
        value = obj[f.name]
        if value is None:
            if not f.nullable:
                errors.append(f"{key}: expected {_TYPE_NAMES[f.type]}, got null")
            continue
        if not _is_type(value, f.type):
            errors.append(f"{key}: expected {_TYPE_NAMES[f.type]}")
            continue
        if f.choices is not None and value not in f.choices:
            errors.append(f"{key}: {value!r} not in {'|'.join(f.choices)}")
        if f.nested is not None:
            errors.extend(validate(value, f.nested, key + "."))
    return errors


def _subtopic_rule(subtopic) -> list[str]:
    """A subtopic answer is {"slug": s} or {"propose": name}; a name needs a letter or digit."""
    if subtopic is None:
        return []
    if set(subtopic) not in ({"slug"}, {"propose"}):
        return ['subtopic: must be {"slug": "..."} or {"propose": "<name>"}']
    value = subtopic.get("slug", subtopic.get("propose"))
    if not isinstance(value, str) or not value.strip():
        return ["subtopic: the slug or name must be a non-empty string"]
    if "propose" in subtopic and (len(" ".join(value.split())) > SUBTOPIC_NAME_MAX
                               or not re.search(r"[^\W_]", value)):
        return [f"subtopic: a proposed name needs a letter or digit and at most {SUBTOPIC_NAME_MAX} characters"]
    return []


def headline_rule(headline) -> list[str]:
    if headline is None:
        return []
    if not isinstance(headline, dict) or set(headline) != {"value", "label", "quote"}:
        return ["headline: must have value, label and quote"]
    if not all(isinstance(v, str) and v.strip() for v in headline.values()):
        return ["headline: value, label and quote must be non-empty strings"]
    if len(headline["label"].split()) > grading.HEADLINE_LABEL_MAX_WORDS:
        return [f"headline: label has more than {grading.HEADLINE_LABEL_MAX_WORDS} words"]
    return []


def _judge_rules(obj: dict) -> list[str]:
    errors = []
    quotes = obj.get("quotes")
    if isinstance(quotes, list) and not all(
            isinstance(q, dict) and isinstance(q.get("number"), str) and isinstance(q.get("quote"), str)
            for q in quotes):
        errors.append("quotes: each quote must be an object with number and quote strings")
    if obj.get("pass") is not True:
        reason = obj.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("reason: required when pass is false")
        return errors
    if obj.get("tier") is None:
        errors.append("tier: required when pass is true")
    if not obj.get("facts"):
        errors.append("facts: required when pass is true")
    errors += _subtopic_rule(obj.get("subtopic"))
    if obj.get("metric") is None:
        errors.append("metric: required when pass is true")
    errors += headline_rule(obj.get("headline"))
    return errors


def _classify_rules(obj: dict) -> list[str]:
    allowed = {f.name for f in CLASSIFY}
    for key in obj:
        if key not in allowed:
            return [f"classify: unexpected field '{key}' (only subtopic, metric, entities, data_date, headline)"]
    return _subtopic_rule(obj.get("subtopic")) + headline_rule(obj.get("headline"))


def _kind_errors(prefix: str, kind, comparison) -> list[str]:
    """A question's kind and comparison: the kind is one of the three, difference needs a comparison
    within the limit, the other kinds take none."""
    if kind not in knowledge.QUESTION_KINDS:
        return [f"{prefix}kind: {kind!r} not in {'|'.join(knowledge.QUESTION_KINDS)}"]
    clean = " ".join(comparison.split()) if isinstance(comparison, str) else ""
    if kind != "difference":
        return [f"{prefix}comparison: a {kind} question has none"] if comparison else []
    if not clean:
        return [f"{prefix}comparison: required for a difference question"]
    if len(clean) > knowledge.QUESTION_COMPARISON_MAX:
        return [f"{prefix}comparison: {len(clean)} characters; the limit is {knowledge.QUESTION_COMPARISON_MAX}"]
    return []


def _group_rules(obj: dict) -> list[str]:
    """One of question / new_question; a new question needs text, measure and kind (+comparison for
    difference) within the limits; question_kind is {kind, comparison}; a new question's direction
    follows its kind."""
    question, new = obj.get("question"), obj.get("new_question")
    if (question is None) == (new is None):
        return ["exactly one of question or new_question is required"]
    if question is not None and not question.strip():
        return ["question: must not be empty"]
    errors = []
    given = obj.get("question_kind")
    if given is not None:
        if set(given) - {"kind", "comparison"} or "kind" not in given:
            errors.append("question_kind: must be {\"kind\": ..., \"comparison\": ...}")
        else:
            errors += _kind_errors("question_kind.", given["kind"], given.get("comparison"))
        if new is not None:
            errors.append("question_kind: only with an existing question")
    if new is None:
        return errors
    allowed = {"key", "text", "measure", "kind", "comparison"}
    if set(new) - allowed:
        return [f"new_question: unexpected field '{sorted(set(new) - allowed)[0]}' (only {', '.join(sorted(allowed))})"]
    for name, limit in (("text", knowledge.QUESTION_TEXT_MAX), ("measure", knowledge.QUESTION_MEASURE_MAX)):
        value = new.get(name)
        clean = " ".join(value.split()) if isinstance(value, str) else ""
        if not clean:
            errors.append(f"new_question.{name}: required, a non-empty string")
        elif len(clean) > limit:
            errors.append(f"new_question.{name}: {len(clean)} characters; the limit is {limit}")
    if "key" in new and (not isinstance(new["key"], str) or not new["key"].strip()):
        errors.append("new_question.key: must be a non-empty string")
    kind_errors = _kind_errors("new_question.", new.get("kind"), new.get("comparison"))
    errors += kind_errors
    if not kind_errors:
        if new["kind"] == "difference" and obj.get("direction") is None:
            errors.append("direction: required for a difference question")
        elif new["kind"] != "difference" and obj.get("direction") is not None:
            errors.append(f"direction: a {new['kind']} question has none; leave it out")
    return errors


def _split_rules(obj: dict) -> list[str]:
    """Each group is an object with name xor into, and lists of question / study ids (strings)."""
    errors = []
    for number, group in enumerate(obj["groups"], 1):
        label = f"groups[{number}]"
        if not isinstance(group, dict):
            errors.append(f"{label}: expected object")
            continue
        for key in group:
            if key not in ("name", "into", "questions", "studies"):
                errors.append(f"{label}: unexpected field '{key}' (only name, into, questions, studies)")
        if ("name" in group) == ("into" in group):
            errors.append(f"{label}: exactly one of name or into is required")
        for key in ("name", "into"):
            if key in group and not (isinstance(group[key], str) and group[key].strip()):
                errors.append(f"{label}.{key}: must be a non-empty string")
        for key in ("questions", "studies"):
            value = group.get(key)
            if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                errors.append(f"{label}.{key}: must be a list of id strings")
    return errors


def _verify_rules(obj: dict) -> list[str]:
    unknown = [f for f in obj.get("red_flags") or [] if f not in grading.RED_FLAGS]
    if unknown:
        return [f"red_flags: unknown code {unknown[0]!r} (allowed: {', '.join(grading.RED_FLAGS)})"]
    return []


def _relate_rules(obj: dict) -> list[str]:
    errors = [] if obj["summary"].strip() else ["summary: must not be blank"]
    for n, line in enumerate(obj["studies"]):
        if not isinstance(line, dict):
            errors.append(f"studies[{n}]: expected object")
            continue
        if not isinstance(line.get("id"), str):
            errors.append(f"studies[{n}].id: expected string")
        if line.get("reason") not in knowledge.PAIR_REASONS:
            errors.append(f"studies[{n}].reason: {line.get('reason')!r} not in {list(knowledge.PAIR_REASONS)}")
        for key in ("quote", "note"):
            if not isinstance(line.get(key), str):
                errors.append(f"studies[{n}].{key}: expected string")
            elif not line[key].strip():
                errors.append(f"studies[{n}].{key}: must not be blank")
    return errors


def _fetch_rules(obj: dict) -> list[str]:
    if ("text" in obj) == ("links" in obj):
        return ["exactly one of text or links is required"]
    if "links" in obj and not all(isinstance(l, dict) and isinstance(l.get("url"), str)
                                  for l in obj["links"]):
        return ["links: each link must be an object with a url string"]
    return []


def _schema(stage: str) -> list[Field]:
    if stage not in SCHEMAS:
        raise ValueError(f"Unknown stage '{stage}'. Choose from: {', '.join(SCHEMAS)}")
    return SCHEMAS[stage]


def validate_result(stage: str, obj) -> list[str]:
    errors = validate(obj, _schema(stage))
    if not errors and stage == "judge":
        errors = _judge_rules(obj)
    if not errors and stage == "classify":
        errors = _classify_rules(obj)
    if not errors and stage == "group":
        errors = _group_rules(obj)
    if not errors and stage == "split":
        errors = _split_rules(obj)
    if not errors and stage == "verify":
        errors = _verify_rules(obj)
    if not errors and stage == "fetch":
        errors = _fetch_rules(obj)
    if not errors and stage == "relate":
        errors = _relate_rules(obj)
    return errors


def parse_payload(payload: str) -> list:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ContractError(f"results payload is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ContractError('results payload must be a JSON object with a "results" list')
    return data["results"]


def check_results(stage: str, results: list, expected_ids: set) -> tuple[list[dict], list[dict]]:
    key = ID_KEYS[stage]
    accepted, rejected, seen = [], [], set()
    for index, res in enumerate(results):
        rid = res.get(key) if isinstance(res, dict) else None
        errors = validate_result(stage, res)
        if not errors:
            if key == "id" and not ID_PATTERNS.get(stage, OPAQUE_ID).fullmatch(rid):
                errors = [f"id: malformed {rid!r}"]
            elif rid not in expected_ids:
                errors = [f"{key}: {rid!r} is not awaiting {stage}"]
            elif rid in seen:
                errors = [f"{key}: duplicate {rid!r} (first copy applied)"]
        if errors:
            rejected.append({"id": f"#{index}" if rid is None else str(rid),
                             "errors": errors})
        else:
            seen.add(rid)
            accepted.append(res)
    return accepted, rejected


def check_payload(stage: str, payload: str, expected_ids: set) -> tuple[list[dict], list[dict]]:
    return check_results(stage, parse_payload(payload), expected_ids)


def ask_llm(backend, stage: str, item_id, prompt: str,
            max_tokens: int) -> tuple[dict | None, list[str]]:
    """One API-mode call: errors of any kind become a rejection, never an exception."""
    try:
        raw = backend.complete(prompt, max_tokens=max_tokens)
    except Exception as exc:  # network, rate limit, auth: the item stays queued
        return None, [f"backend error: {exc}"]
    if not isinstance(raw, str):
        return None, ["response is not text"]
    try:
        obj = interfaces.parse_llm_json(raw)
    except ValueError as exc:
        return None, [f"response is not valid JSON: {exc}"]
    if not isinstance(obj, dict):
        return None, ["result: expected object"]
    obj = {**obj, ID_KEYS[stage]: item_id}
    errors = validate_result(stage, obj)
    return (None, errors) if errors else (obj, [])


def _object_schema(schema: list[Field]) -> dict:
    props, required = {}, []
    for f in schema:
        json_type = _JSON_TYPES[f.type]
        prop = {"type": [json_type, "null"] if f.nullable else json_type}
        if f.choices is not None:
            prop["enum"] = list(f.choices) + ([None] if f.nullable else [])
        if f.nested is not None:
            nested = _object_schema(f.nested)
            prop["properties"] = nested["properties"]
            prop["required"] = nested["required"]
        props[f.name] = prop
        if f.required:
            required.append(f.name)
    return {"type": "object", "properties": props, "required": required}


def export_json_schema(stage: str) -> dict:
    items = _object_schema(_schema(stage))
    if stage == "judge":
        items["if"] = {"properties": {"pass": {"const": True}}, "required": ["pass"]}
        items["then"] = {"required": ["tier", "facts", "metric", "entities"],
                         "properties": {"tier": {"type": "string"},
                                        "facts": {"type": "object"}}}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"Evidence Echo {stage} results",
        "type": "object",
        "required": ["results"],
        "properties": {"results": {"type": "array", "items": items}},
    }
