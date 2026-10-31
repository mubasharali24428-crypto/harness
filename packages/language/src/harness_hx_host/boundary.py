"""The typed host boundary: untrusted data in, a declared type or a failure out.

A static annotation is not evidence. Everything a host operation returns is
data that arrived from outside the program, so it is validated against the
type the source declared *before* the program may touch it — once, at the
boundary, with a position and a stable code when it fails.

Nominal, not structural: two schemas with identical fields are different
types, an unknown field is an error (fail closed, never silently drop data),
and a missing field is an error. ``Any`` is the only universal match, and it
exists for the checker's escape hatch, not for privileged results.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import HxHostError, InvalidHostResult, ScriptError

PRIMITIVES: dict[str, tuple[type, ...]] = {
    "String": (str,),
    "Bool": (bool,),
    "Int": (int,),
    "Float": (int, float),
    "Duration": (str,),
    "Money": (str,),
}


@dataclass(frozen=True)
class SchemaValue:
    """A validated nominal record. The name travels with it: ``RepoFile`` is not a dict."""

    name: str
    fields: Mapping[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {key: plain(value) for key, value in self.fields.items()}


def plain(value: Any) -> Any:
    """Render a runtime value as JSON-able data (evidence, outputs, event payloads)."""
    if isinstance(value, SchemaValue):
        return value.to_json()
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def truthy(value: Any) -> bool:
    if isinstance(value, SchemaValue):
        return True
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return bool(value)


def split_type(text: str) -> tuple[str, list[str]]:
    """``List<Map<String, RepoFile>>`` -> ``("List", ["Map<String, RepoFile>"])``."""
    text = text.strip()
    if "<" not in text:
        return text, []
    name, _, rest = text.partition("<")
    if not rest.endswith(">"):
        raise ScriptError(f"malformed type '{text}'")
    body = rest[:-1]
    args: list[str] = []
    depth = 0
    current = ""
    for char in body:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        if char == "," and depth == 0:
            args.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        args.append(current.strip())
    return name.strip(), args


def validate(
    type_text: str,
    value: Any,
    schemas: Mapping[str, Any],
    *,
    error: type[HxHostError] = ScriptError,
    where: str | None = None,
) -> Any:
    """Validate ``value`` against ``type_text`` or raise ``error``.

    The failure names the field path, because "RepoFile.text is not a String"
    is actionable and "invalid host result" is not.
    """
    text = type_text.strip()
    if text.endswith("?"):
        if value is None:
            return None
        return validate(text[:-1], value, schemas, error=error, where=where)
    name, args = split_type(text)
    # At the top level, name the type being validated; "value" says nothing.
    where = where or (name if name in schemas else "value")
    if name == "Any":
        return value
    if name in PRIMITIVES:
        expected = PRIMITIVES[name]
        # bool is an int subclass in Python; a Bool must not pass as an Int.
        if name in {"Int", "Float"} and isinstance(value, bool):
            raise error(f"{where} must be {name}, got Bool")
        if not isinstance(value, expected):
            got = type(value).__name__
            raise error(f"{where} must be {name}, got {got}")
        return value
    if name == "List":
        if not args:
            raise error(f"{where}: List needs one type argument")
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise error(f"{where} must be List<{args[0]}>, got {type(value).__name__}")
        return [
            validate(args[0], item, schemas, error=error, where=f"{where}[{index}]")
            for index, item in enumerate(value)
        ]
    if name == "Map":
        if len(args) != 2:
            raise error(f"{where}: Map needs two type arguments")
        if not isinstance(value, Mapping):
            raise error(f"{where} must be Map<{args[0]}, {args[1]}>, got {type(value).__name__}")
        out: dict[str, Any] = {}
        for key, item in value.items():
            if args[0] == "String" and not isinstance(key, str):
                raise error(f"{where}: Map key {key!r} must be String")
            out[key] = validate(args[1], item, schemas, error=error, where=f"{where}[{key!r}]")
        return out
    schema = schemas.get(name)
    if schema is None:
        raise error(f"{where}: no schema named '{name}' is declared in this program")
    if isinstance(value, SchemaValue):
        if value.name != name:
            raise error(f"{where} must be {name}, got {value.name}")
        source: Mapping[str, Any] = value.fields
    elif isinstance(value, Mapping):
        source = value
    else:
        raise error(f"{where} must be {name}, got {type(value).__name__}")
    declared = {field.name: field.type for field in schema.fields}
    missing = sorted(set(declared) - set(source))
    extra = sorted(set(source) - set(declared))
    if missing:
        raise error(f"{where}: {name} is missing field(s) {', '.join(missing)}")
    if extra:
        raise error(f"{where}: {name} has undeclared field(s) {', '.join(extra)}")
    fields = {
        field_name: validate(
            type_name_text(declared[field_name]),
            source[field_name],
            schemas,
            error=error,
            where=f"{where}.{field_name}",
        )
        for field_name in declared
    }
    return SchemaValue(name, fields)


def type_name_text(type_ref: Any) -> str:
    """Render an AST ``TypeRef`` the way ``split_type`` reads it."""
    if isinstance(type_ref, str):
        return type_ref
    name = type_ref.name
    if type_ref.args:
        name = f"{name}<{', '.join(type_name_text(arg) for arg in type_ref.args)}>"
    if type_ref.optional:
        name = f"{name}?"
    return name


def host_result(type_text: str, value: Any, schemas: Mapping[str, Any], operation: str) -> Any:
    """Validate one host reply. A mismatch is the host's bug, not the program's."""
    return validate(
        type_text,
        value,
        schemas,
        error=InvalidHostResult,
        where=f"{operation} result",
    )
