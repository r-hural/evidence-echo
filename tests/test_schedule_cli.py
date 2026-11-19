import json
import shutil
from datetime import date, datetime, timezone

import pytest

from evidence_echo import cli
from evidence_echo.core import config
from evidence_echo.scheduling import commands, installer, slots, spec
from tests.conftest import make_workspace


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_set_then_show_round_trip_with_next_slots(workspace, capsys):
    code, out, _ = run(capsys, "--topic", "seo", "schedule", "set", "--fetch", "daily 07:00",
                       "--report", "weekly mon 08:00")
    assert code == 0
    shown = json.loads(run(capsys, "schedule", "show")[1])
    t = shown["topics"]["seo"]
    assert (t["fetch"], t["report"], t["paused"]) == ("daily 07:00", "weekly mon 08:00", False)
    assert t["next_fetch"]["utc"] and t["next_report"]["local"].endswith(("+01:00", "+02:00", "+00:00"))
    sf = spec.load(workspace)
    assert str(sf.topics["seo"].report) == "weekly mon 08:00" and sf.topics["seo"].set_at
    assert shown["next_wake"] is not None and json.loads(out)["topics"]["seo"]["report"] == "weekly mon 08:00"


def test_new_topic_defaults(workspace, capsys):
    run(capsys, "schedule", "channel", "telegram", "--chat-id", "42")
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "daily 07:00")
    t = spec.load(workspace).topics["seo"]
    assert str(t.fetch) == "daily 07:00" and t.notify == ["telegram"]
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "monthly last 09:00", "--notify", "none")
    t = spec.load(workspace).topics["seo"]
    assert str(t.report) == "monthly last 09:00" and t.notify == [] and str(t.fetch) == "daily 07:00"


def test_per_run_report_defaults_fetch_to_off(workspace, capsys):
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "per_run")
    t = spec.load(workspace).topics["seo"]
    assert str(t.fetch) == "off" and str(t.report) == "per_run"


def test_biweekly_sets_the_anchor_once(workspace, capsys):
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "biweekly mon 08:00")
    anchor = spec.load(workspace).topics["seo"].anchor
    assert anchor is not None and anchor.weekday() == 0
    assert anchor >= datetime.now(timezone.utc).date()
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "biweekly mon 08:00", "--notify", "none")
    assert spec.load(workspace).topics["seo"].anchor == anchor          # unchanged rule: same anchor
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "weekly mon 08:00")
    assert spec.load(workspace).topics["seo"].anchor is None


@pytest.mark.parametrize("now,first", [
    (datetime(2026, 11, 12, 12, tzinfo=timezone.utc), date(2026, 11, 16)),   # a Thursday in one fortnight week
    (datetime(2026, 11, 19, 12, tzinfo=timezone.utc), date(2026, 11, 23)),   # and in the other
    (datetime(2026, 11, 23, 7, tzinfo=timezone.utc), date(2026, 11, 23)),    # the day itself, before the time
    (datetime(2026, 11, 23, 9, tzinfo=timezone.utc), date(2026, 11, 30)),    # the day itself, after the time
])
def test_a_biweekly_schedule_starts_on_its_next_weekday_in_any_week(now, first):
    rule, tz = spec.parse_rule("biweekly mon 08:00", "report"), spec.zone("UTC")
    anchor = commands._first_slot_date(rule, tz, now)
    assert anchor == first
    assert slots.next_slot(rule, now, tz, anchor) == datetime(first.year, first.month, first.day, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [["--report", "monthly 31 09:00"], ["--fetch", "per_run"],
                                 ["--report", "weekly funday 08:00"], ["--notify", "pigeon"], []])
def test_invalid_set_exits_2_with_the_allowed_forms(workspace, capsys, bad):
    code, _, err = run(capsys, "--topic", "seo", "schedule", "set", *bad)
    assert code == 2 and err.startswith("evidence-echo:")
    if bad[:1] in (["--report"], ["--fetch"]):
        assert "Allowed:" in err


def test_unknown_topic_exit_2(workspace, capsys):
    code, _, err = run(capsys, "--topic", "nope", "schedule", "set", "--report", "daily 07:00")
    assert code == 2 and "unknown topic 'nope'" in err


