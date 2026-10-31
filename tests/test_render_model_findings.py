"""The render model in quotes mode: findings instead of questions; questions mode stays as it was."""
import json
from contextlib import closing
from datetime import datetime, timezone

from evidence_echo import api
from evidence_echo.core import config, knowledge, settings, store
from evidence_echo.render import model
from evidence_echo.stages import report
from tests.conftest import seed_topic_study
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)
V = store.FINDINGS_VERSION


def finding(text, checked=True, metric="clicks"):
    return {"text": text, "quote": f"quote of {text}", "quote_checked": checked, "metric": metric,
            "measure": "organic clicks"}


def put(db, sid, *texts, **kw):
    item = db.execute("SELECT id FROM items WHERE study_id=?", (sid,)).fetchone()[0]
    store.write_findings(db, sid, item, [finding(t, **kw) for t in texts], V)


def reported(db, *sids):
    store.record_report(db, "r.md", [(sid, f"Title {sid}", f"Summary {sid}") for sid in sids])


def test_questions_mode_model_has_no_findings(subtopiced, topic):
    a = add(subtopiced, question=q(subtopiced), by="A")
    put(subtopiced, a, "x")
    reported(subtopiced, a)
    t = model.build_topic(subtopiced, topic, NOW)
    assert t.mode == "questions" and t.findings == [] and t.finding_count == 0 and t.evidence
    assert all(ts.entry.findings is None and ts.entry.findings_state is None for ts in t.studies)
    rep = model.build_report(subtopiced, topic, {}, NOW)
    assert rep.mode == "questions" and all(e.findings is None for e in rep.new)


def test_quotes_model_rows_per_finding(subtopiced, topic):
    done, waiting, failed = add(subtopiced, by="A"), add(subtopiced, by="B"), add(subtopiced, by="C")
    put(subtopiced, done, "first", "second", checked=False)
    store.mark_findings_failed(subtopiced, failed, V)
    reported(subtopiced, done, waiting, failed)
    t = model.build_topic(subtopiced, topic, NOW, "quotes")
    assert t.mode == "quotes" and t.evidence == [] and t.waiting == 0
    [sub] = t.findings
    states = sorted((r.study_id, r.state, r.finding.text if r.finding else None) for r in sub.rows)
    assert states == sorted([(done, "done", "first"), (done, "done", "second"),
                             (waiting, "waiting", None), (failed, "failed", None)])
    assert len(sub.rows) == 4 and t.finding_count == 2
    first = next(r for r in sub.rows if r.finding and r.finding.text == "first").finding
    assert first.quote_checked is False and first.metric_name == "Clicks" and first.measure == "organic clicks"
    by_id = {ts.entry.study_id: ts.entry for ts in t.studies}
    assert [(by_id[s].findings_state, len(by_id[s].findings)) for s in (done, waiting, failed)] == [
        ("done", 2), ("waiting", 0), ("failed", 0)]


def test_quotes_report_entries_carry_findings(subtopiced, topic):
    a, b = add(subtopiced, by="A"), add(subtopiced, by="B")
    put(subtopiced, a, "one")
    rep = model.build_report(subtopiced, topic, {}, NOW, "quotes")
    assert rep.mode == "quotes"
    by_id = {e.study_id: e for e in rep.new}
    assert by_id[a].findings_state == "done" and [f.text for f in by_id[a].findings] == ["one"]
    assert by_id[b].findings_state == "waiting" and by_id[b].findings == []


def test_quotes_model_subtopic_order_and_unsorted_last(subtopiced, topic):
    back, title, none = add(subtopiced, subtopic="backlinks"), add(subtopiced), add(subtopiced, subtopic=None)
    old, new, undated = add(subtopiced), add(subtopiced), add(subtopiced)
    subtopiced.execute("UPDATE studies SET data_date='2024-01' WHERE id=?", (old,))
    subtopiced.execute("UPDATE studies SET data_date='2026-05' WHERE id=?", (new,))
    subtopiced.execute("UPDATE studies SET created_at='' WHERE id=?", (undated,))   # no date of any kind
    subtopiced.execute("UPDATE items SET published=NULL WHERE study_id=?", (undated,))
    subtopiced.commit()
    reported(subtopiced, back, title, none, old, new)
    subs = model.findings_subtopics(subtopiced)
    assert [s.key for s in subs] == ["title-tags-meta", "backlinks", knowledge.UNSORTED]
    assert [s.name for s in subs][-1] == "Unsorted"
    rows = [r.study_id for r in subs[0].rows]
    assert rows.index(new) < rows.index(old) and rows[-1] == undated   # newest data date first, undated last
    assert [r.study_id for r in subs[2].rows] == [none]


def test_quotes_model_metric_resolves_merge(subtopiced, topic):
    sid = add(subtopiced, by="A")
    put(subtopiced, sid, "ctr finding", metric="ctr")
    store.merge_metrics(subtopiced, "ctr", "clicks")
    [sub] = model.findings_subtopics(subtopiced)
    f = sub.rows[0].finding
    assert (f.metric_slug, f.metric_name) == ("clicks", "Clicks")


def test_quotes_entry_subtopic_has_no_question(subtopiced, topic):
    sid = add(subtopiced, question=q(subtopiced), by="A")
    loose = add(subtopiced, subtopic=None)
    rep = model.build_report(subtopiced, topic, {}, NOW, "quotes")
    by_id = {e.study_id: e for e in rep.new}
    assert by_id[sid].subtopic == {"key": "title-tags-meta", "name": "Title tags & meta", "question": None,
                                   "label": None, "independent": 0, "echo": None}
    assert by_id[loose].subtopic is None


def test_grade_labels_on_rows(subtopiced, topic):
    sid = add(subtopiced, by="A")
    subtopiced.execute("UPDATE studies SET study_type='observational' WHERE id=?", (sid,))
    subtopiced.commit()
    rules = {**topic.grading, "labels": ["observational"]}
    [sub] = model.findings_subtopics(subtopiced, rules)
    assert sub.rows[0].labels == ["observational"] and sub.rows[0].tier == "B"


def test_api_passes_grouping_mode_to_models(workspace, monkeypatch, tmp_path):
    settings.save_global(workspace, {"grouping_mode": "quotes"})
    cfg = config.load("seo")
    assert api._topic_model(cfg).mode == "quotes"
    assert api._topic_model(config.load("seo")).findings == []
    seed_topic_study()
    seen = {}
    original = report.render_with_model

    def spy(*args, **kw):
        seen["report"] = kw.get("mode")
        return original(*args, **kw)

    monkeypatch.setattr(report, "render_with_model", spy)
    vault = tmp_path / "vault"
    vault.mkdir()
    settings.save_global(workspace, {"grouping_mode": "quotes", "obsidian_dir": str(vault)})
    cfg = config.load("seo")
    api.render_report(cfg, json.dumps({"results": []}))
    assert seen["report"] == "quotes"
    modes = []
    monkeypatch.setattr(api.obsidian_render, "export", lambda t, v: modes.append(t.mode) or {})
    api.export_obsidian(config.workspace(), "seo")
    assert modes == ["quotes"]
