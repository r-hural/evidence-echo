import json

from evidence_echo.core import store
from evidence_echo.stages import match
from tests.conftest import FakeBackend, make_item


def judged(db, url, facts=None, tier="B", primary=None):
    store.insert_items(db, [make_item(url=url)])
    item_id = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    db.execute("UPDATE items SET state='judged', verdict_pass=1, evidence_tier=?, facts=?, "
               "primary_url=?, study_id=NULL WHERE id=?",
               (tier, json.dumps(facts or {}), primary, item_id))
    db.commit()
    return item_id


def F(numbers=(), series=None, editions=None, tested="AI tool use"):
    return {"what_was_tested": tested, "findings": "f", "research_timeframe": "t",
            "series": series, "editions": editions, "match_keys": {"key_numbers": list(numbers)}}


def study_of(db, item_id):
    return store.get_item(db, item_id)["study_id"]


def payload(*results):
    return json.dumps({"results": list(results)})


def test_no_candidates_new_study(db, topic):
    a = judged(db, "https://e.com/a", F(["44%"], series="SO Survey 2025", editions=["2025"]))
    assert match.fixed_pass(db) == []
    s = store.get_study(db, study_of(db, a))
    assert store.get_item(db, a)["state"] == "matched"
    assert store.get_item(db, a)["match_reason"] == "no candidates"
    assert store.study_lists(s) == (["2025"], ["44%"]) and s["series_id"]


def test_same_primary_url_joins_stored_study(db):
    sid = store.create_study(db, "A", F(["1%"]), "https://orig.example/r")
    a = judged(db, "https://e.com/a", F(), primary="http://orig.example/r?utm_source=x")
    assert match.fixed_pass(db) == []
    assert study_of(db, a) == sid and store.get_item(db, a)["match_reason"] == "same primary url"


def test_same_primary_url_between_new_items(db):
    a = judged(db, "https://e.com/a", F(), primary="https://orig.example/r")
    b = judged(db, "https://e.com/b", F(), primary="https://orig.example/r")
    match.fixed_pass(db)
    assert study_of(db, a) == study_of(db, b) is not None


def test_candidate_waits_and_items_without_match_keys_never_crash(db):
    a = judged(db, "https://e.com/a", F(["44%", "79%"]))
    b = judged(db, "https://e.com/b", F(["44%"]))
    c = judged(db, "https://e.com/c", {"what_was_tested": "x", "findings": "y",
                                       "research_timeframe": "z"})       # no match_keys/series/editions
    plan = match.fixed_pass(db)
    assert [e["item"]["id"] for e in plan] == [b]
    assert [c_["cid"] for c_ in plan[0]["candidates"]] == [f"s{study_of(db, a)}"]
    assert study_of(db, c) is not None                                    # no candidates: new study


def test_merged_studies_are_never_candidates(db):
    old = store.create_study(db, "B", F(["44%"]), None)
    new = store.create_study(db, "B", F(["5%"]), None)
    db.execute("UPDATE studies SET merged_into=? WHERE id=?", (new, old))
    db.commit()
    a = judged(db, "https://e.com/a", F(["44%"]))
    assert match.fixed_pass(db) == [] and study_of(db, a) not in (old, new)


def test_emit_lists_only_waiting_items_with_candidates(db, topic):
    a = judged(db, "https://e.com/a", F(["44%", "79%"], series="SO Survey"))
    b = judged(db, "https://e.com/b", F(["44%", "79%"], series="SO Survey"))
    out = json.loads(match.emit(db, topic))
    assert out["stage"] == "match" and "when unsure: null" in out["instructions"]
    assert [i["id"] for i in out["items"]] == [f"i{b}"]
    cand = out["items"][0]["candidates"][0]
    assert cand["id"] == f"s{study_of(db, a)}" and cand["series"] == "SO Survey"


def test_apply_after_emit_in_a_new_connection(db, topic, tmp_path):
    a = judged(db, "https://e.com/a", F(["44%"]))
    b = judged(db, "https://e.com/b", F(["44%"]))
    match.emit(db, topic)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    other = store.connect(path)
    out = match.apply(other, payload({"id": f"i{b}", "same_as": f"s{study_of(other, a)}", "reason": "same survey"}))
    assert out == {"applied": 1, "rejected": []}
    assert study_of(other, b) == study_of(other, a)
    assert db.execute("SELECT COUNT(*) FROM studies").fetchone()[0] == 1   # emit's study not re-created
    other.close()


