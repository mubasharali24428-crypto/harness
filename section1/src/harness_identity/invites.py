"""Wave 9 node C — org invites: mint, list, resend, revoke, redeem.

M14-F2a extends the wave-9 lifecycle with admin resend + a derived status so
the S7 team page can render honest badges:

    draft -> sent -> redeemed
                 |-> expired
                 |-> revoked (admin revoke OR superseded by a resend)

Status derivation lives in invite_status(): timestamps/token state are the only
inputs, so the UI never has to re-implement the state machine.

Security posture:
- Raw invite tokens are returned ONCE at mint/resend time; only SHA-256 hex
  digests are persisted (hash_token from harness_contracts, same as auth
  sessions).
- Resend mints a fresh token and revokes the old one in ONE transaction — an
  intercepted stale token dies the moment a fresh one is issued.
- Redemption is a single-statement atomic UPDATE ... WHERE token_hash = :h
  AND redeemed_at IS NULL AND revoked_at IS NULL AND expires_at > :now
  RETURNING ... so concurrent redemptions cannot double-join (one winner,
  everyone else gets 409/404/410).
- TIMESTAMPTZ comparisons bind real datetime objects (asyncpg rejects strings).
- M18-C5 (MAC2-F3): redemption binds to the INVITED IDENTITY, not merely to
  token confidentiality. A forwarded/intercepted invite used to enroll
  whichever account presented the token — admin included. Now the redeemer
  must prove control of the invited mailbox by presenting ``email`` matching
  the minted row (S1 accounts ARE their email: login registers/owns the typed
  address), so a leaked token without the address is inert. Chosen over a
  claim-link scheme because it needs no new tables/tokens/migrations and is
  provable in one code path; the token stays the transport, the mailbox the
  authority. Live tokens answer nothing to identity-less probes: they fail
  closed with INVITE_IDENTITY_MISMATCH (dead tokens keep their honest
  404/409/410 codes — revocation supersedes identity).
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from harness_contracts import Principal, Role, error_body
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_emit import (
    emit_invite_minted,
    emit_invite_redeemed,
    emit_invite_resent,
    emit_invite_revoked,
)
from .auth import get_principal
from .models import Role as DbRole
from .models import TeamInvite, TeamMember, User

router = APIRouter(prefix="/api/v1")

logger = logging.getLogger(__name__)

INVITE_TTL_DAYS = 7
INVITABLE_ROLES = frozenset(r.value for r in DbRole)

# M14-F2a — derived lifecycle statuses (docs/briefs/mission14-wave4-graph.md
# lane F2a). "draft" exists for invites whose send is still out-of-band: mint
# stamps sent_at immediately today, but an external mailer lane can flip rows
# to draft by clearing it without touching this state machine.
INVITE_STATUSES = ("draft", "sent", "redeemed", "expired", "revoked")


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    """Normalize DB timestamps for Python-side comparisons.

    Postgres/asyncpg hands back aware datetimes; SQLite/aiosqlite (the test
    backend) hands back naive UTC ones. Treating naive as UTC keeps the state
    machine identical on both without touching frozen models.
    """
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _invite_status(
    row: TeamInvite, *, now: datetime | None = None, sent_at: datetime | None = None
) -> str:
    """Derive the lifecycle status from timestamps/token state alone.

    Precedence: revoked > redeemed > expired > draft/sent. ``sent_at`` defaults
    to the column so callers probing hypothetical transitions can pass their own.
    """
    if row.revoked_at is not None:
        return "revoked"
    if row.redeemed_at is not None:
        return "redeemed"
    if (now or _now()) >= _as_utc(row.expires_at):
        return "expired"
    effective_sent = sent_at if sent_at is not None else row.sent_at
    return "sent" if effective_sent is not None else "draft"


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of the raw token — the only thing we store."""
    return hashlib.sha256(token.encode()).hexdigest()


def _invite_public(row: TeamInvite, *, now: datetime | None = None) -> dict:
    """camelCase projection per api-conventions.md. Never includes token hashes."""
    return {
        "id": row.id,
        "teamId": row.team_id,
        "email": row.email,
        "role": row.role,
        "expiresAt": row.expires_at.isoformat(),
        "redeemedAt": row.redeemed_at.isoformat() if row.redeemed_at else None,
        "revokedAt": row.revoked_at.isoformat() if row.revoked_at else None,
        "createdAt": row.created_at.isoformat(),
        "status": _invite_status(row, now=now),
    }


class MemberItem(BaseModel):
    """M13-G5a typing island: one roster row (camelCase per api-conventions)."""

    id: str
    displayName: str | None = None
    email: str | None = None
    memberType: str | None = None
    role: str
    createdAt: datetime


