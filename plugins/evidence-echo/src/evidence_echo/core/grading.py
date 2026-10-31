"""Grading rules (SP2): red-flag codes, tier caps, quote check, blind-grade comparison. Pure, no I/O."""
import hashlib
import json
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


STUDY_TYPES = {
    "controlled_test": "A/B, split or holdout test",
    "rct": "randomised controlled trial",
    "meta_analysis": "meta-analysis or systematic review",
    "observational": "cohort, correlation or dataset analysis",
    "survey": "survey or questionnaire",
    "case_series": "before/after test or case study",
    "anecdote": "personal or practitioner anecdote",
    "animal": "animal study",
    "lab": "in vitro, mechanism or simulation study",
    "benchmark": "benchmark run",
    "backtest": "backtest on historical data",
    "other": "none of the above",
}
PEER_REVIEW = ("reviewed", "preprint", "not_applicable", "unknown")
TEXT_BASIS = ("full", "abstract_only", "secondary")
FACT_VALUES = {"study_type": tuple(STUDY_TYPES), "peer_review": PEER_REVIEW, "text_basis": TEXT_BASIS}

FIELD_RED_FLAGS = {
    "benchmark_contamination": "test data likely seen in training",
    "surrogate_outcome": "a marker stands in for the outcome that matters",
    "animal_to_human": "animal or lab result presented as a human effect",
    "backtest_overfit": "strategy tuned on the data it is tested on",
    "small_sample": "too few subjects for the claim",
}
ALL_RED_FLAGS = {**RED_FLAGS, **FIELD_RED_FLAGS}

GRADE_LABEL_TEXT = {
    "controlled_test": "controlled test", "rct": "rct", "meta_analysis": "meta-analysis",
    "observational": "observational", "survey": "survey", "case_series": "case series",
    "anecdote": "anecdote", "animal": "animal", "lab": "lab", "benchmark": "benchmark",
    "backtest": "backtest", "other": "other",
    "reviewed": "peer reviewed", "preprint": "preprint", "not_applicable": "not peer reviewed",
    "unknown": "peer review unknown",
    "full": "full text", "abstract_only": "abstract only", "secondary": "secondary coverage",
}
CAP_TEXT = {
    "no_controls": "no control group", "no_sample": "no stated sample", "animal": "animal study",
    "lab": "lab study", "observational": "observational study", "preprint": "preprint",
    "abstract_only": "abstract only",
}

GENERIC_TIERS = {
    "A": "controlled: A/B or split test with a stated control group",
    "B": "large-scale data: correlation/dataset analysis with stated scale",
    "C": "case study: before/after with real numbers, no control group",
    "D": "practitioner anecdote: small measured experiment; anecdotes with numbers are valid D",
}

REGRADE_VERSION = "regrade-1"
REGRADE_UNAVAILABLE = "unavailable"
BLOCK_KEYS = ("tiers", "study_types", "caps", "labels", "red_flags", "disclaimer")
TIER_TEXT_MAX = 300
DISCLAIMER_MAX = 200


def generic() -> dict:
    return {"tiers": dict(GENERIC_TIERS), "study_types": list(STUDY_TYPES), "caps": {},
            "labels": [], "red_flags": [], "disclaimer": None}


