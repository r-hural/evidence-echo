import json
import sqlite3

from evidence_echo import api
from evidence_echo.core import store
from tests.conftest import make_item

FACTS = {"what_was_tested": "Title tags", "findings": "+4.2%", "research_timeframe": "2026",
         "sample_scale": "12k pages", "has_controls": True, "metric": "organic clicks",
         "baseline": "1,200/week"}
PROV = {"conducted_by": "Acme", "relation": "vendor", "conflict_of_interest": "sells the tool"}


def _judged(db, url="https://example.com/a", tier="B"):
    store.insert_items(db, [make_item(url=url)])
    iid = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()["id"]
    store.set_verdict(db, iid, True, tier, FACTS, None,
                      quotes=[{"number": "4.2%", "quote": "clicks rose 4.2% overall"}],
                      quote_check="ok", capped_from="A", judged_from="full_text", provenance=PROV)
    return iid


def test_set_verdict_stores_grading_fields(db):
    item = store.get_item(db, _judged(db))
    assert item["evidence_tier"] == item["judge_tier"] == "B"
    assert item["quote_check"] == "ok" and item["capped_from"] == "A"
    assert item["judged_from"] == "full_text"
    assert json.loads(item["provenance"]) == PROV and json.loads(item["quotes"])[0]["number"] == "4.2%"


def test_rejected_verdict_clears_grading_fields(db):
    iid = _judged(db)
    store.set_verdict(db, iid, False, None, {}, None)
    item = store.get_item(db, iid)
    assert item["state"] == "rejected"
    assert all(item[c] is None for c in ("quotes", "quote_check", "capped_from", "judged_from",
                                         "provenance", "judge_tier"))


def test_set_verdict_old_callers_still_work(db):
    store.insert_items(db, [make_item()])
    iid = db.execute("SELECT id FROM items").fetchone()["id"]
    store.set_verdict(db, iid, True, "C", {"what_was_tested": "x"}, None)
    assert store.get_item(db, iid)["quote_check"] is None


def test_new_study_takes_metric_baseline_provenance(db):
    iid = _judged(db)
    sid = store.create_study(db, "B", FACTS, None, provenance=PROV, created_by_item=iid)
    s = store.get_study(db, sid)
    assert (s["metric"], s["baseline"]) == ("organic clicks", "1,200/week")
    assert (s["conducted_by"], s["relation"], s["conflict_of_interest"]) == (
        "Acme", "vendor", "sells the tool")
    assert s["created_by_item"] == iid


def test_joining_item_fills_only_null_study_fields(db):
    sid = store.create_study(db, "B", {"what_was_tested": "x", "metric": "clicks"}, None)
    iid = _judged(db)
    store.assign_study(db, iid, sid, "same primary url")
    s = store.get_study(db, sid)
    assert s["metric"] == "clicks"                      # kept
    assert s["baseline"] == "1,200/week" and s["relation"] == "vendor"  # filled
    assert s["evidence_tier"] == "B" and s["created_by_item"] is None


def test_set_blind_grade_lowers_a_study_the_item_created(db):
    iid = _judged(db, tier="B")
    sid = store.create_study(db, "B", FACTS, None, provenance=PROV, created_by_item=iid)
    store.assign_study(db, iid, sid, "new study")
    store.set_blind_grade(db, iid, tier="C", red_flags=["no_baseline"], note="n",
                          final_tier="C", disputed=False, dispute_reason=None)
    item = store.get_item(db, iid)
    assert item["state"] == "verified" and item["evidence_tier"] == "C"
    assert item["judge_tier"] == "B" and item["skeptic_tier"] == "C" and item["skeptic_agrees"] == 0
    assert json.loads(item["red_flags"]) == ["no_baseline"] and item["disputed"] == 0
    assert store.get_study(db, sid)["evidence_tier"] == "C"


def test_set_blind_grade_dispute(db):
    iid = _judged(db, tier="A")
    store.set_blind_grade(db, iid, tier="C", red_flags=[], note="n",
                          final_tier="A", disputed=True, dispute_reason="grade_gap")
    item = store.get_item(db, iid)
    assert item["disputed"] == 1 and item["dispute_reason"] == "grade_gap"
    assert item["evidence_tier"] == "A"


