"""Metric lists: topic.json and template fields, seeding on every topic connection, ev metrics,
ev studies metric, and the topic.json list helpers the split decisions use."""
import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, store, topics
from evidence_echo.core.settings import ConfigError
from tests.conftest import PLUGIN_DIR

TEMPLATES = PLUGIN_DIR / "templates"
BASE = {"id": "x", "name": "X", "domain": "X research", "scope": "Only X.", "sources": []}
SEO_METRICS = ["Clicks", "Impressions", "Rankings", "Organic traffic", "CTR", "Conversions & revenue",
               "Crawling & indexing", "Engagement", "AI citations", "AI share of voice & mentions",
               "AI Overview presence", "AI referral traffic", "Other"]
SEO_LEGACY = {"clicks": "Clicks", "impressions": "Impressions", "rankings": "Rankings",
              "traffic": "Organic traffic", "ctr": "CTR", "conversions": "Conversions & revenue",
              "revenue": "Conversions & revenue", "crawl_index": "Crawling & indexing",
              "engagement": "Engagement"}


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def _topic_json(workspace, topic_id="seo"):
    return json.loads((workspace / "topics" / topic_id / "topic.json").read_text(encoding="utf-8"))


def _drop_metrics_key(workspace, topic_id="seo"):
    path = workspace / "topics" / topic_id / "topic.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("metrics", None)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_seo_template_metrics_and_legacy():
    t = topics.load_template("seo", TEMPLATES)
    assert t.metrics == SEO_METRICS
    names, legacy = topics.template_metrics("seo", TEMPLATES)
    assert names == SEO_METRICS and legacy == SEO_LEGACY
    assert "other" not in legacy
    assert len(topics.load_template("seo", TEMPLATES).subtopics) == 15      # unchanged


def test_template_legacy_value_must_be_listed():
    data = dict(BASE, metrics=["A", "Other"], metric_legacy={"a": "A"})
    assert topics.validate(data, "t.json", template=True).metrics == ["A", "Other"]
    with pytest.raises(ConfigError, match="metric_legacy"):
        topics.validate(dict(data, metric_legacy={"a": "Missing"}), "t.json", template=True)
    with pytest.raises(ConfigError, match="unknown field 'metric_legacy'"):
        topics.validate(data, "topic.json")                                  # template-only
    with pytest.raises(ConfigError, match="metrics"):
        topics.validate(dict(BASE, metrics="Clicks"), "topic.json")
    with pytest.raises(ConfigError, match="metrics"):
        topics.validate(dict(BASE, metrics=["A", 3]), "topic.json")


def test_unknown_or_blank_template_has_no_metrics():
    assert topics.template_metrics("blank", TEMPLATES) == ([], {})
    assert topics.template_metrics("nope", TEMPLATES) == ([], {})
    assert topics.validate(BASE, "f", "x").metrics == []


def test_topic_metrics_default_from_template(tmp_path):
    topics_dir = tmp_path / "topics"
    seo = topics.load_template("seo", TEMPLATES)
    data = topics.to_dict(seo)
    data.pop("metrics")                                   # a topic written before metrics existed
    data["id"] = "old"
    (topics_dir / "old").mkdir(parents=True)
    (topics_dir / "old" / "topic.json").write_text(json.dumps(data), encoding="utf-8")
    topic = topics.load_topic(topics_dir, "old")
    assert topic.metrics == SEO_METRICS and topic.metrics_in_file is False
    assert "metrics" not in topics.to_dict(topic)         # reading never writes the default
    topics.set_field(topics_dir, "old", "scope", "narrower")
    assert "metrics" not in json.loads((topics_dir / "old" / "topic.json").read_text(encoding="utf-8"))
    own = dict(data, id="own", metrics=["Mine", "Other"])
    (topics_dir / "own").mkdir()
    (topics_dir / "own" / "topic.json").write_text(json.dumps(own), encoding="utf-8")
    assert topics.load_topic(topics_dir, "own").metrics == ["Mine", "Other"]


