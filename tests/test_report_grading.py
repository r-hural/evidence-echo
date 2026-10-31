import json

from evidence_echo.core import interfaces, store
from evidence_echo.stages import judge, match, report, verify
from tests.test_judge import VERDICT, SENTENCE, _seed_extracted


def _verified(db, skeptic="A", flags=(), url="https://example.com/p1", text=None, **over):
    iid = _seed_extracted(db, url=url, **({"text": text} if text else {}))
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid), **over)]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [{"id": interfaces.opaque_id(iid), "tier": skeptic,
                                              "red_flags": list(flags), "note": "skeptic note"}]}))
    return iid


def _render(db, tmp_path, topic):
    out = report.render(db, '{"results": []}', tmp_path / "reports", "English", topic)
    return open(out["path"], encoding="utf-8").read()


def test_label_shows_relation(db, tmp_path, topic):
    _verified(db)
    assert "### [A · agency] " in _render(db, tmp_path, topic)


def test_label_shows_cap(db, tmp_path, topic):
    _verified(db, skeptic="B", facts=dict(VERDICT["facts"], has_controls=False))
    assert "[B · agency · capped from A: no control group]" in _render(db, tmp_path, topic)


def test_label_shows_the_grade_that_lost(db, tmp_path, topic):
    _verified(db, skeptic="B")                          # judge A, skeptic B -> B
    assert "[B · agency · judge: A]" in _render(db, tmp_path, topic)


def test_label_when_the_judge_was_lower(db, tmp_path, topic):
    _verified(db, skeptic="B", tier="C")                # judge C, skeptic B -> C
    text = _render(db, tmp_path, topic)
    assert "[C · agency · skeptic: B]" in text


def test_grading_lines(db, tmp_path, topic):
    _verified(db, flags=["no_baseline", "confounded_window"],
              provenance={"conducted_by": "Acme", "relation": "vendor",
                          "conflict_of_interest": "Acme sells the tool"},
              quotes=[{"number": "4.2%", "quote": SENTENCE}])
    text = _render(db, tmp_path, topic)
    assert "Red flags: no baseline, confounded window" in text
    assert "Conflict of interest: Acme sells the tool" in text
    assert "Numbers not found in the text: 12000" in text
    assert "Measured: Clicks · organic clicks" in text and "Baseline: control group" in text
    assert "Graded from" not in text


def test_excerpt_line(db, tmp_path, topic):
    iid = _seed_extracted(db)
    store.set_item_fields(db, iid, text_source="excerpt")
    judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid))]}))
    match.fixed_pass(db)
    verify.apply(db, json.dumps({"results": [{"id": interfaces.opaque_id(iid), "tier": "A",
                                              "red_flags": [], "note": "n"}]}))
    assert "Graded from: the excerpt only" in _render(db, tmp_path, topic)


def test_dispute_reasons(db, tmp_path, topic):
    _verified(db, skeptic="C")
    other = dict(VERDICT["facts"], match_keys={"key_numbers": ["9%"]})   # no shared numbers:
    _verified(db, skeptic="A", url="https://example.com/p2", quotes=None,  # match must not
              text="Rankings fell 9% across forty sites in March.",        # offer it as a candidate
              facts=other, primary_url="https://other.example.com/x")
    text = _render(db, tmp_path, topic)
    assert "grades two tiers apart" in text
    assert "no quoted number found in the text" in text


def test_pre_sp2_study_shows_no_grading_lines(db, tmp_path, topic):
    iid = _verified(db)
    db.execute("""UPDATE items SET quotes=NULL, quote_check=NULL, capped_from=NULL, judged_from=NULL,
                  red_flags=NULL, dispute_reason=NULL, provenance=NULL, judge_tier=NULL WHERE id=?""", (iid,))
    db.execute("""UPDATE studies SET metric=NULL, metric_id=NULL, baseline=NULL, conducted_by=NULL, relation=NULL,
                  conflict_of_interest=NULL""")
    db.commit()
    text = _render(db, tmp_path, topic)
    assert "### [A] " in text
    for marker in ("Red flags", "Conflict of interest", "Numbers not found", "Measured:", "excerpt"):
        assert marker not in text


def test_emit_carries_grading_fields(db, topic):
    _verified(db, skeptic="B", flags=["cherry_pick"])
    entry = json.loads(report.emit(db, topic, "English"))["items"][0]
    assert entry["flags"] == ["cherry_pick"]
    assert entry["provenance"]["relation"] == "agency"
    assert entry["quote_check"] == "ok" and entry["judged_from"] == "full_text"
    assert entry["metric"] == "organic clicks" and entry["baseline"] == "control group"
    assert entry["capped_from"] is None
    assert entry["missing_numbers"] == [] and entry["gap_note"] == "judge: A"


def test_joined_item_grade_gap_does_not_label_the_study(db, tmp_path, topic):
    _verified(db)                                                 # creates the study, A/A
    _verified(db, skeptic="B", url="https://example.com/p2")      # same primary url: joins it
    text = _render(db, tmp_path, topic)
    assert "### [A · agency] " in text
    assert "judge:" not in text and "skeptic:" not in text


def test_baseline_only_line_keeps_case(db, tmp_path, topic):
    _verified(db, facts=dict(VERDICT["facts"], metric=None, baseline="Control Group"))
    assert "Baseline: Control Group  " in _render(db, tmp_path, topic)


def test_no_quote_dispute_label_omits_the_skeptic_grade(db, tmp_path, topic):
    _verified(db, skeptic="A", quotes=None)                       # equal grades, no quote
    text = _render(db, tmp_path, topic)
    assert "### [A · agency] " in text and "/ skeptic" not in text
    assert "Disputed: no quoted number found in the text" in text