def test_apply_rejects_candidate_of_another_item(db, topic):
    a = judged(db, "https://e.com/a", F(["44%"]))
    b = judged(db, "https://e.com/b", F(["44%"]))
    c = judged(db, "https://e.com/c", F(["7%"]))
    d = judged(db, "https://e.com/d", F(["7%"]))
    match.emit(db, topic)
    out = match.apply(db, payload({"id": f"i{b}", "same_as": f"s{study_of(db, c)}", "reason": "x"},
                                  {"id": f"i{d}", "same_as": "s999", "reason": "x"}))
    assert out["applied"] == 0 and len(out["rejected"]) == 2
    assert store.get_item(db, b)["state"] == "judged" and store.get_item(db, d)["state"] == "judged"


def test_apply_null_and_item_reference(db, topic):
    a = judged(db, "https://e.com/a", F(["44%"]))
    b = judged(db, "https://e.com/b", F(["44%"]))
    c = judged(db, "https://e.com/c", F(["44%"]))
    plan = match.fixed_pass(db)
    assert [c_["cid"] for c_ in plan[1]["candidates"]] == [f"i{b}", f"s{study_of(db, a)}"]
    out = match.apply(db, payload({"id": f"i{b}", "same_as": None, "reason": "different sample"},
                                  {"id": f"i{c}", "same_as": f"i{b}", "reason": "same as b"}))
    assert out["applied"] == 2
    assert study_of(db, c) == study_of(db, b) != study_of(db, a)


def test_item_reference_rejected_when_target_rejected(db, topic):
    a = judged(db, "https://e.com/a", F(["44%"]))
    b = judged(db, "https://e.com/b", F(["44%"]))
    c = judged(db, "https://e.com/c", F(["44%"]))
    match.fixed_pass(db)
    out = match.apply(db, payload({"id": f"i{b}", "same_as": "s999", "reason": "x"},
                                  {"id": f"i{c}", "same_as": f"i{b}", "reason": "x"}))
    assert out["applied"] == 0 and {r["id"] for r in out["rejected"]} == {f"i{b}", f"i{c}"}


def test_follower_of_waiting_item(db, topic):
    a = judged(db, "https://e.com/a", F(["44%"]))
    b = judged(db, "https://e.com/b", F(["44%"]), primary="https://orig.example/r")
    c = judged(db, "https://e.com/c", F(), primary="https://orig.example/r")
    out = json.loads(match.emit(db, topic))
    assert [i["id"] for i in out["items"]] == [f"i{b}"]                    # c is never emitted
    match.apply(db, payload({"id": f"i{b}", "same_as": f"s{study_of(db, a)}", "reason": "x"}))
    assert study_of(db, c) == study_of(db, a)
    assert store.get_item(db, c)["match_reason"] == "same primary url"


def test_run_api(db, topic):
    a = judged(db, "https://e.com/a", F(["44%"]))
    b = judged(db, "https://e.com/b", F(["44%"]))
    match.fixed_pass(db)                          # a gets its study here; b waits
    backend = FakeBackend([json.dumps({"same_as": f"s{study_of(db, a)}", "reason": "same"})])
    out = match.run_api(db, backend, topic)
    assert out == {"processed": 1, "rejected": []}
    assert study_of(db, b) == study_of(db, a)
    assert "when unsure: null" in backend.prompts[0] and f"s{study_of(db, a)}" in backend.prompts[0]


def test_run_api_rejects_foreign_candidate(db, topic):
    judged(db, "https://e.com/a", F(["44%"]))
    b = judged(db, "https://e.com/b", F(["44%"]))
    out = match.run_api(db, FakeBackend([json.dumps({"same_as": "s999", "reason": "x"})]), topic)
    assert out["processed"] == 0 and out["rejected"][0]["id"] == f"i{b}"
    assert store.get_item(db, b)["state"] == "judged"


def test_series_only_candidate_waits(db):
    a = judged(db, "https://e.com/a", F(["44%"], series="SO Survey 2025"))
    b = judged(db, "https://e.com/b", F(["9%"], series="SO Survey 2024"))
    plan = match.fixed_pass(db)
    assert [e["item"]["id"] for e in plan] == [b]
    assert [c["cid"] for c in plan[0]["candidates"]] == [f"s{study_of(db, a)}"]


def test_apply_rejects_unknown_result_id(db, topic):
    a = judged(db, "https://e.com/a", F(["44%"]))
    b = judged(db, "https://e.com/b", F(["44%"]))
    out = match.apply(db, payload({"id": "i9999", "same_as": None, "reason": "x"}))
    assert out["applied"] == 0 and [r["id"] for r in out["rejected"]] == ["i9999"]
    assert store.get_item(db, b)["state"] == "judged" and study_of(db, b) is None


