"""Wave 4a Part 4: ev subtopics list's template_update, ev subtopics template --adopt | --decline."""
import json
import shutil

import pytest

from evidence_echo import cli
from evidence_echo.core import config, settings, store, topics
from evidence_echo.core.settings import ConfigError

OLD = ["Links", "AI search / GEO", "Technical SEO", "Local"]
NEW = ["Backlinks", "AI Overviews & AI search", "Core Web Vitals & speed", "Local", "Guest posts", "Other"]
RENAMES = {"AI search / GEO": "AI Overviews & AI search"}


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def _study(db, slug, version=None):
    sid = store.create_study(db, "B", {"what_was_tested": slug}, None,
                             subtopic_id=store.get_subtopic(db, slug)["id"])
    db.execute("UPDATE studies SET classified_version=? WHERE id=?", (version, sid))
    db.commit()
    return sid


@pytest.fixture
def old(db):
    store.seed_subtopics(db, OLD)
    store.subtopic_for_choice(db, {"propose": "Core Web Vitals & speed"}, create=True)   # pending
    store.subtopic_for_choice(db, {"propose": "Image SEO"}, create=True)
    store.accept_subtopic(db, "image-seo")                                                # confirmed
    store.subtopic_for_choice(db, {"propose": "Guest posts"}, create=True)
    store.reject_subtopic(db, "guest-posts")                                              # rejected
    store.subtopic_for_choice(db, {"propose": "Tech stuff"}, create=True)
    return db


def test_seo_template_declares_the_rename(topic):
    assert topics.template_subtopics("seo", config.TEMPLATES_DIR)[1] == {
        "AI search / GEO": "AI Overviews & AI search"}
    assert topics.template_subtopics("blank", config.TEMPLATES_DIR) is None


def test_plan_writes_nothing(old):
    before = store.list_subtopics(old)
    assert store.template_plan(old, NEW, RENAMES) == {
        "add": ["Backlinks", "Core Web Vitals & speed", "Guest posts", "Other"],
        "retire": ["Links", "Technical SEO"],
        "carry": {"AI search / GEO": "AI Overviews & AI search"}}
    assert store.list_subtopics(old) == before


def test_adopt_carries_retires_revives_and_resorts(old):
    links, ai, tech, img = (_study(old, s, "classify-1") for s in
                            ("links", "ai-search-geo", "technical-seo", "image-seo"))
    store.map_subtopic(old, "tech-stuff", "technical-seo")
    mapped = _study(old, "tech-stuff")                    # resolves to technical-seo
    out = store.adopt_template_subtopics(old, NEW, RENAMES)
    assert out == {"added": ["Backlinks", "Core Web Vitals & speed", "Guest posts", "Other"],
                   "carried": {"AI search / GEO": "AI Overviews & AI search"},
                   "retired": ["Links", "Technical SEO"], "studies_to_resort": 3}
    status = {t["slug"]: (t["status"], t["name"]) for t in store.list_subtopics(old)}
    assert status["ai-search-geo"] == ("seeded", "AI Overviews & AI search")         # slug kept
    assert status["links"][0] == status["technical-seo"][0] == "retired"
    assert status["core-web-vitals-speed"][0] == status["guest-posts"][0] == "seeded"
    assert status["image-seo"][0] == "confirmed"
    assert [t["slug"] for t in store.active_subtopics(old)] == [
        "backlinks", "ai-search-geo", "core-web-vitals-speed", "local", "guest-posts", "other",
        "image-seo"]
    for sid in (links, tech, mapped):
        assert (store.get_study(old, sid)["subtopic_id"], store.get_study(old, sid)["classified_version"]) == (None, None)
    assert store.get_study(old, ai)["subtopic_id"] == store.get_subtopic(old, "ai-search-geo")["id"]
    assert store.get_study(old, img)["classified_version"] == "classify-1"


def test_adopt_twice_changes_nothing(old):
    store.adopt_template_subtopics(old, NEW, RENAMES)
    before = store.list_subtopics(old)
    assert store.adopt_template_subtopics(old, NEW, RENAMES) == {
        "added": [], "carried": {}, "retired": [], "studies_to_resort": 0}
    assert store.list_subtopics(old) == before


def test_template_name_of_a_merged_away_subtopic_revives_it(db):
    store.seed_subtopics(db, ["Backlinks", "Links"])
    store.merge_subtopics(db, "links", "backlinks")
    out = store.adopt_template_subtopics(db, ["Links"], {})
    assert out["added"] == ["Links"] and out["retired"] == ["Backlinks"]
    links = store.get_subtopic(db, "links")
    assert (links["status"], links["merged_into"]) == ("seeded", None)


