"""F071/F076/F077: user-configurable specialists with per-agent model and tool policy."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .errors import RuntimeDenied


@dataclass(frozen=True)
class SpecialistSpec:
    name: str
    instructions: str
    model: str  # F076: explicit; never silently substituted
    allowed_tools: frozenset[str]  # F077: default-deny allowlist
    max_context_tokens: int = 8000


class SpecialistRegistry:
    """Team-scoped. ``allowed_models``/``tool_catalog`` come from the team's policy
    (A1 model policy / S5 tool catalog) — this registry only enforces them."""

    def __init__(self, allowed_models: Iterable[str], tool_catalog: Iterable[str]) -> None:
        self.allowed_models = frozenset(allowed_models)
        self.tool_catalog = frozenset(tool_catalog)
        self._by_team: dict[str, dict[str, SpecialistSpec]] = {}

    def register(self, team_id: str, spec: SpecialistSpec) -> None:
        if spec.model not in self.allowed_models:
            raise RuntimeDenied("specialist.model_not_allowed", spec.model)
        unknown = spec.allowed_tools - self.tool_catalog
        if unknown:
            raise RuntimeDenied("specialist.unknown_tool", ",".join(sorted(unknown)))
        if not spec.name.strip() or not 256 <= spec.max_context_tokens <= 200_000:
            raise RuntimeDenied("specialist.invalid")
        self._by_team.setdefault(team_id, {})[spec.name] = spec

    def get(self, team_id: str, name: str) -> SpecialistSpec:
        try:
            return self._by_team[team_id][name]
        except KeyError:
            raise RuntimeDenied("specialist.not_found", name) from None  # no cross-team oracle

    def names(self, team_id: str) -> list[str]:
        return sorted(self._by_team.get(team_id, {}))

    def authorize_tool(self, team_id: str, name: str, tool: str) -> None:
        if tool not in self.get(team_id, name).allowed_tools:
            raise RuntimeDenied("tool.denied", f"{name} may not use {tool}")
