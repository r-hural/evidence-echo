# tests/test_primary_compare.py
"""SP3b-P: contract, emit, two blind answers, agreement, red flag. Network faked."""
import json

import pytest

from evidence_echo.core import contracts, interfaces, knowledge, store
from evidence_echo.stages import primary
from tests.conftest import FakeBackend
from tests.primary_fixtures import (ARTICLE_QUOTE, ORIGINAL_QUOTE, ORIGINAL_URL, SENTENCES,
                                    fake_ctx, original_html, report_item)


def _ready(db, sentences=SENTENCES, **over) -> str:
    """A report item whose original is fetched and cached; returns its opaque id."""
    iid = report_item(db, **over)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html(sentences)}))
    return interfaces.opaque_id(iid)


def answer(oid, verdict="overstated", quote_article=ARTICLE_QUOTE, quote_primary=ORIGINAL_QUOTE,
           note="The article presents a correlation as the cause.") -> dict:
    return {"id": oid, "verdict": verdict, "quote_article": quote_article,
            "quote_primary": quote_primary, "note": note}


def _file(*results) -> str:
    return json.dumps({"results": list(results)})


def _apply(db, a: list, b: list) -> dict:
    return primary.apply(db, _file(*a), _file(*b))


def _emit(db, topic) -> dict:
    """The originals are cached by _ready: the fake network is never asked again."""
    return json.loads(primary.emit(db, topic, ctx=fake_ctx({})))


def _row(db, oid):
    return store.primary_check(db, interfaces.rowid_from_opaque(oid))


def _flags(db, oid):
    return json.loads(store.get_item(db, interfaces.rowid_from_opaque(oid))["red_flags"])


def test_contract_accepts_only_known_verdicts():
    good = answer("i1")
    assert contracts.validate_result("primary", good) == []
    assert contracts.validate_result("primary", dict(good, verdict="accurate"))
    assert contracts.validate_result("primary", {k: v for k, v in good.items() if k != "quote_primary"})


def test_every_verdict_has_a_meaning():
    assert set(primary.VERDICT_MEANINGS) == set(knowledge.PRIMARY_VERDICTS)


def test_emit_carries_both_texts_and_the_choices(db, topic):
    oid = _ready(db)
    payload = _emit(db, topic)
    assert payload["stage"] == "primary" and payload["originals"]["ready"] == 1
    assert payload["choices"] == {"verdict": list(knowledge.PRIMARY_VERDICTS)}
    [item] = payload["items"]
    assert set(item) == {"id", "title", "findings", "quotes", "article_text", "original_text"}
    assert item["id"] == oid and item["findings"] == "+4.2% organic clicks on variant group"
    assert ORIGINAL_QUOTE in item["original_text"] and ARTICLE_QUOTE in item["article_text"]
    assert item["quotes"][0]["number"] == "4.2%"
    for verdict in knowledge.PRIMARY_VERDICTS:
        assert verdict in payload["instructions"]
    assert "untrusted" in payload["instructions"]


def _flat(text):
    return " ".join(text.split())


def test_the_not_comparable_rule_is_in_the_instructions(db, topic):
    _ready(db)
    instructions = _flat(_emit(db, topic)["instructions"])
    assert "original_text is not the publication of the study" in instructions
    assert 'start the note with "original not comparable"' in instructions


def test_the_not_comparable_rule_is_in_the_api_prompt_in_both_orders(db, topic):
    _ready(db)
    reply = json.dumps(answer(interfaces.opaque_id(1), verdict="faithful"))
    backend = FakeBackend([reply, reply])
    primary.run_api(db, backend, topic, ctx=fake_ctx({}))
    assert len(backend.prompts) == 2
    for prompt in backend.prompts:
        assert "original_text is not the publication of the study" in _flat(prompt)
        assert 'start the note with "original not comparable"' in _flat(prompt)


def test_emit_cuts_texts_to_15000_chars(db, topic):
    _ready(db, sentences=SENTENCES + ["Filler sentence number words here. " * 600])
    [item] = _emit(db, topic)["items"]
    assert len(item["original_text"]) == primary.TEXT_LIMIT


def test_emit_fetches_what_is_not_cached_yet(db, topic):
    oid = interfaces.opaque_id(report_item(db))
    calls = []
    payload = json.loads(primary.emit(db, topic, ctx=fake_ctx({ORIGINAL_URL: original_html()}, calls)))
    assert calls == [ORIGINAL_URL] and [i["id"] for i in payload["items"]] == [oid]


def test_two_agreeing_answers_with_found_quotes_are_stored(db):
    oid = _ready(db)
    out = _apply(db, [answer(oid)], [answer(oid, note="Overclaims.")])
    assert out == {"applied": 1, "agreed": 1, "undecided": 0, "rejected": []}
    row = _row(db, oid)
    assert (row["status"], row["verdict"], row["numbers"]) == ("agreed", "overstated", "ok")
    assert row["note"] == "The article presents a correlation as the cause."   # the first answer's
    assert (row["quote_article"], row["quote_primary"]) == (ARTICLE_QUOTE, ORIGINAL_QUOTE)
    assert json.loads(row["missing"]) == [] and row["primary_url"] == ORIGINAL_URL
    assert row["prompt_version"] == primary.PROMPT_VERSION
    assert _flags(db, oid) == ["misrepresented_primary"]
    assert primary.pending(db) == []                       # checked once


