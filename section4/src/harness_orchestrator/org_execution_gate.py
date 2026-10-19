"""Organization emergency execution stop (master prompt part 51.007).

Checked on direct execution start and again when a queued run is claimed. Teams
without an organization retain the pilot behavior. An organization-linked team
fails closed if the organization control cannot be read.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException
from harness_contracts.timeutil import db_now

logger = logging.getLogger(__name__)

CODE = "ORGANIZATION_EXECUTION_DISABLED"


class OrganizationExecutionDisabledError(HTTPException):
    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        super().__init__(
            status_code=409,
            detail={
                "error": {
                    "code": CODE,
                    "message": "organization emergency control has disabled agent execution",
                }
            },
        )


async def organization_execution_state(
    factory: Any, project_id: str | None
) -> tuple[str, bool] | None:
    if factory is None or not project_id:
        return None
    from sqlalchemy import text

    async with factory() as db:
        try:
            org_id = (
                await db.execute(
                    text(
                        "SELECT t.org_id FROM projects p JOIN teams t ON t.id = p.team_id "
                        "WHERE p.id = :p"
                    ),
                    {"p": project_id},
                )
            ).scalar_one_or_none()
        except Exception:  # noqa: BLE001 — pre-0038 scratch schema has no org linkage
            logger.debug("organization execution gate: organization schema absent")
            await db.rollback()
            return None
        if org_id is None:
            return None
        try:
            disabled = (
                await db.execute(
                    text("SELECT execution_disabled FROM organizations WHERE id = :o"),
                    {"o": org_id},
                )
            ).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 — a linked org needs the new control column
            logger.warning("organization execution gate could not read control", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "ORGANIZATION_CONTROL_UNAVAILABLE",
                        "message": "organization execution control could not be checked",
                    }
                },
            ) from exc
    if disabled is None:
        return None
    if not bool(disabled):
        return None
    return str(org_id), True


async def _audit_refusal(
    factory: Any, run_id: str, project_id: str, org_id: str, where: str
) -> None:
    from sqlalchemy import text

    try:
        async with factory() as db:
            detail = json.dumps(
                {"runId": run_id, "projectId": project_id, "orgId": org_id, "where": where}
            )
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, "
                    "target_id, detail, ts) VALUES (NULL, 'system', 'org.execution_refused', "
                    "'org', :o, :d, :n)"
                ),
                {"o": org_id, "d": detail, "n": db_now()},
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — audit failure must not open the execution gate
        logger.exception("organization execution refusal audit failed (run=%s)", run_id)


async def check_organization_execution(
    service: Any,
    run_id: str,
    project_id: str | None,
    *,
    where: str = "start",
) -> str | None:
    factory = getattr(service, "run_queue_factory", None) or getattr(
        service, "session_factory", None
    )
    state = await organization_execution_state(factory, project_id)
    if state is None or not state[1]:
        return None
    org_id = state[0]
    await _audit_refusal(factory, run_id, project_id or "", org_id, where)
    return org_id


async def enforce_organization_execution(service: Any, run_id: str,
                                         project_id: str | None) -> None:
    org_id = await check_organization_execution(service, run_id, project_id)
    if org_id:
        raise OrganizationExecutionDisabledError(org_id)
