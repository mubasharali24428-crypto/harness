"""F074: swarm mode — run a task graph's ready layers concurrently through
independent contexts, per-specialist policy, and the shared board."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .board import TaskBoard
from .context import ContextManager
from .graph import TaskGraph
from .specialists import SpecialistRegistry

Executor = Callable[[str, str, list[str]], Awaitable[Any]]  # (specialist, goal, context)


async def run_swarm(
    graph: TaskGraph,
    *,
    team_id: str,
    registry: SpecialistRegistry,
    board: TaskBoard,
    executor: Executor,
    ctx: ContextManager,
    max_parallel: int = 4,
) -> dict[str, dict[str, Any]]:
    cards = {i: board.create(team_id, n.title, "swarm") for i, n in graph.nodes.items()}
    results: dict[str, dict[str, Any]] = {}
    sem = asyncio.Semaphore(max_parallel)
    shared: dict[str, str] = {}

    async def one(node_id: str) -> None:
        node = graph.nodes[node_id]
        card = cards[node_id]
        spec = registry.get(team_id, node.role)
        board.assign(team_id, card.id, f"agent:{spec.name}", "swarm")
        board.move(team_id, card.id, "in_progress", "swarm", expect_version=card.version)
        window = ctx.seed(node_id, spec.max_context_tokens, shared, node.inputs)
        async with sem:
            try:
                out = await executor(spec.name, node.title, window.render())
            except Exception as exc:  # noqa: BLE001 — a node failure is a result, not a crash
                board.move(team_id, card.id, "failed", "swarm", expect_version=card.version)
                board.escalate(team_id, card.id, f"{type(exc).__name__}", "swarm")
                results[node_id] = {"status": "failed", "error": type(exc).__name__}
                return
        shared[node_id] = str(out)
        board.move(team_id, card.id, "done", "swarm", expect_version=card.version)
        results[node_id] = {"status": "done", "output": out}

    for layer in graph.layers():
        runnable = []
        for nid in layer:
            if any(results.get(d, {}).get("status") != "done" for d in graph.nodes[nid].deps):
                results[nid] = {"status": "skipped", "reason": "dependency_failed"}
            else:
                runnable.append(nid)
        await asyncio.gather(*(one(n) for n in runnable))
    return results
