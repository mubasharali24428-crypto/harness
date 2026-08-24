"""M12-W2.2 lane G7 — run-terminal emit hooks (Section 4 side).

Verifies that every path that settles a run emits a bus event a notification
consumer can key on:

* engine completion / engine failure already emit ENGINE_COMPLETED /
  ENGINE_FAILED via the state machine (Wave 6 D4 — regression-pinned here);
* user stop previously reached DONE silently; it now also emits a PAUSED
  envelope with reason=user_requested_stop so downstream email rules see a
  truthful "stopped, not crashed" signal;
* emissions are best-effort: a broken bus/callback must never break stopping.

These tests use only sync callbacks (on_event) — no DB required.
"""

import pytest
from harness_contracts.events import EventType

from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState


def _service(events: list) -> OrchestratorService:
    return OrchestratorService(
        on_event=lambda etype, payload: events.append((etype, payload)),
    )


async def _executing_run(service: OrchestratorService, run_id: str, project: str = "proj-1"):
    """Put a run into EXECUTING the cheap way (no live engine task): rehydration
    sets ``sm.state`` directly too. Pause/stop semantics don't depend on how the
    state was reached — and these lanes own emission behavior, not planning."""
    await service._ensure_run(run_id, project_id=project)
    run_data = service.runs[run_id]
    run_data["state_machine"].state = AgentState.EXECUTING
    return run_data


# ------------------------------------------------- existing terminal emits


@pytest.mark.asyncio
async def test_engine_failure_emits_engine_failed_event():
    sm_state = {"value": None}

    def capture(etype, _payload):
        sm_state["value"] = etype

    from harness_orchestrator.state_machine import AgentStateMachine

    sm = AgentStateMachine(run_id="r1", on_event=capture)
    sm.start_planning()
    sm.plan_proposed("p1")
    sm.approve_plan("p1", "u")
    sm.start_execution()
    sm.execution_failed("engine exploded")
    assert sm_state["value"] is EventType.ENGINE_FAILED


@pytest.mark.asyncio
async def test_engine_completion_emits_engine_completed_event():
    events: list = []

    from harness_orchestrator.state_machine import AgentStateMachine

    sm = AgentStateMachine(run_id="r2", on_event=lambda t, p: events.append((t, p)))
    sm.start_planning()
    sm.plan_proposed("p1")
    sm.approve_plan("p1", "u")
    sm.start_execution()
    sm.execution_completed("all steps done")
    assert any(t is EventType.ENGINE_COMPLETED for t, _ in events)


# ------------------------------------------------------- G7 stop-path emit


@pytest.mark.asyncio
async def test_stop_emits_paused_event_with_stop_reason():
    """G7: stopping a run is observable on the bus (raw-13 #14 coverage for
    the one terminal transition that used to be silent)."""
    events: list = []
    service = _service(events)
    await _executing_run(service, "run-stop")
    await service.stop_agent("run-stop")

    paused = [(t, p) for t, p in events if t is EventType.PAUSED]
    assert paused, "stop_agent must emit a PAUSED envelope"
    _, payload = paused[-1]
    assert payload["runId"] == "run-stop"
    assert payload["reason"] == "user_requested_stop"
    # The machine itself still lands on DONE (terminal hygiene untouched).
    assert service.runs["run-stop"]["state_machine"].state is AgentState.DONE


@pytest.mark.asyncio
async def test_stop_emit_never_breaks_when_callback_explodes():
    """Email/notification failures must never throw into the run path."""
    def _boom(_etype, _payload):
        raise RuntimeError("notification sink down")

    service = OrchestratorService(on_event=_boom)
    await _executing_run(service, "run-boom")
    await service.stop_agent("run-boom")  # must not raise
    assert service.runs["run-boom"]["state_machine"].state is AgentState.DONE


@pytest.mark.asyncio
async def test_pause_reason_stays_distinct_from_user_stop():
    """A pause keeps reason=user_requested; only stop claims the stop reason,
    so consumers can tell 'will resume' from 'run over'."""
    events: list = []
    service = _service(events)
    await _executing_run(service, "run-pause")
    await service.pause_agent("run-pause")
    await service.stop_agent("run-pause")

    reasons = [p["reason"] for t, p in events if t is EventType.PAUSED]
    assert reasons == ["user_requested", "user_requested_stop"]
