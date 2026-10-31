"""Seeded faults: each rule is switched off and the finding it owned disappears.

A validator that reports nothing passes every test, and one that reports
everything passes none. Seeding the fault and then disabling the rule is how we
show a rule is load-bearing rather than decorative: the code vanishes, and the
remaining findings stay put.
"""

from __future__ import annotations

import dataclasses

import pytest

from harness_hx import RuleSet
from support import check, codes

UNDECLARED = (
    "schema Evidence {\n  passed: Bool\n}\n"
    "workflow w(input: String) -> Evidence {\n"
    "  requires repo.read\n"
    "  budget { steps: 5 }\n"
    '  let e = await verify.tests(input, suite: "unit")\n'
    "  return e\n"
    "}\n"
)

UNAWAITED = (
    "schema Evidence {\n  passed: Bool\n}\n"
    "workflow w(input: String) -> Evidence {\n"
    "  requires tool.test\n"
    "  budget { steps: 5 }\n"
    '  let e = verify.tests(input, suite: "unit")\n'
    "  return e\n"
    "}\n"
)

AMBIENT = (
    "schema Evidence {\n  passed: Bool\n}\n"
    "workflow w(input: String) -> Evidence {\n"
    "  requires tool.test\n"
    "  budget { steps: 5 }\n"
    '  let e = exec("ls")\n'
    "  return e\n"
    "}\n"
)


def test_the_default_ruleset_has_every_rule_on():
    assert RuleSet() == RuleSet(
        enforce_capabilities=True,
        require_awaited_effects=True,
        forbid_ambient=True,
    )


def test_the_rules_cannot_be_mutated_mid_run():
    with pytest.raises(dataclasses.FrozenInstanceError):
        RuleSet().forbid_ambient = False  # type: ignore[misc]


def test_disabling_capability_enforcement_removes_only_hx4001():
    baseline = codes(check(UNDECLARED))
    assert "HX4001" in baseline
    relaxed = codes(check(UNDECLARED, rules=RuleSet(enforce_capabilities=False)))
    assert "HX4001" not in relaxed
    assert "HX7001" in relaxed


def test_disabling_awaited_effects_removes_only_hx4005():
    assert codes(check(UNAWAITED)) == ["HX4005"]
    assert codes(check(UNAWAITED, rules=RuleSet(require_awaited_effects=False))) == []


def test_disabling_the_ambient_wall_downgrades_hx4004_to_an_unknown_name():
    baseline = codes(check(AMBIENT))
    assert "HX4004" in baseline
    relaxed = codes(check(AMBIENT, rules=RuleSet(forbid_ambient=False)))
    assert "HX4004" not in relaxed
    assert "HX3005" in relaxed


def test_the_rules_are_independent():
    ambient_relaxed = codes(check(UNDECLARED, rules=RuleSet(forbid_ambient=False)))
    assert "HX4001" in ambient_relaxed
    capability_relaxed = codes(check(AMBIENT, rules=RuleSet(enforce_capabilities=False)))
    assert "HX4004" in capability_relaxed
    awaited_relaxed = codes(check(AMBIENT, rules=RuleSet(require_awaited_effects=False)))
    assert "HX4004" in awaited_relaxed


def test_turning_rules_off_never_turns_parsing_errors_off():
    source = "schema S {\n  a: Int\n"
    result = check(source, rules=RuleSet(False, False, False))
    assert codes(result) == ["HX2002"]
