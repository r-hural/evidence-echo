"""Wave 4a plan B: number formats in the primary check only (spec 'Plan B scope' item 5)."""
import pytest

from evidence_echo.core import identity
from evidence_echo.stages import primary


def _facts(*numbers):
    return {"match_keys": {"key_numbers": list(numbers)}}


@pytest.mark.parametrize("item,text", [
    ("1234.5", "Revenue reached 1.234,5 units."),
    ("1234567.89", "Total 1.234.567,89 visits."),
    ("12.5%", "Die CTR stieg um 12,5 %."),
    ("12,5 %", "CTR rose 12.5% overall."),
    ("0.5%", "Clicks fell 0,5% overall."),
    ("1234.5%", "Up 1.234,5 % year on year."),
    ("12000", "Wir testeten 12 000 Seiten."),
    ("12000", "Wir testeten 12\u2009000 Seiten."),
    ("12000", "Wir testeten 12\u00a0000 Seiten."),
    ("12000", "Wir testeten 12\u202f000 Seiten."),
    ("1234567", "Exactly 1 234 567 impressions."),
    ("12,5 percent", "CTR rose 12.5% overall."),
])
def test_european_formats_are_read(item, text):
    assert primary.number_check(_facts(item), text) == ("ok", [])


@pytest.mark.parametrize("item,text", [
    ("1.2 billion", "We logged 1,200,000,000 queries."),
    ("1.2bn", "We logged 1.2 billion queries."),
    ("1,2 Mrd.", "We logged 1.2 billion queries."),
    ("3 BN", "Spend was 3,000,000,000 dollars."),
    ("1,200 billion", "Spend was 1,200,000,000,000 dollars."),
    ("1200M", "Spend was 1.2 billion."),
])
def test_billion_words_are_read(item, text):
    assert primary.number_check(_facts(item), text) == ("ok", [])


def test_ambiguous_formats_keep_todays_reading():
    assert primary.number_check(_facts("1.234"), "We saw 1.234 sessions.") == ("ok", [])
    assert primary.number_check(_facts("1234"), "We saw 1.234 sessions.") == ("none", ["1234"])
    assert primary.number_check(_facts("1234"), "We saw 1,234 pages.") == ("ok", [])


def test_three_digits_after_a_comma_before_percent_is_thousands():
    assert primary.number_check(_facts("12.5%"), "Up 12,500 % overall.") == ("none", ["12.5%"])


def test_a_year_before_a_number_is_not_joined():
    assert primary.number_check(_facts("300"), "In 2024 300 sites were tested.") == ("ok", [])


def test_identity_key_numbers_is_unchanged():
    assert [identity.key_numbers([s]) for s in ("1.234,5", "12,5 %", "12 000", "1.2 billion")] \
        == [["1.234", "5"], ["12", "5%"], ["12"], ["1.2"]]


def test_missing_numbers_are_shown_as_written():
    assert primary.number_check(_facts("12,000", "4.2 %"), "Nothing here.") == \
        ("none", ["12,000", "4.2 %"])


def test_a_missing_number_is_shown_once_in_its_first_form():
    assert primary.number_check(_facts("12k", "12,000", "4.2%"), "Nothing here.") == \
        ("none", ["12k", "4.2%"])


def test_missing_numbers_collapse_whitespace_and_accept_non_strings():
    assert primary.number_check(_facts("  4.2\n%", 12000), "Nothing here.") == \
        ("none", ["4.2 %", "12000"])


def test_only_the_missing_entries_are_listed():
    assert primary.number_check(_facts("12,000", "4.2%"), "Up 3.9% on 12 000 pages.") == \
        ("partial", ["4.2%"])


def test_space_thousands_in_year_range_match_comma_form():
    assert primary.number_check(_facts("2,000 pages"), "We crawled 2 000 Seiten.") == ("ok", [])
    assert primary.number_check(_facts("2,000 pages"), "Wir testeten 2 000 Seiten.") == ("ok", [])


def test_space_thousands_with_comma_decimal():
    assert primary.number_check(_facts("2000.5%"), "Up 2 000,5 % overall.") == ("ok", [])


def test_number_check_ignores_key_numbers_not_in_findings():
    """B10: a key number the findings never state (another study of a roundup, the article's own
    headline) is not looked for in this study's original."""
    facts = {"match_keys": {"key_numbers": ["39", "12%"]}, "findings": "The title test lifted CTR 12%."}
    assert primary.number_check(facts, "CTR rose 12% in the test group.") == ("ok", [])


def test_number_check_falls_back_when_findings_name_no_key_number():
    facts = {"match_keys": {"key_numbers": ["39", "12%"]}, "findings": "CTR rose."}
    assert primary.number_check(facts, "CTR rose 12%.") == ("partial", ["39"])
