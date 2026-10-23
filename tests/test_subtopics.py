"""SP3b-T: subtopics in the store - seeding, the judge's subtopic answers, the owner's review."""
import pytest

from evidence_echo.core import config, store, topics
from evidence_echo.core.settings import ConfigError

SEO = topics.load_template("seo", config.TEMPLATES_DIR).subtopics


def _rows(db):
    return [(t["slug"], t["status"]) for t in store.list_subtopics(db)]


def _study(db, subtopic_id=None):
    sid = store.create_study(db, "B", {"what_was_tested": "x"}, None)
    db.execute("UPDATE studies SET subtopic_id=? WHERE id=?", (subtopic_id, sid))
    db.commit()
    return sid


def _id(db, slug):
    return store.get_subtopic(db, slug)["id"]


def test_subtopic_slug():
    assert store.subtopic_slug("Core Web Vitals & speed") == "core-web-vitals-speed"
    assert store.subtopic_slug("  E-E-A-T & authorship ") == "e-e-a-t-authorship"
    assert store.subtopic_slug("AI search / GEO") == "ai-search-geo"
    assert store.subtopic_slug("!!!") == ""


def test_seo_template_lists_fifteen_subtopics_with_unique_slugs():
    assert len(SEO) == 15 and SEO[0] == "Title tags & meta" and SEO[-1] == "Other"
    assert len({store.subtopic_slug(n) for n in SEO}) == 15


def test_seed_inserts_the_topic_list_once_in_order(db):
    assert store.seed_subtopics(db, ["Links", "Local", "links"]) == 2   # one row per slug
    assert store.seed_subtopics(db, ["Links", "Local"]) == 0
    assert _rows(db) == [("links", "seeded"), ("local", "seeded")]
    assert [t["name"] for t in store.active_subtopics(db)] == ["Links", "Local"]


def test_seed_adds_subtopics_added_to_the_topic_later(db):
    store.seed_subtopics(db, ["Links"])
    store.subtopic_for_choice(db, {"propose": "LOCAL"}, create=True)   # the judge proposed it first
    assert store.seed_subtopics(db, ["Links", "Local", "International"]) == 2
    assert _rows(db) == [("links", "seeded"), ("local", "seeded"), ("international", "seeded")]
    assert store.get_subtopic(db, "local")["name"] == "Local"            # the topic's spelling wins


def test_slug_choice_resolves_and_unknown_slug_is_an_error(db):
    store.seed_subtopics(db, ["Links"])
    assert store.subtopic_for_choice(db, {"slug": "links"}) == (_id(db, "links"), None)
    assert store.subtopic_for_choice(db, {"slug": "nope"}) == (None, "subtopic: unknown slug 'nope'")


def test_proposal_creates_a_proposed_subtopic_once(db):
    tid, error = store.subtopic_for_choice(db, {"propose": "Image  SEO"}, create=True)
    assert error is None and store.get_subtopic(db, "image-seo")["name"] == "Image SEO"
    assert store.subtopic_for_choice(db, {"propose": "image seo"}, create=True) == (tid, None)
    assert _rows(db) == [("image-seo", "proposed")]


def test_proposal_without_create_adds_nothing(db):
    assert store.subtopic_for_choice(db, {"propose": "Image SEO"}) == (None, None)
    assert store.list_subtopics(db) == []


def test_proposal_reuses_a_subtopic_by_name_ignoring_case_and_spaces(db):
    store.seed_subtopics(db, SEO)
    tid = _id(db, "title-tags-meta")
    assert store.subtopic_for_choice(db, {"propose": "  title TAGS &  meta "}, create=True) == (tid, None)
    assert [s for s, status in _rows(db) if status == "proposed"] == []


def test_rejected_subtopic_gives_no_subtopic_and_merged_resolves(db):
    store.seed_subtopics(db, ["Links", "Backlinks"])
    store.subtopic_for_choice(db, {"propose": "Guest posts"}, create=True)
    store.reject_subtopic(db, "guest-posts")
    assert store.subtopic_for_choice(db, {"propose": "Guest posts"}, create=True) == (None, None)
    store.merge_subtopics(db, "links", "backlinks")
    assert store.subtopic_for_choice(db, {"slug": "links"}) == (_id(db, "backlinks"), None)
    assert store.resolve_subtopic(db, _id(db, "links"))["slug"] == "backlinks"
    assert store.resolve_subtopic(db, None) is None


