"""Duplicate check: two blind answers on possible-duplicate pairs a weak rule found; the review skips the
pairs both call different, and the owner's "different studies" is kept."""
import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, contracts, store
from evidence_echo.core.settings import ConfigError
from evidence_echo.stages import dupcheck, match
from tests.conftest import FakeBackend
from tests.test_match import F, judged, study_of

TEXT_A = "Ahrefs surveyed 301 marketers: 69% publish more AI content and 85% plan to keep doing so."
TEXT_B = "OtterlyAI found that publishers with AI licensing deals earn 69% more citations, 85% of them on news."
QUOTE_A = "Ahrefs surveyed 301 marketers"
QUOTE_B = "OtterlyAI found that publishers with AI licensing deals"


def number_pair(db, numbers=("69%", "85%"), primary_b=None):
    """Two studies that share only numbers; each item carries its own text. Returns (study a, study b)."""
    a = judged(db, "https://e.com/a", F(numbers, tested="Marketer survey"))
    b = judged(db, "https://e.com/b", F(numbers, tested="AI licensing citations"), primary=primary_b)
    match.fixed_pass(db)
    if store.get_item(db, b)["study_id"] is None:   # b waits: it shares numbers with a
        match.apply(db, json.dumps({"results": [{"id": f"i{b}", "same_as": None, "reason": "unsure"}]}))
    for item, text in ((a, TEXT_A), (b, TEXT_B)):
        db.execute("UPDATE items SET full_text=? WHERE id=?", (text, item))
    db.commit()
    return study_of(db, a), study_of(db, b)


def answer(pid, verdict="different", quote_a=QUOTE_A, quote_b=QUOTE_B, note="A survey versus a citation study."):
    return {"id": pid, "verdict": verdict, "quote_a": quote_a, "quote_b": quote_b, "note": note}


def results(*answers):
    return json.dumps({"results": list(answers)})


def test_emit_lists_weak_pairs_with_both_texts(db, topic):
    sa, sb = number_pair(db)
    data = json.loads(dupcheck.emit(db, topic))
    assert data["stage"] == "dupcheck" and data["choices"] == {"verdict": ["same", "different", "unsure"]}
    [item] = data["items"]
    assert item["id"] == f"p{sa}-{sb}" and item["flagged_by"] == "shared key numbers"
    assert item["shared"] == "69%, 85%"
    assert item["a"]["text"] == TEXT_A and item["b"]["text"] == TEXT_B
    assert item["a"]["name"] and "verdict" in data["instructions"]


def test_strong_rule_pairs_are_never_checked(db, topic):
    sa, sb = number_pair(db, primary_b="https://orig.example/x")
    db.execute("UPDATE studies SET primary_url=?, primary_canonical=? WHERE id=?",
               ("https://orig.example/x", store.canonical_url("https://orig.example/x"), sa))
    db.commit()
    assert store.duplicate_pairs(db)[0]["rule"] == "same primary url"
    assert dupcheck.pending_pairs(db, None) == []


def test_both_different_hides_the_pair(db, topic):
    sa, sb = number_pair(db)
    pid = f"p{sa}-{sb}"
    out = dupcheck.apply(db, results(answer(pid)), results(answer(pid, note="Other words.")))
    assert out == {"applied": 1, "same": 0, "different": 1, "undecided": 0, "rejected": []}
    row = store.pair_decisions(db)[(sa, sb)]
    assert row["check_verdict"] == "different" and row["check_note"] == "A survey versus a citation study."
    assert dupcheck.pending_pairs(db, None) == []   # never asked again under this prompt


@pytest.mark.parametrize("second, why", [
    (answer("{pid}", verdict="same"), "the answers differ"),
    (answer("{pid}", verdict="unsure"), "one is unsure"),
    (answer("{pid}", quote_a="a quote that is not in the text"), "a quote is not found"),
])
def test_undecided_pairs_stay_for_the_owner(db, topic, second, why):
    sa, sb = number_pair(db)
    pid = f"p{sa}-{sb}"
    second = {k: (v.format(pid=pid) if k == "id" else v) for k, v in second.items()}
    out = dupcheck.apply(db, results(answer(pid)), results(second))
    assert out["undecided"] == 1, why
    assert store.pair_decisions(db)[(sa, sb)]["check_verdict"] == "undecided"


def test_one_sided_and_malformed_answers(db, topic):
    sa, sb = number_pair(db)
    pid = f"p{sa}-{sb}"
    out = dupcheck.apply(db, results(answer(pid)), results())
    assert out["applied"] == 0 and out["rejected"] == [{"id": pid, "file": "b", "errors": [dupcheck.ONE_SIDED]}]
    bad = dupcheck.apply(db, results(answer(pid, verdict="maybe")), results(answer(pid)))
    assert bad["applied"] == 0 and bad["rejected"][0]["file"] == "a"
    assert store.pair_decisions(db) == {}
    assert contracts.validate_result("dupcheck", answer("p1-2")) == []


