"""Wave 9 node C — org invite lifecycle tests.

Self-contained by design (conftest.py is frozen this wave): fixtures are
defined locally and the router under test is mounted on a SCRATCH FastAPI app
(app.py belongs to node E this wave and is NOT imported here). Sessions are
seeded straight into the store with contract-shaped token hashes so no login
route is needed. Everything runs offline on aiosqlite.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.invites import INVITE_TTL_DAYS, TeamInvite, _hash_token, router
from harness_identity.models import (
    AuthSession,
    Base,
    Team,
    TeamMember,
    User,
)

# --------------------------------------------------------------- fixtures


@pytest.fixture
async def invites_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def invites_client(invites_factory):
    """Scratch app exposing ONLY this node's router (registration in app.py is
    ox-alpha's merge step; we must not touch that file)."""
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = invites_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _seed_user(
    factory, email: str, *, role: str = "admin", team_id: str | None = None
):
    """Create team/user/membership/session rows directly; return ids + bearer."""
    import secrets

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
        db.add(TeamMember(team_id=team_id, user_id=user.id, display_name=user.name, role=role))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team_id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {"teamId": team_id, "userId": user.id, "token": token}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _insert_invite(
    factory,
    team_id: str,
    *,
    email: str = "invitee@x.com",
    role: str = "engineer",
    raw_token: str | None = None,
    expires_in_days: float = INVITE_TTL_DAYS,
    redeemed_at: datetime | None = None,
    revoked_at: datetime | None = None,
    sent_at: datetime | bool | None = True,  # True => stamp "now" inside the body
) -> tuple[TeamInvite, str]:
    """Insert an invite row with a KNOWN raw token (for redemption tests)."""
    raw = raw_token or "known-test-token-" + email
    effective_sent = datetime.now(UTC) if sent_at is True else sent_at
    async with factory() as db:
        row = TeamInvite(
            team_id=team_id,
            email=email,
            role=role,
            token_hash=_hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
            redeemed_at=redeemed_at,
            revoked_at=revoked_at,
            sent_at=effective_sent,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row, raw


# ------------------------------------------------------------ happy path


async def test_invite_lifecycle_mint_list_revoke_and_member_created(
    invites_factory, invites_client
):
    admin = await _seed_user(invites_factory, "boss@x.com", role="admin")

    # --- mint: raw token appears exactly once, hashed copy never leaks
    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": "Newbie@X.com", "role": "viewer"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "newbie@x.com"  # normalized
    assert body["role"] == "viewer"
    assert body["teamId"] == admin["teamId"]
    assert len(body["token"]) >= 32
    assert body["redeemedAt"] is None
    invite_id, raw_token = body["id"], body["token"]

    # --- pending list shows exactly the one unredeemed invite, sans tokens
    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/invites", headers=_bearer(admin["token"])
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [invite_id]
    assert all("token" not in i and "tokenHash" not in i for i in items)

    # --- redeem by a second authed user: joins team with invited role
    joiner = await _seed_user(invites_factory, "joiner@y.com")
    r = await invites_client.post(
        "/api/v1/invites/redeem",
        json={"token": raw_token},
        headers=_bearer(joiner["token"]),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["membership"] == {"teamId": admin["teamId"], "role": "viewer"}
    assert out["alreadyMember"] is False

    # membership row really created with the invited role
    from sqlalchemy import select

    async with invites_factory() as db:
        member = (
            (
                await db.execute(
                    select(TeamMember).where(
                        TeamMember.team_id == admin["teamId"],
                        TeamMember.user_id == joiner["userId"],
                    )
                )
            )
            .scalars()
            .one()
        )
        assert member.role == "viewer"

    # M14-F2a: the plain list is the FULL lifecycle now — the redeemed invite
    # stays visible with its terminal status instead of vanishing.
    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/invites", headers=_bearer(admin["token"])
    )
    items = r.json()["items"]
    assert [i["id"] for i in items] == [invite_id]
    assert all(i["status"] == "redeemed" for i in items)
    # ...and ?status=sent reproduces the old pending-only view.
    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/invites?status=sent",
        headers=_bearer(admin["token"]),
    )
    assert r.json()["items"] == []

    # --- a fresh invite can be revoked before use; redemption then 410s
    # (M14-F2a: revoke stamps revoked_at instead of deleting the row)
    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": "shortlived@x.com", "role": "guest"},
        headers=_bearer(admin["token"]),
    )
    revoked = r.json()
    r = await invites_client.delete(
        f"/api/v1/teams/{admin['teamId']}/invites/{revoked['id']}",
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200, r.text
    r = await invites_client.post(
        "/api/v1/invites/redeem",
        json={"token": revoked["token"]},
        headers=_bearer(joiner["token"]),
    )
    assert r.status_code == 410, r.text
    assert r.json()["detail"]["error"]["code"] == "INVITE_REVOKED"


# ------------------------------------------------------- rejections


async def test_non_admin_cannot_mint_or_list_or_revoke(invites_factory, invites_client):
    admin = await _seed_user(invites_factory, "adm@x.com", role="admin")
    engineer = await _seed_user(invites_factory, "eng@x.com", role="engineer")
    outsider = await _seed_user(invites_factory, "out@z.com", role="admin")  # other team

    # engineer of the team: 403 on mint
    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": "someone@x.com", "role": "engineer"},
        headers=_bearer(engineer["token"]),
    )
    assert r.status_code == 403, r.text
    err = r.json()["detail"]
    assert err["error"]["code"] == "FORBIDDEN"  # contract error envelope

    # viewer/guest equally rejected
    viewer = await _seed_user(
        invites_factory, "view@x.com", role="viewer", team_id=admin["teamId"]
    )
    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": "someone@x.com", "role": "engineer"},
        headers=_bearer(viewer["token"]),
    )
    assert r.status_code == 403

    # admin of ANOTHER team: cross-tenant probes answer the same 404 an
    # unknown team would produce on every method (M13-G5a no-oracle law).
    # In-team non-admins still get 403 FORBIDDEN (asserted above).
    for method, url in (
        ("post", f"/api/v1/teams/{admin['teamId']}/invites"),
        ("get", f"/api/v1/teams/{admin['teamId']}/invites"),
        ("delete", f"/api/v1/teams/{admin['teamId']}/invites/whatever-id"),
    ):
        kwargs = {"headers": _bearer(outsider["token"])}
        if method == "post":
            kwargs["json"] = {"email": "someone@x.com", "role": "guest"}
        r = await getattr(invites_client, method)(url, **kwargs)
        assert r.status_code == 404, (method, r.text)
        assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"

    # unauthenticated: 401
    r = await invites_client.get(f"/api/v1/teams/{admin['teamId']}/invites")
    assert r.status_code == 401


