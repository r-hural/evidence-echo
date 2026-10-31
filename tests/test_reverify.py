"""Reverify stage, owner rulings after a rules change, studies regrade, reset --stage regrade."""
import json
from contextlib import closing

import pytest

from evidence_echo import api
from evidence_echo.core import config, interfaces, store
from evidence_echo.core.settings import ConfigError
from evidence_echo.render import model as render_model
from evidence_echo.stages import regrade, report, reverify, verify
from tests.conftest import TEST_METRICS
from tests.test_regrade import _answer, _exercise, _hash, _seed, _with_grading


def _requeue(db):
    db.execute("UPDATE items SET graded_rules=NULL")
    db.commit()


def _clean(db):
    """The seeded quotes do not check out (quote_check none); give them a clean start."""
    db.execute("UPDATE items SET quote_check='ok', disputed=0, dispute_reason=NULL")
    db.commit()


def _regraded(db, ex, n=1, *, verified="A", tier="A", **kw):
    """A study graded earlier, then re-graded (judge raw `tier`): waiting for reverify."""
    iid = _seed(db, ex, n, verified=verified, **kw)
    _clean(db)
    db.execute("UPDATE items SET graded_rules=NULL WHERE id=?", (iid,))
    db.commit()
    out = regrade.apply(db, json.dumps({"results": [_answer(iid, tier=tier)]}), ex)
    assert out["applied"] == 1
    return iid


def _skeptic(iid, tier, flags=(), note="n"):
    return json.dumps({"results": [{"id": interfaces.opaque_id(iid), "tier": tier,
                                    "red_flags": list(flags), "note": note}]})


def test_reverify_selects_only_regraded_items(db):
    ex = _exercise()
    regraded = _regraded(db, ex, 1)
    fresh = _seed(db, ex, 2, verified="A")
    matched = _seed(db, ex, 3, verified=None)
    db.execute("UPDATE items SET graded_rules=NULL WHERE id=?", (matched,))
    db.commit()
    regrade.apply(db, json.dumps({"results": [_answer(matched)]}), ex)
    data = json.loads(reverify.emit(db, ex))
    assert data["stage"] == "reverify" and "grading" in data["topic"]
    assert [i["id"] for i in data["items"]] == [interfaces.opaque_id(regraded)]
    assert store.get_item(db, fresh)["verify_version"] == "verify-2"
    out = reverify.apply(db, _skeptic(regraded, "A"), ex)
    assert out == {"applied": 1, "rejected": [], "disputed": []}
    assert store.get_item(db, regraded)["verify_version"] == "verify-2"
    assert json.loads(reverify.emit(db, ex))["items"] == []


def test_reverify_dispute_is_asked(db):
    ex = _exercise()
    iid = _regraded(db, ex)
    out = reverify.apply(db, _skeptic(iid, "D"), ex)
    oid = interfaces.opaque_id(iid)
    assert out["disputed"] == [oid]
    item = store.get_item(db, iid)
    assert item["disputed"] == 1 and "grade_gap" in item["dispute_reason"]
    assert item["state"] == "verified"
    ruling = json.dumps({"results": [{"id": oid, "agrees": True, "adjusted_tier": "B", "note": "ruled"}]})
    assert verify.apply(db, ruling, ex, ruling=True)["applied"] == 1
    assert store.get_item(db, iid)["ruled_tier"] == "B"


def test_reverify_rejects_flag_of_another_field(db):
    ex = _exercise()
    iid = _regraded(db, ex)
    out = reverify.apply(db, _skeptic(iid, "A", flags=["benchmark_contamination"]), ex)
    assert out["applied"] == 0 and out["rejected"]
    assert len(json.loads(reverify.emit(db, ex))["items"]) == 1


