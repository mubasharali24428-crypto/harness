from datetime import UTC, datetime

from sqlalchemy import event as sa_event

from .models import AuditLog

APPEND_ONLY_ACTIONS = frozenset(
    {
        "plan.approved",
        "plan.rejected",
        "agent.paused",
        "agent.resumed",
        "agent.stopped",
        "takeover.started",
        "takeover.ended",
        "pr.opened",
        "review.approved",
        "review.changes_requested",
        "merge.performed",
        "apikey.added",
        "apikey.removed",
        "invite.minted",
        "invite.redeemed",
        "invite.revoked",
        # M14-F2a: admin resend of an unredeemed invite (old token revoked).
        "invite.resent",
        "billing.plan_changed",
        "run.started",
        "run.stopped",
        # P2.3: cancel request vs confirmed-gone (section4 api.py stop route).
        "run.cancel_requested",
        "run.cancel_confirmed",
        # Session 2 follow-up: pause/resume requests were unaudited.
        "run.paused",
        "run.resumed",
        "approval.self_denied",
        # Part 11.009: an admin exported the team's audit/event evidence.
        "audit.exported",
        "forbidden.command_blocked",
        # S1-R05a: denied-but-unprovable ownership checks (raw-SQL writer in
        # section4 authz.py; canonical allowlist lives here).
        "authz.denied",
        # Mission 12 E3a (H-010): roster management transitions.
        "member.removed",
        "member.role_changed",
        # Mission 12 G10: user-authored project rules lifecycle.
        "rules.saved",
        "rules.deleted",
        # M15-G6b: per-team reviewer-threshold tuning lifecycle.
        "reviewer_thresholds.saved",
        "reviewer_thresholds.deleted",
        # Mission 22 M4: team OIDC IdP configuration lifecycle.
        "sso.config_saved",
        "sso.config_deleted",
        # Multiplayer pilot S2: shared-run collaboration + high-impact approvals
        # (raw-SQL writer in section4 collab.py; canonical allowlist is here).
        "participant.joined",
        "leader.assigned",
        "leader.takeover",
        "redirect.recorded",
        "redirect.scope_changed",
        "handoff.requested",
        "handoff.accepted",
        "handoff.rejected",
        "approval.requested",
        "approval.approved",
        "approval.rejected",
        "approval.amended",
        "approval.invalidated",
        "approval.expired",
        "approval.claimed",
        "approval.completed",
    }
)


async def write_audit(
    session,
    action: str,
    actor_member_id: str | None = None,
    actor_kind: str = "human",
    target_type: str = "",
    target_id: str = "",
    detail: dict | None = None,
) -> AuditLog:
    if action not in APPEND_ONLY_ACTIONS:
        raise ValueError(f"unknown audit action: {action}")
    row = AuditLog(
        actor_member_id=actor_member_id,
        actor_kind=actor_kind,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail or {},
        ts=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def register_append_only_guard() -> None:
    """DB-level backstop: audit rows can never be updated or deleted via the ORM."""

    def block_update(mapper, connection, target):  # noqa: ANN001
        raise RuntimeError("audit_log is append-only")

    sa_event.listen(AuditLog, "before_update", block_update)
    sa_event.listen(AuditLog, "before_delete", block_update)
