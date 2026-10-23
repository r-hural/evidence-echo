"""Study rows (oldest first) and change lines per question."""
from evidence_echo.core import knowledge, store
from evidence_echo.render import evidence_text as et
from tests.test_knowledge_questions import add, q, subtopiced  # noqa: F401  (fixture)

SERIES = "Seer Interactive CTR study"


def set_(db, sid, **cols):
    if "series" in cols:
        cols["series_id"] = store.get_or_create_series(db, cols.pop("series"))
    for k, v in cols.items():
        db.execute(f"UPDATE studies SET {k}=? WHERE id=?", (v, sid))
    db.commit()


def hl(value, label):
    return dict(headline_value=value, headline_label=label, headline_quote="q", headline_checked=1)


def seer(db, qid):
    s25 = add(db, question=qid, direction="down", by="Seer", tier="B")
    s24 = add(db, question=qid, direction="down", by="Seer", tier="B")
    ah = add(db, question=qid, direction="down", by="Ahrefs", tier="A")
    set_(db, s24, series=SERIES, data_date="2024-05", **hl("−15 %", "organic CTR"))
    set_(db, s25, series=SERIES, data_date="2025-03", **hl("−34 %", "Organic  CTR."))
    set_(db, ah, data_date="2025-07", **hl("−34.5 %", "position-1 CTR"))
    return s24, s25, ah


def group(db, key="title-tags-meta"):
    return next(e for e in knowledge.evidence(db) if e.key == key).questions[0]


def test_rows_newest_first_with_editions_counted_oldest_first(subtopiced):
    s24, s25, ah = seer(subtopiced, q(subtopiced))
    g = group(subtopiced)
    assert [r.study_id for r in g.rows] == [ah, s25, s24]
    assert [(r.date, r.reported, r.edition) for r in g.rows] == [
        ("2025-07", False, None), ("2025-03", False, 2), ("2024-05", False, 1)]
    assert (g.rows[2].headline, g.rows[2].series, g.rows[0].tier) == ("−15 %", SERIES, "A")
    assert {r.direction for r in g.rows} == {"down"}


def test_change_line_for_a_series_sharing_a_label(subtopiced):
    seer(subtopiced, q(subtopiced))
    assert group(subtopiced).changes == [knowledge.ChangeLine(SERIES, [("−15 %", "2024"), ("−34 %", "2025")])]


def test_no_change_line_across_labels_or_different_studies(subtopiced):
    s24, s25, _ = seer(subtopiced, q(subtopiced))
    set_(subtopiced, s25, headline_label="clicks")
    assert group(subtopiced).changes == []


def test_reported_fallback_and_unchecked_headline(subtopiced):
    sid = add(subtopiced)
    set_(subtopiced, sid, headline_value="−9 %", headline_label="ctr", headline_quote="q", headline_checked=0)
    [other] = knowledge.evidence(subtopiced)[0].others
    row = other.row
    assert (row.date, row.reported, row.headline, row.headline_label) == ("2026-08", True, None, None)
    assert row.direction is None


def test_ties_by_tier_then_id(subtopiced):
    qid = q(subtopiced)
    b = add(subtopiced, question=qid, by="X", tier="B")
    a = add(subtopiced, question=qid, by="Y", tier="A")
    b2 = add(subtopiced, question=qid, by="Z", tier="B")
    for sid in (a, b, b2):
        set_(subtopiced, sid, data_date="2025")
    assert [r.study_id for r in group(subtopiced).rows] == [a, b, b2]


def test_unsorted_has_rows_but_no_changes(subtopiced):
    s24, s25, _ = seer(subtopiced, q(subtopiced))
    set_(subtopiced, s24, subtopic_id=None); set_(subtopiced, s25, subtopic_id=None)
    unsorted = next(e for e in knowledge.evidence(subtopiced) if e.key == knowledge.UNSORTED)
    assert [o.row.study_id for o in unsorted.others] == [s25, s24] and unsorted.questions == []


def row(**over):
    base = dict(study_id=1, name="Seer 2025", date="2025-03", reported=False, headline="−34 %",
                headline_label="organic CTR", direction="down", tier="B", series=SERIES, edition=2)
    base.update(over)
    return knowledge.StudyRow(**base)


def test_text_helpers():
    assert et.row_line(row(), "Seer 2025") == (
        "2025-03 · Seer 2025 · −34 % organic CTR · B (edition 2 of series)")
    assert et.row_line(row(direction=None, edition=None), "X") == (
        "2025-03 · X · −34 % organic CTR · B")
    assert et.change_text(knowledge.ChangeLine(SERIES, [("−15 %", "2024"), ("−34 %", "2025")])) == (
        "Seer Interactive CTR study: −15 % (2024) → −34 % (2025)")
    assert et.row_line(row(reported=True, headline=None, edition=None, tier=None,
                           direction="no_change"), "X") == (
        "reported 2025-03 · X · no checked number · ?")
    assert et.date_text(row(date=None)) == "date unknown"
    assert not hasattr(et, "ARROWS") and not hasattr(et, "arrow")


def test_rows_newest_first_undated_last():
    studies = [{"id": i, "canonical_name": f"S{i}", "data_date": d, "question_direction": None,
                "direction": None, "evidence_tier": "B"}
               for i, d in ((1, "2025"), (2, "2026-08"), (3, None), (4, "2026-10"))]
    rows, _ = knowledge._rows(studies, {}, {}, False)
    assert [r.date for r in rows] == ["2026-10", "2026-08", "2025", None]


def test_editions_still_count_oldest_first(subtopiced):
    s24, s25, _ = seer(subtopiced, q(subtopiced))
    rows = {r.study_id: r for r in group(subtopiced).rows}
    assert (rows[s24].edition, rows[s25].edition) == (1, 2)
    ids = [r.study_id for r in group(subtopiced).rows]
    assert ids.index(s25) < ids.index(s24)          # the older edition is listed second


def test_reported_and_created_month_fallbacks_are_local(monkeypatch):
    from datetime import timedelta, timezone
    from evidence_echo.render import clock
    monkeypatch.setattr(clock, "ZONE", timezone(timedelta(hours=2)))
    late = "2026-10-31T23:30:00+00:00"                  # 1 November, local
    assert knowledge._row_date({"id": 1}, {}, {1: {"rendered_at": late}}) == ("2026-11", True)
    assert knowledge._row_date({"id": 1, "created_at": late}, {}, {}) == ("2026-11", True)
    assert knowledge._row_date({"id": 1}, {1: "2026-10-31"}, {}) == ("2026-10", True)  # article date
