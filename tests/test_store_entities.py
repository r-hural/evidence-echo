"""Entity storage: cleaning, resolution (merge / alias / reject), linking and the owner operations."""
import pytest

from evidence_echo.core import store
from evidence_echo.core.settings import ConfigError


def _study(db, name="s"):
    return store.create_study(db, "B", {"what_was_tested": name}, None)


def test_clean_entity_names():
    raw = ["ChatGPT", " chatgpt ", "Chat  GPT", 7, None, "", "x" * 61, "<script>", "—", "A", "B", "C", "D"]
    assert store.clean_entity_names(raw) == ["ChatGPT", "Chat GPT", "<script>", "A", "B"]
    assert store.clean_entity_names("ChatGPT") == [] and store.clean_entity_names(None) == []


def test_non_ascii_and_punctuation_names(bare_db):
    row = store.entity_for_name(bare_db, "Яндекс", create=True)
    assert row["slug"] == "яндекс" and store.entity_for_name(bare_db, "ЯНДЕКС")["id"] == row["id"]
    assert store.clean_entity_names(["—", "Ahrefs Webmaster Tools"]) == ["Ahrefs Webmaster Tools"]


def test_plus_and_sharp_keep_languages_apart(bare_db):
    assert [store.entity_slug(n) for n in ("C", "C++", "C#", "GPT-4o")] == ["c", "c-plus-plus", "c-sharp", "gpt-4o"]
    ids = {store.entity_for_name(bare_db, n, create=True)["id"] for n in ("C", "C++", "C#")}
    assert len(ids) == 3


def test_resolution_follows_merge_chain_and_alias(bare_db):
    s = _study(bare_db)
    store.add_study_entity(bare_db, s, "GPT-4o"); store.add_study_entity(bare_db, s, "ChatGPT")
    store.add_study_entity(bare_db, s, "OpenAI")
    store.merge_entities(bare_db, "gpt-4o", "chatgpt"); store.merge_entities(bare_db, "chatgpt", "openai")
    assert store.entity_for_name(bare_db, "gpt 4o")["slug"] == "openai"
    store.alias_entity(bare_db, "openai", "Open AI Inc")
    assert store.entity_for_name(bare_db, "open ai inc")["slug"] == "openai"
    assert [e["slug"] for e in store.study_entities(bare_db, s)] == ["openai"]


def test_rejected_name_is_dropped_on_link(bare_db):
    s, t = _study(bare_db), _study(bare_db, "t")
    store.link_study_entities(bare_db, s, ["Listicles", "Shopify"])
    store.reject_entity(bare_db, "listicles")
    assert [e["slug"] for e in store.study_entities(bare_db, s)] == ["shopify"]
    assert store.link_study_entities(bare_db, t, ["listicles"]) == []
    assert store.get_study(bare_db, t)["entities_checked"] == 1
    with pytest.raises(ConfigError):
        store.add_study_entity(bare_db, t, "Listicles")       # "restore it first"
    store.restore_entity(bare_db, "listicles")
    assert store.link_study_entities(bare_db, t, ["Listicles"]) == ["listicles"]


def test_merge_refusals(bare_db):
    s = _study(bare_db)
    store.link_study_entities(bare_db, s, ["A1", "B1", "C1"])
    store.reject_entity(bare_db, "c1"); store.merge_entities(bare_db, "a1", "b1")
    for slug, into in (("b1", "c1"), ("b1", "a1"), ("b1", "b1"), ("nope", "b1")):
        with pytest.raises(ConfigError):
            store.merge_entities(bare_db, slug, into)


def test_link_cap_order_and_owner_add_uncapped(bare_db):
    s = _study(bare_db)
    assert store.link_study_entities(bare_db, s, ["A", "B", "C", "D", "E", "F"]) == ["a", "b", "c", "d", "e"]
    store.add_study_entity(bare_db, s, "G")
    assert [e["slug"] for e in store.study_entities(bare_db, s)] == ["a", "b", "c", "d", "e", "g"]


def test_rename_alias_and_overview(bare_db):
    s = _study(bare_db)
    store.link_study_entities(bare_db, s, ["Shopify", "Wix"])
    store.rename_entity(bare_db, "shopify", "Shopify Plus")
    with pytest.raises(ConfigError):
        store.rename_entity(bare_db, "wix", "shopify plus")
    with pytest.raises(ConfigError):
        store.alias_entity(bare_db, "wix", "Shopify")          # slug exists: use merge
    store.merge_entities(bare_db, "wix", "shopify")
    rows = store.entities_overview(bare_db)["entities"]
    assert rows[0] == {"slug": "shopify", "name": "Shopify Plus", "status": "active", "merged_into": None,
                       "studies": 1, "examples": ["s"]}
    assert rows[-1]["slug"] == "wix" and rows[-1]["merged_into"] == "shopify" and rows[-1]["studies"] == 0


def test_remove_study_entity_sets_checked(bare_db):
    s = _study(bare_db)
    store.add_study_entity(bare_db, s, "Shopify")
    store.remove_study_entity(bare_db, s, "shopify")
    assert store.study_entities(bare_db, s) == [] and store.get_study(bare_db, s)["entities_checked"] == 1


def test_topic_entities_order_and_limit(bare_db):
    a, b = _study(bare_db, "a"), _study(bare_db, "b")
    store.link_study_entities(bare_db, a, ["Wix", "Shopify"])
    store.link_study_entities(bare_db, b, ["Shopify", "Amazon"])
    rows = store.topic_entities(bare_db)
    assert [(e["name"], e["studies"]) for e in rows] == [("Shopify", 2), ("Amazon", 1), ("Wix", 1)]
    assert [e["name"] for e in store.topic_entities(bare_db, 1)] == ["Shopify"]


def test_reject_removes_links_and_restore_does_not_bring_them_back(bare_db):
    s = _study(bare_db)
    store.link_study_entities(bare_db, s, ["Listicles"])
    assert store.reject_entity(bare_db, "listicles")["removed_links"] == 1
    store.restore_entity(bare_db, "listicles")
    assert store.study_entities(bare_db, s) == []
    with pytest.raises(ConfigError):
        store.restore_entity(bare_db, "listicles")             # not rejected any more
