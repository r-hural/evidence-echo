import json

import pytest

from evidence_echo.core import contracts
from evidence_echo.core.contracts import ContractError, Field, validate, validate_result
from tests.conftest import FakeBackend

FACTS = {"what_was_tested": "Title tags", "findings": "+4.2% clicks",
         "research_timeframe": "2026-Q2"}
VALID = {
    "triage": {"id": "i1", "pass": True, "reason": "numbers"},
    "judge": {"id": "i1", "pass": True, "tier": "A", "facts": FACTS, "primary_url": None,
              "metric": "clicks"},
    "verify": {"id": "i1", "tier": "C", "red_flags": ["no_baseline"], "note": "no control"},
    "ruling": {"id": "i1", "agrees": False, "adjusted_tier": "C", "note": "no control"},
    "report": {"study_id": 3, "title": "T", "summary": "S"},
}


def _without(d, key):
    return {k: v for k, v in d.items() if k != key}


@pytest.mark.parametrize("stage", sorted(VALID))
def test_valid_result_accepted(stage):
    assert validate_result(stage, VALID[stage]) == []


def test_judge_fail_needs_no_tier_or_facts():
    assert validate_result("judge", {"id": "i1", "pass": False, "tier": None, "reason": "Opinion piece."}) == []


def test_judge_fail_requires_reason():
    assert "reason: required when pass is false" in validate_result("judge", {"id": "i1", "pass": False})


def test_judge_fail_blank_reason_rejected():
    errors = validate_result("judge", {"id": "i1", "pass": False, "reason": "  "})
    assert "reason: required when pass is false" in errors


def test_omitted_optional_tiers_are_accepted():
    # models drop null keys; a rejection here would stall the item forever in API mode
    assert validate_result("judge", {"id": "i1", "pass": False, "reason": "Opinion piece."}) == []
    assert validate_result("ruling", {"id": "i1", "agrees": True, "note": "ok"}) == []


def test_judge_optional_facts_accept_null():
    facts = dict(FACTS, sample_scale=None, duration=None, has_controls=None,
                 methodology_stated=True, match_keys={"key_numbers": []})
    assert validate_result("judge", dict(VALID["judge"], facts=facts)) == []


BAD = [
    ("triage", dict(VALID["triage"], **{"pass": "yes"}), "pass: expected bool"),
    ("triage", _without(VALID["triage"], "reason"), "reason: missing"),
    ("triage", dict(VALID["triage"], id=7), "id: expected string"),
    ("judge", dict(VALID["judge"], tier="A+"), "tier: 'A+' not in A|B|C|D"),
    ("judge", _without(VALID["judge"], "tier"), "tier: required when pass is true"),
    ("judge", dict(VALID["judge"], tier=None), "tier: required when pass is true"),
    ("judge", _without(VALID["judge"], "facts"), "facts: required when pass is true"),
    ("judge", dict(VALID["judge"], facts=_without(FACTS, "findings")), "facts.findings: missing"),
    ("judge", dict(VALID["judge"], facts=dict(FACTS, has_controls="yes")),
     "facts.has_controls: expected bool"),
    ("judge", dict(VALID["judge"], facts="Title tags"), "facts: expected object"),
    ("judge", dict(VALID["judge"], primary_url=5), "primary_url: expected string"),
    ("ruling", _without(VALID["ruling"], "agrees"), "agrees: missing"),
    ("ruling", dict(VALID["ruling"], adjusted_tier="E"), "adjusted_tier: 'E' not in A|B|C|D"),
    ("ruling", dict(VALID["ruling"], note=None), "note: expected string, got null"),
    ("verify", _without(VALID["verify"], "tier"), "tier: missing"),
    ("verify", dict(VALID["verify"], note=None), "note: expected string, got null"),
    ("report", dict(VALID["report"], study_id="3"), "study_id: expected int"),
    ("report", dict(VALID["report"], study_id=True), "study_id: expected int"),
    ("report", _without(VALID["report"], "summary"), "summary: missing"),
]


@pytest.mark.parametrize("stage,item,error", BAD)
def test_bad_field_rejected(stage, item, error):
    assert error in validate_result(stage, item)