async def test_expired_invite_rejected_honestly(invites_factory, invites_client):
    admin = await _seed_user(invites_factory, "exp-admin@x.com", role="admin")
    joiner = await _seed_user(invites_factory, "exp-joiner@y.com")
    _, raw = await _insert_invite(
        invites_factory, admin["teamId"], expires_in_days=-1  # already stale
    )
    r = await invites_client.post(
        "/api/v1/invites/redeem", json={"token": raw}, headers=_bearer(joiner["token"])
    )
    assert r.status_code == 410, r.text
    assert r.json()["detail"]["error"]["code"] == "INVITE_EXPIRED"

    # nothing was marked redeemed and nobody joined
    from sqlalchemy import func, select

    async with invites_factory() as db:
        assert (await db.execute(select(func.count()).select_from(TeamInvite))).scalar_one() == 1


async def test_double_redeem_rejected_and_single_membership(
    invites_factory, invites_client
):
    admin = await _seed_user(invites_factory, "dbl-admin@x.com", role="admin")
    joiner = await _seed_user(invites_factory, "dbl-joiner@y.com")
    _, raw = await _insert_invite(invites_factory, admin["teamId"])

    r1 = await invites_client.post(
        "/api/v1/invites/redeem", json={"token": raw}, headers=_bearer(joiner["token"])
    )
    assert r1.status_code == 200, r1.text
    r2 = await invites_client.post(
        "/api/v1/invites/redeem", json={"token": raw}, headers=_bearer(joiner["token"])
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["error"]["code"] == "INVITE_ALREADY_REDEEMED"

    # still exactly ONE membership row for the joiner
    from sqlalchemy import func, select

    async with invites_factory() as db:
        n = (
            await db.execute(
                select(func.count())
                .select_from(TeamMember)
                .where(
                    TeamMember.team_id == admin["teamId"],
                    TeamMember.user_id == joiner["userId"],
                )
            )
        ).scalar_one()
    assert n == 1


async def test_unknown_token_rejected_404(invites_factory, invites_client):
    anyone = await _seed_user(invites_factory, "any@x.com")
    r = await invites_client.post(
        "/api/v1/invites/redeem",
        json={"token": "totally-unknown-token-value-here"},
        headers=_bearer(anyone["token"]),
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error"]["code"] == "INVITE_NOT_FOUND"


async def test_redeem_requires_auth_and_valid_body(invites_factory, invites_client):
    r = await invites_client.post("/api/v1/invites/redeem", json={"token": "x" * 32})
    assert r.status_code == 401
    # Auth solves before body validation, so junk tokens still need a session:
    anyone = await _seed_user(invites_factory, "auth@x.com")
    short = await invites_client.post(
        "/api/v1/invites/redeem", json={"token": "short"}, headers=_bearer(anyone["token"])
    )
    assert short.status_code == 422  # min-length guard rejects junk early


async def test_invalid_role_and_email_rejected_422(invites_factory, invites_client):
    admin = await _seed_user(invites_factory, "v-admin@x.com", role="admin")
    base = f"/api/v1/teams/{admin['teamId']}/invites"
    hdrs = _bearer(admin["token"])
    r = await invites_client.post(
        base, json={"email": "ok@x.com", "role": "superuser"}, headers=hdrs
    )
    assert r.status_code == 422
    r = await invites_client.post(
        base, json={"email": "not-an-email", "role": "viewer"}, headers=hdrs
    )
    assert r.status_code == 422


# ------------------------------------------------------- members endpoint


async def test_members_list_shows_roles_and_blocks_outsiders(
    invites_factory, invites_client
):
    admin = await _seed_user(invites_factory, "m-admin@x.com", role="admin")
    engineer = await _seed_user(
        invites_factory, "m-eng@x.com", role="engineer", team_id=admin["teamId"]
    )
    outsider = await _seed_user(invites_factory, "m-out@z.com", role="admin")

    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/members", headers=_bearer(engineer["token"])
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    by_email = {i["email"]: i for i in items}
    assert by_email["m-admin@x.com"]["role"] == "admin"
    assert by_email["m-eng@x.com"]["role"] == "engineer"
    assert all("tokenHash" not in i and "token" not in i for i in items)

    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/members", headers=_bearer(outsider["token"])
    )
    # M13-G5a no-oracle law: a foreign probe gets the same NOT_FOUND an
    # unknown team id would — never a 403 confirming the team exists.
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------- schema/migration parity


def test_team_invites_model_matches_migration_shape():
    cols = TeamInvite.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "team_id",
        "email",
        "role",
        "token_hash",
        "expires_at",
        "redeemed_at",
        "revoked_at",  # M14-F2a
        "sent_at",  # M14-F2a
        "created_at",
    }
    assert cols["id"].primary_key
    assert cols["email"].type.length == 255  # type: ignore[attr-defined]
    assert cols["role"].type.length == 20  # type: ignore[attr-defined]
    th = cols["token_hash"]
    assert th.unique and getattr(th.type, "length", None) == 64
    assert cols["expires_at"].nullable is False
    assert cols["redeemed_at"].nullable is True
    # M14-F2a: both lifecycle columns are additive + nullable (0021 migration).
    assert cols["revoked_at"].nullable is True
    assert cols["sent_at"].nullable is True


