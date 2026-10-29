"""Marathon 2.0 W13 — bring-your-own runners (Section 1 side).

A runner is a customer-controlled host that executes work outside the harness
deployment. The harness keeps plan approval, policy, metering and audit; the
customer keeps the machine. This router owns the control plane only:

- registration mints a one-time bearer token (``hrn_…``) and stores only its
  sha256 plus a one-way attestation key derived from it;
- revocation is a row update — the next request with the token is refused,
  there is no cache to wait out;
- every runner-facing request must carry ``X-Runner-Nonce`` and
  ``X-Runner-Timestamp`` (epoch seconds). A nonce is single-use per runner
  inside the accepted skew window, so a captured request cannot be replayed;
- confinement attestations are signed with the derived key, digest-bound and
  stored; they prove the token holder reported these facts, NOT that the host
  is honest (documented in docs/m2-w13-plan.md);
- jobs are offered to a runner only by a team admin and only for a **queued**
  run of that admin's own team. A stolen token can therefore read and claim
  nothing of another team: foreign ids answer the same 404 as unknown ids.

Exit evidence for the slice: revocation, replay resistance and team isolation
are proven live by ``scripts/e2e/w13_runner_drill.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, can, error_body
from harness_contracts import hash_token as _hash_token
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from .audit_emit import (
    emit_runner_attested,
    emit_runner_job_assigned,
    emit_runner_job_completed,
    emit_runner_registered,
    emit_runner_revoked,
)
from .auth import get_principal
from .models import Project, RunnerJob, TeamRunner

router = APIRouter(prefix="/api/v1")

TOKEN_PREFIX = "hrn_"
ATTESTATION_KEY_LABEL = b"harness-runner-attestation-v1"
MAX_DESCRIPTION = 500
MAX_ATTESTATION_BYTES = 8192
CLOCK_SKEW_SECONDS = 300
LEASE_SECONDS = 180
MAX_JOBS_PER_RUNNER = 20
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_CONFINEMENT = ("docker", "process", "none")
JOB_STATES = ("offered", "leased", "running", "completed", "failed", "released")
_ATT_KEYS = {"schema", "confinement", "os", "hostIdSha256", "runnerVersion", "policyDigest"}


def derive_attestation_key(token: str) -> str:
    """HMAC-SHA256(token, label) as hex — the verifier key, never the token."""
    return hmac.new(token.encode("utf-8"), ATTESTATION_KEY_LABEL, hashlib.sha256).hexdigest()


def canonical_attestation(attestation: dict[str, Any]) -> str:
    return json.dumps(
        attestation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def attestation_digest(attestation: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_attestation(attestation).encode("utf-8")).hexdigest()


def sign_attestation(attestation: dict[str, Any], token: str) -> str:
    key = bytes.fromhex(derive_attestation_key(token))
    message = canonical_attestation(attestation).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _runner_public(row: TeamRunner) -> dict[str, Any]:
    return {
        "id": row.id,
        "teamId": row.team_id,
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "confinement": row.confinement,
        "tokenHint": row.token_hint,
        "lastSeenAt": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "attestedAt": row.attestation_at.isoformat() if row.attestation_at else None,
        "attestationDigest": row.attestation_sha256,
        "revokedAt": row.revoked_at.isoformat() if row.revoked_at else None,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _job_public(row: RunnerJob) -> dict[str, Any]:
    return {
        "runId": row.run_id,
        "projectId": row.project_id,
        "state": row.state,
        "leaseExpiresAt": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
        "resultDigest": row.result_digest,
    }


async def _owned_team(db, team_id: str, principal: Principal) -> bool:
    from .models import Team

    if team_id != principal.teamId:
        return False
    exists = (await db.execute(select(Team.id).where(Team.id == team_id))).scalar_one_or_none()
    return exists is not None


def _require_admin(principal: Principal) -> None:
    if principal.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "only team admins can manage runners"),
        )


def _require_write(principal: Principal) -> None:
    if not can(principal, "write"):
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "viewer/guest roles cannot assign runner jobs"),
        )


# ---------------------------------------------------------------- runner auth


@dataclass(frozen=True)
class RunnerAuth:
    id: str
    team_id: str
    name: str
    confinement: str
    attestation_key: str


async def _authenticate(db, request: Request) -> RunnerAuth:
    """Resolve the runner token and burn the request nonce.

    Order matters: token status is checked before the nonce is written, so a
    revoked runner cannot fill the nonce table; the nonce INSERT is the
    atomic replay arbiter (composite primary key, no read-modify-write).
    """
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail=error_body("UNAUTHORIZED", "runner token required")
        )
    token = auth.removeprefix("Bearer ").strip()
    if not token.startswith(TOKEN_PREFIX):
        raise HTTPException(
            status_code=401, detail=error_body("UNAUTHORIZED", "runner token required")
        )
    row = (
        await db.execute(select(TeamRunner).where(TeamRunner.token_sha256 == _hash_token(token)))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=401, detail=error_body("UNAUTHORIZED", "invalid runner token")
        )
    if row.status != "active":
        # A revoked token is dead on the very next request: no grace window.
        raise HTTPException(
            status_code=401, detail=error_body("UNAUTHORIZED", "runner token revoked")
        )

    nonce = request.headers.get("x-runner-nonce", "")
    raw_ts = request.headers.get("x-runner-timestamp", "")
    if not _NONCE.fullmatch(nonce):
        raise HTTPException(
            status_code=401, detail=error_body("UNAUTHORIZED", "X-Runner-Nonce must be 16-64 chars")
        )
    try:
        ts = int(raw_ts)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail=error_body("STALE_REQUEST", "X-Runner-Timestamp must be epoch seconds"),
        ) from None
    now = _now()
    if abs(now.timestamp() - ts) > CLOCK_SKEW_SECONDS:
        raise HTTPException(
            status_code=401,
            detail=error_body("STALE_REQUEST", f"timestamp outside +/-{CLOCK_SKEW_SECONDS}s"),
        )

    from .models import RunnerNonce

    # Prune only what can no longer be replayed (twice the accepted window).
    cutoff = now - timedelta(seconds=2 * CLOCK_SKEW_SECONDS)
    await db.execute(delete(RunnerNonce).where(RunnerNonce.seen_at < cutoff))
    db.add(RunnerNonce(runner_id=row.id, nonce=nonce, seen_at=now))
    try:
        await db.flush()
    except IntegrityError:  # the composite PK is the replay detector
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=error_body("REPLAY_REJECTED", "nonce already used")
        ) from None

    row.last_seen_at = now
    # Commit the nonce NOW: a later route error must not roll it back, or the
    # same captured request could be replayed after a 404/409.
    await db.commit()
    return RunnerAuth(
        id=row.id,
        team_id=row.team_id,
        name=row.name,
        confinement=row.confinement,
        attestation_key=row.attestation_key,
    )


def _validate_attestation(attestation: dict[str, Any], expected: str) -> None:
    if set(attestation) != _ATT_KEYS:
        extra = sorted(set(attestation) - _ATT_KEYS)
        missing = sorted(_ATT_KEYS - set(attestation))
        raise HTTPException(
            status_code=422,
            detail=error_body(
                "VALIDATION_ERROR", f"attestation keys: missing={missing} unknown={extra}"
            ),
        )
    if attestation["schema"] != 1:
        raise HTTPException(
            status_code=422, detail=error_body("VALIDATION_ERROR", "attestation schema must be 1")
        )
    mode = attestation["confinement"]
    if mode not in _CONFINEMENT:
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", f"confinement must be one of {_CONFINEMENT}"),
        )
    if mode != expected:
        raise HTTPException(
            status_code=409,
            detail=error_body(
                "ATTESTATION_MISMATCH",
                f"runner was registered with confinement {expected!r}, attested {mode!r}",
            ),
        )
    for key in ("os", "runnerVersion"):
        value = attestation[key]
        if not isinstance(value, str) or not 1 <= len(value) <= 32:
            raise HTTPException(
                status_code=422, detail=error_body("VALIDATION_ERROR", f"{key} must be 1-32 chars")
            )
    for key in ("hostIdSha256", "policyDigest"):
        value = attestation[key]
        if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
            raise HTTPException(
                status_code=422,
                detail=error_body("VALIDATION_ERROR", f"{key} must be a sha256 hex digest"),
            )
    if len(canonical_attestation(attestation).encode("utf-8")) > MAX_ATTESTATION_BYTES:
        raise HTTPException(
            status_code=413, detail=error_body("ATTESTATION_TOO_LARGE", "attestation exceeds 8KB")
        )


# ------------------------------------------------------------- admin surface


class RunnerBody(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION)
    confinement: str = Field(default="none")


class AssignBody(BaseModel):
    # Product run ids are uuid4 (36 chars) and the queue column is VARCHAR(36);
    # requiring the storage width keeps a malformed id out before any lookup.
    run_id: str = Field(alias="runId", min_length=36, max_length=36)

    model_config = {"populate_by_name": True}


@router.get("/team-runners")
async def list_runners(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    async with request.app.state.session_factory() as db:
        rows = (
            await db.execute(
                select(TeamRunner)
                .where(TeamRunner.team_id == principal.teamId)
                .order_by(TeamRunner.name)
            )
        ).scalars().all()
        return {"runners": [_runner_public(row) for row in rows]}


@router.post("/team-runners", status_code=201)
async def register_runner(
    body: RunnerBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Mint a runner. The token is returned ONCE and never stored in clear."""
    _require_admin(principal)
    if not _NAME.fullmatch(body.name):
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", f"name must match {_NAME.pattern}"),
        )
    if not body.description.strip():
        raise HTTPException(
            status_code=422, detail=error_body("VALIDATION_ERROR", "description must be non-empty")
        )
    if body.confinement not in _CONFINEMENT:
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", f"confinement must be one of {_CONFINEMENT}"),
        )
    async with request.app.state.session_factory() as db:
        existing = (
            await db.execute(
                select(TeamRunner).where(
                    TeamRunner.team_id == principal.teamId, TeamRunner.name == body.name
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=error_body("NAME_TAKEN", f"runner {body.name!r} already exists"),
            )
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        row = TeamRunner(
            team_id=principal.teamId,
            name=body.name,
            description=body.description.strip(),
            status="active",
            token_sha256=_hash_token(token),
            token_hint=token[-4:],
            attestation_key=derive_attestation_key(token),
            confinement=body.confinement,
            created_by=principal.userId,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        await emit_runner_registered(
            db,
            actor_user_id=principal.userId,
            team_id=row.team_id,
            target_id=row.id,
            detail={
                "name": row.name,
                "confinement": row.confinement,
                "hint": row.token_hint,
            },
        )
        return {
            "runner": _runner_public(row),
            "token": token,
            "attestationKeyLabel": ATTESTATION_KEY_LABEL.decode(),
            "note": "token is shown once; store it on the runner host",
        }


@router.post("/team-runners/{runner_id}/revoke", status_code=204)
async def revoke_runner(
    runner_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    _require_admin(principal)
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(TeamRunner).where(
                    TeamRunner.id == runner_id, TeamRunner.team_id == principal.teamId
                )
            )
        ).scalar_one_or_none()
        if row is None:
            # Foreign runner and unknown id are the same answer: no oracle.
            raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "runner not found"))
        if row.status == "revoked":
            return  # idempotent
        row.status = "revoked"
        row.revoked_by = principal.userId
        row.revoked_at = _now()
        row.updated_at = _now()
        await db.commit()
        await emit_runner_revoked(
            db,
            actor_user_id=principal.userId,
            team_id=row.team_id,
            target_id=row.id,
            detail={"name": row.name, "hint": row.token_hint},
        )


