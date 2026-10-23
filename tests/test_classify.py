"""Wave 4a Part 3 and Metrics: classify - selection, cap, payload, contract, fill-only-empty, modes, reset."""
import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, store
from evidence_echo.stages import classify, judge
from tests.conftest import FakeBackend, make_item

TEXT = "We studied 300k keywords in 2024. Position-1 CTR fell by −34.5 % after the launch of AI Overviews."
QUOTE = "Position-1 CTR fell by −34.5 % after the launch of AI Overviews."
ANSWER = {"subtopic": {"slug": "local"}, "metric": "ctr", "entities": [], "data_date": "2024-5",
          "headline": {"value": "-34.5%", "label": "position-1 CTR", "quote": QUOTE}}


def study(db, *, text=TEXT, state="verified", creator=True, subtopic=None, metric=None, **cols):
    """A study with one item in `state`; cols set study columns directly; `metric` is a metric slug."""
    sid = store.create_study(db, "B", {"what_was_tested": "CTR study", "findings": "CTR fell",
                                       "research_timeframe": "2024"}, None)
    url = f"https://e.com/{sid}"
    store.insert_items(db, [make_item(url=url)])
    iid = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    db.execute("UPDATE items SET study_id=?, state=?, full_text=?, facts=? WHERE id=?",
               (sid, state, text, json.dumps({"what_was_tested": "Item says CTR", "sample_scale": "300k"}), iid))
    cols = {"created_by_item": iid if creator else None, **cols}
    if subtopic:
        cols["subtopic_id"] = store.get_subtopic(db, subtopic)["id"]
    if metric:
        cols["metric_id"] = store.get_metric(db, metric)["id"]
    for k, v in cols.items():
        db.execute(f"UPDATE studies SET {k}=? WHERE id=?", (v, sid))
    db.commit()
    return sid


def answer(sid, **over):
    return json.dumps({"results": [dict(ANSWER, id=f"s{sid}", **over)]})


@pytest.fixture
def seeded(db, topic):
    store.seed_subtopics(db, topic.subtopics)
    return db


DONE = dict(metric="ctr", data_date="2024", headline_checked=1, entities_checked=1,
            headline_value="−3 %", headline_label="ctr", headline_quote="q")


def _metric(db, sid):
    found = store.study_metric(db, store.get_study(db, sid))
    return found["slug"] if found else None


def test_selection(seeded):
    todo = study(seeded)
    study(seeded, subtopic="backlinks", **DONE)                                   # nothing missing
    study(seeded, classified_version=classify.PROMPT_VERSION)                  # asked already
    study(seeded, state="matched")                                             # no reported/verified item
    gone = study(seeded)
    seeded.execute("UPDATE studies SET merged_into=? WHERE id=?", (todo, gone))       # merged away
    retired = study(seeded, subtopic="local", classified_version=classify.PROMPT_VERSION, **DONE)
    seeded.execute("UPDATE subtopics SET status='retired' WHERE slug='local'")            # picked again
    seeded.commit()
    assert [s["id"] for s in store.studies_to_classify(seeded, classify.PROMPT_VERSION)] == [retired, todo]


def test_emit_payload_and_cap(seeded, topic):
    a, b = study(seeded), study(seeded)
    data = json.loads(classify.emit(seeded, topic, cap=1))
    assert data["stage"] == "classify" and data["remaining"] == 1
    assert [i["id"] for i in data["items"]] == [f"s{b}"]                         # newest first
    item = data["items"][0]
    assert item["name"] == "CTR study" and item["full_text"] == TEXT
    assert item["facts"]["what_was_tested"] == "Item says CTR"                 # the item's facts
    assert item["facts"]["findings"] == "CTR fell"                             # study fallback
    assert data["subtopics"][0] == {"slug": "title-tags-meta", "name": "Title tags & meta"}
    for word in ('"headline"', '"data_date"', "untrusted", "s<id>"):
        assert word in data["instructions"], word


def test_apply_fills_and_marks_the_study(seeded):
    sid = study(seeded)
    before = store.get_study(seeded, sid)
    assert classify.apply(seeded, answer(sid)) == {"applied": 1, "rejected": [], "waiting": 0}
    s = store.get_study(seeded, sid)
    assert s["subtopic_id"] == store.get_subtopic(seeded, "local")["id"]
    assert (_metric(seeded, sid), s["data_date"]) == ("ctr", "2024-05")
    assert (s["headline_value"], s["headline_label"], s["headline_checked"]) == ("−34.5 %", "position-1 CTR", 1)
    assert s["classified_version"] == "classify-3"
    for col in ("evidence_tier", "findings", "what_was_tested", "series_id", "conducted_by", "metric"):
        assert s[col] == before[col], col


