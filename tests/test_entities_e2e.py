"""Entities end to end: judge, match, merge, classify an older study, render the topic page."""
import json
from datetime import datetime, timezone

from evidence_echo.core import interfaces, store
from evidence_echo.render import html as html_render
from evidence_echo.render import model
from evidence_echo.stages import classify, judge, match
from tests.conftest import PLUGIN_DIR
from tests.test_classify import answer, seeded, study   # noqa: F401  (seeded is a fixture)
from tests.test_judge import VERDICT, _seed_extracted
from tests.test_render_html_subtopics import _data

NOW = datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc)


def test_skill_and_readme_name_entity_commands():
    skill = (PLUGIN_DIR / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
    readme = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
    assert "entities merge" in skill and "studies entity" in skill and "entities (the named things" in skill
    assert "ev entities" in readme and "entity filter" in readme


def _judged(db, n, entities):
    iid = _seed_extracted(db, url=f"https://example.com/e2e-{n}")
    facts = dict(VERDICT["facts"], match_keys={"key_numbers": [f"{n}.{n}%"]})
    out = judge.apply(db, json.dumps({"results": [dict(VERDICT, id=interfaces.opaque_id(iid), facts=facts,
                                                       primary_url=f"https://example.com/orig-{n}",
                                                       entities=entities)]}))
    assert out["applied"] == 1
    return iid


def test_entities_end_to_end(seeded, topic):
    db = seeded
    first, second = _judged(db, 1, ["GPT-4o"]), _judged(db, 2, ["ChatGPT", "Shopify"])
    match.fixed_pass(db)
    sids = [store.get_item(db, i)["study_id"] for i in (first, second)]
    assert db.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 3
    db.execute("UPDATE items SET state='verified' WHERE id IN (?,?)", (first, second))
    db.commit()
    store.merge_entities(db, "gpt-4o", "chatgpt")
    old = study(db)                                                   # judged before entities existed
    assert classify.apply(db, answer(old, entities=["Shopify"]))["applied"] == 1
    store.record_report(db, "/r/1.md", [(sid, f"Study {sid}", "summary") for sid in (*sids, old)])
    page = html_render.topic_page(model.build_topic(db, topic, NOW), "overview.html", "r.html")
    rows = {r["id"]: r for r in _data(page)["studies"]}
    assert rows[sids[0]]["entities"] == ["chatgpt"] and rows[sids[1]]["entities"] == ["chatgpt", "shopify"]
    assert rows[old]["entities"] == ["shopify"]
    select = page[page.index('<select name="entity">'):]
    select = select[:select.index("</select>")]
    assert "ChatGPT (2)" in select and "Shopify (2)" in select and "GPT-4o" not in select
    card = page[page.index(f'id="s{sids[1]}"'):]
    card = card[:card.index("</article>")]
    assert "<b>About:</b> ChatGPT, Shopify" in card
