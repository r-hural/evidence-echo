# tests/test_report_golden.py
"""Characterization: the Markdown report, byte for byte, captured before SP5a moved it to render/.

The golden files under tests/fixtures/report_golden/ were written once from the pre-SP5a code
(REPORT_GOLDEN_WRITE=1 creates missing files, it never overwrites one). Never edit or regenerate
them, with one exception: SP3b-T regenerated the `.md` files once to add one `Theme:` line per entry (now `Subtopic:`, glossary rename);
that diff holds nothing else; the questions work regenerated only those `Subtopic:` lines (a study not
grouped yet shows `Subtopic: <name>` alone). Any other difference is a regression in render/markdown.py or render/model.py.
"""
import dataclasses
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evidence_echo.core import config, interfaces, store, topics
from evidence_echo.stages import judge, match, report, verify
from tests.test_judge import SENTENCE, TEXT, VERDICT, _seed_extracted

SEO_SUBTOPICS = topics.load_template("seo", config.TEMPLATES_DIR).subtopics
GOLDEN = Path(__file__).parent / "fixtures" / "report_golden"
NOW = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)
T2 = "Rankings fell 9% across forty sites in March."
T3 = "Click-through rose 31% on forty test pages in May."
T4 = "Organic sessions grew 17% across 300 blog posts."


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(report, "datetime", _Frozen)
    monkeypatch.setattr(store, "_now", lambda: NOW.isoformat())


def _study(db, n, *, text, numbers, quotes, primary, skeptic, flags=(), note="skeptic note",
           excerpt=False, facts=None, **over):
    store.seed_subtopics(db, SEO_SUBTOPICS)
    iid = _seed_extracted(db, url=f"https://example.com/g{n}", text=text)
    if excerpt:
        store.set_item_fields(db, iid, text_source="excerpt")
    f = dict(VERDICT["facts"], match_keys={"key_numbers": list(numbers)}, **(facts or {}))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid), facts=f,
                                                 primary_url=primary, quotes=quotes, **over)]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [{"id": interfaces.opaque_id(iid), "tier": skeptic,
                                              "red_flags": list(flags), "note": note}]}))
    return store.get_item(db, iid)["study_id"]


def _check(name: str, db, out: dict) -> None:
    text = Path(out["path"]).read_text(encoding="utf-8")
    entries = [[r["study_id"], r["title"]] for r in db.execute(
        "SELECT study_id, title FROM report_entries ORDER BY rowid")]
    for suffix, actual in ((".md", text), (".entries.json", json.dumps(entries) + "\n")):
        path = GOLDEN / (name + suffix)
        if not path.exists() and os.environ.get("REPORT_GOLDEN_WRITE") == "1":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(actual, encoding="utf-8", newline="\n")
        assert path.exists(), f"missing golden file {path} (run once with REPORT_GOLDEN_WRITE=1)"
        assert actual == path.read_text(encoding="utf-8"), f"{path.name} differs from the golden file"


def test_golden_new_and_disputed(db, tmp_path, topic, frozen):
    s1 = _study(db, 1, text=TEXT, numbers=["4.2%", "12000"],
                quotes=[{"number": "4.2%", "quote": SENTENCE}], primary="https://one.example.com/a",
                skeptic="A", flags=["no_baseline", "confounded_window"],
                provenance={"conducted_by": "Acme", "relation": "vendor",
                            "conflict_of_interest": "Acme sells the tool"})
    _study(db, 2, text=T2, numbers=["9%"], quotes=None, primary="https://two.example.com/b",
           skeptic="A")                                          # no_quote dispute, no entry
    s3 = _study(db, 3, text=T3, numbers=["31%"], quotes=[{"number": "31%", "quote": T3}],
                primary="https://three.example.com/c", skeptic="C", note="no control group",
                facts={"series": "Alpha Report", "editions": ["2025"]})   # grade_gap dispute
    s4 = _study(db, 4, text=T4, numbers=["17%"], quotes=[{"number": "17%", "quote": T4}],
                primary="https://four.example.com/d", skeptic="C", excerpt=True,
                facts={"has_controls": False, "metric": None, "baseline": "Control Group"})
    results = [{"study_id": s1, "title": "Title tags: +4.2% clicks", "summary": "Split test."},
               {"study_id": s3, "title": "Alpha: +31% CTR", "summary": "Survey."},
               {"study_id": s4, "title": "Rejected: no summary"}]
    out = report.render(db, json.dumps({"results": results}), tmp_path, "en", topic)
    _check("new_and_disputed", db, out)


