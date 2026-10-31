"""Metrics in the store: seeding, the legacy and single-option fills, the owner's operations."""
import pytest

from evidence_echo.core import store
from evidence_echo.core.settings import ConfigError

LEGACY = {"traffic": "Organic traffic", "rankings": "Rankings", "ctr": "CTR"}


def _study(bare_db, code=None, metric_id=None):
    sid = store.create_study(bare_db, "B", {"what_was_tested": "x", "metric": "free"}, None)
    bare_db.execute("UPDATE studies SET metric_code=?, metric_id=? WHERE id=?", (code, metric_id, sid))
    bare_db.commit()
    return sid


def _metric_of(bare_db, sid):
    metric = store.study_metric(bare_db, store.get_study(bare_db, sid))
    return metric["slug"] if metric else None


def test_seed_metrics_order_and_other_last(bare_db):
    store.seed_metrics(bare_db, ["Rankings", "AI citations"])
    assert [m["slug"] for m in store.active_metrics(bare_db)] == ["rankings", "ai-citations", "other"]
    store.seed_metrics(bare_db, ["AI citations", "Rankings"])
    assert [m["slug"] for m in store.active_metrics(bare_db)] == ["ai-citations", "rankings", "other"]
    store.seed_metrics(bare_db, ["AI citations", "Rankings"])           # idempotent
    assert len(store.list_metrics(bare_db)) == 3
    assert [(m["name"], m["status"]) for m in store.active_metrics(bare_db)][-1] == ("Other", "seeded")


def test_seed_metrics_listed_other_stays_last_and_single(bare_db):
    store.seed_metrics(bare_db, ["Other", "Clicks", "other"])
    assert [m["slug"] for m in store.active_metrics(bare_db)] == ["clicks", "other"]


def test_seed_metrics_matches_renamed_and_leaves_rejected_alone(bare_db):
    store.seed_metrics(bare_db, ["Rankings"])
    store.rename_metric(bare_db, "rankings", "Positions")
    store.seed_metrics(bare_db, ["Rankings"])
    assert len(store.list_metrics(bare_db)) == 2
    bare_db.execute("UPDATE metrics SET status='rejected' WHERE slug='rankings'")
    bare_db.commit()
    store.seed_metrics(bare_db, ["Rankings"])
    assert store.get_metric(bare_db, "rankings")["status"] == "rejected"


def test_other_cannot_be_rejected_retired_or_merged(bare_db):
    store.seed_metrics(bare_db, ["Clicks"])
    with pytest.raises(ConfigError, match='"Other" cannot be rejected'):
        store.reject_metric(bare_db, "other")
    with pytest.raises(ConfigError, match='"Other" cannot be merged'):
        store.merge_metrics(bare_db, "other", "clicks")
    with pytest.raises(ConfigError, match='"Other" cannot be accepted'):
        store.accept_metric(bare_db, "other")
    bare_db.execute("UPDATE metrics SET status='retired', merged_into=1 WHERE slug='other'")
    bare_db.commit()
    store.seed_metrics(bare_db, ["Clicks"])                              # seeding puts it back
    other = store.get_metric(bare_db, "other")
    assert other["status"] == "seeded" and other["merged_into"] is None
    assert store.active_metrics(bare_db)[-1]["slug"] == "other"


def test_legacy_fill_maps_only_to_active_listed_names(bare_db):
    a, b, c, d = (_study(bare_db, "traffic"), _study(bare_db, "traffic"), _study(bare_db, "other"), _study(bare_db, "rankings"))
    store.seed_metrics(bare_db, ["Rankings", "Organic traffic"], LEGACY)
    assert [_metric_of(bare_db, s) for s in (a, b, c, d)] == ["organic-traffic", "organic-traffic", None, "rankings"]
    only = _study(bare_db, "traffic")
    store.seed_metrics(bare_db, ["Rankings"], {"traffic": "Not listed"})
    assert _metric_of(bare_db, only) is None


def test_legacy_fill_skips_unlisted_target(bare_db):
    sid = _study(bare_db, "traffic")
    store.seed_metrics(bare_db, ["Rankings", "Clicks"], LEGACY)          # "Organic traffic" is not listed
    assert _metric_of(bare_db, sid) is None