def test_reverify_keeps_owner_ruling(db):
    ex = _exercise()
    iid = _seed(db, ex, 1, verified="D")
    ruling = json.dumps({"results": [{"id": interfaces.opaque_id(iid), "agrees": True,
                                      "adjusted_tier": "B", "note": "ruled"}]})
    verify.apply(db, ruling, ex, ruling=True)
    before = store.get_item(db, iid)
    assert before["evidence_tier"] == "B" and before["ruled_tier"] == "B" and before["disputed"] == 0
    # no cap: the skeptic says D, the ruling stays
    _requeue(db)
    regrade.apply(db, json.dumps({"results": [_answer(iid, tier="A")]}), ex)
    out = reverify.apply(db, _skeptic(iid, "D"), ex)
    assert out["disputed"] == []
    item = store.get_item(db, iid)
    assert item["evidence_tier"] == "B" and item["disputed"] == 0 and item["ruled_tier"] == "B"
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "B"
    # the rules now cap this study type at C: the ruled tier is re-capped, no new dispute
    caps = dict(ex.grading["caps"])
    caps["study_type"] = {**caps.get("study_type", {}), "rct": "C"}
    capped = _with_grading(ex, caps=caps)
    _requeue(db)
    regrade.apply(db, json.dumps({"results": [_answer(iid, tier="A")]}), capped)
    out = reverify.apply(db, _skeptic(iid, "A"), capped)
    assert out["disputed"] == []
    item = store.get_item(db, iid)
    assert item["evidence_tier"] == "C" and item["disputed"] == 0 and item["capped_from"] == "B"
    assert store.get_study(db, item["study_id"])["evidence_tier"] == "C"


def test_regrade_does_not_change_state_or_report_sections(db, tmp_path):
    ex = _exercise()
    iid = _seed(db, ex, 1, verified="A")
    _clean(db)
    out = report.render(db, '{"results": []}', tmp_path / "reports", "English", ex)
    assert "path" in out
    assert store.get_item(db, iid)["state"] == "reported"
    new0, seen0, _ = render_model.split(db)
    recent0 = store.recent_studies(db, 10)
    _requeue(db)
    regrade.apply(db, json.dumps({"results": [_answer(iid, tier="A")]}), ex)
    reverify.apply(db, _skeptic(iid, "D"), ex)       # a dispute on a reported study
    item = store.get_item(db, iid)
    assert item["state"] == "reported"
    new1, seen1, _ = render_model.split(db)
    assert set(new1) == set(new0) and set(seen1) == set(seen0)
    assert item["study_id"] not in new1
    assert store.recent_studies(db, 10) == recent0
    again = report.render(db, '{"results": []}', tmp_path / "reports", "English", ex)
    assert again.get("skipped") == "no_new_studies"


def test_reset_stage_regrade_requeues_all(workspace):
    cfg = config.load()
    with closing(store.connect(cfg.db_path)) as db:
        store.seed_metrics(db, TEST_METRICS)
        _seed(db, cfg.topic, 1)
        two = _seed(db, cfg.topic, 2)
        db.execute("UPDATE items SET graded_rules='unavailable' WHERE id=?", (two,))
        db.commit()
        assert store.items_to_regrade(db, _hash(cfg.topic)) == []
    assert api.reset(cfg, "regrade") == {"reset": "regrade", "items": 2}
    with closing(store.connect(cfg.db_path)) as db:
        assert len(store.items_to_regrade(db, _hash(cfg.topic))) == 2


def test_studies_regrade_one(workspace):
    cfg = config.load()
    with closing(store.connect(cfg.db_path)) as db:
        store.seed_metrics(db, TEST_METRICS)
        iid = _seed(db, cfg.topic, 1)
        other = _seed(db, cfg.topic, 2)
        sid = store.get_item(db, iid)["study_id"]
        orphan = store.get_item(db, other)["study_id"]
        db.execute("UPDATE studies SET created_by_item=NULL WHERE id=?", (orphan,))
        db.commit()
        assert store.items_to_regrade(db, _hash(cfg.topic)) == []
    assert api.studies_regrade(cfg, f"s{sid}") == {"study": f"s{sid}", "queued": True}
    with closing(store.connect(cfg.db_path)) as db:
        assert [i["id"] for i in store.items_to_regrade(db, _hash(cfg.topic))] == [iid]
    with pytest.raises(ConfigError, match="no creating item"):
        api.studies_regrade(cfg, f"s{orphan}")
    with pytest.raises(ConfigError, match="no study s999"):
        api.studies_regrade(cfg, "s999")


