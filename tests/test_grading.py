# tests/test_grading.py
import pytest

from evidence_echo.core import grading

TEXT = "Variant pages saw +4.2% organic clicks across 12k pages. The control group stayed flat."


def test_red_flags_are_the_twelve_codes():
    assert list(grading.RED_FLAGS) == [
        "correlation_causation", "cherry_pick", "anecdote_generalisation", "stale_data",
        "headline_overclaim", "misrepresented_primary", "no_baseline", "confounded_window",
        "vendor_metric", "multiple_metrics", "regression_to_mean", "selection_bias"]
    assert all(grading.RED_FLAGS.values())


@pytest.mark.parametrize("value,expected", [
    ("12k pages", True), ("n=3 sites", True), (None, False), ("", False), ("   ", False),
    ("unstated", False), ("Unstated", False), (" UNSTATED ", False)])
def test_stated(value, expected):
    assert grading.stated(value) is expected


@pytest.mark.parametrize("tier,facts,expected", [
    ("A", {"has_controls": True, "sample_scale": "12k pages"}, ("A", None)),
    ("A", {"has_controls": False, "sample_scale": "12k pages"}, ("B", "A")),
    ("A", {"has_controls": None, "sample_scale": "12k pages"}, ("B", "A")),
    ("A", {"sample_scale": "unstated"}, ("C", "A")),
    ("A", {}, ("C", "A")),
    ("B", {"sample_scale": "12k pages"}, ("B", None)),
    ("B", {"sample_scale": ""}, ("C", "B")),
    ("B", {"sample_scale": None}, ("C", "B")),
    ("C", {}, ("C", None)),
    ("D", {}, ("D", None)),
    (None, {}, (None, None)),
])
def test_cap_tier(tier, facts, expected):
    assert grading.cap_tier(tier, facts) == expected


def test_fact_numbers_tolerates_bad_shapes():
    assert grading.fact_numbers({"match_keys": {"key_numbers": ["+4.2%", "12k"]}}) == ["12000", "4.2%"]
    assert grading.fact_numbers({"match_keys": None}) == []
    assert grading.fact_numbers({"match_keys": {"key_numbers": "4.2%"}}) == []
    assert grading.fact_numbers(None) == []


QUOTE = {"number": "4.2%", "quote": "Variant pages saw +4.2% organic clicks across 12k pages."}


def test_exact_quote_verifies():
    assert grading.check_quotes([QUOTE], ["4.2%"], TEXT) == "ok"


def test_whitespace_curly_quotes_and_dashes_are_normalised():
    text = "He said “variant pages saw +4.2% organic clicks” – a clear win."
    quote = {"number": "4.2%", "quote": 'He said "variant pages saw +4.2% organic   clicks" - a clear win.'}
    assert grading.check_quotes([quote], ["4.2%"], text) == "ok"


def test_quote_in_text_without_its_number_fails():
    quote = {"number": "4.2%", "quote": "The control group stayed flat."}
    assert grading.check_quotes([quote], ["4.2%"], TEXT) == "none"


def test_paraphrase_fails():
    quote = {"number": "4.2%", "quote": "Organic clicks rose 4.2% on the variant pages."}
    assert grading.check_quotes([quote], ["4.2%"], TEXT) == "none"


def test_bare_number_quote_fails():
    assert grading.check_quotes([{"number": "4.2%", "quote": "+4.2%"}], ["4.2%"], TEXT) == "none"


def test_partial_and_missing_numbers():
    numbers = ["12000", "4.2%", "9%"]
    quotes = [QUOTE, {"number": "12k", "quote": "Variant pages saw +4.2% organic clicks across 12k pages."}]
    assert grading.check_quotes(quotes, numbers, TEXT) == "partial"
    assert grading.missing_numbers(quotes, numbers, TEXT) == ["9%"]


def test_no_quotes_or_no_numbers_is_none():
    assert grading.check_quotes([], ["4.2%"], TEXT) == "none"
    assert grading.check_quotes(None, ["4.2%"], TEXT) == "none"
    assert grading.check_quotes([QUOTE], [], TEXT) == "none"


def test_malformed_quote_entries_are_ignored():
    quotes = ["4.2%", {"number": 4.2, "quote": "x"}, {"quote": "no number"}, QUOTE]
    assert grading.check_quotes(quotes, ["4.2%"], TEXT) == "ok"


@pytest.mark.parametrize("judge,skeptic,qc,expected", [
    ("B", "B", "ok", ("B", False, None)),
    ("B", "C", "ok", ("C", False, None)),
    ("C", "B", "partial", ("C", False, None)),
    ("A", "C", "ok", ("A", True, "grade_gap")),
    ("A", "D", "ok", ("A", True, "grade_gap")),
    ("D", "B", "ok", ("D", True, "grade_gap")),
    ("B", "B", "none", ("B", True, "no_quote")),
    ("B", "C", None, ("C", False, None)),
    ("A", "C", None, ("A", True, "grade_gap")),
    (None, "C", "ok", ("C", False, None)),
])
def test_compare(judge, skeptic, qc, expected):
    assert grading.compare(judge, skeptic, qc) == expected


def test_red_flag_list_names_every_code():
    text = grading.red_flag_list()
    assert all(f"- {code}: " in text for code in grading.RED_FLAGS)
