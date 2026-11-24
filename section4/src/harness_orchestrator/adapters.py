"""Task 10 — external agent adapter protocol v1: register an external agent to observe a run.

Decision + threat model: docs/decisions/2026-11-24-external-agent-adapter.md.

* A human writer of the run registers the agent (identity = authenticated Principal; the run
  guard answers foreign/unknown runs with the shared 404). The agent gets an observer lease.
* The lease grants NOTHING: ``mode`` is always ``observer`` and ``authority`` is always empty.
  ``maxScope`` is the agent's request, recorded for the human, never applied; scope widens only
  through the existing leader approval routes (collab.py), which an adapter cannot call.
* Capabilities are a closed set. Anything that sounds like authority (approve, grant, merge,
  execute) is rejected with 422 rather than silently dropped.
* ``digest`` binds the lease to the exact registration body (sha256, canonical JSON).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api, collab
from harness_orchestrator.plans import _now

router = APIRouter(prefix="/api/v1/orchestrator", tags=["adapters"])
PROTOCOL = "1"
Capability = Literal["read_events", "read_plan", "propose_patch", "comment"]


class AdapterRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agentId: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    protocolVersion: Literal["1"]
    capabilities: list[Capability] = Field(min_length=1, max_length=4)
    maxScope: list[str] = Field(default_factory=list, max_length=collab.MAX_PATHS)
    pubkey: str = Field(min_length=32, max_length=4096)


def registration_digest(req: AdapterRegistration) -> str:
    body = req.model_dump()
    body["capabilities"] = sorted(set(body["capabilities"]))
    body["maxScope"] = sorted(set(body["maxScope"]))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _view(row: dict) -> dict:
    return {"leaseId": row["id"], "runId": row["run_id"], "agentId": row["agent_id"],
            "protocolVersion": row["protocol"], "capabilities": json.loads(row["capabilities"]),
            "requestedScope": json.loads(row["max_scope"]), "digest": row["digest"],
            "mode": "observer", "authority": [], "registeredBy": row["registered_by"]}


@router.post("/runs/{runId}/adapters/register", status_code=201)
async def register_adapter(runId: str, req: AdapterRegistration, actor: api.CollabWriter) -> dict:
    api._pilot_gate(actor)
    scope = sorted(set(req.maxScope))

    async def go(factory) -> dict:
        if scope:
            collab.validate_paths(scope)  # same traversal rules as approvals; 422 on '..'
        row = {"id": str(uuid.uuid4()), "run_id": runId, "team_id": actor.teamId,
               "agent_id": req.agentId, "protocol": PROTOCOL,
               "capabilities": json.dumps(sorted(set(req.capabilities))),
               "max_scope": json.dumps(scope),
               "pubkey_sha256": hashlib.sha256(req.pubkey.encode()).hexdigest(),
               "digest": registration_digest(req), "registered_by": actor.userId,
               "created_at": _now()}
        async with factory() as db:
            try:
                await db.execute(text(
                    "INSERT INTO run_adapter_leases (id, run_id, team_id, agent_id, protocol,"
                    " capabilities, max_scope, pubkey_sha256, digest, mode, registered_by,"
                    " created_at) VALUES (:id, :run_id, :team_id, :agent_id, :protocol,"
                    " :capabilities, :max_scope, :pubkey_sha256, :digest, 'observer',"
                    " :registered_by, :created_at)"), row)
            except IntegrityError as e:
                raise collab.CollabError(409, "ADAPTER_REGISTERED",
                                         "this agent already has a lease on this run") from e
            await collab._audit(db, actor, "adapter.registered", runId,
                                {"agentId": req.agentId, "leaseId": row["id"],
                                 "digest": row["digest"], "scopeRequested": len(scope)})
            await db.commit()
        return _view(row)

    return await api._collab(go)


@router.get("/runs/{runId}/adapters")
async def list_adapters(runId: str, actor: api.CollabReader) -> dict:
    api._pilot_gate(actor)

    async def go(factory) -> dict:
        async with factory() as db:
            rows = (await db.execute(text(
                "SELECT * FROM run_adapter_leases WHERE run_id = :r ORDER BY agent_id"),
                {"r": runId})).mappings().all()
        return {"adapters": [_view(dict(r)) for r in rows]}

    return await api._collab(go)
