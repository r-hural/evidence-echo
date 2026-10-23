import json

import pytest

from evidence_echo.core import store
from evidence_echo.core.settings import ConfigError
from tests.test_match import F, judged, study_of
from evidence_echo.stages import match


def two_studies(db):
    a = judged(db, "https://e.com/a", F(["44%", "79%"], series="SO Survey", editions=["2025"]),
               primary="https://survey.example/2025")
    b = judged(db, "https://e.com/b", F(["44%", "79%", "12%"], editions=["2023"]))
    match.fixed_pass(db)                          # b waits: shares numbers with a
    match.apply(db, json.dumps({"results": [{"id": f"i{b}", "same_as": None, "reason": "unsure"}]}))
    return study_of(db, a), study_of(db, b), a, b


def test_duplicate_pairs_rules(db):
    sa, sb, _, _ = two_studies(db)
    pairs = store.duplicate_pairs(db)
    assert [(p["keep"], p["other"], p["rule"]) for p in pairs] == [(sa, sb, "shared key numbers: 44%, 79%")]
    sc = store.create_study(db, "B", F(series="SO Survey 2025", editions=["2025"]), None)
    rules = [p["rule"] for p in store.duplicate_pairs(db)]
    assert rules[0] == "same series and edition"
    sd = store.create_study(db, "B", F(), "https://survey.example/2025?utm_source=z")
    assert store.duplicate_pairs(db)[0]["rule"] == "same primary url"


def test_merge_and_unmerge_round_trip(db):
    sa, sb, a, b = two_studies(db)
    out = store.merge_studies(db, sa, sb)
    assert out == {"kept": sa, "merged": sb, "moved_items": 1}
    assert study_of(db, b) == sa and store.get_study(db, sb)["merged_into"] == sa
    assert store.study_lists(store.get_study(db, sa)) == (["2023", "2025"], ["12%", "44%", "79%"])
    assert store.duplicate_pairs(db) == []
    out = store.unmerge_study(db, sb)
    assert out == {"restored": sb, "from": sa, "moved_items": 1}
    assert study_of(db, b) == sb and store.get_study(db, sb)["merged_into"] is None
    assert store.study_lists(store.get_study(db, sa)) == (["2025"], ["44%", "79%"])
    assert store.study_lists(store.get_study(db, sb)) == (["2023"], ["12%", "44%", "79%"])


@pytest.mark.parametrize("pair", [("a", "a"), ("a", 999), (999, "a")])
def test_merge_refusals(db, pair):
    sa, sb, _, _ = two_studies(db)
    ids = {"a": sa}
    keep, other = (ids.get(x, x) for x in pair)
    with pytest.raises(ConfigError):
        store.merge_studies(db, keep, other)


def test_merge_refuses_merged_studies_and_unmerge_refuses_unmerged(db):
    sa, sb, _, _ = two_studies(db)
    store.merge_studies(db, sa, sb)
    with pytest.raises(ConfigError, match="already merged"):
        store.merge_studies(db, sb, sa)
    with pytest.raises(ConfigError, match="was never merged"):
        store.unmerge_study(db, sa)


def test_first_reports_resolve_merges(db):
    sa, sb, _, _ = two_studies(db)
    store.record_report(db, "r1.md", [(sb, "B title")])
    store.record_report(db, "r2.md", [(sa, "A title")])
    store.merge_studies(db, sa, sb)
    first = store.first_reports(db)
    assert first[sa]["title"] == "B title"                    # earliest report of either study


def test_unmerge_refuses_chains_until_unwound(db):
    sa, sb, a, b = two_studies(db)
    sc = store.create_study(db, "B", F(["1%"]), None)
    store.merge_studies(db, sb, sa)           # A -> B
    store.merge_studies(db, sc, sb)           # B -> C
    before = (study_of(db, a), study_of(db, b), store.get_study(db, sa)["merged_into"],
              store.get_study(db, sb)["merged_into"])
    with pytest.raises(ConfigError, match=f"study {sa} was merged into study {sb}; unmerge study {sa} first"):
        store.unmerge_study(db, sb)
    assert before == (study_of(db, a), study_of(db, b), store.get_study(db, sa)["merged_into"],
                      store.get_study(db, sb)["merged_into"])
    store.unmerge_study(db, sa)
    assert study_of(db, a) == sa and store.get_study(db, sa)["merged_into"] is None
    store.unmerge_study(db, sb)
    assert study_of(db, b) == sb and store.get_study(db, sb)["merged_into"] is None


def test_api_studies_list_and_show(workspace):
    from evidence_echo import api
    from evidence_echo.core import config
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    sa, sb, a, b = two_studies(conn)
    store.merge_studies(conn, sa, sb)
    conn.close()
    assert [s["id"] for s in api.studies_list(cfg)] == [sa]
    rows = {s["id"]: s for s in api.studies_list(cfg, include_merged=True)}
    assert set(rows) == {sa, sb} and rows[sb]["coverage"] == 0 and rows[sb]["merged_into"] == sa
    assert rows[sa]["coverage"] == 2
    shown = api.studies_show(cfg, sa)
    assert shown["editions"] == ["2023", "2025"] and shown["resolves_to"] is None
    assert sorted(i["id"] for i in shown["items"]) == sorted([a, b])
    assert all(i["match_reason"] for i in shown["items"] if i["id"] == b)
    merged = api.studies_show(cfg, sb)
    assert merged["items"] == [] and merged["resolves_to"] == sa
    with pytest.raises(ConfigError):
        api.studies_show(cfg, 999)


def test_studies_show_includes_grading_fields(workspace):
    from evidence_echo import api
    from evidence_echo.core import config
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    sa, _, _, _ = two_studies(conn)
    conn.close()
    shown = api.studies_show(cfg, sa)
    for key in ("metric", "baseline", "conducted_by", "relation", "conflict_of_interest"):
        assert key in shown


def test_unmerge_takes_back_the_primary_url_copied_onto_keep(db):
    sa, sb, a, b = two_studies(db)           # sa owns the primary url, sb has none
    store.merge_studies(db, sb, sa)           # keep=sb copies sa's primary url
    assert store.get_study(db, sb)["primary_canonical"]
    store.unmerge_study(db, sa)
    assert store.get_study(db, sb)["primary_url"] is None
    assert store.get_study(db, sb)["primary_canonical"] is None
    assert store.get_study(db, sa)["primary_canonical"] == "https://survey.example/2025"
    assert "same primary url" not in [p["rule"] for p in store.duplicate_pairs(db)]
    c = judged(db, "https://e.com/c", F(["7%"]), primary="https://survey.example/2025")
    assert match.fixed_pass(db) == []
    assert study_of(db, c) == sa


def test_unmerge_keeps_a_primary_url_the_keep_study_owns(db):
    sa, sb, a, b = two_studies(db)
    store.merge_studies(db, sa, sb)
    store.unmerge_study(db, sb)
    assert store.get_study(db, sa)["primary_canonical"] == "https://survey.example/2025"
