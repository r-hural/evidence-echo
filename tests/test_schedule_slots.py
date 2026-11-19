from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from evidence_echo.scheduling.slots import last_slot, next_slot
from evidence_echo.scheduling.spec import parse_rule

BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


def z(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_daily_stays_local_across_dst():
    """Review Focus 1: 07:00 Berlin is 06:00Z in winter and 05:00Z in summer time."""
    rule = parse_rule("daily 07:00", "report")
    assert last_slot(rule, z(2026, 3, 30, 6, 30), BERLIN) == z(2026, 3, 30, 5, 0)   # CEST
    assert last_slot(rule, z(2026, 3, 27, 7, 0), BERLIN) == z(2026, 3, 27, 6, 0)    # CET
    # before today's slot: yesterday's
    assert last_slot(rule, z(2026, 3, 30, 4, 59), BERLIN) == z(2026, 3, 29, 5, 0)


def test_weekly():
    rule = parse_rule("weekly mon 08:00", "report")
    assert last_slot(rule, z(2026, 11, 18, 12), BERLIN) == z(2026, 11, 16, 7, 0)
    assert next_slot(rule, z(2026, 11, 18, 12), BERLIN) == z(2026, 11, 23, 7, 0)


def test_biweekly_counts_from_the_anchor():
    rule = parse_rule("biweekly mon 08:00", "report")
    anchor = date(2026, 11, 16)
    assert last_slot(rule, z(2026, 11, 25), BERLIN, anchor) == z(2026, 11, 16, 7, 0)
    assert last_slot(rule, z(2026, 12, 1, 12), BERLIN, anchor) == z(2026, 11, 30, 7, 0)
    assert next_slot(rule, z(2026, 11, 30, 7, 0), BERLIN, anchor) == z(2026, 12, 14, 7, 0)


def test_monthly_last_is_each_months_last_day():
    rule = parse_rule("monthly last 09:00", "report")
    assert last_slot(rule, z(2027, 3, 5), UTC) == z(2027, 2, 28, 9, 0)
    assert last_slot(rule, z(2028, 3, 5), UTC) == z(2028, 2, 29, 9, 0)   # leap year
    assert next_slot(rule, z(2026, 11, 14), UTC) == z(2026, 11, 30, 9, 0)


def test_monthly_day():
    rule = parse_rule("monthly 15 09:00", "report")
    assert last_slot(rule, z(2026, 11, 14, 12), UTC) == z(2026, 10, 15, 9, 0)


def test_every_6h_and_hourly():
    every = parse_rule("every 6h", "fetch")
    assert last_slot(every, z(2026, 11, 14, 13, 30), UTC) == z(2026, 11, 14, 12, 0)
    assert next_slot(every, z(2026, 11, 14, 13, 30), UTC) == z(2026, 11, 14, 18, 0)
    assert next_slot(every, z(2026, 11, 14, 19, 0), UTC) == z(2026, 11, 15, 0, 0)
    hourly = parse_rule("hourly", "fetch")
    assert last_slot(hourly, z(2026, 11, 14, 13, 30), UTC) == z(2026, 11, 14, 13, 0)


def test_off_and_per_run_have_no_slots():
    for text in ("off", "per_run"):
        rule = parse_rule(text, "report")
        assert last_slot(rule, z(2026, 11, 14), UTC) is None
        assert next_slot(rule, z(2026, 11, 14), UTC) is None


def test_next_slot_in_a_zone_behind_utc():
    la = ZoneInfo("America/Los_Angeles")
    rule = parse_rule("daily 07:00", "report")
    # 2026-11-14 03:00Z is 19:00 on the 13th in Los Angeles: the next 07:00 is the 14th, 15:00Z
    assert next_slot(rule, z(2026, 11, 14, 3, 0), la) == z(2026, 11, 14, 15, 0)
