"""Entities on studies: create / assign / merge carry them, classify fills older studies (classify-3)."""
import json

import pytest

from evidence_echo import api
from evidence_echo.core import store
from evidence_echo.stages import classify
from tests.conftest import make_item
from tests.test_classify import ANSWER, DONE, answer, seeded, study   # noqa: F401  (seeded is a fixture)

UNCHECKED_DONE = {k: v for k, v in DONE.items() if k != "entities_checked"}


def _plain(db, name="s", entity_names=None):
    return store.create_study(db, "B", {"what_was_tested": name}, None, entity_names=entity_names)


def _slugs(db, sid):
    return [e["slug"] for e in store.study_entities(db, sid)]


def _item(db, url, entity_names):
    store.insert_items(db, [make_item(url=url)])
    iid = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    if entity_names is not None:
        db.execute("UPDATE items SET entity_names=? WHERE id=?", (json.dumps(entity_names), iid))
        db.commit()
    return iid


def test_create_study_links_and_checks(bare_db):
    sid = _plain(bare_db, entity_names=["ChatGPT", "Shopify"])
    assert _slugs(bare_db, sid) == ["chatgpt", "shopify"]
    assert store.get_study(bare_db, sid)["entities_checked"] == 1
    empty = _plain(bare_db, "e", entity_names=[])
    assert _slugs(bare_db, empty) == [] and store.get_study(bare_db, empty)["entities_checked"] == 1


def test_pre_step13_item_leaves_study_unchecked(bare_db):
    iid = _item(bare_db, "https://e.com/a", None)
    assert store.item_entity_names(store.get_item(bare_db, iid)) is None
    sid = _plain(bare_db, entity_names=store.item_entity_names(store.get_item(bare_db, iid)))
    assert store.get_study(bare_db, sid)["entities_checked"] is None


def test_assign_fills_only_unchecked(bare_db):
    unchecked, checked = _plain(bare_db, "u"), _plain(bare_db, "c", entity_names=["Wix"])
    store.assign_study(bare_db, _item(bare_db, "https://e.com/1", ["Shopify"]), unchecked, "same")
    store.assign_study(bare_db, _item(bare_db, "https://e.com/2", ["Shopify"]), checked, "same")
    assert _slugs(bare_db, unchecked) == ["shopify"]
    assert store.get_study(bare_db, unchecked)["entities_checked"] == 1
    assert _slugs(bare_db, checked) == ["wix"]
    old = _plain(bare_db, "old")
    store.assign_study(bare_db, _item(bare_db, "https://e.com/3", None), old, "same")
    assert store.get_study(bare_db, old)["entities_checked"] is None


def test_study_merge_unions_entities(bare_db):
    keep = _plain(bare_db, "k", entity_names=["ChatGPT", "Gemini"])
    other = _plain(bare_db, "o", entity_names=["Gemini", "Perplexity"])
    store.merge_studies(bare_db, keep, other)
    assert _slugs(bare_db, keep) == ["chatgpt", "gemini", "perplexity"]
    assert bare_db.execute("SELECT COUNT(*) FROM study_entities WHERE study_id=?", (keep,)).fetchone()[0] == 3
    assert store.get_study(bare_db, keep)["entities_checked"] == 1
    lone, checked = _plain(bare_db, "l"), _plain(bare_db, "c", entity_names=["Wix"])
    store.merge_studies(bare_db, lone, checked)
    assert _slugs(bare_db, lone) == ["wix"] and store.get_study(bare_db, lone)["entities_checked"] == 1


def test_study_merge_brings_back_an_entity_the_owner_removed(bare_db):
    keep = _plain(bare_db, "k", entity_names=["ChatGPT", "Gemini"])
    other = _plain(bare_db, "o", entity_names=["Gemini"])
    store.remove_study_entity(bare_db, keep, "gemini")
    store.merge_studies(bare_db, keep, other)
    assert _slugs(bare_db, keep) == ["chatgpt", "gemini"]


def test_unmerge_keeps_both_link_sets(bare_db):
    keep = _plain(bare_db, "k", entity_names=["ChatGPT"])
    other = _plain(bare_db, "o", entity_names=["Perplexity"])
    store.merge_studies(bare_db, keep, other)
    store.unmerge_study(bare_db, other)
    assert _slugs(bare_db, keep) == ["chatgpt", "perplexity"] and _slugs(bare_db, other) == ["perplexity"]


