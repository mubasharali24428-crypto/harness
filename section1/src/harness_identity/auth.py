import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from harness_contracts import Principal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import AuthSession, Team, TeamMember, User
from .models import Role as DbRole

SESSION_TTL_HOURS = 24 * 14


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def hash_token(token: str) -> str:
    from harness_contracts import hash_token as _h

    return _h(token)


async def login_or_register(
    session: AsyncSession,
    email: str,
    team_id: str | None = None,
    team_name: str | None = None,
) -> tuple[str, Principal]:
    """Dev/MVP identity exchange. GitHub OAuth connect flow arrives via Section 2.

    No team_id => creates a solo team (1 human; agent seats added later). Never
    hardcode a minimum team size.
    """
    email = email.lower().strip()
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        user = User(email=email, name=email.split("@")[0])
        session.add(user)
        await session.flush()

    member: TeamMember | None = None
    if team_id:
        member = (
            await session.execute(
                select(TeamMember).where(
                    TeamMember.team_id == team_id, TeamMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=403, detail="not a member of that team")
    else:
        team = Team(name=team_name or f"{user.name or user.email}'s team")
        session.add(team)
        await session.flush()
        member = TeamMember(
            team_id=team.id, user_id=user.id, display_name=user.name, role=DbRole.ADMIN.value
        )
        session.add(member)
        await session.flush()
        from .models import Project

        session.add(Project(team_id=team.id, name="demo-repo"))
        team_id = team.id

    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=hash_token(token),
            user_id=user.id,
            team_id=team_id,
            expires_at=datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS),
        )
    )
    await session.commit()
    return token, Principal(userId=user.id, teamId=team_id, role=DbRole(member.role))


async def get_principal(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth.removeprefix("Bearer ").strip()
    sessions: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sessions() as db:
        row = (
            await db.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        ).scalar_one_or_none()
        expires = _aware(row.expires_at) if row is not None else None
        if row is None or expires < datetime.now(UTC):
            raise HTTPException(status_code=401, detail="invalid or expired session")
        member = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == row.team_id, TeamMember.user_id == row.user_id
                )
            )
        ).scalar_one()
        return Principal(userId=row.user_id, teamId=row.team_id, role=DbRole(member.role))


RequirePrincipal = Depends(get_principal)


# ================================================================
# H2r: inbox-ownership proof (OTP)
# ================================================================

OTP_CODE_LENGTH = 6


def _hash_code(code: str) -> str:
    import hashlib

    return hashlib.sha256(code.strip().encode()).hexdigest()


async def issue_otp(
    session: AsyncSession | async_sessionmaker,
    settings,
    notify,
    email: str,
) -> None:
    """Replace any active codes for `email` and dispatch a fresh one.

    Enumeration-safe by contract: callers answer 200 regardless of whether the
    address exists; delivery is the notifier's concern (dev logger / SMTP).

    ``session`` accepts either a live AsyncSession or the app's
    async_sessionmaker (routes hand one or the other); direct callers with a
    bare factory get a short-lived session opened for them.
    """
    import secrets

    from .models import LoginOtp

    if isinstance(session, async_sessionmaker):
        async with session() as owned:
            return await issue_otp(owned, settings, notify, email)

    email = email.lower().strip()
    code = "".join(secrets.choice("0123456789") for _ in range(OTP_CODE_LENGTH))

    old = (
        await session.execute(select(LoginOtp).where(LoginOtp.email == email))
    ).scalars().all()
    for row in old:
        await session.delete(row)

    expires = datetime.now(UTC) + timedelta(minutes=settings.otp_ttl_minutes)
    session.add(
        LoginOtp(email=email, code_hash=_hash_code(code), expires_at=expires, attempts=0)
    )
    await session.commit()
    notify(email, code)


async def verify_otp(session: AsyncSession, settings, email: str, code: str):
    """Single-use verification; on success delegates to login_or_register so the
    account/team/project provisioning matches legacy login exactly.

    Returns (token, Principal). Raises HTTPException(401) on any failure.
    """
    from datetime import UTC as _UTC

    from fastapi import HTTPException as _HTTPException

    from .models import LoginOtp

    email = email.lower().strip()
    row = (
        await session.execute(
            select(LoginOtp)
            .where(LoginOtp.email == email, LoginOtp.consumed_at.is_(None))
            .order_by(LoginOtp.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise _HTTPException(
            status_code=401,
            detail={"error": {"code": "INVALID_CODE", "message": "no active code"}},
        )

    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=_UTC)
    now = datetime.now(_UTC)
    if expires < now:
        row.consumed_at = now
        await session.commit()
        raise _HTTPException(
            status_code=401,
            detail={"error": {"code": "EXPIRED", "message": "code expired"}},
        )
    if row.attempts >= settings.otp_max_attempts:
        row.consumed_at = now
        await session.commit()
        raise _HTTPException(
            status_code=401,
            detail={"error": {"code": "LOCKED", "message": "too many attempts"}},
        )

    if row.code_hash != _hash_code(code):
        row.attempts += 1
        locked = row.attempts >= settings.otp_max_attempts
        if locked:
            row.consumed_at = now
        await session.commit()
        raise _HTTPException(
            status_code=401,
            detail={"error": {"code": "INVALID_CODE", "message": "wrong code"}},
        )

    row.consumed_at = now
    await session.commit()
    return await login_or_register(session, email)