@router.post("/team-runners/{runner_id}/jobs", status_code=201)
async def assign_job(
    runner_id: str,
    body: AssignBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Offer a QUEUED run of the caller's team to one of its runners."""
    _require_write(principal)
    async with request.app.state.session_factory() as db:
        runner = (
            await db.execute(
                select(TeamRunner).where(
                    TeamRunner.id == runner_id, TeamRunner.team_id == principal.teamId
                )
            )
        ).scalar_one_or_none()
        if runner is None:
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "runner not found")
            )
        if runner.status != "active":
            raise HTTPException(
                status_code=409,
                detail=error_body("RUNNER_REVOKED", "a revoked runner takes no jobs"),
            )
        claim = (
            await db.execute(
                text(
                    "SELECT rc.state, rc.project_id FROM run_claims rc "
                    "JOIN projects p ON rc.project_id = p.id "
                    "WHERE rc.run_id = :r AND p.team_id = :t"
                ),
                {"r": body.run_id, "t": principal.teamId},
            )
        ).mappings().first()
        if claim is None:
            # Unknown, foreign and unqueued all avoid leaking team data: the
            # first two answer 404; a known-but-not-queued run answers 409.
            raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "run not found"))
        if claim["state"] != "queued":
            raise HTTPException(
                status_code=409,
                detail=error_body("NOT_QUEUED", f"run is {claim['state']}, not queued"),
            )
        existing = (
            await db.execute(select(RunnerJob).where(RunnerJob.run_id == body.run_id))
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=409, detail=error_body("JOB_TAKEN", "this run already has a runner job")
            )
        project = (
            await db.execute(select(Project).where(Project.id == claim["project_id"]))
        ).scalar_one_or_none()
        if project is None or project.team_id != principal.teamId:
            raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "run not found"))
        job = RunnerJob(
            run_id=body.run_id,
            runner_id=runner.id,
            project_id=claim["project_id"],
            state="offered",
        )
        db.add(job)
        await db.commit()
        await emit_runner_job_assigned(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=job.run_id,
            detail={"runnerId": runner.id, "projectId": job.project_id},
        )
        return {"job": _job_public(job)}


