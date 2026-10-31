"""Owner tools for findings: ev findings list / move, and the mode-aware question commands."""
import json
from dataclasses import replace

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, store
from tests.test_group_findings import fq, with_findings


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


@pytest.fixture
def fm(workspace, monkeypatch):
    """Findings-mode topic: two studies with two findings each, two findings-mode questions."""
    monkeypatch.setenv("EV_ECHO_GROUPING_MODE", "findings")
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    s1, (f1, f2) = with_findings(conn, subtopic=slug)
    s2, (f3, f4) = with_findings(conn, subtopic=slug)
    q1 = fq(conn, "Does X raise Y?", "level", subtopic=slug)
    q2 = fq(conn, "Does Z raise W?", "level", subtopic=slug)
    store.set_finding_question(conn, f1, q1, None, store.GROUP_VERSION)
    store.set_finding_question(conn, f3, q1, None, store.GROUP_VERSION)
    conn.close()
    return dict(slug=slug, s1=s1, s2=s2, f=(f1, f2, f3, f4), q1=q1, q2=q2, cfg=cfg)


def test_findings_list_filters(fm, capsys):
    f1, f2, f3, f4 = fm["f"]
    code, out, _ = _run(capsys, "findings", "list")
    assert code == 0 and len(out["findings"]) == 4
    row = next(r for r in out["findings"] if r["id"] == f"f{f1}")
    assert row == {"id": f"f{f1}", "study": f"s{fm['s1']}", "position": 1, "text": f"finding 0 of {fm['s1']}",
                   "quote_checked": True, "metric": None, "question": f"q{fm['q1']}", "direction": None,
                   "by_owner": False}
    by_study = _run(capsys, "findings", "list", "--study", f"s{fm['s2']}")[1]["findings"]
    assert [r["id"] for r in by_study] == [f"f{f3}", f"f{f4}"]
    by_q = _run(capsys, "findings", "list", "--question", f"q{fm['q1']}")[1]["findings"]
    assert [r["id"] for r in by_q] == [f"f{f1}", f"f{f3}"]
    conn = store.connect(fm["cfg"].db_path)
    store.merge_questions(conn, fm["q1"], fm["q2"])
    conn.close()
    merged = _run(capsys, "findings", "list", "--question", f"q{fm['q2']}")[1]["findings"]
    assert [r["id"] for r in merged] == [f"f{f1}", f"f{f3}"]
    assert merged[0]["question"] == f"q{fm['q2']}"
    via_old = _run(capsys, "findings", "list", "--question", f"q{fm['q1']}")[1]["findings"]
    assert [r["id"] for r in via_old] == [f"f{f1}", f"f{f3}"]


def test_findings_list_unknown_ids_are_usage_errors(fm, capsys):
    assert _run(capsys, "findings", "list", "--study", "s999")[0] == 2
    assert _run(capsys, "findings", "list", "--question", "q999")[0] == 2


def test_findings_move_pins_and_survives_regroup(fm, capsys):
    f1, f2, f3, f4 = fm["f"]
    code, out, _ = _run(capsys, "findings", "move", f"f{f2}", f"q{fm['q2']}")
    assert code == 0 and out == {"moved": f"f{f2}", "into": f"q{fm['q2']}"}
    conn = store.connect(fm["cfg"].db_path)
    assert store.get_finding(conn, f2)["by_owner"] == 1
    conn.close()
    api.reset(replace(fm["cfg"], grouping_mode="findings"), "group")
    conn = store.connect(fm["cfg"].db_path)
    assert store.get_finding(conn, f2)["question_id"] == fm["q2"]
    assert store.get_finding(conn, f1)["question_id"] is None
    conn.execute("UPDATE findings SET grouped_version='group-old'")
    conn.commit()
    assert f2 not in [f["id"] for f in store.findings_to_group(conn)]
    conn.close()


def test_findings_move_new_question_in_findings_mode(fm, capsys):
    f1, f2, *_ = fm["f"]
    code, out, _ = _run(capsys, "findings", "move", f"f{f2}", "--new", "Does V raise U?", "--measure", "U",
                        "--kind", "level")
    assert code == 0 and out["moved"] == f"f{f2}"
    conn = store.connect(fm["cfg"].db_path)
    made = store.get_question(conn, int(out["created"][1:]))
    assert made["mode"] == "findings" and made["subtopic_id"] == store.get_subtopic(conn, fm["slug"])["id"]
    assert store.get_finding(conn, f2)["question_id"] == made["id"]
    conn.close()


