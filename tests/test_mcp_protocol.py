import io
import json
from contextlib import closing

from evidence_echo import mcp_server, query
from evidence_echo.core import config, store
from tests.conftest import write_plugin_keys
from tests.evidence_fixtures import add_study, seed_topic


def request(method, id=1, **params):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params}


def run(lines, read=config.read_paths):
    stdin = io.BytesIO(b"".join(
        (line if isinstance(line, bytes) else json.dumps(line).encode("utf-8")) + b"\n" for line in lines))
    stdout = io.BytesIO()
    assert mcp_server.serve(stdin, stdout, read) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def seeded(tmp_path, monkeypatch):
    data = tmp_path / "data"
    with closing(store.connect(seed_topic(data, "seo"))) as conn:
        add_study(conn, "Title tag study", "title tag clicks rose")
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(data))
    return data


def test_initialize_negotiates_version():
    [asked] = run([request("initialize", protocolVersion="2025-03-26")])
    assert asked["result"]["protocolVersion"] == "2025-03-26"
    [other] = run([request("initialize", protocolVersion="1999-01-01")])
    result = other["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "evidence-echo" and result["serverInfo"]["version"] != "unknown"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    for text in ("find_evidence", "/evidence-echo:setup", "untrusted"):
        assert text in result["instructions"]
    assert mcp_server.THIN_COVERAGE_LINE in result["instructions"]


def test_tools_list_schemas():
    [reply] = run([request("tools/list")])
    tools = {t["name"]: t for t in reply["result"]["tools"]}
    assert set(tools) == {"list_topics", "find_evidence", "get_study"}
    assert tools["find_evidence"]["inputSchema"]["required"] == ["question"]
    assert tools["get_study"]["inputSchema"]["required"] == ["topic", "study_id"]
    assert all(t["description"] and t["inputSchema"]["type"] == "object" for t in tools.values())


def test_tools_call_find_evidence(tmp_path, monkeypatch):
    seeded(tmp_path, monkeypatch)
    [reply] = run([request("tools/call", name="find_evidence", arguments={"question": "title tag clicks"})])
    assert reply["result"]["isError"] is False
    assert json.loads(reply["result"]["content"][0]["text"]) == query.find_evidence(config.read_paths(),
                                                                                    "title tag clicks")
    [listed] = run([request("tools/call", name="list_topics", arguments={})])
    assert json.loads(listed["result"]["content"][0]["text"])[0]["id"] == "seo"
    [one] = run([request("tools/call", name="get_study", arguments={"topic": "seo", "study_id": 1})])
    assert one["result"]["isError"] is False and json.loads(one["result"]["content"][0]["text"])["topic"] == "seo"


def test_tools_call_errors_are_results(tmp_path, monkeypatch):
    monkeypatch.setenv("EV_ECHO_DATA_DIR", str(tmp_path / "nothing"))
    [unset] = run([request("tools/call", name="find_evidence", arguments={"question": "x"})])
    assert unset["result"]["isError"] is True and "/evidence-echo:setup" in unset["result"]["content"][0]["text"]
    seeded(tmp_path, monkeypatch)
    [missing] = run([request("tools/call", name="get_study", arguments={"topic": "seo", "study_id": "s99"})])
    assert missing["result"]["isError"] is True
    [no_question] = run([request("tools/call", name="find_evidence", arguments={})])
    assert no_question["result"]["isError"] is True and "question" in no_question["result"]["content"][0]["text"]
    [bad_limit] = run([request("tools/call", name="find_evidence", arguments={"question": "x", "limit": "many"})])
    assert bad_limit["result"]["isError"] is True and "limit" in bad_limit["result"]["content"][0]["text"]


def test_unknown_method_and_tool():
    [method, tool] = run([request("nope"), request("tools/call", id=2, name="nope", arguments={})])
    assert method["error"]["code"] == -32601
    assert tool["error"]["code"] == -32602 and tool["id"] == 2


def test_notifications_get_no_reply():
    replies = run([{"jsonrpc": "2.0", "method": "notifications/initialized"}, request("ping", id=7)])
    assert replies == [{"jsonrpc": "2.0", "id": 7, "result": {}}]


def test_malformed_line_does_not_kill_server():
    replies = run([b"{not json", b"[1,2]", b"", json.dumps(request("ping")).encode()])
    assert [r.get("error", {}).get("code") for r in replies[:2]] == [-32700, -32600]
    assert replies[0]["id"] is None and replies[1]["id"] is None
    assert replies[2]["result"] == {} and len(replies) == 3


def test_internal_error_keeps_serving():
    def broken():
        raise RuntimeError("boom")

    replies = run([request("tools/call", id=1, name="list_topics", arguments={}), request("ping", id=2)],
                  read=broken)
    assert replies[0]["error"]["code"] == -32603 and replies[0]["id"] == 1
    assert replies[1]["result"] == {}


def test_server_never_prints_keys(tmp_path, monkeypatch):
    data = seeded(tmp_path, monkeypatch)
    plugin_data = tmp_path / "plugin-data"
    monkeypatch.setenv("EV_ECHO_PLUGIN_DATA", str(plugin_data))
    write_plugin_keys(plugin_data, ANTHROPIC_API_KEY="sk-test-123")
    (data / "keys.json").write_text(json.dumps({"ANTHROPIC_API_KEY": "sk-test-123"}), encoding="utf-8")
    out = io.BytesIO()
    mcp_server.serve(io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in [
        request("initialize", protocolVersion="2025-06-18"), request("tools/list", id=2),
        request("tools/call", id=3, name="list_topics", arguments={}),
        request("tools/call", id=4, name="find_evidence", arguments={"question": "title tag"})])), out)
    assert b"sk-test-123" not in out.getvalue()


def test_deeply_nested_line_does_not_end_the_session():
    replies = run([b"[" * 200000, json.dumps(request("ping")).encode()])
    assert replies[0]["error"]["code"] == -32700 and replies[-1]["result"] == {}


def test_tool_name_that_is_not_a_string():
    [reply] = run([request("tools/call", name=["find_evidence"])])
    assert reply["error"]["code"] == -32602


def test_instructions_offer_search_on_thin_coverage():
    assert '/evidence-echo:search "<question>" --topic <id>' in mcp_server.INSTRUCTIONS
    assert "thin" in mcp_server.THIN_COVERAGE_LINE
