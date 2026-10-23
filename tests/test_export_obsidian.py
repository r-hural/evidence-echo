import json
from pathlib import Path

from evidence_echo import api, cli
from evidence_echo.core import config, store
from tests.conftest import make_workspace, seed_topic_study


def _vault(tmp_path, monkeypatch) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("EV_ECHO_OBSIDIAN_DIR", str(vault))
    return vault


def _two_topics(tmp_path, monkeypatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    return make_workspace(data, ("diet", "seo"))


def _reported_study(db_path: Path, title: str = "Clicks rose 4%") -> int:
    conn = store.connect(db_path)
    sid = store.create_study(conn, "B", {"what_was_tested": "x"}, None)
    conn.commit()
    store.record_report(conn, "/r/1.md", [(sid, title)])
    conn.close()
    return sid


def _run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out else None), captured.err


def test_export_needs_obsidian_dir(workspace, monkeypatch, capsys):
    monkeypatch.delenv("EV_ECHO_OBSIDIAN_DIR", raising=False)
    code, out, err = _run(capsys, "export", "obsidian")
    assert code == 2 and out is None and "obsidian_dir is not set" in err


def test_export_exits_4_when_not_set_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    code, out, err = _run(capsys, "export", "obsidian")
    assert code == 4 and "not set up" in err


def test_export_one_topic(workspace, tmp_path, monkeypatch, capsys):
    vault = _vault(tmp_path, monkeypatch)
    sid = _reported_study(config.load().db_path)
    code, out, _ = _run(capsys, "export", "obsidian")
    assert code == 0
    [row] = out["topics"]
    assert row["topic"] == "seo" and row["written"] == 2 and row["removed"] == 0
    assert Path(row["dir"]).samefile(vault / "Evidence Echo" / "SEO")
    assert Path(out["obsidian_dir"]).samefile(vault)
    assert len(list((vault / "Evidence Echo" / "SEO" / "studies").glob(f"*-s{sid}.md"))) == 1


def test_export_all_topics_skips_topics_without_data(tmp_path, monkeypatch, capsys):
    data = _two_topics(tmp_path, monkeypatch)
    _vault(tmp_path, monkeypatch)
    _reported_study(data / "topics" / "seo" / "echo.db")
    code, out, _ = _run(capsys, "export", "obsidian")
    assert code == 0
    rows = {r["topic"]: r for r in out["topics"]}
    assert rows["diet"] == {"topic": "diet", "skipped": "no data yet"}
    assert rows["seo"]["written"] == 2
    assert not (data / "topics" / "diet" / "echo.db").exists()  # the export created no database


def test_topic_option_after_or_before_the_command(tmp_path, monkeypatch, capsys):
    data = _two_topics(tmp_path, monkeypatch)
    _vault(tmp_path, monkeypatch)
    _reported_study(data / "topics" / "seo" / "echo.db")
    for argv in (["export", "obsidian", "--topic", "seo"], ["--topic", "seo", "export", "obsidian"]):
        code, out, _ = _run(capsys, *argv)
        assert code == 0 and [r["topic"] for r in out["topics"]] == ["seo"]


def test_ev_echo_topic_selects_one_topic(tmp_path, monkeypatch, capsys):
    data = _two_topics(tmp_path, monkeypatch)
    _vault(tmp_path, monkeypatch)
    _reported_study(data / "topics" / "seo" / "echo.db")
    monkeypatch.setenv("EV_ECHO_TOPIC", "seo")
    code, out, _ = _run(capsys, "export", "obsidian")
    assert code == 0 and [r["topic"] for r in out["topics"]] == ["seo"]


def test_unknown_topic_exits_2(workspace, tmp_path, monkeypatch, capsys):
    _vault(tmp_path, monkeypatch)
    code, _, err = _run(capsys, "export", "obsidian", "--topic", "nope")
    assert code == 2 and "Unknown topic 'nope'" in err


def test_broken_topic_does_not_hide_the_others(tmp_path, monkeypatch, capsys):
    data = _two_topics(tmp_path, monkeypatch)
    _vault(tmp_path, monkeypatch)
    _reported_study(data / "topics" / "seo" / "echo.db")
    (data / "topics" / "diet" / "echo.db").write_bytes(b"this is not a database" * 50)
    code, out, _ = _run(capsys, "export", "obsidian")
    rows = {r["topic"]: r for r in out["topics"]}
    assert code == 0 and "not a database" in rows["diet"]["error"] and rows["seo"]["written"] == 2
    code, out, err = _run(capsys, "export", "obsidian", "--topic", "diet")
    assert code == 1 and out is None and "not a database" in err


def test_render_report_exports_to_obsidian(workspace, tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    seed_topic_study()   # a report needs a new study
    out = api.render_report(config.load(), '{"results": []}')
    assert out["obsidian"]["written"] == 4  # _index.md plus the notes of the one reported study
    assert (vault / "Evidence Echo" / "SEO" / "_index.md").exists()