def test_findings_move_option_rules(fm, capsys):
    f2 = fm["f"][1]
    assert _run(capsys, "findings", "move", f"f{f2}")[0] == 2
    assert _run(capsys, "findings", "move", f"f{f2}", f"q{fm['q2']}", "--measure", "M")[0] == 2
    assert _run(capsys, "findings", "move", f"f{f2}", "--new", "A?")[0] == 2
    assert _run(capsys, "findings", "move", f"f{f2}", "--new", "A?", "--measure", "M")[0] == 2
    assert _run(capsys, "findings", "move", f"f{f2}", "--new", "A?", "--measure", "M",
                "--kind", "difference")[0] == 2


def test_findings_move_refuses_same_study_question(fm, capsys):
    f1, f2, *_ = fm["f"]
    code, _, err = _run(capsys, "findings", "move", f"f{f2}", f"q{fm['q1']}")
    assert code == 2 and "already has a finding" in err
    conn = store.connect(fm["cfg"].db_path)
    assert store.get_finding(conn, f2)["question_id"] is None
    conn.close()


def test_findings_move_refused_new_question_is_deleted(fm, capsys):
    """A refused move leaves no question behind: finding 1 filed under a stale (inactive) subtopic."""
    f2 = fm["f"][1]
    conn = store.connect(fm["cfg"].db_path)
    conn.execute("UPDATE subtopics SET status='retired' WHERE slug=?", (fm["slug"],))
    conn.commit()
    n = len(store.list_questions(conn))
    conn.close()
    code, _, err = _run(capsys, "findings", "move", f"f{f2}", "--new", "Fresh?", "--measure", "M",
                        "--kind", "level")
    assert code == 2
    conn = store.connect(fm["cfg"].db_path)
    assert len(store.list_questions(conn)) == n
    conn.close()


def test_questions_list_mode_default_and_flag(fm, capsys):
    conn = store.connect(fm["cfg"].db_path)
    plain = store.create_question(conn, store.get_subtopic(conn, fm["slug"])["id"], "Plain?", "M", "level")
    conn.close()
    rows = _run(capsys, "questions", "list")[1]["questions"]
    assert {r["id"] for r in rows} == {f"q{fm['q1']}", f"q{fm['q2']}"}
    assert next(r for r in rows if r["id"] == f"q{fm['q1']}")["studies"] == 2
    rows = _run(capsys, "questions", "list", "--mode", "questions")[1]["questions"]
    assert [r["id"] for r in rows] == [f"q{plain}"]


def test_questions_list_defaults_to_questions_mode(workspace, capsys):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    slug = store.active_subtopics(conn)[0]["slug"]
    fqid = fq(conn, subtopic=slug)
    plain = store.create_question(conn, store.get_subtopic(conn, slug)["id"], "Plain?", "M", "level")
    conn.close()
    assert [r["id"] for r in _run(capsys, "questions", "list")[1]["questions"]] == [f"q{plain}"]
    found = _run(capsys, "questions", "list", "--mode", "findings")[1]["questions"]
    assert [r["id"] for r in found] == [f"q{fqid}"]


def test_questions_move_in_findings_mode_refused(fm, capsys):
    code, _, err = _run(capsys, "questions", "move", f"s{fm['s1']}", f"q{fm['q1']}")
    assert code == 2 and "ev findings move f<id> q<id>" in err


def test_questions_merge_mixed_modes_refused(fm, capsys):
    conn = store.connect(fm["cfg"].db_path)
    plain = store.create_question(conn, store.get_subtopic(conn, fm["slug"])["id"], "Plain?", "M", "level")
    conn.close()
    code, _, err = _run(capsys, "questions", "merge", f"q{fm['q1']}", f"q{plain}")
    assert code == 2 and "different grouping modes" in err


def test_questions_rename_works_on_findings_question(fm, capsys):
    code, out, _ = _run(capsys, "questions", "rename", f"q{fm['q1']}", "Does X lift Y?")
    assert code == 0 and out["text"] == "Does X lift Y?"
    conn = store.connect(fm["cfg"].db_path)
    assert store.get_question(conn, fm["q1"])["owner_touched"] == 1
    conn.close()
