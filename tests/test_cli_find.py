import json
from contextlib import closing

from evidence_echo import cli, query
from evidence_echo.core import config, store
from tests.evidence_fixtures import add_study, seed_topic
from tests.test_read_paths import snapshot


def seeded(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for topic_id in ("seo", "cro"):
        with closing(store.connect(seed_topic(data, topic_id))) as conn:
            add_study(conn, f"Title tag clicks in {topic_id}", "title tag clicks rose")
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    return data


def test_cli_find_prints_find_evidence(tmp_path, monkeypatch, capsys):
    seeded(tmp_path, monkeypatch)
    assert cli.main(["find", "title tag clicks"]) == 0
    assert json.loads(capsys.readouterr().out) == query.find_evidence(config.read_paths(), "title tag clicks")


def test_cli_find_topic_either_position(tmp_path, monkeypatch, capsys):
    seeded(tmp_path, monkeypatch)
    for argv in (["--topic", "seo", "find", "title tag clicks"], ["find", "title tag clicks", "--topic", "seo"]):
        assert cli.main(argv) == 0
        assert json.loads(capsys.readouterr().out)["topics_searched"] == ["seo"]


def test_cli_find_unknown_topic_exits_2(tmp_path, monkeypatch, capsys):
    seeded(tmp_path, monkeypatch)
    assert cli.main(["find", "x", "--topic", "nope"]) == 2


def test_cli_find_creates_nothing(tmp_path, monkeypatch, capsys):
    data = seeded(tmp_path, monkeypatch)
    before = snapshot(data)
    assert cli.main(["find", "title tag clicks", "--limit", "2"]) == 0
    assert snapshot(data) == before


def test_cli_find_not_set_up_exits_4(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "nothing"))
    assert cli.main(["find", "title tag clicks"]) == 4
    assert not (tmp_path / "nothing").exists()
