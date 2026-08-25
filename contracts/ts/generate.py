#!/usr/bin/env python3
"""E-codegen — generate the TS contract mirror from the frozen pydantic models.

Usage (repo root):
    contracts/ts/generate.py            # writes contracts/ts/src/*.ts
    contracts/ts/generate.py --check    # exit 1 if the mirror is stale (CI gate)

Replaces hand-maintenance of the TS mirror: the .py files under
contracts/src/harness_contracts/ are the single source of truth. The generator
parses StrEnums and BaseModel fields (type + optional + default) with a small
AST walker — no runtime imports of the package, so it works from a bare venv.

Drift policy (AGENTS.md): additive changes only; this script makes drift
mechanically impossible to merge unnoticed (--check in CI).
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "src" / "harness_contracts"
OUTPUT_DIR = Path(__file__).resolve().parent / "src"

# Modules to generate and their header comments.
MODULES = ["events", "roles", "plan"]


@dataclass
class EnumDef:
    name: str
    members: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ModelDef:
    name: str
    doc: str | None
    fields: list[tuple[str, str, bool]] = field(default_factory=list)  # name, ts_type, optional


PY_TO_TS = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
}


def parse_annotation(node: ast.expr, enums: dict[str, EnumDef]) -> str:
    """Map a pydantic field annotation to its TS type."""
    if isinstance(node, ast.Name):
        if node.id in enums:
            return node.id  # enum types keep their name
        if node.id == "PrincipalId":  # type alias in roles.py — it's just str
            return "string"
        return PY_TO_TS.get(node.id, "unknown")
    if isinstance(node, ast.Constant):
        return "string" if isinstance(node.value, str) else "unknown"
    if isinstance(node, ast.Subscript):
        base = node.value.id if isinstance(node.value, ast.Name) else ""
        arg = parse_annotation(node.slice, enums)
        array_type = f"{arg}[]"
        mapping = {
            "list": array_type,
            "List": array_type,
            "dict": "Record<string, unknown>",
            "Dict": "Record<string, unknown>",
            "frozenset": "unknown",  # EVENT_TYPES etc are helpers, not wire shapes
            "set": "unknown",
            "Literal": arg,
        }
        # typing.Dict[str, Any] etc — base is an Attribute, not Name
        if base == "" and isinstance(node.value, ast.Attribute):
            base = node.value.attr
        if base == "Optional":
            return f"{arg} | null"
        return mapping.get(base, "unknown")
    if isinstance(node, ast.BinOp):  # X | Y unions
        left = parse_annotation(node.left, enums)
        right = parse_annotation(node.right, enums)
        parts = []
        for part in (left, right):
            if part not in parts:
                parts.append(part)
        return " | ".join(parts)
    if isinstance(node, ast.Attribute):
        # e.g. typing.Any — payload dicts
        if node.attr == "Any":
            return "unknown"
        return "unknown"
    return "unknown"


def parse_module(path: Path) -> tuple[dict[str, EnumDef], dict[str, ModelDef]]:
    tree = ast.parse(path.read_text())
    enums: dict[str, EnumDef] = {}
    models: dict[str, ModelDef] = {}

    # Two passes so field annotations can reference enums declared anywhere.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            if "StrEnum" in bases or "Enum" in bases:
                enums[node.name] = EnumDef(name=node.name)
            elif any(b in ("BaseModel",) for b in bases):
                doc = ast.get_docstring(node)
                models[node.name] = ModelDef(name=node.name, doc=doc)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name in enums:
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    target = stmt.targets[0]
                    if isinstance(target, ast.Name):
                        enums[node.name].members.append((target.id, stmt.value.value))
        elif node.name in models:
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    ts_type = parse_annotation(stmt.annotation, enums)
                    optional = False
                    value = stmt.value
                    if isinstance(value, ast.Call):
                        keywords = {kw.arg for kw in value.keywords if kw.arg}
                        if "default_factory" in keywords:
                            # H-1 (M17): pydantic v2 ALWAYS serializes
                            # default_factory fields (list/dict factories run
                            # for every model_dump), so these are present on
                            # every response and must stay REQUIRED in TS.
                            # Marking them optional taught clients to expect
                            # absent data that never is.
                            pass
                        elif "default" in keywords:
                            optional = True
                    elif value is not None:
                        optional = True
                    # `X | None` unions (runId: str | None) — optional on the wire too
                    ann = stmt.annotation
                    is_none_union = (
                        isinstance(ann, ast.BinOp)
                        and isinstance(ann.right, ast.Constant)
                        and ann.right.value is None
                    )
                    ts_type = f"{ts_type.rstrip('?')}"  # normalize before union check
                    if is_none_union:
                        optional = True
                        # strip the "| unknown" tail produced by the None arm
                        if " | " in ts_type:
                            ts_type = ts_type.split(" | ")[0]
                    models[node.name].fields.append((name, ts_type, optional))
    return enums, models


def render_enums(enums: dict[str, EnumDef]) -> str:
    out = []
    for e in enums.values():
        out.append(f"export const {e.name} = {{")
        for member, value in e.members:
            out.append(f'  {member}: "{value}",')
        out.append("} as const;")
        out.append("")
        out.append(f"export type {e.name} = (typeof {e.name})[keyof typeof {e.name}];")
        out.append("")
    return "\n".join(out)


def render_models(models: dict[str, ModelDef]) -> str:
    out = []
    for m in models.values():
        if m.doc:
            out.append("/**")
            for line in m.doc.splitlines():
                out.append(f" * {line}".rstrip())
            out.append(" */")
        out.append(f"export interface {m.name} {{")
        for fname, ftype, optional in m.fields:
            suffix = "?" if optional else ""
            out.append(f"  {fname}{suffix}: {ftype};")
        out.append("}")
        out.append("")
    return "\n".join(out)


HEADER = "// AUTO-GENERATED by contracts/ts/generate.py — do not edit by hand.\n"


def generate() -> dict[str, str]:
    outputs: dict[str, str] = {}
    for module in MODULES:
        enums, models = parse_module(CONTRACTS_DIR / f"{module}.py")
        body = render_enums(enums) + "\n" + render_models(models)
        outputs[f"{module}.ts"] = (
            HEADER
            + f"// Source of truth: contracts/src/harness_contracts/{module}.py\n\n"
            + body
        )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify mirror is current; exit 1 if stale")
    args = parser.parse_args()

    generated = generate()
    stale = []
    for filename, content in sorted(generated.items()):
        target = OUTPUT_DIR / filename
        if args.check:
            if not target.exists() or target.read_text() != content:
                stale.append(filename)
        else:
            target.write_text(content)
            print(f"wrote {target}")

    if args.check:
        if stale:
            print(
                "STALE TS MIRROR: " + ", ".join(stale)
                + "\nRun `contracts/ts/generate.py` locally and commit the result."
            )
            return 1
        print("TS mirror up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
