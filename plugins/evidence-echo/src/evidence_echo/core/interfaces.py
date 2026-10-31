"""LLM backend protocol and the emit/apply JSON envelope shared by LLM stages.

Session mode contract:
  emit  -> {"stage": <name>, "topic": {"id", "name", "domain", "scope", "subtopics"}, "instructions": <str>, "items": [{"id": "i<rowid>", ...}]}
  apply <- {"results": [{"id": "i<rowid>", ...stage-specific fields...}]}
Item ids are opaque ("i" + rowid) so session judgment never sees raw URLs as
identifiers (prompt-injection hardening; content fields are clearly data).
apply payloads are validated by core/contracts.py before they touch the store.
"""
import json
from typing import Protocol

from evidence_echo.core import grading


class LLMBackend(Protocol):
    def complete(self, prompt: str, max_tokens: int = 500) -> str: ...


def opaque_id(rowid: int) -> str:
    return f"i{rowid}"


def rowid_from_opaque(oid: str) -> int:
    return int(oid.lstrip("i"))


def envelope(stage: str, instructions: str, items: list[dict], topic) -> str:
    return json.dumps(
        {"stage": stage,
         "topic": {"id": topic.id, "name": topic.name, "domain": topic.domain,
                   "scope": topic.scope, "subtopics": list(topic.subtopics),
                   "grading": grading.payload_rules(grading.rules_of(topic))},
         "instructions": instructions, "items": items},
        ensure_ascii=False, indent=2)


def parse_llm_json(raw_text: str) -> dict:
    """Parse a JSON object from an LLM response, stripping markdown fences."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)