# ------------------------------------------------- M14-F2a: lifecycle status


async def test_minted_invite_reads_status_sent_with_timestamps(invites_factory, invites_client):
    admin = await _seed_user(invites_factory, "st-admin@x.com", role="admin")
    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites",
        json={"email": "status@x.com", "role": "engineer"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "sent"  # token handed to the admin at mint time
    assert body["redeemedAt"] is None and body["revokedAt"] is None


async def test_lifecycle_list_returns_every_status_and_filters(
    invites_factory, invites_client
):
    admin = await _seed_user(invites_factory, "lc-admin@x.com", role="admin")
    joiner = await _seed_user(invites_factory, "lc-join@y.com")

    _, sent_raw = await _insert_invite(invites_factory, admin["teamId"], email="s@x.com")
    _, redeemed_raw = await _insert_invite(
        invites_factory, admin["teamId"], email="r@x.com"
    )
    _, expired_raw = await _insert_invite(
        invites_factory, admin["teamId"], email="e@x.com", expires_in_days=-2
    )
    _, revoked_raw = await _insert_invite(
        invites_factory,
        admin["teamId"],
        email="v@x.com",
        revoked_at=datetime.now(UTC) - timedelta(hours=1),
    )
    await invites_client.post(
        "/api/v1/invites/redeem", json={"token": redeemed_raw}, headers=_bearer(joiner["token"])
    )

    # full lifecycle list — one row per status bucket
    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/invites", headers=_bearer(admin["token"])
    )
    items = r.json()["items"]
    by_email = {i["email"]: i for i in items}
    assert set(by_email) == {"s@x.com", "r@x.com", "e@x.com", "v@x.com"}
    assert by_email["s@x.com"]["status"] == "sent"
    assert by_email["r@x.com"]["status"] == "redeemed"
    assert by_email["e@x.com"]["status"] == "expired"
    assert by_email["v@x.com"]["status"] == "revoked"

    # ?status= narrows to exactly one bucket; unknown values are rejected 422
    for want in ("sent", "redeemed", "expired", "revoked"):
        r = await invites_client.get(
            f"/api/v1/teams/{admin['teamId']}/invites?status={want}",
            headers=_bearer(admin["token"]),
        )
        items = r.json()["items"]
        assert items and all(i["status"] == want for i in items), want
    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/invites?status=bogus",
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 422


