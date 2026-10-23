"""Wave 4a: the headline number check (token from the quote) and data_date normalisation."""
from datetime import date

import pytest

from evidence_echo.core import grading, knowledge

TEXT = ("We tracked 300k keywords. Position-1 CTR fell by −34.5 % after AI Overviews launched. "
        "Revenue grew to $1.2M in the test group.")
CTR = "Position-1 CTR fell by −34.5 % after AI Overviews launched."


def h(value, quote=CTR, label="position-1 CTR"):
    return {"value": value, "label": label, "quote": quote}


def test_checked_headline_shows_the_article_token():
    assert grading.check_headline(h("-34.5%"), TEXT) == {
        "value": "−34.5 %", "label": "position-1 CTR", "quote": CTR, "checked": True}


def test_sign_comes_from_the_quote_not_the_value():
    assert grading.check_headline(h("34.5%"), TEXT)["value"] == "−34.5 %"
    plain = "Clicks rose 12% on the variant pages."
    out = grading.check_headline(h("+12%", plain, "clicks"), plain)
    assert out == {"value": "12%", "label": "clicks", "quote": plain, "checked": True}


def test_currency_and_multiplier_stay_with_the_token():
    quote = "Revenue grew to $1.2M in the test group."
    assert grading.check_headline(h("1.2M", quote, "revenue"), TEXT)["value"] == "$1.2M"


def test_typographic_quotes_and_unicode_minus():
    text = "Shops said “CTR fell by −15 %” in    2024 tests."
    quote = 'Shops said "CTR fell by -15 %" in 2024 tests.'
    out = grading.check_headline(h("−15%", quote, "CTR"), text)
    assert out["checked"] is True and out["value"] == "-15 %"      # the quote's own spelling


@pytest.mark.parametrize("headline", [
    h("-40%"),                                         # number not in the quote
    h("-34.5%", "Position-1 CTR fell sharply this year."),
    h("-34.5%", "−34.5 %"),                           # under MIN_QUOTE_WORDS (3) words
    h("-34.5%", "Position-1 CTR fell by −34.5 % in another study entirely."),  # not in the text
    h("2024"),                                         # a year is no result number
    h("-34.5% and 12%"),                               # value must name exactly one number
])
def test_failing_checks_keep_the_llm_value_unchecked(headline):
    out = grading.check_headline(headline, TEXT)
    assert out["checked"] is False and out["value"] == headline["value"]


def test_label_spacing_is_collapsed():
    assert grading.check_headline(h("-34.5%", label="  position-1   CTR "), TEXT)["label"] == "position-1 CTR"


def test_headline_token():
    assert grading.headline_token("-34.5%", CTR) == "−34.5 %"
    assert grading.headline_token("7%", CTR) is None


TODAY = date(2026, 10, 18)


@pytest.mark.parametrize("raw,expected", [
    ("2024", "2024"), ("2024-05", "2024-05"), ("2024-5", "2024-05"), ("2024/05", "2024-05"),
    ("2024.05", "2024-05"), (" 2025-03 ", "2025-03"), ("1990", "1990"), ("2027", "2027"),
    ("1989", None), ("2028", None), ("2024-13", None), ("2024-00", None), ("Q2 2024", None),
    ("May 2024", None), ("", None), (None, None), (2024, None),
])
def test_normalise_data_date(raw, expected):
    assert knowledge.normalise_data_date(raw, TODAY) == expected


# Review I1: the token carries the quote's own unit or scale word; a value of another scale fails.
@pytest.mark.parametrize("value,quote,shown", [
    ("$1.2B", "Revenue in the test group hit $1.2 billion over the year.", "$1.2 billion"),
    ("3x", "Pages loaded 3x faster after the change.", "3x"),
    ("+2.5 positions", "Average position improved by 2.5 positions across the set.", "2.5 positions"),
    ("-5pp", "CTR dropped 5 percentage points after the update rolled out.", "5 percentage points"),
    ("$1.2M", "Revenue grew to $1.2 million in the test group.", "$1.2 million"),
])
def test_token_keeps_the_quotes_unit_and_scale(value, quote, shown):
    out = grading.check_headline(h(value, quote, "x"), quote)
    assert out["checked"] is True and out["value"] == shown


@pytest.mark.parametrize("value,quote", [
    ("$1.2", "Revenue in the test group hit $1.2 billion over the year."),
    ("$1.2B", "Revenue in the test group hit $1.2 million over the year."),
    ("1.2M", "Revenue in the test group hit $1.2 billion over the year."),
])
def test_value_of_another_scale_fails(value, quote):
    out = grading.check_headline(h(value, quote, "x"), quote)
    assert out["checked"] is False and out["value"] == value
