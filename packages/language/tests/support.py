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


def check(text: str, profile: str = "agent", rules: RuleSet | None = None, path: str = "<memory>"):
    return check_source(text, path, profile, rules)


def codes(result: CheckResult) -> list[str]:
    return [diagnostic.code for diagnostic in result.diagnostics]


def errors(result: CheckResult) -> list[Diagnostic]:
    return [diagnostic for diagnostic in result.diagnostics if diagnostic.kind == "error"]


def records(result: CheckResult) -> list[dict[str, object]]:
    return [
        {
            "line": diagnostic.line,
            "col": diagnostic.col,
            "kind": diagnostic.kind,
            "code": diagnostic.code,
            "message": diagnostic.message,
        }
        for diagnostic in result.diagnostics
    ]


def expected_for(source: Path) -> list[dict[str, object]]:
    return json.loads(source.with_suffix(".expected.json").read_text(encoding="utf-8"))