# ----------------------------------------------------------- runner surface


class AttestBody(BaseModel):
    attestation: dict
    signature: str = Field(min_length=64, max_length=64)


class FinishBody(BaseModel):
    status: str
    result_digest: str | None = Field(default=None, alias="resultDigest", max_length=64)

    model_config = {"populate_by_name": True}


async def _expire_leases(db, runner_id: str) -> None:
    """Release this runner's expired leases and orphan (never re-queue) the runs.

    Re-queueing would let the harness execute a run that the remote host may
    still be executing; ``orphaned`` requires an explicit human re-dispatch.
    """
    now = _now()
    expired = (
        await db.execute(
            select(RunnerJob).where(
                RunnerJob.runner_id == runner_id,
                RunnerJob.state.in_(("leased", "running")),
                RunnerJob.lease_expires_at < now,
            )
        )
    ).scalars().all()
    for job in expired[:MAX_JOBS_PER_RUNNER]:
        job.state = "released"
        job.lease_expires_at = None
        job.updated_at = now
        await db.execute(
            text(
                "UPDATE run_claims SET state = 'orphaned' "
                "WHERE run_id = :r AND state = 'running' AND worker_id = :w"
            ),
            {"r": job.run_id, "w": f"runner:{runner_id}"},
        )
    await db.commit()


