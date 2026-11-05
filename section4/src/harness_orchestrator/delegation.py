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

S1a scope: minting children only, ``count``-many per call. S1b-1 gave each child its own run
and the cancellation cascade; S1b-2 is the read-only synthesis a human reviews (``/synthesis``).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import bindparam, text

from harness_orchestrator import agent_tree as _at
from harness_orchestrator.agent_tree import (
    Reader,
    ScopeBody,
    Writer,
    _audit,
    _covered,
    _http,
    _micro,
    _mint_child,
    _norm_scope,
    _terminal,
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


async def _open_child_run(
    factory: Any, p: Any, parent_run_id: str, assignment_id: str, *, title: str, description: str,
    paths: list[str],
) -> tuple[str, str]:
    """Give a freshly minted child its own run and propose its plan for human approval.

    Returns ``(run_id, plan_id)``: a human approves with the plan id, which only this call knows.

    The child follows the ordinary lifecycle (propose, approve, start, queue, claim), so every gate
    that guards a normal run guards it too, and its engine gets its own workspace from its run id.
    It is never auto-approved. If the run cannot be opened the assignment is cancelled, so a failed
    delegation leaves nothing live and nothing reserved.
    """
    child_run_id = str(uuid4())
    async with factory() as db:
        project_id = (await db.execute(text("SELECT project_id FROM agent_runs WHERE id=:r"),
                                       {"r": parent_run_id})).scalar_one()
        await db.execute(text("UPDATE agent_assignments SET child_run_id=:c WHERE id=:i"),
                         {"c": child_run_id, "i": assignment_id})
        await db.commit()
    try:
        goal = f"{title}\n\n{description}".strip()
        plan_id = await _at._svc().propose_plan(child_run_id, goal=goal, project_id=project_id)
        # Bind the scope: the engine's shell gate and container mounts enforce the run intent's
        # scopePaths, and a run without them is unconfined. Version 1, so a later human edit of
        # the intent (audited, and it voids open approvals) is the only way to widen it.
        async with factory() as db:
            await db.execute(text(
                "INSERT INTO run_intent_versions (run_id, version, team_id, goal, scope_paths,"
                " permissions, changed_fields, updated_by, updated_at, request_id)"
                " VALUES (:r, 1, :t, :g, :s, '[]', :c, :u, :n, :q)"),
                {"r": child_run_id, "t": p.teamId, "g": goal, "s": json.dumps(paths),
                 "c": json.dumps(["scopePaths"]), "u": p.userId, "n": _at.db_now(),
                 "q": f"deleg-scope-{assignment_id}"})
            await db.commit()
    except Exception as e:  # noqa: BLE001 - any planning failure must undo the delegation
        async with factory() as db:
            await db.execute(text("UPDATE agent_assignments SET child_run_id=NULL WHERE id=:i"),
                             {"i": assignment_id})
            if await _terminal(db, assignment_id, "cancelled", _at.db_now()):
                await _audit(db, p, "agent.cancelled", assignment_id, {
                    "runId": parent_run_id, "reason": "child_run_failed",
                    "error": type(e).__name__})
            await db.commit()
        raise _http(502, "CHILD_RUN_FAILED",
                    f"the child run could not be planned: {type(e).__name__}") from e
    return child_run_id, plan_id


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
    if not scope["paths"]:  # an intent without paths is unconfined, so it cannot bind anything
        raise _http(422, "SCOPE_UNBINDABLE",
                    "a delegated scope must name at least one path; none would mean unrestricted")
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
        if not duplicate:
            child["childRunId"], child["childPlanId"] = await _open_child_run(
                factory, p, runId, child["id"], title=child["title"], description=body.description,
                paths=scope["paths"])
        children.append({**child, "duplicate": duplicate})
    return {"children": children, "minted": sum(1 for child in children if not child["duplicate"])}


def _overlaps(a: list[str], b: list[str]) -> list[str]:
    """Paths of ``a`` that fall under, or contain, a path of ``b``."""
    return sorted({x for x in a if _covered(x, b)} | {y for y in b if _covered(y, a)})


@router.get("/runs/{runId}/synthesis")
async def synthesis(runId: str, p: Reader) -> dict[str, Any]:
    """What the delegated children did, for a human to review. Read-only: nothing is merged.

    Per child: owner, scope, state, spend (from ``cost_ledger``) and the run's final response.
    ``conflicts`` lists live or finished siblings (same parent) whose path scopes overlap, which is
    where two agents could have written the same file. A cancelled child cannot conflict.
    """
    async with _at._factory()() as db:
        rows = (await db.execute(
            text("SELECT * FROM agent_assignments WHERE run_id=:r AND team_id=:t"
                 " ORDER BY created_at, id"), {"r": runId, "t": p.teamId})).mappings().all()
    run_ids = [r["child_run_id"] for r in rows if r["child_run_id"]]
    spent: dict[str, Decimal] = {}
    if run_ids:  # the assignment's own counter is never fed by the run, so the ledger is the truth
        async with _at._factory()() as db:
            ledger = (await db.execute(
                text("SELECT run_id, cost_usd FROM cost_ledger WHERE run_id IN :ids")
                .bindparams(bindparam("ids", expanding=True)), {"ids": run_ids})).all()
        for run_id, cost in ledger:
            spent[run_id] = spent.get(run_id, Decimal(0)) + Decimal(str(cost))
    children = []
    for r in rows:
        if not r["child_run_id"]:
            continue  # a plain spawn has no run of its own, so it has no result to review
        result = await _at._svc().get_engine_result(r["child_run_id"])
        children.append({
            "assignmentId": r["id"], "parentId": r["parent_id"], "childRunId": r["child_run_id"],
            "title": r["title"], "state": r["state"], "agentName": r["agent_name"],
            "agentVersion": r["agent_version"], "delegatedBy": r["delegated_by"],
            "scope": json.loads(r["scope"]),
            "spentUsd": _at._usd(_micro(spent.get(r["child_run_id"], Decimal(0)))),
            "runStatus": result.get("status"), "result": result.get("finalResponse"),
        })
    live = [c for c in children if c["state"] != "cancelled"]
    conflicts = []
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            shared = _overlaps(a["scope"]["paths"], b["scope"]["paths"])
            if a["parentId"] == b["parentId"] and shared:
                conflicts.append({"assignmentIds": [a["assignmentId"], b["assignmentId"]],
                                  "paths": shared})
    return {"runId": runId, "children": children, "conflicts": conflicts,
            "reviewRequired": True, "autoMerge": False}
