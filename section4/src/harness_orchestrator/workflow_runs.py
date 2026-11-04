"""Program runs: Section 4 executes an Hx workflow without knowing what Hx is.

The seam is ``harness_contracts.workflow.WorkflowRunner``. Section 4 calls the
Protocol; the Hx driver implements it; ``deploy/`` is the only place that knows
both. That is what keeps the isolation law intact while a section still gets to
run a typed program.

What a run publishes, in causal order:

1. ``execution_started`` — the program digest, profile and workflow name, so a
   client can tell a program run from a chat turn before anything happens.
2. one ``tool_call`` / ``tool_result`` pair per crossed host boundary, keyed by
   the driver's own operation id, with both strings scrubbed before they are
   stored (a transcript is durable; a secret is not allowed to become one).
3. exactly one ``engine_completed`` (only ``ok``) or ``engine_failed`` carrying
   the driver's code. A run that ends without one of these has no durable
   answer, which is why the publish retries in the service apply to it.

A runner that answers off-contract is a wiring bug, not a program outcome: the
run still gets its terminal ``engine_failed`` (the event log must not leave a
run "executing" forever) and the caller gets an exception naming the violation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import string
from collections.abc import Callable
from typing import Any

from harness_contracts.events import EventType
from harness_contracts.workflow import (
    WORKFLOW_STATUSES,
    WorkflowOperation,
    WorkflowRequest,
    WorkflowResult,
    WorkflowRunner,
)

logger = logging.getLogger(__name__)

#: How much of an argument list or an error the transcript keeps. Same posture
#: as the engine's tool record: enough to review, never the whole world.
ARGUMENT_LIMIT = 2000
ERROR_LIMIT = 500
MESSAGE_LIMIT = 990
_HEX = frozenset(string.hexdigits.lower())


class WorkflowRunnerUnavailable(RuntimeError):
    """No runner is wired. The assembly point skipped the driver."""


class WorkflowContractViolation(RuntimeError):
    """A runner answered in a shape ``harness_contracts.workflow`` forbids."""


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value.lower()) <= _HEX


class WorkflowRuns:
    """The program-run path. Holds no state and touches no database."""

    def __init__(
        self,
        runner: WorkflowRunner | None,
        emit: Callable[[EventType, dict[str, Any]], None],
        clean: Callable[[Any, int], str],
    ) -> None:
        self.runner = runner
        self._emit = emit
        self._clean = clean

    async def execute(self, request: WorkflowRequest) -> WorkflowResult:
        if self.runner is None:
            raise WorkflowRunnerUnavailable(
                "no workflow runner is wired: the assembly point must inject one "
                "(harness_contracts.workflow.WorkflowRunner)"
            )
        source = request.program.encode("utf-8")
        self._emit(
            EventType.EXECUTION_STARTED,
            {
                "runId": request.runId,
                "workflow": request.workflow,
                "profile": request.profile,
                "executor": "hx",
                "sourceDigest": hashlib.sha256(source).hexdigest(),
                "sourceBytes": len(source),
            },
        )
        # The driver is synchronous and may spend the program's whole declared
        # duration in one call: it runs on a worker thread, never on the loop.
        result = await asyncio.to_thread(self.runner.run, request)
        self._check(request, result)
        for operation in result.operations:
            self._emit_tool(request.runId, operation)
        self._emit_terminal(request, result)
        return result

    # ------------------------------------------------------------------ checks

    def _check(self, request: WorkflowRequest, result: Any) -> None:
        if not isinstance(result, WorkflowResult):
            self._fail_contract(
                request, f"runner returned {type(result).__name__}, not WorkflowResult"
            )
        if result.status not in WORKFLOW_STATUSES:
            self._fail_contract(request, f"status {result.status!r} is not in the closed set")
        for operation in result.operations:
            if not isinstance(operation, WorkflowOperation):
                self._fail_contract(
                    request, f"operation is {type(operation).__name__}, not WorkflowOperation"
                )

    def _fail_contract(self, request: WorkflowRequest, reason: str) -> None:
        self._emit(
            EventType.ENGINE_FAILED,
            {"runId": request.runId, "reason": reason[:MESSAGE_LIMIT], "executor": "hx"},
        )
        raise WorkflowContractViolation(reason)

    # ------------------------------------------------------------------ events

    def _emit_tool(self, run_id: str, operation: WorkflowOperation) -> None:
        common = {
            "runId": run_id,
            "callId": self._clean(operation.callId, 120),
            "name": self._clean(operation.operation, 80),
        }
        self._emit(
            EventType.TOOL_CALL,
            {
                **common,
                "arguments": self._clean(operation.arguments, ARGUMENT_LIMIT),
                "turn": None,
                "step": None,
                "startedAt": None,
            },
        )
        self._emit(
            EventType.TOOL_RESULT,
            {
                **common,
                "ok": bool(operation.ok),
                "error": self._clean(operation.error, ERROR_LIMIT) or None,
                "output": "",
                "turn": None,
                "step": None,
                "finishedAt": None,
                "durationMs": int(operation.durationMs),
            },
        )

    def _emit_terminal(self, request: WorkflowRequest, result: WorkflowResult) -> None:
        digest = result.digest if _is_digest(result.digest) else self._computed_digest(result)
        if result.status == "ok":
            self._emit(
                EventType.ENGINE_COMPLETED,
                {
                    "runId": request.runId,
                    "executor": "hx",
                    "workflow": result.workflow or request.workflow,
                    "summary": (
                        f"workflow {result.workflow or request.workflow or '?'} settled ok; "
                        f"evidence {digest[:16]}"
                    ),
                    "evidenceDigest": digest,
                },
            )
            return
        code = result.code or "HXH000"
        message = self._clean(result.message, MESSAGE_LIMIT)
        self._emit(
            EventType.ENGINE_FAILED,
            {
                "runId": request.runId,
                "executor": "hx",
                "status": result.status,
                "code": code,
                "reason": f"{code}: {message}"[:MESSAGE_LIMIT],
                "evidenceDigest": digest,
            },
        )
        logger.info(
            "workflow run %s settled %s (%s) after %d host call(s)",
            request.runId,
            result.status,
            code,
            len(result.operations),
        )

    @staticmethod
    def _computed_digest(result: WorkflowResult) -> str:
        """A runner that ships no digest still gets a stable one over its evidence."""
        blob = json.dumps(result.evidence, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
