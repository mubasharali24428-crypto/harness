"""Hx — the Harness dialect of Hermes.

Hx is a Harness-owned language surface, not the external Hermes compiler. The
dialect keeps the parts of Hermes that carry the product claim (a real grammar,
typed values, two backends we can differentially test) and replaces the parts
that were never true: ambient host access, implicit authority, unbounded
execution.

This package is the checker half of the v0.1 prototype: lexer, parser,
type/effect checker over a single-file program, and the policy-request
manifest. There is no interpreter or VM here — ``hxc check`` proves a program
is well formed and tells the Harness what it asks for; it does not run it.

The public surface is deliberately small::

    from harness_hx import check_source, Diagnostic, RuleSet

    diagnostics = check_source(source, path="app.hx", profile="agent")

Profiles are selected by the host, never by the source. ``core`` is the pure
deterministic subset; ``agent`` adds workflows, agents, effects and spawning.
"""

from __future__ import annotations

from .api import CheckResult, check_file, check_source
from .checker import RuleSet
from .diagnostics import Diagnostic, render_json, render_line
from .manifest import build_manifest
from .version import PROFILES, SOURCE_SUFFIX, SPEC_VERSION, TOOLCHAIN_VERSION

__all__ = [
    "PROFILES",
    "SOURCE_SUFFIX",
    "SPEC_VERSION",
    "TOOLCHAIN_VERSION",
    "CheckResult",
    "Diagnostic",
    "RuleSet",
    "build_manifest",
    "check_file",
    "check_source",
    "render_json",
    "render_line",
]
