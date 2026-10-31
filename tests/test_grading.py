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
    capped, capped_from, reasons = grading.cap_tier(tier, facts)
    assert (capped, capped_from) == expected
    assert bool(reasons) == (capped_from is not None)


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


def test_number_range():
    """B9: the numbers of a headline value as (is percent, low, high)."""
    assert grading.number_range("30–40%") == (True, 30.0, 40.0)
    assert grading.number_range("0.003%") == (True, 0.003, 0.003)
    assert grading.number_range("1.2 million visits") == (False, 1_200_000.0, 1_200_000.0)
    assert grading.number_range("no number") is None
    assert grading.number_range(None) is None
    assert grading.number_range("1–5%") == (True, 1.0, 5.0)
    assert grading.number_range("0–40%") == (True, 0.0, 40.0)


def _words(number, quote, text=None):
    return grading.check_quotes([{"number": number, "quote": quote}], [number], text or quote)


@pytest.mark.parametrize("number,quote", [
    ("80%", "Four in five marketers said they would switch tools."),
    ("33.3%", "About one in three sites lost traffic after the update."),
    ("33%", "About one in three sites lost traffic after the update."),
    ("33.3%", "A third of all pages were not indexed at all."),
    ("50%", "Half of all sites had no structured data."),
    ("66.7%", "Roughly two thirds of the pages were redirected."),
    ("25%", "Only a quarter of the audited sites passed."),
    ("75%", "Three quarters of respondents saw gains."),
    ("20%", "1 out of 5 respondents saw gains in clicks."),
    ("12", "The agency tested twelve sites over six weeks."),
    ("20%", "Twenty percent of pages lost rankings after the update."),
    ("20%", "Twenty per cent of pages lost rankings after the update."),
    ("4.2%", "Clicks grew 4.2 percent over the test period."),
    ("33.33%", "A third of sites saw gains in the test."),
    ("66.67%", "Two thirds of sites saw gains in the test."),
])
def test_numbers_in_words_verify(number, quote):
    assert _words(number, quote) == "ok"


@pytest.mark.parametrize("period", ["first", "second", "latter"])
def test_half_of_a_period_is_not_fifty_percent(period):
    quote = f"In the {period} half of 2024 we tested title changes on 12 sites."
    assert _words("50%", quote) == "none"


def test_plain_number_word_with_percent_is_not_plain():
    assert _words("20", "Twenty percent of pages lost rankings after the update.") == "none"
    assert _words("4.2", "Clicks grew 4.2 percent over the test period.") == "ok"  # digits as before


def test_four_in_five_is_not_75_percent():
    assert _words("75%", "Four in five marketers said they would switch tools.") == "none"


def test_words_do_not_cross_percent_and_plain():
    assert _words("4", "Four in five marketers said they would switch tools.") == "none"


def test_word_quote_still_needs_enough_words_and_the_text():
    assert _words("50%", "Half of", "Half of it was fine") == "none"
    assert grading.check_quotes([{"number": "80%", "quote": "Four in five marketers said so."}],
                                ["80%"], "Other text entirely.") == "none"


def test_quote_found_normalises():
    text = 'Variant pages saw "+4.2%" organic clicks - across 12k pages.'
    assert grading.quote_found("Variant pages saw “+4.2%” organic  clicks – across 12k pages.", text)
    assert not grading.quote_found("Variant pages saw nothing at all", text)
    assert not grading.quote_found(None, text) and not grading.quote_found("Variant pages saw", "")


def test_quote_found_needs_three_words():
    assert not grading.quote_found("rose 31%", "Click-through rose 31% on forty pages.")
    assert grading.quote_found("rose 31% on", "Click-through rose 31% on forty pages.")
