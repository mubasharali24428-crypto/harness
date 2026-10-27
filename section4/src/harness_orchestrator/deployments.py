"""Deployment requests and their approval (W9), bound to the reviewed revision.

Nothing here deploys anything. The guarantee this module makes is narrower and
checkable: an approval names the exact revision a human reviewed, and stops being
valid when that revision moves.

The binding is the triple the merge approval already uses — ``changes_digest``
(path + diff_sha of every current change), the run's recorded head sha, and the
plan version — captured when the request is made and re-read when a decision is
taken. A head that moved, a patch that arrived or a new plan version therefore
turns the request ``stale`` (409 ``DEPLOYMENT_STALE``) instead of approving bytes
nobody looked at. Revocation is final: a revoked request can never be approved,
and revoking twice reports the first revocation rather than starting a new one.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from harness_contracts.roles import Principal
from pydantic import BaseModel, Field
from sqlalchemy import text

from harness_orchestrator import artifacts as _art

router = APIRouter(prefix="/api/v1/orchestrator", tags=["deployments"])

#: Environment names are operator-chosen but constrained: they end up in an approval
#: record a human is asked to trust, so "Prod " and "prod" must not both exist. The
#: name is refused rather than rewritten: an approval that records a different
#: environment than the operator typed misreports what was approved.
ENV_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,59}$")

_COLS = (
    "id, team_id, run_id, environment, changes_digest, run_head_sha, base_sha, plan_version,"
    " state, requested_by, requested_at, decided_by, decided_at, revoked_by, revoked_at, note,"
    " request_id"
)


class DeployBody(BaseModel):
    environment: str = Field(min_length=1, max_length=60)
    note: str | None = Field(default=None, max_length=500)
    requestId: str | None = Field(default=None, max_length=64)


class DecisionBody(BaseModel):
    changesDigest: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class RevokeBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


async def _current_revision(db: Any, p: Principal, run_id: str) -> dict[str, Any]:
    """What the run looks like RIGHT NOW: the thing an approval is bound to."""
    rows = await _art._current_changes(db, p, run_id)
    git = (
        await db.execute(
            text(
                "SELECT run_head_sha, base_sha FROM run_git_state"
                " WHERE run_id = :r AND team_id = :t"
            ),
            {"r": run_id, "t": p.teamId},
        )
    ).first()
    return {
        "changesDigest": _art.changes_digest(rows),
        "runHeadSha": git.run_head_sha if git else None,
        "baseSha": git.base_sha if git else None,
        "planVersion": await _art._plan_version(db, p, run_id),
        "changeCount": len(rows),
    }


async def _find(db: Any, p: Principal, run_id: str, deployment_id: str) -> Any:
    row = (
        await db.execute(
            text(f"SELECT {_COLS} FROM deployments WHERE id = :i AND run_id = :r AND team_id = :t"),
            {"i": deployment_id[:64], "r": run_id, "t": p.teamId},
        )
    ).first()
    if row is None:
        raise _art._not_found("deployment")
    return row


def _stale_fields(row: Any, current: dict[str, Any]) -> list[str]:
    """Which parts of the binding moved. Empty means the approval still names this revision."""
    moved = []
    if row.changes_digest != current["changesDigest"]:
        moved.append("changesDigest")
    if row.run_head_sha != current["runHeadSha"]:
        moved.append("runHeadSha")
    if row.plan_version != current["planVersion"]:
        moved.append("planVersion")
    return moved


def _view(row: Any, current: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "id": row.id,
        "runId": row.run_id,
        "environment": row.environment,
        "state": row.state,
        "binding": {
            "changesDigest": row.changes_digest,
            "runHeadSha": row.run_head_sha,
            "baseSha": row.base_sha,
            "planVersion": row.plan_version,
        },
        "requestedBy": row.requested_by,
        "requestedAt": _art._iso(row.requested_at),
        "decidedBy": row.decided_by,
        "decidedAt": _art._iso(row.decided_at),
        "revokedBy": row.revoked_by,
        "revokedAt": _art._iso(row.revoked_at),
        "note": row.note,
    }
    if current is not None:
        moved = _stale_fields(row, current)
        out["stale"] = bool(moved) and row.state != "revoked"
        out["staleFields"] = moved
    return out


# ----------------------------------------------------------------------- request


@router.post("/runs/{runId}/deployments", status_code=201)
async def request_deployment(runId: str, body: DeployBody, p: _art.Writer) -> Any:
    """Ask for this revision to be deployed. Binds what has been reviewed, as it is now."""

    async def go() -> Any:
        environment = body.environment
        if not ENV_RE.match(environment):
            raise _art.ArError(
                409,
                "ENVIRONMENT_INVALID",
                "environment must be lower-case letters, digits, dot, dash or underscore",
            )
        async with _art._need_factory()() as db:
            current = await _current_revision(db, p, runId)
            if not current["changeCount"]:
                raise _art.ArError(
                    409, "NOTHING_TO_DEPLOY", "the run has no recorded changes to deploy"
                )
            if not current["runHeadSha"]:
                raise _art.ArError(
                    409,
                    "REVISION_UNKNOWN",
                    "record the run's head sha (PUT git-state) before requesting a deployment",
                )
            if body.requestId:
                existing = (
                    await db.execute(
                        text(
                            f"SELECT {_COLS} FROM deployments WHERE run_id = :r AND request_id = :q"
                        ),
                        {"r": runId, "q": body.requestId},
                    )
                ).first()
                if existing is not None:
                    return JSONResponse(
                        {
                            "deployment": _view(existing, current),
                            "replayed": True,
                        },
                        headers=_art._no_store(),
                    )
            now = _art._now()
            dep_id = str(uuid.uuid4())
            await db.execute(
                text(
                    "INSERT INTO deployments (id, team_id, run_id, environment, changes_digest,"
                    " run_head_sha, base_sha, plan_version, state, requested_by, requested_at,"
                    " note, request_id) VALUES (:i, :t, :r, :e, :d, :h, :b, :v, 'requested', :by,"
                    " :n, :note, :q)"
                ),
                {
                    "i": dep_id,
                    "t": p.teamId,
                    "r": runId,
                    "e": environment,
                    "d": current["changesDigest"],
                    "h": current["runHeadSha"],
                    "b": current["baseSha"],
                    "v": current["planVersion"],
                    "by": p.userId,
                    "n": now,
                    "note": body.note,
                    "q": body.requestId,
                },
            )
            await _art._audit(
                db,
                p,
                "deployment.requested",
                "deployment",
                dep_id,
                {
                    "runId": runId,
                    "environment": environment,
                    "changesDigest": current["changesDigest"],
                    "runHeadSha": current["runHeadSha"],
                    "planVersion": current["planVersion"],
                },
            )
            await db.commit()
            row = await _find(db, p, runId, dep_id)
        return JSONResponse(
            {"deployment": _view(row, current), "replayed": False},
            status_code=201,
            headers=_art._no_store(),
        )

    return await _art._handled(go())


# -------------------------------------------------------------------------- list


@router.get("/runs/{runId}/deployments")
async def list_deployments(runId: str, p: _art.Reader) -> Any:
    """Requests for this run, each with a live `stale` verdict against the current revision."""

    async def go() -> Any:
        async with _art._need_factory()() as db:
            current = await _current_revision(db, p, runId)
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_COLS} FROM deployments WHERE run_id = :r AND team_id = :t"
                        " ORDER BY requested_at DESC"
                    ),
                    {"r": runId, "t": p.teamId},
                )
            ).all()
        return JSONResponse(
            {"runId": runId, "items": [_view(r, current) for r in rows]},
            headers=_art._no_store(),
        )

    return await _art._handled(go())


# ------------------------------------------------------------------------ decide


@router.post("/runs/{runId}/deployments/{deploymentId}/approve")
async def approve_deployment(
    runId: str, deploymentId: str, body: DecisionBody, p: _art.Approver
) -> Any:
    """Approve the revision the request names — and only that one."""

    async def go() -> Any:
        async with _art._need_factory()() as db:
            row = await _find(db, p, runId, deploymentId)
            if row.state == "revoked":
                raise _art.ArError(409, "DEPLOYMENT_REVOKED", "this request was revoked")
            if row.state != "requested":
                raise _art.ArError(
                    409, "DEPLOYMENT_DECIDED", f"this request is already {row.state}"
                )
            current = await _current_revision(db, p, runId)
            if body.changesDigest != row.changes_digest:
                raise _art.ArError(
                    409,
                    "DEPLOYMENT_STALE",
                    "the digest you approved is not the one this request was bound to",
                    {"staleFields": ["clientDigest"]},
                )
            moved = _stale_fields(row, current)
            if moved:
                raise _art.ArError(
                    409,
                    "DEPLOYMENT_STALE",
                    "the reviewed revision moved after this request was made",
                    {"staleFields": moved, "current": current},
                )
            now = _art._now()
            await db.execute(
                text(
                    "UPDATE deployments SET state = 'approved', decided_by = :u, decided_at = :n"
                    " WHERE id = :i AND state = 'requested'"
                ),
                {"u": p.userId, "n": now, "i": row.id},
            )
            await _art._audit(
                db,
                p,
                "deployment.approved",
                "deployment",
                row.id,
                {
                    "runId": runId,
                    "environment": row.environment,
                    "changesDigest": row.changes_digest,
                    "runHeadSha": row.run_head_sha,
                    "planVersion": row.plan_version,
                },
            )
            await db.commit()
            row = await _find(db, p, runId, deploymentId)
        return JSONResponse(
            {"deployment": _view(row, current)}, headers=_art._no_store()
        )

    return await _art._handled(go())


@router.post("/runs/{runId}/deployments/{deploymentId}/revoke")
async def revoke_deployment(
    runId: str, deploymentId: str, body: RevokeBody, p: _art.Approver
) -> Any:
    """Withdraw the approval. Final and idempotent: nothing can approve it afterwards."""

    async def go() -> Any:
        async with _art._need_factory()() as db:
            row = await _find(db, p, runId, deploymentId)
            if row.state == "revoked":
                return JSONResponse(
                    {"deployment": _view(row)}, headers=_art._no_store()
                )
            now = _art._now()
            await db.execute(
                text(
                    "UPDATE deployments SET state = 'revoked', revoked_by = :u, revoked_at = :n"
                    " WHERE id = :i AND state != 'revoked'"
                ),
                {"u": p.userId, "n": now, "i": row.id},
            )
            await _art._audit(
                db,
                p,
                "deployment.revoked",
                "deployment",
                row.id,
                {"runId": runId, "environment": row.environment, "fromState": row.state},
            )
            await db.commit()
            row = await _find(db, p, runId, deploymentId)
        return JSONResponse({"deployment": _view(row)}, headers=_art._no_store())

    return await _art._handled(go())
