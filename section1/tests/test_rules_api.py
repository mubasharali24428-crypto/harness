"""Mission 12 G10 — user-authored project rules CRUD tests (S1 side).

Self-contained by design (conftest.py is frozen): fixtures are local and the
router under test mounts on a SCRATCH FastAPI app exactly like the roster
suite — app.py registration is ATLAS's merge step. Sessions are seeded
straight into the store with contract-shaped token hashes so no login route
is needed; everything runs offline on aiosqlite.

Coverage: happy paths (empty GET default, PUT create/update read-back,
DELETE → 204 → empty again), authz matrix (admin/engineer write, viewer/guest
read-only on GET + 403 on PUT/DELETE), 64KB over-cap rejection at the exact
boundary, unknown-project 404 no-oracle (identical answer for foreign team),
and audit rows for rules.saved / rules.deleted.
"""

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.models import (
    AuditLog,
    AuthSession,
    Base,
    Project,
    Team,
    TeamMember,
    User,
)
from harness_identity.rules_api import MAX_RULES_BYTES, router

# --------------------------------------------------------------- fixtures


@pytest.fixture
async def rules_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def rules_client(rules_factory):
    """Scratch app exposing ONLY this node's router; state mirrors what
    make_app() sets that our handlers read (session_factory)."""
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = rules_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_team_with_project(factory, *, project_team: str = "team-a") -> dict:
    """team-a with admin+engineer+viewer+guest and one project owned by
    ``project_team``'s team; plus a foreign admin (team-b) for no-oracle
    checks. Rows inserted via the ORM per roster-suite convention."""
    async with factory() as db:
        team_a = Team(name="rules-team-a")
        team_b = Team(name="rules-team-b")
        db.add_all([team_a, team_b])
        await db.flush()
        memberships = [
            ("a-admin@x.com", "admin", team_a),
            ("a-eng@x.com", "engineer", team_a),
            ("a-view@x.com", "viewer", team_a),
            ("a-guest@x.com", "guest", team_a),
            ("b-admin@x.com", "admin", team_b),
        ]
        users: dict[str, User] = {}
        tokens: dict[str, str] = {}
        for email, _role, _team in memberships:
            user = User(email=email, name=email.split("@")[0])
            db.add(user)
            users[email] = user
        await db.flush()
        for email, role, team in memberships:
            db.add(
                TeamMember(
                    team_id=team.id,
                    user_id=users[email].id,
                    display_name=users[email].name,
                    role=role,
                )
            )
        owner_team = team_a if project_team == "team-a" else team_b
        project = Project(team_id=owner_team.id, name="rules-repo")
        db.add(project)
        await db.flush()
        for email, _role, team in memberships:
            token = secrets.token_urlsafe(32)
            db.add(
                AuthSession(
                    token_hash=hash_token(token),
                    user_id=users[email].id,
                    team_id=team.id,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            tokens[email] = token
        await db.commit()

    def _actor(email: str) -> dict:
        return {"userId": users[email].id, "token": tokens[email]}

    return {
        "projectId": project.id,
        "admin": _actor("a-admin@x.com"),
        "engineer": _actor("a-eng@x.com"),
        "viewer": _actor("a-view@x.com"),
        "guest": _actor("a-guest@x.com"),
        "foreignAdmin": _actor("b-admin@x.com"),
    }


# ----------------------------------------------------------------- helpers


async def _audit_actions(factory, action: str) -> list[AuditLog]:
    async with factory() as db:
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )
        return list(rows)


# ------------------------------------------------------------------- tests


async def test_get_rules_defaults_to_empty(rules_client, rules_factory):
    env = await _seed_team_with_project(rules_factory)
    r = await rules_client.get(
        f"/api/v1/projects/{env['projectId']}/rules", headers=_bearer(env["admin"]["token"])
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projectId"] == env["projectId"]
    assert body["rulesText"] == ""
    assert body["updatedAt"] is None


async def test_put_creates_then_updates_readback(rules_client, rules_factory):
    env = await _seed_team_with_project(rules_factory)
    hdrs = _bearer(env["engineer"]["token"])
    url = f"/api/v1/projects/{env['projectId']}/rules"

    r = await rules_client.put(url, json={"rulesText": "Always use pnpm."},
                               headers=hdrs)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["rulesText"] == "Always use pnpm."
    assert first["updatedBy"] == env["engineer"]["userId"]
    assert first["updatedAt"] is not None

    # update path (same single row, not a second document)
    r = await rules_client.put(url, json={"rulesText": "Always use pnpm.\nNo any types."},
                               headers=hdrs)
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["rulesText"].endswith("No any types.")
    assert second["updatedBy"] == env["engineer"]["userId"]

    rows = await _audit_actions(rules_factory, "rules.saved")
    assert len(rows) == 2  # created=True then created=False


async def test_authz_matrix_write_gated_read_open(rules_client, rules_factory):
    env = await _seed_team_with_project(rules_factory)
    url = f"/api/v1/projects/{env['projectId']}/rules"

    # viewer/guest may READ (empty doc) but never WRITE; P-17 moves the
    # guest's reads to the client portal, so the team API read is 403 now.
    for who in ("viewer", "guest"):
        hdrs = _bearer(env[who]["token"])
        r = await rules_client.get(url, headers=hdrs)
        assert r.status_code == (200 if who == "viewer" else 403)
        r = await rules_client.put(url, json={"rulesText": "nope"}, headers=hdrs)
        assert r.status_code == 403
        r = await rules_client.delete(url, headers=hdrs)
        assert r.status_code == 403

    # admin + engineer may write
    for who in ("admin", "engineer"):
        r = await rules_client.put(url, json={"rulesText": f"rule by {who}"},
                                   headers=_bearer(env[who]["token"]))
        assert r.status_code == 200, r.text

    # unauthenticated never reads
    r = await rules_client.get(url)
    assert r.status_code == 401


async def test_over_cap_rejected_413(rules_client, rules_factory):
    env = await _seed_team_with_project(rules_factory)
    hdrs = _bearer(env["admin"]["token"])
    url = f"/api/v1/projects/{env['projectId']}/rules"

    exactly_cap = "a" * MAX_RULES_BYTES  # ASCII ⇒ chars == bytes == cap: allowed
    r = await rules_client.put(url, json={"rulesText": exactly_cap}, headers=hdrs)
    assert r.status_code == 200, r.text

    # one ASCII char past the byte cap → policy answer 413 RULES_TOO_LARGE
    r = await rules_client.put(url, json={"rulesText": "a" * (MAX_RULES_BYTES + 1)},
                               headers=hdrs)
    assert r.status_code == 413, r.text[:200]

    # byte-count authority: ~21k CJK chars ≈ 64KB+ utf-8 bytes yet far below
    # the char cap — still the policy 413, never silently stored
    cjk_over = "\u4e00" * ((MAX_RULES_BYTES // 3) + 1)
    assert len(cjk_over.encode("utf-8")) > MAX_RULES_BYTES
    r = await rules_client.put(url, json={"rulesText": cjk_over}, headers=hdrs)
    assert r.status_code == 413, r.text[:200]
    body = r.json()
    code = body.get("error", {}).get("code") or body.get("detail", {}).get("error", {}).get("code")
    assert code == "RULES_TOO_LARGE"

    # nothing was persisted by the rejected writes
    r = await rules_client.get(url, headers=hdrs)
    assert len(r.json()["rulesText"]) == MAX_RULES_BYTES


async def test_unknown_and_foreign_project_404_no_oracle(rules_client, rules_factory):
    env = await _seed_team_with_project(rules_factory)
    ours_url = f"/api/v1/projects/{env['projectId']}/rules"

    # foreign admin gets the SAME answer as a bogus id — no existence oracle
    r_foreign = await rules_client.get(ours_url, headers=_bearer(env["foreignAdmin"]["token"]))
    r_bogus = await rules_client.get("/api/v1/projects/does-not-exist/rules",
                                     headers=_bearer(env["foreignAdmin"]["token"]))
    assert r_foreign.status_code == r_bogus.status_code == 404

    # writes follow the same rule for every verb
    r = await rules_client.put(ours_url, json={"rulesText": "hi"},
                               headers=_bearer(env["foreignAdmin"]["token"]))
    assert r.status_code == 404
    r = await rules_client.delete(ours_url, headers=_bearer(env["foreignAdmin"]["token"]))
    assert r.status_code == 404


async def test_delete_idempotent_and_empty_again(rules_client, rules_factory):
    env = await _seed_team_with_project(rules_factory)
    hdrs = _bearer(env["admin"]["token"])
    url = f"/api/v1/projects/{env['projectId']}/rules"

    r = await rules_client.put(url, json={"rulesText": "temp rule"}, headers=hdrs)
    assert r.status_code == 200

    r = await rules_client.delete(url, headers=hdrs)
    assert r.status_code == 204
    # idempotent: deleting when already gone stays 204
    r = await rules_client.delete(url, headers=hdrs)
    assert r.status_code == 204

    r = await rules_client.get(url, headers=hdrs)
    assert r.json()["rulesText"] == ""

    saved = await _audit_actions(rules_factory, "rules.saved")
    deleted = await _audit_actions(rules_factory, "rules.deleted")
    assert len(saved) == 1
    assert len(deleted) == 1  # the silent idempotent delete emits nothing
