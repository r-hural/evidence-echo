import json

from evidence_echo.core import store, legacy


LEGACY = {"posts": [
    {"id": "https://a.com/1", "source_name": "A", "source_type": "rss",
     "title": "T1", "summary": "S1", "url": "https://a.com/1",
     "published": "2026-03-10T00:00:00+00:00", "fetched_at": "2026-03-15T00:00:00+00:00",
     "passes": True, "evidence_type": "ab_test", "reason": "controlled test"},
    {"id": "https://a.com/2", "source_name": "A", "source_type": "rss",
     "title": "T2", "summary": "S2", "url": "https://a.com/2",
     "published": "2026-03-10T00:00:00+00:00", "fetched_at": "2026-03-15T00:00:00+00:00",
     "passes": False, "evidence_type": "none", "reason": "opinion"},
    {"id": "https://a.com/3", "source_name": "A", "source_type": "rss",
     "title": "T3", "summary": "S3", "url": "https://a.com/3",
     "published": "2026-03-10T00:00:00+00:00", "fetched_at": "2026-03-15T00:00:00+00:00",
     "passes": None, "evidence_type": "unclassified", "reason": ""},
]}


def test_import_legacy(db, tmp_path):
    p = tmp_path / "posts_cache.json"
    p.write_text(json.dumps(LEGACY), encoding="utf-8")
    stats = legacy.import_cache(db, p)
    assert stats == {"verified": 1, "rejected": 1, "fetched": 1}
    row = store.items_in_state(db, "verified")[0]
    assert row["evidence_tier"] == "A"
    assert row["study_id"] is not None
    assert legacy.import_cache(db, p) == {"verified": 0, "rejected": 0, "fetched": 0}


def test_legacy_import_ends_verified_with_a_study(db, tmp_path):
    p = tmp_path / "posts_cache.json"
    p.write_text(json.dumps(LEGACY), encoding="utf-8")
    legacy.import_cache(db, p)
    item = store.items_in_state(db, "verified")[0]
    assert item["match_reason"] == "legacy import" and item["first_study_id"] == item["study_id"]
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "A"
