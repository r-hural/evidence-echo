# tests/test_primary_api.py
"""SP3b-P: ev primary and the two-file apply through the API and the CLI. Network faked: the
API and CLI build their own fetch context, so base.make_client returns a MockTransport client."""
import json

import pytest

from evidence_echo import api, cli
from evidence_echo.core import config, interfaces, llm_api, store
from evidence_echo.sources import base
from tests.conftest import FakeBackend
from tests.primary_fixtures import ORIGINAL_URL, fake_ctx, original_html, report_item
from tests.test_primary_compare import answer


@pytest.fixture
def calls(monkeypatch):
    seen = []
    ctx = fake_ctx({ORIGINAL_URL: original_html()}, seen)
    monkeypatch.setattr(base, "make_client", lambda: ctx.client)
    return seen


def _seed(cfg) -> str:
    conn = store.connect(cfg.db_path)
    try:
        return interfaces.opaque_id(report_item(conn))
    finally:
        conn.close()


def _reply(oid) -> str:
    return json.dumps({k: v for k, v in answer(oid).items() if k != "id"})


def _api_mode(monkeypatch):
    monkeypatch.setenv("EV_ECHO_JUDGMENT_BACKEND", "api")
    monkeypatch.setenv("EV_ECHO_ANTHROPIC_API_KEY", "test-key")


def test_primary_is_registered_as_a_two_answer_llm_stage():
    assert "primary" in api.TWO_ANSWER_STAGES and "primary" in api.LLM_STAGES


def test_session_mode_fetches_then_emits(workspace, calls):
    cfg = config.load()
    oid = _seed(cfg)
    assert cfg.judgment_backend == "session" and cfg.primary_check == "on"
    payload = json.loads(api.emit(cfg, "primary"))
    assert payload["stage"] == "primary" and [i["id"] for i in payload["items"]] == [oid]
    assert payload["originals"]["fetched"] == 1 and calls == [ORIGINAL_URL]


def test_off_fetches_nothing(workspace, calls, monkeypatch):
    monkeypatch.setenv("EV_ECHO_PRIMARY_CHECK", "off")
    cfg = config.load()
    _seed(cfg)
    payload = json.loads(api.emit(cfg, "primary"))
    assert payload["items"] == [] and payload["skipped"] == "primary_check is off"
    assert calls == []


def test_api_mode_asks_twice(workspace, calls, monkeypatch):
    _api_mode(monkeypatch)
    cfg = config.load()
    oid = _seed(cfg)
    backend = FakeBackend([_reply(oid), _reply(oid)])
    monkeypatch.setattr(llm_api, "AnthropicBackend", lambda key: backend)
    out = api.run_api(cfg, "primary")
    assert (out["processed"], out["agreed"], out["rejected"]) == (1, 1, [])
    assert out["originals"]["fetched"] == 1 and len(backend.prompts) == 2


def test_api_mode_off_asks_nothing(workspace, calls, monkeypatch):
    _api_mode(monkeypatch)
    monkeypatch.setenv("EV_ECHO_PRIMARY_CHECK", "off")
    cfg = config.load()
    _seed(cfg)

    def no_backend(key):
        raise AssertionError("no backend when primary_check is off")
    monkeypatch.setattr(llm_api, "AnthropicBackend", no_backend)
    assert api.run_api(cfg, "primary") == {"processed": 0, "agreed": 0, "undecided": 0,
                                           "rejected": [], "skipped": "primary_check is off"}
    assert calls == []


def test_api_apply_takes_both_files(workspace, calls):
    cfg = config.load()
    oid = _seed(cfg)
    api.emit(cfg, "primary")
    one = json.dumps({"results": [answer(oid)]})
    out = api.apply(cfg, "primary", one, payload_b=one)
    assert (out["applied"], out["agreed"]) == (1, 1)


def test_cli_primary_then_two_file_apply(workspace, calls, tmp_path, capsys):
    oid = _seed(config.load())
    assert cli.main(["primary"]) == 0                     # no API key: session mode
    payload = json.loads(capsys.readouterr().out)
    assert [i["id"] for i in payload["items"]] == [oid]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for path in (a, b):
        path.write_text(json.dumps({"results": [answer(oid)]}), encoding="utf-8")
    assert cli.main(["apply", "--stage", "primary", str(a), str(b)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["applied"], out["agreed"]) == (1, 1)


def test_cli_primary_emit_flag_in_api_mode(workspace, calls, capsys, monkeypatch):
    _api_mode(monkeypatch)
    _seed(config.load())
    assert cli.main(["primary", "--emit"]) == 0
    assert len(json.loads(capsys.readouterr().out)["items"]) == 1


def test_cli_apply_primary_needs_two_files(workspace, tmp_path):
    a = tmp_path / "a.json"
    a.write_text('{"results": []}', encoding="utf-8")
    assert cli.main(["apply", "--stage", "primary", str(a)]) == 2


def test_off_still_restores_a_dropped_red_flag(workspace, calls, monkeypatch):
    cfg = config.load()
    oid = _seed(cfg)
    api.emit(cfg, "primary")
    both = json.dumps({"results": [dict(json.loads(_reply(oid)), id=oid)]})
    api.apply(cfg, "primary", both, payload_b=both)
    rowid = interfaces.rowid_from_opaque(oid)

    def flags():
        with api._connect(cfg) as conn:
            return json.loads(store.get_item(conn, rowid)["red_flags"])
    assert flags() == ["misrepresented_primary"]
    with api._connect(cfg) as conn:
        conn.execute("UPDATE items SET red_flags='[]' WHERE id=?", (rowid,))
        conn.commit()
    calls.clear()
    monkeypatch.setenv("EV_ECHO_PRIMARY_CHECK", "off")
    off = config.load()
    assert json.loads(api.emit(off, "primary"))["skipped"] == "primary_check is off"
    assert flags() == ["misrepresented_primary"] and calls == []
    with api._connect(off) as conn:
        conn.execute("UPDATE items SET red_flags='[]' WHERE id=?", (rowid,))
        conn.commit()
    assert api.run_api(off, "primary")["skipped"] == "primary_check is off"
    assert flags() == ["misrepresented_primary"] and calls == []
