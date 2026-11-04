"""Authorisation tests: the request is a request, and the host decides.

Three failure kinds are pinned here because they are easy to conflate and mean
different things: not in the table (corrupt request), not granted (policy), and
the principal lacks the permission (identity). Plus the digest binding, which is
what makes "we ran the program we checked" a property rather than a promise.
"""

from __future__ import annotations

import pytest
from harness_contracts.policy_engine import PERMISSIONS

from harness_hx import check_source
from harness_hx.capabilities import CAPABILITIES
from harness_hx.manifest import build_manifest
from harness_hx_host.authority import (
    HUMAN_ONLY,
    assert_checked_source,
    authorize,
    source_digest,
)
from harness_hx_host.errors import DigestMismatch
from support import FIXTURES

SOURCE = (FIXTURES / "valid" / "fix_issue.hx").read_text(encoding="utf-8")
ALL = ("repo.read", "repo.propose_patch", "tool.test", "model.invoke")


def manifest(source: str = SOURCE) -> dict[str, object]:
    result = check_source(source, "fix_issue.hx", "agent")
    assert result.ok, [record.code for record in result.diagnostics]
    return build_manifest(result, "agent")


def test_an_empty_grant_list_grants_nothing():
    decision = authorize(manifest(), granted=())
    assert decision.allowed is False
    assert decision.granted == frozenset()
    assert {denial.reason for denial in decision.denials} == {
        "requested but not granted by this host"
    }


def test_granting_every_requested_capability_allows_the_run():
    decision = authorize(manifest(), granted=ALL)
    assert decision.allowed is True
    assert decision.granted == frozenset(ALL)
    assert decision.denials == ()


def test_the_permission_mapping_is_enforced_for_the_principal():
    # repo.propose_patch maps to "comment"; a read-only principal may not hold it.
    decision = authorize(manifest(), granted=ALL, principal_permissions=["read"])
    assert decision.allowed is False
    denied = {denial.capability: denial for denial in decision.denials}
    assert set(denied) == {"repo.propose_patch"}
    assert "comment" in denied["repo.propose_patch"].reason


def test_no_capability_maps_to_a_human_only_permission():
    assert HUMAN_ONLY == {"approve", "administer", "billing", "export"}
    assert {entry.permission for entry in CAPABILITIES.values()} & HUMAN_ONLY == set()


def test_the_drivers_permission_vocabulary_is_the_contracts():
    # contracts/ is frozen. If a permission name changes there, this fails
    # before any section's bridge test can.
    used = {entry.permission for entry in CAPABILITIES.values()}
    assert used <= set(PERMISSIONS)
    assert HUMAN_ONLY <= set(PERMISSIONS)
    decision = authorize(manifest(), granted=ALL, principal_permissions=PERMISSIONS)
    assert decision.allowed is True


def test_an_unknown_capability_is_refused_as_a_corrupt_request():
    payload = manifest()
    payload["workflows"][0]["requires"] = ["repo.read", "root.everything"]
    decision = authorize(payload, granted=["repo.read", "root.everything"])
    reasons = {denial.capability: denial.reason for denial in decision.denials}
    assert reasons == {"root.everything": "not in the closed capability table"}


def test_a_pure_operation_needs_no_grant():
    decision = authorize(manifest(), granted=())
    assert decision.allows(None) is True
    assert decision.allows("repo.read") is False


def test_the_checked_source_is_the_executed_source():
    payload = manifest()
    assert assert_checked_source(payload, SOURCE) == source_digest(SOURCE)
    with pytest.raises(DigestMismatch):
        assert_checked_source(payload, SOURCE + "\n// swapped after checking\n")


def test_the_decision_serialises_as_a_non_forgeable_record():
    decision = authorize(manifest(), granted=ALL, principal="user:alice", policy="team-acme")
    payload = decision.to_json()
    assert payload["authority"] == "harness"
    assert payload["forgeable"] is False
    assert payload["allowed"] is True
    assert payload["principal"] == "user:alice"
    assert payload["granted"] == sorted(ALL)
