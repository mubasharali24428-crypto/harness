"""Auth/session contract enforcement for FastAPI services.

Factory-based so each section wires its own DB session factory; the SQL below speaks
ONLY to the frozen schema (auth_sessions, team_members, users) by name.

Heavy deps (sqlalchemy, fastapi) are imported lazily inside the factory so sections
that only need types/events don't pull them transitively.
"""

import re
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


# P-17 client trust portal. A team guest is an outside reader (a partner's
# end-client): everything is refused except GET on the routes below, and only
# for a project an admin granted them (`project_guest_grants`). The gate sits in
# the principal resolvers, so no handler has to remember it. Default is deny:
# a new route is closed to guests until it is listed here.
_GUEST_OPEN = (  # no project involved; the handler answers for the caller only
    # \Z, not $: re's $ also matches before a trailing newline, so "path\n"
    # would pass the gate and then die at the router — permitted-but-useless,
    # which a deny-by-default gate must not be (pinned in test_portal_guest).
    ("GET", re.compile(r"^/api/v1/me\Z")),
    ("GET", re.compile(r"^/api/v1/portal/projects\Z")),
    ("POST", re.compile(r"^/api/v1/auth/logout\Z")),
)
_GUEST_SCOPED = (  # GET only; group 1 is the id the SQL resolves to a project
    (re.compile(r"^/api/v1/orchestrator/runs/([^/]+)(?:/plan)?\Z"),
     "SELECT project_id FROM agent_runs WHERE id = :id"),
    (re.compile(r"^/api/v1/reviews/([^/]+)\Z"),
     "SELECT project_id FROM pull_requests WHERE id = :id"),
)
_GUEST_RUNS_LIST = "/api/v1/orchestrator/runs"  # needs ?projectId=<granted project>
_GRANT_SQL = """
    SELECT 1 FROM project_guest_grants g
    JOIN projects p ON p.id = g.project_id AND p.team_id = :team
    WHERE g.user_id = :user AND g.project_id = :project
"""


async def guest_gate(db, request, user_id: str, team_id: str) -> None:
    """Raise 403 unless this guest request is a listed read of a granted project."""
    from fastapi import HTTPException
    from sqlalchemy import text

    method, path = request.method, request.url.path
    if any(m == method and rx.match(path) for m, rx in _GUEST_OPEN):
        return
    project_id = None
    if method == "GET":
        if path == _GUEST_RUNS_LIST:
            project_id = request.query_params.get("projectId")
        for rx, sql in _GUEST_SCOPED:
            found = rx.match(path)
            if found:
                project_id = (await db.execute(text(sql), {"id": found.group(1)})).scalar()
    if project_id and (
        await db.execute(
            text(_GRANT_SQL), {"team": team_id, "user": user_id, "project": project_id}
        )
    ).first():
        return
    # One answer for "not listed", "no such run" and "not granted": no oracle.
    raise HTTPException(
        status_code=403,
        detail={"error": {"code": "GUEST_PORTAL_ONLY", "message": "not available to guests"}},
    )


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
            if role is Role.GUEST:
                await guest_gate(db, request, row["user_id"], row["team_id"])
        return Principal(userId=row["user_id"], teamId=row["team_id"], role=role)

    return get_principal


def hash_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()