def rules_errors(block) -> list[str]:
    """Every problem in a grading block, each naming its key."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["grading: must be an object"]
    errs = [f"grading.{k}: unknown key" for k in block if k not in BLOCK_KEYS]
    if "tiers" in block:
        tiers = block["tiers"]
        if not isinstance(tiers, dict):
            errs.append("grading.tiers: must be an object with keys A, B, C, D")
        else:
            errs += [f"grading.tiers.{t}: missing" for t in TIERS if t not in tiers]
            errs += [f"grading.tiers.{k}: not a tier" for k in tiers if k not in TIERS]
            for t in TIERS:
                text = tiers.get(t)
                if t in tiers and (not isinstance(text, str) or not text.strip()):
                    errs.append(f"grading.tiers.{t}: must be a non-empty string")
                elif isinstance(text, str) and len(text) > TIER_TEXT_MAX:
                    errs.append(f"grading.tiers.{t}: longer than {TIER_TEXT_MAX} characters")
    types = list(STUDY_TYPES)
    if "study_types" in block:
        given = block["study_types"]
        if not isinstance(given, list) or not all(isinstance(v, str) for v in given):
            errs.append("grading.study_types: must be a list of study types")
        else:
            errs += [f"grading.study_types: {v!r} is not a study type"
                     for v in given if v not in STUDY_TYPES]
            types = [v for v in given if v in STUDY_TYPES] + ["other"]
    if "caps" in block:
        caps = block["caps"]
        if not isinstance(caps, dict):
            errs.append("grading.caps: must be an object")
        else:
            for fact, by_value in caps.items():
                if fact not in FACT_VALUES:
                    errs.append(f"grading.caps.{fact}: not a fact (study_type, peer_review, text_basis)")
                elif not isinstance(by_value, dict):
                    errs.append(f"grading.caps.{fact}: must be an object")
                else:
                    allowed = types if fact == "study_type" else FACT_VALUES[fact]
                    for value, tier in by_value.items():
                        where = f"grading.caps.{fact}.{value}"
                        if value not in FACT_VALUES[fact]:
                            errs.append(f"{where}: {value!r} is not a {fact} value")
                        elif value not in allowed:
                            errs.append(f"{where}: {value!r} is not in grading.study_types")
                        if tier not in TIERS:
                            errs.append(f"{where}: {tier!r} is not a tier")
    if "labels" in block:
        labels = block["labels"]
        every = {v for values in FACT_VALUES.values() for v in values}
        if not isinstance(labels, list) or not all(isinstance(v, str) for v in labels):
            errs.append("grading.labels: must be a list of fact values")
        else:
            errs += [f"grading.labels: {v!r} is not a fact value" for v in labels if v not in every]
    if "red_flags" in block:
        flags = block["red_flags"]
        if not isinstance(flags, list) or not all(isinstance(v, str) for v in flags):
            errs.append("grading.red_flags: must be a list of field red-flag codes")
        else:
            errs += [f"grading.red_flags: {v!r} is not a field red flag" for v in flags
                     if v not in FIELD_RED_FLAGS]
    if "disclaimer" in block and block["disclaimer"] is not None:
        text = block["disclaimer"]
        if not isinstance(text, str):
            errs.append("grading.disclaimer: must be a string")
        elif "\n" in text or "\r" in text:
            errs.append("grading.disclaimer: must be one line")
        elif len(text.strip()) > DISCLAIMER_MAX:
            errs.append(f"grading.disclaimer: longer than {DISCLAIMER_MAX} characters")
    return errs


def effective(block) -> dict:
    """The validated block with defaults filled; the generic block for None."""
    if block is None:
        return generic()
    errs = rules_errors(block)
    if errs:
        raise ValueError("; ".join(errs))
    out = generic()
    if "tiers" in block:
        out["tiers"] = {t: block["tiers"][t].strip() for t in TIERS}
    if "study_types" in block:
        given = list(dict.fromkeys(block["study_types"]))
        out["study_types"] = given if "other" in given else given + ["other"]
    out["caps"] = {f: dict(v) for f, v in (block.get("caps") or {}).items() if v}
    out["labels"] = list(dict.fromkeys(block.get("labels") or []))
    out["red_flags"] = list(dict.fromkeys(block.get("red_flags") or []))
    text = block.get("disclaimer")
    out["disclaimer"] = text.strip() if isinstance(text, str) and text.strip() else None
    return out


def rules_of(topic) -> dict:
    return getattr(topic, "grading", None) or generic()


def cap_tier(tier, facts: dict, rules: dict | None = None) -> tuple:
    """A needs has_controls true; B needs a stated sample_scale; then the topic's caps (the lowest
    tier wins). Returns (tier, capped_from, cap_reasons)."""
    facts = facts if isinstance(facts, dict) else {}
    capped, reasons = tier, []
    if capped == "A" and facts.get("has_controls") is not True:
        capped = "B"
        reasons.append("no_controls")
    if capped == "B" and not stated(facts.get("sample_scale")):
        capped = "C"
        reasons.append("no_sample")
    if tier in TIERS:
        for fact, by_value in ((rules or {}).get("caps") or {}).items():
            value = facts.get(fact)
            limit = by_value.get(value) if isinstance(value, str) else None
            if limit in TIERS and limit > tier:  # "D" > "A": a lower grade
                capped = max(capped, limit)
                reasons.append(value)
    return capped, (tier if capped != tier else None), (reasons if capped != tier else [])


def grade_pair(judge_raw, skeptic_raw, facts: dict, rules: dict | None, quote_check) -> dict:
    """Cap both raw tiers with the topic's rules, then compare."""
    j_tier, j_from, j_reasons = cap_tier(judge_raw, facts, rules)
    if skeptic_raw is None:
        s_tier, s_from, s_reasons = None, None, []
        final, disputed, why = j_tier, False, None
    else:
        s_tier, s_from, s_reasons = cap_tier(skeptic_raw, facts, rules)
        final, disputed, why = compare(j_tier, s_tier, quote_check)
    if j_tier == final:
        capped_from, reasons = j_from, j_reasons
    elif s_tier == final:
        capped_from, reasons = s_from, s_reasons
    else:
        capped_from, reasons = None, []
    return {"judge_tier": j_tier, "judge_capped_from": j_from, "judge_reasons": j_reasons,
            "skeptic_tier": s_tier, "final_tier": final, "disputed": disputed,
            "dispute_reason": why, "capped_from": capped_from, "cap_reasons": reasons}


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rules_hash(rules: dict) -> str:
    return _hash({"tiers": rules["tiers"], "study_types": rules["study_types"],
                  "red_flags": rules["red_flags"], "version": REGRADE_VERSION})


