"""Team tool policy: allow/deny patterns enforced before any external call.

Semantics (deny-by-default ethos, no silent surprises):
  * deny rules always win — exact name or ``prefix*``;
  * if ANY allow rules exist the team is in allowlist mode: only tools
    matching an allow pattern may be called;
  * no rules = no policy (grants remain the only gate).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ToolPolicyRule
from .store import Store


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None  # "denied_by_policy" | "not_in_allowlist"
    pattern: str | None = None  # the rule that decided, when one did


def _matches(pattern: str, tool_name: str) -> bool:
    if pattern.endswith("*"):
        return tool_name.startswith(pattern[:-1])
    return tool_name == pattern


def evaluate_policy(rules: list[ToolPolicyRule], tool_name: str) -> PolicyDecision:
    for rule in rules:
        if rule.mode == "deny" and _matches(rule.pattern, tool_name):
            return PolicyDecision(allowed=False, reason="denied_by_policy", pattern=rule.pattern)
    allows = [r for r in rules if r.mode == "allow"]
    if not allows:
        return PolicyDecision(allowed=True)
    for rule in allows:
        if _matches(rule.pattern, tool_name):
            return PolicyDecision(allowed=True, pattern=rule.pattern)
    return PolicyDecision(allowed=False, reason="not_in_allowlist")


def check_tool_policy(store: Store, *, team_id: str, tool_name: str) -> PolicyDecision:
    return evaluate_policy(store.list_policy_rules(team_id), tool_name)
