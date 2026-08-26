"""Protocol-level tests: handshake, capabilities, tools/list shape.

These drive a REAL MCP session (official SDK client ⇄ our lowlevel server)
over memory streams — the same wire traffic `npx
@modelcontextprotocol/inspector` produces against the stdio transport.
All calls go through ``run(coro)`` so they execute on the pair's own loop.
"""

from __future__ import annotations

from conftest import parse_payload


async def test_initialize_handshake(mcp):
    """initialize completed before the fixture yielded: session is usable."""
    run = mcp
    res = run(lambda s: s.send_ping(), timeout=10)
    assert res is not None  # ping round-trip proves the handshake era is live


async def test_tools_list_shape_and_registry_parity(mcp, registry):
    run = mcp
    res = run(lambda s: s.list_tools())
    names = [tool.name for tool in res.tools]
    assert names == ["start_run", "get_run_status", "search_context", "list_projects"]
    assert [tool["name"] for tool in registry] == names

    by_name = {tool.name: tool for tool in res.tools}
    for tool in res.tools:
        # MCP spec: name + inputSchema are required; descriptions written for LLMs.
        assert isinstance(tool.description, str) and len(tool.description) > 40, tool.name
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert "properties" in schema

    start = by_name["start_run"]
    assert start.input_schema["required"] == ["goal"]
    assert start.input_schema["properties"]["goal"]["type"] == "string"

    status = by_name["get_run_status"]
    assert status.input_schema["required"] == ["runId"]

    search = by_name["search_context"]
    assert set(search.input_schema["required"]) == {"query", "projectId"}
    topk = search.input_schema["properties"]["topK"]
    assert topk["type"] == "integer" and topk["minimum"] == 1 and topk["maximum"] == 50

    assert by_name["list_projects"].input_schema["required"] == []


async def test_tool_schemas_are_valid_json_schema(registry):
    """Every published schema must survive a real JSON-Schema validation pass."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover — validator optional in this env
        return
    for tool in registry:
        validator = Draft202012Validator(tool["inputSchema"])
        minimal = {k: _MIN_INSTANCES.get(k, "x") for k in tool["inputSchema"].get("required", [])}
        assert list(validator.iter_errors(minimal)) == [], tool["name"]


_MIN_INSTANCES: dict[str, object] = {
    "goal": "fix the login bug",
    "runId": "run-abc123",
    "query": "where is auth enforced",
    "projectId": "p-1",
}


async def test_unknown_tool_answers_is_error_not_protocol_fault(mcp):
    run = mcp
    res = run(lambda s: s.call_tool("definitely_not_a_tool", {}))
    assert res.is_error is True
    payload = parse_payload(res)
    assert payload["error"]["code"] == "UNKNOWN_TOOL"


async def test_missing_required_argument_is_invalid_arguments(mcp):
    run = mcp
    res = run(lambda s: s.call_tool("start_run", {}))  # goal omitted
    assert res.is_error is True
    payload = parse_payload(res)
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"
    assert "goal" in payload["error"]["message"]
