"""Model fixtures for the Obsidian renderer: Topic values built directly, no database."""
from evidence_echo.render.model import Entry, Link, SourceSummary, Topic, TopicStudy


def view(**over) -> dict:
    base = {
        "flags": [],
        "provenance": {"conducted_by": None, "relation": None, "conflict_of_interest": None},
        "quote_check": "ok",
        "missing_numbers": [],
        "capped_from": None,
        "judged_from": "full_text",
        "metric": None,
        "baseline": None,
        "gap_note": None,
    }
    base.update(over)
    return base


def entry(study_id: int = 1, **over) -> Entry:
    base = dict(
        study_id=study_id, title=f"Study {study_id}: +4.2% clicks",
        summary="A split test on 12k pages.", tier="B", disputed=False, skeptic_tiers=[],
        no_quote_only=False, dispute_reason=None, skeptic_notes=[], view=view(), series=None,
        editions=[], research_timeframe="2026-Q2", published="2026-08-10T00:00:00+00:00",
        primary_url="https://example.com/study",
        coverage=[Link("Example Blog", f"https://example.com/post-{study_id}")],
    )
    base.update(over)
    base.setdefault("canonical_name", base["title"])  # slug source; tests pass "" to test the fallback
    return Entry(**base)


def study(study_id: int = 1, first: str = "2026-08-15", last: str | None = None,
          reported_on: list[str] | None = None, **over) -> TopicStudy:
    return TopicStudy(entry=entry(study_id, **over), first_reported=first,
                      last_reported=last or first, reported_on=reported_on or [first])


def topic(studies=(), sources=None, series=(), name: str = "SEO", topic_id: str = "seo") -> Topic:
    studies = list(studies)
    if sources is None:  # one summary per coverage source name, no disputes or red flags
        names = sorted({c.name for s in studies for c in s.entry.coverage})
        sources = [SourceSummary(n, [s.entry.study_id for s in studies
                                     if any(c.name == n for c in s.entry.coverage)], 0, 0)
                   for n in names]
    return Topic(topic_id=topic_id, topic_name=name, generated_at="2026-10-16T12:00:00+00:00",
                 studies=studies, sources=list(sources), series=list(series))
