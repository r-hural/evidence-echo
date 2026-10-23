# tests/html_fixtures.py
"""Builders for render.model objects, for the pure HTML renderer tests (no database)."""
from html.parser import HTMLParser

from evidence_echo.render.model import Entry, Link, Report, SeenEntry, Topic, TopicStudy


def view(**over) -> dict:
    """A grading view with every key the report shows; `provenance` merges into the default."""
    base = {"flags": [], "provenance": {"conducted_by": None, "relation": None,
                                        "conflict_of_interest": None},
            "quote_check": "ok", "missing_numbers": [], "capped_from": None,
            "judged_from": "full_text", "metric": None, "baseline": None, "gap_note": None}
    provenance = over.pop("provenance", None)
    base.update(over)
    if provenance:
        base["provenance"] = {**base["provenance"], **provenance}
    return base


def entry(study_id: int = 1, **over) -> Entry:
    base = dict(study_id=study_id, title=f"Study {study_id}: title rewrites, +4.2% clicks",
                summary="A split test on 12k pages over six weeks.", tier="B", disputed=False,
                skeptic_tiers=[], no_quote_only=False, dispute_reason=None, skeptic_notes=[],
                view=view(), series=None, editions=[], research_timeframe="2026-08",
                published="2026-08-10T00:00:00+00:00", primary_url="https://example.com/study",
                coverage=[Link("Example Blog", f"https://example.com/post-{study_id}")])
    base.update(over)
    return Entry(**base)


def seen(study_id: int = 9, **over) -> SeenEntry:
    base = dict(study_id=study_id, first_title=f"Earlier study {study_id}",
                first_reported="2026-10-01T08:00:00+00:00",
                coverage=[Link("Other Blog", f"https://other.example/{study_id}")],
                disputed=False, skeptic_tiers=[], skeptic_notes=[])
    base.update(over)
    return SeenEntry(**base)


def report(**over) -> Report:
    base = dict(topic_id="seo", topic_name="SEO", date="2026-10-16", new=[entry(1)],
                disputed=[], seen=[], source_warnings=[])
    base.update(over)
    return Report(**base)


def topic_study(e: Entry, first: str = "2026-10-01T08:00:00+00:00",
                last: str = "2026-10-16T08:00:00+00:00", reported_on=None, reports=None) -> TopicStudy:
    days = reported_on or [first[:10], last[:10]]
    return TopicStudy(entry=e, first_reported=first, last_reported=last, reported_on=days,
                      reports=len(days) if reports is None else reports)


def topic_model(studies=(), **over) -> Topic:
    base = dict(topic_id="seo", topic_name="SEO", generated_at="2026-10-16T09:00:00+00:00",
                studies=list(studies), sources=[], series=[])
    base.update(over)
    return Topic(**base)


class Refs(HTMLParser):
    """Collects every href and src, and filter controls not inside a <label>."""

    def __init__(self):
        super().__init__()
        self.hrefs, self.srcs, self.unlabelled = [], [], []
        self._labels = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "href" in a:
            self.hrefs.append(a["href"])
        if "src" in a:
            self.srcs.append(a["src"])
        if tag == "label":
            self._labels += 1
        if tag in ("input", "select") and not self._labels and not a.get("aria-label"):
            self.unlabelled.append(a.get("name"))

    def handle_endtag(self, tag):
        if tag == "label":
            self._labels -= 1


def refs(page: str) -> Refs:
    parser = Refs()
    parser.feed(page)
    return parser
