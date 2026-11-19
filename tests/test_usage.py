import subprocess

import pytest

from evidence_echo.scheduling import spec, usage


@pytest.mark.parametrize("method,basis", [("claude.ai", "subscription"), ("oauth_token", "subscription"),
    ("api_key", "api"), ("api_key_helper", "api"), ("third_party", "api"), ("none", "api"), (None, "api")])
def test_basis_of(method, basis):
    assert usage.basis_of(method) == basis


@pytest.mark.parametrize("outcome", ["not json", subprocess.TimeoutExpired("claude", 30), OSError("gone")])
def test_an_unreadable_login_counts_as_billed(outcome):
    def run(cmd, **kw):
        if isinstance(outcome, Exception):
            raise outcome
        return subprocess.CompletedProcess(cmd, 0, outcome, "")
    assert usage.detect_basis("claude", {}, ".", run) == "api"


def test_a_failing_status_command_counts_as_billed():
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, '{"authMethod": "claude.ai"}', "")
    assert usage.detect_basis("claude", {}, ".", run) == "api"


def test_detect_basis_asks_claude_with_the_runs_environment():
    seen = {}
    def run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, '{"authMethod": "claude.ai"}', "")
    assert usage.detect_basis("claude", {"A": "1"}, "here", run) == "subscription"
    assert seen["cmd"] == ["claude", "auth", "status", "--json"]
    assert seen["env"] == {"A": "1"} and seen["timeout"] == 30


def test_a_billed_run_gets_the_policy_folders_and_only_the_spend_limit(tmp_path):
    plan = usage.plan_run(spec.ScheduleFile(), "api", tmp_path)
    try:
        assert plan.max_budget_usd == 6.0 and plan.guard_dir.is_dir() and plan.work_dir.is_dir()
        assert plan.env == {"EV_ECHO_GUARD_DIR": str(plan.guard_dir), "EV_ECHO_WORK_DIR": str(plan.work_dir)}
    finally:
        usage.remove_run_dirs(plan)
    assert not plan.guard_dir.exists() and not plan.work_dir.exists()


def test_a_subscription_run_gets_the_guard_and_the_ceiling(tmp_path):
    plan = usage.plan_run(spec.ScheduleFile(), "subscription", tmp_path)
    try:
        assert plan.max_budget_usd == 40.0 and plan.guard_dir.is_dir()
        assert plan.guard_dir.parent == tmp_path / "scheduling" / "guard"
        assert plan.work_dir == plan.guard_dir / "work" and plan.work_dir.is_dir()   # the run writes only here
        assert plan.env == {"EV_ECHO_GUARD_DIR": str(plan.guard_dir), "EV_ECHO_WORK_DIR": str(plan.work_dir),
                            "EV_ECHO_GUARD_MAX_TOOL_CALLS": "500", "EV_ECHO_GUARD_MAX_REPEATED_CALLS": "8",
                            "EV_ECHO_GUARD_MAX_TOTAL_TOKENS": "30000000"}
    finally:
        usage.remove_run_dirs(plan)


def test_plan_run_raises_when_the_guard_folder_cannot_be_made(tmp_path):
    (tmp_path / "scheduling").mkdir()
    (tmp_path / "scheduling" / "guard").write_text("a file, not a folder", encoding="utf-8")
    with pytest.raises(OSError):
        usage.plan_run(spec.ScheduleFile(), "api", tmp_path)


def test_plan_run_removes_only_old_guard_folders(tmp_path):
    import os, time
    guard = tmp_path / "scheduling" / "guard"
    old, fresh = guard / "old", guard / "fresh"
    old.mkdir(parents=True)
    fresh.mkdir()
    stale = time.time() - 2 * 86400
    os.utime(old, (stale, stale))
    usage.plan_run(spec.ScheduleFile(), "subscription", tmp_path)
    assert not old.exists() and fresh.exists()


def test_messages_name_the_real_cause():
    assert usage.trip_message({"limit": "repeated_calls", "value": 8, "max": 8}) == \
        "Stopped by the session guard: the same tool call repeated 8 times, which suggests a loop"
    assert usage.trip_message({"limit": "tool_calls", "value": 501, "max": 500}) == \
        "Stopped by the session guard: 501 tool calls (limit 500), which suggests a loop"
    assert usage.trip_message({"limit": "total_tokens", "value": 31_200_000, "max": 30_000_000}) == \
        "Stopped by the session guard: 31.2M total tokens processed (limit 30M), which suggests a loop"
    assert usage.limit_message("api", 6.0) == ("API spend limit reached ($6). Raise it with "
        "`ev schedule config --api-spend-limit-usd <amount>`. Queued items carry over to the next run")
    assert usage.limit_message("subscription", 40.0) == \
        "Session ceiling reached ($40 API-equivalent), which suggests a loop"


def test_log_fields_keep_real_money_and_estimates_apart():
    assert usage.log_fields("api", 1.5) == {"usage_basis": "api", "api_cost_usd": 1.5}
    assert usage.log_fields("subscription", 1.5) == {"usage_basis": "subscription", "api_equivalent_usd": 1.5}
    assert usage.log_fields(None, None) == {"usage_basis": None}


def test_read_trip_survives_a_missing_or_broken_record(tmp_path):
    assert usage.read_trip(None) is None and usage.read_trip(tmp_path) is None
    (tmp_path / "trip.json").write_text("{broken", encoding="utf-8")
    assert usage.read_trip(tmp_path) == {"limit": None}
    (tmp_path / "trip.json").write_text("{}", encoding="utf-8")
    assert usage.read_trip(tmp_path) == {"limit": None}
    (tmp_path / "trip.json").write_text("[1]", encoding="utf-8")
    assert usage.read_trip(tmp_path) == {"limit": None}


GENERIC = "Stopped by the session guard: a limit was exceeded, which suggests a loop"


@pytest.mark.parametrize("trip", [
    {"limit": "surprise", "value": 1, "max": 2},
    {"limit": "repeated_calls"},
    {"limit": "repeated_calls", "value": "8"},
    {"limit": "repeated_calls", "value": True},
    {"limit": "tool_calls", "value": 501},
    {"limit": "tool_calls", "value": "501", "max": 500},
    {"limit": "tool_calls", "value": True, "max": 500},
    {"limit": "tool_calls", "value": 501, "max": False},
    {"limit": "total_tokens", "value": None, "max": 5},
    {"limit": "total_tokens", "value": 5, "max": "x"},
    {"limit": None},
    {}])
def test_a_malformed_trip_gives_the_generic_message(trip):
    assert usage.trip_message(trip) == GENERIC


def test_read_trip_returns_the_record_and_remove_guard_dir_cleans_up(tmp_path):
    (tmp_path / "trip.json").write_text('{"limit": "tool_calls", "value": 501, "max": 500, "calls_at_trip": 501}',
                                        encoding="utf-8")
    assert usage.read_trip(tmp_path)["value"] == 501
    usage.remove_guard_dir(tmp_path)
    assert not tmp_path.exists()
    usage.remove_guard_dir(None)
    usage.remove_guard_dir(tmp_path)