def test_subtopic_of_item_reads_the_stored_answer(db):
    store.seed_subtopics(db, ["Links"])
    assert store.subtopic_of_item(db, {"subtopic_choice": '{"slug": "links"}'}) == _id(db, "links")
    assert store.subtopic_of_item(db, {"subtopic_choice": None}) is None
    assert store.subtopic_of_item(db, {"subtopic_choice": "not json"}) is None


def test_accept_map_reject(db):
    store.seed_subtopics(db, ["Links"])
    for name in ("Image SEO", "Link building", "Guest posts"):
        store.subtopic_for_choice(db, {"propose": name}, create=True)
    mapped, rejected = _study(db, _id(db, "link-building")), _study(db, _id(db, "guest-posts"))
    assert store.accept_subtopic(db, "image-seo") == {"accepted": "image-seo"}
    assert store.map_subtopic(db, "link-building", "links") == {
        "mapped": "link-building", "into": "links", "moved_studies": 1}
    assert store.reject_subtopic(db, "guest-posts") == {"rejected": "guest-posts", "unsorted_studies": 1}
    assert store.get_study(db, mapped)["subtopic_id"] == _id(db, "links")
    assert store.get_study(db, rejected)["subtopic_id"] is None
    assert [t["slug"] for t in store.active_subtopics(db)] == ["links", "image-seo"]
    assert store.subtopic_for_choice(db, {"propose": "Link building"}) == (_id(db, "links"), None)


def test_review_refusals(db):
    store.seed_subtopics(db, ["Links", "Local"])
    store.subtopic_for_choice(db, {"propose": "Image SEO"}, create=True)
    with pytest.raises(ConfigError, match="no subtopic 'nope'"):
        store.accept_subtopic(db, "nope")
    with pytest.raises(ConfigError, match="not a proposal waiting for review"):
        store.accept_subtopic(db, "links")
    with pytest.raises(ConfigError, match="not a proposal waiting for review"):
        store.reject_subtopic(db, "local")
    with pytest.raises(ConfigError, match="'image-seo' is not an active subtopic"):
        store.map_subtopic(db, "image-seo", "image-seo")


def test_rename_keeps_the_slug_and_refuses_a_taken_name(db):
    store.seed_subtopics(db, ["Links", "Local"])
    assert store.rename_subtopic(db, "links", "  Link  building ") == {"renamed": "links", "name": "Link building"}
    assert store.get_subtopic(db, "links")["name"] == "Link building"
    with pytest.raises(ConfigError, match="already has the name"):
        store.rename_subtopic(db, "local", "link BUILDING")
    with pytest.raises(ConfigError, match="1 to 80 characters"):
        store.rename_subtopic(db, "local", "   ")


def test_merge_moves_studies_and_refuses_bad_pairs(db):
    store.seed_subtopics(db, ["Links", "Backlinks"])
    sid = _study(db, _id(db, "links"))
    assert store.merge_subtopics(db, "links", "backlinks") == {
        "merged": "links", "into": "backlinks", "moved_studies": 1}
    assert store.get_study(db, sid)["subtopic_id"] == _id(db, "backlinks")
    with pytest.raises(ConfigError, match="itself"):
        store.merge_subtopics(db, "backlinks", "backlinks")
    with pytest.raises(ConfigError, match="'links' is not an active subtopic"):
        store.merge_subtopics(db, "links", "backlinks")          # already merged away
    with pytest.raises(ConfigError, match="merged into"):
        store.rename_subtopic(db, "links", "Old links")


def test_subtopics_overview(db):
    store.seed_subtopics(db, ["Links"])
    store.subtopic_for_choice(db, {"propose": "Image SEO"}, create=True)
    _study(db, _id(db, "image-seo"))
    out = store.subtopics_overview(db)
    assert out["proposals"] == 1
    assert [(t["slug"], t["status"], t["studies"]) for t in out["subtopics"]] == [
        ("links", "seeded", 0), ("image-seo", "proposed", 1)]
    assert out["subtopics"][1]["examples"] == ["x"] and out["subtopics"][1]["merged_into"] is None
