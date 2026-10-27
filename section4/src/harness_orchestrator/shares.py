"""Artifact share links (W9): a capability URL for exactly one artifact.

A share is authority carried by the URL, so it is served WITHOUT an Authorization
header. That makes three things non-negotiable, and this module is built around them:

* the row stores only ``sha256(token)`` — the token is shown once, at mint time, and
  a dump of this table yields no working links;
* the route can serve exactly one artifact's bytes and nothing else: no listing, no
  metadata beyond what the download needs, no run access. It always answers with
  ``application/octet-stream``, ``Content-Disposition: attachment``, ``nosniff`` and
  a sandbox CSP, so a browser saves the file instead of rendering it. A capability
  that can serve HTML on the deployment's own origin would be a stored XSS primitive;
* every refusal is a state check on the row or on the artifact, never a guess: unknown
  and revoked are the same 404 (no existence oracle), expired is 410, a deleted or
  retention-expired artifact is 410, and a quarantined artifact refuses the link
  outright (403) rather than bypassing the admin-only download rule.

Revocation is the reason this is a registry rather than a signed URL: one write to
``revoked_at`` stops every copy of the link, immediately and durably.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from harness_contracts.roles import Principal
from pydantic import BaseModel
from sqlalchemy import text

from harness_orchestrator import artifacts as _art

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["shares"])

SHARE_TTL_ENV = "SHARE_LINK_TTL_SECONDS"
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_TTL_SECONDS = 90 * 24 * 60 * 60  # matches the artifact retention default
MIN_TTL_SECONDS = 60
SHARE_PATH = "/api/v1/orchestrator/shares/"

#: Columns the API may return. ``token_sha`` is deliberately absent everywhere.
_COLS = (
    "id, team_id, run_id, artifact_id, created_by, created_at, expires_at, revoked_at,"
    " revoked_by, last_used_at, uses"
)


class ShareBody(BaseModel):
    ttlSeconds: int | None = None


def _token_sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ttl(requested: int | None) -> int:
    default = DEFAULT_TTL_SECONDS
    raw = os.environ.get(SHARE_TTL_ENV, "").strip()
    if raw:
        try:
            default = int(raw)
        except ValueError:
            logger.warning("ignoring non-integer %s=%r", SHARE_TTL_ENV, raw)
    value = default if requested is None else int(requested)
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, value))


def _view(row: Any) -> dict[str, Any]:
    now = _art._now()
    revoked = row.revoked_at is not None
    expires = _art._dt(row.expires_at)
    return {
        "id": row.id,
        "runId": row.run_id,
        "artifactId": row.artifact_id,
        "createdBy": row.created_by,
        "createdAt": _art._iso(row.created_at),
        "expiresAt": _art._iso(row.expires_at),
        "revokedAt": _art._iso(row.revoked_at),
        "revokedBy": row.revoked_by,
        "lastUsedAt": _art._iso(row.last_used_at),
        "uses": int(row.uses),
        "active": not revoked and expires is not None and expires > now,
    }


async def _find_share(db: Any, p: Principal, run_id: str, share_id: str) -> Any:
    row = (
        await db.execute(
            text(
                f"SELECT {_COLS} FROM artifact_shares"
                " WHERE id = :i AND run_id = :r AND team_id = :t"
            ),
            {"i": share_id[:64], "r": run_id, "t": p.teamId},
        )
    ).first()
    if row is None:
        raise _art._not_found("share")
    return row


# --------------------------------------------------------------------------- mint


@router.post("/runs/{runId}/artifacts/{artifactId}/share", status_code=201)
async def create_share(
    runId: str, artifactId: str, body: ShareBody, p: _art.Writer
) -> Any:
    """Mint a link for one artifact. The token is returned exactly once, in this body."""

    async def go() -> Any:
        factory = _art._need_factory()
        async with factory() as db:
            art = await _art._find(db, p, runId, artifactId)
            state = _art._eff_state(art, _art._now())
            if state in {"deleted", "expired"}:
                raise _art.ArError(410, "ARTIFACT_DELETED", "this artifact is gone")
            if state == "quarantined":
                # An admin-only download must never become a link anyone can open.
                raise _art.ArError(
                    409, "SHARE_REFUSED", "quarantined artifacts cannot be shared"
                )
            now = _art._now()
            share_id = str(uuid.uuid4())
            token = secrets.token_urlsafe(32)
            expires = now + timedelta(seconds=_ttl(body.ttlSeconds))
            await db.execute(
                text(
                    "INSERT INTO artifact_shares (id, team_id, run_id, artifact_id, token_sha,"
                    " created_by, created_at, expires_at, uses) VALUES (:i, :t, :r, :a, :s,"
                    " :by, :n, :e, 0)"
                ),
                {
                    "i": share_id,
                    "t": p.teamId,
                    "r": runId,
                    "a": art.id,
                    "s": _token_sha(token),
                    "by": p.userId,
                    "n": now,
                    "e": expires,
                },
            )
            await _art._audit(
                db,
                p,
                "artifact.shared",
                "artifact_share",
                share_id,
                {
                    "runId": runId,
                    "artifactId": art.id,
                    "expiresAt": expires.isoformat(),
                    "ttlSeconds": _ttl(body.ttlSeconds),
                },
            )
            await db.commit()
        return JSONResponse(
            {
                "share": {
                    "id": share_id,
                    "runId": runId,
                    "artifactId": art.id,
                    "createdAt": now.isoformat(),
                    "expiresAt": expires.isoformat(),
                    "token": token,
                    # The authority is the token; the caller composes the origin.
                    "url": f"{SHARE_PATH}{token}",
                }
            },
            status_code=201,
            headers=_art._no_store(),
        )

    return await _art._handled(go())


# --------------------------------------------------------------------------- list


@router.get("/runs/{runId}/shares")
async def list_shares(runId: str, p: _art.Reader) -> Any:
    """The run's links, without tokens: enough to see what is out there and stop it."""

    async def go() -> Any:
        async with _art._need_factory()() as db:
            # `Reader` already proved run ownership (foreign run: the same 404).
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_COLS} FROM artifact_shares WHERE run_id = :r AND team_id = :t"
                        " ORDER BY created_at DESC"
                    ),
                    {"r": runId, "t": p.teamId},
                )
            ).all()
        return JSONResponse(
            {"runId": runId, "items": [_view(r) for r in rows]}, headers=_art._no_store()
        )

    return await _art._handled(go())


