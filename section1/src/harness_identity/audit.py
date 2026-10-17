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
        # A1-S1: policy evaluator denials + capability grant issuance (ids only).
        "policy.denied",
        "policy.capability_issued",
        # A1-S2: personal key lifecycle + consent state machine (ids only).
        "personal_key.added",
        "personal_key.removed",
        "key_share.offered",
        "key_share.accepted",
        "key_share.declined",
        "key_share.revoked",
        # A1-S3: org model allowlist workflow + model routing policy.
        "model_allowlist.requested",
        "model_allowlist.approved",
        "model_allowlist.denied",
        "model_allowlist.revoked",
        "model_policy.saved",
        # A1-S4: package trust, SCIM provisioning, retention (ids/reasons only).
        "package_trust.issuer_added",
        "package_trust.issuer_revoked",
        "package_trust.package_revoked",
        "scim.token_created",
        "scim.token_revoked",
        "scim.user_provisioned",
        "scim.user_deprovisioned",
        "retention.policy_saved",
        "retention.applied",
        # A1-S5: workspace lifecycle, trial, residency, compliance evidence export.
        "workspace.created",
        "workspace.renamed",
        "workspace.closed",
        "trial.started",
        "trial.expired",
        "trial.converted",
        "compliance.evidence_exported",
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
