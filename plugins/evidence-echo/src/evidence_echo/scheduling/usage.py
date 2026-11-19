"""Usage basis (billed API vs subscription) and the limits each scheduled run gets.

`run` is passed in (it has the signature of `runner.run_killable`) because the runner imports this module.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

API = "api"
SUBSCRIPTION = "subscription"

ENV_GUARD_DIR = "EV_ECHO_GUARD_DIR"
ENV_WORK_DIR = "EV_ECHO_WORK_DIR"
ENV_MAX_TOOL_CALLS = "EV_ECHO_GUARD_MAX_TOOL_CALLS"
ENV_MAX_REPEATED_CALLS = "EV_ECHO_GUARD_MAX_REPEATED_CALLS"
ENV_MAX_TOTAL_TOKENS = "EV_ECHO_GUARD_MAX_TOTAL_TOKENS"

_SUBSCRIPTION_METHODS = {"claude.ai", "oauth_token"}
_STALE_SECONDS = 86400


def basis_of(auth_method: str | None) -> str:
    return SUBSCRIPTION if auth_method in _SUBSCRIPTION_METHODS else API


def detect_basis(claude: str, env: dict, cwd: str, run) -> str:
    """Any doubt counts as billed: the stricter limit applies."""
    try:
        proc = run([claude, "auth", "status", "--json"], cwd=cwd, env=env, timeout=30)
        if proc.returncode != 0:
            return API
        data = json.loads(proc.stdout)
        return basis_of(data.get("authMethod") if isinstance(data, dict) else None)
    except Exception:
        return API


@dataclass
class RunPlan:
    basis: str
    max_budget_usd: float
    env: dict[str, str]
    guard_dir: Path | None
    work_dir: Path | None = None


def _clean_old_guard_dirs(root: Path) -> None:
    try:
        cutoff = time.time() - _STALE_SECONDS
        for child in root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


def plan_run(sf, basis: str, data_dir: Path) -> RunPlan:
    """Every scheduled run gets its own guard folder in the data folder (the session guard's state and
    the run's working directory) and an empty `work` folder inside it (the only place the run may
    write). The loop limits apply on a subscription only. Raises OSError when a folder cannot be
    made: without the policy the run must not start."""
    root = Path(data_dir) / "scheduling" / "guard"
    root.mkdir(parents=True, exist_ok=True)
    _clean_old_guard_dirs(root)
    guard = root / uuid.uuid4().hex
    guard.mkdir(mode=0o700)
    work = guard / "work"
    work.mkdir(mode=0o700)
    env = {ENV_GUARD_DIR: str(guard), ENV_WORK_DIR: str(work)}
    if basis != SUBSCRIPTION:
        return RunPlan(API, sf.api_spend_limit_usd, env, guard, work)
    env.update({
        ENV_MAX_TOOL_CALLS: str(sf.session_max_tool_calls),
        ENV_MAX_REPEATED_CALLS: str(sf.session_max_repeated_calls),
        ENV_MAX_TOTAL_TOKENS: str(sf.session_max_total_tokens),
    })
    return RunPlan(SUBSCRIPTION, sf.session_ceiling_usd, env, guard, work)


def read_trip(guard_dir: Path | None) -> dict | None:
    if guard_dir is None:
        return None
    try:
        data = json.loads((Path(guard_dir) / "trip.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"limit": None}   # exists but unreadable: the hook treats it as tripped
    return data if isinstance(data, dict) and data else {"limit": None}


def remove_guard_dir(guard_dir: Path | None) -> None:
    if guard_dir is not None:
        shutil.rmtree(guard_dir, ignore_errors=True)


def remove_run_dirs(plan: RunPlan) -> None:
    remove_guard_dir(plan.guard_dir)   # the work folder is inside it


def _millions(n: float) -> str:
    return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def trip_message(trip: dict) -> str:
    """Never raises: a record that is not well formed gets the generic wording."""
    limit, value, cap = trip.get("limit"), trip.get("value"), trip.get("max")
    if limit == "repeated_calls" and _num(value):
        what = f"the same tool call repeated {value} times"
    elif limit == "tool_calls" and _num(value) and _num(cap):
        what = f"{value} tool calls (limit {cap})"
    elif limit == "total_tokens" and _num(value) and _num(cap):
        what = f"{_millions(value)} total tokens processed (limit {_millions(cap)})"
    else:
        what = "a limit was exceeded"
    return f"Stopped by the session guard: {what}, which suggests a loop"


def limit_message(basis: str, limit_usd: float) -> str:
    if basis == SUBSCRIPTION:
        return f"Session ceiling reached (${limit_usd:g} API-equivalent), which suggests a loop"
    return (f"API spend limit reached (${limit_usd:g}). Raise it with "
            "`ev schedule config --api-spend-limit-usd <amount>`. Queued items carry over to the next run")


def log_fields(basis: str | None, session_usd: float | None) -> dict:
    out: dict = {"usage_basis": basis}
    if session_usd is not None:
        if basis == API:
            out["api_cost_usd"] = session_usd
        elif basis == SUBSCRIPTION:
            out["api_equivalent_usd"] = session_usd
    return out
