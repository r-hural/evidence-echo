from tests.conftest import PLUGIN_DIR

REPORT = (PLUGIN_DIR / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
SETUP = (PLUGIN_DIR / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
README = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")


def test_report_skill_names_html_and_obsidian_results():
    for text in ("`html`", "html.report", "html.topic", "`obsidian`",
                 "Exported N notes to Obsidian", "html_error", "obsidian_error"):
        assert text in REPORT, text


def test_setup_skill_offers_html_and_obsidian():
    assert "html_reports" in SETUP and "Obsidian" in SETUP
    assert 'ev config set obsidian_dir "<vault folder>"' in SETUP


def test_readme_documents_both_settings():
    for text in ("`html_reports`", "EV_ECHO_HTML_REPORTS", "`obsidian_dir`", "EV_ECHO_OBSIDIAN_DIR"):
        assert text in README, text


def test_report_skill_reviews_subtopic_proposals():
    for text in ("ev --topic <id> subtopics list", "subtopics accept <slug>",
                 "subtopics map <slug> <target slug>", "subtopics reject <slug>", "`proposals` is 0"):
        assert text in REPORT, text
    step = REPORT.index("**Subtopics (after verify")
    assert REPORT.index("**Adjudicate") < step < REPORT.index("**Embed:**")
    assert "AskUserQuestion" in REPORT[step:REPORT.index("**Embed:**")]


def test_readme_lists_the_subtopics_commands_and_track_record():
    for text in ("subtopics list | accept <slug> | reject <slug>", "rename <slug> <name>",
                 "`misrepresented`", "`red_flagged`"):
        assert text in README, text


def test_report_skill_dispatches_the_comparer_twice_for_relate():
    for text in ("ev --topic <id> relate", "`evidence-echo:comparer`", "**twice**",
                 "apply --stage relate <file_a> <file_b>"):
        assert text in REPORT, text
    assert (REPORT.index("**Subtopics (after verify") < REPORT.index("**Relate (after group)")
            < REPORT.index("**Embed:**"))
    assert "ev [--topic ID] relate" in README
    assert "`relate` (`on|off`, default `on`, also per topic or `EV_ECHO_RELATE`" in README
    assert "one file per answer" in REPORT


def test_readme_explains_the_subtopic_view():
    for text in ("### What the evidence says", "consistent", "contested", "corroborated by",
                 "Unsorted", "`Subtopic: <name>` line", "`Question: <question> · <label> (<n> independent studies)`",
                 "✓ consistent, ⇅ contested, ≈ varied"):
        assert text in README, text


def test_readme_documents_the_subtopic_notes():
    assert "<Topic name>/subtopics/<subtopic>.md" in README and "also has `subtopic` and links" in README


def test_setup_skill_asks_for_formats_backfill_days_and_shows_a_table():
    for text in ("ev config set md_reports on", "ev config set html_reports on", "multiSelect",
                 "never as JSON", '"7 days" (recommended', '"365 days"', "backfill --days <N>",
                 "demo or own"):
        assert text in SETUP, text
    assert "`md_reports`" in README and "EV_ECHO_MD_REPORTS" in README
