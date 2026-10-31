"""The driver as a contract implementation: ``WorkflowRunner`` over ``run_source``.

Section 4 executes a workflow without importing this package; this class is what
makes that possible. It is a translation layer and nothing more:

* the request's grants become the driver's grant list (empty means deny),
* the run's events become a causal ``operations`` list (start/finish paired by
  operation name, in completion order),
* the evidence bundle becomes a stable ``digest`` — the one thing a replay must
  reproduce and a live run must not be trusted to fake.

The digest deliberately excludes wall-clock time. Two runs of the same program
over the same host answers must agree; a stopwatch never does.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from harness_contracts.workflow import WorkflowOperation, WorkflowRequest, WorkflowResult

from harness_hx.version import SPEC_VERSION, TOOLCHAIN_VERSION

from .boundary import plain
from .bridge import HostBridge
from .run import RunResult, run_source

#: Long enough to recognise a path or a suite name, short enough to stay a
#: transcript line. Section 4 applies its own cap on top of this one.
ARGUMENT_LIMIT = 2000


def _compact(value: Any) -> str:
    try:
        text = json.dumps(plain(value), sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        text = repr(value)
    return text[:ARGUMENT_LIMIT]


def _operations(events: Sequence[Mapping[str, Any]]) -> list[WorkflowOperation]:
    """Pair each call's start with its finish, in the order calls finished."""
    pending: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    operations: list[WorkflowOperation] = []
    for event in events:
        kind = event.get("kind")
        name = event.get("operation")
        if not isinstance(name, str):
            continue
        if kind == "call.start":
            pending[name].append(event)
            continue
        if kind not in ("call.finished", "call.failed") or not pending[name]:
            continue
        start = pending[name].pop(0)
        operations.append(
            WorkflowOperation(
                callId=str(start.get("operationId") or f"{name}:{len(operations)}"),
                operation=name,
                ok=kind == "call.finished",
                arguments=_compact(start.get("args")),
                error=None if kind == "call.finished" else str(event.get("error") or ""),
                durationMs=int(round(float(event.get("durationMs") or 0.0))),
            )
        )
    # A call still in flight when the run settled (cancelled, budget stop) has
    # no result. Saying so is honest; dropping it would hide the crossing.
    for name, starts in pending.items():
        for start in starts:
            operations.append(
                WorkflowOperation(
                    callId=str(start.get("operationId") or f"{name}:unfinished"),
                    operation=name,
                    ok=False,
                    arguments=_compact(start.get("args")),
                    error="no result: the run settled while this call was in flight",
                )
            )
    return operations


def _output_digest(value: Any) -> dict[str, str]:
    """The output's identity without the output's bytes."""
    try:
        blob = json.dumps(plain(value), sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(value)
    return {"sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(), "size": str(len(blob))}


@dataclass
class ContractRunner:
    """``harness_contracts.workflow.WorkflowRunner`` over the Hx driver."""

    bridge: HostBridge | Callable[[], HostBridge]
    clock: Callable[[], float] = time.monotonic

    def _bridge(self) -> HostBridge:
        return self.bridge() if callable(self.bridge) else self.bridge

    def run(self, request: WorkflowRequest) -> WorkflowResult:
        result = run_source(
            request.program,
            path=f"<workflow:{request.workflow or '?'}>",
            profile=request.profile,
            inputs=request.inputs,
            bridge=self._bridge(),
            granted=tuple(request.grants),
            principal_permissions=(
                None
                if request.principalPermissions is None
                else tuple(request.principalPermissions)
            ),
            principal=request.principal,
            policy=request.policy,
            workflow=request.workflow,
            run_id=request.runId,
            correlation_id=request.runId,
            clock=self.clock,
        )
        return self._translate(result)

    def _translate(self, result: RunResult) -> WorkflowResult:
        operations = _operations(result.events)
        canonical = self._canonical(result, operations)
        return WorkflowResult(
            status=result.status,
            code=None if result.error is None else result.error.code,
            message="" if result.error is None else result.error.message,
            workflow=result.workflow,
            output=plain(result.output),
            digest=hashlib.sha256(
                json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            evidence=result.evidence(),
            operations=operations,
            diagnostics=[record.wire() for record in result.diagnostics],
        )

    @staticmethod
    def _canonical(result: RunResult, operations: Sequence[WorkflowOperation]) -> dict[str, Any]:
        """Everything a reviewer would compare, and nothing a clock decides."""
        error = None
        if result.error is not None:
            error = {
                "code": result.error.code,
                "message": result.error.message,
                "line": result.error.line,
                "col": result.error.col,
            }
        return {
            "kind": "hx.workflow/v1",
            "specVersion": SPEC_VERSION,
            "toolchainVersion": TOOLCHAIN_VERSION,
            "workflow": result.workflow,
            "profile": result.profile,
            "source": {"sha256": result.source_digest, "bytes": result.source_bytes},
            "inputsSha256": result.inputs_sha256,
            "manifest": result.manifest,
            "policy": None if result.decision is None else result.decision.to_json(),
            "budget": result.budget,
            "status": result.status,
            "error": error,
            "output": _output_digest(result.output),
            "operations": [
                {
                    "operation": operation.operation,
                    "ok": operation.ok,
                    "arguments": operation.arguments,
                    "error": operation.error,
                }
                for operation in operations
            ],
            "diagnostics": [record.wire() for record in result.diagnostics],
        }
