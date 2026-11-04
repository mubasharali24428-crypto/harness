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
import logging
import re
import secrets
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import EventType, Principal, Role, can, error_body
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
    emit_runner_rotated,
)
from .auth import get_principal
from .models import (
    AgentEvent,
    AgentPlanRecord,
    Project,
    RunnerJob,
    RunnerNonce,
    TeamRunner,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

TOKEN_PREFIX = "hrn_"
ATTESTATION_KEY_LABEL = b"harness-runner-attestation-v1"
MAX_DESCRIPTION = 500
MAX_ATTESTATION_BYTES = 8192
CLOCK_SKEW_SECONDS = 300
LEASE_SECONDS = 180
MAX_JOBS_PER_RUNNER = 20
# Execution leg bounds: a plan with more commands than this is refused rather than
# silently truncated; the event caps bound what one lease can write to the shared log.
MAX_COMMANDS = 20
MAX_EVENTS_PER_REQUEST = 50
MAX_EVENTS_PER_JOB = 4 * MAX_COMMANDS
MAX_COMMAND_CHARS = 4000
MAX_OUTPUT_CHARS = 2000
COMMAND_TIMEOUT_SECONDS = 120
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
        "attempt": row.attempt,
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


async def runner_auth(request: Request) -> RunnerAuth:
    """FastAPI dependency: authenticate BEFORE the request body is parsed.

    Declared as a dependency so an anonymous caller with a malformed body gets
    401 rather than 422 plus the schema. The nonce is committed by the auth path.
    """
    async with request.app.state.session_factory() as db:
        return await _authenticate(db, request)


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


@router.post("/team-runners/{runner_id}/rotate")
async def rotate_runner(
    runner_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Mint a fresh token for an active runner; the old token dies immediately.

    Rotation rewrites the token hash AND the derived attestation key, so the
    runner must re-attest with the new token before its claims verify again.
    There is no overlap window: a leaked token is worthless the moment the
    admin rotates, and a runner holding work keeps its lease until expiry.
    """
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
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "runner not found")
            )
        if row.status != "active":
            raise HTTPException(
                status_code=409,
                detail=error_body("RUNNER_REVOKED", "a revoked runner cannot be rotated"),
            )
        old_hint = row.token_hint
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        row.token_sha256 = _hash_token(token)
        row.token_hint = token[-4:]
        row.attestation_key = derive_attestation_key(token)
        row.updated_at = _now()
        await db.commit()
        await db.refresh(row)
        await emit_runner_rotated(
            db,
            actor_user_id=principal.userId,
            team_id=row.team_id,
            target_id=row.id,
            detail={"name": row.name, "oldHint": old_hint, "hint": row.token_hint},
        )
        return {
            "runner": _runner_public(row),
            "token": token,
            "attestationKeyLabel": ATTESTATION_KEY_LABEL.decode(),
            "note": "token is shown once; the previous token and attestation key are dead",
        }


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
            # Pre-start offer: start_execution has not queued the run yet. Offering
            # first is what lets the orchestrator dispatch it to the host instead of
            # racing its own worker for the queued row. Still the caller's team only.
            claim = (
                await db.execute(
                    text(
                        "SELECT NULL AS state, ar.project_id AS project_id FROM agent_runs ar "
                        "JOIN projects p ON ar.project_id = p.id "
                        "WHERE ar.id = :r AND p.team_id = :t"
                    ),
                    {"r": body.run_id, "t": principal.teamId},
                )
            ).mappings().first()
            # Unknown and foreign runs avoid leaking team data: one 404 for both.
            if claim is None:
                raise HTTPException(
                    status_code=404, detail=error_body("NOT_FOUND", "run not found")
                )
            settled = (
                await db.execute(
                    text(
                        "SELECT COUNT(*) FROM runner_jobs WHERE run_id = :r"
                    ),
                    {"r": body.run_id},
                )
            ).scalar_one()
            if settled:
                # A finished BYO run's claim is deleted at settle time, so this is the
                # re-offer path for a run that already ran on a host: refuse it rather
                # than mint an attempt for a settled run.
                raise HTTPException(
                    status_code=409,
                    detail=error_body("JOB_TAKEN", "this run already has a runner job"),
                )
        elif claim["state"] not in ("queued", "orphaned"):
            raise HTTPException(
                status_code=409,
                detail=error_body("NOT_QUEUED", f"run is {claim['state']}, not queued"),
            )
        redispatch = claim["state"] == "orphaned"
        live = (
            await db.execute(
                select(RunnerJob)
                .where(
                    RunnerJob.run_id == body.run_id,
                    RunnerJob.state.in_(("offered", "leased", "running")),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if live is not None:
            # Only one attempt per run may be live at a time; an orphaned prior
            # attempt never blocks the re-dispatch, a live one always does.
            raise HTTPException(
                status_code=409,
                detail=error_body("JOB_TAKEN", f"attempt {live.attempt} is still {live.state}"),
            )
        attempt = (
            await db.execute(
                text(
                    "SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt "
                    "FROM runner_jobs WHERE run_id = :r"
                ),
                {"r": body.run_id},
            )
        ).mappings().first()["next_attempt"]
        evidence_from_seq = (
            await db.execute(
                text(
                    "SELECT COALESCE(MAX(seq), 0) AS seq_max FROM agent_events WHERE run_id = :r"
                ),
                {"r": body.run_id},
            )
        ).mappings().first()["seq_max"]
        project = (
            await db.execute(select(Project).where(Project.id == claim["project_id"]))
        ).scalar_one_or_none()
        if project is None or project.team_id != principal.teamId:
            raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "run not found"))
        if redispatch:
            # The lost lease was the previous attempt's; hand the claim back to the
            # queue so the new attempt can claim it like any queued run.
            now_iso = _now().isoformat()
            await db.execute(
                text(
                    "UPDATE run_claims SET state = 'queued', worker_id = 'enqueuer', "
                    "claimed_at = :n, heartbeat_at = :n "
                    "WHERE run_id = :r AND state = 'orphaned'"
                ),
                {"r": body.run_id, "n": now_iso},
            )
        job = RunnerJob(
            run_id=body.run_id,
            attempt=attempt,
            runner_id=runner.id,
            project_id=claim["project_id"],
            state="offered",
            evidence_from_seq=evidence_from_seq,
        )
        db.add(job)
        await db.commit()
        await emit_runner_job_assigned(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=job.run_id,
            detail={"runnerId": runner.id, "projectId": job.project_id, "attempt": job.attempt},
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
    # Latest attempt: a re-dispatched run has attempt 1 (dead) and attempt N
    # (live) rows, and every runner-side call addresses the live one.
    job = (
        await db.execute(
            select(RunnerJob)
            .where(RunnerJob.run_id == run_id, RunnerJob.runner_id == runner_id)
            .order_by(RunnerJob.attempt.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        # Another team's (or unknown) run id: one answer, no existence oracle.
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "job not found"))
    return job


# ------------------------------------------------------------ execution leg
#
# The orchestrator (Section 4) owns the run state machine and replays it from
# ``agent_events``. A runner therefore never talks to it: it reads a spec derived
# from the approved plan row and appends tool events / the settling event to the
# same log. The host is trusted to run and report honestly; approval, the command
# allow-list, caps and the final verdict are all re-derived here from durable rows.

_LIFECYCLE = frozenset(
    {
        EventType.EXECUTION_STARTED,
        EventType.RESUMED,
        EventType.PAUSED,
        EventType.ENGINE_COMPLETED,
        EventType.ENGINE_FAILED,
        EventType.DONE_SIGNAL_RECEIVED,
        EventType.REVIEW_PASSED,
        EventType.REVIEW_FAILED,
        EventType.PR_OPENED,
        EventType.BLOCKED_AWAITING_CLARIFICATION,
    }
)
_TOOL_TYPES = (EventType.TOOL_CALL.value, EventType.TOOL_RESULT.value)
# Token-shaped strings that must never reach the durable transcript, whatever the
# host's own scrubbing did. The runner's own bearer token is replaced literally too.
_SECRET_SHAPES = re.compile(
    r"(?:hrn_|sk-|ghp_|gho_|github_pat_|xox[abp]-|AKIA)[A-Za-z0-9_\-]{12,}"
    r"|Bearer\s+\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)


def _clean(value: Any, limit: int, token: str | None = None) -> str:
    """Durable-safe text: NULs replaced, secrets redacted, then bounded."""
    out = str(value if value is not None else "").replace("\x00", "�")
    if token:
        out = out.replace(token, "[redacted]")
    out = _SECRET_SHAPES.sub("[redacted]", out)
    return out.encode("utf-8", "replace").decode("utf-8")[:limit]


def _bearer(request: Request) -> str:
    return request.headers.get("authorization", "").removeprefix("Bearer ").strip()


class RunnerEvent(BaseModel):
    phase: Literal["call", "result"]
    call_id: str = Field(alias="callId", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    command: str | None = Field(default=None, max_length=MAX_COMMAND_CHARS)
    ok: bool | None = None
    exit_code: int | None = Field(default=None, alias="exitCode")
    output: str | None = Field(default=None, max_length=30_000)
    error: str | None = Field(default=None, max_length=1000)
    duration_ms: int | None = Field(default=None, alias="durationMs", ge=0)

    model_config = {"populate_by_name": True}


class EventsBody(BaseModel):
    events: list[RunnerEvent] = Field(min_length=1, max_length=MAX_EVENTS_PER_REQUEST)


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, message))


async def _leased_job(db, runner_id: str, run_id: str) -> RunnerJob:
    await _expire_leases(db, runner_id)
    job = await _own_job_or_404(db, runner_id, run_id)
    if job.state not in ("leased", "running"):
        raise _refuse(409, "JOB_NOT_LEASED", f"job is {job.state}, not leased")
    return job


async def _has_plan(db, run_id: str) -> bool:
    found = await db.execute(
        select(AgentPlanRecord.id).where(AgentPlanRecord.run_id == run_id).limit(1)
    )
    return found.scalar_one_or_none() is not None


async def _approved_plan(db, run_id: str) -> AgentPlanRecord:
    """The run's approved plan row - the only source of what may be executed."""
    plan = (
        await db.execute(
            select(AgentPlanRecord)
            .where(AgentPlanRecord.run_id == run_id, AgentPlanRecord.status == "approved")
            .order_by(AgentPlanRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if plan is None:
        raise _refuse(409, "PLAN_NOT_APPROVED", "the run has no approved plan")
    commands = [str(c) for c in (plan.tests or [])]
    if not commands:
        raise _refuse(409, "NO_COMMANDS", "the approved plan lists no commands to execute")
    if len(commands) > MAX_COMMANDS:
        raise _refuse(
            409, "PLAN_TOO_LARGE", f"the approved plan lists more than {MAX_COMMANDS} commands"
        )
    return plan


async def _run_executing(db, run_id: str) -> bool:
    """True while the log's latest lifecycle event leaves the run EXECUTING.

    A stop, pause, settle or review all end it; the host must not write after that,
    and a late ``finish`` must not resurrect the run.
    """
    last = (
        await db.execute(
            select(AgentEvent.type)
            .where(AgentEvent.run_id == run_id, AgentEvent.type.in_([e.value for e in _LIFECYCLE]))
            .order_by(AgentEvent.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return last in (EventType.EXECUTION_STARTED.value, EventType.RESUMED.value)


async def _tool_events(db, run_id: str, after_seq: int = 0) -> list[AgentEvent]:
    """The run's tool call/result rows, optionally only this attempt's evidence."""
    query = select(AgentEvent).where(
        AgentEvent.run_id == run_id, AgentEvent.type.in_(_TOOL_TYPES)
    )
    if after_seq:
        query = query.where(AgentEvent.seq > after_seq)
    return list((await db.execute(query.order_by(AgentEvent.seq))).scalars().all())


async def _max_tool_calls(db, run_id: str) -> int | None:
    """The run's Part 44 maxToolCalls, or None when unset / pre-0048 schema.

    Same tolerance contract as ``execution.remaining_tool_calls``: a missing row
    or a missing table means "no limit", never a crash inside the write path.
    """
    try:
        row = (
            await db.execute(
                text("SELECT max_tool_calls FROM run_execution_limits WHERE run_id = :r"),
                {"r": run_id},
            )
        ).first()
    except Exception:  # noqa: BLE001 — absent table/column ⇒ free of a tool-call cap
        if db.in_transaction():
            await db.rollback()
        return None
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _calls_of(events: list[AgentEvent]) -> dict[str, str]:
    """callId -> command, parsed from the TOOL_CALL rows this endpoint wrote."""
    calls: dict[str, str] = {}
    for event in events:
        if event.type == EventType.TOOL_CALL.value:
            try:
                calls[event.payload["callId"]] = json.loads(event.payload["arguments"])["command"]
            except (KeyError, ValueError, TypeError):
                continue
    return calls


def _verdict(events: list[AgentEvent], commands: list[str]) -> str | None:
    """Why the recorded evidence does NOT show every approved command passing, else None."""
    calls = _calls_of(events)
    passed: Counter[str] = Counter()
    failed: list[str] = []
    for event in events:
        if event.type != EventType.TOOL_RESULT.value:
            continue
        command = calls.get(event.payload.get("callId"))
        if command is None:
            continue
        if event.payload.get("ok"):
            passed[command] += 1
        else:
            failed.append(command)
    if failed:
        return f"command failed: {failed[0][:200]}"
    wanted = Counter(_clean(c, MAX_COMMAND_CHARS) for c in commands)
    missing = wanted - passed
    if missing:
        return f"no successful result reported for: {next(iter(missing))[:200]}"
    return None


async def _scope_paths(db, run_id: str) -> list[str]:
    row = (
        await db.execute(
            text(
                "SELECT scope_paths FROM run_intent_versions WHERE run_id = :r "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"r": run_id},
        )
    ).first()
    if row is None:
        return []
    value = row[0]
    return list(json.loads(value) if isinstance(value, str) else value or [])


@router.get("/runner/jobs/{run_id}/spec")
async def job_spec(
    run_id: str,
    request: Request,
    auth: Annotated[RunnerAuth, Depends(runner_auth)],
) -> dict:
    """What the host may execute, read from the approved plan - never from the runner."""
    async with request.app.state.session_factory() as db:
        await _leased_job(db, auth.id, run_id)
        plan = await _approved_plan(db, run_id)
        if not await _run_executing(db, run_id):
            raise _refuse(409, "RUN_NOT_EXECUTING", "the run is not executing")
        return {
            "runId": run_id,
            "planId": plan.id,
            "goal": plan.goal,
            "steps": list(plan.steps or []),
            "filesLikelyToChange": list(plan.files_likely_to_change or []),
            "commands": [str(c) for c in plan.tests],
            "forbiddenPaths": list(plan.forbidden_paths or []),
            "scopePaths": await _scope_paths(db, run_id),
            "limits": {
                "maxCommands": MAX_COMMANDS,
                "maxEventsPerJob": MAX_EVENTS_PER_JOB,
                "maxEventsPerRequest": MAX_EVENTS_PER_REQUEST,
                "maxOutputChars": MAX_OUTPUT_CHARS,
                "commandTimeoutSeconds": COMMAND_TIMEOUT_SECONDS,
                "leaseSeconds": LEASE_SECONDS,
            },
        }


@router.post("/runner/jobs/{run_id}/events")
async def post_job_events(
    run_id: str,
    body: EventsBody,
    request: Request,
    auth: Annotated[RunnerAuth, Depends(runner_auth)],
) -> dict:
    """Append bounded, scrubbed tool events to the run's log.

    The whole batch is validated before anything is written, so a refused batch
    leaves no partial transcript. The server names the tool and rebuilds each
    payload; the runner supplies only the command, outcome and output text.
    """
    token = _bearer(request)
    async with request.app.state.session_factory() as db:
        job = await _leased_job(db, auth.id, run_id)
        plan = await _approved_plan(db, run_id)
        if not await _run_executing(db, run_id):
            raise _refuse(409, "RUN_NOT_EXECUTING", "the run is not executing")
        existing = await _tool_events(db, run_id)
        if len(existing) + len(body.events) > MAX_EVENTS_PER_JOB:
            raise _refuse(
                409, "EVENT_CAP_REACHED", f"a job may record at most {MAX_EVENTS_PER_JOB} events"
            )
        allowed = {_clean(c, MAX_COMMAND_CHARS) for c in plan.tests}
        known = _calls_of(existing)
        planned: list[tuple[EventType, dict[str, Any]]] = []
        now = _now().isoformat()
        for event in body.events:
            if event.phase == "call":
                command = _clean(event.command, MAX_COMMAND_CHARS)
                if command not in allowed:
                    raise _refuse(
                        422, "COMMAND_NOT_IN_PLAN", "the command is not in the approved plan"
                    )
                if event.call_id in known:
                    raise _refuse(422, "DUPLICATE_CALL", f"callId {event.call_id!r} already used")
                known[event.call_id] = command
                planned.append(
                    (
                        EventType.TOOL_CALL,
                        {
                            "runId": run_id,
                            "callId": event.call_id,
                            "name": "bash",
                            "arguments": json.dumps({"command": command}),
                            "turn": None,
                            "step": None,
                            "startedAt": now,
                            "runnerId": auth.id,
                        },
                    )
                )
            else:
                if event.call_id not in known:
                    raise _refuse(422, "UNKNOWN_CALL", f"no call recorded for {event.call_id!r}")
                planned.append(
                    (
                        EventType.TOOL_RESULT,
                        {
                            "runId": run_id,
                            "callId": event.call_id,
                            "name": "bash",
                            "ok": bool(event.ok),
                            "error": _clean(event.error, 500, token) or None,
                            "output": _clean(event.output, MAX_OUTPUT_CHARS, token),
                            "exitCode": event.exit_code,
                            "durationMs": event.duration_ms,
                            "finishedAt": now,
                            "runnerId": auth.id,
                        },
                    )
                )
        bus = request.app.state.event_bus
        project_id = job.project_id  # read before the extra query below expires `job`
        # Part 44: the run's own tool calls count against its maxToolCalls, exactly
        # as the engine's shell plugin bounds them. The per-job event cap above bounds
        # transcript SIZE; this bounds the budget the plan approved. Checked on the
        # validated batch BEFORE any write, and after `plan` was already read so the
        # extra query cannot expire the ORM row this path still touches.
        # Absent row / null column (pre-0048 or unset) = no limit, like `remaining_tool_calls`.
        used_calls = sum(1 for e in existing if e.type == EventType.TOOL_CALL.value)
        new_calls = sum(1 for e in body.events if e.phase == "call")
        cap = await _max_tool_calls(db, run_id)
        if cap is not None and used_calls + new_calls > cap:
            raise _refuse(
                409,
                "TOOL_CALL_LIMIT",
                f"this batch exceeds the run's maxToolCalls ({cap}): "
                f"{used_calls} used, {new_calls} in this batch",
            )
        for event_type, payload in planned:
            await bus.publish(event_type, project_id, payload, run_id=run_id)
        return {"accepted": len(planned), "total": len(existing) + len(planned)}


@router.post("/runner/heartbeat")
async def runner_heartbeat(
    body: AttestBody,
    request: Request,
    auth: Annotated[RunnerAuth, Depends(runner_auth)],
) -> dict:
    async with request.app.state.session_factory() as db:
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
async def list_my_jobs(
    request: Request,
    auth: Annotated[RunnerAuth, Depends(runner_auth)],
) -> dict:
    async with request.app.state.session_factory() as db:
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
async def claim_job(
    run_id: str,
    request: Request,
    auth: Annotated[RunnerAuth, Depends(runner_auth)],
) -> dict:
    async with request.app.state.session_factory() as db:
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
async def heartbeat_job(
    run_id: str,
    request: Request,
    auth: Annotated[RunnerAuth, Depends(runner_auth)],
) -> dict:
    async with request.app.state.session_factory() as db:
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
        # A stop/pause/settle recorded in the log reaches the host here, so it can
        # kill the running command instead of finishing work nobody is waiting for.
        cancel = await _has_plan(db, job.run_id) and not await _run_executing(db, job.run_id)
        return {
            "state": job.state,
            "leaseExpiresAt": job.lease_expires_at.isoformat(),
            "cancel": cancel,
        }


@router.post("/runner/jobs/{run_id}/finish")
async def finish_job(
    run_id: str,
    body: FinishBody,
    request: Request,
    auth: Annotated[RunnerAuth, Depends(runner_auth)],
) -> dict:
    """Release the lease and settle the run exactly as a local engine would.

    The terminal verdict is re-derived from the recorded tool results: a runner
    that says ``completed`` without a passing result for every approved command is
    settled ``failed``. A run that is no longer executing (human stop) only loses
    its lease - ``finish`` never resurrects it.
    """
    async with request.app.state.session_factory() as db:
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
        held = (
            await db.execute(
                text("SELECT 1 FROM run_claims WHERE run_id = :r AND worker_id = :w"),
                {"r": job.run_id, "w": f"runner:{auth.id}"},
            )
        ).first()
        if held is None:
            # Lease was reaped or re-assigned: do not pretend completion.
            job.state = "released"
            job.lease_expires_at = None
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail=error_body("LEASE_LOST", "the run claim is no longer held by this runner"),
            )

        final, run_state, event = body.status, None, None
        if await _has_plan(db, job.run_id):
            # Publish BEFORE the claim is released: a failed publish leaves the lease
            # held and the finish retryable, never a run stranded EXECUTING with no owner.
            if not await _run_executing(db, job.run_id):
                final, run_state = "failed", "not_executing"
            else:
                plan = await _approved_plan(db, job.run_id)
                problem = _verdict(
                    await _tool_events(db, job.run_id, job.evidence_from_seq), list(plan.tests)
                )
                if problem is None and body.status == "completed":
                    count = len(plan.tests)
                    event = (
                        EventType.ENGINE_COMPLETED,
                        {
                            "summary": f"BYO runner {auth.name} executed {count} approved "
                            f"command(s); all exited 0",
                        },
                    )
                else:
                    final = "failed"
                    event = (
                        EventType.ENGINE_FAILED,
                        {"reason": problem or f"runner {auth.name} reported failure"},
                    )
        if event is not None:
            await request.app.state.event_bus.publish(
                event[0],
                job.project_id,
                {"runId": job.run_id, "runnerId": auth.id, **event[1]},
                run_id=job.run_id,
            )
        await db.execute(
            text("DELETE FROM run_claims WHERE run_id = :r AND worker_id = :w"),
            {"r": job.run_id, "w": f"runner:{auth.id}"},
        )
        job.state = final
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
        answer = {"runId": job.run_id, "state": job.state}
        if run_state:
            answer["runState"] = run_state
        return answer


async def purge_runner_nonces(
    factory, older_than_seconds: int = 2 * CLOCK_SKEW_SECONDS
) -> int:
    """Delete nonces that can no longer be replayed. Returns the row count.

    The auth path prunes inline so the table stays bounded under load; this
    sweep is the backstop for quiet runners and restarted processes.
    """
    cutoff = _now() - timedelta(seconds=older_than_seconds)
    async with factory() as db:
        result = await db.execute(delete(RunnerNonce).where(RunnerNonce.seen_at < cutoff))
        await db.commit()
        return int(result.rowcount or 0)


async def run_nonce_sweeper(factory, interval_seconds: float) -> None:
    """Background backstop: purge expired nonces every ``interval_seconds``."""
    import asyncio
    import logging

    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            removed = await purge_runner_nonces(factory)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — housekeeping must never kill the service
            logger.exception("runner nonce sweep failed")
            continue
        if removed:
            logger.info("runner nonce sweep removed %d expired nonce(s)", removed)
