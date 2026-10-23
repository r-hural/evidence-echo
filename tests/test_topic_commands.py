import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, topics
from evidence_echo.core.settings import ConfigError
from evidence_echo.sources import rss


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    return config.workspace()


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out else None), out.err


def test_add_from_template_and_list(ws, capsys):
    code, out, _ = _run(capsys, "topic", "add", "seo", "--from", "seo")
    assert code == 0 and out["id"] == "seo" and out["template"] == "seo"
    count = len(topics.load_template("seo", config.TEMPLATES_DIR).sources)
    code, out, _ = _run(capsys, "topic", "list")
    assert out == [{"id": "seo", "name": "SEO Echo", "sources": count, "last_run": None}]


def test_add_blank_needs_name_domain_scope(ws, capsys):
    code, _, err = _run(capsys, "topic", "add", "diet", "--blank", "--name", "Diet")
    assert code == 2 and "--domain" in err
    code, out, _ = _run(capsys, "topic", "add", "diet", "--blank", "--name", "Diet",
                        "--domain", "nutrition research", "--scope", "Weight loss studies.")
    assert code == 0 and out["sources"] == [] and out["template"] == "blank"


@pytest.mark.parametrize("bad", ["Diet", "a_b", "-x"])
def test_add_rejects_bad_id(ws, capsys, bad):
    code, _, err = _run(capsys, "topic", "add", "--from", "seo", "--", bad)
    assert code == 2 and "lowercase" in err


def test_add_existing_id_refused(ws, capsys):
    _run(capsys, "topic", "add", "seo", "--from", "seo")
    code, _, err = _run(capsys, "topic", "add", "seo", "--from", "seo")
    assert code == 2 and "already exists" in err


def test_set_fields_and_id_refused(ws, capsys):
    _run(capsys, "topic", "add", "seo", "--from", "seo")
    assert _run(capsys, "topic", "set", "seo", "name", "My SEO")[1]["name"] == "My SEO"
    assert _run(capsys, "topic", "set", "seo", "subtopics", '["A","B"]')[1]["subtopics"] == ["A", "B"]
    assert _run(capsys, "topic", "set", "seo", "fetch_horizon_days", "14")[1]["fetch_horizon_days"] == 14
    assert _run(capsys, "topic", "set", "seo", "fetch_horizon_days", "null")[1]["fetch_horizon_days"] is None
    code, _, err = _run(capsys, "topic", "set", "seo", "id", "x")
    assert code == 2 and "cannot be changed" in err
    code, _, err = _run(capsys, "topic", "set", "seo", "fetch_horizon_days", "4000")
    assert code == 2 and "fetch_horizon_days" in err


def test_remove_keeps_data_unless_asked(ws, capsys):
    _run(capsys, "topic", "add", "seo", "--from", "seo")
    (ws.topics_dir / "seo" / "echo.db").write_bytes(b"db")
    out = _run(capsys, "topic", "remove", "seo")[1]
    moved = ws.topics_dir / out["moved_to"]
    assert (moved / "echo.db").exists() and topics.topic_ids(ws.topics_dir) == []
    _run(capsys, "topic", "add", "seo", "--from", "seo")
    out = _run(capsys, "topic", "remove", "seo", "--delete-data")[1]
    assert out["deleted"] is True and not (ws.topics_dir / "seo").exists()


def test_sources_add_discovers_and_reports_check(ws, capsys, monkeypatch):
    _run(capsys, "topic", "add", "diet", "--blank", "--name", "Diet", "--domain", "d", "--scope", "s")
    monkeypatch.setattr(rss, "discover_feed", lambda url: {
        "url": "https://s.example/feed", "discovered_from": url,
        "check": {"status": "ok", "entries": 3}})
    code, out, _ = _run(capsys, "sources", "add", "rss", "Site", "https://s.example/")
    assert code == 0
    assert out["source"] == {"type": "rss", "name": "Site", "url": "https://s.example/feed"}
    assert out["discovered_from"] == "https://s.example/" and out["check"]["status"] == "ok"
    listed = _run(capsys, "sources", "list")[1]
    assert [{k: r[k] for k in out["source"]} for r in listed] == [out["source"]]  # list adds state fields


