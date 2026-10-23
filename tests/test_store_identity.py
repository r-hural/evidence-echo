import json

from evidence_echo.core import store
from tests.conftest import make_item


def _item(db, url="https://e.com/a", facts=None, tier="B", primary=None):
    store.insert_items(db, [make_item(url=url)])
    item_id = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    db.execute("UPDATE items SET state='judged', verdict_pass=1, evidence_tier=?, facts=?, primary_url=? "
               "WHERE id=?", (tier, json.dumps(facts or {}), primary, item_id))
    db.commit()
    return item_id


FACTS = {"what_was_tested": "AI tool use", "findings": "44% -> 79%", "research_timeframe": "2023-2025",
         "series": "Stack Overflow Developer Survey 2025", "editions": ["2023", "2025"],
         "match_keys": {"key_numbers": ["44%", "79%"]}}


def test_create_study_fills_identity_fields(db):
    sid = store.create_study(db, "B", FACTS, "https://survey.stackoverflow.co/2025/?utm_source=x")
    s = store.get_study(db, sid)
    assert s["canonical_name"] == "AI tool use"
    assert s["primary_canonical"] == "https://survey.stackoverflow.co/2025/"
    assert store.study_lists(s) == (["2023", "2025"], ["44%", "79%"])
    series = db.execute("SELECT * FROM series WHERE id=?", (s["series_id"],)).fetchone()
    assert series["key"] == "stack overflow developer survey"
    assert store.get_or_create_series(db, "stack overflow developer-survey") == s["series_id"]
    assert store.get_or_create_series(db, "2025") is None


def test_assign_study_enriches_and_keeps_tier(db):
    sid = store.create_study(db, "A", {"what_was_tested": "x", "findings": "y",
                                       "research_timeframe": "z"}, None)
    item_id = _item(db, facts=FACTS, tier="C", primary="https://p.example/r")
    store.assign_study(db, item_id, sid, "same as s1")
    s = store.get_study(db, sid)
    assert s["evidence_tier"] == "A"
    assert store.study_lists(s) == (["2023", "2025"], ["44%", "79%"])
    assert s["primary_url"] == "https://p.example/r"
    it = store.get_item(db, item_id)
    assert (it["state"], it["study_id"], it["first_study_id"], it["match_reason"]) == ("matched", sid, sid, "same as s1")


def test_resolve_and_active_studies(db):
    a = store.create_study(db, "B", FACTS, None)
    b = store.create_study(db, "B", FACTS, None)
    c = store.create_study(db, "B", FACTS, None)
    db.execute("UPDATE studies SET merged_into=? WHERE id=?", (b, a))
    db.execute("UPDATE studies SET merged_into=? WHERE id=?", (c, b))
    assert store.resolve_study(db, a) == c
    active = store.active_studies(db)
    assert [s["id"] for s in active] == [c]
    assert active[0]["series_key"] == "stack overflow developer survey"
    assert active[0]["fingerprint"] == ["44%", "79%"] and active[0]["editions"] == ["2023", "2025"]
