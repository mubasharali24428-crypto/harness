"""Part 38.005: the shared policy gate (contracts/policy_enforce.py) on the identity app.

Org policy binds every classified route of an org-linked team, additively to the legacy role
check; teams without an org are untouched; every identity route is classified."""

import re

import pytest
from harness_contracts.policy_enforce import EXEMPT, IDENTITY_RULES, classify
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from harness_identity import policy
from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import TeamMember, User

ORG = "org-gate"


@pytest.fixture
async def env(db):
    settings = Settings(
        master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
    )
    app = make_app(db, settings)
    async with db() as s:
        for ddl in (
            "CREATE TABLE IF NOT EXISTS organizations "
            "(id TEXT PRIMARY KEY, name TEXT, slug TEXT, status TEXT, created_at TEXT, "
            "require_dual_approval BOOLEAN DEFAULT FALSE)",
            "CREATE TABLE IF NOT EXISTS org_members (org_id TEXT, user_id TEXT, role TEXT, "
            "expires_at TEXT, created_at TEXT, removed_at TEXT)",
            "ALTER TABLE teams ADD COLUMN org_id VARCHAR(36)",
        ):
            await s.execute(text(ddl))
        await policy.ensure_schema(s)
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.app_, c.db = app, db
        yield c


async def login(c, name):
    r = await c.post("/api/v1/auth/login", json={"email": f"{name}@example.com"})
    assert r.status_code == 200, r.text
    j = r.json()
    return {"Authorization": f"Bearer {j['token']}"}, j["principal"]


async def put_doc(c, doc):
    async with c.db() as s:
        await s.execute(text("DELETE FROM policy_documents"))
        await s.execute(
            text(
                "INSERT INTO policy_documents (id, org_id, level, scope_id, version, doc,"
                " updated_by, updated_at) VALUES ('d1', :o, 'org', :o, 1, :d, 'u', "
                "'2026-01-01 00:00:00')"
            ),
            {"o": ORG, "d": doc},
        )
        await s.commit()


def deny(who, perms):
    import json

    rule = {"id": "r1", "effect": "deny", "permissions": perms, "subjects": [who]}
    return json.dumps({"rules": [rule]})


def test_every_identity_route_is_classified():
    app = make_app(None, Settings(master_key=b"x" * 32, cors_origins=[], require_ownership=False))
    paths = app.openapi()["paths"]
    assert len(paths) > 50  # guard against a vacuous walk (FastAPI nests included routers)
    missing = [
        f"{m.upper()} {p}"
        for p, ops in paths.items()
        for m in ops
        if m in {"get", "post", "put", "patch", "delete"}
        and classify(IDENTITY_RULES, m.upper(), re.sub(r"\{[^}]+\}", "abc123", p)) is None
    ]
    assert missing == []


def test_classification_examples():
    c = lambda m, p: classify(IDENTITY_RULES, m, p)  # noqa: E731
    assert c("GET", "/api/v1/audit/export") == "export"
    assert c("PUT", "/api/v1/billing/plan") == "billing"
    assert c("PATCH", "/api/v1/teams/t1/members/u1") == "administer"
    assert c("POST", "/api/v1/workspaces/w1/pause") == "steer"
    assert c("GET", "/api/v1/workspaces/w1/settings") == "read"
    assert c("POST", "/api/v1/auth/login") == EXEMPT
    assert c("GET", "/api/v1/no-such-route") is None


def test_rendering_preference_is_self_service_and_admin_for_others():
    c = lambda m, p: classify(IDENTITY_RULES, m, p)  # noqa: E731
    # X-07/X-D1: every role (guest included) may read and set its OWN rendering;
    # setting another member's rendering stays on the admin-only teams door.
    assert c("GET", "/api/v1/me/rendering") == "read"
    assert c("PUT", "/api/v1/me/rendering") == "read"
    assert c("PUT", "/api/v1/teams/t1/members/u1/rendering") == "administer"


async def test_unlinked_team_is_untouched(env):
    h, _ = await login(env, "solo")
    assert (await env.get("/api/v1/audit", headers=h)).status_code == 200


async def test_org_policy_denies_a_route_the_role_check_allows(env):
    h, p = await login(env, "alice")
    async with env.db() as s:
        await s.execute(
            text("UPDATE teams SET org_id=:o WHERE id=:t"), {"o": ORG, "t": p["teamId"]}
        )
        await s.execute(
            text("INSERT INTO org_members (org_id,user_id,role) VALUES (:o,:u,'owner')"),
            {"o": ORG, "u": p["userId"]},
        )
        await s.commit()
    assert (await env.get("/api/v1/audit", headers=h)).status_code == 200  # no policy yet
    await put_doc(env, deny(f"user:{p['userId']}", ["administer"]))
    r = await env.get("/api/v1/audit", headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "POLICY_DENIED"
    assert (await env.get("/api/v1/me", headers=h)).status_code == 200  # other permission
    async with env.db() as s:
        logged = (await s.execute(text(
            "SELECT decision, permission FROM policy_decision_log WHERE decision='deny'"
        ))).all()
        audited = (await s.execute(text(
            "SELECT count(*) FROM audit_log WHERE action='policy.denied'"
        ))).scalar_one()
    assert [tuple(x) for x in logged] == [("deny", "administer")] and audited == 1


async def test_non_org_member_of_a_linked_team_is_still_bound_by_org_deny(env):
    h, p = await login(env, "boss")
    async with env.db() as s:
        await s.execute(
            text("UPDATE teams SET org_id=:o WHERE id=:t"), {"o": ORG, "t": p["teamId"]}
        )
        v = User(email="viewer@example.com", name="viewer")
        s.add(v)
        await s.flush()
        s.add(TeamMember(team_id=p["teamId"], user_id=v.id, display_name="v", role="viewer"))
        await s.commit()
    await put_doc(env, deny("role:viewer", ["read"]))
    # a viewer session on that team
    from datetime import UTC, datetime, timedelta

    from harness_contracts import hash_token

    from harness_identity.models import AuthSession

    async with env.db() as s:
        s.add(AuthSession(token_hash=hash_token("viewer-token"), user_id=v.id, team_id=p["teamId"],
                          expires_at=datetime.now(UTC) + timedelta(hours=1)))
        await s.commit()
    r = await env.get("/api/v1/me", headers={"Authorization": "Bearer viewer-token"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "POLICY_DENIED"
    assert (await env.get("/api/v1/me", headers=h)).status_code == 200  # the admin is not a viewer


async def test_unauthenticated_requests_still_get_401_from_the_route(env):
    assert (await env.get("/api/v1/me")).status_code == 401
