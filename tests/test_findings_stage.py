"""The findings backfill stage: queue, apply, failure marks, API mode, reset, report skill wiring."""
import json
from contextlib import closing
from pathlib import Path

from evidence_echo import api, cli
from evidence_echo.core import config, findings, settings, store
from evidence_echo.stages import findings as stage
from evidence_echo.stages import regrade
from tests.conftest import FakeBackend, seed_new_study
from tests.test_judge import SENTENCE, TEXT

V = store.FINDINGS_VERSION


def old_study(db, text=TEXT, excerpt=None):
    """A study as it was before release: its findings cleared."""
    iid = seed_new_study(db)
    sid = store.get_item(db, iid)["study_id"]
    db.execute("UPDATE items SET full_text=?, excerpt=COALESCE(?, excerpt) WHERE id=?", (text, excerpt, iid))
    db.commit()
    store.clear_findings(db, sid)
    return sid


def answer(sid, *listed):
    return json.dumps({"results": [{"id": f"s{sid}", "finding_list": list(listed)}]})


def f(text="Clicks rose 4.2%", quote=SENTENCE, metric="clicks", measure="organic clicks"):
    return {"text": text, "quote": quote, "metric": metric, "measure": measure}


def emit(db, topic, cap=40):
    return json.loads(stage.emit(db, topic, cap))


def test_emit_selects_batch_newest_first(db, topic):
    made = [old_study(db) for _ in range(5)]
    data = emit(db, topic, 2)
    assert [i["id"] for i in data["items"]] == [f"s{made[4]}", f"s{made[3]}"]
    assert data["stage"] == "findings" and data["remaining"] == 3 and data["pending"] == 5
    assert data["items"][0]["full_text"] == TEXT and "untrusted" in data["instructions"]
    assert findings.STAGE_RULES in data["instructions"]
    assert {"id", "name", "what_was_tested", "summary", "metric", "full_text"} <= set(data["items"][0])


def test_apply_writes_findings_and_progress(db, topic):
    sid = old_study(db)
    before = store.findings_progress(db, V)
    out = stage.apply(db, answer(sid, f(), f("Invented", "not in the article at all")))
    assert out["applied"] == 1 and out["rejected"] == []
    assert out["done"] == before["done"] + 1 and out["pending"] == before["pending"] - 1
    rows = store.study_findings(db, sid)
    assert [r["quote_checked"] for r in rows] == [1, 0]
    assert store.get_study(db, sid)["findings_version"] == V


def test_zero_findings_marks_failed(db, topic):
    sid = old_study(db)
    out = stage.apply(db, answer(sid))
    assert out["applied"] == 1 and out["pending"] == 0
    assert store.get_study(db, sid)["findings_version"] == "findings-1:failed"
    assert emit(db, topic)["items"] == []


def test_two_rejected_answers_mark_failed(db, topic):
    sid = old_study(db)
    out = stage.apply(db, answer(sid, f(metric="nonsense")))
    assert out["applied"] == 0 and out["rejected"][0]["id"] == f"s{sid}"
    assert [i["id"] for i in emit(db, topic)["items"]] == [f"s{sid}"]
    stage.apply(db, answer(sid, f(metric="nonsense")))
    assert store.get_study(db, sid)["findings_version"] == "findings-1:failed"
    assert emit(db, topic)["items"] == [] and stage.apply(db, answer(sid, f()))["applied"] == 0


def test_contract_rejection_counts_as_rejected_answer(db):
    sid = old_study(db)
    stage.apply(db, answer(sid, {"text": "x"}))
    assert store.get_study(db, sid)["findings_version"] == "findings-1:rejected"
    stage.apply(db, answer(sid, {"text": "x"}))
    assert store.get_study(db, sid)["findings_version"] == "findings-1:failed"


def test_textless_study_marked_failed_at_emit(db, topic):
    sid = old_study(db, text=None, excerpt="")
    assert emit(db, topic)["items"] == []
    assert store.get_study(db, sid)["findings_version"] == "findings-1:failed"
    assert store.findings_progress(db, V)["pending"] == 0


def test_excerpt_only_study_is_read_from_excerpt(db, topic):
    sid = old_study(db, text=None, excerpt=SENTENCE + " More text.")
    assert [i["full_text"] for i in emit(db, topic)["items"]] == [SENTENCE + " More text."]
    stage.apply(db, answer(sid, f()))
    assert store.study_findings(db, sid)[0]["quote_checked"] == 1