async def _own_job_or_404(db, runner_id: str, run_id: str) -> RunnerJob:
    job = (
        await db.execute(
            select(RunnerJob).where(RunnerJob.run_id == run_id, RunnerJob.runner_id == runner_id)
        )
    ).scalar_one_or_none()
    if job is None:
        # Another team's (or unknown) run id: one answer, no existence oracle.
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "job not found"))
    return job


@router.post("/runner/heartbeat")
async def runner_heartbeat(body: AttestBody, request: Request) -> dict:
    async with request.app.state.session_factory() as db:
        auth = await _authenticate(db, request)
        _validate_attestation(body.attestation, auth.confinement)
        canonical = canonical_attestation(body.attestation)
        key = bytes.fromhex(auth.attestation_key)
        expected = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, body.signature):
            raise HTTPException(
                status_code=409,
                detail=error_body("ATTESTATION_MISMATCH", "attestation signature does not verify"),
            )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        row = (
            await db.execute(select(TeamRunner).where(TeamRunner.id == auth.id))
        ).scalar_one()
        changed = row.attestation_sha256 != digest
        row.attestation_json = canonical
        row.attestation_sha256 = digest
        row.attestation_at = _now()
        row.last_seen_at = _now()
        await db.commit()
        if changed:
            await emit_runner_attested(
                db,
                team_id=auth.team_id,
                target_id=auth.id,
                detail={
                    "confinement": body.attestation["confinement"],
                    "digest": digest[:12],
                    "os": body.attestation["os"],
                },
            )
        await _expire_leases(db, auth.id)
        return {
            "verified": True,
            "runnerId": auth.id,
            "teamId": auth.team_id,
            "confinement": auth.confinement,
            "attestationDigest": digest,
            "leaseSeconds": LEASE_SECONDS,
            "serverTime": _now().isoformat(),
        }


@router.get("/runner/jobs")
async def list_my_jobs(request: Request) -> dict:
    async with request.app.state.session_factory() as db:
        auth = await _authenticate(db, request)
        await _expire_leases(db, auth.id)
        jobs = (
            await db.execute(
                select(RunnerJob)
                .where(
                    RunnerJob.runner_id == auth.id,
                    RunnerJob.state.in_(("offered", "leased", "running")),
                )
                .order_by(RunnerJob.created_at)
                .limit(MAX_JOBS_PER_RUNNER)
            )
        ).scalars().all()
        return {"jobs": [_job_public(job) for job in jobs]}


