"""Done-signal gate (Mission 11, Wave 1, lane W1-D1 — H-007 + H-008).

The human done-signal used to be a rubber stamp: any authenticated principal
could fire it (the API route even declared ``RunReader``), an empty diffText
produced a vacuous "review passed cleanly" with zero evidence of work, and it
fired happily on stopped/DONE runs, resurrecting them through REVIEWING into a
zombie EXECUTING loop (reviewer_passed sent the machine back to EXECUTING —
there was no terminal state at all).

This module centralizes every guard in one place so both the FastAPI route and
OrchestratorService enforce identical semantics:

* **Role** — only executor-class principals (admin/engineer) may declare work
  done; read-only roles are rejected (403 ROLE_DENIED at the route).
* **Evidence** — a done-signal without a non-empty diffText is rejected (400);
  no diff means nothing was delivered.
* **Liveness** — stopped/DONE runs cannot be resurrected by a late signal
  (409); any non-terminal state may still hand work over to review (the
  golden conformance flow accepts a paused run), only terminal states are
  dead.

The reviewer verdict itself stays in ReviewerSubAgent. Terminal semantics are
fixed in state_machine: a review that follows an *engine-opened done-gate*
(AWAITING_DONE_SIGNAL → REVIEWING) reaching pass lands on DONE — a real
terminal state that accepts no further transitions — instead of looping back
to EXECUTING and producing zombie "executing" runs forever.
"""

from dataclasses import dataclass

from harness_contracts.roles import Principal

from harness_orchestrator.state_machine import TERMINAL_STATES, AgentState

# States a done-signal may arrive from: anything NOT terminal. Mid-flight
# states stay legal (a human may accept work while paused on an edit — the
# golden conformance flow does exactly that); only DONE (stop or accepted
# review) is resurrection and gets the 409.
DONE_SIGNAL_STATES: frozenset[AgentState] = frozenset(
    state for state in AgentState if state not in TERMINAL_STATES
)


class DoneSignalRejectedError(Exception):
    """Base for done-signal rejections carrying the HTTP status they map to."""

    http_status = 400
    code = "DONE_SIGNAL_INVALID"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DoneSignalRoleDenied(DoneSignalRejectedError):
    """Read-only roles may not declare work done."""

    http_status = 403
    code = "ROLE_DENIED"


class EmptyDiffTextError(DoneSignalRejectedError):
    """A done-signal must carry non-empty diff text as evidence of delivery."""

    http_status = 400
    code = "EMPTY_DIFF"


class RunNotLiveForDoneSignalError(DoneSignalRejectedError):
    """Stopped/DONE runs cannot be resurrected by a late done-signal."""

    http_status = 409
    code = "RUN_NOT_LIVE"


class StagedApprovalPendingError(RunNotLiveForDoneSignalError):
    """Withheld changes must be resolved before review/completion."""

    code = "STAGED_APPROVAL_PENDING"


# Executor class: who may declare work done. Read-only roles (viewer/guest)
# are excluded deliberately — same staff set frozen contracts already trust
# with run control ("write"), which is what a done-signal effectively is.
EXECUTOR_ROLES = frozenset({"admin", "engineer"})


@dataclass(frozen=True)
class DoneSignalDecision:
    """Outcome of gating one done-signal attempt."""

    allowed: bool
    reason: str | None = None


def validate_done_signal(
    principal: Principal,
    current_state: AgentState,
    diff_text: str,
) -> DoneSignalDecision:
    """Gate a human done-signal before any transition or review spend happens.

    Order matters and mirrors HTTP semantics:
      1. role   — 403 before anything else (identity first),
      2. liveness — 409 before evidence (a stopped/finished run has no gate
         to open, whatever the payload says),
      3. evidence — 400 empty/whitespace-only diffText last, so the client
         sees the cheapest-to-fix problem once identity and timing are clean.
    """
    if str(principal.role) not in EXECUTOR_ROLES:
        return DoneSignalDecision(False, f"role '{principal.role}' may not signal done")
    if current_state not in DONE_SIGNAL_STATES:
        return DoneSignalDecision(
            False,
            f"run is not live for a done-signal (state '{current_state}')",
        )
    if not isinstance(diff_text, str) or not diff_text.strip():
        return DoneSignalDecision(False, "diffText must be non-empty")
    return DoneSignalDecision(True)


def raise_for_decision(decision: DoneSignalDecision) -> None:
    """Translate a negative decision into its typed rejection error."""
    if decision.allowed:
        return
    reason = decision.reason or ""
    if reason.startswith("role "):
        raise DoneSignalRoleDenied(reason)
    if reason.startswith("run is not live"):
        raise RunNotLiveForDoneSignalError(reason)
    raise EmptyDiffTextError(reason)


def gate_done_signal(principal: Principal, current_state: AgentState, diff_text: str) -> None:
    """All-in-one guard: raises the typed rejection or returns silently."""
    raise_for_decision(validate_done_signal(principal, current_state, diff_text))


__all__ = [
    "EXECUTOR_ROLES",
    "DONE_SIGNAL_STATES",
    "DoneSignalDecision",
    "DoneSignalRejectedError",
    "DoneSignalRoleDenied",
    "EmptyDiffTextError",
    "RunNotLiveForDoneSignalError",
    "gate_done_signal",
    "raise_for_decision",
    "validate_done_signal",
]
