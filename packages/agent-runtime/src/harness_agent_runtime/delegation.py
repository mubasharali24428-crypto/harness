"""F072: agent-to-agent delegation under explicit, non-escalating contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import RuntimeDenied
from .specialists import SpecialistRegistry

MAX_DEPTH = 3
MAX_FANOUT = 8


@dataclass(frozen=True)
class DelegationContract:
    parent: str
    child: str
    task_id: str
    goal: str
    tools: frozenset[str]
    token_budget: int
    depth: int


@dataclass
class Delegator:
    registry: SpecialistRegistry
    team_id: str
    root_budget: int
    _spent: dict[str, int] = field(default_factory=dict)
    _fanout: dict[str, int] = field(default_factory=dict)
    _chain: dict[str, tuple[str, ...]] = field(default_factory=dict)  # agent -> ancestry
    _budget: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._budget["root"] = self.root_budget

    def delegate(
        self, parent: str, child: str, task_id: str, goal: str, tools: set[str], budget: int
    ) -> DelegationContract:
        child_spec = self.registry.get(self.team_id, child)
        parent_tools = (
            child_spec.allowed_tools
            if parent == "root"
            else self.registry.get(self.team_id, parent).allowed_tools
        )
        ancestry = self._chain.get(parent, ()) + ((parent,) if parent != "root" else ())
        if child in ancestry or child == parent:
            raise RuntimeDenied("delegation.cycle", child)
        if len(ancestry) + 1 > MAX_DEPTH:
            raise RuntimeDenied("delegation.too_deep")
        if self._fanout.get(parent, 0) >= MAX_FANOUT:
            raise RuntimeDenied("delegation.fanout")
        if parent != "root" and not set(tools) <= parent_tools:
            raise RuntimeDenied("delegation.tool_escalation")
        if frozenset(tools) - child_spec.allowed_tools:
            raise RuntimeDenied("delegation.tool_not_in_child_policy")
        available = self._budget.get(parent, 0)
        if budget <= 0 or budget > available:
            raise RuntimeDenied("delegation.budget")
        self._budget[parent] = available - budget  # conserved: carved out of parent
        self._budget[child] = self._budget.get(child, 0) + budget
        self._fanout[parent] = self._fanout.get(parent, 0) + 1
        self._chain[child] = ancestry
        return DelegationContract(
            parent, child, task_id, goal, frozenset(tools), budget, len(ancestry) + 1
        )

    def total_budget(self) -> int:
        return sum(self._budget.values())