def test_reset_verify_restores_judge_tier(workspace):
    from evidence_echo.core import config
    cfg = config.load("seo")
    with api._connect(cfg) as db:
        iid = _judged(db, tier="B")
        sid = store.create_study(db, "B", FACTS, None, provenance=PROV, created_by_item=iid)
        store.assign_study(db, iid, sid, "new study")
        store.set_blind_grade(db, iid, tier="C", red_flags=["no_baseline"], note="n",
                              final_tier="C", disputed=False, dispute_reason=None)
    api.reset(cfg, "verify")
    with api._connect(cfg) as db:
        item = store.get_item(db, iid)
        assert item["state"] == "matched" and item["evidence_tier"] == "B"
        assert item["skeptic_tier"] is None and item["red_flags"] is None
        assert item["disputed"] == 0 and item["dispute_reason"] is None
        assert store.get_study(db, sid)["evidence_tier"] == "B"


def test_reset_judge_clears_judge_fields(workspace):
    from evidence_echo.core import config
    cfg = config.load("seo")
    with api._connect(cfg) as db:
        iid = _judged(db, tier="B")
    api.reset(cfg, "judge")
    with api._connect(cfg) as db:
        item = store.get_item(db, iid)
        assert item["state"] == "extracted"
        assert all(item[c] is None for c in ("quotes", "quote_check", "capped_from", "judged_from",
                                             "provenance", "judge_tier"))


V07_ITEMS = """
CREATE TABLE items (id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, source_name TEXT NOT NULL,
  source_type TEXT NOT NULL, title TEXT DEFAULT '', excerpt TEXT DEFAULT '', full_text TEXT,
  published TEXT, fetched_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'fetched',
  triage_pass INTEGER, triage_reason TEXT, verdict_pass INTEGER, evidence_tier TEXT, facts TEXT,
  primary_url TEXT, skeptic_agrees INTEGER, skeptic_tier TEXT, skeptic_note TEXT,
  disputed INTEGER NOT NULL DEFAULT 0, study_id INTEGER, canonical_url TEXT, content_hash TEXT,
  text_source TEXT, fetch_status TEXT, date_unknown INTEGER NOT NULL DEFAULT 0,
  first_study_id INTEGER, match_reason TEXT);
CREATE TABLE studies (id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL, what_was_tested TEXT,
  findings TEXT, evidence_tier TEXT, research_timeframe TEXT, sample_scale TEXT, has_controls INTEGER,
  methodology_stated INTEGER, duration TEXT, entities TEXT, primary_url TEXT, series_id INTEGER,
  editions TEXT, primary_canonical TEXT, fingerprint TEXT, merged_into INTEGER, created_at TEXT NOT NULL);
INSERT INTO studies (id, canonical_name, evidence_tier, created_at) VALUES (1, 'Old', 'B', '2026-10-01');
INSERT INTO items (id, url, source_name, source_type, fetched_at, state, evidence_tier, study_id)
  VALUES (1, 'https://e.com/a', 'S', 'rss', '2026-10-01', 'matched', 'B', 1);
PRAGMA user_version = 2;
"""


def test_migration_step3_from_07(tmp_path):
    path = tmp_path / "echo.db"
    raw = sqlite3.connect(path)
    raw.executescript(V07_ITEMS)
    raw.close()
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    item = store.get_item(conn, 1)
    study = store.get_study(conn, 1)
    assert item["evidence_tier"] == "B" and item["state"] == "matched"
    assert all(item[c] is None for c in ("quotes", "quote_check", "capped_from", "judged_from",
                                         "red_flags", "dispute_reason", "provenance", "judge_tier"))
    assert all(study[c] is None for c in ("metric", "baseline", "conducted_by", "relation",
                                          "conflict_of_interest", "created_by_item"))
    conn.close()
    store.connect(path).close()                       # second open: nothing to do
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION


def test_reset_verify_after_pre_sp2_grade_keeps_the_judge_tier(workspace):
    # 0.7-migrated item: judge_tier NULL. A lowering blind grade, reset, then a grade two tiers
    # from the judge must be a dispute, not a one-tier step from the lowered tier.
    from evidence_echo.core import config, interfaces
    from evidence_echo.stages import verify
    cfg = config.load("seo")

    def grade(db, iid, tier):
        verify.apply(db, json.dumps({"results": [
            {"id": interfaces.opaque_id(iid), "tier": tier, "red_flags": [], "note": "n"}]}))

    with api._connect(cfg) as db:
        iid = _judged(db, tier="A")
        sid = store.create_study(db, "A", FACTS, None, provenance=PROV, created_by_item=iid)
        store.assign_study(db, iid, sid, "new study")
        db.execute("UPDATE items SET judge_tier=NULL, quote_check=NULL, state='matched' WHERE id=?",
                   (iid,))
        db.commit()
        grade(db, iid, "B")
        assert store.get_item(db, iid)["evidence_tier"] == "B"
        assert store.get_study(db, sid)["evidence_tier"] == "B"
    api.reset(cfg, "verify")
    with api._connect(cfg) as db:
        assert store.get_item(db, iid)["evidence_tier"] == "A"
        assert store.get_study(db, sid)["evidence_tier"] == "A"
        grade(db, iid, "C")
        item = store.get_item(db, iid)
        assert item["disputed"] == 1 and item["dispute_reason"] == "grade_gap"
        assert item["evidence_tier"] == "A"
        assert store.get_study(db, sid)["evidence_tier"] == "A"


