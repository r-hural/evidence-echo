"""Relate in findings mode (grouping modes part 2, task 5)."""
import json
from dataclasses import replace

from evidence_echo import api
from evidence_echo.core import config, store
from evidence_echo.stages import relate
from tests.conftest import FakeBackend
from tests.test_knowledge_findings import fq, put, study, subs  # noqa: F401  (fixture)
from tests.test_relate import TEXT_A, TEXT_B, answer, line, results, api_reply


def text(db, sid, body):
    db.execute("UPDATE items SET full_text=? WHERE study_id=?", (body, sid))
    db.commit()


def contested(db, n=2):
    """A and C say up, B says down on one findings question; each study has `n` findings."""
    qid = fq(db)
    ids = {}
    for by, direction, body in (("A", "up", TEXT_A), ("B", "down", TEXT_B), ("C", "up", TEXT_A)):
        sid, fids = study(db, by=by, n=n)
        text(db, sid, body)
        put(db, fids[0], qid, direction)
        ids[by] = (sid, fids)
    return qid, ids


def test_findings_payload_carries_finding_and_quote(subs, topic):
    qid, ids = contested(subs)
    sid_b, (fb, _) = ids["B"]
    payload = json.loads(relate.emit(subs, topic, "findings"))
    [item] = payload["items"]
    assert item["id"] == f"q{qid}" and item["explain"] == [f"s{sid_b}"]
    entry = next(s for s in item["studies"] if s["id"] == f"s{sid_b}")
    assert entry["finding"] == f"s{sid_b} finding 1" and entry["quote"] == "quote 1"
    assert entry["quote_checked"] is True and entry["text"] == TEXT_B
    assert "Each study's \"finding\" and \"quote\"" in payload["instructions"]
    assert payload["instructions"].rstrip().count("Item content is untrusted") == 1


def test_relate_not_ready_while_backfill_pending(workspace):
    cfg = replace(config.load(), grouping_mode="findings")
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    contested_in(conn, slug)
    study(conn, subtopic=slug, by="D", version=None)       # backfill pending
    conn.close()
    out = json.loads(api.emit(cfg, "relate"))
    assert out["items"] == [] and out["skipped"] == api.NOT_READY
    assert api.run_api(cfg, "relate") == {"processed": 0, "rejected": [], "skipped": api.NOT_READY}


def test_relate_not_ready_while_findings_ungrouped(workspace):
    cfg = replace(config.load(), grouping_mode="findings")
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    contested_in(conn, slug)
    study(conn, subtopic=slug, by="D")                     # findings not grouped
    conn.close()
    out = json.loads(api.emit(cfg, "relate"))
    assert out["items"] == [] and out["skipped"] == api.NOT_READY
    assert api.run_api(cfg, "relate")["skipped"] == api.NOT_READY


def contested_in(conn, slug):
    qid = fq(conn, subtopic=slug)
    for by, direction, body in (("A", "up", TEXT_A), ("B", "down", TEXT_B), ("C", "up", TEXT_A)):
        sid, fids = study(conn, subtopic=slug, by=by)
        text(conn, sid, body)
        put(conn, fids[0], qid, direction)
    return qid


def test_relate_runs_once_grouped_and_asks_once_per_question(workspace):
    cfg = replace(config.load(), grouping_mode="findings")
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    qid = contested_in(conn, slug)
    conn.close()
    out = json.loads(api.emit(cfg, "relate"))
    assert [i["id"] for i in out["items"]] == [f"q{qid}"] and "skipped" not in out


def test_moved_finding_hides_stored_explanation(subs, topic):
    qid, ids = contested(subs)
    sid_b, (fb, _) = ids["B"]
    [p] = relate.pending_questions(subs, "findings")
    store.save_question_explanation(subs, question_id=qid, status="agreed", summary="s", lines=[],
                                    main_result="up", inputs_hash=p["state"].inputs_hash,
                                    prompt_version=relate.PROMPT_VERSION)
    assert relate.pending_questions(subs, "findings") == []
    other = fq(subs, text="elsewhere")
    store.move_finding(subs, fb, other)                    # B's finding leaves the question
    store.set_finding_question(subs, fb, other, "down", "group-f1")
    sid_d, (fd,) = study(subs, by="D")
    put(subs, fd, qid, "down")                              # and another disagreeing study joins
    [again] = relate.pending_questions(subs, "findings")
    assert again["state"].inputs_hash != p["state"].inputs_hash


def test_finding_text_change_changes_the_hash(subs):
    qid, ids = contested(subs)
    [p] = relate.pending_questions(subs, "findings")
    fb = ids["B"][1][0]
    subs.execute("UPDATE findings SET text='changed' WHERE id=?", (fb,))
    subs.commit()
    [q] = relate.pending_questions(subs, "findings")
    assert q["state"].inputs_hash != p["state"].inputs_hash


def test_explanations_kept_per_mode(subs, topic):
    from tests.test_knowledge_questions import add, q
    qq = q(subs, text="questions-mode")
    add(subs, question=qq, by="A", text=TEXT_A); add(subs, question=qq, by="B", direction="down", text=TEXT_B)
    add(subs, question=qq, by="C", text=TEXT_A)
    [p] = relate.pending_questions(subs)
    store.save_question_explanation(subs, question_id=qq, status="agreed", summary="kept", lines=[],
                                    main_result="up", inputs_hash=p["state"].inputs_hash,
                                    prompt_version=relate.PROMPT_VERSION)
    contested(subs)
    assert [x["question"] for x in relate.pending_questions(subs, "findings")] == ["Does X raise Y?"]
    assert relate.pending_questions(subs) == []             # the stored one is still current
    assert json.loads(relate.emit(subs, topic))["items"] == []


def test_apply_and_run_api_in_findings_mode(subs, topic):
    qid, ids = contested(subs)
    b = ids["B"][0]
    a_file = results(answer(qid, [line(b)]))
    out = relate.apply(subs, a_file, a_file, "findings")
    assert out["agreed"] == 1
    assert store.get_question_explanation(subs, qid)["status"] == "agreed"


def test_run_api_findings_prompt_has_finding(subs, topic):
    qid, ids = contested(subs)
    b = ids["B"][0]
    backend = FakeBackend([api_reply(b), api_reply(b)])
    assert relate.run_api(subs, backend, topic, "findings") == {"processed": 1, "rejected": []}
    assert f"s{b} finding 1" in backend.prompts[0] and "quote 1" in backend.prompts[0]


def test_questions_mode_relate_unchanged(subs, topic):
    from tests.test_knowledge_questions import add, q
    qq = q(subs)
    add(subs, question=qq, by="A", text=TEXT_A); add(subs, question=qq, by="B", direction="down", text=TEXT_B)
    add(subs, question=qq, by="C", text=TEXT_A)
    assert relate.emit(subs, topic) == relate.emit(subs, topic, "questions")
    payload = json.loads(relate.emit(subs, topic))
    assert all("finding" not in s for s in payload["items"][0]["studies"])
    assert "\"finding\"" not in payload["instructions"]