def test_validate_template_fields(tmp_path):
    base = {"id": "t", "name": "T", "domain": "d", "scope": "s", "sources": []}
    assert topics.validate({**base, "subtopic_renames": {"a": "b"}}, "t.json", template=True).id == "t"
    with pytest.raises(ConfigError, match="unknown field 'subtopic_renames'"):
        topics.validate({**base, "subtopic_renames": {"a": "b"}}, "topic.json")
    with pytest.raises(ConfigError, match="subtopic_renames"):
        topics.validate({**base, "subtopic_renames": {"a": ""}}, "t.json", template=True)
    assert topics.validate({**base, "template_subtopics_declined": None}, "x").template_subtopics_declined is None
    assert topics.validate({**base, "template_subtopics_declined": "ab12"}, "x").template_subtopics_declined == "ab12"
    with pytest.raises(ConfigError, match="template_subtopics_declined"):
        topics.validate({**base, "template_subtopics_declined": 3}, "x")


def test_new_topic_from_the_template_gets_no_renames(workspace):
    t = topics.create_from_template(workspace / "topics", "seo2", "seo", config.TEMPLATES_DIR)
    data = json.loads((workspace / "topics" / "seo2" / "topic.json").read_text(encoding="utf-8"))
    assert "subtopic_renames" not in data and data["template_subtopics_declined"] is None
    assert t.template == "seo"


def test_no_update_when_the_list_matches(workspace, capsys):
    assert "template_update" not in _run(capsys, "subtopics", "list")[1]


def test_update_decline_and_adopt_through_the_cli(workspace, capsys):
    tpl = topics.load_template("seo", config.TEMPLATES_DIR).subtopics
    topics.set_field(workspace / "topics", "seo", "subtopics", json.dumps(["Links", "AI search / GEO"]))
    update = _run(capsys, "subtopics", "list")[1]["template_update"]
    assert (update["template"], update["count"], update["retire"]) == ("seo", 15, ["Links"])
    assert update["carry"] == {"AI search / GEO": "AI Overviews & AI search"}
    assert len(update["add"]) == 14 and "AI Overviews & AI search" not in update["add"]
    code, out, _ = _run(capsys, "subtopics", "template", "--decline")
    assert code == 0 and out["declined"] == "seo"
    assert topics.load_topic(workspace / "topics", "seo").template_subtopics_declined == out["hash"]
    assert "template_update" not in _run(capsys, "subtopics", "list")[1]
    code, out, _ = _run(capsys, "subtopics", "template", "--adopt")
    assert code == 0 and out["retired"] == ["Links"] and out["studies_to_resort"] == 0
    t = topics.load_topic(workspace / "topics", "seo")
    assert (t.subtopics, t.template_subtopics_declined) == (tpl, None)
    listed = _run(capsys, "subtopics", "list")[1]
    assert "template_update" not in listed and listed["subtopics"][0]["name"] == tpl[0]


def test_a_changed_template_asks_again(workspace, capsys, tmp_path, monkeypatch):
    topics.set_field(workspace / "topics", "seo", "subtopics", json.dumps(["Links"]))
    _run(capsys, "subtopics", "template", "--decline")
    copy = tmp_path / "templates"
    shutil.copytree(config.TEMPLATES_DIR, copy)
    data = json.loads((copy / "seo.json").read_text(encoding="utf-8"))
    data["subtopics"] = data["subtopics"] + ["Video SEO"]
    (copy / "seo.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config, "TEMPLATES_DIR", copy)
    assert _run(capsys, "subtopics", "list")[1]["template_update"]["count"] == 16


def test_blank_topic_has_no_template(workspace, capsys):
    _run(capsys, "topic", "add", "ai", "--blank", "--name", "AI", "--domain", "AI coding", "--scope", "s")
    assert "template_update" not in _run(capsys, "--topic", "ai", "subtopics", "list")[1]
    code, _, err = _run(capsys, "--topic", "ai", "subtopics", "template", "--adopt")
    assert code == 2 and "has no template" in err


def test_template_needs_one_flag(workspace, capsys):
    with pytest.raises(SystemExit):
        cli.main(["subtopics", "template"])


def test_slug_only_match_keeps_the_owners_rename(db):
    """Review I3: a subtopic matched only by slug keeps the owner's name and is not 'carried'."""
    store.seed_subtopics(db, ["Local"])
    store.rename_subtopic(db, "local", "Local SEO (my name)")
    assert store.template_plan(db, ["Local"], {})["carry"] == {}
    out = store.adopt_template_subtopics(db, ["Local"], {})
    assert out["carried"] == {}
    assert store.get_subtopic(db, "local")["name"] == "Local SEO (my name)"


def test_name_match_still_takes_the_template_spelling(db):
    store.seed_subtopics(db, ["local"])
    assert store.adopt_template_subtopics(db, ["Local"], {})["carried"] == {"local": "Local"}
    assert store.get_subtopic(db, "local")["name"] == "Local"