def test_golden_seen_new_and_warnings(db, tmp_path, topic, frozen):
    s1 = _study(db, 1, text=TEXT, numbers=["4.2%", "12000"], quotes=VERDICT["quotes"],
                primary="https://one.example.com/a", skeptic="A")
    report.render(db, json.dumps({"results": [{"study_id": s1, "title": "First title",
                                               "summary": "S"}]}), tmp_path, "en", topic)
    _study(db, 5, text=TEXT, numbers=["4.2%", "12000"], quotes=VERDICT["quotes"],
           primary="https://one.example.com/a", skeptic="C", note="no control group")
    _study(db, 6, text=TEXT, numbers=["4.2%", "12000"], quotes=VERDICT["quotes"],
           primary="https://one.example.com/a", skeptic="A")
    s7 = _study(db, 7, text=T2, numbers=["9%"], quotes=[{"number": "9%", "quote": T2}],
                primary="https://two.example.com/b", skeptic="B", flags=["cherry_pick"],
                facts={"series": "Zeta Survey"})
    for _ in range(5):
        store.record_fetch(db, "Dead", "rss", new=0, error="HTTP 404")
    store.record_fetch(db, "Flaky", "rss", new=0, error="timeout")
    t = dataclasses.replace(topic, sources=[{"type": "rss", "name": "Dead", "url": "https://d/"},
                                            {"type": "rss", "name": "Flaky", "url": "https://f/"}])
    out = report.render(db, json.dumps({"results": [
        {"study_id": s7, "title": "Rankings: -9%", "summary": "Forty sites."}]}), tmp_path, "en", t)
    _check("seen_new_and_warnings", db, out)


def test_no_new_studies_writes_no_report(db, tmp_path, topic, frozen):
    """The old "empty" golden: a run without new studies now writes nothing and says so."""
    out = report.render(db, '{"results": []}', tmp_path, "en", topic)
    assert out == {"path": None, "skipped": "no_new_studies",
                   "warning": "No new studies since the last report; no report was written."}
    assert not list(tmp_path.glob("*.md")) and store.list_reports(db) == []


T5 = "Impressions rose 12% on eighty pages in June."
T6 = "Bounce rate dropped 8% across fifty landing pages."


def test_golden_coverage_and_label_variants(db, tmp_path, topic, frozen):
    p0 = "https://zero.example.com/z"
    s0 = _study(db, 10, text=TEXT, numbers=["4.2%", "12000"], quotes=VERDICT["quotes"],
                primary=p0, skeptic="A")
    report.render(db, json.dumps({"results": [{"study_id": s0, "title": "Earlier study",
                                               "summary": "S"}]}), tmp_path, "en", topic)
    _study(db, 11, text=TEXT, numbers=["4.2%", "12000"], quotes=VERDICT["quotes"],
           primary=p0, skeptic="A")                      # seen-before study, joined, no dispute
    px = "https://x.example.com/x"                       # one new study, two items
    sx = _study(db, 12, text=T2, numbers=["9%"], quotes=[{"number": "9%", "quote": T2}],
                primary=px, skeptic="A")
    _study(db, 13, text=T2, numbers=["9%"], quotes=[{"number": "9%", "quote": T2}],
           primary=px, skeptic="B")                      # joined item, one tier lower
    sy = _study(db, 14, text=T5, numbers=["12%"], quotes=[{"number": "12%", "quote": T5}],
                primary="https://y.example.com/y", skeptic="B", tier="C")   # judge C, skeptic B
    sz = _study(db, 15, text=T6, numbers=["8%"], quotes=[{"number": "8%", "quote": T6}],
                primary=None, skeptic="A",
                facts={"research_timeframe": "", "baseline": None},
                provenance={"conducted_by": None, "relation": "unknown",
                            "conflict_of_interest": None})
    results = [{"study_id": sx, "title": "Rankings: -9% in March", "summary": "Forty sites."},
               {"study_id": sy, "title": "Impressions: +12%", "summary": "Eighty pages."},
               {"study_id": sz, "title": "Bounce: -8%", "summary": "Fifty pages."}]
    out = report.render(db, json.dumps({"results": results}), tmp_path, "en", topic)
    _check("coverage_and_label_variants", db, out)


def test_golden_disputed_only_no_notes(db, tmp_path, topic, frozen):
    s1 = _study(db, 20, text=T3, numbers=["31%"], quotes=[{"number": "31%", "quote": T3}],
                primary="https://three.example.com/c", skeptic="C", note="")   # grade_gap, no note
    out = report.render(db, json.dumps({"results": [
        {"study_id": s1, "title": "CTR: +31%", "summary": "Forty pages."}]}), tmp_path, "en", topic)
    _check("disputed_only_no_notes", db, out)