def test_different_verdicts_are_undecided(db):
    oid = _ready(db)
    assert _apply(db, [answer(oid)], [answer(oid, verdict="faithful")])["undecided"] == 1
    row = _row(db, oid)
    assert (row["status"], row["verdict"], row["note"], row["quote_article"]) == (
        "undecided", None, None, None)
    assert row["numbers"] == "ok"                           # the number check is code: kept
    assert _flags(db, oid) == []


@pytest.mark.parametrize("which", ["first", "second"])
def test_a_quote_missing_from_its_text_makes_it_undecided(db, which):
    oid = _ready(db)
    bad = answer(oid, quote_primary="the original never says this sentence")
    a, b = (bad, answer(oid)) if which == "first" else (answer(oid), bad)
    assert _apply(db, [a], [b])["undecided"] == 1
    assert _flags(db, oid) == []


def test_an_article_quote_taken_from_the_original_is_not_found(db):
    oid = _ready(db)
    swapped = answer(oid, quote_article=ORIGINAL_QUOTE)   # each quote must come from its own text
    assert _apply(db, [swapped], [swapped])["undecided"] == 1


def test_faithful_adds_no_red_flag(db):
    oid = _ready(db)
    _apply(db, [answer(oid, verdict="faithful")], [answer(oid, verdict="faithful")])
    assert _row(db, oid)["verdict"] == "faithful" and _flags(db, oid) == []


def test_the_red_flag_is_added_once(db):
    oid = _ready(db, flags=["misrepresented_primary", "no_baseline"])   # the skeptic said so too
    _apply(db, [answer(oid)], [answer(oid)])
    flags = _flags(db, oid)
    assert flags.count("misrepresented_primary") == 1 and "no_baseline" in flags


def test_the_check_never_changes_the_tier(db):
    oid = _ready(db)
    before = store.get_item(db, interfaces.rowid_from_opaque(oid))["evidence_tier"]
    _apply(db, [answer(oid, verdict="direction_flipped")], [answer(oid, verdict="direction_flipped")])
    assert store.get_item(db, interfaces.rowid_from_opaque(oid))["evidence_tier"] == before


def test_numbers_missing_from_the_original_are_recorded(db):
    changed = [s.replace("4.2%", "3.9%") for s in SENTENCES]
    oid = _ready(db, sentences=changed)
    quote = ORIGINAL_QUOTE.replace("4.2%", "3.9%")
    _apply(db, [answer(oid, quote_primary=quote)], [answer(oid, quote_primary=quote)])
    row = _row(db, oid)
    assert row["numbers"] == "partial" and json.loads(row["missing"]) == ["4.2%"]


def test_an_item_answered_in_one_file_only_stays_pending(db):
    oid = _ready(db)
    out = _apply(db, [answer(oid)], [])
    assert out["applied"] == 0
    assert out["rejected"] == [{"id": oid, "errors": ["answered in only one file"]}]
    assert _row(db, oid) is None and len(primary.pending(db)) == 1


def test_an_invalid_answer_is_rejected_and_the_item_stays_pending(db):
    oid = _ready(db)
    out = _apply(db, [answer(oid, verdict="accurate")], [answer(oid)])
    assert out["applied"] == 0
    [rej] = out["rejected"]
    assert rej["id"] == oid and rej["errors"][0].startswith("first file: verdict")
    assert len(primary.pending(db)) == 1


def test_an_id_not_awaiting_the_check_is_rejected(db):
    _ready(db)
    out = _apply(db, [answer("i999")], [answer("i999")])
    assert out["applied"] == 0 and {r["id"] for r in out["rejected"]} == {"i999"}


def test_a_payload_that_is_not_json_raises(db):
    _ready(db)
    with pytest.raises(contracts.ContractError):
        primary.apply(db, "not json", _file())


def test_api_mode_asks_twice_with_the_texts_in_both_orders(db, topic):
    oid = _ready(db)
    reply = json.dumps({k: v for k, v in answer(oid).items() if k != "id"})
    backend = FakeBackend([reply, reply])
    out = primary.run_api(db, backend, topic, ctx=fake_ctx({}))
    assert {k: out[k] for k in ("processed", "agreed", "undecided", "rejected")} == {
        "processed": 1, "agreed": 1, "undecided": 0, "rejected": []}
    assert out["originals"]["ready"] == 1
    first, second = backend.prompts
    assert first.index("Article text") < first.index("Original study text")
    assert second.index("Original study text") < second.index("Article text")
    assert _row(db, oid)["status"] == "agreed"


def test_api_mode_failure_leaves_the_item_pending(db, topic):
    oid = _ready(db)
    backend = FakeBackend(["not json"])
    out = primary.run_api(db, backend, topic, ctx=fake_ctx({}))
    assert out["processed"] == 0 and out["rejected"][0]["id"] == oid
    assert len(backend.prompts) == 1                        # no second call after a failed first
    assert len(primary.pending(db)) == 1


def test_prepare_restores_a_cleared_red_flag(db):
    oid = _ready(db)
    _apply(db, [answer(oid)], [answer(oid)])
    db.execute("UPDATE items SET red_flags='[]' WHERE id=?", (interfaces.rowid_from_opaque(oid),))
    db.commit()                                     # what reset --stage verify + verify leaves
    primary.prepare(db, fake_ctx({}))
    assert _flags(db, oid) == ["misrepresented_primary"]