def test_run_api_respects_cap(db, topic):
    made = [old_study(db) for _ in range(3)]
    backend = FakeBackend([json.dumps({"finding_list": [f()]})] * 2)
    out = stage.run_api(db, backend, topic, cap=2)
    assert out["processed"] == 2 and out["rejected"] == [] and out["pending"] == 1
    assert len(backend.prompts) == 2
    assert all(TEXT in p and findings.STAGE_RULES in p for p in backend.prompts)
    assert store.study_findings(db, made[2]) and store.study_findings(db, made[1]) and not store.study_findings(db, made[0])


def test_backend_error_is_not_a_rejected_answer(db, topic):
    class Boom:
        def complete(self, prompt, max_tokens=500):
            raise RuntimeError("rate limit")
    sid = old_study(db)
    out = stage.run_api(db, Boom(), topic, cap=5)
    assert out["processed"] == 0 and out["rejected"][0]["errors"][0].startswith("backend error")
    assert store.get_study(db, sid)["findings_version"] is None
    bad = stage.run_api(db, FakeBackend(["not json"]), topic, cap=5)
    assert store.get_study(db, sid)["findings_version"] == "findings-1:rejected" and bad["processed"] == 0


def test_regrade_keeps_findings(db, topic):
    iid = seed_new_study(db)
    sid = store.get_item(db, iid)["study_id"]
    before = store.study_findings(db, sid)
    assert before
    db.execute("UPDATE items SET graded_rules=NULL WHERE id=?", (iid,))
    db.commit()
    data = json.loads(regrade.emit(db, topic))
    assert data["items"]
    regrade.apply(db, json.dumps({"results": [{"id": data["items"][0]["id"], "tier": "B", "study_type": "survey",
                                               "peer_review": "unknown", "text_basis": "full"}]}), topic)
    assert store.study_findings(db, sid) == before


# --- api level ------------------------------------------------------------------------------------
def _cfg(workspace, **values):
    settings.save_global(workspace, values)
    return config.load("seo")


def test_emit_skipped_in_questions_mode(workspace):
    cfg = _cfg(workspace)
    with closing(store.connect(cfg.db_path)) as conn:
        old_study(conn)
    data = json.loads(api.emit(cfg, "findings"))
    assert data["skipped"] == "grouping_mode questions" and data["items"] == []
    assert api.run_api(cfg, "findings")["skipped"] == "grouping_mode questions"


def test_emit_skipped_when_batch_zero(workspace):
    cfg = _cfg(workspace, grouping_mode="quotes", findings_batch=0)
    data = json.loads(api.emit(cfg, "findings"))
    assert data["skipped"] == "findings_batch is 0" and data["items"] == []
    assert api.run_api(cfg, "findings")["skipped"] == "findings_batch is 0"


def test_emit_in_quotes_mode_lists_studies(workspace):
    cfg = _cfg(workspace, grouping_mode="quotes", findings_batch=1)
    with closing(store.connect(cfg.db_path)) as conn:
        made = [old_study(conn), old_study(conn)]
    data = json.loads(api.emit(cfg, "findings"))
    assert [i["id"] for i in data["items"]] == [f"s{made[1]}"] and data["remaining"] == 1 and "skipped" not in data


def test_reset_findings_all_and_one_study(workspace):
    cfg = _cfg(workspace, grouping_mode="quotes")
    with closing(store.connect(cfg.db_path)) as conn:
        a, b = old_study(conn), old_study(conn)
        stage.apply(conn, answer(a, f(), f()))
        stage.apply(conn, answer(b))                           # failed
    assert api.reset(cfg, "findings", study=f"s{a}") == {"reset": "findings", "studies": 1, "findings": 2}
    out = api.reset(cfg, "findings")
    assert out["reset"] == "findings" and out["findings"] == 0
    with closing(store.connect(cfg.db_path)) as conn:
        assert {s["id"] for s in store.studies_without_findings(conn, V)} >= {a, b}


def test_cli_reset_study_only_with_findings(workspace, capsys):
    assert cli.main(["reset", "--stage", "judge", "--study", "s1"]) == 2
    assert "--study" in capsys.readouterr().err
    assert cli.main(["reset", "--stage", "findings", "--study", "12"]) == 2
    assert cli.main(["reset", "--stage", "findings", "--study", "s12"]) == 0


def test_report_skill_runs_findings_after_classify():
    skill = (Path(stage.__file__).parents[3] / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
    classify = skill.index("**Classify (after the template question")
    run = skill.index("ev --topic <id> findings")
    subtopics = skill.index("**Subtopics (after verify")
    assert classify < run < subtopics
    assert "studies into findings" in skill and "say nothing when both are 0" in skill

