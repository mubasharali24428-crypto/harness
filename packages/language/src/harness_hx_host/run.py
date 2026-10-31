"""One call that takes source text to a reviewed, evidenced outcome.

The order is the product claim, so it is the order of this function:

1. **Check.** Errors mean nothing runs — ``status="refused"``, no manifest.
2. **Bind.** The manifest's source digest must match the bytes about to run.
3. **Authorise.** The host grants a subset of the request; empty grants deny.
4. **Run.** The runtime executes inside the declared budget, asking the
   decision at every call site.
5. **Record.** An evidence bundle a human can review: what was asked, what was
   granted, what was called, what it cost, and what came back.

Nothing here merges, applies an edit, approves, or retries a side effect.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from harness_hx import ast as A
from harness_hx.api import CheckResult, check_source
from harness_hx.diagnostics import Diagnostic
from harness_hx.manifest import build_manifest
from harness_hx.version import SPEC_VERSION, TOOLCHAIN_VERSION

from .authority import (
    Authorisation,
    assert_checked_source,
    authority_problems,
    authorize,
)
from .boundary import plain
from .bridge import HostBridge
from .errors import (
    BadPriceTable,
    CapabilityDenied,
    CheckRefused,
    DigestMismatch,
    HxHostError,
    NotRunnable,
    ScriptError,
    StaleAuthority,
)
from .pricing import Pricing, PricingError
from .runtime import Runtime
from .secrets import Tally, scrub_text, scrub_value


@dataclass(frozen=True)
class RunResult:
    """The whole outcome, including the parts a caller might not like."""

    status: str
    path: str
    profile: str
    workflow: str | None
    run_id: str
    correlation_id: str
    source_digest: str
    source_bytes: int = 0
    inputs_sha256: str | None = None
    output: Any = None
    manifest: dict[str, Any] = field(default_factory=dict)
    decision: Authorisation | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    budget: dict[str, Any] = field(default_factory=dict)
    error: HxHostError | None = None
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def evidence(self) -> dict[str, Any]:
        """The artifact a reviewer keeps. Contains no secret values by construction.

        The output is scrubbed as it is written, not as it is produced: the
        program's value stays what it is, while the durable copy cannot carry a
        token. ``redactions`` says a value was masked, so a reviewer reading a
        ``[redacted]`` here knows it was masked by us and not returned by the host.
        """
        tally = Tally()
        outcome: dict[str, Any] = {
            "status": self.status,
            "output": scrub_value(plain(self.output), tally=tally),
        }
        if self.error is not None:
            outcome["error"] = {
                "code": self.error.code,
                "status": self.status,
                "message": scrub_text(self.error.message),
                "line": self.error.line,
                "col": self.error.col,
                "detail": scrub_value(self.error.detail, tally=tally),
            }
        return {
            "kind": "hx.evidence/v1",
            "runId": self.run_id,
            "correlationId": self.correlation_id,
            "source": {
                "path": self.path,
                "sha256": self.source_digest,
                "bytes": self.source_bytes,
            },
            "specVersion": SPEC_VERSION,
            "toolchainVersion": TOOLCHAIN_VERSION,
            "profile": self.profile,
            "workflow": self.workflow,
            "manifest": self.manifest,
            "policy": self.decision.to_json() if self.decision else None,
            "inputsSha256": self.inputs_sha256,
            "events": list(self.events),
            "usage": self.budget,
            "outcome": outcome,
            "redactions": tally.to_json(),
            "review": {"required": True, "approved": False, "autoMerge": False},
            "durationMs": self.duration_ms,
        }

    def to_json(self) -> dict[str, Any]:
        payload = self.evidence()
        payload["diagnostics"] = [record.wire() for record in self.diagnostics]
        return payload


def _select_workflow(
    result: CheckResult, name: str | None
) -> tuple[A.WorkflowDecl | None, HxHostError | None]:
    workflows = [decl for decl in result.program.decls if isinstance(decl, A.WorkflowDecl)]
    if name is not None:
        for decl in workflows:
            if decl.name == name:
                return decl, None
        return None, NotRunnable(f"no workflow named '{name}' in this program")
    if not workflows:
        return None, NotRunnable("this program declares no workflow, so there is nothing to run")
    if len(workflows) > 1:
        names = ", ".join(decl.name for decl in workflows)
        return None, NotRunnable(f"this program declares several workflows ({names}); name one")
    return workflows[0], None


def run_source(
    source: str,
    *,
    path: str = "<memory>",
    profile: str = "agent",
    inputs: Mapping[str, Any] | None = None,
    bridge: HostBridge | None = None,
    granted: Sequence[str] = (),
    principal_permissions: Sequence[str] | None = None,
    principal: str = "anonymous",
    policy: str = "local-dev",
    expected_digest: str | None = None,
    workflow: str | None = None,
    run_id: str | None = None,
    correlation_id: str | None = None,
    cancel: threading.Event | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    prices: Mapping[str, Any] | None = None,
    authority: Authorisation | None = None,
) -> RunResult:
    """Check, authorise, run and record one Hx program.

    ``prices`` is the host's price table (operation name -> USD amount). Supplying
    one is what makes ``budget.cost`` a limit: the next crossing is priced before
    it happens and an operation with no price is refused instead of running free.
    Supplying none leaves cost declared-and-reported, and the evidence says so.

    ``authority`` is a decision the host already holds — normally a
    ``RevisableDecision`` it can revoke into while the run is in flight. It is
    checked against this manifest, not trusted: authority minted for a different
    program, or one that carries a human-only permission, is refused here.
    """
    started = clock()
    run_id = run_id or uuid4().hex
    correlation_id = correlation_id or run_id
    source_bytes = len(source.encode("utf-8"))
    inputs_sha256 = inputs_digest(inputs)

    result = check_source(source, path, profile)
    if not result.ok:
        return RunResult(
            status="refused",
            path=path,
            profile=profile,
            workflow=workflow,
            run_id=run_id,
            correlation_id=correlation_id,
            source_digest=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            source_bytes=source_bytes,
            inputs_sha256=inputs_sha256,
            diagnostics=tuple(result.diagnostics),
            error=CheckRefused(
                f"{result.errors} error(s); nothing was executed",
                detail={"errors": result.errors, "warnings": result.warnings},
            ),
            duration_ms=round((clock() - started) * 1000.0, 3),
        )

    manifest = build_manifest(result, profile)
    base = {
        "path": path,
        "profile": profile,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "manifest": manifest,
        "diagnostics": tuple(result.diagnostics),
    }

    try:
        digest = assert_checked_source(manifest, source)
        if expected_digest is not None and expected_digest != digest:
            raise DigestMismatch("the supplied digest does not describe these bytes")
        try:
            pricing = Pricing.from_mapping(prices) if prices else None
        except PricingError as bad:
            raise BadPriceTable(f"the host's price table is not usable: {bad}") from bad
        if authority is not None:
            problems = authority_problems(authority, manifest)
            if problems:
                raise StaleAuthority(
                    "the supplied authority does not belong to this program",
                    detail={"problems": problems},
                )
    except HxHostError as error:
        return RunResult(
            status=error.status,
            source_digest=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            source_bytes=source_bytes,
            inputs_sha256=inputs_sha256,
            workflow=workflow,
            error=error,
            duration_ms=round((clock() - started) * 1000.0, 3),
            **base,
        )

    decision: Authorisation = authority if authority is not None else authorize(
        manifest,
        granted=granted,
        principal_permissions=principal_permissions,
        principal=principal,
        policy=policy,
    )
    selected, selection_error = _select_workflow(result, workflow)
    workflow_name = selected.name if selected else workflow

    def finish(
        status: str,
        *,
        output: Any = None,
        error: HxHostError | None = None,
        events: Sequence[dict[str, Any]] = (),
        budget: dict[str, Any] | None = None,
    ) -> RunResult:
        return RunResult(
            status=status,
            output=output,
            decision=decision,
            workflow=workflow_name,
            events=tuple(events),
            budget=budget or {},
            error=error,
            source_digest=digest,
            source_bytes=len(source.encode("utf-8")),
            inputs_sha256=inputs_digest(inputs),
            duration_ms=round((clock() - started) * 1000.0, 3),
            **base,
        )

    if not decision.allowed:
        return finish(
            "denied",
            error=CapabilityDenied(
                "the host did not grant every requested capability",
                detail={"denied": [denial.to_json() for denial in decision.denials]},
            ),
        )
    if selection_error is not None or selected is None:
        error = selection_error or NotRunnable("no workflow to run")
        return finish(error.status, error=error)
    # The checker already refuses a workflow under any profile but ``agent``
    # (HX6001), so this is a second lock on the same door rather than the lock:
    # the product rule is the host's, so the host states it too.
    if profile != "agent":
        return finish(
            "refused",
            error=NotRunnable(
                f"profile '{profile}' does not execute workflows; the host runs the agent profile",
                detail={"profile": profile},
            ),
        )
    if bridge is None:
        return finish("refused", error=NotRunnable("no host bridge was supplied"))

    runtime = Runtime(
        result.program,
        selected,
        decision,
        bridge,
        inputs=inputs,
        run_id=run_id,
        correlation_id=correlation_id,
        cancel=cancel,
        emit=emit,
        clock=clock,
        pricing=pricing,
    )
    try:
        output = runtime.run()
        return finish("ok", output=output, events=runtime.events, budget=runtime.budget.to_json())
    except HxHostError as error:
        return finish(
            error.status,
            error=error,
            events=runtime.events,
            budget=runtime.budget.to_json(),
        )
    except Exception as error:  # noqa: BLE001 - an internal bug still becomes evidence
        return finish(
            "error",
            error=ScriptError(f"internal runtime failure: {type(error).__name__}: {error}"),
            events=runtime.events,
            budget=runtime.budget.to_json(),
        )


def digest_of(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def inputs_digest(inputs: Mapping[str, Any] | None) -> str | None:
    """A digest, never the values: inputs can carry sensitive data."""
    if not inputs:
        return None
    return hashlib.sha256(
        json.dumps(plain(dict(inputs)), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
