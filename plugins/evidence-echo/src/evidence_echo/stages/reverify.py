"""Reverify: the skeptic grades a re-graded study again, blind, with the topic's current rules.

Regrade changes the judge's side (tier and facts) of a stored study; reverify asks the skeptic again
and compares the two in code, exactly like verify. It never changes an item's state. A study the owner
ruled on keeps the ruled tier (capped again) and raises no new dispute.
"""
import json

from evidence_echo.core import contracts, grading, interfaces, store
from evidence_echo.stages import regrade, verify

STAGE = "reverify"
PROMPT_VERSION = verify.PROMPT_VERSION


def _selected(conn, topic) -> list[dict]:
    return store.items_to_reverify(conn, grading.rules_hash(grading.rules_of(topic)))


def emit(conn, topic) -> str:
    items = [{"id": interfaces.opaque_id(it["id"]), "title": it["title"],
              "publication_date": it["published"],
              "full_text": (it["full_text"] or "")[:verify.TEXT_LIMIT]}
             for it in _selected(conn, topic)]
    return interfaces.envelope(STAGE, verify.instructions(topic), items, topic)


def _grade(conn, item: dict, res: dict, topic) -> bool:
    """Store the skeptic's answer; True when it raised a dispute that was not there before."""
    rules = grading.rules_of(topic)
    facts = verify._facts(item)
    judge_raw = item.get("judge_tier_raw") or item["judge_tier"] or item["evidence_tier"]
    flags = res.get("red_flags") or []
    ruled = regrade._ruled_tier(item)
    if ruled is None and item.get("skeptic_agrees") is not None and item.get("skeptic_tier") is None:
        ruled = item["evidence_tier"]    # the owner settled a dispute in the judge's favour
    if ruled:    # the owner's tier stays, only capped; no new dispute
        final, capped_from, reasons = grading.cap_tier(ruled, facts, rules)
        skeptic = grading.cap_tier(res["tier"], facts, rules)[0]
        store.set_blind_grade(conn, item["id"], tier=skeptic, red_flags=flags, note=res["note"],
                              final_tier=final, disputed=bool(item["disputed"]),
                              dispute_reason=item.get("dispute_reason"), raw_tier=res["tier"],
                              capped_from=capped_from, cap_reasons=reasons,
                              verify_version=PROMPT_VERSION, keep_state=True)
        if not item.get("ruled_tier"):    # the skeptic tier is set now: keep the ruling recognisable
            conn.execute("UPDATE items SET ruled_tier=? WHERE id=?", (ruled, item["id"]))
            conn.commit()
        return False
    pair = grading.grade_pair(judge_raw, res["tier"], facts, rules, item["quote_check"])
    store.set_blind_grade(conn, item["id"], tier=pair["skeptic_tier"], red_flags=flags, note=res["note"],
                          final_tier=pair["final_tier"], disputed=pair["disputed"],
                          dispute_reason=pair["dispute_reason"], raw_tier=res["tier"],
                          capped_from=pair["capped_from"], cap_reasons=pair["cap_reasons"],
                          verify_version=PROMPT_VERSION, keep_state=True)
    return bool(pair["disputed"]) and not item["disputed"]


def apply(conn, payload: str, topic=None) -> dict:
    waiting = {interfaces.opaque_id(it["id"]): it for it in _selected(conn, topic)}
    accepted, rejected = contracts.check_payload(STAGE, payload, set(waiting))
    applied, disputed = 0, []
    for res in accepted:
        errors = verify._flag_errors(topic, res)
        if errors:
            rejected.append({"id": res["id"], "errors": errors})   # stays queued
            continue
        if _grade(conn, waiting[res["id"]], res, topic):
            disputed.append(res["id"])
        applied += 1
    return {"applied": applied, "rejected": rejected, "disputed": disputed}


def run_api(conn, backend, topic) -> dict:
    processed, rejected, disputed = 0, [], []
    for it in _selected(conn, topic):
        oid = interfaces.opaque_id(it["id"])
        prompt = verify.PROMPT.format(rules=verify._rules(topic), published=it["published"] or "(unknown)",
                                      title=it["title"] or "(no title)",
                                      full_text=(it["full_text"] or "")[:verify.TEXT_LIMIT])
        res, errors = contracts.ask_llm(backend, STAGE, oid, prompt, max_tokens=300)
        if not errors:
            errors = verify._flag_errors(topic, res)
        if errors:
            rejected.append({"id": oid, "errors": errors})   # stays queued, retried next run
            continue
        if _grade(conn, it, res, topic):
            disputed.append(oid)
        processed += 1
    return {"processed": processed, "rejected": rejected, "disputed": disputed}
