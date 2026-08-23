"""D5 authorization guards (wave 6 Phase 0 / P0.5).

Route-layer enforcement of two previously-missing checks:

1. Team ownership — every run route must prove ``agent_runs.project_id``
   belongs to the caller's team before touching state. The join speaks only to
   frozen table names (``agent_runs`` → ``projects``), mirroring AGENTS.md's
   cross-section rule. When no session factory is wired (bare unit-test mode),
   the DB-backed half is skipped exactly like rehydration is today.
2. Role permission — mutating actions require ``roles.can()`` from the frozen
   contracts package (viewer/guest cannot drive runs; only admin/engineer can
   approve or reject plans). ``can(..., "read")`` is True for everyone, which
   is why read-only routes call only the ownership guard.
"""

from harness_contracts.roles import Principal, TeamScopedAction, can


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


def require_role(principal: Principal, action: TeamScopedAction) -> None:
    """Raise :class:`RoleDeniedError` unless ``principal.role`` allows ``action``."""
    if not can(principal, action):
        raise RoleDeniedError(principal.userId, str(principal.role), action)


async def authorize_run(service, run_id: str, principal: Principal) -> dict:
    """Resolve the run and enforce team ownership; returns the run record.

    ``service`` is the OrchestratorService: ``_ensure_run`` rehydrates or seeds
    the run as usual, then the project→team ownership check reads the frozen
    schema through the service's own session factory when one is wired.
    """
    run_data = await service._ensure_run(run_id)
    factory = getattr(service, "session_factory", None)
    if factory is None:
        return run_data  # bare unit mode: no DB, nothing to check against

    from sqlalchemy import text

    project_id = run_data.get("projectId")
    if not project_id:
        return run_data  # run never bound to a project — first propose owns it below
    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT team_id FROM projects WHERE id = :p"), {"p": project_id}
            )
        ).first()
    if row is not None and row.team_id != principal.teamId:
        raise OwnershipDeniedError(run_id, principal.teamId)
    return run_data
