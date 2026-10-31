"""Manifest tests: the hand-off to the Harness is a request, and reads as one.

The manifest is the only artefact a host consumes, so these tests pin the parts
policy depends on: pinned versions, a content digest, typed budgets, the closed
capability names, and the absence of any field that could be mistaken for a
grant.
"""

from __future__ import annotations

import hashlib
import json

from harness_hx import build_manifest, check_source
from support import FIXTURES, check

SOURCE = (FIXTURES / "valid" / "fix_issue.hx").read_text(encoding="utf-8")


def manifest(source: str = SOURCE, path: str = "fix_issue.hx") -> dict[str, object]:
    result = check_source(source, path, "agent")
    assert result.ok, [record.code for record in result.diagnostics]
    return build_manifest(result, "agent")


def test_the_manifest_names_the_language_and_pins_both_versions():
    payload = manifest()
    assert payload["language"] == "hx"
    assert payload["spec_version"] == "0.3.0"
    assert payload["toolchain"] == {"name": "hxc", "version": "0.3.0"}


def test_the_source_digest_is_content_addressed():
    payload = manifest()
    assert payload["source"]["sha256"] == hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
    assert payload["source"]["bytes"] == len(SOURCE.encode("utf-8"))
    assert payload["source"]["path"] == "fix_issue.hx"


def test_the_profile_is_recorded_as_chosen_by_the_host():
    assert manifest()["profile"] == "agent"


def test_the_module_name_is_kept_for_review():
    assert manifest()["module"] == "maintenance"


def test_capabilities_are_listed_once_and_sorted():
    workflow = manifest()["workflows"][0]
    assert workflow["requires"] == [
        "model.invoke",
        "repo.propose_patch",
        "repo.read",
        "tool.test",
    ]
    assert manifest()["policy_request"]["requested_capabilities"] == workflow["requires"]


def test_each_request_pairs_the_capability_with_the_operation_that_uses_it():
    requests = manifest()["workflows"][0]["requests"]
    assert requests == [
        {"capability": "model.invoke", "operation": "developer.plan"},
        {"capability": "repo.propose_patch", "operation": "repo.propose_patch"},
        {"capability": "repo.read", "operation": "repo.read"},
        {"capability": "tool.test", "operation": "verify.tests"},
    ]


def test_the_pure_operation_is_not_a_request():
    operations = [item["operation"] for item in manifest()["workflows"][0]["requests"]]
    assert "review.bundle" not in operations


def test_budgets_are_typed_not_stringly():
    budget = manifest()["workflows"][0]["budget"]
    assert budget == {
        "steps": 200,
        "tool_calls": 12,
        "tasks": 4,
        "duration": "10m",
        "cost": "2.00 USD",
    }
    assert isinstance(budget["steps"], int)
    assert isinstance(budget["duration"], str)


def test_a_workflow_without_a_budget_carries_null_rather_than_a_default():
    source = (FIXTURES / "warnings" / "no_budget.hx").read_text(encoding="utf-8")
    payload = build_manifest(check(source), "agent")
    assert payload["workflows"][0]["budget"] is None


def test_top_level_agents_are_listed_with_their_settings():
    source = (
        "schema P {\n  text: String\n}\n"
        "agent reviewer: Agent<P> {\n"
        '  role: "reviewer"\n'
        '  model: "default"\n'
        "}\n"
    )
    payload = build_manifest(check(source), "agent")
    assert payload["agents"] == [
        {"name": "reviewer", "type": "Agent<P>", "settings": ["model", "role"]}
    ]


def test_the_policy_request_cannot_be_mistaken_for_a_grant():
    request = manifest()["policy_request"]
    assert request["authority"] == "harness"
    assert request["grants"] == "none"
    assert request["forgeable"] is False
    assert request["approval"] == "required-for-review"


def test_the_permission_ceiling_speaks_harness_vocabulary():
    ceiling = manifest()["policy_request"]["permission_ceiling"]
    assert ceiling == {
        "model.invoke": "read",
        "repo.propose_patch": "comment",
        "repo.read": "read",
        "tool.test": "read",
    }
    assert "approve" not in ceiling.values()


def test_the_top_level_keys_are_the_reviewed_set():
    assert sorted(manifest()) == [
        "agents",
        "language",
        "module",
        "policy_request",
        "profile",
        "source",
        "spec_version",
        "toolchain",
        "workflows",
    ]


def test_the_rendered_manifest_is_stable_and_json():
    result = check_source(SOURCE, "fix_issue.hx", "agent")
    first = build_manifest(result, "agent")
    second = build_manifest(result, "agent")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.loads(json.dumps(first))["language"] == "hx"