def test_url_join_grows_pool_numbers(db):
    sid = store.create_study(db, "A", F(), "https://orig.example/r")
    one = judged(db, "https://e.com/1", F(["44%"]), primary="https://orig.example/r")
    two = judged(db, "https://e.com/2", F(["44%"]))
    plan = match.fixed_pass(db)
    assert study_of(db, one) == sid
    assert [e["item"]["id"] for e in plan] == [two]
    assert [c["cid"] for c in plan[0]["candidates"]] == [f"s{sid}"]


def test_same_primary_url_with_disjoint_editions_is_a_candidate_not_a_join(db):
    sid = store.create_study(db, "A", F(["1%"], series="State of X", editions=["2025"]), "https://v.example/state")
    new = judged(db, "https://e.com/n", F(["9%"], series="State of X", editions=["2026"]),
                 primary="https://v.example/state")
    plan = match.fixed_pass(db)
    assert [e["item"]["id"] for e in plan] == [new]
    assert [c["cid"] for c in plan[0]["candidates"]] == [f"s{sid}"]
    assert study_of(db, new) is None


def test_same_primary_url_with_overlapping_or_missing_editions_still_joins(db):
    sid = store.create_study(db, "A", F(["1%"], editions=["2025"]), "https://v.example/state")
    a = judged(db, "https://e.com/a", F(editions=["2025"]), primary="https://v.example/state")
    b = judged(db, "https://e.com/b", F(), primary="https://v.example/state")
    assert match.fixed_pass(db) == []
    assert study_of(db, a) == study_of(db, b) == sid


def test_url_candidate_is_forced_even_without_shared_series_or_numbers(db):
    sid = store.create_study(db, "A", F(["1%"], editions=["2025"]), "https://v.example/state")
    new = judged(db, "https://e.com/n", F(["9%"], editions=["2026"]), primary="https://v.example/state")
    plan = match.fixed_pass(db)
    assert [c["cid"] for c in plan[0]["candidates"]] == [f"s{sid}"]


def test_disjoint_editions_within_a_run(db):
    a = judged(db, "https://e.com/a", F(["9%"], editions=["2025"]), primary="https://v.example/state")
    b = judged(db, "https://e.com/b", F(["8%"], editions=["2026"]), primary="https://v.example/state")
    plan = match.fixed_pass(db)                       # a got a study; b must ask about it
    assert [e["item"]["id"] for e in plan] == [b]
    assert [c["cid"] for c in plan[0]["candidates"]] == [f"s{study_of(db, a)}"]


def test_disjoint_editions_against_a_waiting_item(db):
    store.create_study(db, "A", F(["44%", "79%"]), None)
    a = judged(db, "https://e.com/a", F(["44%"], editions=["2025"]), primary="https://v.example/state")
    b = judged(db, "https://e.com/b", F(["8%"], editions=["2026"]), primary="https://v.example/state")
    c = judged(db, "https://e.com/c", F(["7%"], editions=["2025"]), primary="https://v.example/state")
    plan = {e["item"]["id"]: e for e in match.fixed_pass(db)}
    assert plan[b]["follows"] is None and f"i{a}" in [x["cid"] for x in plan[b]["candidates"]]
    assert plan[c]["follows"] == a                    # same edition as the waiting item: still follows


def test_same_as_may_name_any_qualifying_candidate_not_only_the_top_five(db):
    sids = [store.create_study(db, "B", F(series="State of X", editions=[str(2010 + n)]), None)
            for n in range(7)]
    x = judged(db, "https://e.com/x", F(series="State of X", editions=["2030"]))
    plan = match.fixed_pass(db)
    assert len(plan[0]["candidates"]) == 5 and f"s{sids[0]}" not in [c["cid"] for c in plan[0]["candidates"]]
    out = match.apply(db, payload({"id": f"i{x}", "same_as": f"s{sids[0]}", "reason": "r"}))
    assert out["rejected"] == [] and study_of(db, x) == sids[0]


def test_same_as_still_rejects_a_study_that_does_not_qualify(db):
    store.create_study(db, "B", F(["44%"]), None)
    other = store.create_study(db, "B", F(["5%"]), None)
    x = judged(db, "https://e.com/x", F(["44%"]))
    out = match.apply(db, payload({"id": f"i{x}", "same_as": f"s{other}", "reason": "r"}))
    assert out["rejected"] and study_of(db, x) is None


P = "https://v.example/state"


def _asked(plan):
    return [(e["item"]["id"], [c["cid"] for c in e["candidates"]]) for e in plan
            if e["follows"] is None and e.get("joins") is None]


