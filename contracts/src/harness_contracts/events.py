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
    # Wave 6 (additive): engine lifecycle. Emitted by S4 when the execution
    # engine settles — completion advances EXECUTING→AWAITING_DONE_SIGNAL,
    # failure advances EXECUTING→BLOCKED. Payload keys: summary | reason.
    ENGINE_COMPLETED = "engine_completed"
    ENGINE_FAILED = "engine_failed"
    # W1 (contracts 1.0.0rc3, additive): the live transcript. One TOOL_CALL when
    # the agent invokes a tool and one TOOL_RESULT when it settles, paired by
    # callId. Payloads: TOOL_CALL {runId, callId, name, arguments, turn, step,
    # startedAt}; TOOL_RESULT {runId, callId, name, ok, error?, output?,
    # durationMs}. Output and arguments are scrubbed before they are stored.
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    # W1 (same rc3 bump): the runtime compacted the conversation. Payload:
    # {runId, compactions, compactedTokens, at}. Emitted per run completion, not
    # per compaction, so a transcript reads as one fact rather than a stream.
    CONTEXT_COMPACTED = "context_compacted"


EVENT_TYPES = frozenset(EventType)


class EventEnvelope(BaseModel):
    """The one and only wire shape on the WebSocket bus. Section 1 owns delivery."""

    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: str
    seq: int
    projectId: str
    runId: str | None = None
    # Part 14 (session 12): the project stream carries a schema version like the collab frames
    # (docs/realtime.md 5.5). Version 1 is the only one written today; clients ignore a missing field.
    schemaVersion: int = 1


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
