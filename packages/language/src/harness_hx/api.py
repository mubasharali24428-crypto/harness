"""The one entry point the rest of the Harness should use: source in, records out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import ast as A
from .checker import RuleSet, check
from .diagnostics import Diagnostic, Reporter
from .parser import parse

PROFILES = ("core", "agent")


@dataclass(frozen=True)
class CheckResult:
    """Everything a caller needs: the records, and the tree they came from."""

    path: str
    source: str
    program: A.Program
    diagnostics: list[Diagnostic]

    @property
    def errors(self) -> int:
        return sum(1 for record in self.diagnostics if record.kind == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for record in self.diagnostics if record.kind == "warning")

    @property
    def ok(self) -> bool:
        return self.errors == 0


def check_source(
    source: str,
    path: str = "<memory>",
    profile: str = "agent",
    rules: RuleSet | None = None,
) -> CheckResult:
    """Check one program. The profile is an argument because the host selects it."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; expected one of {', '.join(PROFILES)}")
    reporter = Reporter(path)
    program = parse(source, reporter)
    check(program, profile, reporter, rules)
    return CheckResult(path, source, program, reporter.diagnostics())


def check_file(
    path: str | Path,
    profile: str = "agent",
    rules: RuleSet | None = None,
) -> CheckResult:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    return check_source(source, str(file_path), profile, rules)
