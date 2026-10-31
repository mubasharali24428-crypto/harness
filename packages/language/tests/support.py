"""Shared helpers for the fixture-driven tests."""

from __future__ import annotations

import json
from pathlib import Path

from harness_hx import CheckResult, RuleSet, check_source
from harness_hx.diagnostics import Diagnostic

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# (directory, profile) — each directory is checked under the profile it targets.
SETS = (
    ("valid", "agent"),
    ("invalid", "agent"),
    ("warnings", "agent"),
    ("core", "core"),
)


def cases() -> list[tuple[str, str, Path]]:
    found: list[tuple[str, str, Path]] = []
    for directory, profile in SETS:
        for source in sorted((FIXTURES / directory).glob("*.hx")):
            found.append((directory, profile, source))
    return found


def case_ids() -> list[str]:
    return [f"{directory}/{source.stem}" for directory, _, source in cases()]


def modules_for(source: Path) -> dict[str, str] | None:
    """A fixture's sidecar modules, if it has one.

    `use` needs bytes to resolve against, so a cross-file fixture ships a
    `<name>.hx.modules.json` mapping of module name to source. Without the sidecar
    a fixture is a single file, checked exactly as it always was.
    """
    sidecar = source.with_suffix(".hx.modules.json")
    if not sidecar.exists():
        return None
    return {
        name: text
        for name, text in json.loads(sidecar.read_text(encoding="utf-8")).items()
    }


def check(
    text: str,
    profile: str = "agent",
    rules: RuleSet | None = None,
    path: str = "<memory>",
    modules: dict[str, str] | None = None,
):
    return check_source(text, path, profile, rules, modules)


def codes(result: CheckResult) -> list[str]:
    return [diagnostic.code for diagnostic in result.diagnostics]


def errors(result: CheckResult) -> list[Diagnostic]:
    return [diagnostic for diagnostic in result.diagnostics if diagnostic.kind == "error"]


def records(result: CheckResult) -> list[dict[str, object]]:
    """The golden shape for a check's records.

    `path` is written only when a record comes from some other file. Before M8 every
    record belonged to the checked file, so the fixture's own name was implied; now a
    `use` can make a record land in a helper, and "which file" is part of what a
    reviewer is agreeing to. Staying silent when the path is the obvious one keeps
    that fact visible in the few goldens where it actually differs.
    """
    found: list[dict[str, object]] = []
    for diagnostic in result.diagnostics:
        record: dict[str, object] = {
            "line": diagnostic.line,
            "col": diagnostic.col,
            "kind": diagnostic.kind,
            "code": diagnostic.code,
            "message": diagnostic.message,
        }
        if diagnostic.path != result.path:
            record["path"] = diagnostic.path
        found.append(record)
    return found


def expected_for(source: Path) -> list[dict[str, object]]:
    return json.loads(source.with_suffix(".expected.json").read_text(encoding="utf-8"))
