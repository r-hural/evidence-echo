"""SP3b-P: the grading view's primary field and its line in the Markdown, HTML and Obsidian output."""
import json
from datetime import datetime, timezone

import pytest

from evidence_echo.core import interfaces
from evidence_echo.render import html as html_render
from evidence_echo.render import markdown, model, obsidian
from evidence_echo.stages import primary, report
from tests import html_fixtures as hf
from tests import obsidian_fixtures as of
from tests.primary_fixtures import ORIGINAL_URL, fake_ctx, original_html, report_item
from tests.test_primary_compare import answer


def check_row(item_id, status="agreed", verdict="overstated", missing=(), note="n", numbers="ok"):
    return {"item_id": item_id, "status": status, "verdict": verdict, "note": note,
            "numbers": numbers, "missing": json.dumps(list(missing))}


# --- the view ---

def test_no_check_no_primary():
    assert model.primary_view([{"id": 1}], {}) is None


def test_the_most_telling_check_is_shown():
    group = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    checks = {1: check_row(1, verdict="faithful"),
              2: check_row(2, status="unreachable", verdict=None, note=None, numbers=None),
              3: check_row(3, status="undecided", verdict=None, note=None, missing=["44%"],
                           numbers="partial"),
              4: check_row(4, verdict="direction_flipped", note="It says clicks rose; they fell.")}
    assert model.primary_view(group, checks) == {
        "numbers": "ok", "missing": [], "verdict": "direction_flipped",
        "note": "It says clicks rose; they fell.", "status": "agreed"}
    del checks[4]
    assert model.primary_view(group, checks)["missing"] == ["44%"]
    del checks[3]
    assert model.primary_view(group, checks)["status"] == "unreachable"
    del checks[2]
    assert model.primary_view(group, checks)["verdict"] == "faithful"


def test_an_undecided_check_shows_no_verdict():
    view = model.primary_view([{"id": 1}], {1: check_row(1, status="undecided")})
    assert view["verdict"] is None and view["note"] is None and view["status"] == "undecided"


def test_grading_view_without_checks_has_no_primary():
    study = {"created_by_item": None, "metric": None, "baseline": None, "conducted_by": None,
             "relation": None, "conflict_of_interest": None}
    view = model.grading_view(study, [{"id": 1}])
    assert view["primary"] is None and list(view)[-1] == "primary"


# --- the line ---

@pytest.mark.parametrize("field,text", [
    (None, None),
    ({"status": "unreachable", "verdict": None, "note": None, "missing": [], "numbers": None},
     "Original not reachable"),
    ({"status": "agreed", "verdict": "overstated", "note": "Claims causation.", "missing": [],
      "numbers": "ok"}, "Original: overstated — Claims causation."),
    ({"status": "agreed", "verdict": "direction_flipped", "note": "", "missing": [], "numbers": "ok"},
     "Original: direction flipped"),
    ({"status": "undecided", "verdict": None, "note": None, "missing": ["44%"], "numbers": "partial"},
     "Original: numbers not found — 44%"),
    ({"status": "agreed", "verdict": "cherry_picked", "note": "Leaves out the losers",
      "missing": ["44%", "12000"], "numbers": "none"},
     "Original: cherry picked — Leaves out the losers; numbers not found — 44%, 12000"),
    ({"status": "agreed", "verdict": "faithful", "note": "Matches.", "missing": [], "numbers": "ok"},
     None),
])
def test_primary_text(field, text):
    assert markdown.primary_text(field) == text


def test_a_note_cannot_break_the_line():
    p = {"status": "agreed", "verdict": "overstated", "note": "two\n\n## lines", "missing": [],
         "numbers": "ok"}
    assert markdown.primary_text(p) == "Original: overstated — two ## lines"


PRIMARY = {"status": "agreed", "verdict": "overstated", "missing": ["44%"], "numbers": "partial",
           "note": "<script>alert(1)</script> [[evil]] `x` #tag"}
LINE = markdown.primary_text(PRIMARY)


def test_markdown_entry_has_the_line_last_among_grading_lines():
    e = hf.entry(1, view=hf.view(metric="clicks", primary=PRIMARY))
    lines = markdown.report_markdown(hf.report(new=[e])).splitlines()
    assert lines.index(f"{markdown.plain(LINE)}  ") == lines.index("Measured: clicks  ") + 1


def test_html_card_escapes_the_line():
    e = hf.entry(1, view=hf.view(primary=PRIMARY))
    assert f"Original: {markdown.fact_pairs(e.view)[-1][1]}" == LINE
    card = html_render.card(e)
    assert "<script>alert(1)" not in card and '<b>Original:</b> <span class="ee-warn">' + html_render.esc(LINE.removeprefix("Original: ")) in card


def test_obsidian_note_neutralises_the_line():
    assert obsidian.grading_lines(of.view(primary=PRIMARY))[-1] == LINE + "  "
    notes = obsidian.render_notes(of.topic([of.study(1, view=of.view(primary=PRIMARY))]), "SEO")
    note = next(text for path, text in notes.items() if "/studies/" in path)
    assert "[[evil]]" not in note and "<script>" not in note
    assert "\\[\\[evil\\]\\]" in note