def test_sources_add_failing_check_still_adds_with_warning(ws, capsys, monkeypatch):
    _run(capsys, "topic", "add", "diet", "--blank", "--name", "Diet", "--domain", "d", "--scope", "s")
    monkeypatch.setattr(rss, "discover_feed", lambda url: {
        "url": url, "discovered_from": None, "check": {"status": "http_error", "entries": 0}})
    out = _run(capsys, "sources", "add", "rss", "Bad", "https://bad.example/feed")[1]
    assert out["warning"] == "the feed check failed (http_error); it was added anyway"
    assert len(_run(capsys, "sources", "list")[1]) == 1


def test_sources_add_linkedin_and_duplicate_and_remove(ws, capsys):
    _run(capsys, "topic", "add", "diet", "--blank", "--name", "Diet", "--domain", "d", "--scope", "s")
    out = _run(capsys, "sources", "add", "linkedin", "Jane", "jane-doe")[1]
    assert out["source"] == {"type": "linkedin", "name": "Jane", "slug": "jane-doe"}
    code, _, err = _run(capsys, "sources", "add", "linkedin", "Jane", "other")
    assert code == 2 and "duplicate source name 'Jane'" in err
    assert _run(capsys, "sources", "remove", "Jane")[1]["removed"] == "Jane"
    code, _, err = _run(capsys, "sources", "remove", "Jane")
    assert code == 2 and "no source named 'Jane'" in err


def test_sources_commands_need_topic_choice_with_several(ws, capsys):
    _run(capsys, "topic", "add", "a", "--blank", "--name", "A", "--domain", "d", "--scope", "s")
    _run(capsys, "topic", "add", "b", "--blank", "--name", "B", "--domain", "d", "--scope", "s")
    code, _, err = _run(capsys, "sources", "list")
    assert code == 2 and "several topics" in err
    assert _run(capsys, "--topic", "b", "sources", "list")[0] == 0


@pytest.mark.parametrize("target", ["file:///etc/passwd", "ftp://x.example/feed", "/local/feed.xml"])
def test_sources_add_rejects_non_http_url_before_discovery(ws, capsys, monkeypatch, target):
    _run(capsys, "topic", "add", "diet", "--blank", "--name", "Diet", "--domain", "d", "--scope", "s")

    def boom(url):
        raise AssertionError("discover_feed must not be called")

    monkeypatch.setattr(rss, "discover_feed", boom)
    code, _, err = _run(capsys, "sources", "add", "rss", "Local", target)
    assert code == 2 and "must start with http:// or https://" in err and target in err
    assert _run(capsys, "sources", "list")[1] == []


def test_sources_add_rejects_non_http_discovered_url(ws, capsys, monkeypatch):
    _run(capsys, "topic", "add", "diet", "--blank", "--name", "Diet", "--domain", "d", "--scope", "s")
    monkeypatch.setattr(rss, "discover_feed", lambda url: {
        "url": "file:///etc/passwd", "discovered_from": url, "check": {"status": "ok", "entries": 1}})
    code, _, err = _run(capsys, "sources", "add", "rss", "Site", "https://s.example/")
    assert code == 2 and "http://" in err
    assert _run(capsys, "sources", "list")[1] == []


def test_topic_list_survives_a_broken_topic(ws, capsys):
    _run(capsys, "topic", "add", "good", "--from", "seo")
    _run(capsys, "topic", "add", "bad", "--from", "seo")
    (ws.topics_dir / "bad" / "topic.json").write_text('{"id": "bad", "name": 5}', encoding="utf-8")
    code, out, _ = _run(capsys, "topic", "list")
    assert code == 0 and [t["id"] for t in out] == ["bad", "good"]
    bad, good = out
    assert bad["name"] == "bad" and bad["sources"] == 0 and "topic.json" in bad["error"]
    assert "error" not in good
