"""Hardening for externally-supplied tool schemas and results.

An MCP server is attacker-influenced input: its ``tools/list`` payload can be
a recursion bomb, a megabyte description, a non-object schema or a ``$ref``
cycle. The guard runs BEFORE any schema is persisted or surfaced, with hard
byte/depth/node bounds. ``$ref`` is treated as a terminal leaf — we never
resolve external references, so ref-based expansion bombs cannot recurse us;
the depth bound still applies to the literal document.

Rejections are recorded (reason included) rather than raised and swallowed:
the operator can see exactly which tool was rejected and why, and the tool is
never callable afterward.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class SchemaRejectedError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


_OBJECT = "object"


@dataclass(frozen=True)
class SchemaLimits:
    max_bytes: int = 64 * 1024
    max_depth: int = 12
    max_nodes: int = 2000
    max_string_chars: int = 16 * 1024


def _walk(value: Any, depth: int, limits: SchemaLimits, seen: list[int], stats: list[int]) -> None:
    if depth > limits.max_depth:
        raise SchemaRejectedError(f"schema exceeds max depth {limits.max_depth}")
    stats[0] += 1
    if stats[0] > limits.max_nodes:
        raise SchemaRejectedError(f"schema exceeds max nodes {limits.max_nodes}")
    if isinstance(value, str):
        if len(value) > limits.max_string_chars:
            raise SchemaRejectedError(f"schema string exceeds {limits.max_string_chars} chars")
        return
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return
    if isinstance(value, (dict, list)):
        ident = id(value)
        if ident in seen:
            # A self-referential Python object (json.loads can't produce one,
            # but direct construction can) is a cycle — reject outright.
            raise SchemaRejectedError("schema contains a reference cycle")
        seen.append(ident)
        try:
            if isinstance(value, dict):
                for k, v in value.items():
                    if not isinstance(k, str):
                        raise SchemaRejectedError("schema object keys must be strings")
                    _walk(k, depth + 1, limits, seen, stats)
                    _walk(v, depth + 1, limits, seen, stats)
            else:
                for item in value:
                    _walk(item, depth + 1, limits, seen, stats)
        finally:
            seen.pop()
        return
    raise SchemaRejectedError(f"schema contains unsupported type {type(value).__name__}")


def validate_tool_schema(schema: Any, limits: SchemaLimits | None = None) -> dict:
    """Return the validated schema or raise :class:`SchemaRejectedError`.

    Requirements beyond bounds: the root MUST be a JSON object describing the
    tool's named arguments — i.e. ``type`` is either omitted or ``"object"``
    (a tool whose arguments are a bare string cannot be called through the
    registry's argument dict contract).
    """
    limits = limits or SchemaLimits()
    if not isinstance(schema, dict):
        raise SchemaRejectedError("tool inputSchema must be a JSON object")
    _walk(schema, 0, limits, [], [0])  # cycles + bounds first (pre-serialization)
    try:
        raw = json.dumps(schema, separators=(",", ":"), sort_keys=True, default=_no_default)
    except ValueError as exc:
        raise SchemaRejectedError(f"schema is not JSON-serializable: {exc}") from exc
    if len(raw.encode("utf-8")) > limits.max_bytes:
        raise SchemaRejectedError(f"schema exceeds {limits.max_bytes} bytes")
    stype = schema.get("type")
    if stype is not None and stype != _OBJECT:
        raise SchemaRejectedError(
            "tool inputSchema must describe an object (type 'object' or omitted)"
        )
    return schema


def _no_default(value: Any) -> Any:
    raise SchemaRejectedError(f"schema contains non-JSON value {type(value).__name__}")


@dataclass(frozen=True)
class ResultTruncation:
    blocks: list[dict]
    truncated: bool


def bound_result_blocks(blocks: list[dict], max_bytes: int) -> ResultTruncation:
    """Cap the serialized size of tool-call observation blocks.

    Greedy, order-preserving and exact: every append is re-serialized and
    checked against the budget; when a block does not fit whole, a text block
    is prefix-cut with an explicit ``[truncated]`` marker, a non-text block is
    replaced by a marker block, and a block that cannot fit even truncated is
    dropped (the flag still reports the loss). The final payload never
    exceeds ``max_bytes``.
    """
    total = _ser(blocks)
    if total <= max_bytes:
        return ResultTruncation(blocks=blocks, truncated=False)

    out: list[dict] = []
    for block in blocks:
        if _ser([*out, block]) <= max_bytes:
            out.append(block)
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text = block["text"]
            marker = "\n[truncated]"
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                trial = {**block, "text": text[:mid] + marker}
                if _ser([*out, trial]) <= max_bytes:
                    lo = mid
                else:
                    hi = mid - 1
            if lo > 0:
                out.append({**block, "text": text[:lo] + marker})
            continue
        marker_block = {
            "type": "text",
            "text": (
                f"[truncated non-text block: type={block.get('type')} "
                f"estBytes={_ser([block])}]"
            ),
        }
        if _ser([*out, marker_block]) <= max_bytes:
            out.append(marker_block)
    return ResultTruncation(blocks=out, truncated=True)


def _ser(blocks: list[dict]) -> int:
    return len(json.dumps(blocks, separators=(",", ":")).encode("utf-8"))
