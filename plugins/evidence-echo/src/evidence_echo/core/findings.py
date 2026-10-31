"""Findings: one measured result of a study, with its verbatim quote (pure checks, no SQL).

The judge extracts 1-4 findings per new study; the findings stage (stages/findings.py) splits older
studies. A finding's quote is checked against the article text with grading's normalisation.
"""
import re

from evidence_echo.core import grading, store

KEYS = ("text", "quote", "metric", "measure")

RULES = (
    "finding_list: 1 to 4 findings, one per distinct outcome the study measured, the main one first; "
    "include results that did not change. Each: text (one line, at most 160 characters, the result in "
    "the study's words), quote (the sentence of the text that states it, copied exactly), metric (one "
    "slug from the metric list), measure (what was measured, free text, or null)")

STAGE_RULES = RULES.replace("1 to 4 findings", "0 to 4 findings") + (
    ". An empty list when the text states no measured result")


def _one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def shape_errors(raw, minimum: int) -> list[str]:
    """`raw` must be a list of `minimum`..4 objects with exactly text, quote, metric and measure."""
    if not isinstance(raw, list):
        return ["finding_list: a list of findings"]
    if not minimum <= len(raw) <= store.FINDINGS_MAX:
        return [f"finding_list: {minimum} to {store.FINDINGS_MAX} findings, got {len(raw)}"]
    errors = []
    for n, f in enumerate(raw):
        where = f"finding_list[{n}]"
        if not isinstance(f, dict) or set(f) != set(KEYS):
            errors.append(f"{where}: an object with exactly {', '.join(KEYS)}")
            continue
        text = f["text"]
        if not isinstance(text, str) or not _one_line(text):
            errors.append(f"{where}.text: a non-empty line of text")
        elif "\n" in text or "\r" in text:
            errors.append(f"{where}.text: one line, no line breaks")
        elif len(_one_line(text)) > store.FINDING_TEXT_MAX:
            errors.append(f"{where}.text: {len(_one_line(text))} characters; the limit is "
                          f"{store.FINDING_TEXT_MAX}")
        if not isinstance(f["quote"], str) or not f["quote"].strip():
            errors.append(f"{where}.quote: a non-empty string")
        if not isinstance(f["metric"], str):
            errors.append(f"{where}.metric: a slug from the metric list")
        if f["measure"] is not None and not isinstance(f["measure"], str):
            errors.append(f"{where}.measure: text or null")
    return errors


def metric_errors(conn, raw: list) -> list[str]:
    """Each finding's metric must be a slug of the topic's active metric list."""
    return [f"finding_list[{n}].metric: '{f.get('metric')}' is not in the topic's list"
            for n, f in enumerate(raw) if isinstance(f, dict)
            and store.metric_for_slug(conn, f.get("metric")) is None]


def checked(raw: list, text: str) -> list[dict]:
    """The findings as store.write_findings takes them: cleaned text, quote_checked from the article."""
    out = []
    for f in raw:
        measure = f.get("measure")
        out.append({"text": _one_line(f["text"]), "quote": f["quote"].strip(),
                    "quote_checked": grading.quote_found(f["quote"], text or ""),
                    "metric": f["metric"], "measure": _one_line(measure) if measure else None})
    return out
