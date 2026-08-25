"""M18-C5 (MAC2-F3) — invite redemption binds to the INVITED IDENTITY.

Finding F3 (m17-findings-mac2.md): ``redeem_invite`` bound control solely to
token confidentiality — any authed account presenting an intercepted/forwarded
token joined the team AT THE MINTED ROLE, including ``admin``. The claim row
carried the invited email but it was used only cosmetically.

Fix under test (design choice: email-binding, not a claim-link scheme —
simpler, provable, zero new schema):
  - the redeem call must present the invited ``email``;
  - a LIVE token answers nothing to an identity-less probe except
    403 INVITE_IDENTITY_MISMATCH, and validate-first means a failed identity
    check NEVER consumes the token;
  - dead tokens keep their honest legacy codes (404/409/410) regardless of
    what identity was presented — revocation supersedes identity.

Self-contained fixtures (conftest.py is frozen this wave), same seeding
pattern as tests/test_invites.py.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.invites import INVITE_TTL_DAYS, TeamInvite, _hash_token, router
from harness_identity.models import AuthSession, Base, Team, TeamMember, User

# ------------------------------------------------------------ fixtures


@pytest.fixture
async def f3_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def f3_client(f3_factory):
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = f3_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _seed_user(
    factory, email: str, *, role: str = "admin", team_id: str | None = None
):
    """Team/user/membership/session rows directly; returns ids + bearer."""
    async with factory() as db:
        if team_id is None:
            team = Team(name=f"team-{email}")
            db.add(team)
            await db.flush()
            team_id = team.id
        else:
            team = await db.get(Team, team_id)
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=user.id, display_name=user.name, role=role))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {"teamId": team.id, "userId": user.id, "token": token}


async def _insert_invite(
    factory,
    team_id: str,
    *,
    email: str = "invitee@x.com",
    role: str = "engineer",
    redeemed_at: datetime | None = None,
    revoked_at: datetime | None = None,
    expires_in_days: float = INVITE_TTL_DAYS,
) -> tuple[TeamInvite, str]:
    """Insert an invite row with a KNOWN raw token (for redemption tests)."""
    raw = "f3-token-" + email
    async with factory() as db:
        row = TeamInvite(
            team_id=team_id,
            email=email,
            role=role,
            token_hash=_hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
            redeemed_at=redeemed_at,
            revoked_at=revoked_at,
            sent_at=datetime.now(UTC),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row, raw


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _member_count(factory, team_id: str, user_id: str) -> int:
    async with factory() as db:
        return (
            await db.execute(
                select(func.count())
                .select_from(TeamMember)
                .where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
            )
        ).scalar_one()


async def _invite_row(factory, raw_token: str) -> TeamInvite:
    async with factory() as db:
        return (
            await db.execute(
                select(TeamInvite).where(TeamInvite.token_hash == _hash_token(raw_token))
            )
        ).scalar_one()


# ------------------------------------------------------- THE F3 property


async def test_forwarded_admin_token_cannot_enroll_a_foreign_account(
    f3_factory, f3_client
):
    """The exact finding-F3 attack: an admin-grade invite is forwarded to an
    attacker who holds their OWN account. Token alone must enroll nobody."""
    admin = await _seed_user(f3_factory, "boss@x.com", role="admin")
    attacker = await _seed_user(f3_factory, "mallory@evil.com")
    _, raw = await _insert_invite(
        f3_factory, admin["teamId"], email="alice@x.com", role="admin"
    )

    # No email presented -> body validation rejects before anything else.
    r = await f3_client.post(
        "/api/v1/invites/redeem", json={"token": raw}, headers=_bearer(attacker["token"])
    )
    assert r.status_code == 422

    # Wrong email presented -> fail closed, and the token is NOT consumed.
    r = await f3_client.post(
        "/api/v1/invites/redeem",
        json={"token": raw, "email": "mallory@evil.com"},
        headers=_bearer(attacker["token"]),
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"]["code"] == "INVITE_IDENTITY_MISMATCH"

    row = await _invite_row(f3_factory, raw)
    assert row.redeemed_at is None, "a failed identity probe must never burn the token"
    assert await _member_count(f3_factory, admin["teamId"], attacker["userId"]) == 0


async def test_validate_first_wrong_identity_leaves_token_redeemable_by_owner(
    f3_factory, f3_client
):
    """DoS-by-forwarding is out: probing with a wrong identity cannot poison
    the invite — the legitimate recipient still redeems afterwards."""
    admin = await _seed_user(f3_factory, "v-admin@x.com", role="admin")
    prober = await _seed_user(f3_factory, "prober@z.com")
    owner = await _seed_user(f3_factory, "owner@y.com")
    _, raw = await _insert_invite(f3_factory, admin["teamId"], email="owner@y.com")

    for _ in range(3):
        r = await f3_client.post(
            "/api/v1/invites/redeem",
            json={"token": raw, "email": "prober@z.com"},
            headers=_bearer(prober["token"]),
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"]["code"] == "INVITE_IDENTITY_MISMATCH"

    r = await f3_client.post(
        "/api/v1/invites/redeem",
        json={"token": raw, "email": "Owner@Y.com"},  # case-insensitive match
        headers=_bearer(owner["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["membership"] == {"teamId": admin["teamId"], "role": "engineer"}
    assert await _member_count(f3_factory, admin["teamId"], owner["userId"]) == 1


async def test_email_match_is_normalized_not_strict(f3_factory, f3_client):
    """Whitespace/case tolerance mirrors mint normalization; the gate binds
    the MAILBOX, not the exact string."""
    admin = await _seed_user(f3_factory, "n-admin@x.com", role="admin")
    joiner = await _seed_user(f3_factory, "n-join@y.com")
    _, raw = await _insert_invite(f3_factory, admin["teamId"], email="invitee@x.com")

    r = await f3_client.post(
        "/api/v1/invites/redeem",
        json={"token": raw, "email": "  INVITEE@X.com "},
        headers=_bearer(joiner["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["alreadyMember"] is False


# ------------------------------------------------------- legacy codes hold


@pytest.mark.parametrize(
    ("seed_kwargs", "expected_status", "expected_code"),
    [
        pytest.param(
            {"redeemed_at": datetime.now(UTC)},
            409,
            "INVITE_ALREADY_REDEEMED",
            id="redeemed",
        ),
        pytest.param(
            {"revoked_at": datetime.now(UTC)}, 410, "INVITE_REVOKED", id="revoked"
        ),
        pytest.param({"expires_in_days": -2}, 410, "INVITE_EXPIRED", id="expired"),
    ],
)
async def test_dead_tokens_answer_honest_codes_even_with_correct_email(
    f3_factory, f3_client, seed_kwargs, expected_status, expected_code
):
    """Ordering law: revocation supersedes identity. Dead invites keep their
    historical codes whether or not the presented email matches."""
    admin = await _seed_user(f3_factory, "d-admin@x.com", role="admin")
    joiner = await _seed_user(f3_factory, "d-join@y.com")
    _, raw = await _insert_invite(f3_factory, admin["teamId"], **seed_kwargs)

    r = await f3_client.post(
        "/api/v1/invites/redeem",
        json={"token": raw, "email": "invitee@x.com"},
        headers=_bearer(joiner["token"]),
    )
    assert r.status_code == expected_status, r.text
    assert r.json()["detail"]["error"]["code"] == expected_code


async def test_unknown_token_stays_404_with_any_email(f3_factory, f3_client):
    anyone = await _seed_user(f3_factory, "u-any@x.com")
    r = await f3_client.post(
        "/api/v1/invites/redeem",
        json={"token": "no-such-token-value-at-all-here", "email": "u-any@x.com"},
        headers=_bearer(anyone["token"]),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "INVITE_NOT_FOUND"


# ------------------------------------------------------- already-member path


async def test_already_member_path_is_identity_gated_too(f3_factory, f3_client):
    """The consume-and-report path burns tokens too, so it sits behind the
    same identity gate: wrong email -> 403 and the token stays live."""
    admin = await _seed_user(f3_factory, "am-admin@x.com", role="admin")
    member = await _seed_user(
        f3_factory, "member@y.com", role="engineer", team_id=admin["teamId"]
    )
    outsider = await _seed_user(f3_factory, "out@z.com")
    _, raw = await _insert_invite(f3_factory, admin["teamId"], email="member@y.com")

    r = await f3_client.post(
        "/api/v1/invites/redeem",
        json={"token": raw, "email": "out@z.com"},
        headers=_bearer(outsider["token"]),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "INVITE_IDENTITY_MISMATCH"
    row = await _invite_row(f3_factory, raw)
    assert row.redeemed_at is None

    r = await f3_client.post(
        "/api/v1/invites/redeem",
        json={"token": raw, "email": "member@y.com"},
        headers=_bearer(member["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alreadyMember"] is True
    assert body["membership"]["role"] == "engineer"  # existing role reported
