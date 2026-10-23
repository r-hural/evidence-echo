import json
from datetime import datetime

from conftest import make_item
from evidence_echo.core import legacy, store


def _row(db, item_id):
    return db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()


def _new(db, **over):
    return store.insert_item(db, make_item(**over))


def test_triage_fail_stamps_rejected_at(db):
    i = _new(db)
    store.set_triage(db, i, False, "no data")
    stamp = _row(db, i)["rejected_at"]
    assert stamp is not None
    datetime.fromisoformat(stamp)


def test_session_fetch_timeout_stamps_rejected_at(db):
    i = _new(db)
    store.set_item_fields(db, i, state="rejected_triage", triage_reason="x")
    assert _row(db, i)["rejected_at"] is not None


def test_leaving_rejected_clears(db):
    i = _new(db)
    store.set_triage(db, i, False, "no data")
    store.set_item_fields(db, i, state="fetched")
    assert _row(db, i)["rejected_at"] is None


def test_same_state_update_keeps_time(db):
    i = _new(db)
    store.set_triage(db, i, False, "no data")
    db.execute("UPDATE items SET rejected_at='2020-01-01T00:00:00.000+00:00' WHERE id=?", (i,))
    db.commit()
    store.set_item_fields(db, i, triage_reason="changed")
    assert _row(db, i)["rejected_at"] == "2020-01-01T00:00:00.000+00:00"


def test_legacy_import_leaves_rejected_at_null(db, tmp_path):
    p = tmp_path / "posts_cache.json"
    p.write_text(json.dumps({"posts": [
        {"id": "https://a.com/2", "source_name": "A", "source_type": "rss", "title": "T2",
         "summary": "S2", "url": "https://a.com/2", "published": "2026-03-10T00:00:00+00:00",
         "fetched_at": "2026-03-15T00:00:00+00:00", "passes": False,
         "evidence_type": "none", "reason": "opinion"}]}), encoding="utf-8")
    legacy.import_cache(db, p)
    row = db.execute("SELECT * FROM items WHERE url='https://a.com/2'").fetchone()
    assert row["state"] == "rejected"
    assert row["rejected_at"] is None


def test_step8_requeues_judge_fails(tmp_path):
    path = tmp_path / "v7.db"
    conn = store.connect(path)
    judge = store.insert_item(conn, make_item(url="https://a.com/j", state="rejected",
                                              full_text="body", text_source="page"))
    conn.execute("UPDATE items SET verdict_pass=0 WHERE id=?", (judge,))
    legacy_fail = store.insert_item(conn, make_item(url="https://a.com/l", state="rejected"))
    conn.execute("UPDATE items SET triage_pass=0 WHERE id=?", (legacy_fail,))
    triage = store.insert_item(conn, make_item(url="https://a.com/t", state="rejected_triage"))
    conn.execute("DROP TRIGGER items_rejected_at")
    conn.execute("ALTER TABLE items DROP COLUMN rejected_at")
    conn.execute("ALTER TABLE items DROP COLUMN judge_reason")
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()

    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    j = _row(conn, judge)
    assert j["state"] == "extracted" and j["verdict_pass"] is None and j["full_text"] == "body"
    assert _row(conn, legacy_fail)["state"] == "rejected"
    assert _row(conn, legacy_fail)["triage_pass"] == 0
    assert _row(conn, triage)["state"] == "rejected_triage"
    before = [tuple(r) for r in conn.execute("SELECT * FROM items ORDER BY id")]
    conn.close()
    conn = store.connect(path)
    assert [tuple(r) for r in conn.execute("SELECT * FROM items ORDER BY id")] == before
    conn.close()


def test_latest_report_time(db):
    assert store.latest_report_time(db) is None
    store.record_report(db, "a.html", [])
    second = store.record_report(db, "b.html", [])
    expected = db.execute("SELECT rendered_at FROM reports WHERE id=?", (second,)).fetchone()[0]
    assert store.latest_report_time(db) == expected


def test_rejected_items(db):
    a = _new(db, url="https://a.com/a")
    b = _new(db, url="https://a.com/b")
    _new(db, url="https://a.com/c")
    store.set_triage(db, a, False, "no")
    store.set_item_fields(db, b, state="rejected")
    rows = store.rejected_items(db)
    assert {r["id"] for r in rows} == {a, b}
    assert "rejected_at" in rows[0]
