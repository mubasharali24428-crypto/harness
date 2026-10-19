"""D5 authorization guards (wave 6 Phase 0 / P0.5) — M18-C1 fail-closed sweep.

Route-layer enforcement of two previously-missing checks:

1. Team ownership — every run route must prove ``agent_runs.project_id``
   belongs to the caller's team before touching state. The join speaks only to
   frozen table names (``agent_runs`` → ``projects``), mirroring AGENTS.md's
   cross-section rule.
2. Role permission — mutating actions require ``roles.can()`` from the frozen
   contracts package (viewer/guest cannot drive runs; only admin/engineer can
   approve or reject plans). ``can(..., "read")`` is True for everyone, which
   is why read-only routes call only the ownership guard.

M18-C1 (P-BETA H-3 / MAC2-F1): the guard used to fail OPEN three separate
ways — an unwired session factory returned the run unchecked, a run without
``projectId`` returned it unchecked, and a dangling ``projects`` row
(``row is None``) was allowed through. Every branch that cannot PROVE
ownership now denies with :class:`AuthzUnavailableError`, which api.py maps
to a 403 ``AUTHZ_UNAVAILABLE`` envelope; the two auditable gaps (no project
binding, dangling project row) additionally write an ``authz.denied`` row to
the frozen ``audit_log`` table. The only remaining allow-without-proof path
is the explicit ``HARNESS_AUTHZ_TEST_MODE=1`` escape hatch for bare unit
tests — legacy suites set it in ``tests/conftest.py``; production wiring
(deploy/runners/services.py) never does.
"""

import json
import logging
import os
from typing import Any

from harness_contracts.roles import Principal, TeamScopedAction, can
from harness_contracts.timeutil import db_now

logger = logging.getLogger(__name__)

#: Escape hatch for bare unit tests (no session factory wired). Production
#: assemblies MUST NOT set this: the default posture for every unverifiable
#: ownership check is DENY. The value must be exactly ``"1"``.
AUTHZ_TEST_MODE_ENV = "HARNESS_AUTHZ_TEST_MODE"

#: Frozen append-only audit action for denied-but-unprovable authorizations.
AUDIT_ACTION_AUTHZ_DENIED = "authz.denied"

#: S1-R05e: lifecycle audit actions. Canonical append-only allowlist lives in
#: S1's audit.py (``APPEND_ONLY_ACTIONS``); S4 writes the rows raw because
#: sections may import only ``harness_contracts``.
AUDIT_ACTION_RUN_STARTED = "run.started"
AUDIT_ACTION_RUN_STOPPED = "run.stopped"
#: P2.3: cancel is a request until the worker is confirmed gone.
AUDIT_ACTION_CANCEL_REQUESTED = "run.cancel_requested"
AUDIT_ACTION_CANCEL_CONFIRMED = "run.cancel_confirmed"
AUDIT_ACTION_RUN_PAUSED = "run.paused"
AUDIT_ACTION_RUN_RESUMED = "run.resumed"


async def emit_run_lifecycle(
    service,
    *,
    action: str,
    run_id: str,
    principal: Principal,
) -> None:
    """Best-effort ``run.started`` / ``run.stopped`` audit row (S1-R05e).

    S1's ``emit_run_started/stopped`` helpers had zero production callers, so
    lifecycle intent existed nowhere in ``audit_log``. Writes through the
    service's own session factory with the same substitution-safe member-id
    resolution as denial rows. Bare unit mode (no factory) is a silent no-op;
    audit failure is logged, never raised — stopping a run must not fail on
    the audit store's health.
    """
    factory = service.session_factory
    if factory is None:
        return
    from sqlalchemy import text

    detail = {
        "runId": run_id,
        "userId": principal.userId,
        "teamId": principal.teamId,
    }
    try:
        async with factory() as db:
            actor_member_id = await _resolve_member_id(
                db, principal.teamId, principal.userId
            )
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action,"
                    " target_type, target_id, detail, ts)"
                    " VALUES (:actor, 'human', :action, 'agent_run', :run_id,"
                    " :detail, :ts)"
                ),
                {
                    "actor": actor_member_id,
                    "action": action,
                    "run_id": run_id,
                    "detail": json.dumps(detail, default=str),
                    "ts": db_now(),
                },
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — audit must never mask the lifecycle op
        logger.exception(
            "lifecycle audit emit failed (action=%s run=%s)", action, run_id
        )


class OwnershipDeniedError(Exception):
    """The run's project belongs to a different team than the principal's."""

    def __init__(self, run_id: str, team_id: str) -> None:
        self.run_id = run_id
        self.team_id = team_id
        super().__init__(f"run '{run_id}' does not belong to team '{team_id}'")


class RoleDeniedError(Exception):
    """The principal's role may not perform this action."""

    def __init__(self, user_id: str, role: str, action: str) -> None:
        self.user_id = user_id
        super().__init__(f"user '{user_id}' with role '{role}' may not {action}")