def test_newer_join_does_not_change_an_older_items_edition_clash_between_emit_and_apply(db):
    s = store.create_study(db, "A", F(["1%"], editions=["2025"]), P)
    o = judged(db, "https://e.com/o", F(["9%"], editions=["2026"]), primary=P)
    j = judged(db, "https://e.com/j", F(["8%"], editions=["2025", "2026"]), primary=P)
    emitted = _asked(match.fixed_pass(db))                                 # emit
    assert [i for i, _ in emitted] == [o, j]                               # j fits both s and o
    assert _asked(match.fixed_pass(db)) == emitted                         # apply sees the same plan
    out = match.apply(db, payload({"id": f"i{o}", "same_as": None, "reason": "other edition"}))
    assert out["rejected"] == []
    assert study_of(db, o) not in (None, s) and store.get_item(db, o)["match_reason"] == "other edition"
    assert study_of(db, j) is None                                         # not answered: asked again


def test_newer_join_does_not_change_an_older_items_candidates_between_emit_and_apply(db):
    s = store.create_study(db, "A", F(["1%"]), P)
    t = store.create_study(db, "A", F(["5%"]), None)
    x = judged(db, "https://e.com/x", F(["5%", "7%"]))
    j = judged(db, "https://e.com/j", F(["7%"]), primary=P)
    emitted = _asked(match.fixed_pass(db))                                 # emit
    assert emitted == [(x, [f"s{t}"])]
    assert study_of(db, j) is None                                         # its join waits for apply
    assert _asked(match.fixed_pass(db)) == emitted
    out = match.apply(db, payload({"id": f"i{x}", "same_as": f"s{t}", "reason": "same"}))
    assert out["rejected"] == [] and study_of(db, x) == t
    assert study_of(db, j) == s and store.get_item(db, j)["match_reason"] == "same primary url"


def test_same_url_on_two_edition_studies_joins_the_one_with_the_edition(db):
    s25 = store.create_study(db, "A", F(["1%"], editions=["2025"]), P)
    n = judged(db, "https://e.com/n", F(["9%"], editions=["2026"]), primary=P)
    match.apply(db, payload({"id": f"i{n}", "same_as": None, "reason": "other edition"}))
    s26 = study_of(db, n)
    assert s26 not in (None, s25) and store.get_study(db, s26)["primary_canonical"] == P
    m = judged(db, "https://e.com/m", F(["3%"], editions=["2025"]), primary=P)
    assert match.fixed_pass(db) == []
    assert study_of(db, m) == s25 and store.get_item(db, m)["match_reason"] == "same primary url"


def test_same_url_fitting_several_studies_forces_them_all(db):
    s25 = store.create_study(db, "A", F(["1%"], editions=["2025"]), P)
    s26 = store.create_study(db, "A", F(["2%"], editions=["2026"]), P)
    x = judged(db, "https://e.com/x", F(["9%"]), primary=P)                # no editions: fits both
    plan = match.fixed_pass(db)
    assert study_of(db, x) is None and [e["item"]["id"] for e in plan] == [x]
    assert {c["cid"] for c in plan[0]["candidates"][:2]} == {f"s{s25}", f"s{s26}"}
    assert {f"s{s25}", f"s{s26}"} <= plan[0]["allowed"]


def test_forced_url_target_takes_the_first_slot_even_when_outranked(db):
    old = store.create_study(db, "A", F(series="State of X", editions=["2025"]), P)
    for n in range(6):
        store.create_study(db, "B", F(series="State of X", editions=[str(2010 + n)]), None)
    x = judged(db, "https://e.com/x", F(series="State of X", editions=["2026"]), primary=P)
    plan = match.fixed_pass(db)
    cids = [c["cid"] for c in plan[0]["candidates"]]
    assert len(cids) == 5 and cids[0] == f"s{old}"


def test_item_clashing_with_a_stored_study_still_follows_a_waiting_item_of_its_edition(db):
    s = store.create_study(db, "A", F(["1%"], editions=["2025"]), P)
    a = judged(db, "https://e.com/a", F(["9%"], editions=["2026"]), primary=P)
    b = judged(db, "https://e.com/b", F(["8%"], editions=["2026"]), primary=P)
    plan = {e["item"]["id"]: e for e in match.fixed_pass(db)}
    assert plan[a]["follows"] is None and [c["cid"] for c in plan[a]["candidates"]] == [f"s{s}"]
    assert plan[b]["follows"] == a
    match.apply(db, payload({"id": f"i{a}", "same_as": None, "reason": "other edition"}))
    assert study_of(db, b) == study_of(db, a) != s
