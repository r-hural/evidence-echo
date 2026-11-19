from datetime import date, time

import pytest

from evidence_echo.core.settings import ConfigError
from evidence_echo.scheduling import spec


def test_parse_forms():
    assert spec.parse_rule("daily 07:00", "fetch").time == time(7, 0)
    r = spec.parse_rule("weekly Mon 08:30", "report")
    assert (r.kind, r.weekday, r.time) == ("weekly", 0, time(8, 30))
    assert spec.parse_rule("monthly last 09:00", "report").day == "last"
    assert spec.parse_rule("every 6h", "fetch").hours == 6
    assert spec.parse_rule("per_run", "report").kind == "per_run"
    assert str(spec.parse_rule("biweekly fri 7:05", "report")) == "biweekly fri 07:05"


@pytest.mark.parametrize("text, field", [
    ("monthly 31 09:00", "report"), ("monthly 29 09:00", "report"),
    ("per_run", "fetch"), ("biweekly mon 08:00", "fetch"), ("monthly 1 08:00", "fetch"),
    ("hourly", "report"), ("every 6h", "report"), ("every 25h", "fetch"), ("every 0h", "fetch"),
    ("weekly funday 08:00", "report"), ("daily 25:00", "report"), ("daily", "report"),
    ("sometimes", "report"), ("", "report"), ("off now", "report")])
def test_refused(text, field):
    with pytest.raises(ConfigError) as exc:
        spec.parse_rule(text, field)
    assert field in str(exc.value) and "Allowed:" in str(exc.value)


def test_weekday_error_names_the_days():
    with pytest.raises(ConfigError, match="mon, tue, wed, thu, fri, sat, sun"):
        spec.parse_rule("weekly funday 08:00", "report")


def test_load_missing_file_gives_defaults(tmp_path):
    sf = spec.load(tmp_path)
    assert (sf.wake, sf.api_spend_limit_usd, sf.notify_when_empty, sf.installed) == ("auto", 6.0, "on", False)
    assert sf.topics == {} and sf.timezone


def test_usage_limit_defaults():
    sf = spec.ScheduleFile()
    assert (sf.api_spend_limit_usd, sf.session_max_total_tokens, sf.session_max_repeated_calls,
            sf.session_max_tool_calls, sf.session_ceiling_usd) == (6.0, 30_000_000, 8, 500, 40.0)


def test_an_old_budget_key_becomes_the_api_spend_limit(tmp_path):
    spec.path_of(tmp_path).parent.mkdir(parents=True)
    spec.path_of(tmp_path).write_text('{"timezone": "UTC", "budget_usd": 2.0}', encoding="utf-8")
    sf = spec.load(tmp_path)
    assert sf.api_spend_limit_usd == 2.0
    saved = spec.to_json(sf)
    assert saved["api_spend_limit_usd"] == 2.0 and "budget_usd" not in saved


def test_save_load_round_trip(tmp_path):
    sf = spec.ScheduleFile(timezone="Europe/Berlin", wake="every 3h", api_spend_limit_usd=1.5,
                           channels={"telegram": {"chat_id": "42"}}, installed=True)
    sf.topics["seo"] = spec.TopicSchedule(
        fetch=spec.parse_rule("daily 07:00", "fetch"), report=spec.parse_rule("biweekly mon 08:00", "report"),
        notify=["telegram"], paused=True, notify_when_empty="off", anchor=date(2026, 11, 16),
        desktop_task=True, set_at="2026-11-14T10:00:00+00:00")
    path = spec.save(tmp_path, sf)
    assert path == tmp_path / "scheduling" / "schedule.json"
    assert spec.load(tmp_path) == sf


def test_corrupt_file_raises_with_the_path(tmp_path):
    path = spec.path_of(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="schedule.json"):
        spec.load(tmp_path)
    path.write_text('{"topics": {"seo": {"report": "monthly 31 09:00"}}}', encoding="utf-8")
    with pytest.raises(ConfigError, match="schedule.json"):
        spec.load(tmp_path)


def test_check_wake():
    assert spec.check_wake("auto") == "auto"
    assert spec.check_wake("every 6h") == "every 6h"
    for bad in ("every 30h", "every 0h", "hourly", "every h"):
        with pytest.raises(ConfigError):
            spec.check_wake(bad)
