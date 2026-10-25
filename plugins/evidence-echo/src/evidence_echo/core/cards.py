"""Study card: the short text of one study that gets embedded for candidate matching."""
import json

CARD_VERSION = "card-v1"
CARD_MAX = 2000


def _load(raw) -> dict:
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def study_card(item: dict) -> str | None:
    """Card text from facts, provenance and title; None when there is nothing to embed."""
    facts = _load(item["facts"])
    prov = _load(item["provenance"])
    editions = facts.get("editions")
    editions = " ".join(str(e) for e in editions) if isinstance(editions, list) else _text(editions)
    lines = [
        _text(prov.get("conducted_by")),
        " ".join(p for p in (_text(facts.get("series")), editions) if p),
        _text(facts.get("what_was_tested")),
        _text(facts.get("findings")),
        _text(facts.get("research_timeframe")),
        _text(facts.get("sample_scale")),
        _text(item["title"]),
    ]
    card = "\n".join(line for line in lines if line)
    return card[:CARD_MAX] if card else None
