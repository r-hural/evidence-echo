"""Findings-mode Markdown: a card lists every question its findings joined; questions mode is unchanged."""
from evidence_echo.render import markdown
from tests.html_fixtures import entry, report

EVIL = "<b>x</b> [link](http://e) &amp; [[x]] #tag | a\nsecond line"


def q(question="Does A help?", label="consistent", independent=2, finding="Clicks rose 4%", checked=True):
    return {"question_id": 1, "question": question, "label": label, "independent": independent, "echo": None,
            "finding": finding, "quote_checked": checked}


def sub(*questions, waiting=False):
    return {"key": "local", "name": "Local", "question": None, "label": None, "independent": 0, "echo": None,
            "questions": list(questions), "waiting": waiting}


def test_markdown_entry_lists_every_question_with_finding():
    e = entry(1, subtopic=sub(q(), q("Does B hurt?", label=None, independent=1, finding="Rankings fell"),
                              q("Is C slow?", checked=False), waiting=True))
    lines = markdown.entry_lines(e)
    at = lines.index("Subtopic: Local  ")
    assert lines[at + 1] == "Question: Does A help? · ✓ consistent (2 independent studies) — Clicks rose 4%  "
    assert lines[at + 2] == "Question: Does B hurt? — Rankings fell  "
    assert lines[at + 3].startswith("Question: Is C slow? · ✓ consistent (2 independent studies) — Clicks rose 4%")
    assert lines[at + 3].endswith(markdown.UNCHECKED_NOTE + "  ")
    assert lines[at + 4] == "Findings: waiting to be split  "
    assert markdown.questions_lines(sub(q(), q("Does B hurt?", label=None, independent=1)))[1:] == [
        ("Question", "Does A help? · ✓ consistent (2 independent studies) — Clicks rose 4%"),
        ("Question", "Does B hurt? — Clicks rose 4%")]
    assert markdown.subtopic_lines(sub(q()))[0] == ("Subtopic", "Local")
    text = markdown.report_markdown(report(new=[e], mode="findings"))
    assert "Question: Does A help?" in text


def test_markdown_findings_text_is_plain():
    out = "\n".join(markdown.entry_lines(entry(1, subtopic=sub(q(question=EVIL, finding=EVIL)))))
    line = next(x for x in out.splitlines() if x.startswith("Question:"))
    assert "<b>" not in line and "[link]" not in line and "&lt;b>" in line and "\[link\]" in line
    assert "\n" not in line.rstrip("\n") and "second line" in line


def test_markdown_questions_mode_unchanged():
    base = {"key": "local", "name": "Local", "question": "Does X?", "label": "contested", "independent": 2,
            "echo": None}
    assert markdown.subtopic_lines(base) == [
        ("Subtopic", "Local"), ("Question", "Does X? · ⇅ contested (2 independent studies)")]
    lines = markdown.entry_lines(entry(1, subtopic=base))
    assert "Question: Does X? · ⇅ contested (2 independent studies)  " in lines
    assert not any(x.startswith("Findings") for x in lines)   # plus tests/test_report_golden.py


def test_markdown_findings_mode_card_has_no_quotes_mode_findings_list():
    from tests.html_fixtures import finding
    lines = markdown.entry_lines(entry(1, subtopic=sub(q()), findings=[finding(1)], findings_state="done"))
    assert not any(x.startswith("Findings") for x in lines)
    lines = markdown.entry_lines(entry(1, subtopic=sub(waiting=True), findings=[], findings_state="waiting"))
    assert [x for x in lines if x.startswith("Findings")] == ["Findings: waiting to be split  "]
    quotes = {"key": "local", "name": "Local", "question": None, "label": None, "independent": 0, "echo": None}
    lines = markdown.entry_lines(entry(1, subtopic=quotes, findings=[finding(1)], findings_state="done"))
    assert "Findings:  " in lines      # quotes mode keeps its list