def test_topic_add_from_template_writes_metrics(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    config.workspace()
    code, out, _ = _run(capsys, "topic", "add", "seo", "--from", "seo")
    assert code == 0 and out["metrics"] == SEO_METRICS and "metrics_in_file" not in out
    assert _topic_json(tmp_path)["metrics"] == SEO_METRICS
    code, out, _ = _run(capsys, "topic", "add", "blank1", "--blank", "--name", "B", "--domain", "d",
                        "--scope", "s")
    assert code == 0 and "metrics" not in out
    assert "metrics" not in _topic_json(tmp_path, "blank1")


def test_topic_set_metrics(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    config.workspace()
    _run(capsys, "topic", "add", "seo", "--from", "seo")
    code, out, _ = _run(capsys, "topic", "set", "seo", "metrics", '["Clicks", "Other"]')
    assert code == 0 and out["metrics"] == ["Clicks", "Other"]
    assert _topic_json(tmp_path)["metrics"] == ["Clicks", "Other"]
    code, _, err = _run(capsys, "topic", "set", "seo", "metrics", "not json")
    assert code == 2 and "metrics must be a JSON list" in err
    code, _, err = _run(capsys, "topic", "set", "seo", "metrics", '[1]')
    assert code == 2


def test_topic_db_seeds_metrics(workspace):
    _drop_metrics_key(workspace)
    cfg = config.load()
    assert cfg.topic.metrics == SEO_METRICS
    with api._topic_db(cfg) as conn:
        active = store.active_metrics(conn)
    assert [m["name"] for m in active] == SEO_METRICS and active[-1]["slug"] == "other"
    assert len(active) == 13


def test_topic_db_legacy_fill(workspace):
    cfg = config.load()
    with api._connect(cfg) as conn:
        sid = store.create_study(conn, "B", {"what_was_tested": "x"}, None, metric="traffic")
        conn.execute("UPDATE studies SET metric_code='traffic', metric_id=NULL WHERE id=?", (sid,))
        conn.commit()
    with api._topic_db(cfg) as conn:
        assert store.study_metric(conn, store.get_study(conn, sid))["name"] == "Organic traffic"


def test_metrics_cli_roundtrip(workspace, capsys):
    code, out, _ = _run(capsys, "metrics", "list")
    assert code == 0 and out["proposals"] == 0
    assert [m["name"] for m in out["metrics"]] == SEO_METRICS
    assert out["metrics"][0] == {"slug": "clicks", "name": "Clicks", "status": "seeded", "merged_into": None,
                                 "studies": 0, "examples": [], "split": None}
    assert _run(capsys, "metrics", "rename", "ctr", "Click-through rate")[1] == {
        "renamed": "ctr", "name": "Click-through rate"}
    assert _run(capsys, "metrics", "merge", "impressions", "clicks")[1]["merged"] == "impressions"
    rows = {m["slug"]: m for m in _run(capsys, "metrics", "list")[1]["metrics"]}
    assert rows["impressions"]["merged_into"] == "clicks"
    cfg = config.load()
    with api._topic_db(cfg) as conn:
        store._insert_metric(conn, "Visits", "proposed")
        store._insert_metric(conn, "Hits", "proposed")
        conn.commit()
    assert _run(capsys, "metrics", "accept", "visits")[1] == {"accepted": "visits"}
    assert _run(capsys, "metrics", "reject", "hits")[1]["rejected"] == "hits"
    code, out, err = _run(capsys, "metrics", "reject", "other")
    assert code == 2 and out is None and '"Other" cannot be rejected' in err
    code, _, err = _run(capsys, "metrics", "merge", "other", "clicks")
    assert code == 2 and '"Other" cannot be merged' in err
    code, _, err = _run(capsys, "metrics", "accept", "nope")
    assert code == 2 and "no metric 'nope'" in err


def test_metrics_needs_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    assert _run(capsys, "metrics", "list")[0] == 4


def test_studies_metric_cli(workspace, capsys):
    cfg = config.load()
    with api._topic_db(cfg) as conn:
        sid = store.create_study(conn, "B", {"what_was_tested": "x"}, None)
    assert _run(capsys, "studies", "metric", f"s{sid}", "rankings")[1] == {"study": sid, "metric": "rankings"}
    assert _run(capsys, "studies", "metric", str(sid), "ctr")[1] == {"study": sid, "metric": "ctr"}
    code, _, err = _run(capsys, "studies", "metric", str(sid), "nope")
    assert code == 2 and "not an active metric" in err
    code, _, err = _run(capsys, "studies", "metric", "abc", "ctr")
    assert code == 2 and "study id" in err
    code, _, err = _run(capsys, "studies", "metric", "999", "ctr")
    assert code == 2 and "no active study" in err


def test_topic_metric_insert_after_source(workspace):
    cfg = config.load()
    subs = cfg.topic.subtopics
    api.topic_metric_insert(cfg, "subtopic", "AI citations & GEO", after="Backlinks")
    now = _topic_json(workspace)["subtopics"]
    assert now.index("AI citations & GEO") == now.index("Backlinks") + 1 and len(now) == len(subs) + 1
    assert now[-1] == "Other"
    api.topic_metric_insert(cfg, "subtopic", "Tail", after=None)               # appended before Other
    now = _topic_json(workspace)["subtopics"]
    assert now[-2:] == ["Tail", "Other"]
    api.topic_metric_insert(config.load(), "subtopic", "tail", after="Backlinks")   # already there: no change
    assert _topic_json(workspace)["subtopics"] == now
    # metrics: before Other, whatever `after` says
    api.topic_metric_insert(cfg, "metric", "PRs merged", after="Clicks")
    metrics = _topic_json(workspace)["metrics"]
    assert metrics[-2:] == ["PRs merged", "Other"] and metrics[:-2] == SEO_METRICS[:-1]


def test_topic_metric_insert_writes_key_from_default(workspace):
    _drop_metrics_key(workspace)
    cfg = config.load()
    assert "metrics" not in _topic_json(workspace)
    api.topic_metric_insert(cfg, "metric", "New one", after=None)
    assert _topic_json(workspace)["metrics"] == SEO_METRICS[:-1] + ["New one", "Other"]


def test_topic_list_remove(workspace):
    cfg = config.load()
    api.topic_list_remove(cfg, "subtopic", "backlinks")                     # name match ignores case
    assert "Backlinks" not in _topic_json(workspace)["subtopics"]
    api.topic_list_remove(config.load(), "subtopic", "Not there")           # nothing to remove
    api.topic_list_remove(cfg, "metric", "CTR")
    assert "CTR" not in _topic_json(workspace)["metrics"]
    with pytest.raises(ValueError):
        api.topic_list_remove(cfg, "entity", "x")
