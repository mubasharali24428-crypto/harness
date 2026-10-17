"""F030: delegating task-graph planner (validated DAG + deterministic decomposition)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import RuntimeDenied

MAX_NODES = 50


@dataclass(frozen=True)
class TaskNode:
    id: str
    title: str
    role: str = "generalist"
    deps: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()  # the only context keys this node may read


@dataclass
class TaskGraph:
    nodes: dict[str, TaskNode] = field(default_factory=dict)

    @classmethod
    def build(cls, nodes: list[TaskNode]) -> TaskGraph:
        if not nodes or len(nodes) > MAX_NODES:
            raise RuntimeDenied("graph.size", f"1..{MAX_NODES} nodes required")
        by_id: dict[str, TaskNode] = {}
        for n in nodes:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", n.id) or n.id in by_id:
                raise RuntimeDenied("graph.bad_id", n.id)
            by_id[n.id] = n
        for n in nodes:
            for d in n.deps:
                if d not in by_id or d == n.id:
                    raise RuntimeDenied("graph.bad_dependency", f"{n.id}->{d}")
        g = cls(by_id)
        g.layers()  # raises on a cycle
        return g

    def layers(self) -> list[list[str]]:
        """Kahn layers: every layer's nodes are independent and may run in parallel."""
        remaining = {i: set(n.deps) for i, n in self.nodes.items()}
        out: list[list[str]] = []
        while remaining:
            ready = sorted(i for i, d in remaining.items() if not d)
            if not ready:
                raise RuntimeDenied("graph.cycle", ",".join(sorted(remaining)))
            out.append(ready)
            for i in ready:
                del remaining[i]
            for d in remaining.values():
                d.difference_update(ready)
        return out

    def descendants(self, node_id: str) -> set[str]:
        out: set[str] = set()
        frontier = {node_id}
        while frontier:
            nxt = {i for i, n in self.nodes.items() if frontier & set(n.deps)} - out
            out |= nxt
            frontier = nxt
        return out


_SPLIT = re.compile(r"\s*(?:;|\n|\d+[.)]\s+|\bthen\b|\band then\b)\s*", re.I)


def decompose(goal: str, role_keywords: dict[str, tuple[str, ...]]) -> TaskGraph:
    """Deterministic planner: clauses become nodes, routed to the specialist whose
    keywords match. ``then`` / numbering imply order; ``;`` and lines are independent."""
    parts = [p.strip() for p in _SPLIT.split(goal or "") if p and p.strip()]
    ordered = bool(re.search(r"\bthen\b|^\s*\d+[.)]", goal or "", re.I | re.M))
    nodes: list[TaskNode] = []
    for i, text in enumerate(parts[:MAX_NODES], 1):
        role = next(
            (r for r, kws in role_keywords.items() if any(k in text.lower() for k in kws)),
            "generalist",
        )
        deps = (f"t{i - 1}",) if ordered and i > 1 else ()
        nodes.append(TaskNode(f"t{i}", text, role, deps))
    return TaskGraph.build(nodes)
