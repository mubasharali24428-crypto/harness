import pytest
from harness_contracts.events import EventType

from harness_orchestrator.state_machine import (
    AgentState,
    AgentStateMachine,
    InvalidStateTransitionError,
)


def test_state_machine_standard_lifecycle():
    events = []

    def capture_event(ev_type, payload):
        events.append((ev_type, payload))

    sm = AgentStateMachine(run_id="run-sm-001", on_event=capture_event)
    assert sm.state == AgentState.IDLE

    # 1. Propose plan
    sm.start_planning()
    assert sm.state == AgentState.PLANNING

    sm.plan_proposed("plan-001")
    assert sm.state == AgentState.AWAITING_PLAN_APPROVAL
    assert events[0][0] == EventType.PLAN_PROPOSED

    # 2. Cannot start execution before approval
    with pytest.raises(InvalidStateTransitionError):
        sm.start_execution()

    # 3. Approve plan
    sm.approve_plan("plan-001", "user-1")
    assert sm.plan_approved is True
    assert events[1][0] == EventType.PLAN_APPROVED

    # 4. Start execution
    sm.start_execution()
    assert sm.state == AgentState.EXECUTING
    assert events[2][0] == EventType.EXECUTION_STARTED

    # 5. Pause & Resume
    sm.pause()
    assert sm.state == AgentState.PAUSED
    assert events[3][0] == EventType.PAUSED

    sm.resume()
    assert sm.state == AgentState.EXECUTING
    assert events[4][0] == EventType.RESUMED


def test_pause_on_human_edit():
    events = []
    sm = AgentStateMachine(run_id="run-edit-001", on_event=lambda t, p: events.append((t, p)))

    sm.plan_approved = True
    sm.transition_to(AgentState.EXECUTING)

    # Human starts editing
    sm.submit_human_edit("src/main.py")
    assert sm.state == AgentState.PAUSED
    assert sm.is_human_editing is True
    assert events[-1][0] == EventType.PAUSED
    assert events[-1][1]["reason"] == "human_edit"


# ----------------------------------------------------------------------
# Wave 6 (D4): engine lifecycle — completion opens the done-gate, failure
# blocks, and both are no-ops outside EXECUTING (controls own the narrative).
# ----------------------------------------------------------------------


def _executing_sm(events: list) -> AgentStateMachine:
    sm = AgentStateMachine(run_id="run-life", on_event=lambda t, p: events.append((t, p)))
    sm.plan_approved = True
    sm.transition_to(AgentState.EXECUTING)
    return sm


def test_engine_completion_opens_done_gate():
    events = []
    sm = _executing_sm(events)
    sm.execution_completed("all steps ran")
    assert sm.state == AgentState.AWAITING_DONE_SIGNAL
    assert events[-1][0] == EventType.ENGINE_COMPLETED
    assert events[-1][1]["summary"] == "all steps ran"
    assert events[-1][1]["runId"] == "run-life"


def test_engine_failure_blocks_run():
    events = []
    sm = _executing_sm(events)
    sm.execution_failed("model ladder exhausted")
    assert sm.state == AgentState.BLOCKED
    assert events[-1][0] == EventType.ENGINE_FAILED
    assert events[-1][1]["reason"] == "model ladder exhausted"


def test_lifecycle_events_are_noop_outside_executing():
    events = []
    sm = AgentStateMachine(run_id="run-idle", on_event=lambda t, p: events.append((t, p)))
    sm.execution_completed("premature")
    sm.execution_failed("premature")
    assert sm.state == AgentState.IDLE
    assert events == []

    # After stop (DONE), a late engine result must not resurrect anything.
    events.clear()
    sm2 = _executing_sm(events)
    sm2.finish()
    sm2.execution_completed("late zombie")
    assert sm2.state == AgentState.DONE
    assert events == []