def test_classify_selects_unchecked_and_links(seeded, topic):
    sid = study(seeded, subtopic="backlinks", **UNCHECKED_DONE, classified_version="classify-2")
    assert sid in [s["id"] for s in store.studies_to_classify(seeded, classify.PROMPT_VERSION)]
    out = classify.apply(seeded, answer(sid, entities=["Shopify"]))
    assert out["applied"] == 1 and _slugs(seeded, sid) == ["shopify"]
    assert store.get_study(seeded, sid)["entities_checked"] == 1
    assert store.studies_to_classify(seeded, classify.PROMPT_VERSION) == []


def test_classify_payload_and_prompt(seeded, topic):
    sid = study(seeded)
    store.link_study_entities(seeded, sid, ["ChatGPT"])
    other = study(seeded)
    data = json.loads(classify.emit(seeded, topic))
    by_id = {i["id"]: i for i in data["items"]}
    assert by_id[f"s{sid}"]["entities"] == ["ChatGPT"] and by_id[f"s{other}"]["entities"] is None
    assert data["entities"] == ["ChatGPT"] and "- ChatGPT" in data["instructions"]
    assert "When the study already has entities (not null), return them unchanged." in data["instructions"]
    assert '"entities": ["..."]' in classify.RESULT_SHAPE and '"entities": ["..."]' in classify.PROMPT


def test_classify_ignores_entities_of_a_checked_study(seeded):
    sid = study(seeded, entities_checked=1)
    store.link_study_entities(seeded, sid, ["Wix"])
    classify.apply(seeded, answer(sid, entities=["Shopify", "Gemini"]))
    assert _slugs(seeded, sid) == ["wix"]


def test_classify_empty_answer_checks(seeded):
    sid = study(seeded)
    assert store.fill_study_knowledge(seeded, sid, classify.PROMPT_VERSION, entity_names=[]) == ["entities"]
    assert _slugs(seeded, sid) == [] and store.get_study(seeded, sid)["entities_checked"] == 1
    assert store.fill_study_knowledge(seeded, sid, classify.PROMPT_VERSION, entity_names=["Wix"]) == []


def test_classify_answer_without_entities_is_rejected(seeded):
    sid = study(seeded)
    payload = json.dumps({"results": [{k: v for k, v in dict(ANSWER, id=f"s{sid}").items() if k != "entities"}]})
    out = classify.apply(seeded, payload)
    assert out["applied"] == 0 and out["rejected"][0]["errors"] == ["entities: missing"]
    assert store.get_study(seeded, sid)["entities_checked"] is None


def test_classify_drops_a_rejected_name(seeded):
    other = study(seeded)
    store.link_study_entities(seeded, other, ["Listicles"])
    store.reject_entity(seeded, "listicles")
    sid = study(seeded)
    classify.apply(seeded, answer(sid, entities=["Listicles", "Wix"]))
    assert _slugs(seeded, sid) == ["wix"]


def test_owner_remove_survives_assign_and_classify(seeded):
    sid = study(seeded)
    store.link_study_entities(seeded, sid, ["Shopify"])
    store.remove_study_entity(seeded, sid, "shopify")
    store.assign_study(seeded, _item(seeded, "https://e.com/new", ["Shopify"]), sid, "same")
    assert _slugs(seeded, sid) == []
    assert store.get_study(seeded, sid)["entities_checked"] == 1      # classify asks only unchecked studies
    classify.apply(seeded, answer(sid, entities=["Shopify"]))
    assert _slugs(seeded, sid) == []


def test_judge_reset_clears_entity_names(workspace):
    from evidence_echo.core import config
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.insert_items(conn, [make_item(url="https://e.com/x")])
    conn.execute("UPDATE items SET state='judged', entity_names='[\"Wix\"]'")
    conn.commit()
    conn.close()
    api.reset(cfg, "judge")
    conn = store.connect(cfg.db_path)
    assert conn.execute("SELECT state, entity_names FROM items").fetchone()[:] == ("extracted", None)
    conn.close()


@pytest.mark.parametrize("entity_names", [None, ["Shopify"]])
def test_match_marks_classified_only_when_entities_were_asked(db, entity_names):
    from tests.test_match import F, judged
    from evidence_echo.stages import match
    if not store.active_subtopics(db):
        store.seed_subtopics(db, ["Title tags"])
    store.seed_metrics(db, ["Clicks"])
    iid = judged(db, "https://e.com/old", F(["9%"]))
    db.execute("UPDATE items SET subtopic_choice=?, metric_code='clicks', entity_names=? WHERE id=?",
               (json.dumps({"slug": store.active_subtopics(db)[0]["slug"]}),
                None if entity_names is None else json.dumps(entity_names), iid))
    db.commit()
    match.fixed_pass(db)
    sid = store.get_item(db, iid)["study_id"]
    marked = store.get_study(db, sid)["classified_version"] == classify.PROMPT_VERSION
    assert marked == (entity_names is not None)          # judged before entities: classify must ask
