"""Sprint 2 Task 15 — W31 memory-tier proposals attached to a run (C-08 seam, proposal only).

* Off unless ``HARNESS_SCHEDULER_PROPOSALS=1``; the routes answer 404 FEATURE_OFF otherwise.
* The solver is NOT vendored: ``w31/tier_ttl.py`` is loaded by file path from
  ``HARNESS_W31_ROOT`` (the experiment directory), the same by-path pattern the W31 ADR uses
  for SCHEDULER_ROOT. Missing path or missing OR-Tools -> 503 SCHEDULER_UNAVAILABLE, never a
  silent fallback.
* Admin only. The caller supplies per-item usage (bytes, hits/day, billing approval): the
  Harness records no memory-usage telemetry, so it does not invent any.
* The stored row is the proposal plus the W31 validator's violations. Nothing reads it to move
  memory or dispatch work; the run's state is untouched
  (docs/briefs/2026-11-05-w32-execution-seam.md).
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from functools import cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from harness_contracts.roles import Principal, Role
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from harness_orchestrator import api
from harness_orchestrator.plans import _now

router = APIRouter(prefix="/api/v1/orchestrator", tags=["scheduler-proposals"])
FLAG_ENV, ROOT_ENV = "HARNESS_SCHEDULER_PROPOSALS", "HARNESS_W31_ROOT"
RunReader = Annotated[Principal, Depends(api._authz_dep("read", audit=True))]


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    bytes: int = Field(gt=0, le=1_048_576)
    hits_per_day: int = Field(ge=0, le=1_000_000)
    billing_approved: bool = False


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Item] = Field(min_length=1, max_length=500)


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"error": {"code": code, "message": message}})


def _gate(p: Principal) -> None:
    if os.environ.get(FLAG_ENV, "0") != "1":
        raise _err(404, "FEATURE_OFF", "scheduler proposals are not enabled")
    if p.role != Role.ADMIN:
        raise _err(403, "ROLE_DENIED", "only team admins may read or request scheduler proposals")


@cache
def _load(root: str) -> Any:
    path = Path(root) / "w31" / "tier_ttl.py"
    spec = importlib.util.spec_from_file_location("w31_tier_ttl", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise ImportError(f"no W31 tier module at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # imports ortools; ImportError when it is not installed
    return module


def load_tier_ttl() -> Any:
    root = os.environ.get(ROOT_ENV, "")
    try:
        if not root:
            raise ImportError(f"{ROOT_ENV} is not set")
        return _load(root)
    except ImportError as e:
        raise _err(503, "SCHEDULER_UNAVAILABLE", f"W31 solver unavailable: {e}") from e


def _view(row: Any) -> dict[str, Any]:
    violations = json.loads(row["violations"])
    return {"runId": row["run_id"], "schema": row["schema_tag"],
            "proposal": json.loads(row["proposal"]), "violations": violations,
            "valid": not violations, "proposalOnly": True, "requiresHumanApproval": True,
            "createdBy": row["created_by"]}


@router.post("/runs/{runId}/scheduler-proposals", status_code=201)
async def create_proposal(runId: str, req: ProposalRequest, p: RunReader) -> dict[str, Any]:
    _gate(p)
    w31 = load_tier_ttl()
    items = [i.model_dump() for i in req.items]
    proposal = w31.solve(items)
    row = {"id": str(uuid.uuid4()), "run_id": runId, "team_id": p.teamId,
           "schema_tag": str(proposal.get("schema", "unknown")), "proposal": json.dumps(proposal),
           "violations": json.dumps(w31.validate(items, proposal)), "created_by": p.userId,
           "created_at": _now()}
    async with api._service.session_factory() as db:
        await db.execute(text(
            "INSERT INTO scheduler_proposals (id, run_id, team_id, schema_tag, proposal,"
            " violations, created_by, created_at) VALUES (:id, :run_id, :team_id, :schema_tag,"
            " :proposal, :violations, :created_by, :created_at)"), row)
        await db.commit()
    return _view(row)


@router.get("/runs/{runId}/scheduler-proposals")
async def latest_proposal(runId: str, p: RunReader) -> dict[str, Any]:
    _gate(p)
    async with api._service.session_factory() as db:
        row = (await db.execute(text(
            "SELECT * FROM scheduler_proposals WHERE run_id = :r AND team_id = :t"
            " ORDER BY created_at DESC LIMIT 1"), {"r": runId, "t": p.teamId})).mappings().first()
    return {"runId": runId, "latest": _view(row) if row else None}
