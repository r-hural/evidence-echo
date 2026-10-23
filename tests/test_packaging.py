import json
import re
import tomllib

from evidence_echo.core import grading
from tests.conftest import PLUGIN_DIR, REPO_ROOT

SKILL = (PLUGIN_DIR / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
README = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")


def test_versions_match():
    pyproject = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    # a release bumps the three manifests together; the test needs no edit
    version = pyproject["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    assert plugin["version"] == version
    assert market["plugins"][0]["version"] == version


def test_skill_runs_launcher_without_env_or_key_text():
    assert 'python "${CLAUDE_PLUGIN_ROOT}/scripts/ev.py"' in SKILL
    for forbidden in ("${user_config", "ANTHROPIC_API_KEY", "VOYAGE_API_KEY",
                      "APIFY_API_TOKEN", "EV_ECHO_", "uv run", "PYTHONPATH"):
        assert forbidden not in SKILL, forbidden


def test_skill_handles_rejected_items():
    assert '"rejected"' in SKILL


def test_readme_documents_launcher_and_drops_known_issue():
    assert "scripts/ev.py" in README
    assert "Known issue" not in README
    assert "pip install feedparser" not in README
    assert "topics" in README


def test_skill_guards_shell_timeout_and_python_name():
    assert "Bash tool" in SKILL            # settings reach the engine only through Bash
    assert "600000" in SKILL               # first run may exceed the 2-minute default
    assert "python3" in SKILL
    assert "py -3" in SKILL


def test_skill_retries_its_own_rejected_results_file():
    assert "fix the file and run the command once more" in SKILL


def test_skill_applies_owner_rulings_with_ruling_flag():
    assert "ev --topic <id> apply --stage verify --ruling <file>" in SKILL


def test_readme_cli_lists_render_and_ruling():
    assert "report --render" in README
    assert "--ruling" in README


def test_readme_cli_lists_sources_doctor():
    assert "sources doctor" in README


def test_skill_explains_usage_errors():
    # exit code 2 is both "Python too old" (launcher) and argparse usage errors
    assert "usage:" in SKILL


SETUP = (PLUGIN_DIR / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8") \
    if (PLUGIN_DIR / "skills" / "setup" / "SKILL.md").exists() else ""


def test_setup_skill_frontmatter_and_triggers():
    assert SETUP.startswith("---\nname: setup\n")
    head = SETUP.split("---")[1]
    for phrase in ("set up", "topic", "sources", "settings"):
        assert phrase in head


def test_setup_skill_steps_use_engine_commands():
    for cmd in ("ev setup status", "ev config set reports_dir", "ev topic add",
                "ev sources add", "ev sources remove", "ev templates", "ev config set"):
        assert cmd in SETUP
    assert "AskUserQuestion" in SETUP
    assert "one question at a time" in SETUP.lower()


def test_setup_skill_never_asks_for_keys():
    assert "Never ask for a key" in SETUP or "never ask the user to paste a key" in SETUP.lower()
    assert "plugin's settings" in SETUP


def test_setup_skill_requires_a_working_source_and_offers_first_run():
    assert '"ok"' in SETUP or "`ok`" in SETUP
    assert "evidence-echo:report" in SETUP


def test_report_skill_hands_over_to_setup_and_handles_topics():
    assert "Exit code 4" in SKILL and "evidence-echo:setup" in SKILL
    assert "--topic" in SKILL and "All topics" in SKILL
    assert "overview" in SKILL
    assert "--edition" not in SKILL and "editions/" not in SKILL
    assert "ev templates" in SKILL or "ev topic list" in SKILL


def test_report_skill_handles_empty_and_broken_topics():
    section = SKILL.split("## Which topic")[1].split("## Pipeline")[0]
    assert "empty list" in section and "evidence-echo:setup" in section
    assert '"error"' in section and "skip" in section.lower()
    assert "once" in section


def test_readme_documents_topics_and_setup():
    for text in ("/evidence-echo:setup", "topic.json", "config.json", "overview.md",
                 "~/.evidence-echo", "ev topic", "ev sources add", "ev config set"):
        assert text in README
    assert "`edition`" not in README


def test_report_skill_handles_an_overview_error():
    line = next(l for l in SKILL.splitlines() if "overview_error" in l)
    assert "ev overview" in line


def test_skills_name_only_existing_commands():
    from evidence_echo import cli
    parser = cli._parser()
    commands = set(parser._subparsers._group_actions[0].choices)
    for skill in ("setup", "report"):
        text = (PLUGIN_DIR / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        for cmd in re.findall(r"`ev (?:--topic <id> )?([a-z-]+)", text):
            assert cmd in commands, f"{skill} skill names unknown command '{cmd}'"
    assert "fetch-fallback" in (PLUGIN_DIR / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
    assert "backfill" in (PLUGIN_DIR / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")


def test_skeptic_agent_is_blind_and_lists_every_code():
    text = (PLUGIN_DIR / "agents" / "skeptic.md").read_text(encoding="utf-8")
    assert "claimed_tier" not in text and "claimed_facts" not in text
    codes = re.findall(r"^- `([a-z_]+)`", text, flags=re.M)
    assert codes == list(grading.RED_FLAGS)
    assert '"red_flags"' in text and '"tier"' in text


def test_report_skill_has_no_claimed_tier_and_keeps_ruling_shape():
    text = (PLUGIN_DIR / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
    assert "claimed_tier" not in text
    assert "--ruling" in text and '"agrees": true' in text
    assert '"red_flags"' in text


def test_readme_documents_html_pages():
    text = README
    assert "### HTML pages" in text
    assert text.index("### Overview") < text.index("### HTML pages") < text.index("### How grades are checked")
    section = text.split("### HTML pages")[1].split("\n### ")[0]
    for needle in ("-report.html", "<id>.html", "overview.html", "html_reports", "Without JavaScript"):
        assert needle in section, needle


def _comparer() -> str:
    return (PLUGIN_DIR / "agents" / "comparer.md").read_text(encoding="utf-8")


def test_every_agent_file_is_in_the_manifest():
    plugin = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    on_disk = sorted(f"./agents/{p.name}" for p in (PLUGIN_DIR / "agents").glob("*.md"))
    assert sorted(plugin["agents"]) == on_disk
    assert "./agents/comparer.md" in on_disk


def test_comparer_agent_frontmatter_and_read_only_tools():
    text = _comparer()
    assert text.startswith("---\nname: comparer\n")
    head = text.split("---")[1]
    assert "description:" in head and "twice" in head
    [tools] = [line for line in text.splitlines() if line.startswith("tools:")]
    assert tools == "tools: Read"           # untrusted text in, no write tool


def test_comparer_agent_is_generic_and_blind():
    text = _comparer()
    for needle in ("`instructions`", "`choices`", "`items`", "`id`", "exactly",
                   "untrusted", "blind", '{"results": ['):
        assert needle in text, needle
    assert "SEO" not in text
    for stage_word in ("contested", "faithful", "different_population"):   # vocabularies come in the payload
        assert stage_word not in text


def test_report_skill_runs_the_primary_check_after_embed_with_two_comparers():
    assert "ev --topic <id> primary" in SKILL
    assert "evidence-echo:comparer" in SKILL and "twice" in SKILL
    assert "ev --topic <id> apply --stage primary <file_a> <file_b>" in SKILL
    embed = SKILL.index("ev --topic <id> embed")
    check = SKILL.index("ev --topic <id> primary")
    emit = SKILL.index("ev --topic <id> report`")
    assert embed < check < emit
    assert "not fetched automatically yet" not in SKILL


def test_readme_documents_the_primary_check():
    assert "primary_check" in README and "Original not reachable" in README
    assert "ev [--topic ID] primary [--emit]" in README
    assert "apply --stage primary" in README


def test_readme_documents_the_primary_reset():
    assert "ev [--topic ID] reset --stage primary [--url URL]" in README
    assert "already reported" in README


def test_report_skill_asks_the_template_question_then_classifies_before_subtopics():
    for text in ("subtopics template --adopt", "subtopics template --decline", "template_update",
                 "ev --topic <id> classify", "ev --topic <id> apply --stage classify <file>",
                 "studies_to_resort", "classify_batch", "AskUserQuestion"):
        assert text in SKILL, text
    verify = SKILL.index("ev --topic <id> verify")
    template = SKILL.index("subtopics template --adopt")
    classify = SKILL.index("ev --topic <id> classify")
    proposals = SKILL.index("**Subtopics (after verify")
    relate = SKILL.index("ev --topic <id> relate")
    assert verify < template < classify < proposals < relate
    assert "Filled in N older studies; M wait for the next run." in SKILL


def test_readme_documents_theme_history():
    for text in ("classify_batch", "EV_ECHO_CLASSIFY_BATCH", "ev [--topic ID] classify [--emit]",
                 "apply --stage classify", "subtopics template --adopt | --decline",
                 "reset --stage classify", "Studies, newest first", "edition N of series",
                 "wait to be grouped"):
        assert text in README, text


def test_report_skill_groups_between_subtopics_and_relate():
    for text in ("ev --topic <id> group", "ev --topic <id> apply --stage group <file>",
                 "Grouped N studies into questions (M new); K wait for the next run.", "new_question"):
        assert text in SKILL, text
    proposals = SKILL.index("**Subtopics (after verify")
    group = SKILL.index("ev --topic <id> group")
    relate = SKILL.index("ev --topic <id> relate")
    assert proposals < group < relate


def test_readme_documents_questions():
    for text in ("ev [--topic ID] group [--emit]", "apply --stage group", "questions list",
                 "questions merge", "move <sid>", "reset --stage group", "Other studies"):
        assert text in README, text


def test_skill_group_step_names_kinds():
    step = SKILL.split("**Group (after subtopics, before relate):**")[1].split("**Relate (after group):**")[0]
    for word in ("difference", "level", "capability", "question_kind", "comparison"):
        assert word in step
    assert "at most three rounds" in step
    assert "studies that disagree" in SKILL or "pairs of studies that disagree" in SKILL


def test_docs_name_question_kinds():
    glossary = (PLUGIN_DIR.parent.parent / "docs" / "GLOSSARY.md").read_text(encoding="utf-8")
    for term in ("**Question kind**", "**Difference question**", "**Level question**", "**Comparison**"):
        assert term in glossary
    assert "--kind" in README and "Compared:" in README


def test_report_skill_has_split_step():
    for text in ("ev --topic <id> split", "ev --topic <id> split list", "split accept", "split reject",
                 "split rename", "split source", "Rename and accept", "Retire", "AskUserQuestion",
                 "ev --topic <id> apply --stage split <file>"):
        assert text in SKILL, text
    group = SKILL.index("**Group (after subtopics, before relate):**")
    split = SKILL.index("**Split (after group, before relate):**")
    relate = SKILL.index("**Relate (after group):**")
    assert group < split < relate
    step = SKILL[split:relate]
    for word in ("Keep", "Other subtopic", "Never decide a split yourself", "Added N metrics"):
        assert word in step, word
    assert "Split <source> into N subtopics" in step


def test_report_skill_classify_rounds():
    step = SKILL.split("**Classify (after the template question, before subtopics):**")[1].split(
        "**Subtopics (after verify")[0]
    assert "at most three rounds" in step and "metric" in step
    assert "direction" not in step
    subtopics = SKILL.split("**Subtopics (after verify")[1].split("**Group (after subtopics")[0]
    assert "by the judge or classify" in subtopics and '"split": null' in subtopics


def test_setup_skill_mentions_metric_proposals():
    text = (PLUGIN_DIR / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    blank = next(line for line in text.splitlines() if line.startswith("- Blank:"))
    assert '"Other"' in blank and "proposes a metric list" in blank


def test_glossary_has_split_proposal_and_metric_list():
    glossary = (PLUGIN_DIR.parent.parent / "docs" / "GLOSSARY.md").read_text(encoding="utf-8")
    for term in ("**Split proposal**", "**Metric**", "**Measured**", "**Direction**"):
        assert term in glossary, term
    assert "from its topic's own list" in glossary and "| **Metric** |" in glossary
    assert "Measured: <metric name> · <free text>" in glossary
    assert "retired: it is not asked, not stored" in glossary


PUBLIC_REPO = "https://github.com/r-hural/evidence-echo"


def test_public_metadata():
    plugin = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["author"]["name"] == "r-hural"
    assert plugin["license"] == "MIT"
    assert plugin["homepage"] == plugin["repository"] == PUBLIC_REPO
    assert plugin["keywords"] and all(isinstance(k, str) and k for k in plugin["keywords"])
    project = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "MIT"
    assert project["authors"] == [{"name": "r-hural"}]
    assert project["urls"]["Homepage"] == PUBLIC_REPO
    market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["owner"]["name"] == "r-hural"
    assert market["plugins"][0]["license"] == "MIT"
    licence = (PLUGIN_DIR / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License") and "r-hural" in licence
    # The overlay copy becomes the root LICENSE of the public tree, where release/ does not ship.
    root_licence = REPO_ROOT / "release" / "overlay" / "LICENSE"
    if not root_licence.exists():
        root_licence = REPO_ROOT / "LICENSE"
    assert root_licence.read_text(encoding="utf-8") == licence
