"""Split detection: which subtopic or metric is overloaded or bloated (thresholds, blocks, re-arm)."""
from evidence_echo.core import knowledge, store
from tests.split_fixtures import add_many, add_study, with_decided_proposal

SUBS = ["Links", "Local", "Speed", "Other"]


def _targets(db):
    return [(t["target"], t["slug"], t["count"]) for t in knowledge.split_targets(db)]


def _db(db):
    store.seed_subtopics(db, SUBS)
    return db


def test_constants():
    assert (knowledge.SPLIT_MIN_STUDIES, knowledge.SPLIT_SUBTOPIC_SHARE) == (15, 0.30)
    assert (knowledge.SPLIT_OTHER_MIN, knowledge.SPLIT_OTHER_SHARE, knowledge.SPLIT_REARM) == (10, 0.20, 1.25)
    assert (knowledge.SPLIT_MAX_STUDIES, knowledge.SPLIT_MIN_GROUP) == (150, 3)
    assert knowledge.SPLIT_SUBTOPIC_GROUPS == (2, 8) and knowledge.SPLIT_METRIC_GROUPS == (1, 10)


def test_overloaded_subtopic_thresholds(db):
    _db(db)
    add_many(db, 15, subtopic="links", metric="clicks")
    add_many(db, 35, metric="clicks")                          # unsorted: N = 50, links = 15 = 30%: not more than
    assert [t for t in _targets(db) if t[0] == "subtopic"] == []
    add_study(db, subtopic="links", metric="clicks")           # 16 of 51 > 15.3
    assert ("subtopic", "links", 16) in _targets(db)


def test_overloaded_subtopic_needs_the_minimum_count(db):
    _db(db)
    add_many(db, 14, subtopic="links", metric="clicks")
    add_many(db, 26, subtopic="local", metric="clicks")        # links: 14 of 40 = 35% but under 15
    assert [t[1] for t in _targets(db) if t[0] == "subtopic"] == ["local"]
    add_many(db, 60, subtopic="speed", metric="clicks")        # N = 100; links 14, local 26, speed 60
    assert [t[1] for t in _targets(db) if t[0] == "subtopic"] == ["speed"]       # largest; local 26% no


def test_overloaded_subtopic_share_at_a_hundred(db):
    _db(db)
    add_many(db, 31, subtopic="links", metric="clicks")
    add_many(db, 69, subtopic="local", metric="clicks")
    hits = [t for t in _targets(db) if t[0] == "subtopic"]
    assert hits == [("subtopic", "local", 69)]                  # at most one: the largest (links 31 also qualifies)


def test_other_subtopic_and_other_metric_thresholds(db):
    _db(db)
    add_many(db, 10, subtopic="other", metric="clicks")
    add_many(db, 39, metric="clicks")                          # N = 49: 10 > 9.8
    assert ("subtopic", "other", 10) in _targets(db)
    db.execute("DELETE FROM studies WHERE id IN (SELECT id FROM studies WHERE subtopic_id=? LIMIT 1)",
               (store.get_subtopic(db, "other")["id"],))
    db.commit()
    add_study(db, metric="clicks")                             # 9 of 49 again: under the minimum
    assert ("subtopic", "other", 9) not in _targets(db) and not [t for t in _targets(db) if t[1] == "other"]


def test_other_subtopic_exactly_twenty_percent_does_not_fire(db):
    _db(db)
    add_many(db, 10, subtopic="other", metric="clicks")
    add_many(db, 40, subtopic="local", metric="clicks")        # 10 of 50 = 20%: not more than
    assert not [t for t in _targets(db) if t[1] == "other"]


def test_other_metric_thresholds(db):
    _db(db)
    add_many(db, 10, subtopic="links", metric="other")
    add_many(db, 39, subtopic="local", metric="clicks")        # N = 49
    assert ("metric", "other", 10) in _targets(db)
    add_study(db, subtopic="local", metric="clicks")           # N = 50: exactly 20%
    assert not [t for t in _targets(db) if t[0] == "metric"]


def test_other_metric_below_the_minimum(db):
    _db(db)
    add_many(db, 9, subtopic="links", metric="other")
    add_many(db, 11, subtopic="local", metric="clicks")        # 45%, but 9 studies
    assert not [t for t in _targets(db) if t[0] == "metric"]


def test_studies_without_a_metric_do_not_count_as_other(db):
    _db(db)
    add_many(db, 12, subtopic="links")                        # no metric at all
    add_many(db, 3, subtopic="local", metric="clicks")
    assert not [t for t in _targets(db) if t[0] == "metric"]


def test_pending_proposal_blocks_target(db):
    _db(db)
    add_many(db, 20, subtopic="links", metric="other")
    add_many(db, 10, subtopic="local", metric="clicks")
    assert {t[0] for t in _targets(db)} == {"subtopic", "metric"}
    links, other = store.get_subtopic(db, "links"), store.get_metric(db, "other")
    for target, source in (("subtopic", links), ("metric", other)):
        db.execute("""INSERT INTO split_proposals (target, source_id, studies_at, status, created_at)
                      VALUES (?,?,30,'pending','x')""", (target, source["id"]))
    db.commit()
    assert _targets(db) == []


def test_rearm_after_decided_proposal(db):
    _db(db)
    add_many(db, 40, subtopic="links", metric="clicks")
    with_decided_proposal(db, "subtopic", "links", 40)
    add_many(db, 9, subtopic="links", metric="clicks")
    assert [t for t in _targets(db) if t[1] == "links"] == []          # 49 < 1.25 * 40
    add_study(db, subtopic="links", metric="clicks")
    assert [t[2] for t in _targets(db) if t[1] == "links"] == [50]    # 50 >= 1.25 * 40: fires again


def test_rearm_uses_the_latest_decided_proposal(db):
    _db(db)
    add_many(db, 60, subtopic="links", metric="clicks")
    with_decided_proposal(db, "subtopic", "links", 40)
    with_decided_proposal(db, "subtopic", "links", 60)                 # the latest one counts
    assert [t for t in _targets(db) if t[1] == "links"] == []


def test_at_most_one_subtopic_target(db):
    _db(db)
    add_many(db, 20, subtopic="links", metric="clicks")
    add_many(db, 25, subtopic="local", metric="clicks")
    add_many(db, 12, subtopic="other", metric="clicks")
    hits = [t for t in _targets(db) if t[0] == "subtopic"]
    assert hits == [("subtopic", "local", 25)]


def test_metric_target_on_other_only_topic(bare_db):
    db = bare_db
    store.seed_metrics(db, [])                                # a blank topic: Other is the only metric
    store.seed_subtopics(db, SUBS)
    for _ in range(10):
        add_study(db, subtopic="links")
    for _ in range(3):
        add_study(db, subtopic="local")
    store.seed_metrics(db, [])                                # the single-option fill gives them all Other
    assert _targets(db) == [("metric", "other", 13)]
    assert knowledge.split_targets(db)[0]["source_id"] == store.get_metric(db, "other")["id"]


def test_unsorted_studies_do_not_count_for_a_subtopic(db):
    _db(db)
    add_many(db, 30, metric="clicks")                          # no subtopic
    assert [t for t in _targets(db) if t[0] == "subtopic"] == []
