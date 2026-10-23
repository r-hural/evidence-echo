"""SP3b-T: ev subtopics - list (seeds the topic's subtopics), accept, map, reject, rename, merge."""
import json

from evidence_echo import cli
from evidence_echo.core import config, store


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_subtopics_list_seeds_the_topic_subtopics(workspace, capsys):
    code, out, _ = _run(capsys, "subtopics", "list")
    assert code == 0 and out["proposals"] == 0 and len(out["subtopics"]) == 15
    assert out["subtopics"][0] == {"slug": "title-tags-meta", "name": "Title tags & meta",
                                "status": "seeded", "merged_into": None, "studies": 0, "examples": [], "split": None}


def test_subtopics_review_commands(workspace, capsys):
    cfg = config.load()
    conn = store.connect(cfg.db_path)
    store.seed_subtopics(conn, cfg.topic.subtopics)
    for name in ("Image SEO", "Link building", "Guest posts"):
        store.subtopic_for_choice(conn, {"propose": name}, create=True)
    conn.close()
    assert _run(capsys, "subtopics", "list")[1]["proposals"] == 3
    assert _run(capsys, "subtopics", "accept", "image-seo")[1] == {"accepted": "image-seo"}
    assert _run(capsys, "subtopics", "map", "link-building", "backlinks")[1]["into"] == "backlinks"
    assert _run(capsys, "subtopics", "reject", "guest-posts")[1]["rejected"] == "guest-posts"
    assert _run(capsys, "subtopics", "rename", "local", "Local SEO")[1] == {"renamed": "local", "name": "Local SEO"}
    assert _run(capsys, "subtopics", "merge", "internal-links", "backlinks")[1]["merged"] == "internal-links"
    out = _run(capsys, "subtopics", "list")[1]
    assert out["proposals"] == 0
    assert {t["slug"]: t["merged_into"] for t in out["subtopics"]}["internal-links"] == "backlinks"


def test_subtopics_refusal_exits_2(workspace, capsys):
    code, out, err = _run(capsys, "subtopics", "accept", "nope")
    assert code == 2 and out is None and "no subtopic 'nope'" in err


def test_subtopics_needs_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path))
    assert _run(capsys, "subtopics", "list")[0] == 4
