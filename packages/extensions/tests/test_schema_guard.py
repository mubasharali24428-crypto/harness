"""Schema guard + result bounding unit tests."""

from __future__ import annotations

import pytest

from harness_extensions.schema_guard import (
    SchemaLimits,
    SchemaRejectedError,
    bound_result_blocks,
    validate_tool_schema,
)


def test_accepts_normal_schema():
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    assert validate_tool_schema(schema) == schema


def test_rejects_non_object_root():
    with pytest.raises(SchemaRejectedError, match="describe an object"):
        validate_tool_schema({"type": "string"})


def test_rejects_deep_recursion():
    node: dict = {"type": "object", "properties": {}}
    for _ in range(80):
        node = {"type": "object", "properties": {"n": node}}
    with pytest.raises(SchemaRejectedError, match="depth"):
        validate_tool_schema(node, SchemaLimits(max_depth=12))


def test_rejects_node_count():
    schema = {
        "type": "object",
        "properties": {f"p{i}": {"type": "string"} for i in range(500)},
    }
    with pytest.raises(SchemaRejectedError, match="nodes"):
        validate_tool_schema(schema, SchemaLimits(max_nodes=100))


def test_rejects_oversized_schema():
    schema = {"type": "object", "properties": {"big": {"type": "string", "desc": "x" * 9000}}}
    with pytest.raises(SchemaRejectedError, match="bytes"):
        validate_tool_schema(schema, SchemaLimits(max_bytes=1024))


def test_rejects_long_strings():
    schema = {"type": "object", "properties": {"a": {"type": "string", "d": "y" * 20000}}}
    with pytest.raises(SchemaRejectedError, match="string exceeds"):
        validate_tool_schema(schema, SchemaLimits(max_string_chars=1000))


def test_rejects_cycle():
    schema: dict = {"type": "object", "properties": {}}
    schema["properties"]["self"] = schema
    with pytest.raises(SchemaRejectedError, match="cycle"):
        validate_tool_schema(schema)


def test_rejects_non_string_type_field():
    with pytest.raises(SchemaRejectedError, match="describe an object"):
        validate_tool_schema({"type": {"a": 1}})


def test_rejects_non_json_values():
    with pytest.raises(SchemaRejectedError):
        validate_tool_schema({"type": "object", "properties": {"x": object()}})


def test_result_passthrough_when_small():
    blocks = [{"type": "text", "text": "hello"}]
    out = bound_result_blocks(blocks, 10_000)
    assert out.truncated is False
    assert out.blocks == blocks


def test_result_truncated_with_marker():
    blocks = [{"type": "text", "text": "z" * 50_000}]
    out = bound_result_blocks(blocks, 4096)
    assert out.truncated is True
    text = out.blocks[0]["text"]
    assert text.endswith("[truncated]")
    assert len(text.encode()) < 4096 + 100


def test_multiple_blocks_cut_in_order():
    import json

    blocks = [
        {"type": "text", "text": "a" * 3000},
        {"type": "text", "text": "b" * 3000},
        {"type": "text", "text": "c" * 3000},
    ]
    out = bound_result_blocks(blocks, 4096)
    assert out.truncated is True
    # First block fits whole; a later block is cut with the marker; the total
    # serialized payload never exceeds the budget.
    assert out.blocks[0] == blocks[0]
    assert out.blocks[-1]["text"].endswith("[truncated]")
    serialized = len(json.dumps(out.blocks, separators=(",", ":")).encode("utf-8"))
    assert serialized <= 4096


def test_non_text_block_over_budget_marker():
    blocks = [{"type": "image", "data": "x" * 20_000, "mimeType": "image/png"}]
    out = bound_result_blocks(blocks, 1024)
    assert out.truncated is True
    assert "truncated non-text block" in out.blocks[0]["text"]
