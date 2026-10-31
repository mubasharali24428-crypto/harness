"""Profile tests: the host selects the profile, and the source cannot.

``core`` is the deterministic subset; ``agent`` adds workflows, agents, effects
and spawning. A program that declares its own profile would be selecting its own
authority, which is the thing the whole dialect exists to prevent.
"""

from __future__ import annotations

import pytest

from harness_hx import PROFILES
from support import FIXTURES, check, codes, errors

CORE_WORKFLOW = (FIXTURES / "core" / "core_workflow.hx").read_text(encoding="utf-8")
PURE_HELPERS = (FIXTURES / "core" / "pure_helpers.hx").read_text(encoding="utf-8")


def test_the_host_selects_the_profile():
    assert PROFILES == ("core", "agent")
    with pytest.raises(ValueError, match="unknown profile"):
        check(PURE_HELPERS, profile="turbo")


def test_core_accepts_pure_programs():
    assert errors(check(PURE_HELPERS, profile="core")) == []


def test_core_refuses_a_workflow_and_says_why():
    result = check(CORE_WORKFLOW, profile="core")
    assert codes(result) == ["HX6001"]
    assert "agent profile" in result.diagnostics[0].message


def test_core_refuses_agent_declarations():
    source = (
        "schema P {\n  text: String\n}\n"
        "agent a: Agent<P> {\n  role: \"x\"\n}\n"
    )
    assert codes(check(source, profile="core")) == ["HX6001"]


def test_core_refuses_await_and_spawn():
    awaited = "fn f() -> Bool {\n  let x = await 5\n  return true\n}\n"
    assert codes(check(awaited, profile="core")) == ["HX6001"]
    spawned = "fn f() -> Bool {\n  let x = spawn 5\n  return true\n}\n"
    assert codes(check(spawned, profile="core")) == ["HX6001"]


def test_the_source_cannot_choose_the_profile():
    source = "profile agent\n\nschema S {\n  a: Int\n}\n"
    result = check(source)
    assert codes(result) == ["HX6002"]
    assert "selected by the host" in result.diagnostics[0].message


def test_agent_profile_is_where_workflows_live():
    assert errors(check(CORE_WORKFLOW, profile="agent")) == []
