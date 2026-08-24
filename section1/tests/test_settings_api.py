"""M15-G6b — per-team reviewer-threshold settings API tests (S1 side).

Self-contained like test_rules_api.py (conftest is frozen): scratch app
mounting ONLY settings_api's router, sessions seeded straight into the store,
everything offline on aiosqlite.

Coverage: defaults-on-empty GET, admin PUT create/update read-back, strict
authz (only admin writes; engineer/viewer/guest read-only), malformed PUT →
422 VALIDATION_ERROR envelope with nothing persisted, unknown/foreign team →
identical 404 no-oracle, idempotent DELETE → back to defaults, audit rows for
reviewer_thresholds.saved / .deleted.
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
from harness_identity.settings_api import router

VALID_DOC = {
    "maxCriticalRiskFlags": 1,
    "maxHighRiskFlags": 2,
    "maxMediumRiskFlags": 5,
    "maxTotalRiskFlags": 20,
    "maxChangedFiles": 80,
    "maxDiffLines": 4000,
    "requireTestEvidence": False,
}


@pytest.fixture
async def settings_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def settings_client(settings_factory):
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = settings_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_team(factory) -> dict:
    """One team with admin+engineer+viewer+guest, plus a foreign admin."""
    async with factory() as db:
        team_a = Team(name="g6b-team-a")
        team_b = Team(name="g6b-team-b")
        db.add_all([team_a, team_b])
        await db.flush()
        memberships = [
            ("a-admin@x.com", "admin", team_a),
            ("a-eng@x.com", "engineer", team_a),
            ("a-view@x.com", "viewer", team_a),
            ("a-guest@x.com", "guest", team_a),
            ("b-admin@x.com", "admin", team_b),
        ]
        users, tokens = {}, {}
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

    def actor(email: str) -> dict:
        return {"userId": users[email].id, "token": tokens[email]}

    return {
        "teamId": team_a.id,
        "foreignTeamId": team_b.id,
        "admin": actor("a-admin@x.com"),
        "engineer": actor("a-eng@x.com"),
        "viewer": actor("a-view@x.com"),
        "guest": actor("a-guest@x.com"),
        "foreignAdmin": actor("b-admin@x.com"),
    }


def _url(env: dict, team_key: str = "teamId") -> str:
    return f"/api/v1/teams/{env[team_key]}/settings/reviewer-thresholds"


async def _audit_actions(factory, action: str) -> list[AuditLog]:
    async with factory() as db:
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )
        return list(rows)


async def test_get_defaults_when_never_configured(settings_client, settings_factory):
    env = await _seed_team(settings_factory)
    r = await settings_client.get(_url(env), headers=_bearer(env["admin"]["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["teamId"] == env["teamId"]
    assert body["thresholdsSource"] == "defaults"
    assert body["thresholds"]["maxCriticalRiskFlags"] == 0
    assert body["updatedAt"] is None


async def test_admin_put_creates_then_updates_readback(settings_client, settings_factory):
    env = await _seed_team(settings_factory)
    hdrs = _bearer(env["admin"]["token"])
    url = _url(env)

    r = await settings_client.put(url, json={"thresholds": VALID_DOC}, headers=hdrs)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thresholdsSource"] == "team"
    assert body["thresholds"] == VALID_DOC
    assert body["updatedBy"] == env["admin"]["userId"]

    updated = {**VALID_DOC, "maxMediumRiskFlags": 6}
    r = await settings_client.put(url, json={"thresholds": updated}, headers=hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["thresholds"]["maxMediumRiskFlags"] == 6

    saved = await _audit_actions(settings_factory, "reviewer_thresholds.saved")
    assert len(saved) == 2  # created=True then created=False


async def test_authz_only_admin_writes_everyone_reads(settings_client, settings_factory):
    env = await _seed_team(settings_factory)
    url = _url(env)

    # every role may READ (defaults when empty)
    for who in ("admin", "engineer", "viewer", "guest"):
        r = await settings_client.get(url, headers=_bearer(env[who]["token"]))
        assert r.status_code == 200, (who, r.text)

    # only admin may WRITE
    for who in ("engineer", "viewer", "guest"):
        r = await settings_client.put(
            url, json={"thresholds": VALID_DOC}, headers=_bearer(env[who]["token"])
        )
        assert r.status_code == 403, (who, r.text)
        body = r.json()
        code = (
            body.get("error", {}).get("code")
            or body.get("detail", {}).get("error", {}).get("code")
        )
        assert code == "FORBIDDEN"
        r = await settings_client.delete(url, headers=_bearer(env[who]["token"]))
        assert r.status_code == 403, who

    r = await settings_client.put(
        url, json={"thresholds": VALID_DOC}, headers=_bearer(env["admin"]["token"])
    )
    assert r.status_code == 200

    # unauthenticated never reads
    r = await settings_client.get(url)
    assert r.status_code == 401


async def test_malformed_put_422_and_nothing_persisted(settings_client, settings_factory):
    env = await _seed_team(settings_factory)
    hdrs = _bearer(env["admin"]["token"])
    url = _url(env)

    bad_docs = [
        {"maxCriticalRiskFlags": -1},                  # negative
        {"maxCriticalRiskFlags": "lots"},              # wrong type
        {"maxChangedFiles": 0},                        # below bound
        {"bogusKey": 3},                               # unknown key rejected
        {"maxTotalRiskFlags": True},                   # bool where int belongs
    ]
    for bad in bad_docs:
        r = await settings_client.put(url, json={"thresholds": bad}, headers=hdrs)
        assert r.status_code == 422, (bad, r.text)
        body = r.json()
        code = (
            body.get("error", {}).get("code")
            or body.get("detail", {}).get("error", {}).get("code")
        )
        assert code == "VALIDATION_ERROR"

    # nothing persisted by the rejected writes
    r = await settings_client.get(url, headers=hdrs)
    assert r.status_code == 200
    assert r.json()["thresholdsSource"] == "defaults"


async def test_unknown_and_foreign_team_404_no_oracle(settings_client, settings_factory):
    """Foreign ids answer the SAME 404 as bogus ones — no existence oracle.

    Note the foreign admin probes THEIR OWN team id (200, their data) vs OUR
    team id (404): from their seat ours is indistinguishable from a typo.
    """
    env = await _seed_team(settings_factory)
    foreign_hdrs = _bearer(env["foreignAdmin"]["token"])

    # foreign admin sees their own team fine
    r_own = await settings_client.get(
        _url(env, "foreignTeamId"), headers=foreign_hdrs
    )
    assert r_own.status_code == 200

    # ...but our team and a bogus id are indistinguishable 404s
    r_foreign = await settings_client.get(_url(env), headers=foreign_hdrs)
    r_bogus = await settings_client.get(
        "/api/v1/teams/does-not-exist/settings/reviewer-thresholds",
        headers=foreign_hdrs,
    )
    assert r_foreign.status_code == r_bogus.status_code == 404

    # writes follow the same rule for every verb
    r = await settings_client.put(
        _url(env), json={"thresholds": VALID_DOC}, headers=foreign_hdrs
    )
    assert r.status_code == 404
    r = await settings_client.delete(_url(env), headers=foreign_hdrs)
    assert r.status_code == 404


async def test_delete_resets_to_defaults_idempotent(settings_client, settings_factory):
    env = await _seed_team(settings_factory)
    hdrs = _bearer(env["admin"]["token"])
    url = _url(env)

    r = await settings_client.put(url, json={"thresholds": VALID_DOC}, headers=hdrs)
    assert r.status_code == 200

    r = await settings_client.delete(url, headers=hdrs)
    assert r.status_code == 204
    # idempotent: deleting when already gone stays 204
    r = await settings_client.delete(url, headers=hdrs)
    assert r.status_code == 204

    r = await settings_client.get(url, headers=hdrs)
    body = r.json()
    assert body["thresholdsSource"] == "defaults"
    assert body["thresholds"]["maxCriticalRiskFlags"] == 0

    deleted = await _audit_actions(settings_factory, "reviewer_thresholds.deleted")
    assert len(deleted) == 1  # silent idempotent delete emits nothing


async def test_partial_document_fills_missing_keys_with_defaults(
    settings_client, settings_factory
):
    """Tuning one knob must not require restating the whole document."""
    env = await _seed_team(settings_factory)
    hdrs = _bearer(env["admin"]["token"])
    r = await settings_client.put(
        _url(env),
        json={"thresholds": {"maxMediumRiskFlags": 7}},
        headers=hdrs,
    )
    assert r.status_code == 200, r.text
    thresholds = r.json()["thresholds"]
    assert thresholds["maxMediumRiskFlags"] == 7
    assert thresholds["maxCriticalRiskFlags"] == 0  # untouched knob keeps default
