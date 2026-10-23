import json
from types import SimpleNamespace

from evidence_echo.core import interfaces, store
from evidence_echo.stages import judge, match, report, verify
from tests.conftest import make_item
from tests.test_judge import VERDICT

TOPIC = SimpleNamespace(id="seo", name="SEO", domain="SEO", scope="s", subtopics=[])


def _seed_verified(db) -> int:
    store.insert_items(db, [make_item()])
    [it] = store.items_in_state(db, "fetched")
    store.set_full_text(db, it["id"], "text")
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(it["id"]))]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [{"id": interfaces.opaque_id(it["id"]),
                                              "tier": "A", "red_flags": [], "note": "ok"}]}))
    return store.get_item(db, it["id"])["study_id"]


def test_latest_report_none_when_empty(db):
    assert store.latest_report(db) is None


def test_record_and_read_latest(db):
    sid = store.create_study(db, "B", {"what_was_tested": "x"}, None)
    db.commit()
    store.record_report(db, "/r/1.md", [(sid, "First")])
    rid = store.record_report(db, "/r/2.md", [(sid, "Second")])
    latest = store.latest_report(db)
    assert latest["path"] == "/r/2.md" and rid
    assert latest["entries"] == [{"study_id": sid, "title": "Second", "tier": "B",
                                  "published": None}]


def test_render_records_titles_including_rejected_fallback(db, tmp_path):
    sid = _seed_verified(db)
    out = report.render(db, json.dumps({"results": [
        {"study_id": sid, "title": "Result-first title", "summary": "S"}]}),
        tmp_path, "en", TOPIC)
    latest = store.latest_report(db)
    assert latest["path"] == out["path"]
    assert [e["title"] for e in latest["entries"]] == ["Result-first title"]
