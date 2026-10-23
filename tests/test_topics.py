import json

import pytest

from evidence_echo.core import topics
from evidence_echo.core.settings import ConfigError
from tests.conftest import PLUGIN_DIR

TEMPLATES = PLUGIN_DIR / "templates"
BASE = {"id": "x", "name": "X", "domain": "X research", "scope": "Only X.", "sources": []}


def _write_topic(topics_dir, data, folder=None):
    d = topics_dir / (folder or data["id"])
    d.mkdir(parents=True)
    (d / "topic.json").write_text(json.dumps(data), encoding="utf-8")


def test_seo_template_loads():
    t = topics.load_template("seo", TEMPLATES)
    assert (t.id, t.domain) == ("seo", "SEO and AI search")
    assert t.template == "seo"
    assert t.linkedin_apify_actor == "harvestapi~linkedin-profile-posts"
    assert any(s["type"] == "rss" for s in t.sources)
    ids = [t["id"] for t in topics.list_templates(TEMPLATES)]
    assert ids == topics.template_ids(TEMPLATES) and {"ai-coding", "cro", "exercise", "seo"} <= set(ids)


@pytest.mark.parametrize("bad", ["../seo", "SEO", "seo.json", "", "templates/seo"])
def test_path_like_template_ids_are_unknown(bad):
    available = ", ".join(topics.template_ids(TEMPLATES))
    with pytest.raises(ConfigError, match=rf"Unknown template .*Available: {available}$"):
        topics.load_template(bad, TEMPLATES)


@pytest.mark.parametrize("tid", ["a", "weight-loss", "seo2", "a" * 40])
def test_valid_ids(tid):
    assert topics.ID_RE.match(tid)


@pytest.mark.parametrize("tid", ["", "-a", "A", "a_b", "a b", "a" * 41, "ä", "a\n"])
def test_invalid_ids(tid):
    assert not topics.ID_RE.match(tid)


def test_validate_names_file_and_field():
    with pytest.raises(ConfigError, match=r"^x/topic\.json: field 'scope' missing$"):
        topics.validate({k: v for k, v in BASE.items() if k != "scope"}, "x/topic.json", "x")
    with pytest.raises(ConfigError, match=r"field 'sources' must be a list"):
        topics.validate(dict(BASE, sources="feeds"), "x/topic.json", "x")
    with pytest.raises(ConfigError, match=r"field 'id' is 'y', must equal the folder name 'x'"):
        topics.validate(dict(BASE, id="y"), "x/topic.json", "x")


def test_validate_overrides_use_settings_checks():
    t = topics.validate(dict(BASE, fetch_horizon_days="14", output_language=None), "f", "x")
    assert t.fetch_horizon_days == 14 and t.output_language is None
    with pytest.raises(ConfigError, match=r"f: field 'fetch_horizon_days'"):
        topics.validate(dict(BASE, fetch_horizon_days=0), "f", "x")
    with pytest.raises(ConfigError, match=r"f: unknown field 'colour'"):
        topics.validate(dict(BASE, colour="red"), "f", "x")


@pytest.mark.parametrize("source,msg", [
    ({"type": "rss", "name": "A"}, "'url'"),
    ({"type": "linkedin", "name": "A"}, "'slug'"),
    ({"type": "ftp", "name": "A", "url": "x"}, "type"),
    ("feed", "object"),
])
def test_validate_sources(source, msg):
    with pytest.raises(ConfigError, match=msg):
        topics.validate(dict(BASE, sources=[source]), "f", "x")


def test_source_names_unique():
    src = {"type": "rss", "name": "A", "url": "https://a/feed"}
    with pytest.raises(ConfigError, match="duplicate source name 'A'"):
        topics.validate(dict(BASE, sources=[src, src]), "f", "x")


def test_topic_ids_skip_hidden_and_folders_without_file(tmp_path):
    _write_topic(tmp_path, BASE)
    _write_topic(tmp_path, dict(BASE, id="b"))
    (tmp_path / ".removed-c-1").mkdir()
    (tmp_path / "empty").mkdir()
    assert topics.topic_ids(tmp_path) == ["b", "x"]