@router.post("/runner/jobs/{run_id}/claim")
async def claim_job(run_id: str, request: Request) -> dict:
    async with request.app.state.session_factory() as db:
        auth = await _authenticate(db, request)
        await _expire_leases(db, auth.id)
        job = await _own_job_or_404(db, auth.id, run_id)
        if job.state != "offered":
            raise HTTPException(
                status_code=409,
                detail=error_body("JOB_NOT_OFFERED", f"job is {job.state}, not offered"),
            )
        # The queue row is the lease: one live claim per run, and the
        # orchestrator's own worker only ever takes state='queued'.
        claimed = (
            await db.execute(
                text(
                    "UPDATE run_claims SET state = 'running', worker_id = :w, "
                    "claimed_at = :now, heartbeat_at = :now "
                    "WHERE run_id = :r AND project_id = :p AND state = 'queued'"
                ),
                {
                    "w": f"runner:{auth.id}",
                    "now": _now().isoformat(),
                    "r": job.run_id,
                    "p": job.project_id,
                },
            )
        ).rowcount
        if not claimed:
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail=error_body("NOT_QUEUED", "the run is no longer queued"),
            )
        job.state = "leased"
        job.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
        job.updated_at = _now()
        await db.commit()
        return {
            "job": _job_public(job),
            "leaseSeconds": LEASE_SECONDS,
            "heartbeatPath": f"/api/v1/runner/jobs/{job.run_id}/heartbeat",
            "finishPath": f"/api/v1/runner/jobs/{job.run_id}/finish",
        }


@router.post("/runner/jobs/{run_id}/heartbeat")
async def heartbeat_job(run_id: str, request: Request) -> dict:
    async with request.app.state.session_factory() as db:
        auth = await _authenticate(db, request)
        await _expire_leases(db, auth.id)
        job = await _own_job_or_404(db, auth.id, run_id)
        if job.state not in ("leased", "running"):
            raise HTTPException(
                status_code=409,
                detail=error_body("JOB_NOT_LEASED", f"job is {job.state}, not leased"),
            )
        matched = (
            await db.execute(
                text(
                    "UPDATE run_claims SET heartbeat_at = :now "
                    "WHERE run_id = :r AND state = 'running' AND worker_id = :w"
                ),
                {"now": _now().isoformat(), "r": job.run_id, "w": f"runner:{auth.id}"},
            )
        ).rowcount
        if not matched:
            job.state = "released"
            job.lease_expires_at = None
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail=error_body("LEASE_LOST", "the run claim is no longer held by this runner"),
            )
        job.state = "running"
        job.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
        job.updated_at = _now()
        await db.commit()
        return {
            "state": job.state,
            "leaseExpiresAt": job.lease_expires_at.isoformat(),
        }


@router.post("/runner/jobs/{run_id}/finish")
async def finish_job(run_id: str, body: FinishBody, request: Request) -> dict:
    async with request.app.state.session_factory() as db:
        auth = await _authenticate(db, request)
        job = await _own_job_or_404(db, auth.id, run_id)
        if job.state not in ("leased", "running"):
            raise HTTPException(
                status_code=409,
                detail=error_body("JOB_NOT_LEASED", f"job is {job.state}, not leased"),
            )
        if body.status not in ("completed", "failed"):
            raise HTTPException(
                status_code=422,
                detail=error_body("VALIDATION_ERROR", "status must be completed or failed"),
            )
        digest = body.result_digest
        if digest is not None and not _SHA256_HEX.fullmatch(digest):
            raise HTTPException(
                status_code=422,
                detail=error_body("VALIDATION_ERROR", "resultDigest must be sha256 hex"),
            )
        released = (
            await db.execute(
                text(
                    "DELETE FROM run_claims WHERE run_id = :r AND worker_id = :w"
                ),
                {"r": job.run_id, "w": f"runner:{auth.id}"},
            )
        ).rowcount
        if not released:
            # Lease was reaped or re-assigned: do not pretend completion.
            job.state = "released"
            job.lease_expires_at = None
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail=error_body("LEASE_LOST", "the run claim is no longer held by this runner"),
            )
        job.state = body.status
        job.lease_expires_at = None
        job.result_digest = digest
        job.updated_at = _now()
        await db.commit()
        await emit_runner_job_completed(
            db,
            team_id=auth.team_id,
            target_id=job.run_id,
            detail={
                "runnerId": auth.id,
                "state": job.state,
                "resultDigest": (digest or "")[:12],
            },
        )
        return {"runId": job.run_id, "state": job.state}
