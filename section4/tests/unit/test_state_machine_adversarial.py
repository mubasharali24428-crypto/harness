"""Section-B adversarial matrix: every state × every transition.

The direct suite (test_state_machine.py, 5 tests) exercises the golden paths.
This file pins the FULL cross-product so no cell is unexplored:

* For each of the 10 states (built through a legitimate event path), every
  public mutator is attempted and must land exactly one of:
  - a documented goto (state + emitted events asserted), or
  - a documented no-op (state, flags, events ALL untouched), or
  - InvalidStateTransitionError (state, flags, events ALL untouched).
* DONE is absorbing: every mutator raises, the single deliberate exception is
  ``resume`` (documented silent no-op, pinned in test_resume.py).
* Coverage is completeness-checked by introspection: a new public method on
  AgentStateMachine without a matrix row fails this file (anti-rot).

Unconditional ``goto`` methods are per the design split documented in
state_machine.py: the machine enforces the LIVENESS half (H-007/H-008);
role/diff-evidence/call-site gating lives in signals.py and the service layer.
This matrix therefore pins exactly what the class guarantees — nothing more.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness_orchestrator.state_machine import (
    PLAN_REJECTED_EVENT,
    AgentState,
    AgentStateMachine,
    InvalidStateTransitionError,
)

E = AgentState
LIVE_STATES = [s for s in E if s not in (E.DONE,)]
ALL_STATES = list(E)

# Recorder-backed builders: each returns (machine, events list) in the state.
def _recorder():
    events: list[tuple[Any, dict]] = []
    return events, (lambda t, p: events.append((t, p)))


def build(state: AgentState) -> tuple[AgentStateMachine, list]:
    events, on_event = _recorder()
    sm = AgentStateMachine("adv-" + state.value, on_event=on_event)
    if state == E.IDLE:
        pass
    elif state == E.PLANNING:
        sm.start_planning()
    elif state == E.AWAITING_PLAN_APPROVAL:
        sm.start_planning()
        sm.plan_proposed("p1")
    elif state == E.EXECUTING:
        sm.start_planning()
        sm.plan_proposed("p1")
        sm.approve_plan("p1", "u1")
        sm.start_execution()
    elif state == E.PAUSED:
        sm.start_planning()
        sm.plan_proposed("p1")
        sm.approve_plan("p1", "u1")
        sm.start_execution()
        sm.pause()
    elif state == E.AWAITING_DONE_SIGNAL:
        sm.start_planning()
        sm.plan_proposed("p1")
        sm.approve_plan("p1", "u1")
        sm.start_execution()
        sm.execution_completed("ok")
    elif state == E.REVIEWING:
        sm.start_planning()
        sm.plan_proposed("p1")
        sm.approve_plan("p1", "u1")
        sm.start_execution()
        sm.execution_completed("ok")
        sm.signal_done()  # done-gate path: reviewer_passed from here terminates
    elif state == E.BLOCKED:
        sm.start_planning()
        sm.plan_proposed("p1")
        sm.approve_plan("p1", "u1")
        sm.start_execution()
        sm.execution_failed("boom")
    elif state == E.PR_OPENED:
        sm.start_planning()
        sm.plan_proposed("p1")
        sm.approve_plan("p1", "u1")
        sm.start_execution()
        sm.open_pr({"number": 1})
    elif state == E.DONE:
        sm.start_planning()
        sm.plan_proposed("p1")
        sm.approve_plan("p1", "u1")
        sm.start_execution()
        sm.finish()
    else:  # pragma: no cover — enum exhaustiveness
        raise AssertionError(state)
    return sm, events


# ---------------------------------------------------------------------------
# Cell kinds
# ---------------------------------------------------------------------------
RAISE = "raise"
NOOP = "noop"


def _goto(target, *, types, flag_checks=None):
    """Expected result of a successful mutator call: new state, new events
    (appended types), optional flag assertions."""
    return {
        "kind": "goto",
        "target": target,
        "types": types,
        "flags": flag_checks or {},
    }


# Every mutator, every source state -> expected cell.
# 'target' may be a callable(source_state) for done-gate-dependent verdicts.
MATRIX: dict[str, dict[AgentState, Any]] = {
    "start_planning": {s: RAISE for s in ALL_STATES},
    "plan_proposed": {
        s: _goto(E.AWAITING_PLAN_APPROVAL, types=["plan_proposed"]) for s in LIVE_STATES
    },
    "approve_plan": {
        s: _goto(s, types=["plan_approved"], flag_checks={"plan_approved": True})
        for s in LIVE_STATES
    },
    "reject_plan": {
        s: _goto(E.IDLE, types=[PLAN_REJECTED_EVENT], flag_checks={"plan_approved": False})
        for s in LIVE_STATES
    },
    "start_execution": {s: RAISE for s in ALL_STATES},  # overridden below
    "execution_completed": {s: NOOP for s in ALL_STATES},  # overridden below
    "execution_failed": {s: NOOP for s in ALL_STATES},  # overridden below
    "pause": {s: NOOP for s in ALL_STATES},  # overridden below
    "resume": {s: NOOP for s in ALL_STATES},  # overridden below
    "submit_human_edit": {
        s: _goto(E.PAUSED, types=["paused"], flag_checks={"is_human_editing": True})
        for s in LIVE_STATES
    },
    "signal_done": {s: _goto(E.REVIEWING, types=["done_signal_received"]) for s in LIVE_STATES},
    "reviewer_passed": {s: _goto(E.EXECUTING, types=["review_passed"]) for s in LIVE_STATES},
    "reviewer_failed": {s: _goto(E.BLOCKED, types=["review_failed"]) for s in LIVE_STATES},
    "block_for_clarification": {
        s: _goto(E.BLOCKED, types=["blocked_awaiting_clarification"]) for s in LIVE_STATES
    },
    "open_pr": {s: _goto(E.PR_OPENED, types=["pr_opened"]) for s in LIVE_STATES},
    "finish": {s: _goto(E.DONE, types=[]) for s in LIVE_STATES},
}

# start_planning has two legal sources, none elsewhere; DONE raises like all.
MATRIX["start_planning"][E.IDLE] = _goto(E.PLANNING, types=[])
MATRIX["start_planning"][E.BLOCKED] = _goto(E.PLANNING, types=[])

# start_execution: guarded by plan_approved (+TTL). Canonical builds leave the
# flag set on/after approval; pre-approval states raise.
for s in ALL_STATES:
    MATRIX["start_execution"][s] = _goto(E.EXECUTING, types=["execution_started"])
MATRIX["start_execution"][E.IDLE] = RAISE
MATRIX["start_execution"][E.PLANNING] = RAISE
MATRIX["start_execution"][E.AWAITING_PLAN_APPROVAL] = RAISE
MATRIX["start_execution"][E.DONE] = RAISE  # terminal beats flag

# Opportunistic transitions fire ONLY from their owning state.
MATRIX["execution_completed"][E.EXECUTING] = _goto(
    E.AWAITING_DONE_SIGNAL, types=["engine_completed"], flag_checks={"done_gate_opened": True}
)
MATRIX["execution_failed"][E.EXECUTING] = _goto(E.BLOCKED, types=["engine_failed"])
MATRIX["pause"][E.EXECUTING] = _goto(E.PAUSED, types=["paused"])
MATRIX["resume"][E.PAUSED] = _goto(
    E.EXECUTING, types=["resumed"], flag_checks={"is_human_editing": False}
)
MATRIX["resume"][E.BLOCKED] = _goto(
    E.EXECUTING, types=["resumed"], flag_checks={"is_human_editing": False}
)

# Reviewer pass target depends on the done-gate path, not just the state name.
# AWAITING_DONE_SIGNAL was built through execution_completed -> gate ARMED.
MATRIX["reviewer_passed"][E.REVIEWING] = _goto(E.DONE, types=["review_passed"])
MATRIX["reviewer_passed"][E.AWAITING_DONE_SIGNAL] = _goto(
    E.DONE, types=["review_passed"], flag_checks={"done_gate_opened": False}
)

# DONE is absorbing: every mutator raises...
for _method, row in MATRIX.items():
    row[E.DONE] = RAISE
# ...except resume, whose DONE no-op is deliberate (test_resume.py contract).
MATRIX["resume"][E.DONE] = NOOP


def _call(sm: AgentStateMachine, method: str) -> None:
    args: dict[str, tuple] = {
        "plan_proposed": ("p2",),
        "approve_plan": ("p2", "u2"),
        "reject_plan": ("p2", "u2"),
        "execution_completed": ("summary",),
        "execution_failed": ("reason",),
        "submit_human_edit": ("src/x.py",),
        "reviewer_passed": ({"findings": []},),
        "reviewer_failed": ({"findings": []},),
        "block_for_clarification": ("what?",),
        "open_pr": ({"number": 2},),
    }.get(method, ())
    getattr(sm, method)(*args)


def _snapshot(sm: AgentStateMachine) -> dict:
    return {
        "state": sm.state,
        "plan_approved": sm.plan_approved,
        "plan_approved_at": sm.plan_approved_at,
        "is_human_editing": sm.is_human_editing,
        "done_gate_opened": sm.done_gate_opened,
    }


@pytest.mark.parametrize("method", sorted(MATRIX))
def test_matrix_row(method: str) -> None:
    for state in ALL_STATES:
        expected = MATRIX[method][state]
        sm, events = build(state)
        before_state, before_events, before_flags = sm.state, list(events), _snapshot(sm)

        if expected == RAISE:
            with pytest.raises(InvalidStateTransitionError):
                _call(sm, method)
            assert _snapshot(sm) == before_flags, (
                f"{method} from {state}: partial mutation on refusal"
            )
            assert sm.state == before_state
            assert events == before_events, f"{method} from {state}: event emitted on refusal"
            continue

        if expected == NOOP:
            _call(sm, method)
            assert _snapshot(sm) == before_flags, f"{method} from {state}: no-op mutated flags"
            assert sm.state == before_state
            assert events == before_events, f"{method} from {state}: no-op emitted"
            continue

        _call(sm, method)
        target = expected["target"]
        assert sm.state == target, f"{method} from {state}: landed {sm.state}, want {target}"
        appended = [t for t, _ in events[len(before_events):]]
        assert appended == expected["types"], f"{method} from {state}: events {appended}"
        for flag, want in expected["flags"].items():
            assert getattr(sm, flag) == want, f"{method} from {state}: {flag} != {want}"

        # Failure modes from here are covered by the DONE row for terminal
        # methods; nothing else to assert per cell.


def test_matrix_covers_every_public_method() -> None:
    covered = set(MATRIX) | {"emit", "transition_to"}
    public = {
        name
        for name, obj in vars(AgentStateMachine).items()
        if callable(obj) and not name.startswith("_")
    }
    assert public <= covered, f"uncovered public methods: {sorted(public - covered)}"


def test_raw_transition_to_refuses_terminal() -> None:
    sm, events = build(E.DONE)
    before = list(events)
    with pytest.raises(InvalidStateTransitionError):
        sm.transition_to(E.EXECUTING)
    assert sm.state == E.DONE
    assert events == before


def test_terminal_absorption_after_reaching_done() -> None:
    """Once DONE, NOTHING moves the run — walk the whole DONE column twice."""
    sm, events = build(E.DONE)
    for _ in range(2):
        for method in MATRIX:
            if method == "resume":
                continue
            with pytest.raises(InvalidStateTransitionError):
                _call(sm, method)
        assert sm.state == E.DONE


def test_signal_done_from_done_gate_arms_termination_only_on_that_path() -> None:
    """done_gate_opened is re-derived from the PATH: review pass after an
    engine completion terminates; a mid-flight hand-over keeps the loop."""
    sm, _ = build(E.AWAITING_DONE_SIGNAL)
    sm.signal_done()
    assert sm.done_gate_opened is True
    sm.reviewer_passed({})
    assert sm.state == E.DONE

    sm2, _ = build(E.PAUSED)
    sm2.signal_done()  # mid-flight hand-over: same REVIEWING state, no gate
    assert sm2.done_gate_opened is False
    sm2.reviewer_passed({})
    assert sm2.state == E.EXECUTING
