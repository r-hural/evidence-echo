import copy

import pytest

from evidence_echo.core import grading

EXERCISE = grading.effective({
    "tiers": {"A": "rct", "B": "cohort", "C": "case series", "D": "anecdote"},
    "study_types": ["rct", "observational", "animal", "lab", "anecdote"],
    "caps": {"study_type": {"animal": "C", "lab": "C"}, "peer_review": {"preprint": "B"},
             "text_basis": {"abstract_only": "B"}},
    "labels": ["observational", "animal", "lab", "preprint", "abstract_only"],
    "red_flags": ["surrogate_outcome", "animal_to_human"],
    "disclaimer": "Summary of published studies, not advice.",
})
GOOD = {"has_controls": True, "sample_scale": "n=40"}


def test_generic_block_is_todays_grading():
    assert grading.effective(None) == grading.generic()
    assert grading.generic()["caps"] == {}
    assert "other" in grading.generic()["study_types"]
    assert "A/B or split test" in grading.generic()["tiers"]["A"]
    assert grading.generic() is not grading.generic()


def test_exercise_block_is_valid():
    assert grading.rules_errors(EXERCISE) == []
    assert EXERCISE["study_types"][-1] == "other"


@pytest.mark.parametrize("block, key", [
    ({"x": 1}, "grading.x"),
    ({"tiers": {"A": "a", "B": "b", "C": "c"}}, "grading.tiers.D"),
    ({"tiers": {"A": "a", "B": "b", "C": "c", "D": "d" * 301}}, "grading.tiers.D"),
    ({"study_types": ["mouse"]}, "grading.study_types"),
    ({"caps": {"study_type": {"animal": "E"}}}, "grading.caps.study_type.animal"),
    ({"caps": {"sample": {"x": "C"}}}, "grading.caps.sample"),
    ({"study_types": ["rct"], "caps": {"study_type": {"animal": "C"}}}, "grading.caps.study_type.animal"),
    ({"labels": ["nonsense"]}, "grading.labels"),
    ({"red_flags": ["cherry_pick"]}, "grading.red_flags"),
    ({"disclaimer": "a\nb"}, "grading.disclaimer"),
    ({"disclaimer": "x" * 201}, "grading.disclaimer"),
])
def test_rules_errors_names_each_problem(block, key):
    errs = grading.rules_errors(block)
    assert len(errs) == 1 and key in errs[0]
    with pytest.raises(ValueError, match="grading"):
        grading.effective(block)


def test_cap_tier_each_cap():
    assert grading.cap_tier("A", {**GOOD, "study_type": "animal"}, EXERCISE) == ("C", "A", ["animal"])
    assert grading.cap_tier("A", {**GOOD, "text_basis": "abstract_only"}, EXERCISE) == \
        ("B", "A", ["abstract_only"])
    assert grading.cap_tier("A", {**GOOD, "peer_review": "preprint"}, EXERCISE) == ("B", "A", ["preprint"])
    assert grading.cap_tier("A", GOOD, EXERCISE) == ("A", None, [])
    assert grading.cap_tier(None, GOOD, EXERCISE) == (None, None, [])


def test_cap_tier_lowest_wins_and_existing_caps_kept():
    facts = {"has_controls": False, "sample_scale": "unstated", "study_type": "lab"}
    assert grading.cap_tier("A", facts, EXERCISE) == ("C", "A", ["no_controls", "no_sample", "lab"])
    assert grading.cap_tier("A", facts) == ("C", "A", ["no_controls", "no_sample"])
    assert grading.cap_tier("A", facts, grading.generic()) == ("C", "A", ["no_controls", "no_sample"])
    assert grading.cap_tier("C", {"study_type": "animal"}, EXERCISE) == ("C", None, [])


def test_grade_pair_caps_both_before_compare():
    animal = {**GOOD, "study_type": "animal"}
    out = grading.grade_pair("A", "A", animal, EXERCISE, "ok")
    assert (out["final_tier"], out["disputed"], out["capped_from"], out["cap_reasons"]) == \
        ("C", False, "A", ["animal"])
    out = grading.grade_pair("A", "D", {**GOOD, "study_type": "rct"}, EXERCISE, "ok")
    assert out["disputed"] and out["dispute_reason"] == "grade_gap" and out["final_tier"] == "A"
    out = grading.grade_pair("A", None, animal, EXERCISE, "ok")
    assert out["final_tier"] == "C" and not out["disputed"] and out["skeptic_tier"] is None


def test_rules_hash_ignores_caps_labels_disclaimer():
    base = grading.rules_hash(EXERCISE)
    caps_base = grading.caps_hash(EXERCISE)
    for key, value in [("caps", {}), ("labels", []), ("disclaimer", "other text")]:
        changed = copy.deepcopy(EXERCISE)
        changed[key] = value
        assert grading.rules_hash(changed) == base
        assert (grading.caps_hash(changed) != caps_base) == (key == "caps")
    for key, value in [("tiers", {**EXERCISE["tiers"], "A": "changed"}),
                       ("study_types", ["rct", "other"]), ("red_flags", [])]:
        changed = copy.deepcopy(EXERCISE)
        changed[key] = value
        assert grading.rules_hash(changed) != base
        assert grading.caps_hash(changed) == caps_base


def test_grade_labels_in_block_order():
    facts = {"study_type": "observational", "peer_review": "preprint"}
    assert grading.grade_labels(facts, EXERCISE) == ["observational", "preprint"]
    assert grading.grade_labels({"study_type": "rct", "peer_review": "reviewed"}, EXERCISE) == []
    assert grading.grade_labels({"text_basis": "abstract_only"}, EXERCISE) == ["abstract only"]
    assert grading.grade_labels(None, EXERCISE) == []


def test_payload_rules_lists_global_then_field_flags():
    payload = grading.payload_rules(EXERCISE)
    assert list(payload["red_flags"]) == list(grading.RED_FLAGS) + EXERCISE["red_flags"]
    assert payload["tiers"] == EXERCISE["tiers"]
    text = grading.red_flag_list(EXERCISE)
    assert "surrogate_outcome" in text and "benchmark_contamination" not in text
    assert grading.red_flag_list() == grading.red_flag_list(grading.generic())


def test_texts_and_tier_lines():
    assert grading.cap_text(["no_controls", "animal", "rct"]) == "no control group, animal study, rct"
    assert grading.tier_rules_text(EXERCISE).splitlines()[0] == "- Tier A: rct"
    assert grading.rules_of(object()) == grading.generic()
