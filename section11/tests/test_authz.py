"""A2-S1 authz proofs: project ownership, fail-closed, no-oracle 404s.

The route layer must prove projects.team_id == principal.teamId BEFORE any
work. These tests pin every branch of that contract:

* owned      -> work proceeds
* unknown    -> 404, body byte-identical to the foreign-team 404 (no oracle)
* foreign    -> same 404 (a cross-team probe learns nothing)
* unverifiable (no projects table / no factory) -> 403 AUTHZ_UNAVAILABLE
* unauthenticated -> 401 before ANY project lookup happens
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from conftest import (
    _AUTH_DDL_MEMBERS,
    _AUTH_DDL_SESSIONS,
    AUTH_HEADERS,
    TEST_PRINCIPAL,
    TEST_TOKEN,
    seed_project,
)
from harness_contracts.roles import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_context.authz import (
    REASON_LOOKUP_FAILED,
    REASON_UNWIRED,
    AuthzUnavailableError,
    ProjectAccessDenied,
    authorize_project,
)


def _principal(team: str = "team-x1") -> Principal:
    return Principal(userId="u-x1", teamId=team, role="admin")


async def _search(client, project_id: str, headers: dict | None = None):
    return await client.post(
        "/api/v1/context/search",
        json={"query": "x", "projectId": project_id},
        headers=AUTH_HEADERS if headers is None else headers,
    )


# --------------------------------------------------------------- unit level


async def test_owned_project_passes(db):
    await seed_project(db, "p-mine")
    assert await authorize_project(db, _principal(), "p-mine") is None


async def test_unknown_project_denied(db):
    with pytest.raises(ProjectAccessDenied):
        await authorize_project(db, _principal(), "p-nope")


async def test_foreign_team_project_denied(db):
    await seed_project(db, "p-foreign", team_id="team-other")
    with pytest.raises(ProjectAccessDenied):
        await authorize_project(db, _principal(), "p-foreign")


async def test_unwired_factory_fails_closed():
    with pytest.raises(AuthzUnavailableError) as ei:
        await authorize_project(None, _principal(), "p-mine")
    assert ei.value.reason == REASON_UNWIRED


async def test_lookup_trouble_fails_closed():
    """No projects table at all -> typed unavailable, never a silent allow."""
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with pytest.raises(AuthzUnavailableError) as ei:
            await authorize_project(factory, _principal(), "p-mine")
        assert ei.value.reason == REASON_LOOKUP_FAILED
    finally:
        await engine.dispose()


# --------------------------------------------------------------- HTTP level


async def test_owned_project_search_resolves(client, db):
    await seed_project(db, "p-mine")
    res = await _search(client, "p-mine")
    assert res.status_code == 200
    assert res.json()["results"] == []


async def test_unknown_and_foreign_answer_identically(client, db):
    """The no-oracle property: both 404s are the exact same body."""
    await seed_project(db, "p-foreign", team_id="team-other")
    foreign = await _search(client, "p-foreign")
    unknown = await _search(client, "p-unknown")

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
    assert foreign.json()["detail"]["error"]["code"] == "NOT_FOUND"
    # Neither the id nor the owning team leaks through the denial text.
    assert "p-foreign" not in foreign.text
    assert "team-other" not in foreign.text


async def test_unauthenticated_is_401_before_any_project_lookup(client, db):
    """No bearer -> 401 even for an OWNED project (authn precedes authz)."""
    await seed_project(db, "p-mine")
    res = await _search(client, "p-mine", headers={})
    assert res.status_code == 401


async def test_forged_bearer_is_401_not_404(client, db):
    """A bad token must not be able to probe project existence either."""
    await seed_project(db, "p-mine")
    res = await _search(
        client, "p-mine", headers={"Authorization": "Bearer forged-token"}
    )
    assert res.status_code == 401


async def test_unverifiable_authz_answers_403():
    """App DB has auth tables but NO projects table: 403 AUTHZ_UNAVAILABLE.

    Fail-closed proof at HTTP level: the request is well-authenticated (token
    resolves through the real bearer dep) but the ownership check itself
    cannot run — the answer is a denial, never a pass-through.
    """
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(text(_AUTH_DDL_SESSIONS))
        await conn.execute(text(_AUTH_DDL_MEMBERS))
        await conn.execute(
            text(
                "INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at) "
                "VALUES (:h, :u, :t, :e)"
            ),
            {
                "h": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
                "u": TEST_PRINCIPAL["userId"],
                "t": TEST_PRINCIPAL["teamId"],
                "e": datetime.now(UTC) + timedelta(hours=1),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO team_members (user_id, team_id, role) VALUES (:u, :t, :r)"
            ),
            {
                "u": TEST_PRINCIPAL["userId"],
                "t": TEST_PRINCIPAL["teamId"],
                "r": TEST_PRINCIPAL["role"],
            },
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from httpx import ASGITransport, AsyncClient

    from harness_context.api import make_app
    from harness_context.config import Settings
    from harness_context.embeddings import HashingEmbedding

    app = make_app(
        factory,
        Settings(embedding_backend="hashing", cors_origins=[]),
        backend=HashingEmbedding(dims=256),
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            res = await c.post(
                "/api/v1/context/search",
                json={"query": "x", "projectId": "p-any"},
                headers=AUTH_HEADERS,
            )
    finally:
        await engine.dispose()

    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "AUTHZ_UNAVAILABLE"
