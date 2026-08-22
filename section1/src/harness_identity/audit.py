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
        "forbidden.command_blocked",
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
