"""DB-backed ephemeral auth state (wave 7, cross-process safe).

All timestamps are stored and compared as UTC ISO-8601 strings. SQLite keeps
DATETIME columns as TEXT: binding datetime objects goes through the deprecated
default adapter (space separator) while hand-seeded rows typically use
.isoformat() (T separator), and the two formats do not compare correctly as
strings — an expired row can look valid. Normalizing to ISO text at every
boundary makes comparisons exact regardless of driver.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from harness_contracts import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

WS_TICKET_TTL_SECONDS = 30


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso(dt: datetime) -> str:
    return dt.isoformat() if dt.tzinfo else dt.replace(tzinfo=UTC).isoformat()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def mint_ws_ticket(
    factory: async_sessionmaker,
    principal: Principal,
    project_id: str,
    ttl: int = WS_TICKET_TTL_SECONDS,
) -> tuple[str, int]:
    ticket = secrets.token_urlsafe(32)
    now_iso = _now_iso()
    async with factory() as db:
        await db.execute(
            text("DELETE FROM ws_tickets WHERE expires_at < :cutoff"), {"cutoff": now_iso}
        )
        await db.execute(
            text(
                "INSERT INTO ws_tickets (token_hash, user_id, team_id, role, project_id,"
                " expires_at, created_at)"
                " VALUES (:h, :u, :t, :r, :p, :e, :c)"
            ),
            {
                "h": _hash(ticket),
                "u": principal.userId,
                "t": principal.teamId,
                "r": str(principal.role),
                "p": project_id,
                "e": _iso(datetime.now(UTC) + timedelta(seconds=ttl)),
                "c": now_iso,
            },
        )
        await db.commit()
    return ticket, ttl


async def redeem_ws_ticket(factory: async_sessionmaker, token: str, project_id: str):
    if not token:
        return None
    async with factory() as db:
        row = (
            await db.execute(
                text(
                    "DELETE FROM ws_tickets"
                    " WHERE token_hash = :h AND project_id = :p AND expires_at > :cutoff"
                    " RETURNING user_id, team_id, role"
                ),
                {"h": _hash(token), "p": project_id, "cutoff": _now_iso()},
            )
        ).first()
        await db.commit()
    if row is None:
        return None
    from harness_contracts.roles import Role

    try:
        role = Role(row.role)
    except ValueError:
        role = Role(row.role.lower())
    return Principal(userId=row.user_id, teamId=row.team_id, role=role)