@pytest.mark.parametrize("item", ["text", None, ["id", "i1"], 3])
def test_non_object_result_rejected(item):
    assert validate_result("triage", item) == ["result: expected object"]


def test_validate_nested_prefix():
    schema = [Field("a", dict, nested=[Field("b", str)])]
    assert validate({"a": {"b": 1}}, schema) == ["a.b: expected string"]


def test_contract_error_is_value_error():
    assert issubclass(ContractError, ValueError)


@pytest.mark.parametrize("payload,msg", [
    ("not json", "not valid JSON"),
    ("[]", '"results" list'),
    ('{"results": {}}', '"results" list'),
    ('{"items": []}', '"results" list'),
])
def test_parse_payload_rejects_malformed(payload, msg):
    with pytest.raises(ContractError, match=msg):
        contracts.parse_payload(payload)


def test_parse_payload_returns_results():
    assert contracts.parse_payload('{"results": [1, 2]}') == [1, 2]


def test_check_results_sorts_items():
    results = [
        VALID["triage"],                                      # i1 ok
        dict(VALID["triage"], id="i2", **{"pass": "no"}),     # schema error
        dict(VALID["triage"], reason="again"),                # duplicate i1
        dict(VALID["triage"], id="i9"),                       # not awaiting
        dict(VALID["triage"], id="item1"),                    # malformed id
        "junk",                                               # not an object
    ]
    accepted, rejected = contracts.check_results("triage", results, {"i1", "i2"})
    assert accepted == [VALID["triage"]]
    assert rejected == [
        {"id": "i2", "errors": ["pass: expected bool"]},
        {"id": "i1", "errors": ["id: duplicate 'i1' (first copy applied)"]},
        {"id": "i9", "errors": ["id: 'i9' is not awaiting triage"]},
        {"id": "item1", "errors": ["id: malformed 'item1'"]},
        {"id": "#5", "errors": ["result: expected object"]},
    ]


def test_check_results_report_uses_study_id():
    accepted, rejected = contracts.check_results(
        "report", [VALID["report"], dict(VALID["report"], study_id=4)], {3})
    assert accepted == [VALID["report"]]
    assert rejected == [{"id": "4", "errors": ["study_id: 4 is not awaiting report"]}]


def test_check_payload_parses_then_checks():
    accepted, rejected = contracts.check_payload(
        "triage", json.dumps({"results": [VALID["triage"]]}), {"i1"})
    assert accepted == [VALID["triage"]] and rejected == []


def test_ask_llm_accepts_fenced_json_and_injects_id():
    backend = FakeBackend(['```json\n{"pass": true, "reason": "ok", "id": "i999"}\n```'])
    res, errors = contracts.ask_llm(backend, "triage", "i4", "prompt", 150)
    assert errors == []
    assert res == {"pass": True, "reason": "ok", "id": "i4"}
    assert backend.prompts == ["prompt"]


@pytest.mark.parametrize("raw,error", [
    ("garbage", "response is not valid JSON"),
    ('["pass", true]', "result: expected object"),
    ('{"pass": 1, "reason": "x"}', "pass: expected bool"),
])
def test_ask_llm_rejects(raw, error):
    res, errors = contracts.ask_llm(FakeBackend([raw]), "triage", "i4", "p", 150)
    assert res is None
    assert error in errors[0]


def test_ask_llm_backend_exception():
    class Boom:
        def complete(self, prompt, max_tokens=500):
            raise RuntimeError("overloaded")
    res, errors = contracts.ask_llm(Boom(), "triage", "i4", "p", 150)
    assert res is None and errors == ["backend error: overloaded"]


@pytest.mark.parametrize("stage", ["triage", "judge", "verify", "ruling", "report"])
def test_export_json_schema_shape(stage):
    schema = contracts.export_json_schema(stage)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["required"] == ["results"]
    items = schema["properties"]["results"]["items"]
    assert items["type"] == "object"
    assert set(items["required"]) == {f.name for f in contracts.SCHEMAS[stage] if f.required}
    json.dumps(schema)  # serialisable


