import pytest

from evidence_echo.core import identity


@pytest.mark.parametrize("name,key", [
    ("Stack Overflow Developer Survey 2025", "stack overflow developer survey"),
    ("stack overflow developer-survey", "stack overflow developer survey"),
    ("  State of JS (2023/2024) ", "state of js"),
    ("", ""), (None, ""), ("2025", ""),
])
def test_series_key(name, key):
    assert identity.series_key(name) == key


def test_key_numbers_normalised():
    got = identity.key_numbers(["AI use rose from 44% to 79 %", "+4.2% clicks", "12k pages",
                                "1.5m sessions", "1,200 sites", "-3% bounce", "n=3"])
    assert got == sorted(["44%", "79%", "4.2%", "12000", "1500000", "1200", "-3%", "3"])


def test_key_numbers_drop_years_and_trivial():
    assert identity.key_numbers(["surveys 2023 to 2025", "2026-Q2", "0", "1", "100%", "0%"]) == []
    assert identity.key_numbers(["2025%"]) == ["2025%"]           # a percentage is never a year


def test_key_numbers_tolerates_odd_input():
    assert identity.key_numbers(None) == []
    assert identity.key_numbers([44, 7.5, None, "v2", "Q3"]) == ["44", "7.5"]
    assert identity.key_numbers(["44%", "44 %"]) == ["44%"]


def _c(cid, series="", numbers=(), order=0):
    return {"cid": cid, "series_key": series, "numbers": list(numbers), "order": order}


def test_rank_candidates_order_and_cap():
    pool = [_c("s1", numbers=["44%"], order=1),
            _c("s2", "so survey", order=2),
            _c("s3", numbers=["44%", "79%"], order=3),
            _c("s4", numbers=["5%"], order=4),
            _c("s5", numbers=["44%"], order=5)]
    got = [c["cid"] for c in identity.rank_candidates("so survey", ["44%", "79%"], pool)]
    assert got == ["s2", "s3", "s5", "s1"]                       # series, then shared count, then newest


def test_rank_candidates_cap_and_empty_series():
    pool = [_c(f"s{i}", numbers=["44%"], order=i) for i in range(8)]
    assert len(identity.rank_candidates("", ["44%"], pool)) == identity.MAX_CANDIDATES
    assert identity.rank_candidates("", [], [_c("s1", "")]) == []   # empty keys never match


def test_key_numbers_ranges_are_not_signs():
    assert identity.key_numbers(["44%-79%"]) == ["44%", "79%"]
    assert identity.key_numbers(["10-20"]) == ["10", "20"]
    assert identity.key_numbers(["-3% bounce"]) == ["-3%"]
