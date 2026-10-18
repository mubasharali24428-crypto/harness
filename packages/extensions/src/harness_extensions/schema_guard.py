"""Bounds on untrusted tool definitions discovered from external MCP servers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

MAX_TOOLS = 200
MAX_SCHEMA_BYTES = 32 * 1024
MAX_DEPTH = 8
MAX_NODES = 1500
MAX_DESC = 1000
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
# Types we can validate arguments against; anything else is rejected rather than guessed.
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
_INJECTION = re.compile(
    r"(?i)ignore (all|any|previous|prior)|disregard .{0,30}instruction|you must (now )?call|"
    r"system prompt|exfiltrat|send .{0,40}(secret|token|key)"
)


class SchemaRejected(ValueError):
    pass


def _walk(node: Any, depth: int, count: list[int]) -> None:
    count[0] += 1
    if depth > MAX_DEPTH:
        raise SchemaRejected("schema nesting too deep")
    if count[0] > MAX_NODES:
        raise SchemaRejected("schema has too many nodes")
    if isinstance(node, dict):
        if "$ref" in node or "$dynamicRef" in node or "$recursiveRef" in node:
            raise SchemaRejected("schema references ($ref) are not accepted")
        t = node.get("type")
        if isinstance(t, str) and t not in _TYPES:
            raise SchemaRejected(f"unsupported schema type '{t}'")
        for v in node.values():
            _walk(v, depth + 1, count)
    elif isinstance(node, list):
        for v in node:
            _walk(v, depth + 1, count)


def digest(tool: dict[str, Any]) -> str:
    """Stable hash of the security-relevant definition (name + schema + description)."""
    body = {k: tool.get(k) for k in ("name", "description", "inputSchema")}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_tool(raw: Any) -> dict[str, Any]:
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("name"), str)
        or not NAME.match(raw["name"])
    ):
        raise SchemaRejected("tool name is missing or unsafe")
    schema = raw.get("inputSchema", {"type": "object"})
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise SchemaRejected("inputSchema must be an object schema")
    if len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        raise SchemaRejected("inputSchema is too large")
    _walk(schema, 0, [0])
    desc = raw.get("description") or ""
    if not isinstance(desc, str):
        raise SchemaRejected("description must be a string")
    clean = {"name": raw["name"], "description": desc[:MAX_DESC], "inputSchema": schema}
    clean["suspicious"] = bool(_INJECTION.search(desc))  # flagged for the admin; never acted on
    return clean


def validate_arguments(schema: dict[str, Any], args: Any) -> None:
    """Subset validator: type, required, properties, enum, additionalProperties=false."""

    def check(s: dict[str, Any], v: Any, path: str) -> None:
        t = s.get("type")
        ok = {
            "object": isinstance(v, dict),
            "array": isinstance(v, list),
            "string": isinstance(v, str),
            "boolean": isinstance(v, bool),
            "null": v is None,
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, int | float) and not isinstance(v, bool),
        }
        if t and not ok.get(t, False):
            raise SchemaRejected(f"{path or 'arguments'} must be {t}")
        if "enum" in s and v not in s["enum"]:
            raise SchemaRejected(f"{path} is not an allowed value")
        if t == "object":
            props = s.get("properties", {})
            for r in s.get("required", []):
                if r not in v:
                    raise SchemaRejected(f"{path + '.' if path else ''}{r} is required")
            if s.get("additionalProperties") is False:
                extra = set(v) - set(props)
                if extra:
                    raise SchemaRejected(f"unexpected arguments: {sorted(extra)}")
            for k, sub in props.items():
                if k in v and isinstance(sub, dict):
                    check(sub, v[k], f"{path}.{k}" if path else k)
        if t == "array" and isinstance(s.get("items"), dict):
            for i, item in enumerate(v):
                check(s["items"], item, f"{path}[{i}]")
        if t == "string" and isinstance(s.get("maxLength"), int) and len(v) > s["maxLength"]:
            raise SchemaRejected(f"{path} is too long")

    check(schema, args, "")
