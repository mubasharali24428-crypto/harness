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

    def emit(self, event_type: EventType, payload: dict[str, Any] | None = None) -> None:
        if self.on_event:
            self.on_event(event_type, payload or {})

    def transition_to(self, new_state: AgentState) -> None:
        self.state = new_state

    def start_planning(self) -> None:
        if self.state not in (AgentState.IDLE, AgentState.BLOCKED):
            raise InvalidStateTransitionError(f"Cannot start planning from state '{self.state}'.")
        self.transition_to(AgentState.PLANNING)

    def plan_proposed(self, plan_id: str) -> None:
        self.transition_to(AgentState.AWAITING_PLAN_APPROVAL)
        self.emit(EventType.PLAN_PROPOSED, {"planId": plan_id, "runId": self.run_id})

    def approve_plan(self, plan_id: str, principal_user_id: str) -> None:
        self.plan_approved = True
        self.emit(
            EventType.PLAN_APPROVED,
            {"planId": plan_id, "runId": self.run_id, "approvedBy": principal_user_id},
        )

    def reject_plan(self, plan_id: str, principal_user_id: str) -> None:
        self.plan_approved = False
        self.transition_to(AgentState.IDLE)

    def start_execution(self) -> None:
        if not self.plan_approved:
            raise InvalidStateTransitionError("Cannot start execution: plan is not approved.")
        self.transition_to(AgentState.EXECUTING)
        self.emit(EventType.EXECUTION_STARTED, {"runId": self.run_id})

    def execution_completed(self, summary: str = "") -> None:
        """Wave 6 (D4): engine settled successfully — open the human done-gate.
        No-op unless EXECUTING (pause/stop/human-edit already own the narrative)."""
        if self.state == AgentState.EXECUTING:
            self.transition_to(AgentState.AWAITING_DONE_SIGNAL)
            self.emit(
                EventType.ENGINE_COMPLETED,
                {"runId": self.run_id, "summary": summary},
            )

    def execution_failed(self, reason: str = "") -> None:
        """Wave 6 (D4): engine failed honestly — block for human intervention."""
        if self.state == AgentState.EXECUTING:
            self.transition_to(AgentState.BLOCKED)
            self.emit(EventType.ENGINE_FAILED, {"runId": self.run_id, "reason": reason})

    def pause(self, reason: str = "user_requested") -> None:
        if self.state == AgentState.EXECUTING:
            self.transition_to(AgentState.PAUSED)
            self.emit(EventType.PAUSED, {"runId": self.run_id, "reason": reason})

    def resume(self) -> None:
        if self.state in (AgentState.PAUSED, AgentState.BLOCKED):
            self.is_human_editing = False
            self.transition_to(AgentState.EXECUTING)
            self.emit(EventType.RESUMED, {"runId": self.run_id})

    def submit_human_edit(self, path: str) -> None:
        """Implicit pause triggered the moment a human modifies code."""
        self.is_human_editing = True
        self.transition_to(AgentState.PAUSED)
        self.emit(
            EventType.PAUSED,
            {"runId": self.run_id, "reason": "human_edit", "path": path},
        )

    def signal_done(self) -> None:
        """Explicit done signal received; triggers reviewer sub-agent."""
        self.is_human_editing = False
        self.transition_to(AgentState.REVIEWING)
        self.emit(EventType.DONE_SIGNAL_RECEIVED, {"runId": self.run_id})

    def reviewer_passed(self, findings: dict[str, Any]) -> None:
        self.emit(EventType.REVIEW_PASSED, {"runId": self.run_id, "findings": findings})
        self.transition_to(AgentState.EXECUTING)

    def reviewer_failed(self, findings: dict[str, Any]) -> None:
        self.emit(EventType.REVIEW_FAILED, {"runId": self.run_id, "findings": findings})
        self.transition_to(AgentState.BLOCKED)

    def block_for_clarification(self, question: str) -> None:
        self.clarification_question = question
        self.transition_to(AgentState.BLOCKED)
        self.emit(
            EventType.BLOCKED_AWAITING_CLARIFICATION,
            {"runId": self.run_id, "question": question},
        )

    def open_pr(self, pr_data: dict[str, Any]) -> None:
        self.transition_to(AgentState.PR_OPENED)
        self.emit(EventType.PR_OPENED, {"runId": self.run_id, "pr": pr_data})

    def finish(self) -> None:
        self.transition_to(AgentState.DONE)
