"""Findings-mode model and HTML pages (grouping modes part 2, task 7)."""
import re

import pytest

from evidence_echo.core import knowledge, store
from evidence_echo.render import html as html_render
from evidence_echo.render import model
from tests.test_knowledge_findings import fq, put, study, subs  # noqa: F401  (fixture)
from tests.test_report_golden import NOW, frozen  # noqa: F401  (fixture)


def build(db, topic, studies):
    store.record_report(db, "r.md", [(s, f"Title {s}", f"Summary {s}") for s in studies])
    t = model.build_topic(db, topic, NOW, "findings")
    return t, html_render.topic_page(t, "overview.html", "report.html")


def three_questions(db):
    """Study `sid` has three findings under questions in three subtopics; each question has a second study."""
    sid, (f1, f2, f3) = study(db, n=3, by="Main")
    a, b = fq(db, text="Qa?"), fq(db, "local", text="Qb?")
    c = fq(db, "backlinks", text="Qc?", kind="level")
    put(db, f1, a, "up")
    put(db, f2, b, "down")
    put(db, f3, c, None)
    others = [study(db, "title-tags-meta", by="O1")[0], study(db, "local", by="O2")[0],
              study(db, "backlinks", by="O3")[0]]
    for (o, (of,)), q_ in zip([(o, [f["id"] for f in store.study_findings(db, o)]) for o in others], (a, b, c)):
        put(db, of, q_, None if q_ == c else "up")
    return sid, others, (a, b, c)


def test_question_rows_show_finding_text_and_quote(subs, topic, frozen):
    sid, others, _ = three_questions(subs)
    _, out = build(subs, topic, [sid, *others])
    row = re.search(rf'<li><a class="ee-row" href="#s{sid}">(.*?)</a>(<details class="ee-quote">.*?</details>)</li>',
                    out, re.S)
    assert row and f"s{sid} finding 1" in row.group(1) and "<details" not in row.group(1)
    assert "<summary>Quote</summary><blockquote>“quote 1”</blockquote>" in row.group(2)
    assert "no checked number" not in row.group(1)


def test_unchecked_quote_row_is_marked(subs, topic, frozen):
    qid = fq(subs)
    sid, (f1,) = study(subs, by="A")
    put(subs, f1, qid, "up")
    subs.execute("UPDATE findings SET quote_checked=0 WHERE id=?", (f1,))
    subs.commit()
    _, out = build(subs, topic, [sid])
    assert f'<span class="ee-mark" aria-hidden="true">#</span>{html_render.UNCHECKED_QUOTE}' in out.split(
        'id="ee-evidence"')[1].split('id="ee-all"')[0]


def test_study_listed_under_every_question(subs, topic, frozen):
    sid, others, (a, b, c) = three_questions(subs)
    _, out = build(subs, topic, [sid, *others])
    for qid in (a, b, c):
        block = re.search(rf'data-question="q{qid}".*?</li>\s*(?=<li class="ee-question|</ul>)', out, re.S).group(0)
        assert f'href="#s{sid}"' in block


def test_waiting_to_be_split_line(subs, topic, frozen):
    qid = fq(subs)
    done, (f1,) = study(subs, by="A")
    put(subs, f1, qid, "up")
    waiting, _ = study(subs, by="B")          # findings backfilled, none grouped
    t, out = build(subs, topic, [done, waiting])
    assert '<details class="ee-waiting-split"><summary>Waiting to be split (1)</summary>' in out
    assert f'href="#s{waiting}"' in out.split('class="ee-waiting-split"')[1]
    assert " open" not in out.split('class="ee-waiting-split"')[1].split(">")[0]
    # nothing waits: no line
    subs.execute("DELETE FROM findings WHERE study_id=?", (waiting,))
    subs.execute("UPDATE studies SET findings_version='findings-1:failed' WHERE id=?", (waiting,))
    subs.commit()
    _, out = build(subs, topic, [done, waiting])
    assert '<details class="ee-waiting-split">' not in out


