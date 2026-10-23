import dataclasses
import json
from pathlib import Path

from evidence_echo.core import store, interfaces
from evidence_echo.stages import judge, match, report, verify
from tests.conftest import make_item, seed_new_study
from tests.test_judge import VERDICT, _seed_extracted


def _seed_verified(db, disputed=False):
    item_id = _seed_extracted(db)
    judge.apply(db, json.dumps(
        {"results": [dict(VERDICT, id=interfaces.opaque_id(item_id))]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(item_id), "tier": "C" if disputed else "A", "red_flags": [],   # judge A: two-tier gap = dispute
         "note": "check" if disputed else "ok"}]}))
    return item_id


def test_emit_groups_by_study_with_dates_and_dispute(db, topic):
    _seed_verified(db)
    payload = json.loads(report.emit(db, topic, "en"))
    assert payload["stage"] == "report"
    assert payload["topic"]["id"] == "seo"
    assert "Output language: en" in payload["instructions"]
    entry = payload["items"][0]
    assert entry["tier"] == "A"
    assert entry["research_timeframe"] == "2026-Q2"
    assert entry["publication_date"]
    assert entry["disputed"] is False
    assert entry["sources"][0]["url"]


def test_render_writes_markdown_and_marks_reported(db, tmp_path, topic):
    _seed_verified(db)
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    entries = {"results": [{
        "study_id": sid,
        "title": "Title-tag rewrites on 12k pages: +4.2% organic clicks",
        "summary": "Controlled 50/50 split test over 6 weeks; ecommerce product pages.",
    }]}
    out = report.render(db, json.dumps(entries), tmp_path, "en", topic)
    assert out["applied"] == 1 and out["rejected"] == []
    path = Path(out["path"])
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("# SEO Echo Report - ")
    assert "+4.2% organic clicks" in text
    assert "[A · agency]" in text                      # tier badge
    assert "2026-Q2" in text                  # research timeframe on date line
    assert store.items_in_state(db, "reported")


def test_render_disputed_section(db, tmp_path, topic):
    _seed_verified(db, disputed=True)
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    entries = {"results": [{"study_id": sid, "title": "T", "summary": "S"}]}
    text = Path(report.render(db, json.dumps(entries), tmp_path, "en", topic)["path"]).read_text(encoding="utf-8")
    assert "Disputed" in text
    assert "no control group" in text or "check" in text  # skeptic note shown


def test_render_never_overwrites_an_existing_report(db, tmp_path, topic):
    _seed_verified(db)
    sid = store.items_in_state(db, "verified")[0]["study_id"]
    first = report.render(db, json.dumps({"results": [
        {"study_id": sid, "title": "Real finding", "summary": "S"}]}), tmp_path, "en", topic)
    seed_new_study(db)
    second = report.render(db, '{"results": []}', tmp_path, "en", topic)
    seed_new_study(db)
    third = report.render(db, '{"results": []}', tmp_path, "en", topic)
    assert "Real finding" in Path(first["path"]).read_text(encoding="utf-8")
    assert Path(second["path"]).name.endswith("-report-2.md")
    assert Path(third["path"]).name.endswith("-report-3.md")
    assert Path(first["path"]).name.endswith("-report.md")


def test_report_flags_failing_sources(db, tmp_path, topic):
    for _ in range(5):
        store.record_fetch(db, "Dead", "rss", new=0, error="HTTP 404")
    store.record_fetch(db, "Flaky", "rss", new=0, error="timeout")
    t = dataclasses.replace(topic, sources=[{"type": "rss", "name": "Dead", "url": "https://d/"},
                                            {"type": "rss", "name": "Flaky", "url": "https://f/"}])
    seed_new_study(db)
    out = report.render(db, '{"results": []}', tmp_path, "en", t)
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "Dead (failing since " in text and "check or remove" in text
    assert "Flaky (timeout)" in text


def _verified(db, url, facts, primary=None):
    item_id = _seed_extracted(db, url=url)
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(item_id), facts=facts,
                                                  primary_url=primary)]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(item_id), "tier": "A", "red_flags": [], "note": "ok"}]}))
    return item_id


def _facts(series=None, editions=None, numbers=("1%",)):
    return dict(VERDICT["facts"], series=series, editions=editions,
                match_keys={"key_numbers": list(numbers)})