def test_export_judge_details():
    items = contracts.export_json_schema("judge")["properties"]["results"]["items"]
    assert items["properties"]["pass"] == {"type": "boolean"}
    assert items["properties"]["tier"] == {"type": ["string", "null"],
                                           "enum": ["A", "B", "C", "D", None]}
    assert items["properties"]["facts"]["type"] == ["object", "null"]
    assert "findings" in items["properties"]["facts"]["required"]
    assert items["if"]["properties"]["pass"] == {"const": True}
    assert items["then"]["required"] == ["tier", "facts", "metric", "entities"]
    assert "direction" not in items["properties"]


def test_export_unknown_stage():
    with pytest.raises(ValueError, match="Unknown stage 'embed'"):
        contracts.export_json_schema("embed")


@pytest.mark.parametrize("raw", [None, 42, {"pass": True}])
def test_ask_llm_non_text_reply_is_rejected(raw):
    res, errors = contracts.ask_llm(FakeBackend([raw]), "triage", "i4", "p", 150)
    assert res is None and errors == ["response is not text"]


def test_fetch_contract_ids_and_rules():
    acc, rej = contracts.check_results("fetch", [
        {"id": "s3", "links": [{"url": "https://e.com/x"}]},
        {"id": "i1", "text": "t", "links": []},
        {"id": "x1", "text": "t"}], {"s3", "i1", "x1"})
    assert [a["id"] for a in acc] == ["s3"]
    assert rej[0]["errors"] == ["exactly one of text or links is required"]
    assert rej[1]["errors"] == ["id: malformed 'x1'"]


def _judge(**over):
    base = {"id": "i1", "pass": True, "tier": "B",
            "facts": {"what_was_tested": "x", "findings": "y", "research_timeframe": "2026"},
            "metric": "clicks"}
    return {**base, **over}


def test_judge_new_fields_are_optional():
    assert validate_result("judge", _judge()) == []


def test_judge_accepts_quotes_provenance_metric_baseline():
    res = _judge(quotes=[{"number": "4.2%", "quote": "clicks rose 4.2% overall"}],
                 provenance={"conducted_by": "Acme", "relation": "vendor",
                             "conflict_of_interest": "Acme sells the tool"})
    res["facts"].update(metric="organic clicks", baseline="1,200/week")
    assert validate_result("judge", res) == []


def test_judge_rejects_bad_quotes_and_relation():
    assert validate_result("judge", _judge(quotes=[{"number": "4.2%"}]))
    assert validate_result("judge", _judge(quotes=["4.2%"]))
    assert validate_result("judge", _judge(provenance={"relation": "friend"}))
    assert validate_result("judge", _judge(facts={"what_was_tested": "x", "findings": "y",
                                                  "research_timeframe": "z", "metric": 3}))


def test_blind_verify_null_red_flags_validates():
    assert validate_result("verify", {"id": "i1", "tier": "C", "red_flags": None, "note": "n"}) == []


def test_blind_verify_shape():
    ok = {"id": "i1", "tier": "C", "red_flags": ["no_baseline"], "note": "n"}
    assert validate_result("verify", ok) == []
    assert validate_result("verify", {"id": "i1", "tier": "C", "note": "n"}) == []
    assert validate_result("verify", {**ok, "tier": None})
    assert validate_result("verify", {**ok, "tier": "E"})


def test_unknown_red_flag_rejects_the_item():
    errors = validate_result("verify", {"id": "i1", "tier": "C",
                                        "red_flags": ["vibes"], "note": "n"})
    assert errors and "vibes" in errors[0]
    assert validate_result("verify", {"id": "i1", "tier": "C", "red_flags": [3], "note": "n"})


def test_ruling_keeps_the_old_shape():
    old = {"id": "i1", "agrees": True, "adjusted_tier": "C", "note": "owner"}
    assert validate_result("ruling", old) == []
    assert validate_result("verify", old)            # the skeptic may not send a ruling
    assert validate_result("ruling", {"id": "i1", "tier": "C", "note": "n"})


def test_ruling_and_verify_export_json_schema():
    assert contracts.export_json_schema("ruling")["properties"]["results"]["items"]["required"] == [
        "id", "agrees", "note"]
    assert "red_flags" in contracts.export_json_schema("verify")["properties"]["results"]["items"]["properties"]