# ---- schema step 15: grading facts, raw tiers, cap reasons, rule stamps, rulings ----
FACTS15 = {"what_was_tested": "T", "findings": "F", "study_type": "animal", "peer_review": "reviewed",
           "text_basis": "full"}
STAMPS15 = {"graded_rules": "h", "caps_hash": "c", "judge_version": "judge-2"}


def _judged15(conn):
    iid = store.insert_item(conn, make_item(url="https://example.com/g15"))
    store.set_verdict(conn, iid, True, "C", dict(FACTS15), None, capped_from="A", raw_tier="A",
                      cap_reasons=["animal"], stamps=STAMPS15)
    return iid


def test_set_verdict_stores_grading_facts_and_stamps(db):
    iid = _judged15(db)
    it = store.get_item(db, iid)
    assert (it["study_type"], it["peer_review"], it["text_basis"]) == ("animal", "reviewed", "full")
    assert it["judge_tier_raw"] == "A" and json.loads(it["cap_reasons"]) == ["animal"]
    assert (it["graded_rules"], it["caps_hash"], it["judge_version"]) == ("h", "c", "judge-2")
    sid = store.create_study(db, "C", dict(FACTS15), None, created_by_item=iid)
    st = store.get_study(db, sid)
    assert (st["study_type"], st["peer_review"], st["text_basis"]) == ("animal", "reviewed", "full")


def test_failing_verdict_clears_step15_columns(db):
    iid = _judged15(db)
    db.execute("UPDATE items SET skeptic_tier_raw='B', verify_version='verify-2', ruled_tier='C' WHERE id=?",
               (iid,))
    store.set_verdict(db, iid, False, None, {}, None, reason="no")
    it = store.get_item(db, iid)
    for col, _ in store.STEP15_ITEM_COLUMNS:
        assert it[col] is None, col


def test_set_blind_grade_keep_state(db):
    iid = _judged15(db)
    db.execute("UPDATE items SET state='reported' WHERE id=?", (iid,))
    store.set_blind_grade(db, iid, "C", [], "n", "C", False, None, raw_tier="B",
                          capped_from="B", cap_reasons=["animal"], verify_version="verify-2",
                          keep_state=True)
    it = store.get_item(db, iid)
    assert it["state"] == "reported" and it["skeptic_tier_raw"] == "B"
    assert it["verify_version"] == "verify-2" and it["capped_from"] == "B"
    assert json.loads(it["cap_reasons"]) == ["animal"]
    store.set_blind_grade(db, iid, "C", [], "n", "C", False, None)
    it = store.get_item(db, iid)
    assert it["state"] == "verified" and json.loads(it["cap_reasons"]) == ["animal"]   # not told: kept


def test_set_skeptic_keeps_reported_state(db):
    iid = _judged15(db)
    db.execute("UPDATE items SET state='reported', disputed=1 WHERE id=?", (iid,))
    store.set_skeptic(db, iid, True, "B", "ok", resolved=True)
    assert store.get_item(db, iid)["state"] == "reported"
    store.set_ruled_tier(db, iid, "B")
    it = store.get_item(db, iid)
    assert it["ruled_tier"] == "B" and it["evidence_tier"] == "B"
    db.execute("UPDATE items SET state='matched' WHERE id=?", (iid,))
    store.set_skeptic(db, iid, False, "A", "no")
    assert store.get_item(db, iid)["state"] == "verified"


def test_clear_blind_grades_clears_raw_and_version(db):
    iid = _judged15(db)
    db.execute("UPDATE items SET state='matched' WHERE id=?", (iid,))
    store.set_blind_grade(db, iid, "C", [], "n", "C", False, None, raw_tier="B", verify_version="verify-2")
    db.execute("UPDATE items SET state='matched' WHERE id=?", (iid,))
    store.clear_blind_grades(db)
    it = store.get_item(db, iid)
    assert it["skeptic_tier_raw"] is None and it["verify_version"] is None
