"""Wave 6 (D3): per-run publish serialization.

Live evidence (smoke-run-1, 2026-08-22): approve→start in rapid succession
published execution_started (seq 37) BEFORE plan_approved (seq 38) — the
fire-and-forget publish tasks raced. The bus seq must match causal order,
so each run's publishes now chain: task N+1 awaits task N.
"""

import asyncio

from harness_contracts.events import EventType

from harness_orchestrator.service import OrchestratorService


class RecordingBus:
    """Duck-typed EventBus that records publish order and concurrency."""

    def __init__(self, delay: float = 0.01) -> None:
        self.published: list[str] = []
        self.inflight = 0
        self.max_inflight = 0
        self._delay = delay

    async def publish(self, event_type, project_id, payload, run_id) -> None:
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        await asyncio.sleep(self._delay)  # widen the race window
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}")
        self.inflight -= 1

    async def ensure_run(self, project_id, run_id) -> None:  # FK seed hook
        return None


async def _drain(svc: OrchestratorService) -> None:
    if svc._bg_tasks:
        await asyncio.gather(*list(svc._bg_tasks))


async def test_same_run_publishes_serialize_in_call_order():
    bus = RecordingBus()
    svc = OrchestratorService(bus=bus)
    await svc.propose_plan("run-order", goal="g", project_id="proj-1")  # plan_proposed

    for i in range(6):
        svc._emit(EventType.PAUSED, {"runId": "run-order", "n": i})
    await _drain(svc)

    names = [p.split(":", 1)[1] for p in bus.published]
    assert names == ["plan_proposed"] + ["paused"] * 6
    # Serialization proof: two same-run publishes never overlapped in flight.
    assert bus.max_inflight == 1


async def test_cross_run_delivery_is_independent_but_complete():
    """Chains are per-run: a slow run must not starve another run's events."""
    bus = RecordingBus(delay=0.02)
    svc = OrchestratorService(bus=bus)
    await svc.propose_plan("run-c", goal="g", project_id="proj-1")
    await svc.propose_plan("run-d", goal="g", project_id="proj-1")

    svc._emit(EventType.PAUSED, {"runId": "run-c"})
    svc._emit(EventType.RESUMED, {"runId": "run-d"})
    svc._emit(EventType.PAUSED, {"runId": "run-d"})
    await _drain(svc)

    by_run = {
        r: [p.split(":", 1)[1] for p in bus.published if p.startswith(r)]
        for r in ("run-c", "run-d")
    }
    assert by_run["run-c"] == ["plan_proposed", "paused"]
    assert by_run["run-d"] == ["plan_proposed", "resumed", "paused"]
