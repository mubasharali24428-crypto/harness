"""Marathon 2.0 W12 — team skill catalog API tests (Section 1 side).

Self-contained by design (conftest.py is frozen): fixtures are local and the
router under test mounts on a SCRATCH FastAPI app exactly like the rules and
roster suites. Sessions are seeded straight into the store with
contract-shaped token hashes so no login route is needed; everything runs
offline on aiosqlite.

Coverage: draft create -> review -> enable/disable -> remove lifecycle with
read-back, the digest pin (golden vector shared with Section 4's loader),
authz matrix (engineer authors and toggles; only admins review and remove;
viewer/guest read-only; unauth 401), cross-team 404 no-oracle, name/version/
size validation with the exact 413 policy code, and audit rows for every
lifecycle action.
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
    Team,
    TeamMember,
    User,
)
from harness_identity.skills_api import MAX_INSTRUCTIONS_BYTES, router, skill_digest

# Golden vector shared with Section 4's skills_loader (byte-identical pin).
GOLDEN_DIGEST = "74e80712b547a590700d84dbf94dfd8c1ad61298eae19433ffd0a5e50492e93e"


# --------------------------------------------------------------- fixtures


@pytest.fixture
async def skills_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def skills_client(skills_factory):
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = skills_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_two_teams(factory) -> dict:
    async with factory() as db:
        team_a = Team(name="skills-team-a")
        team_b = Team(name="skills-team-b")
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
        "teamA": team_a.id,
        "teamB": team_b.id,
        "admin": _actor("a-admin@x.com"),
        "engineer": _actor("a-eng@x.com"),
        "viewer": _actor("a-view@x.com"),
        "guest": _actor("a-guest@x.com"),
        "foreignAdmin": _actor("b-admin@x.com"),
    }


async def _audit_actions(factory, action: str) -> list[AuditLog]:
    async with factory() as db:
        return list(
            (await db.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )


def _create_body(**overrides) -> dict:
    body = {
        "name": "deploy-guide",
        "version": "1.0.0",
        "description": "Deployment checklist",
        "instructions": "Run make static-gates before PR.",
        "source": "team-authored",
        "author": "mubashar",
    }
    body.update(overrides)
    return body


# ------------------------------------------------------------------- tests


def test_golden_digest_matches_section4():
    assert (
        skill_digest("deploy-guide", "1.0.0", "Deployment checklist",
                     "Run make static-gates before PR.", "team-authored",
                     "mubashar", "team-a")
        == GOLDEN_DIGEST
    )


async def test_lifecycle_draft_review_disable_enable_remove(skills_client, skills_factory):
    env = await _seed_two_teams(skills_factory)
    admin = _bearer(env["admin"]["token"])
    eng = _bearer(env["engineer"]["token"])
    url = f"/api/v1/teams/{env['teamA']}/skills"

    # draft create
    r = await skills_client.post(url, json=_create_body(), headers=eng)
    assert r.status_code == 201, r.text
    created = r.json()["skill"]
    assert created["status"] == "draft" and created["enabled"] is False
    # the pin is the canonical digest over THIS team's content
    expected_pin = skill_digest("deploy-guide", "1.0.0", "Deployment checklist",
                                "Run make static-gates before PR.", "team-authored",
                                "mubashar", env["teamA"])
    assert created["pinnedSha256"] == expected_pin
    assert created["createdBy"] == env["engineer"]["userId"]

    # draft is listed but never "reviewed" state
    r = await skills_client.get(url, headers=eng)
    assert r.status_code == 200
    listed = r.json()["skills"]
    assert [s["name"] for s in listed] == ["deploy-guide"]
    assert listed[0]["status"] == "draft"

    # draft cannot be enabled
    r = await skills_client.post(f"{url}/deploy-guide/enable", headers=eng)
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "NOT_REVIEWED"

    # only an admin reviews
    r = await skills_client.post(f"{url}/deploy-guide/review", headers=eng)
    assert r.status_code == 403
    r = await skills_client.post(f"{url}/deploy-guide/review", headers=admin)
    assert r.status_code == 200, r.text
    reviewed = r.json()["skill"]
    assert reviewed["status"] == "reviewed" and reviewed["enabled"] is True
    assert reviewed["reviewedBy"] == env["admin"]["userId"]
    assert reviewed["pinnedSha256"] == expected_pin  # the pin never moved

    # disable keeps the pin, flips the status
    r = await skills_client.post(f"{url}/deploy-guide/disable", headers=eng)
    assert r.status_code == 200, r.text
    disabled = r.json()["skill"]
    assert disabled["status"] == "disabled" and disabled["enabled"] is False
    assert disabled["pinnedSha256"] == expected_pin

    # enable returns to reviewed
    r = await skills_client.post(f"{url}/deploy-guide/enable", headers=eng)
    assert r.status_code == 200
    assert r.json()["skill"]["status"] == "reviewed"

    # removal is admin-only and idempotent
    r = await skills_client.delete(f"{url}/deploy-guide", headers=eng)
    assert r.status_code == 403
    r = await skills_client.delete(f"{url}/deploy-guide", headers=admin)
    assert r.status_code == 204
    r = await skills_client.delete(f"{url}/deploy-guide", headers=admin)
    assert r.status_code == 204  # idempotent
    r = await skills_client.get(url, headers=eng)
    assert r.json()["skills"] == []

    assert len(await _audit_actions(skills_factory, "skill.created")) == 1
    assert len(await _audit_actions(skills_factory, "skill.reviewed")) == 1
    assert len(await _audit_actions(skills_factory, "skill.disabled")) == 1
    assert len(await _audit_actions(skills_factory, "skill.enabled")) == 1
    assert len(await _audit_actions(skills_factory, "skill.removed")) == 1


async def test_authz_matrix_and_cross_team_no_oracle(skills_client, skills_factory):
    env = await _seed_two_teams(skills_factory)
    url_a = f"/api/v1/teams/{env['teamA']}/skills"
    url_b = f"/api/v1/teams/{env['teamB']}/skills"

    # foreign admin probing team A gets the identical 404 as an unknown team
    for path in (url_a, "/api/v1/teams/does-not-exist/skills"):
        r = await skills_client.get(path, headers=_bearer(env["foreignAdmin"]["token"]))
        assert r.status_code == 404
    r = await skills_client.post(url_a, json=_create_body(),
                                 headers=_bearer(env["foreignAdmin"]["token"]))
    assert r.status_code == 404
    r = await skills_client.delete(f"{url_a}/deploy-guide",
                                   headers=_bearer(env["foreignAdmin"]["token"]))
    assert r.status_code == 404

    # viewer/guest read their own team but cannot mutate
    for who in ("viewer", "guest"):
        hdrs = _bearer(env[who]["token"])
        expected = 200 if who == "viewer" else 403  # P-17: guests reach only the client portal
        assert (await skills_client.get(url_a, headers=hdrs)).status_code == expected
        assert (await skills_client.post(url_a, json=_create_body(),
                                         headers=hdrs)).status_code == 403

    # unauthenticated never reads
    assert (await skills_client.get(url_a)).status_code == 401

    # the foreign team's own catalog stays empty (no cross-team bleed)
    r = await skills_client.get(url_b, headers=_bearer(env["foreignAdmin"]["token"]))
    assert r.status_code == 200
    assert r.json()["skills"] == []


async def test_validation_duplicates_and_caps(skills_client, skills_factory):
    env = await _seed_two_teams(skills_factory)
    hdrs = _bearer(env["engineer"]["token"])
    url = f"/api/v1/teams/{env['teamA']}/skills"

    r = await skills_client.post(url, json=_create_body(name="Bad Name!"), headers=hdrs)
    assert r.status_code == 422 and r.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"
    r = await skills_client.post(url, json=_create_body(version="1.0"), headers=hdrs)
    assert r.status_code == 422
    r = await skills_client.post(url, json=_create_body(description=""), headers=hdrs)
    assert r.status_code == 422

    assert (await skills_client.post(url, json=_create_body(), headers=hdrs)).status_code == 201
    r = await skills_client.post(url, json=_create_body(), headers=hdrs)
    assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "NAME_TAKEN"

    # the authoritative byte cap: CJK text below the char bound but over 16KB
    # of UTF-8 answers the policy code, not a generic validation error
    cjk = "技" * (MAX_INSTRUCTIONS_BYTES // 3 + 2)
    r = await skills_client.post(
        url, json=_create_body(name="cjk-skill", instructions=cjk), headers=hdrs
    )
    assert r.status_code == 413 and r.json()["detail"]["error"]["code"] == "SKILL_TOO_LARGE"
