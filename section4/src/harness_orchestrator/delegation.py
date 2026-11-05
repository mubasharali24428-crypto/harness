"""W28 S1 — subagent delegation: the delegate verb.

The W8 quota gate landed with Module 0044; the spawn endpoint already records
and gates who may spawn what, but **nothing on the engine path calls it** — a
human has to do it through the HTTP surface. This module is the seam the engine
calls, and the only path a delegated subagent may be minted on:

* one gate — the same chain as spawn (approved definition, scope subset of
  definition ∩ parent ∩ delegating user, CAS budget reservation);
* one caller identity — the delegating user plus the run they are on;
* one killswitch — ``HARNESS_SUBAGENTS=1`` enables; anything else refuses with
  ``SUBAGENTS_DISABLED`` and reaches no ledger row;
* one linkage — each child carries ``parent_id`` pointing at the parent's
  assignment on this run; depth, live-children and the run pool cap it exactly
  as the plain spawn gate does.

This is by no means the engine integration: the Hx SDK plugin that would call
this from a live run (the true "engine-driven" wiring) is the next slice, and
the child-run dispatch branch lands with it. Until then the verb is API-level —
plus the kill-switch means it ships inert.

S1a scope: minting children only, ``count``-many per call. Claiming, isolated
roots, synthesis and cancellation cascade are subsequent slices of W28.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from harness_orchestrator import agent_tree as _at
from harness_orchestrator.agent_tree import (
    ScopeBody,
    Writer,
    _http,
    _micro,
    _mint_child,
    _norm_scope,
)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["delegation"])

DEFAULT_BUDGET_USD = Decimal("1.00")
# Live children one call may mint. Deliberately tight; widen only with measured use.
MAX_PER_CALL = 4


def _enabled() -> bool:
    return os.environ.get("HARNESS_SUBAGENTS", "").strip() == "1"


class DelegationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    agentId: str = Field(min_length=1, max_length=36)
    count: int = Field(default=1, ge=1, le=MAX_PER_CALL)
    # The scope of the children: a subset of what the caller themselves holds.
    scope: ScopeBody = Field(default_factory=ScopeBody)
    budgetUsd: Decimal = Field(default=DEFAULT_BUDGET_USD, ge=Decimal("0.001"), le=Decimal("1000"))
    requestId: str | None = Field(default=None, max_length=64)
    # The assignment that requested the delegation; NULL mints it as a root task.
    parentAssignmentId: str | None = Field(default=None, max_length=36)


@router.post("/runs/{runId}/delegations", status_code=201)
async def delegate(
    runId: str, body: DelegationBody, response: Response, p: Writer
) -> dict[str, Any]:
    if not _enabled():
        raise _http(
            403,
            "SUBAGENTS_DISABLED",
            "delegated subagents are disabled by default; set HARNESS_SUBAGENTS=1 to enable",
        )
    request_id = body.requestId or f"deleg-{uuid4().hex[:24]}"
    scope = _norm_scope(body.scope)
    budget = _micro(body.budgetUsd)
    title = body.title.strip()
    if not title:
        raise _http(400, "TITLE_REQUIRED", "title must not be blank")
    factory = _at._factory()
    children: list[dict[str, Any]] = []
    for index in range(body.count):
        child, duplicate = await _mint_child(
            factory, p, runId,
            request_id=f"{request_id}-child-{index}",
            parent_assignment_id=body.parentAssignmentId,
            agent_id=body.agentId,
            title=title if body.count == 1 else f"{title} ({index + 1}/{body.count})",
            scope=scope,
            budget_microusd=budget,
        )
        children.append({**child, "duplicate": duplicate})
    return {"children": children, "minted": sum(1 for child in children if not child["duplicate"])}
