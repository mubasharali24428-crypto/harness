"""Part 10: the written-down transition table must equal what the machine does.

``lifecycle.RULES`` is the spec; these tests drive the REAL AgentStateMachine
through every (trigger, state) pair and fail when code and table drift apart.
docs/lifecycle.md embeds the same table and is checked against it too.
"""

from pathlib import Path

import pytest

from harness_orchestrator import lifecycle
from harness_orchestrator.lifecycle import NOOP, REFUSED, RULES, TRANSITION, lifecycle_view, outcome
from harness_orchestrator.state_machine import (
    TERMINAL_STATES,
    AgentState,
    AgentStateMachine,
    InvalidStateTransitionError,
)

ROOT = Path(__file__).resolve().parents[3]

CALLS = {
    "pause": lambda sm: sm.pause(),
    "resume": lambda sm: sm.resume(),
    "stop": lambda sm: sm.finish(),
    "complete": lambda sm: sm.execution_completed("ok"),
    "fail": lambda sm: sm.execution_failed("boom"),
    "timeout": lambda sm: sm.execution_failed("timeout: deadline"),
}


def test_every_rule_has_a_call_and_a_durable_record():
    assert {r.trigger for r in RULES} == set(CALLS) == set(lifecycle.DURABLE_RECORD)


@pytest.mark.parametrize("state", list(AgentState))
@pytest.mark.parametrize("trigger", sorted(CALLS))
def test_machine_matches_table(trigger, state):
    expected, target = outcome(trigger, state)
    if trigger == "stop" and state in TERMINAL_STATES:
        # The machine refuses; the SERVICE makes a repeated stop an idempotent no-op
        # (covered end to end in test_lifecycle_races.py).
        sm = AgentStateMachine("r")
        sm.state = state
        with pytest.raises(InvalidStateTransitionError):
            CALLS[trigger](sm)
        assert expected == NOOP
        return
    sm = AgentStateMachine("r")
    sm.state = state
    sm.plan_approved = True
    if expected == REFUSED:
        with pytest.raises(InvalidStateTransitionError):
            CALLS[trigger](sm)
        assert sm.state is state
        return
    CALLS[trigger](sm)
    assert sm.state is target
    assert (expected == TRANSITION) == (target is not state)


def test_done_is_sealed_for_every_trigger():
    for trigger in CALLS:
        assert outcome(trigger, AgentState.DONE)[1] is AgentState.DONE


def test_stop_is_reachable_from_every_live_state():
    for state in AgentState:
        if state in TERMINAL_STATES:
            continue
        assert outcome("stop", state) == (TRANSITION, AgentState.DONE)


def test_doc_embeds_the_current_table():
    doc = (ROOT / "docs" / "lifecycle.md").read_text(encoding="utf-8")
    assert lifecycle.render_markdown_table() in doc, "docs/lifecycle.md table is stale"


# ------------------------------------------------------------- honest status view


def view(state, *, stop=False, quiesced=True):
    return lifecycle_view(state, stop_requested=stop, engine_quiesced=quiesced)


def test_never_stopped_while_engine_can_mutate():
    v = view(AgentState.DONE, stop=True, quiesced=False)
    assert v["phase"] == "stopping"
    assert v["mutationPossible"] is True
    assert v["cancelRequested"] is True and v["cancelConfirmed"] is False


def test_stopped_only_when_confirmed_gone():
    v = view(AgentState.DONE, stop=True, quiesced=True)
    assert v["phase"] == "stopped"
    assert v["mutationPossible"] is False
    assert v["cancelConfirmed"] is True


def test_completed_is_not_stopped():
    v = view(AgentState.DONE, stop=False, quiesced=True)
    assert v["phase"] == "completed" and v["cancelRequested"] is False


def test_pausing_until_engine_is_gone():
    assert view(AgentState.PAUSED, quiesced=False)["phase"] == "pausing"
    assert view(AgentState.PAUSED, quiesced=True)["phase"] == "paused"


def test_executing_can_always_mutate_even_before_the_claim():
    v = view(AgentState.EXECUTING, quiesced=True)
    assert v["phase"] == "running" and v["mutationPossible"] is True


@pytest.mark.parametrize("state", list(AgentState))
def test_stopped_phase_is_never_reported_with_a_live_engine(state):
    for stop in (True, False):
        v = view(state, stop=stop, quiesced=False)
        assert v["phase"] not in ("stopped", "paused", "completed")
        assert v["mutationPossible"] is True
