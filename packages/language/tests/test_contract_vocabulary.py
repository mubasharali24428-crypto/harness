"""The bridge test: every Hx capability must mean something to the Harness.

Hx invents no parallel permission vocabulary. Each capability names a permission
from ``harness_contracts.policy_engine``, and the table is closed so a new one
cannot appear without this file changing. The human-only permissions are absent
by construction — a program may never hold them, so it may never name them.
"""

from __future__ import annotations

from harness_contracts.policy_engine import PERMISSIONS

from harness_hx.capabilities import (
    AGENT_OPERATIONS,
    CAPABILITIES,
    FORBIDDEN_AMBIENT,
    FORBIDDEN_AUTHORITY,
    HOST_OPERATIONS,
    capability_names,
)

HUMAN_ONLY = {"approve", "administer", "billing", "export"}


def test_every_capability_speaks_the_harness_permission_vocabulary():
    for capability in CAPABILITIES.values():
        assert capability.permission in PERMISSIONS, capability.name


def test_no_capability_reaches_a_human_only_permission():
    assert HUMAN_ONLY <= set(PERMISSIONS)
    for capability in CAPABILITIES.values():
        assert capability.permission not in HUMAN_ONLY, capability.name


def test_every_effect_maps_to_a_declared_capability():
    for operation in HOST_OPERATIONS.values():
        if operation.capability is None:
            continue
        assert operation.capability in CAPABILITIES, operation.name


def test_the_pure_operation_requests_nothing():
    bundle = HOST_OPERATIONS["review.bundle"]
    assert bundle.capability is None
    assert not bundle.is_effect


def test_the_capability_table_is_closed():
    assert set(CAPABILITIES) == {
        "model.invoke",
        "repo.read",
        "repo.propose_patch",
        "tool.test",
    }


def test_the_operation_table_is_closed():
    assert set(HOST_OPERATIONS) == {
        "repo.read",
        "repo.list",
        "repo.propose_patch",
        "verify.tests",
        "review.bundle",
    }
    assert AGENT_OPERATIONS == ("plan", "propose", "review")


def test_no_operation_name_collides_with_the_forbidden_namespace():
    assert not set(HOST_OPERATIONS) & FORBIDDEN_AMBIENT
    assert not set(CAPABILITIES) & FORBIDDEN_AUTHORITY


def test_the_names_are_dotted_and_pronounceable():
    for name in (*CAPABILITIES, *HOST_OPERATIONS):
        namespace, _, operation = name.partition(".")
        assert namespace and operation, name
        assert name == name.lower(), name


def test_the_known_names_line_is_what_the_error_message_quotes():
    assert capability_names() == "model.invoke, repo.propose_patch, repo.read, tool.test"
