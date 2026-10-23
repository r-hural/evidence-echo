from datetime import timedelta, timezone

import pytest

from evidence_echo.render import clock


@pytest.fixture(autouse=True)
def plus_two(monkeypatch):
    monkeypatch.setattr(clock, "ZONE", timezone(timedelta(hours=2)))


def test_naive_string_is_utc():
    assert clock.local_minute("2026-10-20T07:31:00") == "2026-10-20 09:31"


def test_aware_string_converts():
    assert clock.local_minute("2026-10-20T07:31:00+00:00") == "2026-10-20 09:31"


def test_date_crosses_midnight():
    assert clock.local_date("2026-10-20T22:30:00+00:00") == "2026-10-21"


def test_empty_values():
    assert clock.local_date(None) == ""
    assert clock.local_minute("") == ""
