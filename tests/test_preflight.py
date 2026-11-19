import json
from dataclasses import replace

from evidence_echo import api, cli
from evidence_echo.core import config, topics
from tests.conftest import make_workspace


def _set_sources(tid, sources):
    ws = config.workspace()
    t = topics.load_topic(ws.topics_dir, tid)
    topics.save_topic(ws.topics_dir, replace(t, sources=sources))


def _linkedin(n):
    return [{"type": "linkedin", "name": f"Person {i}", "slug": f"p{i}"} for i in range(n)]


def test_preflight_lists_missing_key(workspace):
    _set_sources("seo", _linkedin(3))
    out = api.preflight(config.workspace(), "seo")
    groups = out["topics"]["seo"]
    assert [g["reason"] for g in groups] == ["Apify token missing (plugin settings)"]
    assert len(groups[0]["sources"]) == 3
    assert out["problems"] == 1


def test_preflight_all_topics(workspace):
    make_workspace(workspace, ("seo", "diet"))
    _set_sources("seo", _linkedin(2))
    _set_sources("diet", [])
    out = api.preflight(config.workspace(), None)
    assert set(out["topics"]) == {"seo", "diet"}
    assert out["topics"]["diet"] == [] and len(out["topics"]["seo"]) == 1


def test_cli_preflight_json(workspace, capsys):
    _set_sources("seo", _linkedin(1))
    assert cli.main(["--topic", "seo", "preflight"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["problems"] == 1 and "seo" in data["topics"]