def test_legacy_fill_maps_a_code_equal_to_an_active_slug(bare_db):
    sid = _study(bare_db, "ai-citations")
    store.seed_metrics(bare_db, ["AI citations", "Clicks"], LEGACY)
    assert _metric_of(bare_db, sid) == "ai-citations"


def test_single_option_fill(bare_db):
    a, b = _study(bare_db), _study(bare_db, "traffic")
    merged = _study(bare_db)
    bare_db.execute("UPDATE studies SET merged_into=? WHERE id=?", (a, merged))
    bare_db.commit()
    store.seed_metrics(bare_db, [])
    assert _metric_of(bare_db, a) == "other" and _metric_of(bare_db, b) == "other"
    assert store.get_study(bare_db, merged)["metric_id"] is None
    # with two metrics nothing is filled
    c = _study(bare_db)
    store.seed_metrics(bare_db, ["Clicks"])
    assert _metric_of(bare_db, c) is None


def test_metric_owner_ops(bare_db):
    store.seed_metrics(bare_db, ["Clicks", "Rankings"])
    a = _study(bare_db, metric_id=store.get_metric(bare_db, "clicks")["id"])
    b = _study(bare_db)
    proposed = store._insert_metric(bare_db, "AI citations", "proposed")
    store._insert_metric(bare_db, "Visits", "proposed")
    bare_db.execute("UPDATE studies SET metric_id=? WHERE id=?", (proposed, b))
    bare_db.commit()
    assert store.accept_metric(bare_db, "ai-citations") == {"accepted": "ai-citations"}
    assert store.get_metric(bare_db, "ai-citations")["status"] == "confirmed"
    with pytest.raises(ConfigError, match="not a proposal"):
        store.accept_metric(bare_db, "ai-citations")
    out = store.reject_metric(bare_db, "visits")
    assert out["rejected"] == "visits" and store.get_metric(bare_db, "visits")["status"] == "rejected"
    # rename keeps the slug
    store.rename_metric(bare_db, "rankings", "Positions")
    assert store.get_metric(bare_db, "rankings")["name"] == "Positions"
    with pytest.raises(ConfigError, match="already has the name"):
        store.rename_metric(bare_db, "rankings", "clicks")
    with pytest.raises(ConfigError, match="1 to 80"):
        store.rename_metric(bare_db, "rankings", "x" * 81)
    # merge moves studies and resolves
    out = store.merge_metrics(bare_db, "clicks", "rankings")
    assert out == {"merged": "clicks", "into": "rankings", "moved_studies": 1, "moved_findings": 0}
    assert _metric_of(bare_db, a) == "rankings"
    assert store.metric_for_slug(bare_db, "clicks")["slug"] == "rankings"
    with pytest.raises(ConfigError, match="not an active"):
        store.merge_metrics(bare_db, "clicks", "rankings")
    # set_study_metric refuses an inactive slug
    with pytest.raises(ConfigError, match="not an active"):
        store.set_study_metric(bare_db, b, "visits")
    with pytest.raises(ConfigError, match="not an active"):
        store.set_study_metric(bare_db, b, "nope")
    assert store.set_study_metric(bare_db, b, "other") == {"study": b, "metric": "other"}
    assert _metric_of(bare_db, b) == "other"


def test_reject_sends_studies_to_no_metric(bare_db):
    store.seed_metrics(bare_db, ["Clicks"])
    pid = store._insert_metric(bare_db, "Visits", "proposed")
    sid = _study(bare_db, metric_id=pid)
    assert store.reject_metric(bare_db, "visits")["studies_without_metric"] == 1
    assert store.get_study(bare_db, sid)["metric_id"] is None


def test_metrics_overview_rows(bare_db):
    store.seed_metrics(bare_db, ["Clicks", "Rankings"])
    cid = store.get_metric(bare_db, "clicks")["id"]
    for _ in range(4):
        _study(bare_db, metric_id=cid)
    overview = store.metrics_overview(bare_db)
    assert overview["proposals"] == 0
    rows = {r["slug"]: r for r in overview["metrics"]}
    assert [r["slug"] for r in overview["metrics"]] == ["clicks", "rankings", "other"]
    assert rows["clicks"]["studies"] == 4 and len(rows["clicks"]["examples"]) == 3
    assert rows["clicks"]["split"] is None and rows["rankings"]["studies"] == 0
    assert rows["clicks"]["merged_into"] is None
