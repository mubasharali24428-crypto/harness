"""W17: a transient bus failure must not erase a run's terminal event.

Found by the dry-engine load ladder: one run in ~2,300 had execution_started and
model_routed in the durable log, no engine_completed/engine_failed, and no claim
row, so the client saw "executing" until its own timeout. ``_publish_safe``
swallows every exception ("delivery must never break orchestration") and the
claim is released regardless, so a single failed terminal write is unrecoverable.

Fix: ENGINE_COMPLETED / ENGINE_FAILED retry with a bounded number of attempts
and backoff; non-terminal publishes stay best-effort. A publish that exhausts
retries still never raises into the run.
"""

import pytest
from harness_contracts import EventType

from harness_orchestrator.service import OrchestratorService


class _FlakyBus:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.published: list[str] = []

    async def publish(self, event_type, project_id, payload, run_id):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("pool timeout")
        self.published.append(event_type.value)


class _Stub:
    def __init__(self, bus) -> None:
        self.bus = bus


@pytest.mark.parametrize("failures", [1, 2, 3])
@pytest.mark.parametrize("event", [EventType.ENGINE_COMPLETED, EventType.ENGINE_FAILED])
async def test_terminal_event_survives_transient_publish_failures(event, failures):
    bus = _FlakyBus(failures=failures)
    await OrchestratorService._publish_safe(_Stub(bus), event, "p1", {"runId": "r1"}, "r1")
    assert bus.published == [event.value]


@pytest.mark.parametrize("event", [EventType.ENGINE_COMPLETED, EventType.ENGINE_FAILED])
async def test_terminal_publish_retries_bounded_then_never_raises(event):
    """Negative control: an always-failing bus is retried a bounded number of
    times and the failure still does not escape into the run."""
    calls: list[str] = []

    class _DeadBus:
        async def publish(self, event_type, project_id, payload, run_id):
            calls.append(event_type.value)
            raise ConnectionError("bus down")

    stub = _Stub(_DeadBus())
    await OrchestratorService._publish_safe(stub, event, "p1", {"runId": "r1"}, "r1")
    assert calls == [event.value] * OrchestratorService._TERMINAL_PUBLISH_ATTEMPTS


async def test_a_non_terminal_event_may_still_be_dropped_without_breaking_the_run():
    bus = _FlakyBus(failures=1)
    await OrchestratorService._publish_safe(_Stub(bus), EventType.TOOL_CALL, "p1", {}, "r1")
    assert bus.published == []  # best-effort by design; only terminal events need a retry