def test_owner_subtopic_is_kept_and_only_empty_fields_fill(seeded):
    store.subtopic_for_choice(seeded, {"propose": "Image SEO"}, create=True)
    store.accept_subtopic(seeded, "image-seo")
    sid = study(seeded, subtopic="image-seo", metric="ctr")
    classify.apply(seeded, answer(sid))
    s = store.get_study(seeded, sid)
    assert s["subtopic_id"] == store.get_subtopic(seeded, "image-seo")["id"] and _metric(seeded, sid) == "ctr"
    assert (s["data_date"], s["headline_checked"]) == ("2024-05", 1)


def test_classify_keeps_existing_metric_and_drops_headline(seeded):
    """Review I2: the headline belongs to the study's metric; an answer for another metric
    keeps only what does not depend on the metric."""
    sid = study(seeded, metric="clicks")
    assert classify.apply(seeded, answer(sid))["applied"] == 1
    s = store.get_study(seeded, sid)
    assert (_metric(seeded, sid), s["data_date"]) == ("clicks", "2024-05")
    assert (s["headline_quote"], s["headline_checked"]) == (None, None)
    assert s["subtopic_id"] == store.get_subtopic(seeded, "local")["id"]


def test_payload_and_prompt_carry_the_studys_metric(seeded, topic):
    sid = study(seeded, metric="clicks")
    item = json.loads(classify.emit(seeded, topic))["items"][0]
    assert item["metric"] == "clicks" and "direction" not in item
    assert "keep" in classify.instructions(topic).lower()
    backend = FakeBackend([json.dumps(ANSWER)])
    classify.run_api(seeded, backend, topic)
    assert '"metric": "clicks"' in backend.prompts[0] and "direction" not in backend.prompts[0]
    assert _metric(seeded, sid) == "clicks"
    study(seeded)
    assert json.loads(classify.emit(seeded, topic))["items"][0]["metric"] is None   # newest first


def test_classify_prompt_has_no_judge_only_wording(topic):
    """Review M5: classify has no 'pass' field."""
    assert "pass is true" not in classify.instructions(topic)
    assert "pass is true" not in classify.PROMPT
    assert "pass is true" in judge.instructions(topic)


@pytest.mark.parametrize("how", ["map", "merge"])
def test_mapped_or_merged_owner_subtopic_is_kept(seeded, how):
    """Review M8/RF1: a study on a subtopic the owner mapped or merged keeps where it resolves."""
    if how == "map":
        store.subtopic_for_choice(seeded, {"propose": "Tech stuff"}, create=True)
        old = store.get_subtopic(seeded, "tech-stuff")["id"]
        store.map_subtopic(seeded, "tech-stuff", "structured-data")
        target = "structured-data"
    else:
        old = store.get_subtopic(seeded, "backlinks")["id"]
        store.merge_subtopics(seeded, "backlinks", "structured-data")
        target = "structured-data"
    sid = study(seeded)
    seeded.execute("UPDATE studies SET subtopic_id=? WHERE id=?", (old, sid)); seeded.commit()
    assert classify.apply(seeded, answer(sid))["applied"] == 1        # the answer says 'local'
    s = store.get_study(seeded, sid)
    assert s["subtopic_id"] == old
    assert store.resolve_subtopic(seeded, s["subtopic_id"])["slug"] == target


def test_proposals_are_shared_and_studies_follow_them(seeded):
    a, b = study(seeded), study(seeded)
    payload = json.dumps({"results": [dict(ANSWER, id=f"s{x}", subtopic={"propose": "Video SEO"}) for x in (a, b)]})
    assert classify.apply(seeded, payload)["applied"] == 2
    pending = [t for t in store.list_subtopics(seeded) if t["status"] == "proposed"]
    assert [t["slug"] for t in pending] == ["video-seo"]
    assert store.get_study(seeded, a)["subtopic_id"] == store.get_study(seeded, b)["subtopic_id"] == pending[0]["id"]


