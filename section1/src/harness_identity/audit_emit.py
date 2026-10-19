"""Mission 11 W1-A2 — thin typed emission helpers over write_audit (C-002).

One helper per pinned registry action so call sites stay one-liners:

    await emit_apikey_added(session, actor=principal, target_id=key_row.id,
                            team_id=principal.teamId, detail={...})

Design law (brief HARD RULES):
- NEVER throws into the request path: every helper wraps its write in a
  broad try/except and degrades to a warning log on failure. A broken audit
  sink must not take down the user request it is observing.
- No secrets in audit content: callers pass ids/hints only. As defense in
  depth, `_scrub` drops any detail key matching secret-shaped names
  (token/key/secret/password/code/otp...) before the row is written.
- Typed surface: every helper takes an explicit ``session`` plus explicit
  actor/team/target ids — no hidden global state, trivially assertable.

Actor identity note: Principal (harness_contracts) carries userId, not the
team_members.id that audit_log.actor_member_id references; helpers resolve
the caller's TeamMember row inside the SAME session so the FK always holds
(NULL member id rather than a fabricated one when membership is missing).
``team_id`` lands in detail for every action — audit_log has no team column
and the frozen models/migrations are not ours to change this wave.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import write_audit
from .models import AuditLog, TeamMember

logger = logging.getLogger(__name__)

_SECRET_KEY_PATTERN = re.compile(
    # Deliberately broad (substring match): any detail key CONTAINING these
    # words is dropped. False positives cost a metadata field; false negatives
    # leak secrets into an immutable log.
    r"token|secret|password|passwd|key|otp|authorization|credential",
    re.IGNORECASE,
)


def _scrub(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Drop secret-shaped keys from audit detail — belt to callers' braces."""
    if not detail:
        return {}
    return {k: v for k, v in detail.items() if not _SECRET_KEY_PATTERN.search(k)}


async def _member_id(db: AsyncSession, user_id: str | None, team_id: str | None) -> str | None:
    """Resolve the caller's TeamMember.id (FK target of actor_member_id)."""
    if user_id is None or team_id is None:
        return None
    row = (
        await db.execute(
            select(TeamMember.id).where(
                TeamMember.user_id == user_id, TeamMember.team_id == team_id
            )
        )
    ).scalar_one_or_none()
    return str(row) if row is not None else None


async def _emit(
    db: AsyncSession,
    action: str,
    *,
    user_id: str | None = None,
    team_id: str | None = None,
    actor_kind: str = "human",
    target_type: str,
    target_id: str = "",
    detail: dict[str, Any] | None = None,
    session_owner: bool = False,
) -> AuditLog | None:
    """Shared body: resolve actor, scrub detail, write — or warn, never raise."""
    payload = {"teamId": team_id} if team_id else {}
    payload.update(_scrub(detail))
    try:
        member_id = await _member_id(db, user_id, team_id)
        row = await write_audit(
            db,
            action,
            actor_member_id=member_id,
            actor_kind=actor_kind,
            target_type=target_type,
            target_id=target_id,
            detail=payload,
        )
        # Outbound integrations consume only a closed identity projection of
        # selected audit events. Keep the audit commit authoritative if an
        # older schema has not yet applied the optional integration migration.
        try:
            from .webhooks import enqueue_audit_event

            if await enqueue_audit_event(db, row):
                await db.commit()
        except Exception:  # noqa: BLE001 — a webhook sink never blocks audit
            await db.rollback()
            logger.warning("webhook event enqueue failed for %s", action, exc_info=True)
        return row
    except Exception:  # noqa: BLE001 — emission must never break the request path
        logger.warning("audit emission failed for %s", action, exc_info=True)
        if session_owner:
            # Only roll back a transaction THIS call site owns. When the caller
            # passes their own live session we leave its transaction untouched.
            await db.rollback()
        return None


