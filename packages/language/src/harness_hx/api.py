"""The one entry point the rest of the Harness should use: source in, records out.

One file is the common case and stays the fast path: parse, check, return. Since M8
a program may also `use` modules, which adds two things this layer owns and the
checker does not: turning names into bytes, and checking each file as *itself*
before the file that reads it sees its declarations.

The prefixing step is the subtle one. Inside `notes.hx` a type is `Summary`; to
`main.hx` it is `notes.Summary`, and the same value must be the same type on both
sides of that boundary or a caller could pass a look-alike record into a helper. So
each module's declarations are re-keyed for the reader, and the rewrite happens on
the way out — the module's own check still sees its own names.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from . import ast as A
from .checker import Checker, RuleSet, check
from .diagnostics import Diagnostic, Reporter
from .loader import (
    ResolvedModule,
    Resolver,
    dict_resolver,
    directory_resolver,
    load_modules,
)
from .parser import parse

PROFILES = ("core", "agent")

@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Everything a caller needs: the records, and the tree they came from."""

    path: str
    source: str
    program: A.Program
    diagnostics: list[Diagnostic]
    modules: tuple[ResolvedModule, ...] = ()

    @property
    def errors(self) -> int:
        return sum(1 for record in self.diagnostics if record.kind == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for record in self.diagnostics if record.kind == "warning")

    @property
    def ok(self) -> bool:
        return self.errors == 0

    @property
    def sources(self) -> tuple[tuple[str, str], ...]:
        """(path, text) for every file that built this tree, entry first."""
        return ((self.path, self.source), *(
            (module.path, module.source) for module in self.modules
        ))


def resolver_for(
    modules: Mapping[str, str] | str | Path | Resolver | None,
) -> Resolver | None:
    """Accept whichever of the three host shapes it was handed, or none."""
    if modules is None:
        return None
    if callable(modules):
        return cast(Resolver, modules)
    if isinstance(modules, (str, Path)):
        return directory_resolver(modules)
    return dict_resolver(modules)


def check_source(
    source: str,
    path: str = "<memory>",
    profile: str = "agent",
    rules: RuleSet | None = None,
    modules: Mapping[str, str] | str | Path | None = None,
) -> CheckResult:
    """Check one program, and since M8 the files it is built from.

    `modules` is the host's answer to `use`: a name -> source mapping, a directory,
    or a callable. Passing nothing while the program says `use` is an error, not a
    silent single-file check: the author asked for a helper the host did not give.
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; expected one of {', '.join(PROFILES)}")
    reporter = Reporter(path)
    program = parse(source, reporter)
    loaded = load_modules(program, path, resolver_for(modules))
    tables: dict[str, tuple[A.Decl, ...]] = {}
    per_module: list[Diagnostic] = []
    for module in loaded.modules:
        # Checked as its own file first, so a mistake in a helper is reported in the
        # helper's own words; then renamed for the file that reads it.
        module_reporter = Reporter(module.path)
        Checker(module.source_program, profile, module_reporter, rules).run()
        per_module.extend(module_reporter.diagnostics())
        tables[module.name] = module.program.decls
    check(program, profile, reporter, rules, tables)
    records = [*reporter.diagnostics(), *per_module, *loaded.diagnostics]
    # One order for one program: records grouped by file, then position. Without it
    # a two-file check would report in resolution order, which depends on a mapping.
    return CheckResult(
        path,
        source,
        program,
        sorted(records, key=lambda record: record.sort_key()),
        loaded.modules,
    )


def check_file(
    path: str | Path,
    profile: str = "agent",
    rules: RuleSet | None = None,
    modules: Mapping[str, str] | str | Path | None = "auto",
) -> CheckResult:
    """Check a file on disk, resolving `use` against its own directory by default.

    `"auto"` means "the entry file's directory": that is where a module lives in
    every project layout this language has been used in, and a caller who wants a
    different root passes it as `modules`.
    """
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    root: Mapping[str, str] | str | Path | None = (
        file_path.parent if modules == "auto" else modules
    )
    return check_source(source, str(file_path), profile, rules, root)