def test_wake_setting(workspace, capsys):
    assert run(capsys, "schedule", "config", "--wake", "every 6h")[0] == 0
    assert spec.load(workspace).wake == "every 6h"
    code, _, err = run(capsys, "schedule", "config", "--wake", "every 30h")
    assert code == 2 and "wake" in err
    assert run(capsys, "schedule", "config", "--timezone", "Mars/Olympus")[0] == 2
    assert run(capsys, "schedule", "config", "--timezone", "Europe/Berlin", "--api-spend-limit-usd", "3",
               "--notify-when-empty", "off")[0] == 0
    sf = spec.load(workspace)
    assert (sf.timezone, sf.api_spend_limit_usd, sf.notify_when_empty) == ("Europe/Berlin", 3.0, "off")


def test_schedule_config_sets_each_usage_limit(workspace, capsys):
    assert run(capsys, "schedule", "config", "--api-spend-limit-usd", "9", "--session-max-total-tokens", "1000",
               "--session-max-repeated-calls", "4", "--session-max-tool-calls", "50",
               "--session-ceiling-usd", "12")[0] == 0
    sf = spec.load(workspace)
    assert (sf.api_spend_limit_usd, sf.session_max_total_tokens, sf.session_max_repeated_calls,
            sf.session_max_tool_calls, sf.session_ceiling_usd) == (9.0, 1000, 4, 50, 12.0)


def test_a_usage_limit_must_be_above_zero(workspace, capsys):
    code, _, err = run(capsys, "schedule", "config", "--session-max-tool-calls", "0")
    assert code == 2 and "--session-max-tool-calls" in err


@pytest.mark.parametrize("flag", ["--api-spend-limit-usd", "--session-ceiling-usd"])
@pytest.mark.parametrize("bad", ["nan", "inf"])
def test_a_dollar_limit_must_be_finite(workspace, capsys, flag, bad):
    code, _, err = run(capsys, "schedule", "config", flag, bad)
    assert code == 2 and flag in err


def test_the_old_budget_flag_is_gone(workspace, capsys):
    with pytest.raises(SystemExit):
        run(capsys, "schedule", "config", "--budget-usd", "3")


def test_removed_topic_is_dropped_on_the_next_save(workspace, capsys):
    make_workspace(workspace, ("seo", "diet"))
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "daily 07:00")
    run(capsys, "--topic", "diet", "schedule", "set", "--report", "daily 08:00")
    assert set(spec.load(workspace).topics) == {"seo", "diet"}
    shutil.rmtree(config.workspace().topics_dir / "diet")
    run(capsys, "--topic", "seo", "schedule", "pause")
    sf = spec.load(workspace)
    assert set(sf.topics) == {"seo"} and sf.topics["seo"].paused
    run(capsys, "--topic", "seo", "schedule", "resume")
    assert not spec.load(workspace).topics["seo"].paused
    run(capsys, "--topic", "seo", "schedule", "remove")
    assert spec.load(workspace).topics == {}


def test_pause_without_a_schedule_exit_2(workspace, capsys):
    assert run(capsys, "--topic", "seo", "schedule", "pause")[0] == 2


def test_set_on_an_installed_file_calls_the_installer(workspace, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(installer, "sync", lambda data_dir: calls.append(data_dir))
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "daily 07:00")
    assert calls == []                                                   # not installed yet
    sf = spec.load(workspace)
    sf.installed = True
    spec.save(workspace, sf)
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "daily 09:00")
    run(capsys, "--topic", "seo", "schedule", "pause")
    assert calls == [workspace, workspace]


def test_channels(workspace, capsys):
    run(capsys, "schedule", "channel", "telegram", "--chat-id", "42")
    run(capsys, "schedule", "channel", "email", "--host", "smtp.example.com", "--port", "587",
        "--user", "me", "--from", "me@example.com", "--to", "you@example.com", "--security", "starttls")
    channels = spec.load(workspace).channels
    assert channels["telegram"] == {"chat_id": "42"}
    assert channels["email"] == {"host": "smtp.example.com", "port": 587, "user": "me",
                                 "from": "me@example.com", "to": "you@example.com", "security": "starttls"}
    run(capsys, "--topic", "seo", "schedule", "set", "--report", "daily 07:00")
    assert spec.load(workspace).topics["seo"].notify == ["telegram", "email"]
    run(capsys, "schedule", "channel", "remove", "telegram")
    sf = spec.load(workspace)
    assert "telegram" not in sf.channels and sf.topics["seo"].notify == ["email"]
    assert "password" not in json.dumps(sf.channels)


def test_corrupt_schedule_file_exit_2(workspace, capsys):
    path = spec.path_of(workspace)
    path.parent.mkdir(parents=True)
    path.write_text("{oops", encoding="utf-8")
    code, _, err = run(capsys, "schedule", "show")
    assert code == 2 and "schedule.json" in err