async def emit_apikey_added(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """apikey.added — call from the key MINT handler after the row commits."""
    return await _emit(
        session,
        "apikey.added",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="api_key",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_apikey_removed(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """apikey.removed — call from the key REVOKE handler."""
    return await _emit(
        session,
        "apikey.removed",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="api_key",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_invite_minted(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """invite.minted — invite creation. NEVER pass the raw token as detail."""
    return await _emit(
        session,
        "invite.minted",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_invite",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_invite_redeemed(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """invite.redeemed — token claim + membership insert."""
    return await _emit(
        session,
        "invite.redeemed",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_invite",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_invite_revoked(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """invite.revoked — admin deletes an unredeemed invite."""
    return await _emit(
        session,
        "invite.revoked",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_invite",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_invite_resent(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """invite.resent — M14-F2a: admin re-mints an unredeemed invite; the old
    token is revoked in the same transaction. NEVER pass either raw token."""
    return await _emit(
        session,
        "invite.resent",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_invite",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_billing_plan_changed(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str = "",
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """billing.plan_changed — admin PUT /billing/plan. Detail carries plan names only."""
    return await _emit(
        session,
        "billing.plan_changed",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="billing_plan",
        target_id=target_id or team_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_billing_limits_changed(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str = "",
    before: dict[str, Any],
    after: dict[str, Any],
) -> AuditLog | None:
    """billing.limits_changed — before/after operational quotas, with no secret data."""
    return await _emit(
        session,
        "billing.limits_changed",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="billing_plan",
        target_id=target_id or team_id,
        detail={"before": before, "after": after},
    )


async def emit_run_started(
    session: AsyncSession,
    *,
    run_id: str,
    project_id: str = "",
    team_id: str | None = None,
    actor_user_id: str | None = None,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "agent",
    session_owner: bool = False,
) -> AuditLog | None:
    """run.started — engine enters running state. Section-4 seam: exported for
    ATLAS merge wiring into runs/{id}/start (W1-A2 does NOT edit section4)."""
    return await _emit(
        session,
        "run.started",
        user_id=actor_user_id,
        team_id=team_id,
        actor_kind=actor_kind,
        target_type="agent_run",
        target_id=run_id,
        detail={"projectId": project_id} if project_id else detail,
        session_owner=session_owner,
    )


async def emit_run_stopped(
    session: AsyncSession,
    *,
    run_id: str,
    project_id: str = "",
    team_id: str | None = None,
    actor_user_id: str | None = None,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "agent",
    session_owner: bool = False,
) -> AuditLog | None:
    """run.stopped — engine leaves running state. Section-4 seam, same note as
    emit_run_started: coordination export only, no section4 edits this lane."""
    return await _emit(
        session,
        "run.stopped",
        user_id=actor_user_id,
        team_id=team_id,
        actor_kind=actor_kind,
        target_type="agent_run",
        target_id=run_id,
        detail={"projectId": project_id} if project_id else detail,
        session_owner=session_owner,
    )


def emit_plan_rejected_contract() -> dict[str, str]:
    """Coordination note for ATLAS merge (plan.rejected consumed by W1-D1).

    W1-D1's reject_plan lives in section4/src/harness_orchestrator/{api,state_machine}.py
    — files owned by D1 alone, so A2 ships the symbol contract instead of the edit:

        from harness_identity.audit_emit import emit_plan_rejected

        await emit_plan_rejected(session, actor_user_id=<caller userId>,
                                 team_id=<principal.teamId>, target_id=<runId>,
                                 detail={"reason": <short reason>})
    """
    return {
        "action": "plan.rejected",
        "helper": "harness_identity.audit_emit.emit_plan_rejected",
        "consumer": "W1-D1 (section4 reject_plan)",
    }


async def emit_plan_rejected(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """plan.rejected — already allowlisted pre-wave; exported here so W1-D1's
    reject path can stay a one-liner at merge without touching S1 internals."""
    return await _emit(
        session,
        "plan.rejected",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="agent_run",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_member_removed(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """member.removed — Mission 12 E3a: admin deletes a team membership.

    Call from the DELETE /teams/{id}/members/{uid} handler AFTER the membership
    row commits; target_id is the (now-gone) TeamMember.id, detail carries the
    removed userId + former role only.
    """
    return await _emit(
        session,
        "member.removed",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_member",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_member_role_changed(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """member.role_changed — Mission 12 E3a: admin PATCHes a member's role.

    Call from the PATCH /teams/{id}/members/{uid} handler AFTER the role
    commits; detail carries {targetUserId, fromRole, toRole}.
    """
    return await _emit(
        session,
        "member.role_changed",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_member",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )

async def emit_reviewer_thresholds_saved(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """reviewer_thresholds.saved — M15-G6b: admin PUT of reviewer thresholds.

    Call from the PUT /teams/{teamId}/settings/reviewer-thresholds handler
    AFTER the row commits; target_id is the teamId, detail carries {created}.
    """
    return await _emit(
        session,
        "reviewer_thresholds.saved",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_settings",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_reviewer_thresholds_deleted(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """reviewer_thresholds.deleted — M15-G6b: admin reset to defaults.

    Emitted only when a row actually existed; the idempotent no-row 204 stays
    silent. Call AFTER the delete commits.
    """
    return await _emit(
        session,
        "reviewer_thresholds.deleted",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_settings",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_rules_saved(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """rules.saved — Mission 12 G10: project rules PUT (create or update).

    Call from the PUT /projects/{projectId}/rules handler AFTER the row
    commits; target_id is the projectId, detail carries {chars, created}.
    """
    return await _emit(
        session,
        "rules.saved",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="project_rules",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_rules_deleted(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """rules.deleted — Mission 12 G10: project rules DELETE.

    Emitted only when a row actually existed; the idempotent no-row 204 stays
    silent. Call AFTER the delete commits.
    """
    return await _emit(
        session,
        "rules.deleted",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="project_rules",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_sso_config_saved(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """sso.config_saved — Mission 22 M4: team IdP configuration PUT (create or
    update). Call from sso.py's PUT handler AFTER the row commits; target_id is
    the teamId, detail carries {created}. NEVER pass the client secret as
    detail — _scrub drops secret-shaped keys, but don't rely on that here."""
    return await _emit(
        session,
        "sso.config_saved",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_sso_config",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_sso_config_deleted(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "human",
    session_owner: bool = False,
) -> AuditLog | None:
    """sso.config_deleted — Mission 22 M4: team IdP config removed (SSO off,
    OTP-only login restored). Emitted only when a row actually existed; the
    idempotent no-row 204 stays silent. Call AFTER the delete commits."""
    return await _emit(
        session,
        "sso.config_deleted",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team_sso_config",
        target_id=target_id,
        detail=detail,
        actor_kind=actor_kind,
        session_owner=session_owner,
    )


async def emit_audit_exported(
    session: AsyncSession,
    *,
    actor_user_id: str,
    team_id: str,
    detail: dict[str, Any] | None = None,
) -> AuditLog | None:
    """audit.exported - an admin read the team's audit/event evidence (stream, cursor, rows)."""
    return await _emit(
        session,
        "audit.exported",
        user_id=actor_user_id,
        team_id=team_id,
        target_type="team",
        target_id=team_id,
        detail=detail,
    )