def test_seen_before_study_goes_to_more_coverage(db, tmp_path, topic):
    a = _verified(db, "https://e.com/a", _facts(), primary=VERDICT["primary_url"])
    sid = store.get_item(db, a)["study_id"]
    report.render(db, json.dumps({"results": [{"study_id": sid, "title": "First title", "summary": "S"}]}),
                  tmp_path, "en", topic)
    b = _seed_extracted(db, url="https://other.example/b")
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(b), facts=_facts())]}))
    match.fixed_pass(db)                                           # same primary url -> same study
    verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(b), "tier": "A", "red_flags": [], "note": "ok"}]}))
    assert json.loads(report.emit(db, topic, "en"))["items"] == []
    skipped = report.render(db, json.dumps({"results": []}), tmp_path, "en", topic)
    assert skipped["skipped"] == "no_new_studies" and skipped["path"] is None   # coverage alone: no report
    assert store.get_item(db, b)["state"] == "verified"                         # ... it waits for the next one
    seed_new_study(db)
    out = report.render(db, json.dumps({"results": []}), tmp_path, "en", topic)
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "## More coverage of earlier studies" in text
    assert "- First title (first reported " in text and "https://other.example/b" in text
    assert store.get_item(db, b)["state"] == "reported"
    assert db.execute("SELECT COUNT(*) FROM report_entries WHERE study_id=?", (sid,)).fetchone()[0] == 2


def test_series_line_and_grouping(db, tmp_path, topic):
    x = _verified(db, "https://e.com/x", _facts(numbers=("3%",)))
    s1 = _verified(db, "https://e.com/s1", _facts(series="Zeta Survey", editions=["2024"], numbers=("8%",)))
    s2 = _verified(db, "https://e.com/s2", _facts(series="Alpha Report", numbers=("9%",)))
    payload = json.loads(report.emit(db, topic, "en"))
    assert {i["series"] for i in payload["items"]} == {None, "Zeta Survey", "Alpha Report"}
    results = [{"study_id": i["study_id"], "title": f"T{i['study_id']}", "summary": "S"} for i in payload["items"]]
    text = Path(report.render(db, json.dumps({"results": results}), tmp_path, "en", topic)["path"]).read_text(encoding="utf-8")
    assert "*Part of: Zeta Survey (editions 2024)*" in text and "*Part of: Alpha Report*" in text
    sid = {k: store.get_item(db, v)["study_id"] for k, v in (("x", x), ("s1", s1), ("s2", s2))}
    order = [text.index(f"T{sid[k]}") for k in ("s2", "s1", "x")]
    assert order == sorted(order)                                   # Alpha, Zeta, then no series


def test_seen_before_dispute_is_shown(db, tmp_path, topic):
    a = _verified(db, "https://e.com/a", _facts(), primary=VERDICT["primary_url"])
    sid = store.get_item(db, a)["study_id"]
    report.render(db, json.dumps({"results": [{"study_id": sid, "title": "First title", "summary": "S"}]}),
                  tmp_path, "en", topic)
    b = _seed_extracted(db, url="https://other.example/b")
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(b), facts=_facts())]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(b), "tier": "C", "red_flags": [], "note": "no control group"}]}))
    seed_new_study(db)
    out = report.render(db, json.dumps({"results": []}), tmp_path, "en", topic)
    text = Path(out["path"]).read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("- First title"))
    assert "disputed" in line and "C" in line and "no control group" in line
    assert store.get_item(db, b)["state"] == "reported"


def test_new_and_seen_before_studies_render_together(db, tmp_path, topic):
    old = _verified(db, "https://e.com/old", _facts(numbers=("3%",)), primary=VERDICT["primary_url"])
    old_sid = store.get_item(db, old)["study_id"]
    report.render(db, json.dumps({"results": [{"study_id": old_sid, "title": "Old title", "summary": "S"}]}),
                  tmp_path, "en", topic)
    again = _seed_extracted(db, url="https://other.example/again")
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(again), facts=_facts(numbers=("3%",)))]}))
    match.fixed_pass(db)                                            # same primary url -> the old study
    verify.apply(db, json.dumps({"results": [
        {"id": interfaces.opaque_id(again), "tier": "A", "red_flags": [], "note": "ok"}]}))
    fresh = _verified(db, "https://e.com/fresh", _facts(numbers=("77%",)), primary="https://new.example/study")
    fresh_sid = store.get_item(db, fresh)["study_id"]
    payload = json.loads(report.emit(db, topic, "en"))
    assert [i["study_id"] for i in payload["items"]] == [fresh_sid]
    out = report.render(db, json.dumps({"results": [{"study_id": fresh_sid, "title": "Fresh title", "summary": "S"}]}),
                        tmp_path, "en", topic)
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "Fresh title" in text and "## More coverage of earlier studies" in text
    assert text.index("Fresh title") < text.index("## More coverage of earlier studies")
    assert "- Old title (first reported " in text and "https://other.example/again" in text
    assert store.get_item(db, again)["state"] == "reported" and store.get_item(db, fresh)["state"] == "reported"
