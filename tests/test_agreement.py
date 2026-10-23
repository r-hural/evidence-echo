import pytest

from evidence_echo.core import agreement, knowledge

TEXT = ("We moved 40 sites to the new template in March.\n"
        "Organic clicks rose by 12% over the “following” eight weeks — a clear lift.")
REASONS = knowledge.PAIR_REASONS


def test_quote_found_exact_and_normalised():
    assert agreement.quote_found("Organic clicks rose by 12%", TEXT)
    assert agreement.quote_found("in March. Organic   clicks", TEXT)            # whitespace and line break
    assert agreement.quote_found('the "following" eight weeks - a clear lift', TEXT)  # curly quotes, dash


def test_quote_found_rejects_a_paraphrase():
    assert not agreement.quote_found("clicks went up by twelve percent", TEXT)
    assert not agreement.quote_found("organic clicks rose by 12%", TEXT)         # case matters, as in grading


def test_quote_found_rejects_a_bare_number_and_short_quotes():
    assert not agreement.quote_found("12%", TEXT)
    assert not agreement.quote_found("clicks rose", TEXT)                        # two words
    assert agreement.quote_found("clicks rose by", TEXT)                         # three words


@pytest.mark.parametrize("quote, text", [(None, TEXT), (12, TEXT), ("", TEXT), ("   ", TEXT),
                                         ("Organic clicks rose", None), ("Organic clicks rose", "")])
def test_quote_found_rejects_missing_values(quote, text):
    assert not agreement.quote_found(quote, text)


def test_agree_returns_the_first_answer():
    a = {"id": "p1", "reason": "different_population", "note": "first"}
    b = {"id": "p1", "reason": "different_population", "note": "second"}
    assert agreement.agree(a, b, "reason", REASONS) is a


def test_agree_none_when_the_answers_differ():
    a = {"id": "p1", "reason": "different_population"}
    b = {"id": "p1", "reason": "method_quality"}
    assert agreement.agree(a, b, "reason", REASONS) is None


@pytest.mark.parametrize("a, b", [(None, {"reason": "method_quality"}),
                                  ({"reason": "method_quality"}, None),
                                  (None, None),
                                  ({"note": "no reason"}, {"note": "no reason"}),
                                  ("method_quality", "method_quality")])
def test_agree_none_when_an_answer_is_missing(a, b):
    assert agreement.agree(a, b, "reason", REASONS) is None


@pytest.mark.parametrize("value", ["because", "", None, 3, ["method_quality"]])
def test_agree_none_for_a_value_outside_the_choices(value):
    a, b = {"reason": value}, {"reason": value}
    assert agreement.agree(a, b, "reason", REASONS) is None


def test_check_every_named_quote():
    answer = {"quote_a": "Organic clicks rose by 12%", "quote_b": "rankings fell for nine sites"}
    other = "In May rankings fell for nine sites after the update."
    assert agreement.check(answer, {"quote_a": TEXT, "quote_b": other})
    assert not agreement.check(answer, {"quote_a": TEXT, "quote_b": TEXT})     # quote_b not in TEXT
    assert not agreement.check({"quote_a": "Organic clicks rose by 12%"},
                               {"quote_a": TEXT, "quote_b": other})             # quote_b missing


def test_check_needs_an_answer():
    assert not agreement.check(None, {"quote_a": TEXT})
    assert agreement.check({"note": "x"}, {})                                   # nothing to check


def test_vocabularies_match_the_spec():
    assert not hasattr(knowledge, "METRICS") and not hasattr(knowledge, "DIRECTIONS")   # per-topic lists now
    assert knowledge.PAIR_REASONS == ("different_population", "different_timeframe",
                                      "different_measure", "different_intervention",
                                      "method_quality", "genuine_conflict", "not_comparable")
    assert knowledge.PRIMARY_VERDICTS == ("faithful", "overstated", "direction_flipped",
                                          "wrong_metric", "cherry_picked")
