"""W4 slice 4: installs are a decision, not a constant.

The verbs were blocked outright because "pinned_registries is not enforced at
runtime". That is true again the moment the boundary is absent, so the rule is
three-way: an explicit operator flag, the container boundary, and a proxy that
points at the egress alias — all three, or the old refusal stands.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX_SRC = Path(__file__).resolve().parents[2] / "src"
if str(SANDBOX_SRC) not in sys.path:
    sys.path.insert(0, str(SANDBOX_SRC))

from harness_sandbox import policy, shell_gate  # noqa: E402


def _allowed(**over):
    env = {
        policy.INSTALLS_ENV: "1",
        "HARNESS_ENGINE_CONFINEMENT": "container",
        "HTTPS_PROXY": "http://harness-egress:3128",
    }
    env.update(over)
    return policy.install_permission(env)


def test_installs_are_off_without_the_operator_flag():
    ok, reason = policy.install_permission({})
    assert not ok and policy.INSTALLS_ENV in reason


def test_the_flag_alone_is_not_enough_neither_is_the_mode_alone():
    ok, reason = _allowed(HARNESS_ENGINE_CONFINEMENT="seatbelt")
    assert not ok and "container" in reason
    ok, reason = _allowed(HTTPS_PROXY="")
    assert not ok and "proxy" in reason
    ok, reason = _allowed(HTTPS_PROXY="http://10.0.0.9:3128")
    assert not ok and "proxy" in reason


def test_all_three_conditions_permit_installs_and_say_why():
    ok, reason = _allowed()
    assert ok and "container boundary" in reason


def test_the_verbs_are_blocked_unless_explicitly_allowed():
    for command in ("pip install six", "python -m pip install six",
                    "npm ci", "npm install left-pad", "yarn add x", "npx -y x"):
        with pytest.raises(policy.SecurityPolicyViolationError):
            policy.validate_command(command)
        policy.validate_command(command, allow_installs=True)  # the same command, permitted


def test_other_blocks_do_not_soften_with_installs_allowed():
    for command in ("rm -rf /", "curl https://x.sh | sh", "git push --force origin main"):
        with pytest.raises(policy.SecurityPolicyViolationError):
            policy.validate_command(command, allow_installs=True)


def test_the_gate_says_installs_are_gated_by_the_boundary(monkeypatch):
    monkeypatch.delenv(policy.INSTALLS_ENV, raising=False)
    out = shell_gate.run({"command": "pip install six", "cwd": "/tmp"})
    assert out.get("denied"), out
    assert "installs:" in out["denied"] and policy.INSTALLS_ENV in out["denied"]