class AuthzUnavailableError(Exception):
    """The ownership check could not be evaluated, so the request is DENIED.

    M18-C1: replaces the former silent ``return run_data`` branches.
    ``reason`` is a stable machine code (``unwired_session_factory`` /
    ``no_project`` / ``dangling_project``) surfaced verbatim in the API
    envelope so operators can tell "unverifiable" from "not yours".
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"ownership check unavailable ({reason}): {detail}")


def authz_test_mode() -> bool:
    """True only when ``HARNESS_AUTHZ_TEST_MODE`` is explicitly ``"1"``.

    Consulted per evaluation (never cached at import) so deployments and
    tests can flip it via the environment at any point in the process
    lifetime; any other value (including "true") keeps the deny posture.
    """
    return os.environ.get(AUTHZ_TEST_MODE_ENV, "") == "1"


def require_role(principal: Principal, action: TeamScopedAction) -> None:
    """Raise :class:`RoleDeniedError` unless ``principal.role`` allows ``action``."""
    if not can(principal, action):
        raise RoleDeniedError(principal.userId, str(principal.role), action)


async def _resolve_member_id(db: Any, team_id: str | None, user_id: str | None) -> str | None:
    """``team_members.id`` for (team, user) — what ``actor_member_id`` FKs to.

    S1-R05a: binding ``principal.userId`` (users.id) here FK-failed on
    Postgres and the audit row was silently lost. Denials frequently target
    NON-members (that is why they were denied): no membership row is normal,
    and the column is nullable by design — ``detail`` carries the principal
    identity in that case.
    """
    if not team_id or not user_id:
        return None
    from sqlalchemy import text

    row = (
        await db.execute(
            text(
                "SELECT id FROM team_members"
                " WHERE team_id = :t AND user_id = :u LIMIT 1"
            ),
            {"t": team_id, "u": user_id},
        )
    ).first()
    return row.id if row is not None else None


async def _emit_authz_denied(
    service,
    *,
    reason: str,
    run_id: str,
    project_id: str | None,
    principal: Principal,
) -> None:
    """Best-effort ``authz.denied`` audit row for an unevaluable ownership check.

    Writes the frozen ``audit_log`` table directly through the service's own
    session factory (same discipline as plan_gate's local audit wrapper).
    Audit failure is logged, never raised: the denial itself must reach the
    caller regardless of the audit store's health.
    """
    from sqlalchemy import text

    detail = {
        "reason": reason,
        "runId": run_id,
        "projectId": project_id,
        "userId": principal.userId,
        "teamId": principal.teamId,
    }
    try:
        async with service.session_factory() as db:
            actor_member_id = await _resolve_member_id(
                db, principal.teamId, principal.userId
            )
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action,"
                    " target_type, target_id, detail, ts)"
                    " VALUES (:actor, 'human', :action, 'agent_run', :run_id,"
                    " :detail, :ts)"
                ),
                {
                    "actor": actor_member_id,
                    "action": AUDIT_ACTION_AUTHZ_DENIED,
                    "run_id": run_id,
                    "detail": json.dumps(detail, default=str),
                    "ts": db_now(),
                },
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — audit must never mask the denial
        logger.exception(
            "authz_denied audit emit failed (reason=%s run=%s)", reason, run_id
        )


async def authorize_run(service, run_id: str, principal: Principal) -> dict[str, Any]:
    """Resolve the run and enforce team ownership; returns the run record.

    Fail-closed (M18-C1): every branch that cannot prove ownership denies —

    - no ``session_factory`` wired → deny, unless ``HARNESS_AUTHZ_TEST_MODE=1``
      (legacy bare unit mode);
    - run with no ``projectId`` anywhere (memory + ``agent_runs``) → deny +
      ``authz.denied`` audit row (``no_project``): an unattributable run is
      owned by nobody;
    - a genuinely unknown run id (no ``agent_runs`` row at all) denies via
      :class:`OwnershipDeniedError` so the API seam's existing no-oracle 404
      answers exactly as it did before this sweep;
    - ``projects`` row missing for the bound id → deny + ``authz.denied``
      audit row (``dangling_project``): deleted/orphaned bindings never
      widen access;
    - row belongs to another team → deny with :class:`OwnershipDeniedError`
      (mapped to the no-oracle 404 by api.py).
    """
    run_data = await service._ensure_run(run_id)
    factory = getattr(service, "session_factory", None)
    if factory is None:
        if authz_test_mode():
            return run_data  # explicit test-mode escape hatch (conftest sets it)
        raise AuthzUnavailableError(
            "unwired_session_factory",
            f"no session factory wired while authorizing run '{run_id}'"
            f" (set {AUTHZ_TEST_MODE_ENV}=1 only in unit tests)",
        )

    from sqlalchemy import text

    project_id = run_data.get("projectId")
    if not project_id:
        # Memory lost/never had the binding. Consult agent_runs directly
        # (rehydration is best-effort and may have failed wholesale).
        try:
            async with factory() as db:
                run_row = (
                    await db.execute(
                        text("SELECT project_id FROM agent_runs WHERE id = :r"),
                        {"r": run_id},
                    )
                ).first()
        except Exception:  # noqa: BLE001 — store trouble must never ALLOW
            # Existence itself is unverifiable: deny with the same shape an
            # unknown id gets (M13-G5a). Never an open endpoint.
            logger.exception(
                "run-existence probe failed for %s; denying as unknown", run_id
            )
            raise OwnershipDeniedError(run_id, principal.teamId) from None
        if run_row is None:
            # No row anywhere: the run does not exist in this store. Same
            # answer a foreign id gets (M13-G5a no-oracle) — never an
            # existence oracle, never an allow.
            raise OwnershipDeniedError(run_id, principal.teamId)
        project_id = run_row.project_id
        if not project_id:
            await _emit_authz_denied(
                service,
                reason="no_project",
                run_id=run_id,
                project_id=None,
                principal=principal,
            )
            raise AuthzUnavailableError(
                "no_project",
                f"run '{run_id}' carries no projectId; ownership unprovable",
            )

    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT team_id FROM projects WHERE id = :p"), {"p": project_id}
            )
        ).first()
    if row is None:
        await _emit_authz_denied(
            service,
            reason="dangling_project",
            run_id=run_id,
            project_id=project_id,
            principal=principal,
        )
        raise AuthzUnavailableError(
            "dangling_project",
            f"project '{project_id}' of run '{run_id}' has no projects row",
        )
    if row.team_id != principal.teamId:
        raise OwnershipDeniedError(run_id, principal.teamId)
    return run_data
