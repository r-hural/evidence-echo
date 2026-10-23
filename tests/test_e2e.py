"""Full pipeline with fake LLM/embedder: fetch(seeded) -> ... -> report."""
import json
from pathlib import Path

from evidence_echo.core import store
from evidence_echo.sources import base
from evidence_echo.stages import triage, extract, judge, match, verify, embed, report
from tests.conftest import make_item, FakeBackend
from tests.test_embed import FakeEmbedder
from tests.test_judge import SENTENCE, VERDICT


def test_full_pipeline(db, tmp_path, monkeypatch, topic):
    store.insert_items(db, [make_item()])

    triage.run_api(db, FakeBackend(['{"pass": true, "reason": "numbers"}']), topic)
    # page text contains the sentence VERDICT quotes, so the judge's quote check passes
    html = f"<html><body><article><p>{SENTENCE} The control group stayed flat.</p></article></body></html>"

    class _Kind:
        def full_text(self, url, ctx):
            return base.FullText(extract.text_from_html(html, url), "ok")
    monkeypatch.setattr(extract, "get_kind", lambda t: _Kind())
    extract.run(db, base.FetchContext(cutoff=None, client=None, limiter=base.RateLimiter()))
    judge.run_api(db, FakeBackend([json.dumps(VERDICT)]), topic)
    match.run_api(db, FakeBackend([]), topic)
    verify.run_api(db, FakeBackend(
        ['{"tier": "A", "red_flags": [], "note": "ok"}']), topic)
    embed.run(db, FakeEmbedder())

    payload = json.loads(report.emit(db, topic, "en"))
    sid = payload["items"][0]["study_id"]
    path = Path(report.render(db, json.dumps({"results": [
        {"study_id": sid,
         "title": "Title-tag rewrites on 12k pages: +4.2% organic clicks",
         "summary": "Controlled split test, six weeks, ecommerce."}]}),
        tmp_path, "en", topic)["path"])

    text = path.read_text(encoding="utf-8")
    assert "[A · agency]" in text and "+4.2%" in text
    assert store.items_in_state(db, "reported")
    assert not store.items_in_state(db, "fetched")


def test_stack_overflow_case_one_study(db, tmp_path, topic):
    for n, url in enumerate(["https://news.example/so-ai-2025", "https://blog.example/ai-adoption"]):
        store.insert_items(db, [make_item(url=url, title=f"SO survey {n}")])
    for it in store.items_in_state(db, "fetched"):
        store.set_triage(db, it["id"], True, "numbers")
        store.set_full_text(db, it["id"], "Stack Overflow survey: AI use 44% in 2023, 79% in 2025.")
    facts = {"what_was_tested": "Developer AI tool use", "findings": "44% (2023) -> 79% (2025)",
             "research_timeframe": "2023-2025", "series": "Stack Overflow Developer Survey",
             "editions": ["2023", "2025"], "match_keys": {"key_numbers": ["44%", "79%"]}}
    judge.run_api(db, FakeBackend([
        json.dumps({"pass": True, "tier": "B", "facts": facts, "metric": "other", "entities": [], "direction": "positive",
                    "primary_url": "https://survey.stackoverflow.co/2025/"}),
        json.dumps({"pass": True, "tier": "B", "facts": facts, "primary_url": None,
                    "metric": "other", "entities": [], "direction": "positive"})]), topic)
    first = store.items_in_state(db, "judged")[0]["id"]
    match.fixed_pass(db)
    sid = store.get_item(db, first)["study_id"]
    out = match.run_api(db, FakeBackend([json.dumps({"same_as": f"s{sid}", "reason": "same survey"})]), topic)
    assert out["processed"] == 1
    assert {it["study_id"] for it in store.items_in_state(db, "matched")} == {sid}
    verify.run_api(db, FakeBackend(['{"tier": "A", "red_flags": [], "note": "ok"}'] * 2), topic)
    payload = json.loads(report.emit(db, topic, "en"))
    assert len(payload["items"]) == 1 and len(payload["items"][0]["sources"]) == 2
