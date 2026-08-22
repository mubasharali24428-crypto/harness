from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    PLAN_PROPOSED = "plan_proposed"
    PLAN_APPROVED = "plan_approved"
    EXECUTION_STARTED = "execution_started"
    PAUSED = "paused"
    RESUMED = "resumed"
    BLOCKED_AWAITING_CLARIFICATION = "blocked_awaiting_clarification"
    DONE_SIGNAL_RECEIVED = "done_signal_received"
    REVIEW_PASSED = "review_passed"
    REVIEW_FAILED = "review_failed"
    PR_OPENED = "pr_opened"
    TERMINAL_OUTPUT = "terminal_output"
    DIFF_PROGRESS = "diff_progress"


EVENT_TYPES = frozenset(EventType)


class EventEnvelope(BaseModel):
    """The one and only wire shape on the WebSocket bus. Section 1 owns delivery."""

    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: str
    seq: int
    projectId: str
    runId: str | None = None


def event_envelope(
    event_type: EventType,
    project_id: str,
    seq: int,
    payload: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        type=event_type,
        payload=payload or {},
        ts=datetime.now(UTC).isoformat(),
        seq=seq,
        projectId=project_id,
        runId=run_id,
    )