def test_card_lists_questions_with_finding_text_and_label(subs, topic, frozen):
    sid, others, (a, b, c) = three_questions(subs)
    t, out = build(subs, topic, [sid, *others])
    card = re.search(rf'<article class="card" id="s{sid}".*?</article>', out, re.S).group(0)
    assert "<b>Questions:</b>" in card
    items = re.findall(r"<li>(.*?)</li>", card.split("<b>Questions:</b>")[1], re.S)
    assert len(items) == 3
    assert "Qa?" in items[0] and "consistent" in items[0] and f"s{sid} finding 1" in items[0]
    assert "contested" in items[1] and f"s{sid} finding 2" in items[1]
    assert "Qc?" in items[2]
    assert "<b>Findings:</b>" not in card
    entry = [x.entry for x in t.studies if x.entry.study_id == sid][0]
    assert entry.subtopic["question"] is None and len(entry.subtopic["questions"]) == 3
    assert entry.findings_state == "done" and len(entry.findings) == 3


def test_card_marks_unchecked_and_waiting(subs, topic, frozen):
    qid = fq(subs)
    sid, (f1, f2) = study(subs, n=2, by="A")
    put(subs, f1, qid, "up")
    subs.execute("UPDATE findings SET quote_checked=0 WHERE id=?", (f1,))
    subs.commit()
    _, out = build(subs, topic, [sid])
    card = re.search(rf'<article class="card" id="s{sid}".*?</article>', out, re.S).group(0)
    assert "ee-mark" in card.split("<b>Questions:</b>")[1].split("</ul>")[0]
    assert "<b>Findings:</b> waiting to be split" in card


def test_findings_mode_header_counts(subs, topic, frozen):
    sid, others, _ = three_questions(subs)
    t, out = build(subs, topic, [sid, *others])
    assert t.finding_count == 6
    assert "4 studies · 6 findings · 3 questions · updated " in out


def test_findings_mode_subtopic_filter_uses_position_one_subtopic(subs, topic, frozen):
    sid, others, _ = three_questions(subs)
    t, _ = build(subs, topic, [sid, *others])
    rows = {r["id"]: r for r in html_render.filter_data(t)["studies"]}
    assert rows[sid]["subtopic"] == "title-tags-meta"
    assert rows[sid]["metrics"] == [] or isinstance(rows[sid]["metrics"], list)


def test_subtopic_study_count_not_double_counted(subs, topic, frozen):
    sid, (f1, f2) = study(subs, n=2, by="A")
    a, b = fq(subs, text="Qa?"), fq(subs, text="Qb?")
    put(subs, f1, a, "up")
    put(subs, f2, b, "up")
    t, out = build(subs, topic, [sid])
    sub = t.evidence[0]
    assert html_render._subtopic_counts(sub, False) == "2 questions · 1 study"


def test_questions_mode_model_has_no_findings_fields(subs, topic, frozen):
    store.record_report(subs, "r.md", [(study(subs, by="A")[0], "T", "S")])
    t = model.build_topic(subs, topic, NOW)
    assert t.mode == "questions" and all(x.entry.findings is None for x in t.studies)
    assert all("questions" not in (x.entry.subtopic or {}) for x in t.studies)


def test_findings_report_page_widens_key(subs, topic, frozen):
    qid = fq(subs)
    sid, (f1,) = study(subs, by="A")
    put(subs, f1, qid, "up")
    r = model.build_report(subs, topic, {}, NOW, "findings")
    out = html_render.report_page(r, "t.html", "o.html")
    assert html_render.RATINGS_KEY_FINDINGS.split("<span")[-1] in out
    assert r.mode == "findings"
    entry = r.new[0]
    assert entry.subtopic["questions"][0]["question"] == "Does X raise Y?"


@pytest.mark.parametrize("mode", ["questions"])
def test_quote_disclosure_matches_finding_row(mode):
    assert html_render.quote_disclosure("q", True) == '<details class="ee-quote"><summary>Quote</summary><blockquote>“q”</blockquote></details>'