class MemberListResponse(BaseModel):
    items: list[MemberItem]


async def _require_team_admin(
    db: AsyncSession, principal: Principal, team_id: str
) -> None:
    """Admin-only gate with the M13-G5a no-oracle law for the team mismatch.

    Role first (a non-admin gets 403 wherever they probe), then membership:
    a foreign caller gets the same NOT_FOUND an unknown team id would produce,
    never a 403 confirming the team exists.
    """
    if principal.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail=error_body("FORBIDDEN", "admin only"))
    if principal.teamId != team_id:
        raise HTTPException(
            status_code=404, detail=error_body("NOT_FOUND", "team not found")
        )


class InviteCreateBody(BaseModel):
    # Dependency-free shape check: EmailStr would pull in `email_validator`,
    # which section1's venv deliberately does not carry.
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = Field(pattern="^(admin|engineer|viewer|guest)$")


class InviteRedeemBody(BaseModel):
    # M18-C5 (MAC2-F3): ``email`` binds redemption to the invited identity.
    # The redeemer proves control of the invited mailbox (S1 accounts are
    # created by logging in AS an address), so a forwarded/leaked token alone
    # can no longer enroll a foreign account at the minted role. Same loose
    # shape check as InviteCreateBody — dependency-free, case/space tolerant.
    token: str = Field(min_length=16, max_length=256)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    @field_validator("token", "email", mode="before")
    @classmethod
    def _strip_inputs(cls, v: object) -> object:
        # Tolerate client-side whitespace BEFORE the pattern/length checks.
        return v.strip() if isinstance(v, str) else v


@router.post("/teams/{team_id}/invites", status_code=201)
async def create_invite(
    team_id: str,
    body: InviteCreateBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Admin-only: mint an invite. The raw token appears exactly once, here."""
    async with request.app.state.session_factory() as db:
        await _require_team_admin(db, principal, team_id)

        raw_token = secrets.token_urlsafe(32)
        now = _now()
        invite = TeamInvite(
            team_id=team_id,
            email=body.email.lower().strip(),
            role=DbRole(body.role).value,
            token_hash=_hash_token(raw_token),
            expires_at=now + timedelta(days=INVITE_TTL_DAYS),
            # M14-F2a: the token is in the admin's hands the moment we return,
            # so a fresh invite starts life as "sent" (never "draft").
            sent_at=now,
        )
        db.add(invite)
        await db.commit()
        await db.refresh(invite)
        # C-002 audit: mint is sensitive; detail carries email/role only — the
        # raw token below must never enter the audit row.
        await emit_invite_minted(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=invite.id,
            detail={"email": invite.email, "role": invite.role},
        )
        out = _invite_public(invite)
        out["token"] = raw_token  # shown once; never stored, never listed again
        return out


@router.get(
    "/teams/{team_id}/members",
    response_model=MemberListResponse,
    responses={
        401: {"description": "bad or missing token"},
        404: {"description": "caller is not a member of the team (no-oracle)"},
    },
)
async def list_members(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Team roster with roles (any member of the team). Backs the S7 team page;
    S1 previously exposed no members read, so node C owns this one too."""
    async with request.app.state.session_factory() as db:
        member = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == team_id, TeamMember.user_id == principal.userId
                )
            )
        ).scalar_one_or_none()
        if member is None:
            # M13-G5a no-oracle law: a foreign probe gets the same
            # NOT_FOUND an unknown team id would produce — never a 403
            # confirming the team exists.
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "team not found")
            )
        rows = (
            (
                await db.execute(
                    select(TeamMember, User)
                    .outerjoin(User, TeamMember.user_id == User.id)
                    .where(TeamMember.team_id == team_id)
                    .order_by(TeamMember.created_at)
                )
            )
            .all()
        )
        return {
            "items": [
                {
                    "id": m.id,
                    "displayName": m.display_name,
                    "email": u.email if u is not None else None,
                    "memberType": m.member_type,
                    "role": m.role,
                    "createdAt": m.created_at.isoformat(),
                }
                for m, u in rows
            ]
        }


