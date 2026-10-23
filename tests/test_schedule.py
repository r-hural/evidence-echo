from datetime import datetime, timedelta, timezone

import pytest

from evidence_echo.stages import schedule

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def days_ago(*ds):
    return [NOW - timedelta(days=d) for d in ds]


def test_learned_interval_is_half_the_median_gap():
    assert schedule.learned_interval(days_ago(0, 4, 8, 12)) == 2.0        # gaps 4,4,4
    assert schedule.learned_interval(days_ago(0, 2, 8, 10, 20)) == 2.0    # gaps 2,6,2,10 -> median 4


def test_learned_interval_unsorted_input():
    assert schedule.learned_interval(days_ago(8, 0, 12, 4)) == 2.0


def test_learned_interval_clamped():
    assert schedule.learned_interval(days_ago(0, 0.1, 0.2)) == schedule.MIN_INTERVAL_DAYS
    assert schedule.learned_interval(days_ago(0, 60, 120)) == schedule.MAX_INTERVAL_DAYS


def test_learned_interval_identical_dates():
    assert schedule.learned_interval([NOW, NOW, NOW, NOW]) == schedule.MIN_INTERVAL_DAYS


def test_learned_interval_needs_three_dates():
    assert schedule.learned_interval(days_ago(0, 5)) is None
    assert schedule.learned_interval([]) is None


def test_learned_interval_uses_newest_sample_only():
    dates = days_ago(*range(0, 20, 2)) + days_ago(100, 200, 300)   # 10 newest gaps of 2 days
    assert schedule.learned_interval(dates) == 1.0


def test_interval_pin_wins():
    src = {"name": "R", "fetch_every": 7}
    assert schedule.interval(src, days_ago(0, 2, 4, 6)) == (7.0, "pinned", False)
    assert schedule.interval({"name": "R"}, days_ago(0, 2, 4, 6)) == (1.0, "learned", False)
    assert schedule.interval({"name": "R"}, []) == (None, None, False)


def test_interval_cap_applies_to_learned_and_pinned():
    assert schedule.interval({"name": "R"}, days_ago(0, 30, 60, 90), max_days=6) == (6.0, "learned", True)
    assert schedule.interval({"name": "R", "fetch_every": 20}, [], max_days=6) == (6.0, "pinned", True)
    assert schedule.interval({"name": "R", "fetch_every": 6}, [], max_days=6) == (6.0, "pinned", False)


def test_interval_cap_never_below_one_day():
    assert schedule.interval({"name": "R", "fetch_every": 5}, [], max_days=0) == (1.0, "pinned", True)


def test_interval_cap_from_horizon_and_kind():
    assert schedule.interval_cap("rss", 7) == 6
    assert schedule.interval_cap("rss", 30) == 29
    assert schedule.interval_cap("rss", 1) == 1                       # floored
    assert schedule.interval_cap("linkedin", 30) == schedule.LINKEDIN_MAX_DAYS == 6
    assert schedule.interval_cap("linkedin", 3) == 2


def _state(checked_days_ago=None, errors=0):
    checked = None if checked_days_ago is None else (NOW - timedelta(days=checked_days_ago)).isoformat()
    return {"last_checked": checked, "consecutive_errors": errors}


@pytest.mark.parametrize("state,interval,due", [
    (None, 7.0, True),                          # rule 3: no state row
    (_state(None), 7.0, True),                  # rule 3: never checked
    (_state(1, errors=2), 7.0, True),           # rule 4: last fetch failed
    (_state(1), None, True),                    # rule 5: no interval
    (_state(7), 7.0, True),                     # rule 6: exactly due
    (_state(6.9), 7.0, False),                  # not due yet
])
def test_is_due(state, interval, due):
    assert schedule.is_due(state, interval, NOW) is due


def test_next_due():
    assert schedule.next_due(_state(2), 7.0) == NOW + timedelta(days=5)
    assert schedule.next_due(_state(None), 7.0) is None
    assert schedule.next_due(_state(2), None) is None
