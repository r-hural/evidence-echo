"""Grade labels, cap reasons and field red flags on every page and file (grading rules, Task 8)."""
import json
from datetime import datetime, timezone

from evidence_echo.core import grading, store
from evidence_echo.render import html as html_render
from evidence_echo.render import markdown, model, obsidian
from evidence_echo.stages import report
from tests import html_fixtures as hf
from tests import obsidian_fixtures as of
from tests.test_regrade import _exercise, _seed

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


def _animal(db, topic, n=1, **facts):
    """A study of an animal experiment that both graders rated A (capped to C by the exercise topic)."""
    iid = _seed(db, topic, n, verified="A", study_type="animal", has_controls=True,
                sample_scale="n=40 rats", **facts)
    return store.get_item(db, iid)["study_id"]


def _view_of(db, topic, sid, rules=None):
    study = store.get_study(db, sid)
    group = store.study_items(db, sid, ("verified", "embedded", "reported"))
    return model.grading_view(study, group, rules=grading.rules_of(topic) if rules is None else rules)


def test_grading_view_labels_in_block_order(db):
    topic = _exercise()
    sid = _animal(db, topic, 1)
    db.execute("UPDATE studies SET study_type='observational', peer_review='preprint' WHERE id=?", (sid,))
    db.commit()
    assert _view_of(db, topic, sid)["grade_labels"] == ["observational", "preprint"]
    assert _view_of(db, topic, sid, rules=grading.generic())["grade_labels"] == []
    assert model.grading_view(store.get_study(db, sid), [])["grade_labels"] == []


def test_markdown_label_has_grade_labels_and_reason(db):
    topic = _exercise()
    sid = _animal(db, topic, 1)
    view = _view_of(db, topic, sid)
    assert view["capped_from"] == "A" and view["cap_reasons"] == ["animal study"]
    e = hf.entry(1, tier="C", view=view)
    assert markdown.label_extras(e)[:1] == ["animal"]
    assert "capped from A: animal study" in markdown.label_extras(e)
    line = " · ".join([markdown.tier_label(e)] + markdown.label_extras(e))
    assert "C · animal" in line and "capped from A: animal study" in line


def test_cap_note_without_reasons_is_as_before():
    e = hf.entry(1, view=hf.view(capped_from="A"))
    assert markdown.label_extras(e) == ["capped from A"]


def test_html_card_and_chart_show_grade_labels():
    e = hf.entry(1, tier="C", view=hf.view(grade_labels=["animal"], capped_from="A",
                                           cap_reasons=["animal study"]))
    card = html_render.card(e)
    assert 'class="ee-gl">animal<' in card
    assert card.index("tier") < card.index("ee-gl")
    assert "capped from A: animal study" in card
    page = html_render.report_page(hf.report(new=[e]), "t.html", "o.html")
    assert "Grade labels" in page
    plain = html_render.report_page(hf.report(new=[hf.entry(2)]), "t.html", "o.html")
    assert "Grade labels" not in plain


def test_obsidian_label_matches_markdown():
    e = hf.entry(1, tier="C", view=hf.view(grade_labels=["animal"], capped_from="A",
                                           cap_reasons=["animal study"]))
    assert obsidian._label(e) == "C · animal · capped from A: animal study"
    assert obsidian._label(e) == " · ".join([markdown.tier_label(e)] + markdown.label_extras(e))


def test_field_flags_in_filters_and_cards(db):
    topic = _exercise()
    sid = _animal(db, topic, 1)
    db.execute("UPDATE items SET red_flags=? WHERE study_id=?", (json.dumps(["animal_to_human"]), sid))
    db.commit()
    view = _view_of(db, topic, sid)
    assert view["flags"] == ["animal_to_human"]
    e = hf.entry(1, view=view)
    assert "animal to human" in html_render.card(e)
    assert 'value="animal_to_human"' in html_render.filters_form(hf.topic_model([hf.topic_study(e)]))


def test_flags_follow_the_catalog_order():
    codes = ["small_sample", "no_baseline", "correlation_causation"]
    group = [{"id": 1, "red_flags": json.dumps(codes), "judged_from": None, "quote_check": None}]
    flags = model.grading_view({"id": 1}, group)["flags"]
    assert flags == [c for c in grading.ALL_RED_FLAGS if c in codes]


def test_report_emit_carries_grade_labels(db):
    topic = _exercise()
    _animal(db, topic, 1)
    item = json.loads(report.emit(db, topic, "en"))["items"][0]
    assert item["grade_labels"] == ["animal"]
    assert item["cap_reasons"] == ["animal study"]


def test_build_report_passes_the_topic_rules(db):
    topic = _exercise()
    sid = _animal(db, topic, 1)
    rep = model.build_report(db, topic, {}, NOW)
    (shown,) = rep.new + rep.disputed
    assert shown.view["grade_labels"] == ["animal"]
    assert shown.study_id == sid
