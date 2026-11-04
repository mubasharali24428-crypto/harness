"""The workflow-run contract: what one program run looks like from outside.

A *workflow* is a typed Hx program with capabilities, a budget and evidence —
not a chat turn. Section 4 executes one; the Hx driver (``harness_hx_host``)
implements ``run_source`` and this Protocol. Neither side imports the other:
Section 4 depends on this module, the driver depends on this module, and the
assembly point (``deploy/``) is the only place that knows both.

Contracts 1.0.0rc7 (additive): this module is new. Nothing existing changed.

Three properties are deliberately part of the contract rather than a
convention, because callers in another section rely on them:

1. ``WorkflowResult.status`` is a closed set. A run settles as exactly one of
   them, and ``ok`` is the only success.
2. Every ``WorkflowOperation`` is one crossed host boundary, in the order it
   crossed. The outbound ``arguments`` and the ``error`` are already scrubbed:
   a secret that reached the host never reaches the transcript (and a
   transcript is durable).
3. ``digest`` covers the canonical evidence bundle. Two runs of the same
   program with the same grants over the same host answers have the same
   digest — that is what makes a replay comparable to the run it replays.
"""

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

#: The closed status vocabulary. Mirrors the driver's ``RunResult.status``.
WorkflowStatus = Literal["ok", "refused", "denied", "budget", "cancelled", "error"]

WORKFLOW_STATUSES: tuple[str, ...] = ("ok", "refused", "denied", "budget", "cancelled", "error")


class WorkflowOperation(BaseModel):
    """One host crossing, already scrubbed, in causal order."""

    callId: str
    operation: str
    ok: bool
    arguments: str = ""
    error: str | None = None
    durationMs: int = 0


class WorkflowRequest(BaseModel):
    """Everything the host decided before the program runs.

    ``grants`` is the host's answer to the manifest — a subset, possibly empty.
    ``principalPermissions`` is Section 1's policy answer for the authenticated
    principal; ``None`` means "not resolved here", which the driver treats as
    no permission rather than as all of them.
    """

    runId: str
    projectId: str
    program: str
    workflow: str | None = None
    profile: Literal["core", "agent"] = "agent"
    grants: list[str] = []
    principalPermissions: list[str] | None = None
    principal: str = "anonymous"
    policy: str = "harness"
    inputs: dict[str, Any] = {}


class WorkflowResult(BaseModel):
    """The whole outcome, including the parts a caller might not like."""

    status: WorkflowStatus = "error"
    code: str | None = None
    message: str = ""
    workflow: str | None = None
    output: Any = None
    digest: str = ""
    evidence: dict[str, Any] = {}
    operations: list[WorkflowOperation] = []
    diagnostics: list[dict[str, Any]] = []


@runtime_checkable
class WorkflowRunner(Protocol):
    """Implemented by the Hx driver; consumed by Section 4. Synchronous by design.

    A runner is called from a worker thread, never from an event loop: the
    program it executes may spend its whole declared duration in one call.
    """

    def run(self, request: WorkflowRequest) -> WorkflowResult: ...