def caps_hash(rules: dict) -> str:
    return _hash(rules["caps"])


def _flags(rules: dict | None) -> dict:
    return {**RED_FLAGS, **{c: FIELD_RED_FLAGS[c] for c in (rules or {}).get("red_flags", [])}}


def payload_rules(rules: dict) -> dict:
    return {"tiers": dict(rules["tiers"]), "red_flags": _flags(rules)}


def tier_rules_text(rules: dict) -> str:
    return "\n".join(f"- Tier {t}: {rules['tiers'][t]}" for t in TIERS)


def grade_labels(facts: dict, rules: dict) -> list[str]:
    facts = facts if isinstance(facts, dict) else {}
    have = {facts.get(f) for f in FACT_VALUES}
    return [GRADE_LABEL_TEXT[v] for v in rules.get("labels", []) if v in have and v in GRADE_LABEL_TEXT]


def cap_text(reasons: list[str]) -> str:
    return ", ".join(CAP_TEXT.get(r, GRADE_LABEL_TEXT.get(r, r)) for r in reasons)


def fact_numbers(facts) -> list[str]:
    match_keys = facts.get("match_keys") if isinstance(facts, dict) else None
    values = match_keys.get("key_numbers") if isinstance(match_keys, dict) else None
    return identity.key_numbers(values if isinstance(values, list) else [])


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_QUOTES)).strip()


def quote_found(quote, text: str) -> bool:
    """A quote of at least MIN_QUOTE_WORDS words that sits in the article text (curly quotes, dashes and
    whitespace ignored)."""
    if not isinstance(quote, str):
        return False
    norm = _norm(_minus(quote))
    return len(norm.split(" ")) >= MIN_QUOTE_WORDS and norm in _norm(_minus(text or ""))


# Numbers written in words ("four in five", "half", "two thirds", "twenty"): read only after the
# digit check failed, so identity.key_numbers (study fingerprints) stays untouched.
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_WORD = "|".join(sorted(_WORDS, key=len, reverse=True))
_RATIO = re.compile(rf"\b({_WORD}|\d+) (?:in|out of) ({_WORD}|\d+)\b", re.IGNORECASE)
# "half" of a period ("the first half of 2024") is no result
_FRACTION = re.compile(r"\b(?:(a|one|two|three) (third|quarter)s?|"
                       r"(?<!first )(?<!second )(?<!latter )(?<!other )(half))\b", re.IGNORECASE)
_BIG = re.compile(r"\b(?:a|one) (hundred|thousand)\b", re.IGNORECASE)
_CARDINAL = re.compile(rf"\b({_WORD})\b(\s*(?:%|per ?cent\b))?", re.IGNORECASE)
_PERCENT_WORD = re.compile(r"(?<=\d)\s*per ?cent\b", re.IGNORECASE)  # "4.2 percent" -> "4.2%"
_FRACTION_PARTS = {"third": 3, "quarter": 4}
_FRACTION_COUNT = {"a": 1, "one": 1, "two": 2, "three": 3}


def _word_value(token: str) -> float:
    token = token.lower()
    return float(token) if token.isdigit() else float(_WORDS[token])


