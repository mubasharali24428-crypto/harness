"""W4: the egress allow-list is data, and the data comes from the provider registry.

Before this file, ``egress.py`` carried three hard-coded git hosts and a
hand-written registry list in ``config.py``. The roadmap requires the list to be
*derived from* the provider registry instead, so these tests pin three things:

1. the checked-in allow-list equals a fresh derivation from
   ``section3/src/harness_router/provider_registry.py`` (drift is a failing test,
   not a code review miss);
2. the derivation refuses hosts that are declared-but-not-real placeholders or
   loopback, so "derived" never silently means "reachable";
3. a destination decision is purpose-scoped — docs are reachable by a web tool,
   not by a sandboxed shell — and an unknown purpose fails closed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from harness_sandbox import egress
from harness_sandbox.egress import (
    PURPOSE_ANY,
    PURPOSE_SANDBOX,
    PURPOSE_WEB,
    EgressDecision,
    egress_decision,
    is_allowed_destination,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GENERATOR = _REPO_ROOT / "scripts" / "gen_egress_allowlist.py"
_ALLOWLIST = Path(egress.__file__).with_name("egress_allowlist.json")


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_egress_allowlist", _GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_allowlist_matches_a_fresh_derivation():
    generator = _load_generator()
    expected = generator.build()
    actual = json.loads(_ALLOWLIST.read_text(encoding="utf-8"))
    assert actual == expected, (
        "section5/src/harness_sandbox/egress_allowlist.json is stale. "
        "Regenerate it: python3 scripts/gen_egress_allowlist.py"
    )


def test_generated_groups_are_sorted_and_unique():
    data = json.loads(_ALLOWLIST.read_text(encoding="utf-8"))
    for group in ("providerEndpoints", "providerDocs", "packageRegistries", "gitHosts"):
        hosts = data[group]
        assert hosts == sorted(set(hosts)), f"{group} must be sorted and duplicate-free"
        assert all(h == h.lower().strip() for h in hosts)


def test_a_registry_endpoint_is_reachable_from_the_sandbox():
    assert is_allowed_destination("https://api.anthropic.com/v1/messages") is True
    assert is_allowed_destination("api.deepseek.com") is True


def test_docs_are_a_web_destination_not_a_sandbox_one():
    # "the allow-list must be derived from the provider registry" must not mean
    # a sandboxed shell can open a tunnel to every docs site we link to.
    assert is_allowed_destination("https://api-docs.deepseek.com/en/docs", PURPOSE_WEB) is True
    assert is_allowed_destination("https://api-docs.deepseek.com/en/docs", PURPOSE_SANDBOX) is False
    assert is_allowed_destination("https://api-docs.deepseek.com/en/docs") is False


def test_reviewed_git_and_package_hosts_survive_the_derivation():
    for host in ("github.com", "api.github.com", "gitlab.com", "pypi.org", "registry.npmjs.org"):
        assert is_allowed_destination(host, PURPOSE_SANDBOX) is True, host


def test_placeholders_and_loopback_are_never_reachable():
    for bad in (
        "replace-with-your-resource.openai.azure.com",
        "replace-with-your-account.snowflakecomputing.com",
        "replace-with-your-workspace.ai",
        "127.0.0.1",
        "localhost",
        "0.0.0.0",
        "::1",
    ):
        for purpose in (PURPOSE_SANDBOX, PURPOSE_WEB, PURPOSE_ANY):
            assert is_allowed_destination(bad, purpose) is False, (bad, purpose)


def test_matching_is_exact_not_suffixed():
    assert is_allowed_destination("api.anthropic.com.evil.test", PURPOSE_ANY) is False
    assert is_allowed_destination("evil.test", PURPOSE_ANY) is False


def test_decision_explains_which_group_allowed_it():
    allowed = egress_decision("https://api-docs.deepseek.com/en/docs", PURPOSE_WEB)
    assert isinstance(allowed, EgressDecision)
    assert allowed.allowed is True
    assert allowed.host == "api-docs.deepseek.com"
    assert "providerDocs" in allowed.reason

    denied = egress_decision("https://api-docs.deepseek.com/en/docs", PURPOSE_SANDBOX)
    assert denied.allowed is False
    assert "not in" in denied.reason


def test_unknown_purpose_fails_closed():
    with pytest.raises(ValueError):
        is_allowed_destination("github.com", "smuggle")