@router.get("/teams/{team_id}/invites")
async def list_invites(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    status: Annotated[str | None, Query(alias="status")] = None,
) -> dict:
    """M14-F2a — full invite lifecycle list for the admin UI.

    Every status is returned (draft/sent/redeemed/expired/revoked derived per
    invite via _invite_status); ``?status=<s>`` narrows to one bucket. The old
    pending-only behavior stays available as ``?status=sent``.

    Admin-only: any other caller (foreign OR in-team non-admin) gets 404/403
    without leaking whether any invites exist (enumeration guard, M13-G5a).
    """
    async with request.app.state.session_factory() as db:
        member = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == team_id, TeamMember.user_id == principal.userId
                )
            )
        ).scalar_one_or_none()
        if member is None:
            # M13-G5a no-oracle law: a foreign probe gets the same
            # NOT_FOUND an unknown team id would produce — never a 403
            # confirming the team exists.
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "team not found")
            )
        if principal.role != Role.ADMIN:
            raise HTTPException(
                status_code=403, detail=error_body("FORBIDDEN", "admin only")
            )

        now = _now()
        rows = (
            (
                await db.execute(
                    select(TeamInvite)
                    .where(TeamInvite.team_id == team_id)
                    .order_by(TeamInvite.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        items = [_invite_public(r, now=now) for r in rows]
        if status is not None:
            if status not in INVITE_STATUSES:
                raise HTTPException(
                    status_code=422,
                    detail=error_body("VALIDATION_ERROR", f"unknown invite status: {status}"),
                )
            items = [i for i in items if i["status"] == status]
        return {"items": items}


@router.delete("/teams/{team_id}/invites/{invite_id}")
async def revoke_invite(
    team_id: str,
    invite_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """M14-F2a — admin-only revoke. The row is KEPT (revoked_at stamped) so the
    lifecycle list can show "revoked" and audit rows keep a real target; the
    token dies either way. Revoking an already-terminal invite is idempotent."""
    async with request.app.state.session_factory() as db:
        await _require_team_admin(db, principal, team_id)
        row = (
            await db.execute(
                select(TeamInvite).where(
                    TeamInvite.id == invite_id, TeamInvite.team_id == team_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "invite not found")
            )
        already_revoked = row.revoked_at is not None
        if not already_revoked:
            row.revoked_at = _now()
            await db.commit()
            # C-002 audit: admin revoke of an unredeemed (or live) invite.
            # Emitted after commit via the never-throw helpers.
            await emit_invite_revoked(
                db,
                actor_user_id=principal.userId,
                team_id=team_id,
                target_id=invite_id,
                detail={"email": row.email, "role": row.role},
            )
    return {"ok": True}


@router.post("/teams/{team_id}/invites/{invite_id}/resend", status_code=201)
async def resend_invite(
    team_id: str,
    invite_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """M14-F2a — admin-only resend: mint a fresh token for the SAME invite and
    revoke the old one in ONE transaction. An intercepted stale token dies the
    moment a fresh one is issued. Only live invites can be resent — redeemed,
    expired, revoked or draft-less rows answer honest 409/410 codes."""
    async with request.app.state.session_factory() as db:
        await _require_team_admin(db, principal, team_id)
        row = (
            (
                await db.execute(
                    select(TeamInvite).where(
                        TeamInvite.id == invite_id, TeamInvite.team_id == team_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=404, detail=error_body("NOT_FOUND", "invite not found")
            )
        now = _now()
        if row.revoked_at is not None:
            raise HTTPException(
                status_code=409, detail=error_body("INVITE_REVOKED", "invite was revoked")
            )
        if row.redeemed_at is not None:
            raise HTTPException(
                status_code=409,
                detail=error_body("INVITE_ALREADY_REDEEMED", "invite already redeemed"),
            )
        if now >= _as_utc(row.expires_at):
            raise HTTPException(
                status_code=410, detail=error_body("INVITE_EXPIRED", "invite expired")
            )

        raw_token = secrets.token_urlsafe(32)
        row.token_hash = _hash_token(raw_token)
        row.revoked_at = None
        row.sent_at = now
        row.expires_at = now + timedelta(days=INVITE_TTL_DAYS)  # full TTL again
        await db.commit()
        # C-002 audit: resend re-mints a bearer-grade join token; detail carries
        # email/role only — neither raw token ever enters the audit row.
        await emit_invite_resent(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=row.id,
            detail={"email": row.email, "role": row.role},
        )
        out = _invite_public(row, now=now)
        out["token"] = raw_token  # shown once; never stored, never listed again
        return out


def _redeem_rejection(
    row: TeamInvite | None, presented_email: str, now: datetime
) -> HTTPException | None:
    """Honest-code diagnosis for a row that cannot be claimed right now.

    M18-C5 (MAC2-F3) ordering law: legacy codes keep their historical
    precedence (redeemed > revoked > expired); the NEW identity gate sits
    last, so a dead token answers its honest 404/409/410 regardless of what
    email was presented, and a LIVE token answers nothing to an identity-less
    probe except 403 INVITE_IDENTITY_MISMATCH (fail closed).
    """
    if row is None:
        return HTTPException(
            status_code=404,
            detail=error_body("INVITE_NOT_FOUND", "unknown invite token"),
        )
    if row.redeemed_at is not None:
        return HTTPException(
            status_code=409, detail=error_body("INVITE_ALREADY_REDEEMED", "")
        )
    if row.revoked_at is not None:
        # M14-F2a: admin revoke (or a resend superseding this token).
        return HTTPException(
            status_code=410,
            detail=error_body("INVITE_REVOKED", "invite was revoked"),
        )
    if now >= _as_utc(row.expires_at):
        return HTTPException(status_code=410, detail=error_body("INVITE_EXPIRED", ""))
    # MAC2-F3: THE identity gate. Token confidentiality alone no longer
    # enrolls anyone; the caller must present the invited mailbox.
    if (row.email or "").strip().lower() != presented_email:
        logger.info(
            "invite redeem rejected: INVITE_IDENTITY_MISMATCH (invite=%s)", row.id
        )
        return HTTPException(
            status_code=403,
            detail=error_body(
                "INVITE_IDENTITY_MISMATCH",
                "this invite was issued to a different email",
            ),
        )
    return None


@router.post("/invites/redeem")
async def redeem_invite(
    body: InviteRedeemBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """An authed user redeems a token into the invited team at the invited
    role — M18-C5 (MAC2-F3): ONLY after presenting the invited ``email``.

    Validate-first, then atomic single-statement claim: the identity gate runs
    BEFORE any mutation, so a wrong/missing identity never consumes a live
    token (no DoS-by-forwarding, no poisoned audit trail). The claim itself
    keeps the exact historical WHERE clause (expired/redeemed/unknown answer
    honest codes; concurrent redemptions cannot double-join), and a lost race
    re-runs the diagnosis once so the caller hears the true reason.
    """
    token = body.token.strip()
    digest = _hash_token(token)
    presented_email = body.email.strip().lower()
    now = _now()
    factory = request.app.state.session_factory
    async with factory() as db:
        claimed = None
        for _attempt in range(2):
            row = (
                await db.execute(
                    select(TeamInvite).where(TeamInvite.token_hash == digest)
                )
            ).scalar_one_or_none()
            rejection = _redeem_rejection(row, presented_email, now)
            if rejection is not None:
                raise rejection
            claimed = (
                (
                    await db.execute(
                        update(TeamInvite)
                        .where(
                            TeamInvite.token_hash == digest,
                            TeamInvite.redeemed_at.is_(None),
                            TeamInvite.revoked_at.is_(None),  # M14-F2a: dead token
                            TeamInvite.expires_at > now,
                        )
                        .values(redeemed_at=now)
                        .returning(TeamInvite.__table__)
                        # M18-C5: the validate-first SELECT above loads the row
                        # into the identity map; the default "evaluate"
                        # synchronize would re-evaluate the WHERE against that
                        # Python object (and trip over SQLite's naive
                        # datetimes). We consume RETURNING directly — no sync.
                        .execution_options(synchronize_session=False)
                    )
                )
                .mappings()
                .first()
            )
            if claimed is not None:
                break
            # Lost a race (the row decayed between the read and the claim):
            # loop once more — the second diagnosis raises the honest code,
            # because row state only ever moves toward a terminal form.
        assert claimed is not None  # unreachable: rejection above raises first

        user = await db.get(User, principal.userId)
        display_name = user.name if user is not None else ""
        if not display_name:
            display_name = (claimed["email"] or "").split("@")[0]

        existing_member = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == claimed["team_id"],
                    TeamMember.user_id == principal.userId,
                )
            )
        ).scalar_one_or_none()
        if existing_member is not None:
            await db.commit()
            # C-002 audit: this token was still consumed — same event as a
            # first join, minus the membership insert.
            await emit_invite_redeemed(
                db,
                actor_user_id=principal.userId,
                team_id=claimed["team_id"],
                target_id=str(claimed["id"]),
                detail={"email": claimed["email"], "role": str(claimed["role"])},
            )
            return {
                "membership": {
                    "teamId": claimed["team_id"],
                    "role": existing_member.role,
                },
                "alreadyMember": True,
            }

        member = TeamMember(
            team_id=claimed["team_id"],
            user_id=principal.userId,
            display_name=display_name[:200],
            role=str(claimed["role"]),
            member_type="human",
        )
        db.add(member)
        await db.commit()

    # C-002 audit: a token was redeemed into real membership. Emitted after the
    # transaction closes so the audit write can never poison the atomic claim;
    # detail is ids/role only — the raw token never enters the audit row.
    await emit_invite_redeemed(
        request.app.state.session_factory(),
        actor_user_id=principal.userId,
        team_id=claimed["team_id"],
        target_id=str(claimed["id"]),
        session_owner=True,
        detail={"email": claimed["email"], "role": str(claimed["role"])},
    )

    return {"membership": {"teamId": claimed["team_id"], "role": str(claimed["role"])},
            "alreadyMember": False}