def _word_numbers(quote: str) -> list[tuple[bool, float]]:
    """(is percent, value) for each number written in words in `quote`."""
    out = []
    for m in _RATIO.finditer(quote):
        whole = _word_value(m.group(2))
        if whole:
            out.append((True, _word_value(m.group(1)) / whole * 100))
    for m in _FRACTION.finditer(quote):
        if m.group(3):
            out.append((True, 50.0))
        else:
            out.append((True, _FRACTION_COUNT[m.group(1).lower()]
                        / _FRACTION_PARTS[m.group(2).lower()] * 100))
    for m in _BIG.finditer(quote):
        out.append((False, 100.0 if m.group(1).lower() == "hundred" else 1000.0))
    rest = _FRACTION.sub(" ", _RATIO.sub(" ", quote))
    out.extend((bool(m.group(2)), _word_value(m.group(1))) for m in _CARDINAL.finditer(rest))
    return out


def _word_match(number: str, quote: str) -> bool:
    pct = number.endswith("%")
    try:
        wanted = float(number.rstrip("%"))
    except ValueError:
        return False
    for is_pct, value in _word_numbers(quote):
        if is_pct != pct:
            continue
        if pct and wanted in (round(value, 2), round(value, 1), float(round(value))):
            return True  # a third is 33.33, 33.3 or 33
        if not pct and wanted == value:
            return True
    return False


def _verified(number: str, quotes: list, text: str) -> bool:
    for q in quotes:
        if not (isinstance(q, dict) and isinstance(q.get("number"), str)
                and isinstance(q.get("quote"), str)):
            continue
        quote = _norm(q["quote"])
        if (number in identity.key_numbers([q["number"]])
                and len(quote.split(" ")) >= MIN_QUOTE_WORDS
                and quote in text
                and (number in identity.key_numbers([quote])
                     or number in identity.key_numbers([_PERCENT_WORD.sub("%", quote)])
                     or _word_match(number, quote))):
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


def red_flag_list(rules: dict | None = None) -> str:
    return "\n".join(f"- {code}: {meaning}" for code, meaning in _flags(rules).items())


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


def _bare_unit(unit, text: str, start: int) -> bool:
    """A lone "b" that is not a unit: "3 b" without a currency sign ("$3 B" is billions, "3b"
    attached is too). A lone "s" stays seconds ("LCP fell to 2.3 s after the fix")."""
    if unit.group(1).lower() == "b":
        return unit.group(0)[0].isspace() and not (start and text[start - 1] in _CURRENCY)
    return False


_FELL = re.compile(r"\b(fell|fall|falls|drop|dropped|drops|declin\w*|decreas\w*|lost|loss|lower|down|"
                   r"dip\w*|shrank|shrunk|sank|slump\w*|reduc\w*)\b", re.IGNORECASE)
_ROSE = re.compile(r"\b(rose|rise|rises|grew|grow|grows|growth|increas\w*|gain\w*|higher|up|"
                   r"improv\w*|climb\w*|jump\w*|surg\w*|boost\w*)\b", re.IGNORECASE)
# a "," or "." before a digit is part of a number ("3.5%", "1,234"), not a clause end
_CLAUSE_END = re.compile(r"[,.](?!\d)|[;:]|\b(?:while|whereas|but|and|however)\b", re.IGNORECASE)


def _sign(text: str, start: int) -> int:
    """-1, +1 or 0 (unknown) for the number at `start`: its own sign, else the direction word
    nearest before it in the same clause."""
    if text[start] in "+-":
        return -1 if text[start] == "-" else 1
    before = text[:start]
    ends = list(_CLAUSE_END.finditer(before))
    clause = before[ends[-1].end():] if ends else before
    fell, rose = list(_FELL.finditer(clause)), list(_ROSE.finditer(clause))
    if fell and (not rose or fell[-1].start() > rose[-1].start()):
        return -1
    return 1 if rose else 0


def _numbers(text: str, keep_trivial: bool = False):
    """(match start, token end, (is percent, magnitude)) for each result number in `text`: its
    k/M multiplier and an adjacent scale word count; years and (unless `keep_trivial`) trivial
    numbers are left out."""
    out = []
    for m in identity._NUMBER.finditer(text):
        sign, digits, mult, pct = m.groups()
        end, scale = m.end(), 1.0
        unit = None if pct else _UNIT.match(text, m.end())
        if unit and _bare_unit(unit, text, m.start()):
            unit = None
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
    plain, value = _minus(quote), _minus(value)
    hits = [(start, end) for start, end, key in _numbers(plain) if key in wanted]
    if len(hits) > 1:  # the same magnitude twice: take the one with the sign the value has
        want = _sign(value, _numbers(value)[0][0])
        hits = [h for h in hits if _sign(plain, h[0]) == want] or hits if want else hits
    for start, end in hits[:1]:
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