def test_run_api_asks_twice_and_stores(db, topic):
    sa, sb = number_pair(db)
    reply = json.dumps({k: v for k, v in answer("x").items() if k != "id"})
    backend = FakeBackend([reply, reply])
    out = dupcheck.run_api(db, backend, topic)
    assert out == {"processed": 1, "same": 0, "different": 1, "undecided": 0, "rejected": []}
    assert len(backend.prompts) == 2 and TEXT_A in backend.prompts[0]
    assert store.pair_decisions(db)[(sa, sb)]["check_version"] == dupcheck.PROMPT_VERSION


def test_run_api_failure_stores_nothing(db, topic):
    number_pair(db)
    out = dupcheck.run_api(db, FakeBackend(["not json", "not json"]), topic)
    assert out["processed"] == 0 and len(out["rejected"]) == 1 and store.pair_decisions(db) == {}


def _workspace_pair(workspace):
    cfg = config.load("seo")
    with store.connect(cfg.db_path) as conn:
        sa, sb = number_pair(conn)
    return cfg, sa, sb


def test_review_skips_checked_and_owner_pairs(workspace, capsys):
    cfg, sa, sb = _workspace_pair(workspace)
    first = api.studies_duplicates(cfg)["pairs"]
    assert len(first) == 1 and first[0]["check"] is None
    pid = f"p{sa}-{sb}"
    files = []
    for n, verdict in enumerate(("same", "same")):
        path = workspace / f"answer-{n}.json"
        path.write_text(results(answer(pid, verdict=verdict, note="One survey.")), encoding="utf-8")
        files.append(str(path))
    assert cli.main(["--topic", "seo", "apply", "--stage", "dupcheck", *files]) == 0
    assert json.loads(capsys.readouterr().out)["same"] == 1
    [pair] = api.studies_duplicates(cfg)["pairs"]
    assert pair["check"] == {"verdict": "same", "note": "One survey."}   # shown, with the verdict
    with store.connect(cfg.db_path) as conn:
        store.save_pair_check(conn, sa, sb, "different", "Two studies.", dupcheck.PROMPT_VERSION)
    assert api.studies_duplicates(cfg) == {"pairs": []}
    assert api.studies_duplicates(cfg, count_only=True) == {"count": 0}
    [hidden] = api.studies_duplicates(cfg, include_hidden=True)["pairs"]
    assert hidden["hidden"] == "check: different studies"


def test_owner_distinct_and_undo(workspace, capsys):
    cfg, sa, sb = _workspace_pair(workspace)
    assert cli.main(["--topic", "seo", "studies", "distinct", str(sb), str(sa)]) == 0
    assert json.loads(capsys.readouterr().out) == {"pair": [sa, sb], "distinct": True}
    assert api.studies_duplicates(cfg, count_only=True) == {"count": 0}
    with store.connect(cfg.db_path) as conn:
        assert dupcheck.pending_pairs(conn, None) == []   # the owner decided: no check needed
    assert api.studies_duplicates(cfg, include_hidden=True)["pairs"][0]["hidden"] == "owner: different studies"
    api.studies_distinct(cfg, sa, sb, undo=True)
    assert api.studies_duplicates(cfg, count_only=True) == {"count": 1}
    with pytest.raises(ConfigError):
        api.studies_distinct(cfg, sa, 9999)


def test_reset_checks_again_and_keeps_owner_verdicts(workspace):
    cfg, sa, sb = _workspace_pair(workspace)
    with store.connect(cfg.db_path) as conn:
        store.save_pair_check(conn, sa, sb, "different", "x", dupcheck.PROMPT_VERSION)
        store.set_pair_owner(conn, sb, sb + 100, "different")   # another pair
    assert api.reset(cfg, "dupcheck") == {"reset": "dupcheck", "pairs": 1}
    with store.connect(cfg.db_path) as conn:
        assert [p["id"] for p in dupcheck.pending_pairs(conn, None)] == [f"p{sa}-{sb}"]
        assert store.pair_decisions(conn)[(sb, sb + 100)]["owner_verdict"] == "different"


def test_a_new_prompt_version_checks_again(db, topic, monkeypatch):
    sa, sb = number_pair(db)
    store.save_pair_check(db, sa, sb, "different", "x", dupcheck.PROMPT_VERSION)
    monkeypatch.setattr(dupcheck, "PROMPT_VERSION", "dupcheck-next")
    assert [p["id"] for p in dupcheck.pending_pairs(db, None)] == [f"p{sa}-{sb}"]
