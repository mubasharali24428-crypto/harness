"""Task 42 (owner decision D2) — soft-remove of team seats.

`audit_log.actor_member_id` references `team_members.id` ON DELETE RESTRICT (migration 0023) and
the audit log is append-only, so a seat that ever acted cannot be deleted: the old hard DELETE
answered 500 on Postgres (SQLite never enforced the FK, which is why unit tests stayed green).

A removed seat therefore keeps its row, as the FK target that historical audit rows resolve
their actor name through, and stops being a seat:

- ``removed_at``      is stamped (NULL means active);
- ``removed_user_id`` remembers who held it (forensics; erasure anonymises through it);
- ``user_id``         is set to NULL.

Nulling ``user_id`` is the fail-closed half. Every door that turns a person into a principal
looks the seat up by ``(team_id, user_id)``: Section 1 ``get_principal``/login/socket, the shared
bearer dependency in ``harness_contracts.fastapi_auth`` (sections 2, 8, 11, runners,
extensions), SSO, SCIM, notifications and every section4 ``team_members`` lookup. None of them
needs to learn about ``removed_at`` to stop matching a removed person. Queries that scan a whole
team WITHOUT a user id (the roster list, last-admin counts) filter ``removed_at IS NULL``
explicitly. A re-invited person gets a fresh seat row; the dead one is never reactivated.

The caller deletes the person's ``auth_sessions`` for the scope (existing revoke pattern) and
commits; nothing here commits.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .timeutil import now_utc

# Column expression for "this seat is active" in raw SQL (alias-free).
ACTIVE_SQL = "removed_at IS NULL"


async def soft_remove_seats(db: AsyncSession, user_id: str, scope_sql: str = "",
                            params: dict | None = None) -> list[str]:
    """Soft-remove every active seat ``user_id`` holds, narrowed by ``scope_sql``.

    ``scope_sql`` is a trusted, code-owned fragment such as ``" AND team_id = :t"``; its bind
    values come in ``params``. Returns the ids of the seats it removed (for audit detail).
    """
    bind = {**(params or {}), "u": user_id, "n": now_utc()}
    where = f"user_id = :u AND {ACTIVE_SQL}{scope_sql}"
    ids = [r[0] for r in (await db.execute(
        text(f"SELECT id FROM team_members WHERE {where} ORDER BY id"), bind)).all()]
    if ids:
        await db.execute(text(
            "UPDATE team_members SET removed_at = :n, removed_user_id = user_id, user_id = NULL"
            f" WHERE {where}"), bind)
    return ids