def test_cli_studies_regrade_parses():
    from evidence_echo import cli
    args = cli._parser().parse_args(["studies", "regrade", "s3"])
    assert args.action == "regrade" and args.sid == "s3"


def test_render_output_has_regrade_counts(workspace):
    cfg = config.load()
    with closing(store.connect(cfg.db_path)) as db:
        store.seed_metrics(db, TEST_METRICS)
        _seed(db, cfg.topic, 1)
        two = _seed(db, cfg.topic, 2)
        _requeue(db)
        regrade.apply(db, json.dumps({"results": [_answer(two, tier="A", study_type="other")]}), cfg.topic)
    out = api.render_report(cfg, '{"results": []}')
    assert out["regrade"] == {"done": 1, "pending": 1}


def test_run_api_reverifies(db):
    from tests.conftest import FakeBackend
    ex = _exercise()
    iid = _regraded(db, ex)
    backend = FakeBackend([json.dumps({"tier": "D", "red_flags": [], "note": "weak"})])
    out = reverify.run_api(db, backend, ex)
    assert out == {"processed": 1, "rejected": [], "disputed": [interfaces.opaque_id(iid)]}
    assert store.get_item(db, iid)["verify_version"] == "verify-2"


def test_reverify_keeps_ruling_that_agreed_with_the_judge(db):
    ex = _exercise()
    iid = _seed(db, ex, 1, verified="D")           # judge A (VERDICT), skeptic D: disputed
    ruling = json.dumps({"results": [{"id": interfaces.opaque_id(iid), "agrees": True, "note": "judge is right"}]})
    verify.apply(db, ruling, ex, ruling=True)
    before = store.get_item(db, iid)
    assert before["disputed"] == 0 and before["skeptic_tier"] is None
    _requeue(db)
    regrade.apply(db, json.dumps({"results": [_answer(iid, tier="A")]}), ex)
    out = reverify.apply(db, _skeptic(iid, "D"), ex)
    assert out["disputed"] == []
    item = store.get_item(db, iid)
    assert item["disputed"] == 0 and item["evidence_tier"] == before["evidence_tier"]


def test_ruling_that_agreed_with_the_judge_survives_a_second_rules_change(db):
    ex = _exercise()
    iid = _seed(db, ex, 1, verified="D")
    ruling = json.dumps({"results": [{"id": interfaces.opaque_id(iid), "agrees": True, "note": "judge is right"}]})
    verify.apply(db, ruling, ex, ruling=True)
    before = store.get_item(db, iid)
    for _ in range(2):                    # the signature (skeptic_tier NULL) is gone after the first reverify
        _requeue(db)
        db.execute("UPDATE items SET caps_hash=NULL")
        db.commit()
        regrade.recap(db, ex)
        regrade.apply(db, json.dumps({"results": [_answer(iid, tier="A")]}), ex)
        assert reverify.apply(db, _skeptic(iid, "D"), ex)["disputed"] == []
        item = store.get_item(db, iid)
        assert item["disputed"] == 0 and item["evidence_tier"] == before["evidence_tier"]
    assert item["ruled_tier"] == before["evidence_tier"]

def test_legacy_ruling_survives_regrade_then_reverify(db):
    ex = _exercise()
    iid = _seed(db, ex, 1, verified="D")
    # a ruling stored before step 15: final tier B, no ruled_tier, no raw tiers, no caps hash
    db.execute("UPDATE items SET evidence_tier='B', ruled_tier=NULL, skeptic_tier_raw=NULL, caps_hash=NULL, "
               "disputed=0, dispute_reason=NULL WHERE id=?", (iid,))
    db.commit()
    _requeue(db)
    regrade.recap(db, ex)
    assert store.get_item(db, iid)["ruled_tier"] == "B"
    regrade.apply(db, json.dumps({"results": [_answer(iid, tier="A")]}), ex)
    out = reverify.apply(db, _skeptic(iid, "D"), ex)
    assert out["disputed"] == []
    assert store.get_item(db, iid)["evidence_tier"] == "B"
