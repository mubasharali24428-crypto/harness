"""DB-backed OAuth state store (cross-process safe).

Timestamps are stored/compared as UTC ISO-8601 strings — see S1's
harness_identity/ephemeral.py for the rationale (SQLite DATETIME is TEXT;
mixing adapter-formatted datetimes with .isoformat() rows breaks expiry
comparisons).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

OAUTH_STATE_TTL_SECONDS = 600


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso(dt: datetime) -> str:
    return dt.isoformat() if dt.tzinfo else dt.replace(tzinfo=UTC).isoformat()


async def insert_oauth_state(
    factory: async_sessionmaker, state: str, user_id: str,
    ttl: int = OAUTH_STATE_TTL_SECONDS,
) -> None:
    now_iso = _now_iso()
    async with factory() as db:
        await db.execute(
            text("DELETE FROM oauth_states WHERE expires_at < :cutoff"), {"cutoff": now_iso}
        )
        await db.execute(
            text(
                "INSERT INTO oauth_states (state, user_id, expires_at, created_at)"
                " VALUES (:s, :u, :e, :c)"
            ),
            {
                "s": state,
                "u": user_id,
                "e": _iso(datetime.now(UTC) + timedelta(seconds=ttl)),
                "c": now_iso,
            },
        )
        await db.commit()


async def consume_oauth_state(factory: async_sessionmaker, state: str):
    async with factory() as db:
        row = (
            await db.execute(
                text(
                    "DELETE FROM oauth_states"
                    " WHERE state = :s AND expires_at > :cutoff RETURNING user_id"
                ),
                {"s": state, "cutoff": _now_iso()},
            )
        ).first()
        await db.commit()
    return row.user_id if row else None