async def test_draft_status_derives_from_missing_sent_at(invites_factory, invites_client):
    """draft == not-yet-sent: derived purely from sent_at IS NULL."""
    admin = await _seed_user(invites_factory, "dr-admin@x.com", role="admin")
    row, _raw = await _insert_invite(
        invites_factory, admin["teamId"], email="d@x.com", sent_at=None
    )
    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/invites", headers=_bearer(admin["token"])
    )
    items = r.json()["items"]
    assert [i["id"] for i in items] == [row.id]
    assert all(i["status"] == "draft" for i in items)
    # ?status=draft narrows to exactly that bucket
    r = await invites_client.get(
        f"/api/v1/teams/{admin['teamId']}/invites?status=draft",
        headers=_bearer(admin["token"]),
    )
    assert [i["id"] for i in r.json()["items"]] == [row.id]


async def test_resend_revokes_old_token_and_issues_new_one(
    invites_factory, invites_client
):
    admin = await _seed_user(invites_factory, "rs-admin@x.com", role="admin")
    joiner = await _seed_user(invites_factory, "rs-join@y.com")
    invite, old_token = await _insert_invite(
        invites_factory, admin["teamId"], email="rs@x.com"
    )

    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites/{invite.id}/resend",
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    new_token = body["token"]

    # fresh token, same invite id/identity; old token never re-listed
    assert new_token != old_token
    assert len(new_token) >= 32
    assert body["id"] == invite.id
    assert body["email"] == "rs@x.com" and body["role"] == "engineer"
    assert body["status"] == "sent" and body["revokedAt"] is None

    # THE transition under test: the OLD token must now be dead — the row's
    # token_hash was replaced, so redemption answers honestly (unknown token
    # => 404; the security property is that it can NEVER join).
    r = await invites_client.post(
        "/api/v1/invites/redeem",
        json={"token": old_token},
        headers=_bearer(joiner["token"]),
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error"]["code"] == "INVITE_NOT_FOUND"

    # ...and the NEW token redeems into real membership.
    r = await invites_client.post(
        "/api/v1/invites/redeem",
        json={"token": new_token},
        headers=_bearer(joiner["token"]),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["membership"] == {"teamId": admin["teamId"], "role": "engineer"}
    assert out["alreadyMember"] is False

    # resend of a terminal invite answers honestly
    for status_code, code in ((409, "INVITE_ALREADY_REDEEMED"),):
        r = await invites_client.post(
            f"/api/v1/teams/{admin['teamId']}/invites/{invite.id}/resend",
            headers=_bearer(admin["token"]),
        )
        assert r.status_code == status_code, r.text
        assert r.json()["detail"]["error"]["code"] == code

    # unknown invite id -> NOT_FOUND even for admins of this team
    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites/nope/resend",
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 404


async def test_resend_rejected_when_revoked_or_expired(invites_factory, invites_client):
    admin = await _seed_user(invites_factory, "rx-admin@x.com", role="admin")
    rev, _ = await _insert_invite(
        invites_factory,
        admin["teamId"],
        email="rv@x.com",
        revoked_at=datetime.now(UTC),
    )
    exp, _ = await _insert_invite(
        invites_factory, admin["teamId"], email="ex@x.com", expires_in_days=-3
    )
    base = f"/api/v1/teams/{admin['teamId']}/invites"
    r = await invites_client.post(f"{base}/{rev.id}/resend", headers=_bearer(admin["token"]))
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "INVITE_REVOKED"
    r = await invites_client.post(f"{base}/{exp.id}/resend", headers=_bearer(admin["token"]))
    assert r.status_code == 410
    assert r.json()["detail"]["error"]["code"] == "INVITE_EXPIRED"


async def test_resend_is_admin_gated_like_other_mutations(
    invites_factory, invites_client
):
    admin = await _seed_user(invites_factory, "rg-admin@x.com", role="admin")
    engineer = await _seed_user(
        invites_factory, "rg-eng@x.com", role="engineer", team_id=admin["teamId"]
    )
    outsider = await _seed_user(invites_factory, "rg-out@z.com", role="admin")
    invite, _ = await _insert_invite(invites_factory, admin["teamId"])

    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites/{invite.id}/resend",
        headers=_bearer(engineer["token"]),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "FORBIDDEN"
    # foreign team admin: no-oracle law -> same NOT_FOUND an unknown team gets
    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites/{invite.id}/resend",
        headers=_bearer(outsider["token"]),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"
    # unauthenticated -> 401
    r = await invites_client.post(
        f"/api/v1/teams/{admin['teamId']}/invites/{invite.id}/resend"
    )
    assert r.status_code == 401
