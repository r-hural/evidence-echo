import json

from evidence_echo.core import cards


def _item(facts, prov, title="T"):
    return {"facts": facts if facts is None or isinstance(facts, str) else json.dumps(facts),
            "provenance": prov if prov is None or isinstance(prov, str) else json.dumps(prov),
            "title": title}


FACTS = {"series": "SO Survey", "editions": ["2025"], "what_was_tested": "AI use",
         "findings": "44% use AI", "research_timeframe": "May 2025", "sample_scale": "n=49k"}


def test_card_field_order_and_skips_empty():
    prov = {"conducted_by": "Stack Overflow"}
    card = cards.study_card(_item(FACTS, prov))
    assert card.split("\n") == ["Stack Overflow", "SO Survey 2025", "AI use", "44% use AI",
                                "May 2025", "n=49k", "T"]
    facts = {k: v for k, v in FACTS.items() if k != "sample_scale"}
    assert "n=49k" not in cards.study_card(_item(facts, prov)).split("\n")


def test_card_capped():
    card = cards.study_card(_item({"findings": "x" * 5000}, None))
    assert len(card) == cards.CARD_MAX == 2000


def test_card_falls_back_to_title_and_empty_is_none():
    assert cards.study_card(_item("{}", None)) == "T"
    assert cards.study_card(_item("not json", None, title="")) is None
