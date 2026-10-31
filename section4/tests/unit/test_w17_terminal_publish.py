"""W17: a transient bus failure must not erase a run's terminal event.

Found by the dry-engine load ladder: one run in ~2,300 had execution_started and
model_routed in the durable log, no engine_completed/engine_failed, and no claim
row, so the client saw "executing" until its own timeout. ``_publish_safe``
swallows every exception ("delivery must never break orchestration") and the
claim is released regardless, so a single failed terminal write is unrecoverable.

Strict xfail: it documents the demonstrated gap without changing service.py, which
carries another lane's uncommitted work. Delete the marker when the retry lands.
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


@pytest.mark.xfail(
    strict=True, reason="W17: terminal event dropped after one transient publish failure"
)
@pytest.mark.parametrize("event", [EventType.ENGINE_COMPLETED, EventType.ENGINE_FAILED])
async def test_terminal_event_survives_one_transient_publish_failure(event):
    bus = _FlakyBus(failures=1)
    await OrchestratorService._publish_safe(_Stub(bus), event, "p1", {"runId": "r1"}, "r1")
    assert bus.published == [event.value]


async def test_a_non_terminal_event_may_still_be_dropped_without_breaking_the_run():
    bus = _FlakyBus(failures=1)
    await OrchestratorService._publish_safe(_Stub(bus), EventType.TOOL_CALL, "p1", {}, "r1")
    assert bus.published == []  # best-effort by design; only terminal events need a retry
