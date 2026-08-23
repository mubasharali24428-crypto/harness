"""Agent lifecycle state machine (Section 4).

Mission 11 Wave 1 (lane W1-D1) — terminal-state hygiene (H-008):

* DONE is now a real TERMINAL state. ``finish()`` seals the machine: every
  subsequent transition attempt raises :class:`InvalidStateTransitionError`
  instead of being silently ignored, so a stopped run can never be walked
  back into execution by a late event or a stale done-signal. Callers that
  relied on the old fire-and-forget no-op semantics keep working — they just
  get a loud error instead of a quiet lie.
* REVIEW_PASSED in AWAITING_DONE_SIGNAL now reaches DONE (the run's work is
  accepted and the lifecycle is over) instead of looping back to EXECUTING,
  which produced zombie "executing" runs forever after. REVIEW_FAILED still
  blocks for human intervention — a failed review may legitimately be
  resumed after fixes.

Mission 11 Wave 1 (H-007): ``signal_done`` refuses terminal states — a done-
signal may hand work to review from any live state, but never resurrect a
stopped/finished run. Role and diff-evidence gating live in
harness_orchestrator.signals; this class enforces the liveness half.

Mission 11 Wave 1 (S-010 observability): ``reject_plan`` emits a
``plan.rejected`` event itself (payload carries planId + rejectedBy) instead
of trusting an upstream layer to record it — the frozen contracts EventType
enum has no PLAN_REJECTED member, so the wire name is the frozen audit action
string already allowlisted in the shared audit registry. The rejection also
clears ``plan_approved`` *before* the run returns to IDLE, so neither a late
caller nor a post-restart rehydrated machine can reach EXECUTING on a rejected
plan without a fresh, audited approval.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from harness_contracts.events import EventType


class AgentState(StrEnum):
    IDLE = "idle"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    EXECUTING = "executing"
    PAUSED = "paused"
    AWAITING_DONE_SIGNAL = "awaiting_done_signal"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    PR_OPENED = "pr_opened"
    DONE = "done"


# Terminal states accept NO further transitions — the run is over. A stop
# (or an accepted review) is final; nothing resurrects it.
TERMINAL_STATES: frozenset[AgentState] = frozenset({AgentState.DONE})

# S-010: wire name of the plan-rejection event emitted by ``reject_plan``.
# Kept as a plain string because EventType lives in frozen contracts and has
# no PLAN_REJECTED member; this exact spelling is the audit action name
# already allowlisted upstream ("plan.rejected" in the shared audit registry
# and written by plan_gate's audit row), so machine events and audit rows
# reconcile under one name. Consumers that persist every emission will see
# it; rehydration maps only known EventType values, so replaying it is inert.
PLAN_REJECTED_EVENT = "plan.rejected"


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state machine transition is attempted."""

    pass


