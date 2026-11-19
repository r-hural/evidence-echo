"""Source problems grouped by reason: missing keys, failing sources, one-off errors.

A source whose kind needs a key that is not set is skipped (never fetched, never recorded as an
error), so it cannot drift towards "failing"; it is listed here as skipped instead.
"""
from dataclasses import dataclass

from evidence_echo.core import store
from evidence_echo.sources import get_kind

FAILING_REASON = "Failing for 5+ runs — check or remove"
ERROR_CHARS = 60


@dataclass(frozen=True)
class ProblemGroup:
    reason: str
    sources: list[str]


def missing_key_reason(source_type: str, cfg) -> str | None:
    """The kind's reason text when the kind needs a key and cfg has none; else None."""
    kind = get_kind(source_type)
    needs = getattr(kind, "NEEDS_KEY", None)
    if needs and not getattr(cfg, needs, ""):
        return getattr(kind, "NO_KEY_REASON", f"{needs} missing")
    return None


def problems(conn, topic, cfg) -> list[ProblemGroup]:
    sources = list(getattr(topic, "sources", []))
    groups: dict[str, list[str]] = {}
    skipped = set()
    if cfg is not None:
        for s in sources:
            reason = missing_key_reason(s["type"], cfg)
            if reason:
                groups.setdefault(reason, []).append(s["name"])
                skipped.add(s["name"])
    out = [ProblemGroup(r, names) for r, names in groups.items()]
    names = {s["name"] for s in sources} - skipped
    states = [s for s in store.all_source_states(conn) if s["name"] in names]
    failing = [f"{s['name']} (since {(s['failing_since'] or '')[:10]})"
               for s in states if s["consecutive_errors"] >= store.FAILING_AFTER]
    if failing:
        out.append(ProblemGroup(FAILING_REASON, failing))
    erring: dict[str, list[str]] = {}
    for s in states:
        if s["last_error"] and s["consecutive_errors"] < store.FAILING_AFTER:
            erring.setdefault(s["last_error"][:ERROR_CHARS], []).append(s["name"])
    out += [ProblemGroup(r, n) for r, n in erring.items()]
    return out


def format_group(g: ProblemGroup) -> str:
    return f"{g.reason}: {', '.join(g.sources)}"
