import re

import pytest

from evidence_echo import cli
from tests.conftest import PLUGIN_DIR

REPORT = (PLUGIN_DIR / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
SCHEDULE = (PLUGIN_DIR / "skills" / "schedule" / "SKILL.md").read_text(encoding="utf-8")


def test_report_skill_takes_the_run_lock_and_ends_every_path():
    for text in ("--unattended", "ev --topic <id> schedule begin", '"ok": false',
                 "ev --topic <id> schedule end --outcome ok", "schedule end --outcome empty",
                 "schedule end --outcome failed --error", "ev --topic <id> notify --render-output <out>",
                 "ev --topic <id> notify --empty", 'ev --topic <id> notify --failed "<one line, no secrets>"',
                 "ev --topic <id> preflight"):
        assert text in REPORT, text
    assert REPORT.index("schedule begin") < REPORT.index("0. **Preflight:**")
    assert REPORT.index("11. **Duplicate review") < REPORT.index("12. **End of the run")


def test_report_skill_never_asks_when_unattended():
    section = REPORT.split("## Run lock and unattended runs")[1].split("## Pipeline")[0]
    assert "never use AskUserQuestion" in section.lower() or "never use AskUserQuestion, anywhere" in section
    for text in ("Preflight: run as-is", "Adjudicate", "split decisions", "duplicate review", "stays pending"):
        assert text in section, text
    for step in ("Skip this step without asking if there are no disputes. Unattended: skip it.",
                 "Unattended: skip this step; proposals stay pending.",
                 "Unattended: stop after the apply; the proposals stay pending.",
                 "Unattended: run only the duplicate check, never offer the review."):
        assert step in REPORT, step


def test_report_skill_keeps_source_errors_apart():
    assert "separate **Source errors** section" in REPORT


def test_schedule_skill_names_its_commands():
    for text in ("ev schedule show", "ev schedule status", "ev schedule install", "ev schedule uninstall",
                 "ev --topic <id> schedule set --fetch", "ev schedule config --wake", "ev notify telegram-chat-id",
                 "ev schedule channel telegram --chat-id <id>", "ev schedule channel email --host",
                 "ev notify test telegram", "ev schedule channel remove <name>",
                 "ev --topic <id> schedule set --report off --desktop-task on",
                 "/evidence-echo:report --topic <id> --unattended"):
        assert text in SCHEDULE, text


def test_schedule_skill_keeps_secrets_out_of_the_chat():
    assert "Never ask for a key, token or password in the chat" in SCHEDULE
    assert "plugin setting" in SCHEDULE and "new Claude Code session" in SCHEDULE
    assert "BotFather" in SCHEDULE


def test_schedule_skill_front_matter():
    head = SCHEDULE.split("---")[1]
    assert "name: schedule" in head and "description:" in head
    assert len(re.search(r"description: (.*)", head).group(1)) < 1024


@pytest.mark.parametrize("skill", [REPORT, SCHEDULE])
def test_skills_name_only_existing_commands(skill):
    commands = set(cli._parser()._subparsers._group_actions[0].choices)
    for cmd in re.findall(r"`ev (?:--topic <id> )?([a-z-]+)", skill):
        assert cmd in commands, cmd


@pytest.mark.parametrize("argv", [
    ["schedule", "show"], ["schedule", "status"], ["schedule", "install"], ["schedule", "uninstall"],
    ["--topic", "x", "schedule", "set", "--fetch", "daily 07:00", "--report", "weekly mon 08:00",
     "--notify", "telegram,email", "--notify-when-empty", "off", "--desktop-task", "on"],
    ["--topic", "x", "schedule", "pause"], ["--topic", "x", "schedule", "begin"],
    ["--topic", "x", "schedule", "end", "--outcome", "failed", "--error", "boom", "--trigger", "scheduled"],
    ["schedule", "config", "--wake", "every 6h", "--timezone", "Europe/Berlin", "--api-spend-limit-usd", "2",
     "--notify-when-empty", "off"],
    ["schedule", "channel", "telegram", "--chat-id", "5"],
    ["schedule", "channel", "email", "--host", "h", "--port", "587", "--user", "u", "--from", "a@b", "--to", "c@d",
     "--security", "starttls"],
    ["schedule", "channel", "remove", "telegram"],
    ["--topic", "x", "notify", "--render-output", "f.json"], ["--topic", "x", "notify", "--empty"],
    ["--topic", "x", "notify", "--failed", "boom"], ["notify", "test", "telegram"], ["notify", "telegram-chat-id"],
    ["--topic", "x", "preflight"], ["preflight"], ["run-due"]])
def test_the_commands_the_skills_show_parse(argv):
    cli._parser().parse_args(argv)


def test_unattended_runs_may_not_cut_work_to_fit_a_budget():
    text = (PLUGIN_DIR / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
    assert "process every item each stage emits" in text and "budget figure" in text


def test_docs_use_the_new_limit_names():
    for rel in ("skills/schedule/SKILL.md", "README.md"):
        text = (PLUGIN_DIR / rel).read_text(encoding="utf-8")
        assert "--api-spend-limit-usd" in text and "--session-max-total-tokens" in text
        assert "--budget-usd" not in text and "cost cap" not in text.lower()


def test_the_report_skill_offers_a_duplicate_review_on_its_own():
    text = (PLUGIN_DIR / "skills" / "report" / "SKILL.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    assert "review possible duplicates" in front
    assert "## Duplicate review only" in text
    section = text.split("## Duplicate review only")[1].split("\n## ")[0]
    for command in ("studies duplicates", "studies merge", "studies distinct", "AskUserQuestion"):
        assert command in section
    for phrase in ("Same study — merge", "Different studies", "never merge"):
        assert phrase in section
    assert "ev --topic <id> fetch" not in section and "report --render" not in section


def test_the_readme_says_how_to_decide_duplicates_from_a_message():
    text = (PLUGIN_DIR / "README.md").read_text(encoding="utf-8")
    assert "review Evidence Echo duplicates" in text
