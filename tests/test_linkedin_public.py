"""LinkedIn sources are documented with their caveats; no shipped template names a LinkedIn profile."""
import json

from tests.conftest import PLUGIN_DIR, REPO_ROOT


def _root_readme():
    # The overlay copy becomes the root README of the public tree, where release/ does not ship.
    path = REPO_ROOT / "release" / "overlay" / "README.md"
    return (path if path.exists() else REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_templates_ship_no_linkedin_sources():
    for path in sorted(PLUGIN_DIR.glob("templates/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "linkedin_apify_actor" not in data, path.name
        assert [s["name"] for s in data["sources"] if s["type"] == "linkedin"] == [], path.name
        assert "linkedin.com" not in path.read_text(encoding="utf-8").lower(), path.name


def test_linkedin_documented_with_its_caveats():
    config = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert config["userConfig"]["apify_api_token"]["description"] == "Optional; enables LinkedIn sources"
    manual = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
    section = manual.split("## Content, copyright and personal data", 1)[1].split("\n## ", 1)[0]
    for phrase in ("User Agreement forbids scraping", "not affiliated with LinkedIn or Apify",
                   "harvestapi~linkedin-profile-posts", "GDPR", "not\n  medical"):
        assert phrase in section, phrase
    root = _root_readme()
    assert "## Content, copyright and personal data" in root
    assert "README.md#content-copyright-and-personal-data" in root


def test_setup_asks_before_adding_linkedin():
    skill = (PLUGIN_DIR / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    line = next(ln for ln in skill.splitlines() if "sources add linkedin" in ln)
    assert "never as a suggestion" in line and "User Agreement forbids scraping" in line
    assert "Add only after they confirm" in line


def test_topic_without_an_actor_still_fetches_linkedin(tmp_path):
    """Templates do not name the Apify actor; a topic with a linkedin source gets the default."""
    from evidence_echo.core import topics
    data = {"id": "x", "name": "X", "domain": "X", "scope": "X",
            "sources": [{"type": "linkedin", "name": "A", "slug": "a"}]}
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "topic.json").write_text(json.dumps(data), encoding="utf-8")
    assert topics.load_topic(tmp_path, "x").linkedin_apify_actor == topics.APIFY_ACTOR
    assert topics.APIFY_ACTOR == "harvestapi~linkedin-profile-posts"
