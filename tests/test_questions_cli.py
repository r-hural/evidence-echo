"""Questions: ev questions list, rename, merge, move."""
import json

import pytest

from evidence_echo import cli
from evidence_echo.core import config, store
from tests.test_knowledge_questions import add, q


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


@pytest.fixture
def seeded(workspace):
    """The workspace with two grouped studies, a second question and a study in another subtopic."""
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    first, second = (s["slug"] for s in store.active_subtopics(conn)[:2])
    one = q(conn, subtopic=first, text="Does X raise Y?", measure="Y")
    two = q(conn, subtopic=first, text="Does Z lower W?", measure="W")
    elsewhere = q(conn, subtopic=second, text="Other subtopic?", measure="M")
    a = add(conn, subtopic=first, question=one, by="A")
    b = add(conn, subtopic=first, question=one, by="B", direction="down")
    c = add(conn, subtopic=first, by="C")
    conn.close()
    return dict(first=first, second=second, one=one, two=two, elsewhere=elsewhere, a=a, b=b, c=c)


def test_list_and_filter_by_subtopic(seeded, capsys):
    code, out, _ = _run(capsys, "questions", "list")
    assert code == 0
    rows = {r["id"]: r for r in out["questions"]}
    one = rows[f"q{seeded['one']}"]
    assert one == {"id": f"q{seeded['one']}", "subtopic": seeded["first"], "text": "Does X raise Y?",
                   "measure": "Y", "kind": "difference", "comparison": "with X vs without X",
                   "studies": 2, "independent": 2, "label": "contested",
                   "merged_into": None, "owner_touched": False}
    assert rows[f"q{seeded['two']}"]["studies"] == 0 and rows[f"q{seeded['two']}"]["label"] is None
    only = _run(capsys, "questions", "list", "--subtopic", seeded["second"])[1]["questions"]
    assert [r["id"] for r in only] == [f"q{seeded['elsewhere']}"]
    assert _run(capsys, "questions", "list", "--subtopic", "no-such")[0] == 2


def test_rename_and_measure_via_cli(seeded, capsys):
    qid = f"q{seeded['one']}"
    code, out, _ = _run(capsys, "questions", "rename", qid, "Does X lift Y?")
    assert code == 0 and out == {"renamed": qid, "text": "Does X lift Y?", "measure": "Y",
                                 "kind": "difference", "comparison": "with X vs without X",
                                 "directions_cleared": 0}
    code, out, _ = _run(capsys, "questions", "rename", qid, "Does X lift Y?", "--measure", "Y per page")
    assert out["directions_cleared"] == 2 and out["measure"] == "Y per page"
    rows = {r["id"]: r for r in _run(capsys, "questions", "list")[1]["questions"]}
    assert rows[qid]["owner_touched"] is True and rows[qid]["label"] is None   # directions are asked again
    assert _run(capsys, "questions", "rename", qid, "x" * 121)[0] == 2


def test_merge_refusal_exits_2_with_message(seeded, capsys):
    code, _, err = _run(capsys, "questions", "merge", f"q{seeded['one']}", f"q{seeded['one']}")
    assert code == 2 and "itself" in err
    code, out, _ = _run(capsys, "questions", "merge", f"q{seeded['two']}", f"q{seeded['one']}")
    assert code == 0 and out["merged"] == f"q{seeded['two']}" and out["into"] == f"q{seeded['one']}"
    rows = {r["id"]: r for r in _run(capsys, "questions", "list")[1]["questions"]}
    assert rows[f"q{seeded['two']}"]["merged_into"] == f"q{seeded['one']}"
    assert _run(capsys, "questions", "merge", "q9999", f"q{seeded['one']}")[0] == 2


def test_move_into_existing_and_new_question(seeded, capsys):
    code, out, _ = _run(capsys, "questions", "move", f"s{seeded['c']}", f"q{seeded['two']}")
    assert code == 0 and out == {"moved": f"s{seeded['c']}", "into": f"q{seeded['two']}"}
    code, out, _ = _run(capsys, "questions", "move", f"s{seeded['a']}", "--new", "Brand new?",
                        "--measure", "N", "--kind", "level")
    assert code == 0 and out["moved"] == f"s{seeded['a']}" and out["created"] == out["into"]
    conn = store.connect(config.load().db_path)
    got = store.get_study(conn, seeded["a"])
    assert (got["question_by_owner"], got["question_direction"]) == (1, None)
    assert store.get_question(conn, got["question_id"])["text"] == "Brand new?"
    conn.close()
    assert _run(capsys, "questions", "move", f"s{seeded['a']}")[0] == 2           # neither question nor --new
    assert _run(capsys, "questions", "move", f"s{seeded['a']}", "--new", "T", "--kind", "level")[0] == 2   # no --measure
    assert _run(capsys, "questions", "move", "s9999", "--new", "T", "--measure", "M", "--kind", "level")[0] == 2


def test_ids_with_or_without_prefix(seeded, capsys):
    plain = _run(capsys, "questions", "rename", str(seeded["one"]), "Plain id?")
    assert plain[0] == 0 and plain[1]["renamed"] == f"q{seeded['one']}"
    moved = _run(capsys, "questions", "move", str(seeded["c"]), str(seeded["two"]))
    assert moved[0] == 0 and moved[1]["moved"] == f"s{seeded['c']}"
    code, _, err = _run(capsys, "questions", "rename", "banana", "x")
    assert code == 2 and "q12" in err


def test_move_new_requires_kind(seeded, capsys):
    code, _, err = _run(capsys, "questions", "move", f"s{seeded['a']}", "--new", "T", "--measure", "M")
    assert code == 2 and "--kind" in err


def test_move_new_difference_requires_comparison(seeded, capsys):
    code, _, err = _run(capsys, "questions", "move", f"s{seeded['a']}", "--new", "T", "--measure", "M",
                        "--kind", "difference")
    assert code == 2 and "--comparison" in err
    code, _, err = _run(capsys, "questions", "move", f"s{seeded['a']}", "--new", "T", "--measure", "M",
                        "--kind", "level", "--comparison", "a vs b")
    assert code == 2 and "comparison" in err
    code, out, _ = _run(capsys, "questions", "move", f"s{seeded['a']}", "--new", "T", "--measure", "M",
                        "--kind", "difference", "--comparison", "a vs b")
    assert code == 0 and out["created"] == out["into"]


def test_questions_list_shows_kind(seeded, capsys):
    rows = {r["id"]: r for r in _run(capsys, "questions", "list")[1]["questions"]}
    one = rows[f"q{seeded['one']}"]
    assert (one["kind"], one["comparison"]) == ("difference", "with X vs without X")


def test_rename_kind_and_comparison_via_cli(seeded, capsys):
    qid = f"q{seeded['one']}"
    code, out, _ = _run(capsys, "questions", "rename", qid, "How much Y?", "--kind", "level")
    assert code == 0 and out["kind"] == "level" and out["comparison"] is None and out["directions_cleared"] == 2
    assert _run(capsys, "questions", "rename", qid, "x", "--kind", "bogus")[0] == 2