class AgentStateMachine:
    """State machine managing agent lifecycle, pauses, and governance gates."""

    def __init__(
        self,
        run_id: str,
        on_event: Callable[[EventType, dict[str, Any]], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self.state: AgentState = AgentState.IDLE
        self.on_event = on_event
        self.plan_approved: bool = False
        self.is_human_editing: bool = False
        self.clarification_question: str | None = None
        # H-008: set when the engine opened the human done-gate. A review
        # verdict following THAT path terminates the run (DONE); a review from
        # a mid-flight hand-over (paused/…, the golden conformance flow) keeps
        # the legacy loop so humans can keep iterating before final acceptance.
        self.done_gate_opened: bool = False

    def emit(self, event_type: EventType, payload: dict[str, Any] | None = None) -> None:
        if self.on_event:
            self.on_event(event_type, payload or {})

    def transition_to(self, new_state: AgentState) -> None:
        """Raw transition. Refuses to leave a terminal state — DONE means done."""
        if self.state in TERMINAL_STATES:
            raise InvalidStateTransitionError(
                f"Run '{self.run_id}' is in terminal state '{self.state}'; "
                f"no further transitions are allowed (refused '{new_state}')."
            )
        self.state = new_state

    def _require_live(self, action: str) -> None:
        """Shared guard for every named transition (H-008 terminal hygiene)."""
        if self.state in TERMINAL_STATES:
            raise InvalidStateTransitionError(
                f"Cannot {action}: run '{self.run_id}' is in terminal state '{self.state}'."
            )

    def start_planning(self) -> None:
        self._require_live("start planning")
        if self.state not in (AgentState.IDLE, AgentState.BLOCKED):
            raise InvalidStateTransitionError(f"Cannot start planning from state '{self.state}'.")
        self.transition_to(AgentState.PLANNING)

    def plan_proposed(self, plan_id: str) -> None:
        self._require_live("propose plan")
        self.transition_to(AgentState.AWAITING_PLAN_APPROVAL)
        self.emit(EventType.PLAN_PROPOSED, {"planId": plan_id, "runId": self.run_id})

    def approve_plan(self, plan_id: str, principal_user_id: str) -> None:
        self._require_live("approve plan")
        self.plan_approved = True
        self.emit(
            EventType.PLAN_APPROVED,
            {"planId": plan_id, "runId": self.run_id, "approvedBy": principal_user_id},
        )

    def reject_plan(self, plan_id: str, principal_user_id: str) -> None:
        """Reject the open plan and return the run to IDLE (S-010 observable).

        Emits the ``plan.rejected`` event itself (payload: runId, planId,
        rejectedBy) so the rejection is fully observable on the event stream
        alongside plan_gate's audit row — no upstream layer can forget it.
        Approval is cleared BEFORE the transition back to IDLE, so the rejected
        plan can never be started: not by a stale in-process caller, and not
        after a restart (a fresh machine boots IDLE with ``plan_approved``
        False, and replaying the rejection keeps it that way until a fresh,
        audited approve_plan).
        """
        self._require_live("reject plan")
        self.plan_approved = False
        self.transition_to(AgentState.IDLE)
        self.emit(
            PLAN_REJECTED_EVENT,
            {
                "runId": self.run_id,
                "planId": plan_id,
                "rejectedBy": principal_user_id,
            },
        )

    def start_execution(self) -> None:
        self._require_live("start execution")
        if not self.plan_approved:
            raise InvalidStateTransitionError("Cannot start execution: plan is not approved.")
        self.transition_to(AgentState.EXECUTING)
        self.emit(EventType.EXECUTION_STARTED, {"runId": self.run_id})

    def execution_completed(self, summary: str = "") -> None:
        """Wave 6 (D4): engine settled successfully — open the human done-gate.
        No-op unless EXECUTING (pause/stop/human-edit already own the narrative)."""
        self._require_live("record engine completion")
        if self.state == AgentState.EXECUTING:
            self.transition_to(AgentState.AWAITING_DONE_SIGNAL)
            self.done_gate_opened = True  # H-008: a pass from here terminates
            self.emit(
                EventType.ENGINE_COMPLETED,
                {"runId": self.run_id, "summary": summary},
            )

    def execution_failed(self, reason: str = "") -> None:
        """Wave 6 (D4): engine failed honestly — block for human intervention."""
        self._require_live("record engine failure")
        if self.state == AgentState.EXECUTING:
            self.transition_to(AgentState.BLOCKED)
            self.emit(EventType.ENGINE_FAILED, {"runId": self.run_id, "reason": reason})

    def pause(self, reason: str = "user_requested") -> None:
        self._require_live("pause")
        if self.state == AgentState.EXECUTING:
            self.transition_to(AgentState.PAUSED)
            self.emit(EventType.PAUSED, {"runId": self.run_id, "reason": reason})

    def resume(self) -> None:
        # Deliberately NO terminal guard: resume-from-DONE stays a silent no-op
        # (pinned by tests/unit/test_resume.py). Resurrection is impossible —
        # the body below only ever fires from PAUSED/BLOCKED.
        if self.state in (AgentState.PAUSED, AgentState.BLOCKED):
            self.is_human_editing = False
            self.transition_to(AgentState.EXECUTING)
            self.emit(EventType.RESUMED, {"runId": self.run_id})

    def submit_human_edit(self, path: str) -> None:
        """Implicit pause triggered the moment a human modifies code."""
        self._require_live("record a human edit")
        self.is_human_editing = True
        self.transition_to(AgentState.PAUSED)
        self.emit(
            EventType.PAUSED,
            {"runId": self.run_id, "reason": "human_edit", "path": path},
        )

    def signal_done(self) -> None:
        """Explicit done signal received; triggers reviewer sub-agent.

        H-007/H-008: legal from any non-terminal state — role and diff-evidence
        gating live in signals.py, this class owns the liveness half. Firing on
        terminal DONE raises instead of resurrecting the run.

        Zombie hardening: the terminal verdict is a property of the PATH taken,
        so the done-gate is re-derived from the state at signal time. A process
        restart rehydrates a run straight into AWAITING_DONE_SIGNAL while the
        in-memory 'done_gate_opened' flag resets to False (event replay can
        restore states, not attributes); without this re-derivation a review
        pass on the rehydrated run looped back to EXECUTING forever.
        """
        self._require_live("signal done")
        if self.state == AgentState.AWAITING_DONE_SIGNAL:
            self.done_gate_opened = True
        self.is_human_editing = False
        self.transition_to(AgentState.REVIEWING)
        self.emit(EventType.DONE_SIGNAL_RECEIVED, {"runId": self.run_id})

    def reviewer_passed(self, findings: dict[str, Any]) -> None:
        """Review accepted the delivered work.

        H-008 zombie fix: a pass that follows the engine-opened done-gate
        lands on DONE — the run's work is accepted and its lifecycle is over.
        A pass from a mid-flight hand-over keeps the legacy EXECUTING loop so
        humans can keep iterating before final acceptance.
        """
        self._require_live("record a review verdict")
        self.emit(EventType.REVIEW_PASSED, {"runId": self.run_id, "findings": findings})
        if self.done_gate_opened:
            self.done_gate_opened = False
            self.transition_to(AgentState.DONE)
        else:
            self.transition_to(AgentState.EXECUTING)

    def reviewer_failed(self, findings: dict[str, Any]) -> None:
        """Review rejected the diff — block for human intervention (resumable)."""
        self._require_live("record a review verdict")
        self.emit(EventType.REVIEW_FAILED, {"runId": self.run_id, "findings": findings})
        self.transition_to(AgentState.BLOCKED)

    def block_for_clarification(self, question: str) -> None:
        self._require_live("block for clarification")
        self.clarification_question = question
        self.transition_to(AgentState.BLOCKED)
        self.emit(
            EventType.BLOCKED_AWAITING_CLARIFICATION,
            {"runId": self.run_id, "question": question},
        )

    def open_pr(self, pr_data: dict[str, Any]) -> None:
        self._require_live("open a PR")
        self.transition_to(AgentState.PR_OPENED)
        self.emit(EventType.PR_OPENED, {"runId": self.run_id, "pr": pr_data})

    def finish(self) -> None:
        """Stop the run — DONE is terminal and sealed against resurrection."""
        self._require_live("stop the run")
        self.transition_to(AgentState.DONE)
