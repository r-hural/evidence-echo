"""Two blind answers (SP3b): agreement on a fixed-vocabulary value and checked quotes. Pure, no I/O.

A question explanation line or a primary-source verdict is stored only when both answers agree
(`agree`) and the kept answer's quotes are found in their texts (`check`).
"""
from evidence_echo.core import grading


def quote_found(quote, text) -> bool:
    """At least MIN_QUOTE_WORDS words, and a substring of the text (both normalised as grading does)."""
    if not isinstance(quote, str) or not isinstance(text, str):
        return False
    norm = grading._norm(quote)
    return len(norm.split()) >= grading.MIN_QUOTE_WORDS and norm in grading._norm(text)


def agree(a, b, key: str, choices) -> dict | None:
    """`a` when both answers are objects whose `key` is one of `choices` and equal; else None."""
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return None
    value = a.get(key)
    if not isinstance(value, str) or value not in choices or b.get(key) != value:
        return None
    return a


def check(answer, quotes: dict[str, str]) -> bool:
    """Every named quote field of `answer` is found in its text: {"quote_a": text_a, ...}."""
    if not isinstance(answer, dict):
        return False
    return all(quote_found(answer.get(field), text) for field, text in quotes.items())
