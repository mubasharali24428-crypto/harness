"""Part 37 invariants (lane O): orgs, roles, invites, guests, service identities, revocation.

Scratch app mounting only the orgs router. Schema = ORM create_all plus the real migration
0038 SQL, so the migration file itself is under test (SQLite).
"""

import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.models import AuthSession, Base, Team, TeamMember, User
from harness_identity.orgs import mark_session_assurance, org_role, router

MIGRATION = Path(__file__).parents[1] / "migrations" / "0038_organizations.sql"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        sql = re.sub(r"--[^\n]*", "", MIGRATION.read_text())
        for stmt in filter(str.strip, sql.split(";")):
            await conn.execute(text(stmt))
        await conn.execute(text(
            "ALTER TABLE organizations ADD COLUMN require_dual_approval "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        await conn.execute(
            text("ALTER TABLE organizations ADD COLUMN event_retention_days INTEGER")
        )
        await conn.execute(
            text("ALTER TABLE organizations ADD COLUMN audit_retention_days INTEGER")
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def c(factory):
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        client.factory = factory
        yield client


async def user(factory, email, *, team_id=None, role="admin", ttl=timedelta(days=1)):
    async with factory() as db:
        if team_id is None:
            t = Team(name=f"t-{email}")
            db.add(t)
            await db.flush()
            team_id = t.id
        u = User(email=email, name=email.split("@")[0])
        db.add(u)
        await db.flush()
        db.add(TeamMember(team_id=team_id, user_id=u.id, display_name=u.name, role=role))
        tok = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(tok),
                user_id=u.id,
                team_id=team_id,
                expires_at=datetime.now(UTC) + ttl,
            )
        )
        await db.commit()
    return {"id": u.id, "team": team_id, "h": {"Authorization": f"Bearer {tok}"}, "tok": tok}


async def mk_org(c, owner, slug="acme"):
    r = await c.post("/api/v1/orgs", json={"name": slug, "slug": slug}, headers=owner["h"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def join(c, org, admin, joiner, role="member", **extra):
    r = await c.post(
        f"/api/v1/orgs/{org}/invites",
        json={"email": joiner["email"], "role": role, **extra},
        headers=admin["h"],
    )
    assert r.status_code == 201, r.text
    a = await c.post(
        "/api/v1/orgs/invites/accept", json={"token": r.json()["token"]}, headers=joiner["h"]
    )
    assert a.status_code == 200, a.text
    return a.json()


async def person(c, email):
    u = await user(c.factory, email)
    u["email"] = email
    return u


async def audit_actions(c, org, owner):
    h = (await c.get(f"/api/v1/orgs/{org}/history", headers=owner["h"])).json()["items"]
    return [i["action"] for i in h]


# ---- 002 multi-org, 003 roles


async def test_user_in_multiple_orgs_and_creator_is_owner(c):
    a = await person(c, "a@x.io")
    o1, o2 = await mk_org(c, a, "one"), await mk_org(c, a, "two")
    items = (await c.get("/api/v1/orgs", headers=a["h"])).json()["items"]
    assert {i["id"] for i in items} == {o1, o2} and {i["role"] for i in items} == {"owner"}
    async with c.factory() as db:
        assert await org_role(db, o1, a["id"]) == "owner"


async def test_cross_org_isolation_is_404(c):
    a, b = await person(c, "a@x.io"), await person(c, "b@y.io")
    oa = await mk_org(c, a, "orga")
    await mk_org(c, b, "orgb")
    for method, path in [
        ("get", f"/api/v1/orgs/{oa}"),
        ("get", f"/api/v1/orgs/{oa}/members"),
        ("get", f"/api/v1/orgs/{oa}/history"),
        ("get", f"/api/v1/orgs/{oa}/invites"),
        ("get", f"/api/v1/orgs/{oa}/service-identities"),
        ("delete", f"/api/v1/orgs/{oa}"),
    ]:
        r = await getattr(c, method)(path, headers=b["h"])
        assert r.status_code == 404, (path, r.status_code)
    r = await c.patch(
        f"/api/v1/orgs/{oa}/members/{a['id']}", json={"role": "member"}, headers=b["h"]
    )
    assert r.status_code == 404
    assert (await c.get("/api/v1/orgs/nope", headers=a["h"])).status_code == 404


async def test_unauthenticated_denied(c):
    assert (await c.get("/api/v1/orgs")).status_code == 401


async def test_escalation_attempts_denied(c):
    own, adm, mem = [await person(c, f"{n}@x.io") for n in ("o", "adm", "m")]
    org = await mk_org(c, own)
    await join(c, org, own, adm, "admin")
    await join(c, org, own, mem, "member")
    p = f"/api/v1/orgs/{org}/members"
    # member cannot manage anything
    assert (
        await c.patch(f"{p}/{mem['id']}", json={"role": "owner"}, headers=mem["h"])
    ).status_code == 403
    assert (
        await c.post(
            f"/api/v1/orgs/{org}/invites",
            json={"email": "z@x.io", "role": "member"},
            headers=mem["h"],
        )
    ).status_code == 403
    assert (await c.delete(f"{p}/{adm['id']}", headers=mem["h"])).status_code == 403
    # admin cannot make owners, touch owners, or remove owners
    assert (
        await c.patch(f"{p}/{mem['id']}", json={"role": "owner"}, headers=adm["h"])
    ).status_code == 403
    assert (
        await c.patch(f"{p}/{adm['id']}", json={"role": "owner"}, headers=adm["h"])
    ).status_code == 403
    assert (
        await c.patch(f"{p}/{own['id']}", json={"role": "member"}, headers=adm["h"])
    ).status_code == 403
    assert (await c.delete(f"{p}/{own['id']}", headers=adm["h"])).status_code == 403
    # invites can never mint owners
    r = await c.post(
        f"/api/v1/orgs/{org}/invites", json={"email": "z@x.io", "role": "owner"}, headers=own["h"]
    )
    assert r.status_code == 422
    # only owners flip SSO/MFA
    assert (
        await c.patch(f"/api/v1/orgs/{org}", json={"requireMfa": True}, headers=adm["h"])
    ).status_code == 403
    # only an owner can promote to owner
    ok = await c.patch(f"{p}/{mem['id']}", json={"role": "owner"}, headers=own["h"])
    assert ok.status_code == 200


async def test_last_owner_protection(c):
    own, other = await person(c, "o@x.io"), await person(c, "p@x.io")
    org = await mk_org(c, own)
    p = f"/api/v1/orgs/{org}/members"
    assert (
        await c.patch(f"{p}/{own['id']}", json={"role": "admin"}, headers=own["h"])
    ).status_code == 409
    assert (await c.delete(f"{p}/{own['id']}", headers=own["h"])).status_code == 409
    assert (await c.delete("/api/v1/orgs/me/identity", headers=own["h"])).status_code == 409
    await join(c, org, own, other, "member")
    await c.patch(f"{p}/{other['id']}", json={"role": "owner"}, headers=own["h"])
    assert (await c.delete(f"{p}/{own['id']}", headers=own["h"])).status_code == 204  # now fine
    assert (
        await c.patch(f"{p}/{other['id']}", json={"role": "member"}, headers=other["h"])
    ).status_code == 409


# ---- 004 invites, domain policy


async def test_invite_lifecycle_expiry_revoke_identity_binding(c):
    own, bob, eve = (
        await person(c, "o@x.io"),
        await person(c, "bob@x.io"),
        await person(c, "eve@x.io"),
    )
    org = await mk_org(c, own)
    inv = (
        await c.post(
            f"/api/v1/orgs/{org}/invites",
            json={"email": "BOB@x.io", "role": "member"},
            headers=own["h"],
        )
    ).json()
    body = {"token": inv["token"]}
    assert (
        await c.post("/api/v1/orgs/invites/accept", json=body, headers=eve["h"])
    ).status_code == 404
    assert (
        await c.post("/api/v1/orgs/invites/accept", json={"token": "x" * 20}, headers=bob["h"])
    ).status_code == 404
    assert (
        await c.post("/api/v1/orgs/invites/accept", json=body, headers=bob["h"])
    ).status_code == 200
    assert (
        await c.post("/api/v1/orgs/invites/accept", json=body, headers=bob["h"])
    ).status_code in (409,)
    # revoke
    inv2 = (
        await c.post(
            f"/api/v1/orgs/{org}/invites",
            json={"email": "eve@x.io", "role": "member"},
            headers=own["h"],
        )
    ).json()
    assert (
        await c.delete(f"/api/v1/orgs/{org}/invites/{inv2['id']}", headers=own["h"])
    ).status_code == 204
    assert (
        await c.post("/api/v1/orgs/invites/accept", json={"token": inv2["token"]}, headers=eve["h"])
    ).status_code == 410
    # expiry
    inv3 = (
        await c.post(
            f"/api/v1/orgs/{org}/invites",
            json={"email": "eve@x.io", "role": "member"},
            headers=own["h"],
        )
    ).json()
    async with c.factory() as db:
        await db.execute(
            text("UPDATE org_invites SET expires_at = :e WHERE id = :i"),
            {"e": datetime.now(UTC) - timedelta(seconds=1), "i": inv3["id"]},
        )
        await db.commit()
    assert (
        await c.post("/api/v1/orgs/invites/accept", json={"token": inv3["token"]}, headers=eve["h"])
    ).status_code == 410
    statuses = {
        i["id"]: i["status"]
        for i in (await c.get(f"/api/v1/orgs/{org}/invites", headers=own["h"])).json()["items"]
    }
    assert (
        statuses[inv["id"]] == "redeemed"
        and statuses[inv2["id"]] == "revoked"
        and statuses[inv3["id"]] == "expired"
    )
    assert "token" not in str((await c.get(f"/api/v1/orgs/{org}/invites", headers=own["h"])).json())


async def test_domain_policy_blocks_invite_and_accept(c):
    own, bob = await person(c, "o@corp.com"), await person(c, "bob@other.com")
    org = await mk_org(c, own)
    bad = await c.patch(
        f"/api/v1/orgs/{org}", json={"allowedDomains": ["not a domain"]}, headers=own["h"]
    )
    assert bad.status_code == 422
    assert (
        await c.patch(
            f"/api/v1/orgs/{org}", json={"allowedDomains": ["@Corp.com"]}, headers=own["h"]
        )
    ).json()["allowedDomains"] == ["corp.com"]
    r = await c.post(
        f"/api/v1/orgs/{org}/invites",
        json={"email": "bob@other.com", "role": "member"},
        headers=own["h"],
    )
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "DOMAIN_NOT_ALLOWED"
    # policy tightened after the invite was minted: accept re-checks
    await c.patch(f"/api/v1/orgs/{org}", json={"allowedDomains": []}, headers=own["h"])
    t = (
        await c.post(
            f"/api/v1/orgs/{org}/invites",
            json={"email": "bob@other.com", "role": "member"},
            headers=own["h"],
        )
    ).json()["token"]
    await c.patch(f"/api/v1/orgs/{org}", json={"allowedDomains": ["corp.com"]}, headers=own["h"])
    assert (
        await c.post("/api/v1/orgs/invites/accept", json={"token": t}, headers=bob["h"])
    ).status_code == 403
    # guests are outside the domain policy
    g = await c.post(
        f"/api/v1/orgs/{org}/invites",
        json={"email": "bob@other.com", "role": "guest"},
        headers=own["h"],
    )
    assert g.status_code == 201


# ---- 005 session expiry / sign-out


async def test_expired_and_signed_out_sessions_fail(c, factory):
    expired = await user(factory, "e@x.io", ttl=timedelta(seconds=-5))
    assert (await c.get("/api/v1/orgs", headers=expired["h"])).status_code == 401
    live = await person(c, "l@x.io")
    assert (await c.get("/api/v1/orgs", headers=live["h"])).status_code == 200
    async with factory() as db:  # what POST /auth/logout does: delete the presenting session
        await db.execute(
            text("DELETE FROM auth_sessions WHERE token_hash = :h"), {"h": hash_token(live["tok"])}
        )
        await db.commit()
    assert (await c.get("/api/v1/orgs", headers=live["h"])).status_code == 401


# ---- 006 SSO/MFA flags


async def test_require_sso_and_mfa_enforced_with_owner_break_glass(c, factory):
    own, mem = await person(c, "o@x.io"), await person(c, "m@x.io")
    org = await mk_org(c, own)
    await join(c, org, own, mem, "member")
    assert (
        await c.patch(f"/api/v1/orgs/{org}", json={"requireSso": True}, headers=own["h"])
    ).status_code == 200
    r = await c.get(f"/api/v1/orgs/{org}/members", headers=mem["h"])
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "SSO_REQUIRED"
    async with factory() as db:
        await mark_session_assurance(db, hash_token(mem["tok"]), sso=True)
    assert (await c.get(f"/api/v1/orgs/{org}/members", headers=mem["h"])).status_code == 200
    await c.patch(
        f"/api/v1/orgs/{org}", json={"requireMfa": True}, headers=own["h"]
    )  # owner session lacks assurance but may flip flags
    r = await c.get(f"/api/v1/orgs/{org}/members", headers=mem["h"])
    assert r.json()["detail"]["error"]["code"] == "MFA_REQUIRED"
    assert (
        await c.patch(
            f"/api/v1/orgs/{org}", json={"requireSso": False, "requireMfa": False}, headers=own["h"]
        )
    ).status_code == 200
    assert (await c.get(f"/api/v1/orgs/{org}/members", headers=mem["h"])).status_code == 200


# ---- 007 history


async def test_history_has_before_after_and_is_manager_only(c):
    own, mem = await person(c, "o@x.io"), await person(c, "m@x.io")
    org = await mk_org(c, own)
    await join(c, org, own, mem, "member")
    await c.patch(
        f"/api/v1/orgs/{org}/members/{mem['id']}", json={"role": "billing_admin"}, headers=own["h"]
    )
    await c.delete(f"/api/v1/orgs/{org}/members/{mem['id']}", headers=own["h"])
    items = (await c.get(f"/api/v1/orgs/{org}/history", headers=own["h"])).json()["items"]
    by = {i["action"]: i for i in items}
    ch = by["org.member_role_changed"]["detail"]
    assert ch["before"]["role"] == "member" and ch["after"]["role"] == "billing_admin"
    assert by["org.member_role_changed"]["actorUserId"] == own["id"]
    assert by["org.member_removed"]["detail"]["before"] == {"role": "billing_admin"}
    assert {"org.created", "org.member_added", "invite.minted", "invite.redeemed"} <= set(by)
    assert all(i["detail"]["orgId"] == org for i in items)
    bm = await person(c, "bm@x.io")
    await join(c, org, own, bm, "billing_admin")
    assert (await c.get(f"/api/v1/orgs/{org}/history", headers=bm["h"])).status_code == 403


# ---- 008 guests


async def test_guest_expiry_enforced_audited_and_revokes(c, factory):
    own = await person(c, "o@x.io")
    org = await mk_org(c, own)
    g = await person(c, "g@ext.io")
    res = await join(c, org, own, g, "guest", guestDays=1)
    assert res["expiresAt"]
    p = f"/api/v1/orgs/{org}"
    assert (await c.get(p, headers=g["h"])).status_code == 200
    assert (await c.get(f"{p}/members", headers=g["h"])).status_code == 403  # guest boundary
    assert (
        await c.post(f"{p}/invites", json={"email": "q@ext.io", "role": "member"}, headers=g["h"])
    ).status_code == 403
    async with factory() as db:
        await db.execute(
            text("UPDATE org_members SET expires_at = :e WHERE user_id = :u"),
            {"e": datetime.now(UTC) - timedelta(seconds=1), "u": g["id"]},
        )
        await db.commit()
    assert (await c.get(p, headers=g["h"])).status_code == 404
    assert (await c.get("/api/v1/orgs", headers=g["h"])).json()["items"] == []
    acts = await audit_actions(c, org, own)
    assert acts.count("org.guest_expired") == 1
    await c.get(p, headers=g["h"])
    assert (await audit_actions(c, org, own)).count("org.guest_expired") == 1  # exactly once
    assert "org.guest_added" in acts


async def test_guest_role_requires_expiry_and_non_guest_has_none(c):
    own, m = await person(c, "o@x.io"), await person(c, "m@x.io")
    org = await mk_org(c, own)
    await join(c, org, own, m, "member")
    r = await c.patch(
        f"/api/v1/orgs/{org}/members/{m['id']}", json={"role": "guest"}, headers=own["h"]
    )
    assert r.json()["expiresAt"] is not None
    r = await c.patch(
        f"/api/v1/orgs/{org}/members/{m['id']}", json={"role": "member"}, headers=own["h"]
    )
    assert r.json()["expiresAt"] is None
    big = await c.post(
        f"/api/v1/orgs/{org}/invites",
        json={"email": "q@x.io", "role": "guest", "guestDays": 9999},
        headers=own["h"],
    )
    assert big.status_code == 422


# ---- 009 service identities


async def test_service_identity_lifecycle(c, factory):
    own, mem = await person(c, "o@x.io"), await person(c, "m@x.io")
    org = await mk_org(c, own)
    await join(c, org, own, mem, "member")
    sp = f"/api/v1/orgs/{org}/service-identities"
    assert (
        await c.post(sp, json={"name": "ci", "scopes": ["runs:read"]}, headers=mem["h"])
    ).status_code == 403
    for bad in (["org:admin"], ["owner"], ["*"], ["runs:read", "admin"]):
        assert (
            await c.post(sp, json={"name": "ci", "scopes": bad}, headers=own["h"])
        ).status_code == 422
    r = await c.post(
        sp, json={"name": "ci", "scopes": ["runs:write", "runs:read"]}, headers=own["h"]
    )
    assert r.status_code == 201
    tok, sid = r.json()["token"], r.json()["id"]
    assert tok.startswith("hsi_")
    listed = (await c.get(sp, headers=own["h"])).json()["items"][0]
    assert (
        "token" not in listed
        and listed["lastUsedAt"] is None
        and listed["scopes"] == ["runs:read", "runs:write"]
    )
    async with factory() as db:
        stored = (
            await db.execute(text("SELECT token_hash FROM org_service_identities"))
        ).scalar_one()
    assert stored == hash_token(tok) and tok not in stored
    w = await c.get("/api/v1/orgs/service/whoami", headers={"Authorization": f"Bearer {tok}"})
    assert w.status_code == 200 and w.json()["orgId"] == org
    assert (await c.get(sp, headers=own["h"])).json()["items"][0]["lastUsedAt"] is not None
    # a service token cannot drive org management (no human session), and holds no org role
    assert (
        await c.get(f"/api/v1/orgs/{org}/members", headers={"Authorization": f"Bearer {tok}"})
    ).status_code == 401
    async with factory() as db:
        assert await org_role(db, org, sid) is None
    assert (await c.delete(f"{sp}/{sid}", headers=own["h"])).status_code == 204
    assert (
        await c.get("/api/v1/orgs/service/whoami", headers={"Authorization": f"Bearer {tok}"})
    ).status_code == 401
    assert (await c.get("/api/v1/orgs/service/whoami")).status_code == 401
    other = await person(c, "z@z.io")
    assert (await c.delete(f"{sp}/{sid}", headers=other["h"])).status_code == 404


async def test_service_identity_expiry(c, factory):
    own = await person(c, "o@x.io")
    org = await mk_org(c, own)
    r = (
        await c.post(
            f"/api/v1/orgs/{org}/service-identities",
            json={"name": "x", "scopes": ["runs:read"], "expiresInDays": 1},
            headers=own["h"],
        )
    ).json()
    async with factory() as db:
        await db.execute(
            text("UPDATE org_service_identities SET expires_at = :e"),
            {"e": datetime.now(UTC) - timedelta(seconds=1)},
        )
        await db.commit()
    assert (
        await c.get(
            "/api/v1/orgs/service/whoami", headers={"Authorization": f"Bearer {r['token']}"}
        )
    ).status_code == 401


# ---- 010 revocation timing


async def test_removal_revokes_sessions_and_service_identities_on_next_request(c, factory):
    own = await person(c, "o@x.io")
    org = await mk_org(c, own)
    await c.post(f"/api/v1/orgs/{org}/teams", json={"teamId": own["team"]}, headers=own["h"])
    team_mate = await user(factory, "tm@x.io", team_id=own["team"], role="engineer")
    team_mate["email"] = "tm@x.io"
    await join(c, org, own, team_mate, "admin")
    sid = await c.post(
        f"/api/v1/orgs/{org}/service-identities",
        json={"name": "bot", "scopes": ["runs:read"]},
        headers=team_mate["h"],
    )
    stok = sid.json()["token"]
    assert (await c.get("/api/v1/orgs", headers=team_mate["h"])).status_code == 200
    assert (
        await c.get("/api/v1/orgs/service/whoami", headers={"Authorization": f"Bearer {stok}"})
    ).status_code == 200
    assert (
        await c.delete(f"/api/v1/orgs/{org}/members/{team_mate['id']}", headers=own["h"])
    ).status_code == 204
    assert (await c.get("/api/v1/orgs", headers=team_mate["h"])).status_code == 401  # session gone
    assert (
        await c.get("/api/v1/orgs/service/whoami", headers={"Authorization": f"Bearer {stok}"})
    ).status_code == 401
    async with factory() as db:
        assert await org_role(db, org, team_mate["id"]) is None
        left = (
            await db.execute(
                text("SELECT COUNT(*) FROM team_members WHERE user_id = :u"), {"u": team_mate["id"]}
            )
        ).scalar_one()
    assert left == 0


async def test_team_departure_revokes_team_sessions_only(c, factory):
    own = await person(c, "o@x.io")
    org = await mk_org(c, own)
    await c.post(f"/api/v1/orgs/{org}/teams", json={"teamId": own["team"]}, headers=own["h"])
    tm = await user(factory, "tm@x.io", team_id=own["team"], role="engineer")
    r = await c.delete(
        f"/api/v1/orgs/{org}/teams/{own['team']}/members/{tm['id']}", headers=own["h"]
    )
    assert r.status_code == 204
    assert (await c.get("/api/v1/orgs", headers=tm["h"])).status_code == 401
    assert (
        await c.delete(f"/api/v1/orgs/{org}/teams/nope/members/{tm['id']}", headers=own["h"])
    ).status_code == 404


async def test_leave_org_self_and_multi_org_session_survives_elsewhere(c):
    own, m = await person(c, "o@x.io"), await person(c, "m@x.io")
    o1 = await mk_org(c, own, "one")
    await join(c, o1, own, m, "member")
    o2 = await mk_org(c, m, "two")
    assert (
        await c.delete(f"/api/v1/orgs/{o1}/members/{m['id']}", headers=m["h"])
    ).status_code == 204
    assert (await c.get(f"/api/v1/orgs/{o1}", headers=m["h"])).status_code == 404
    assert (
        await c.get(f"/api/v1/orgs/{o2}", headers=m["h"])
    ).status_code == 200  # other org unaffected
    assert (
        await c.delete(f"/api/v1/orgs/{o1}/members/{m['id']}", headers=m["h"])
    ).status_code == 404


async def test_identity_deletion_revokes_everything(c, factory):
    own, m = await person(c, "o@x.io"), await person(c, "m@x.io")
    org = await mk_org(c, own)
    await join(c, org, own, m, "admin")
    sid = (
        await c.post(
            f"/api/v1/orgs/{org}/service-identities",
            json={"name": "b", "scopes": ["runs:read"]},
            headers=m["h"],
        )
    ).json()
    assert (await c.delete("/api/v1/orgs/me/identity", headers=m["h"])).status_code == 204
    assert (await c.get("/api/v1/orgs", headers=m["h"])).status_code == 401
    assert (
        await c.get(
            "/api/v1/orgs/service/whoami", headers={"Authorization": f"Bearer {sid['token']}"}
        )
    ).status_code == 401
    async with factory() as db:
        assert (
            await db.execute(text("SELECT is_active FROM users WHERE id = :u"), {"u": m["id"]})
        ).scalar_one() in (0, False)
    assert "org.member_removed" in await audit_actions(c, org, own)


async def test_org_deletion_revokes_all(c, factory):
    own = await person(c, "o@x.io")
    org = await mk_org(c, own)
    await c.post(f"/api/v1/orgs/{org}/teams", json={"teamId": own["team"]}, headers=own["h"])
    mem = await user(factory, "mm@x.io", team_id=own["team"], role="engineer")
    mem["email"] = "mm@x.io"
    await join(c, org, own, mem, "member")
    sid = (
        await c.post(
            f"/api/v1/orgs/{org}/service-identities",
            json={"name": "b", "scopes": ["runs:read"]},
            headers=own["h"],
        )
    ).json()
    assert (await c.delete(f"/api/v1/orgs/{org}", headers=mem["h"])).status_code == 403
    assert (await c.delete(f"/api/v1/orgs/{org}", headers=own["h"])).status_code == 204
    assert (
        await c.get(f"/api/v1/orgs/{org}", headers=own["h"])
    ).status_code == 401  # own team session died too
    assert (await c.get("/api/v1/orgs", headers=mem["h"])).status_code == 401
    assert (
        await c.get(
            "/api/v1/orgs/service/whoami", headers={"Authorization": f"Bearer {sid['token']}"}
        )
    ).status_code == 401


# ---- teams link


async def test_link_team_requires_own_admin_team_and_single_org(c):
    own, other = await person(c, "o@x.io"), await person(c, "p@x.io")
    org, org2 = await mk_org(c, own, "one"), await mk_org(c, other, "two")
    assert (
        await c.post(f"/api/v1/orgs/{org}/teams", json={"teamId": other["team"]}, headers=own["h"])
    ).status_code == 404
    assert (
        await c.post(f"/api/v1/orgs/{org}/teams", json={"teamId": own["team"]}, headers=own["h"])
    ).status_code == 201
    assert (
        await c.post(f"/api/v1/orgs/{org2}/teams", json={"teamId": own["team"]}, headers=other["h"])
    ).status_code == 404
    again = await c.post(
        f"/api/v1/orgs/{org}/teams", json={"teamId": own["team"]}, headers=own["h"]
    )
    assert again.status_code == 409


async def test_slug_unique_and_validated(c):
    a = await person(c, "a@x.io")
    await mk_org(c, a, "same")
    r = await c.post("/api/v1/orgs", json={"name": "x", "slug": "same"}, headers=a["h"])
    assert r.status_code == 409
    assert (
        await c.post("/api/v1/orgs", json={"name": "x", "slug": "Bad Slug"}, headers=a["h"])
    ).status_code == 422


async def test_sso_session_mint_records_sso_assurance(factory):
    """The SSO completion path marks its session so an org's require_sso can be satisfied."""
    from harness_contracts import Principal

    from harness_identity.sso import _mint_session

    u = await user(factory, "sso@x.io")
    principal = Principal(userId=u["id"], teamId=u["team"], role="admin")
    raw = await _mint_session(factory, principal)
    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT sso, mfa FROM org_session_assurance WHERE token_hash = :h"),
                {"h": hash_token(raw)},
            )
        ).first()
    assert row is not None and bool(row.sso) is True
