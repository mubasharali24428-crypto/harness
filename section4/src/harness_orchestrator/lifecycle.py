"""Run lifecycle: the explicit transition table and the honest status view.

Master prompt part 10 (001 explicit durable transitions, 002 semantics, 004 never
say "stopped" while execution can still mutate files, 010 honest status).

``TRANSITIONS`` is the single written-down spec of what pause / resume / stop /
timeout / failure / completion do to each :class:`AgentState`. It is not
decoration: ``tests/unit/test_lifecycle_table.py`` drives the real
:class:`AgentStateMachine` through every (trigger, state) pair and fails when the
machine and this table disagree, and ``docs/lifecycle.md`` is checked against it
too. The durable witness for each transition (event type, audit action, queue
effect) is listed in ``DURABLE_RECORD``; replay of those rows after a restart is
what makes the state durable (``OrchestratorService._rehydrate``).

``lifecycle_view`` turns (state, stop/pause evidence, whether the engine is still
alive) into the operator-facing phase. ``stopped`` is only ever returned once the
engine is confirmed quiesced; until then the phase is ``stopping`` and
``mutationPossible`` is true.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from harness_orchestrator.state_machine import TERMINAL_STATES, AgentState

S = AgentState

# Outcomes of a trigger applied to a state.
TRANSITION = "transition"  # state changes
NOOP = "noop"  # accepted, nothing changes (idempotent)
REFUSED = "refused"  # InvalidStateTransitionError (API: 409)

_NON_TERMINAL = tuple(s for s in AgentState if s not in TERMINAL_STATES)


class Rule(NamedTuple):
    trigger: str
    machine_call: str
    moves: dict[AgentState, AgentState]  # source -> target, everything else is `otherwise`
    otherwise: str  # NOOP or REFUSED for non-listed non-terminal states
    terminal: str  # outcome when the run is already DONE
    summary: str


# Triggers map to the real calls: service.pause_agent / resume_agent / stop_agent,
# and the engine settling inside OrchestratorService._execute.
RULES: tuple[Rule, ...] = (
    Rule(
        "pause", "AgentStateMachine.pause",
        {S.EXECUTING: S.PAUSED}, NOOP, REFUSED,
        "Only an executing run pauses. Pausing anything else is accepted and changes nothing; "
        "pausing a finished/stopped run is refused (409).",
    ),
    Rule(
        "resume", "AgentStateMachine.resume",
        {S.PAUSED: S.EXECUTING, S.BLOCKED: S.EXECUTING}, NOOP, NOOP,
        "Paused and blocked runs resume (the engine is re-spawned with the same session id). "
        "Resuming anything else, including a DONE run, is a silent no-op: nothing is resurrected.",
    ),
    Rule(
        "stop", "OrchestratorService.stop_agent -> AgentStateMachine.finish",
        {s: S.DONE for s in _NON_TERMINAL}, NOOP, NOOP,
        "Stop is terminal from every live state. A repeated stop on a DONE run is an "
        "idempotent no-op. DONE is sealed: no later event moves the run.",
    ),
    Rule(
        "complete", "AgentStateMachine.execution_completed",
        {S.EXECUTING: S.AWAITING_DONE_SIGNAL}, NOOP, REFUSED,
        "The engine settled successfully: open the human done-gate. A result that arrives "
        "after pause/stop is discarded by the service (cancel_requested) and never reaches "
        "the machine.",
    ),
    Rule(
        "fail", "AgentStateMachine.execution_failed",
        {S.EXECUTING: S.BLOCKED}, NOOP, REFUSED,
        "Engine error, provider-circuit-open, staging failure, pending approval, or a lost "
        "worker heartbeat: the run blocks for a human and stays resumable.",
    ),
    Rule(
        "timeout", "AgentStateMachine.execution_failed",
        {S.EXECUTING: S.BLOCKED}, NOOP, REFUSED,
        "The engine deadline expired: same transition as `fail` with reason `timeout: ...`; "
        "the worker is killed by the runtime and the queue claim is released.",
    ),
)

# What makes each trigger durable (survives a service restart).
DURABLE_RECORD: dict[str, str] = {
    "pause": "agent_events `paused` {reason}; run_claims row released; worker process group killed",
    "resume": "agent_events `resumed`; run_claims re-enqueued (one live claim per run)",
    "stop": (
        "agent_events `paused` {reason: user_requested_stop} (replay tombstone to DONE); "
        "audit run.cancel_requested, run.stopped, and run.cancel_confirmed once the worker is gone"
    ),
    "complete": (
        "agent_events `engine_completed`; later done_signal_received -> review_passed -> DONE"
    ),
    "fail": "agent_events `engine_failed` {reason}; heartbeat_lost is emitted by the zombie reaper",
    "timeout": "agent_events `engine_failed` {reason: 'timeout: ...'}",
}


def outcome(trigger: str, state: AgentState) -> tuple[str, AgentState]:
    """(outcome, resulting state) the table prescribes for ``trigger`` in ``state``."""
    rule = next(r for r in RULES if r.trigger == trigger)
    if state in TERMINAL_STATES:
        return rule.terminal, state
    if state in rule.moves:
        return TRANSITION, rule.moves[state]
    return rule.otherwise, state


def render_markdown_table() -> str:
    """The table as it appears in docs/lifecycle.md (a test keeps the two in sync)."""
    lines = ["| Trigger | From | To | Outcome |", "|---|---|---|---|"]
    for rule in RULES:
        for state in AgentState:
            out, to = outcome(rule.trigger, state)
            lines.append(f"| {rule.trigger} | {state.value} | {to.value} | {out} |")
    return "\n".join(lines)


# ---------------------------------------------------------------- honest status

# Phases an operator may see. `stopped` and `paused` are only claimed when the
# engine is confirmed gone; otherwise `stopping` / `pausing`.
PHASES = (
    "pending",  # idle / planning / awaiting plan approval
    "running",
    "pausing",
    "paused",
    "blocked",
    "reviewing",  # awaiting done signal / in review / PR open
    "stopping",
    "stopped",
    "completed",
)


def lifecycle_view(
    state: AgentState,
    *,
    stop_requested: bool,
    engine_quiesced: bool,
) -> dict[str, Any]:
    """Operator-facing lifecycle: never `stopped`/`paused` while the engine can mutate.

    ``stop_requested`` is the durable stop tombstone (live flag or replayed
    `paused{user_requested_stop}`), ``engine_quiesced`` is process truth (worker
    gone / engine thread finished). Cancel is *requested* the moment stop is
    accepted and *confirmed* only when ``engine_quiesced``.
    """
    # An EXECUTING run may be queued but not yet claimed: it can still mutate.
    mutation_possible = state is S.EXECUTING or not engine_quiesced
    if state in TERMINAL_STATES:
        if stop_requested:
            phase = "stopped" if engine_quiesced else "stopping"
        else:
            phase = "completed" if engine_quiesced else "stopping"  # alive engine: not done yet
    elif state is S.EXECUTING:
        phase = "running"
    elif state is S.PAUSED:
        phase = "paused" if engine_quiesced else "pausing"
    elif state is S.BLOCKED:
        phase = "blocked"
    elif state in (S.AWAITING_DONE_SIGNAL, S.REVIEWING, S.PR_OPENED):
        phase = "reviewing"
    else:
        phase = "pending"
    return {
        "phase": phase,
        "cancelRequested": bool(stop_requested),
        "cancelConfirmed": bool(stop_requested and engine_quiesced),
        "mutationPossible": mutation_possible,
    }
