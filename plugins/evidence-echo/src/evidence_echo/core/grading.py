"""Grading rules (SP2): red-flag codes, tier caps, quote check, blind-grade comparison. Pure, no I/O."""
import re

from evidence_echo.core import identity

TIERS = ("A", "B", "C", "D")

RED_FLAGS = {
    "correlation_causation": "correlation presented as causation",
    "cherry_pick": "cherry-picked results",
    "anecdote_generalisation": "one anecdote generalised",
    "stale_data": "old data presented as current",
    "headline_overclaim": "headline claims more than the body shows",
    "misrepresented_primary": "misrepresents the original study",
    "no_baseline": "no baseline to compare against",
    "confounded_window": "something else changed during the test (e.g. a core update)",
    "vendor_metric": "a vendor's own metric used as the outcome",
    "multiple_metrics": "many metrics tested, the best one reported",
    "regression_to_mean": "regression to the mean",
    "selection_bias": "biased selection of sites or subjects",
}

MIN_QUOTE_WORDS = 3
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"',
                         "–": "-", "—": "-"})


def stated(value) -> bool:
    return isinstance(value, str) and value.strip() != "" and value.strip().lower() != "unstated"


def cap_tier(tier, facts: dict) -> tuple:
    """A needs has_controls true; B needs a stated sample_scale. Returns (tier, capped_from)."""
    facts = facts if isinstance(facts, dict) else {}
    capped = tier
    if capped == "A" and facts.get("has_controls") is not True:
        capped = "B"
    if capped == "B" and not stated(facts.get("sample_scale")):
        capped = "C"
    return capped, (tier if capped != tier else None)


def fact_numbers(facts) -> list[str]:
    match_keys = facts.get("match_keys") if isinstance(facts, dict) else None
    values = match_keys.get("key_numbers") if isinstance(match_keys, dict) else None
    return identity.key_numbers(values if isinstance(values, list) else [])


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_QUOTES)).strip()


def _verified(number: str, quotes: list, text: str) -> bool:
    for q in quotes:
        if not (isinstance(q, dict) and isinstance(q.get("number"), str)
                and isinstance(q.get("quote"), str)):
            continue
        quote = _norm(q["quote"])
        if (number in identity.key_numbers([q["number"]])
                and len(quote.split(" ")) >= MIN_QUOTE_WORDS
                and quote in text
                and number in identity.key_numbers([quote])):
            return True
    return False


def missing_numbers(quotes, key_numbers: list[str], text: str) -> list[str]:
    quotes = quotes if isinstance(quotes, list) else []
    norm_text = _norm(text or "")
    return [n for n in key_numbers if not _verified(n, quotes, norm_text)]


def check_quotes(quotes, key_numbers: list[str], text: str) -> str:
    if not key_numbers:
        return "none"
    missing = missing_numbers(quotes, key_numbers, text)
    if not missing:
        return "ok"
    return "partial" if len(missing) < len(key_numbers) else "none"


def compare(judge_tier, skeptic_tier, quote_check) -> tuple:
    """(final_tier, disputed, dispute_reason) for a blind grade against the judge's capped tier."""
    if judge_tier is None:
        return skeptic_tier, False, None
    if quote_check == "none":
        return judge_tier, True, "no_quote"
    gap = abs(TIERS.index(judge_tier) - TIERS.index(skeptic_tier))
    if gap >= 2:
        return judge_tier, True, "grade_gap"
    if gap == 1:
        return max(judge_tier, skeptic_tier), False, None  # "D" > "A": the lower grade
    return judge_tier, False, None


def red_flag_list() -> str:
    return "\n".join(f"- {code}: {meaning}" for code, meaning in RED_FLAGS.items())


# --- wave 4a: headline number -------------------------------------------------------------------
MINUS = "−"
HEADLINE_LABEL_MAX_WORDS = 6
_CURRENCY = "$€£"


def _minus(text: str) -> str:
    return text.replace(MINUS, "-")          # one char for one char: indices stay aligned


# A unit or scale word right after a number belongs to it ("$1.2 billion", "3x", "5 percentage points").
_UNIT = re.compile(r"\s?(percentage points?|positions?|points?|thousand|million|billion|trillion|"
                   r"bps?|pp|ms|mn|bn|x|×|b|s)(?!\w)", re.IGNORECASE)
_SCALE = {"thousand": 1e3, "million": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9,
          "trillion": 1e12}


def _numbers(text: str, keep_trivial: bool = False):
    """(match start, token end, (is percent, magnitude)) for each result number in `text`: its
    k/M multiplier and an adjacent scale word count; years and (unless `keep_trivial`) trivial
    numbers are left out."""
    out = []
    for m in identity._NUMBER.finditer(text):
        sign, digits, mult, pct = m.groups()
        end, scale = m.end(), 1.0
        unit = None if pct else _UNIT.match(text, m.end())
        if unit:
            end, scale = unit.end(), _SCALE.get(unit.group(1).lower(), 1.0)
        number = float(digits.replace(",", "")) * scale
        if mult:
            number *= 1000 if mult.lower() == "k" else 1_000_000
        if not pct and not mult and scale == 1.0 and digits.isdigit() and 1990 <= number <= 2099:
            continue  # a year is an edition, not a result
        if not keep_trivial and scale == 1.0 and identity.key_numbers([m.group(0)]) == []:
            continue  # trivial (0, 1, 0 %, 100 %)
        out.append((m.start(), end, (bool(pct), round(number, 9))))
    return out


def number_range(value) -> tuple[bool, float, float] | None:
    """(is percent, low, high) over the result numbers of a headline value ("30–40%" ->
    (True, 30.0, 40.0)), or None when it holds none. A % anywhere makes the whole value a percent.
    0 and 1 count here: "1–5%" is 1 to 5, not 5."""
    if not isinstance(value, str):
        return None
    keys = [key for _, _, key in _numbers(_minus(value), keep_trivial=True)]
    if not keys:
        return None
    magnitudes = [abs(m) for _, m in keys]
    return any(pct for pct, _ in keys), min(magnitudes), max(magnitudes)


def headline_token(value: str, quote: str) -> str | None:
    """The article's own spelling (sign, unit, scale word) of the one number named by `value`, or
    None. The magnitude includes the scale, so "$1.2B" does not match "$1.2 million"."""
    if not isinstance(value, str) or not isinstance(quote, str):
        return None
    wanted = {key for _, _, key in _numbers(_minus(value))}
    if len(wanted) != 1:
        return None
    for start, end, key in _numbers(_minus(quote)):
        if key in wanted:
            start -= 1 if start and quote[start - 1] in _CURRENCY else 0
            return quote[start:end].strip()
    return None


def check_headline(headline: dict, text: str) -> dict:
    """Check a judge headline against the article; `value` is the article's token when checked."""
    value, label, quote = (headline.get(k) for k in ("value", "label", "quote"))
    token = headline_token(value, quote)
    norm_quote = _norm(_minus(quote)) if isinstance(quote, str) else ""
    checked = bool(token and len(norm_quote.split(" ")) >= MIN_QUOTE_WORDS
                   and norm_quote in _norm(_minus(text or "")))
    return {"value": token if checked else value,
            "label": re.sub(r"\s+", " ", label).strip() if isinstance(label, str) else label,
            "quote": quote, "checked": checked}
