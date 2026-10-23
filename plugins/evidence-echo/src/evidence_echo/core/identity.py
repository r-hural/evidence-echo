"""Study identity helpers (pure): series keys, key-number fingerprints, candidate ranking."""
import re

MAX_CANDIDATES = 5
_YEAR = re.compile(r"(?<!\d)(19[9]\d|20\d\d)(?!\d)")
_NUMBER = re.compile(r"(?<![\w.%])([+-]?)(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
                     r"(?:\s*([kKmM])(?![A-Za-z]))?(\s*%)?")
_TRIVIAL = {"0", "1", "0%", "100%"}


def series_key(name) -> str:
    """'Stack Overflow Developer Survey 2025' -> 'stack overflow developer survey'."""
    if not isinstance(name, str):
        return ""
    return " ".join(re.findall(r"[^\W_]+", _YEAR.sub(" ", name.lower())))


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def key_numbers(values) -> list[str]:
    """Normalised result numbers: '12k' -> '12000', '44 %' -> '44%'; years and trivia dropped."""
    out = set()
    for value in values or []:
        if value is None or isinstance(value, bool):
            continue
        for sign, digits, mult, pct in _NUMBER.findall(str(value)):
            number = float(digits.replace(",", ""))
            if mult:
                number *= 1000 if mult.lower() == "k" else 1_000_000
            if not pct and not mult and digits.isdigit() and 1990 <= number <= 2099:
                continue  # a year is an edition, not a result
            text = ("-" if sign == "-" else "") + _fmt(number) + ("%" if pct else "")
            if text not in _TRIVIAL:
                out.add(text)
    return sorted(out)


def qualifying(series: str, numbers: list[str], pool: list[dict]) -> list[dict]:
    """Every pool entry sharing the series or a number: same series first, most shared, newest."""
    wanted, scored = set(numbers), []
    for cand in pool:
        same = bool(series) and cand["series_key"] == series
        shared = len(wanted & set(cand["numbers"]))
        if same or shared:
            scored.append(((not same, -shared, -cand["order"]), cand))
    scored.sort(key=lambda pair: pair[0])
    return [cand for _, cand in scored]


def rank_candidates(series: str, numbers: list[str], pool: list[dict]) -> list[dict]:
    """The best MAX_CANDIDATES of qualifying()."""
    return qualifying(series, numbers, pool)[:MAX_CANDIDATES]