def test_views_without_a_primary_key_still_render():
    assert markdown.fact_pairs(hf.view()) == []
    assert obsidian.grading_lines(of.view()) == []
    assert markdown.grading_texts(hf.view()) == []


# --- end to end through the store ---

def _checked(db, verdict="overstated") -> int:
    iid = report_item(db)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html()}))
    a = answer(interfaces.opaque_id(iid), verdict=verdict, note="Presents a correlation as the cause.")
    primary.apply(db, json.dumps({"results": [a]}), json.dumps({"results": [a]}))
    return iid


def _render(db, tmp_path, topic) -> str:
    out = report.render(db, '{"results": []}', tmp_path / "reports", "English", topic)
    return open(out["path"], encoding="utf-8").read()


def test_report_shows_the_verdict_and_the_red_flag(db, tmp_path, topic):
    _checked(db)
    text = _render(db, tmp_path, topic)
    assert "Original: overstated — Presents a correlation as the cause.  " in text
    assert "Red flags: misrepresented primary" in text


def test_report_shows_an_unreachable_original(db, tmp_path, topic):
    report_item(db)
    for _ in range(3):
        primary.prepare(db, fake_ctx({ORIGINAL_URL: 404}))
    assert "Original: not reachable  " in _render(db, tmp_path, topic)


def test_a_faithful_original_adds_no_line(db, tmp_path, topic):
    _checked(db, verdict="faithful")
    text = _render(db, tmp_path, topic)
    assert "Original:" not in text and "Original not" not in text


def test_the_topic_model_carries_the_check(db, tmp_path, topic):
    _checked(db)
    _render(db, tmp_path, topic)
    [ts] = model.build_topic(db, topic, datetime.now(timezone.utc)).studies
    assert ts.entry.view["primary"]["verdict"] == "overstated"


# --- an original that could not be fetched ---

NOT_CHECKED = "Original not checked (could not be fetched)"


def _entry(db, tmp_path, topic):
    _render(db, tmp_path, topic)
    [ts] = model.build_topic(db, topic, datetime.now(timezone.utc)).studies
    return ts.entry


def test_a_failed_fetch_without_a_check_shows_not_checked(db, tmp_path, topic):
    report_item(db)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: 404}))  # one failed attempt: not unreachable yet
    e = _entry(db, tmp_path, topic)
    assert e.view["primary"]["status"] == "not_checked"
    assert markdown.grading_texts(e.view)[-1] == "Original: not checked (could not be fetched)"
    assert '<b>Original:</b> <span class="ee-warn">not checked (could not be fetched)' in html_render.card(e)
    assert obsidian.grading_lines(e.view)[-1] == "Original: not checked (could not be fetched)  "


def test_the_markdown_report_shows_not_checked(db, tmp_path, topic):
    report_item(db)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: 404}))
    assert "Original: not checked (could not be fetched)  " in _render(db, tmp_path, topic)


def test_a_fetched_original_without_a_check_shows_no_line(db, tmp_path, topic):
    report_item(db)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html()}))
    text = _render(db, tmp_path, topic)                  # one render: a second one has nothing new
    [ts] = model.build_topic(db, topic, datetime.now(timezone.utc)).studies
    assert ts.entry.view["primary"] is None
    assert "Original not" not in text


def test_an_unreachable_check_still_says_not_reachable(db, tmp_path, topic):
    report_item(db)
    for _ in range(3):
        primary.prepare(db, fake_ctx({ORIGINAL_URL: 404}))
    view = _entry(db, tmp_path, topic).view
    assert view["primary"]["status"] == "unreachable"
    assert markdown.grading_texts(view)[-1] == "Original: not reachable"


def test_plain_escapes_markup():
    assert markdown.plain("<b>a_b</b> [x](javascript:y) `c` *d* \\ & ok") == \
        "&lt;b>a\\_b&lt;/b> \\[x\\](javascript:y) \\`c\\` \\*d\\* \\\\ & ok"


def test_plain_leaves_fixed_texts_alone():
    for text in ("Original not reachable", "Original not checked (could not be fetched)",
                 "Original: overstated — Claims causation.; numbers not found — 44%, 12,000"):
        assert markdown.plain(text) == text


def test_markdown_report_escapes_the_primary_line():
    hostile = dict(PRIMARY, missing=["<img src=x onerror=alert(1)>"])
    e = hf.entry(1, view=hf.view(primary=hostile))
    text = markdown.report_markdown(hf.report(new=[e]))
    assert "<script>" not in text and "<img" not in text and "[[evil]]" not in text
    assert ("Original: overstated — &lt;script>alert(1)&lt;/script> \\[\\[evil\\]\\] "
            "\\`x\\` #tag; numbers not found — &lt;img src=x onerror=alert(1)>  ") in text


def test_html_and_obsidian_still_get_the_raw_line():
    assert f"Original: {markdown.fact_pairs(hf.view(primary=PRIMARY))[-1][1]}" == LINE
    assert obsidian.grading_lines(of.view(primary=PRIMARY))[-1] == LINE + "  "