@pytest.mark.parametrize("over,error", [
    ({"tier": "A"}, "classify: unexpected field 'tier'"),
    ({"facts": {}}, "classify: unexpected field 'facts'"),
    ({"metric": None}, "metric: expected string, got null"),
    ({"metric": "sessions"}, "metric sessions is not in the topic's list"),
    ({"direction": "negative"}, "classify: unexpected field 'direction'"),
    ({"subtopic": {"slug": "nope"}}, "subtopic: unknown slug 'nope'"),
    ({"headline": {"value": "1"}}, "headline: must have value, label and quote"),
])
def test_invalid_answers_are_rejected_and_asked_again(seeded, over, error):
    sid = study(seeded)
    out = classify.apply(seeded, answer(sid, **over))
    assert out["applied"] == 0 and error in out["rejected"][0]["errors"][0] and out["waiting"] == 1
    assert store.get_study(seeded, sid)["classified_version"] is None


def test_missing_metric_is_rejected(seeded):
    sid = study(seeded)
    res = {k: v for k, v in ANSWER.items() if k != "metric"}
    out = classify.apply(seeded, json.dumps({"results": [dict(res, id=f"s{sid}")]}))
    assert "metric: missing" in out["rejected"][0]["errors"][0]


def test_source_item_choice(seeded):
    sid = study(seeded, creator=False, text=None)
    url = "https://e.com/second"
    store.insert_items(seeded, [make_item(url=url)])
    seeded.execute("UPDATE items SET study_id=?, state='reported', full_text=? WHERE url=?", (sid, TEXT, url))
    seeded.commit()
    s = store.get_study(seeded, sid)
    assert store.classify_source_item(seeded, s)["url"] == url                 # earliest with full text
    seeded.execute("UPDATE items SET full_text=NULL WHERE url=?", (url,)); seeded.commit()
    assert store.classify_source_item(seeded, s)["url"] == f"https://e.com/{sid}"   # earliest item


def test_no_full_text_means_no_headline(seeded, topic):
    sid = study(seeded, text=None)
    assert json.loads(classify.emit(seeded, topic))["items"][0]["full_text"] is None
    classify.apply(seeded, answer(sid))
    s = store.get_study(seeded, sid)
    assert (s["headline_quote"], s["headline_checked"], _metric(seeded, sid)) == (None, None, "ctr")


def test_unchecked_headline_is_stored_but_not_checked(seeded):
    sid = study(seeded)
    classify.apply(seeded, answer(sid, headline={"value": "-50%", "label": "ctr", "quote": QUOTE}))
    assert store.get_study(seeded, sid)["headline_checked"] == 0


def test_api_mode(seeded, topic):
    sid = study(seeded)
    backend = FakeBackend([json.dumps(ANSWER)])
    assert classify.run_api(seeded, backend, topic, cap=40) == {"processed": 1, "rejected": [], "waiting": 0}
    assert "CTR study" in backend.prompts[0] and QUOTE in backend.prompts[0]
    assert store.get_study(seeded, sid)["data_date"] == "2024-05"


def test_api_cap_leaves_the_rest_waiting(seeded, topic):
    study(seeded); study(seeded)
    out = classify.run_api(seeded, FakeBackend([json.dumps(ANSWER)]), topic, cap=1)
    assert (out["processed"], out["waiting"]) == (1, 1)


def _run(capsys, *argv):
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_off_emits_nothing(workspace, capsys, monkeypatch):
    monkeypatch.setenv("EV_ECHO_CLASSIFY_BATCH", "0")
    monkeypatch.setenv("EV_ECHO_JUDGMENT_BACKEND", "session")
    code, out = _run(capsys, "classify")
    assert code == 0 and out["items"] == [] and out["skipped"] == "classify_batch is 0"