# ------------------------------------------------------------------------- revoke


@router.post("/runs/{runId}/shares/{shareId}/revoke")
async def revoke_share(runId: str, shareId: str, p: _art.Writer) -> Any:
    """Stop a link. Idempotent: a second call reports the first revocation, not a new one."""

    async def go() -> Any:
        async with _art._need_factory()() as db:
            row = await _find_share(db, p, runId, shareId)
            if row.revoked_at is None:
                now = _art._now()
                await db.execute(
                    text(
                        "UPDATE artifact_shares SET revoked_at = :n, revoked_by = :u"
                        " WHERE id = :i AND revoked_at IS NULL"
                    ),
                    {"n": now, "u": p.userId, "i": row.id},
                )
                await _art._audit(
                    db,
                    p,
                    "artifact.share_revoked",
                    "artifact_share",
                    row.id,
                    {"runId": runId, "artifactId": row.artifact_id},
                )
                await db.commit()
                row = await _find_share(db, p, runId, shareId)
        return JSONResponse({"share": _view(row)}, headers=_art._no_store())

    return await _art._handled(go())


# --------------------------------------------------------------- anonymous open


@router.get("/shares/{token}")
async def open_share(token: str, request: Request) -> Any:
    """Serve one artifact's bytes to whoever holds the token. No header, no session."""

    async def go() -> Any:
        factory = _art._need_factory()
        digest = _token_sha(token[:200])
        async with factory() as db:
            share = (
                await db.execute(
                    text(f"SELECT {_COLS} FROM artifact_shares WHERE token_sha = :s"),
                    {"s": digest},
                )
            ).first()
            if share is None or share.revoked_at is not None:
                # Same answer for a forged token and a revoked one: no oracle.
                raise _art.ArError(404, "NOT_FOUND", "share not found")
            if _art._dt(share.expires_at) <= _art._now():
                raise _art.ArError(410, "SHARE_EXPIRED", "this link has expired")
            art = (
                await db.execute(
                    text(
                        f"SELECT {_art._ACOLS} FROM artifacts"
                        " WHERE id = :i AND run_id = :r AND team_id = :t"
                    ),
                    {"i": share.artifact_id, "r": share.run_id, "t": share.team_id},
                )
            ).first()
            if art is None:
                raise _art.ArError(404, "NOT_FOUND", "share not found")
            state = _art._eff_state(art, _art._now())
            if state == "deleted":
                raise _art.ArError(410, "ARTIFACT_DELETED", "this artifact was deleted")
            if state == "expired":
                raise _art.ArError(
                    410, "ARTIFACT_EXPIRED", "this artifact passed its retention period"
                )
            if state == "quarantined":
                raise _art.ArError(403, "SHARE_BLOCKED", "this artifact cannot be shared")
            data = await _art._bytes(art)
            now = _art._now()
            await db.execute(
                text(
                    "UPDATE artifact_shares SET uses = uses + 1, last_used_at = :n WHERE id = :i"
                ),
                {"n": now, "i": share.id},
            )
            # System actor: the request has no principal, and the row says which link it was.
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type,"
                    " target_id, detail, ts) VALUES (NULL, 'system', 'share.opened',"
                    " 'artifact_share', :i, :d, :n)"
                ),
                {
                    "i": share.id,
                    "d": json.dumps(
                        {
                            "shareId": share.id,
                            "runId": share.run_id,
                            "artifactId": art.id,
                            "sizeBytes": len(data),
                            "client": (request.client.host if request.client else None),
                        }
                    ),
                    "n": now,
                },
            )
            await db.commit()
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers=_art._no_store(
                {
                    "Content-Disposition": _art._disposition(art.name),
                    "Content-Security-Policy": "sandbox; default-src 'none'",
                    "X-Artifact-Sha256": art.sha256,
                }
            ),
        )

    return await _art._handled(go())


def configure_shares(service: Any) -> None:
    """Deploy-time wiring: the artifact router owns the service reference, this shares it."""
    _art.configure_artifacts(service)
