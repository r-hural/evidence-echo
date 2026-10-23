"""ev entities ... and ev studies entity ...: each command's JSON result and its usage errors."""
import json

from evidence_echo import api, cli
from evidence_echo.core import config, store


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def _seed(names_a=("Shopify", "ChatGPT"), names_b=("ChatGPT",)):
    with api._topic_db(config.load()) as conn:
        a = store.create_study(conn, "B", {"what_was_tested": "a"}, None, entity_names=list(names_a))
        b = store.create_study(conn, "B", {"what_was_tested": "b"}, None, entity_names=list(names_b))
    return a, b


def test_entities_commands_round_trip(workspace, capsys):
    _seed()
    code, out, _ = _run(capsys, "entities", "list")
    assert code == 0
    assert [(e["slug"], e["studies"]) for e in out["entities"]] == [("chatgpt", 2), ("shopify", 1)]
    assert _run(capsys, "entities", "rename", "shopify", "Shopify Plus")[1] == {
        "renamed": "shopify", "name": "Shopify Plus"}
    assert _run(capsys, "entities", "alias", "chatgpt", "GPT-4o")[1] == {
        "alias": "gpt-4o", "name": "GPT-4o", "of": "chatgpt"}
    assert _run(capsys, "entities", "merge", "shopify", "chatgpt")[1] == {
        "merged": "shopify", "into": "chatgpt", "moved_studies": 0}
    rows = {e["slug"]: e for e in _run(capsys, "entities", "list")[1]["entities"]}
    assert rows["shopify"]["merged_into"] == "chatgpt" and rows["gpt-4o"]["merged_into"] == "chatgpt"
    assert _run(capsys, "entities", "reject", "chatgpt")[1] == {"rejected": "chatgpt", "removed_links": 2}
    assert _run(capsys, "entities", "restore", "chatgpt")[1] == {"restored": "chatgpt"}


def test_studies_entity_add_and_remove(workspace, capsys):
    a, _ = _seed()
    assert _run(capsys, "studies", "entity", f"s{a}", "add", "Gemini")[1] == {
        "study": a, "entity": "gemini", "name": "Gemini"}
    assert _run(capsys, "studies", "entity", str(a), "remove", "shopify")[1] == {"study": a, "removed": "shopify"}
    with api._topic_db(config.load()) as conn:
        assert [e["slug"] for e in store.study_entities(conn, a)] == ["chatgpt", "gemini"]


def test_usage_errors_exit_2(workspace, capsys):
    a, _ = _seed()
    for argv, text in ((("entities", "rename", "nope", "X"), "no entity 'nope'"),
                       (("entities", "merge", "shopify", "shopify"), "cannot merge an entity with itself"),
                       (("entities", "restore", "shopify"), "not a rejected entity"),
                       (("studies", "entity", "s999", "add", "X"), "no active study"),
                       (("studies", "entity", "xyz", "add", "X"), "the study id"),
                       (("studies", "entity", str(a), "remove", "wix"), "no entity 'wix'")):
        code, out, err = _run(capsys, *argv)
        assert code == 2 and out is None and text in err, (argv, err)
    _run(capsys, "entities", "reject", "shopify")
    code, _, err = _run(capsys, "studies", "entity", str(a), "add", "Shopify")
    assert code == 2 and "restore it first" in err


def test_entities_needs_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    assert _run(capsys, "entities", "list")[0] == 4
