"""Wave 4a, SP3b fixes: picks of proposed/accepted/mapped/rejected/retired subtopics, seeding by
name, display order from topic.json."""
from evidence_echo.core import knowledge, store
from tests.test_knowledge_questions import add


def _id(db, slug):
    return store.get_subtopic(db, slug)["id"]


def _retire(db, slug):
    db.execute("UPDATE subtopics SET status='retired' WHERE slug=?", (slug,))
    db.commit()


def test_slug_picks_follow_the_proposal_and_retired_is_no_subtopic(db):
    store.seed_subtopics(db, ["Links", "Local"])
    pid, _ = store.subtopic_for_choice(db, {"propose": "Image SEO"}, create=True)
    assert store.subtopic_for_choice(db, {"slug": "image-seo"}) == (pid, None)        # pending
    store.accept_subtopic(db, "image-seo")
    assert store.subtopic_for_choice(db, {"slug": "image-seo"}) == (pid, None)        # accepted
    store.subtopic_for_choice(db, {"propose": "Link building"}, create=True)
    store.map_subtopic(db, "link-building", "links")
    assert store.subtopic_for_choice(db, {"slug": "link-building"}) == (_id(db, "links"), None)
    store.subtopic_for_choice(db, {"propose": "Guest posts"}, create=True)
    store.reject_subtopic(db, "guest-posts")
    assert store.subtopic_for_choice(db, {"slug": "guest-posts"}) == (None, None)     # rejected
    _retire(db, "local")
    count = len(store.list_subtopics(db))
    assert store.subtopic_for_choice(db, {"slug": "local"}) == (None, None)           # retired
    assert store.subtopic_for_choice(db, {"propose": "Local"}, create=True) == (None, None)
    _retire(db, "links")                                                           # mapped into retired
    assert store.subtopic_for_choice(db, {"slug": "link-building"}) == (None, None)
    assert len(store.list_subtopics(db)) == count                                     # nothing created


def test_retired_subtopic_is_not_offered_and_its_studies_are_unsorted(db):
    store.seed_subtopics(db, ["Links", "Local"])
    add(db, subtopic="local", by="A")
    _retire(db, "local")
    assert [t["slug"] for t in store.active_subtopics(db)] == ["links"]
    assert [e.key for e in knowledge.evidence(db)] == [knowledge.UNSORTED]
    assert [t["status"] for t in store.subtopics_overview(db)["subtopics"]] == ["seeded", "retired"]


def test_a_renamed_subtopic_listed_under_its_new_name_is_not_duplicated(db):
    store.seed_subtopics(db, ["Links", "AI search / GEO"])
    store.rename_subtopic(db, "ai-search-geo", "AI Overviews & AI search")
    assert store.seed_subtopics(db, ["Links", "  ai overviews &  AI search"]) == 0
    assert [t["slug"] for t in store.list_subtopics(db)] == ["links", "ai-search-geo"]


def test_seeding_never_revives_rejected_or_retired_subtopics(db):
    store.seed_subtopics(db, ["Links"])
    store.subtopic_for_choice(db, {"propose": "Guest posts"}, create=True)
    store.reject_subtopic(db, "guest-posts")
    _retire(db, "links")
    assert store.seed_subtopics(db, ["Links", "Guest posts"]) == 0
    assert {t["slug"]: t["status"] for t in store.list_subtopics(db)} == {
        "links": "retired", "guest-posts": "rejected"}


def test_display_order_follows_the_topic_list_not_the_id(db):
    store.seed_subtopics(db, ["Links", "Local", "Backlinks"])
    store.subtopic_for_choice(db, {"propose": "Image SEO"}, create=True)
    store.accept_subtopic(db, "image-seo")
    store.seed_subtopics(db, ["Backlinks", "Links", "Local"])
    assert [t["slug"] for t in store.active_subtopics(db)] == ["backlinks", "links", "local", "image-seo"]
    add(db, subtopic="links", by="A"); add(db, subtopic="backlinks", by="B")
    assert [e.key for e in knowledge.evidence(db)] == ["backlinks", "links"]


def test_a_seeded_subtopic_dropped_from_the_list_goes_after_the_listed_ones(db):
    store.seed_subtopics(db, ["Links", "Local"])
    store.seed_subtopics(db, ["Local"])
    assert [t["slug"] for t in store.active_subtopics(db)] == ["local", "links"]


def test_seed_subtopics_writes_nothing_when_nothing_changes(db):
    """Review M4: a second seeding with the same list is a read, not a write."""
    names = ["Links", "Local"]
    store.seed_subtopics(db, names)
    before = db.total_changes
    assert store.seed_subtopics(db, names) == 0
    assert db.total_changes == before
    store.seed_subtopics(db, ["Local", "Links"])                      # order changed: positions move
    assert [t["position"] for t in store.list_subtopics(db)] == [1, 0]


def test_joining_item_naming_a_retired_subtopic_leaves_the_study_unsorted(db):
    """Review M8/RF4: assign_study resolves a joiner's stored subtopic pick; a retired one fills nothing."""
    import json
    from tests.conftest import make_item
    store.seed_subtopics(db, ["Links", "Local"])
    _retire(db, "local")
    sid = store.create_study(db, "B", {"what_was_tested": "x"}, None)
    out = {}
    for slug in ("local", "links"):
        url = f"https://e.com/{slug}"
        store.insert_items(db, [make_item(url=url)])
        iid = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
        db.execute("UPDATE items SET facts='{}', subtopic_choice=? WHERE id=?",
                   (json.dumps({"slug": slug}), iid))
        db.commit()
        store.assign_study(db, iid, sid, "test")
        out[slug] = store.get_study(db, sid)["subtopic_id"]
    assert out == {"local": None, "links": _id(db, "links")}