def test_load_topic_and_unknown(tmp_path):
    _write_topic(tmp_path, BASE)
    assert topics.load_topic(tmp_path, "x").name == "X"
    with pytest.raises(ConfigError, match=r"^Unknown topic 'nope'\. Topics: x$"):
        topics.load_topic(tmp_path, "nope")


def test_save_topic_roundtrip(tmp_path):
    t = topics.validate(dict(BASE, subtopics=["a"]), "f", "x")
    path = topics.save_topic(tmp_path, t)
    assert path == tmp_path / "x" / "topic.json"
    assert topics.load_topic(tmp_path, "x") == t
    assert json.loads(path.read_text(encoding="utf-8"))["subtopics"] == ["a"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "C:/x/feed.xml", "ftp://a/feed", "feed.xml"])
def test_rss_url_must_be_http(url):
    src = {"type": "rss", "name": "A", "url": url}
    with pytest.raises(ConfigError, match=r"^f: rss source 'A' needs an http\(s\) url$"):
        topics.validate(dict(BASE, sources=[src]), "f", "x")


def test_rss_url_scheme_case_insensitive():
    src = {"type": "rss", "name": "A", "url": "HTTPS://a/feed"}
    assert topics.validate(dict(BASE, sources=[src]), "f", "x").sources == [src]


def test_topic_fetch_horizon_and_scan_depth():
    t = topics.validate(dict(BASE, fetch_horizon_days=14, scan_depth="full"), "f", "x")
    assert t.fetch_horizon_days == 14 and t.scan_depth == "full"
    assert "lookback_days" not in topics.to_dict(t)
    with pytest.raises(ConfigError, match="scan_depth"):
        topics.validate(dict(BASE, scan_depth="deep"), "f", "x")
    with pytest.raises(ConfigError, match="unknown field 'lookback_days'"):
        topics.validate(dict(BASE, lookback_days=14), "f", "x")


def test_unknown_source_kind_message():
    src = {"type": "youtube", "name": "A", "url": "https://y/"}
    with pytest.raises(ConfigError, match=r"^f: source type must be one of "):
        topics.validate(dict(BASE, sources=[src]), "f", "x")


def test_source_scan_depth_checked():
    src = {"type": "rss", "name": "A", "url": "https://a/feed", "scan_depth": "deep"}
    with pytest.raises(ConfigError, match=r"^f: rss source 'A' scan_depth must be headlines or full$"):
        topics.validate(dict(BASE, sources=[src]), "f", "x")


def test_topic_adaptive_fetch_and_fetch_every():
    src = {"type": "rss", "name": "R", "url": "https://e.com/feed", "fetch_every": 7}
    t = topics.validate(dict(BASE, adaptive_fetch="on", sources=[src]), "f", "x")
    assert t.adaptive_fetch == "on" and t.sources[0]["fetch_every"] == 7


@pytest.mark.parametrize("bad", [0, 61, 2.5, True, "7"])
def test_fetch_every_rejected(bad):
    src = {"type": "rss", "name": "R", "url": "https://e.com/feed", "fetch_every": bad}
    with pytest.raises(ConfigError, match="fetch_every"):
        topics.validate(dict(BASE, sources=[src]), "f", "x")


def test_validate_rejects_old_theme_fields_and_accepts_subtopics():
    with pytest.raises(ConfigError, match="unknown field 'themes'"):
        topics.validate(dict(BASE, themes=["A"]), "f", "x")
    with pytest.raises(ConfigError, match="unknown field 'theme_renames'"):
        topics.validate(dict(BASE, theme_renames={"A": "B"}), "f", "x", template=True)
    with pytest.raises(ConfigError, match="unknown field 'subtopic_renames'"):
        topics.validate(dict(BASE, subtopic_renames={"A": "B"}), "f", "x")
    t = topics.validate(dict(BASE, subtopics=["A"], subtopic_renames={"C": "A"}), "f", "x", template=True)
    assert t.subtopics == ["A"]
