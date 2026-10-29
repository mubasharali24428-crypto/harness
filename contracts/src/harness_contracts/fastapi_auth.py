"""Auth/session contract enforcement for FastAPI services.

Factory-based so each section wires its own DB session factory; the SQL below speaks
ONLY to the frozen schema (auth_sessions, team_members, users) by name.

Heavy deps (sqlalchemy, fastapi) are imported lazily inside the factory so sections
that only need types/events don't pull them transitively.
"""

from datetime import UTC, datetime

from .roles import Principal, Role

# W16: the seat is the authority, not the session row. This join is the reason a
# deactivation reaches EVERY service that uses this dependency (sections 2, 8,
# 11, the deployed runners, the extensions server) rather than only Section 1's
# own `get_principal`. Without it a revoked person kept acting on the GitHub,
# review and context surfaces with the same token, because those services never
# looked at `users` at all. Same 401 as an expired session: a bearer surface
# gets no oracle about why a token stopped working.
_LOOKUP_SQL = """
    SELECT s.user_id AS user_id, m.team_id AS team_id, m.role AS role, s.expires_at AS expires_at
    FROM auth_sessions s
    JOIN team_members m ON m.team_id = s.team_id AND m.user_id = s.user_id
    JOIN users u ON u.id = s.user_id AND u.is_active
    WHERE s.token_hash = :h
    LIMIT 1
"""


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def make_bearer_dep(session_factory):
    import hashlib

    from fastapi import HTTPException, Request
    from sqlalchemy import text

    async def get_principal(request: Request) -> Principal:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = auth_header.removeprefix("Bearer ").strip()
        hashed = hashlib.sha256(token.encode()).hexdigest()
        async with session_factory() as db:
            row = (await db.execute(text(_LOOKUP_SQL), {"h": hashed})).mappings().first()
        if row is None:
            raise HTTPException(status_code=401, detail="invalid or expired session")
        expires = row["expires_at"]
        if hasattr(expires, "tzinfo") and not expires.tzinfo:
            expires = expires.replace(tzinfo=UTC)
        if getattr(expires, "tzinfo", None) is not None and expires <= datetime.now(UTC):
            raise HTTPException(status_code=401, detail="invalid or expired session")
        # SQLAlchemy's Enum() stores member NAMES ('ADMIN'); StrEnum values are
        # 'admin'. Accept either so raw-SQL reads stay portable across drivers.
        raw_role = str(row["role"])
        try:
            role = Role(raw_role)
        except ValueError:
            role = Role(raw_role.lower())
        return Principal(userId=row["user_id"], teamId=row["team_id"], role=role)

    return get_principal


def hash_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()