def test_session_round_trip_and_reset(workspace, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_JUDGMENT_BACKEND", "session")
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    store.seed_metrics(conn, cfg.topic.metrics)
    sid = study(conn)
    conn.close()
    code, out = _run(capsys, "classify")
    assert code == 0 and out["items"][0]["id"] == f"s{sid}"
    f = tmp_path / "c.json"
    f.write_text(answer(sid), encoding="utf-8")
    assert _run(capsys, "apply", "--stage", "classify", str(f))[1]["applied"] == 1
    assert _run(capsys, "classify")[1]["items"] == []                           # asked once
    assert api.reset(cfg, "classify") == {"reset": "classify", "studies": 1}
    assert _run(capsys, "reset", "--stage", "classify")[1] == {"reset": "classify", "studies": 0}


def test_classify_is_a_cli_stage_and_an_apply_target():
    parser = cli._parser()
    assert "classify" in parser._subparsers._group_actions[0].choices
    assert "classify" in api.LLM_STAGES and "classify" not in api.TWO_ANSWER_STAGES
    assert "classify" in api.RESET_STAGES


def test_api_off_makes_no_call(workspace, monkeypatch):
    """Review M8: ev classify in API mode with classify_batch 0 builds no backend."""
    from evidence_echo.core import llm_api
    monkeypatch.setenv("EV_ECHO_CLASSIFY_BATCH", "0")
    monkeypatch.setattr(llm_api, "AnthropicBackend", lambda key: pytest.fail("no backend when off"))
    assert api.run_api(config.load(), "classify") == {
        "processed": 0, "rejected": [], "waiting": 0, "skipped": "classify_batch is 0"}


def test_judged_study_is_not_classified_again(db):
    from tests.conftest import seed_new_study
    seed_new_study(db)
    assert store.studies_to_classify(db, classify.PROMPT_VERSION) == []


def test_classify_selects_missing_metric_not_direction(seeded):
    """A study is queued for its missing or unresolved metric; a missing old direction no longer counts."""
    no_metric = study(seeded, subtopic="backlinks", data_date="2024", headline_checked=1, entities_checked=1)
    complete = study(seeded, subtopic="backlinks", metric="ctr", data_date="2024", headline_checked=1,
                     entities_checked=1)
    seeded.execute("UPDATE studies SET direction=NULL, metric_code=NULL WHERE id=?", (complete,))
    unresolved = study(seeded, subtopic="backlinks", metric="impressions", data_date="2024", headline_checked=1,
                       entities_checked=1)
    seeded.execute("UPDATE metrics SET status='retired' WHERE slug='impressions'")
    seeded.commit()
    assert [s["id"] for s in store.studies_to_classify(seeded, "x")] == [unresolved, no_metric]


def test_classify_payload_has_no_direction(seeded, topic):
    study(seeded)
    data = json.loads(classify.emit(seeded, topic))
    assert "direction" not in data["items"][0] and "direction" not in data["instructions"]
    assert "direction" not in classify.PROMPT and "direction" not in classify.RESULT_SHAPE
    assert [m["slug"] for m in data["metrics"]][-1] == "other"
    assert "- ctr: CTR" in data["instructions"]
    assert "Pick the most specific subtopic and metric that fits" in data["instructions"]


def test_classify_version_2_reasks_once(seeded):
    """A study stamped by classify-1 that still lacks a metric is asked again under classify-3, once."""
    assert classify.PROMPT_VERSION == "classify-3"
    sid = study(seeded, subtopic="backlinks", data_date="2024", headline_checked=1,
                classified_version="classify-1")
    assert [s["id"] for s in store.studies_to_classify(seeded, classify.PROMPT_VERSION)] == [sid]
    assert classify.apply(seeded, answer(sid))["applied"] == 1
    assert store.get_study(seeded, sid)["classified_version"] == "classify-3"
    assert store.studies_to_classify(seeded, classify.PROMPT_VERSION) == []
    # a rejected answer keeps it queued; an answered one that still lacks nothing is not asked again
    other = study(seeded, subtopic="backlinks", data_date="2024", headline_checked=1,
                  classified_version="classify-3")
    assert other not in [s["id"] for s in store.studies_to_classify(seeded, classify.PROMPT_VERSION)]


def test_classify_unknown_metric_rejected(seeded):
    sid = study(seeded)
    out = classify.apply(seeded, answer(sid, metric="sessions"))
    assert out["applied"] == 0 and out["waiting"] == 1
    assert out["rejected"][0]["errors"] == ["metric sessions is not in the topic's list"]
    s = store.get_study(seeded, sid)
    assert s["metric_id"] is None and s["classified_version"] is None


def test_classify_bad_metric_leaves_no_proposed_subtopic(seeded):
    sid = study(seeded)
    out = classify.apply(seeded, answer(sid, subtopic={"propose": "Brand new area"}, metric="sessions"))
    assert out["applied"] == 0
    assert store.get_subtopic(seeded, "brand-new-area") is None
