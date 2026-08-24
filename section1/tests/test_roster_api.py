"""Mission 12 E3a — roster removal/role-change API tests (W2-C1 server side).

Self-contained by design (conftest.py is frozen): fixtures are local and the
router under test mounts on a SCRATCH FastAPI app exactly like the wave-9/10
suites — app.py registration is ATLAS's merge step and we must not touch that
file. Sessions are seeded straight into the store with contract-shaped token
hashes so no login route is needed. Everything runs offline on aiosqlite.

Coverage: happy paths (204 / 200 {userId, role}), authz matrix
(admin/engineer/viewer × both verbs), self-removal guard, last-admin guard on
both verbs, invalid-role 422, cross-team 404 no-oracle, idempotent-delete 404,
audit row assertions for member.removed/member.role_changed, and a no-op PATCH
staying silent.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.invites import router as invites_router
from harness_identity.models import (
    AuditLog,
    AuthSession,
    Base,
    Team,
    TeamMember,
    User,
)
from harness_identity.teams_api import router

# invites router supplies the roster LIST endpoint used as read-back oracle


# --------------------------------------------------------------- fixtures


@pytest.fixture
async def roster_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def roster_client(roster_factory):
    """Scratch app exposing ONLY this node's router; state mirrors what
    make_app() sets that our handlers read (session_factory)."""
    app = FastAPI()
    app.include_router(router)
    # invites router supplies the roster LIST endpoint used as read-back oracle
    app.include_router(invites_router)
    app.state.session_factory = roster_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_member(
    factory,
    team_id: str,
    email: str,
    *,
    role: str = "engineer",
) -> dict:
    """Create user + membership + session rows directly; return ids + bearer."""
    async with factory() as db:
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        member = TeamMember(
            team_id=team_id, user_id=user.id, display_name=user.name, role=role
        )
        db.add(member)
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
    return {
        "teamId": team_id,
        "userId": user.id,
        "memberId": member.id,
        "role": role,
        "token": token,
    }


async def _seed_team(factory, *emails_with_roles: tuple[str, str]) -> dict:
    """New team with the given (email, role) members; first member is the
    founding admin per login_or_register convention. Rows are inserted via the
    ORM so the Enum(name-storage) column semantics match production."""
    async with factory() as db:
        team = Team(name="roster-test-team")
        db.add(team)
        await db.flush()
        team_id = team.id
        users: dict[str, User] = {}
        members: dict[str, TeamMember] = {}
        tokens: dict[str, str] = {}
        for email, _role in emails_with_roles:
            user = User(email=email.lower(), name=email.split("@")[0])
            db.add(user)
            users[email] = user
        await db.flush()
        for email, role in emails_with_roles:
            member = TeamMember(
                team_id=team_id,
                user_id=users[email].id,
                display_name=users[email].name,
                role=role,
            )
            db.add(member)
            members[email] = member
            token = secrets.token_urlsafe(32)
            db.add(
                AuthSession(
                    token_hash=hash_token(token),
                    user_id=users[email].id,
                    team_id=team_id,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            tokens[email] = token
        await db.commit()
    out = {"teamId": team_id}
    for i, (email, role) in enumerate(emails_with_roles):
        if i == 0 and role != "admin":
            raise ValueError("founding member must be admin")
        out[email] = {
            "teamId": team_id,
            "userId": users[email].id,
            "memberId": members[email].id,
            "role": role,
            "token": tokens[email],
        }
    return out


async def _members_of(client, actor: dict) -> list[dict]:
    r = await client.get(
        f"/api/v1/teams/{actor['teamId']}/members", headers=_bearer(actor["token"])
    )
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _audit_rows(factory, action: str | None = None) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.seq)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    async with factory() as db:
        return list((await db.execute(stmt)).scalars().all())


# ------------------------------------------------------------ happy paths


async def test_delete_removes_membership_204_and_audits(
    roster_factory, roster_client
):
    team = await _seed_team(
        roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer")
    )
    admin, eng = team["boss@x.com"], team["eng@x.com"]

    r = await roster_client.delete(
        f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}",
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 204, r.text
    assert r.content == b""  # contract: empty body

    # membership really gone from the roster
    ids = [m["id"] for m in await _members_of(roster_client, admin)]
    assert eng["memberId"] not in ids
    assert admin["memberId"] in ids  # admin untouched


async def test_patch_role_returns_200_userid_role_and_persists(
    roster_factory, roster_client
):
    team = await _seed_team(
        roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer")
    )
    admin, eng = team["boss@x.com"], team["eng@x.com"]

    r = await roster_client.patch(
        f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}",
        json={"role": "viewer"},
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"userId": eng["userId"], "role": "viewer"}  # exact shape

    rows = await _members_of(roster_client, admin)
    persisted = next(m for m in rows if m["id"] == eng["memberId"])
    assert persisted["role"] == "viewer"

    # promote back up through engineer -> admin also works both directions
    r2 = await roster_client.patch(
        f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}",
        json={"role": "admin"},
        headers=_bearer(admin["token"]),
    )
    assert r2.status_code == 200
    assert r2.json()["role"] == "admin"


# ------------------------------------------------------- authz matrix


@pytest.mark.parametrize("verb", ["delete", "patch"])
@pytest.mark.parametrize("caller_role", ["admin", "engineer", "viewer"])
async def test_authz_matrix_admin_only(
    roster_factory, roster_client, caller_role, verb
):
    team = await _seed_team(
        roster_factory,
        ("boss@x.com", "admin"),
        (f"{caller_role}@x.com", caller_role),
        ("target@x.com", "engineer"),
    )
    caller = team[f"{caller_role}@x.com"]
    target = team["target@x.com"]
    url = f"/api/v1/teams/{team['teamId']}/members/{target['userId']}"

    if verb == "delete":
        r = await roster_client.delete(url, headers=_bearer(caller["token"]))
    else:
        r = await roster_client.patch(
            url, json={"role": "viewer"}, headers=_bearer(caller["token"])
        )

    if caller_role == "admin":
        expected = {  # target is an engineer: both verbs succeed
            "delete": 204,
            "patch": 200,
        }
        assert r.status_code == expected[verb], r.text
    else:
        assert r.status_code == 403, r.text
        err = r.json()["detail"]
        assert err["error"]["code"] == "FORBIDDEN"  # contract envelope
        # nothing was actually done by the forbidden caller
        rows = await _members_of(roster_client, team["boss@x.com"])
        match = next(m for m in rows if m["id"] == target["memberId"])
        assert match["role"] == "engineer"  # untouched


async def test_unauthenticated_calls_are_401(roster_factory, roster_client):
    team = await _seed_team(roster_factory, ("boss@x.com", "admin"))
    url = f"/api/v1/teams/{team['teamId']}/members/{team['boss@x.com']['userId']}"
    assert (
        await roster_client.delete(url)
    ).status_code == 401
    assert (
        await roster_client.patch(url, json={"role": "viewer"})
    ).status_code == 401


# ------------------------------------------------------- guards (409s)


async def test_cannot_remove_yourself_409_self_removal(
    roster_factory, roster_client
):
    team = await _seed_team(
        roster_factory, ("a@x.com", "admin"), ("b@x.com", "admin")
    )
    a = team["a@x.com"]

    r = await roster_client.delete(
        f"/api/v1/teams/{team['teamId']}/members/{a['userId']}",
        headers=_bearer(a["token"]),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"]["code"] == "SELF_REMOVAL"
    # still a member afterwards
    assert a["memberId"] in [
        m["id"] for m in await _members_of(roster_client, a)
    ]


async def test_removal_allowed_while_another_admin_remains(
    roster_factory, roster_client
):
    """Positive control for the guard: with TWO admins, removing one is legal
    (the survivor keeps the team admin-ed)."""
    team = await _seed_team(
        roster_factory, ("a@x.com", "admin"), ("b@x.com", "admin")
    )
    r = await roster_client.delete(
        f"/api/v1/teams/{team['teamId']}/members/{team['b@x.com']['userId']}",
        headers=_bearer(team["a@x.com"]["token"]),
    )
    assert r.status_code == 204, r.text
    # a remains as the only admin
    rows = await _members_of(roster_client, team["a@x.com"])
    remaining = [m for m in rows if m["memberType"] != "agent"]
    assert len(remaining) == 1


async def test_sole_admin_cannot_demote_themselves_409_last_admin(
    roster_factory, roster_client
):
    """The single admin trying to hand their own seat down would leave the
    team headless — blocked regardless of target==caller."""
    team = await _seed_team(
        roster_factory, ("only@x.com", "admin"), ("eng@x.com", "engineer")
    )
    only = team["only@x.com"]
    r = await roster_client.patch(
        f"/api/v1/teams/{team['teamId']}/members/{only['userId']}",
        json={"role": "engineer"},
        headers=_bearer(only["token"]),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"]["code"] == "LAST_ADMIN"
    # role untouched
    rows = await _members_of(roster_client, only)
    assert next(m for m in rows if m["id"] == only["memberId"])["role"] == "admin"


async def test_demotion_allowed_while_another_admin_remains(
    roster_factory, roster_client
):
    """Positive control: with TWO admins, demoting one to viewer is legal —
    the survivor keeps the team admin-ed."""
    team = await _seed_team(
        roster_factory, ("a@x.com", "admin"), ("b@x.com", "admin")
    )
    a, b = team["a@x.com"], team["b@x.com"]
    url = f"/api/v1/teams/{team['teamId']}/members/{b['userId']}"

    r = await roster_client.patch(
        url, json={"role": "viewer"}, headers=_bearer(a["token"])
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"userId": b["userId"], "role": "viewer"}
    rows = await _members_of(roster_client, a)
    roles = {m["id"]: m["role"] for m in rows}
    assert roles[a["memberId"]] == "admin"
    assert roles[b["memberId"]] == "viewer"


async def test_sole_admin_deleting_themselves_gets_last_admin_first(
    roster_factory, roster_client
):
    """Guard-order note pinned: LAST_ADMIN outranks SELF_REMOVAL when both
    would apply (sole admin deleting themselves)."""
    team = await _seed_team(roster_factory, ("only@x.com", "admin"))
    only = team["only@x.com"]
    r = await roster_client.delete(
        f"/api/v1/teams/{team['teamId']}/members/{only['userId']}",
        headers=_bearer(only["token"]),
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "LAST_ADMIN"


async def test_invalid_patch_role_is_422_validation_error(
    roster_factory, roster_client
):
    team = await _seed_team(
        roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer")
    )
    admin, eng = team["boss@x.com"], team["eng@x.com"]
    url = f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}"
    for bad in ("guest", "root", "", "ADMIN"):
        r = await roster_client.patch(
            url, json={"role": bad}, headers=_bearer(admin["token"])
        )
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"
    # ...and the membership survived all four attempts unchanged
    rows = await _members_of(roster_client, admin)
    assert next(m for m in rows if m["id"] == eng["memberId"])["role"] == "engineer"


# --------------------------------------------------- cross-team & idempotency


async def test_cross_team_access_is_404_no_oracle(roster_factory, roster_client):
    team_a = await _seed_team(
        roster_factory, ("aa@x.com", "admin"), ("am@x.com", "engineer")
    )
    team_b = await _seed_team(roster_factory, ("bb@y.com", "admin"))
    b_admin = team_b["bb@y.com"]
    a_member = team_a["am@x.com"]

    url = f"/api/v1/teams/{team_a['teamId']}/members/{a_member['userId']}"
    # DELETE of A's member by B's admin: identical answer to unknown id
    r = await roster_client.delete(url, headers=_bearer(b_admin["token"]))
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"

    r = await roster_client.patch(
        url, json={"role": "viewer"}, headers=_bearer(b_admin["token"])
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error"]["code"] == "NOT_FOUND"

    # unknown userId inside own team behaves identically (no oracle either way)
    ghost = "00000000-dead-beef-0000-000000000000"
    own = f"/api/v1/teams/{team_b['teamId']}/members/{ghost}"
    assert (
        await roster_client.delete(own, headers=_bearer(b_admin["token"]))
    ).status_code == 404
    assert (
        await roster_client.patch(
            own, json={"role": "viewer"}, headers=_bearer(b_admin["token"])
        )
    ).status_code == 404

    # A's membership was NOT touched by any of B's attempts
    rows = await _members_of(roster_client, team_a["aa@x.com"])
    assert a_member["memberId"] in [m["id"] for m in rows]


async def test_second_delete_after_removal_is_404(roster_factory, roster_client):
    """Idempotent-delete → 404 (hard delete, unlike api_keys soft revoke)."""
    team = await _seed_team(
        roster_factory, ("boss@x.com", "admin"), ("gone@x.com", "engineer")
    )
    admin, gone = team["boss@x.com"], team["gone@x.com"]
    url = f"/api/v1/teams/{team['teamId']}/members/{gone['userId']}"
    first = await roster_client.delete(url, headers=_bearer(admin["token"]))
    assert first.status_code == 204
    second = await roster_client.delete(url, headers=_bearer(admin["token"]))
    assert second.status_code == 404, second.text
    assert second.json()["detail"]["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------- audit trail


async def test_audit_rows_emitted_for_both_transitions(
    roster_factory, roster_client
):
    team = await _seed_team(
        roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer"),
        ("vw@x.com", "viewer"),
    )
    admin, eng = team["boss@x.com"], team["eng@x.com"]

    await roster_client.patch(
        f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}",
        json={"role": "viewer"},
        headers=_bearer(admin["token"]),
    )
    await roster_client.delete(
        f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}",
        headers=_bearer(admin["token"]),
    )

    changed = await _audit_rows(roster_factory, "member.role_changed")
    assert len(changed) == 1
    row = changed[0]
    assert row.target_type == "team_member"
    assert row.target_id == eng["memberId"]
    detail = dict(row.detail)
    assert detail.get("teamId") == team["teamId"]  # _emit stamps teamId
    assert detail.get("targetUserId") == eng["userId"]
    assert detail.get("fromRole") == "engineer"
    assert detail.get("toRole") == "viewer"
    assert row.actor_member_id == admin["memberId"]  # resolved FK, not NULL

    removed = await _audit_rows(roster_factory, "member.removed")
    assert len(removed) == 1
    row = removed[0]
    assert row.target_type == "team_member"
    assert row.target_id == eng["memberId"]
    detail = dict(row.detail)
    assert detail.get("removedUserId") == eng["userId"]
    assert detail.get("role") == "viewer"  # role at removal time
    assert row.actor_member_id == admin["memberId"]

    # rejected attempts wrote NO audit rows (guards fired before any write)
    assert [r.action for r in await _audit_rows(roster_factory)] == [
        "member.role_changed",
        "member.removed",
    ]


async def test_noop_patch_returns_200_but_stays_silent_in_audit(
    roster_factory, roster_client
):
    team = await _seed_team(
        roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer")
    )
    admin, eng = team["boss@x.com"], team["eng@x.com"]
    r = await roster_client.patch(
        f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}",
        json={"role": "engineer"},  # already engineer
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"userId": eng["userId"], "role": "engineer"}
    assert await _audit_rows(roster_factory, "member.role_changed") == []


async def test_new_actions_join_append_only_registry(roster_factory):
    from harness_identity.audit import APPEND_ONLY_ACTIONS

    assert {"member.removed", "member.role_changed"} <= APPEND_ONLY_ACTIONS


# ------------------------------------------- storage-semantics regression


async def test_role_column_round_trips_lowercase_values(
    roster_factory, roster_client
):
    """Pins the Enum(name-storage) trap this module documents: ORM writes must
    surface lowercase values back out of the DB-facing layer, and the
    count-other-admins filter must see admins written as value strings."""
    team = await _seed_team(
        roster_factory, ("boss@x.com", "admin"), ("eng@x.com", "engineer")
    )
    admin, eng = team["boss@x.com"], team["eng@x.com"]

    # demote the only other... wait, admin is sole admin: guard must hold
    r = await roster_client.patch(
        f"/api/v1/teams/{team['teamId']}/members/{eng['userId']}",
        json={"role": "admin"},  # promote engineer first
        headers=_bearer(admin["token"]),
    )
    assert r.status_code == 200
    # now two admins exist; demoting one must succeed (count filter sees both)
    r = await roster_client.patch(
        f"/api/v1/teams/{team['teamId']}/members/{admin['userId']}",
        json={"role": "engineer"},
        headers=_bearer(eng["token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "engineer"
