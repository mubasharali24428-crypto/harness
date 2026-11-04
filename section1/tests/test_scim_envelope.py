"""The SCIM error body must survive the product's error envelope.

`make_app` installs the contract envelope on every route, and that envelope
stringifies any dict `detail` into `REQUEST_ERROR.message`. For our own API that
is the point; for `/scim/v2` it would be fatal — Okta/Entra parse the RFC 7644
error object, not our envelope, and a stringified body makes a refused
deprovision look like a transport failure.

These tests run the REAL app factory, not a scratch router, because the
envelope is exactly the thing under test.
"""

import secrets
from datetime import UTC, datetime

import pytest
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.crypto import KeyVault  # noqa: F401 — parity with other suites
from harness_identity.models import Base
from harness_identity.scim import ScimToken

MASTER_KEY = b"test-master-key-0123456789abcdef"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


@pytest.fixture
async def app_client():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = make_app(factory, Settings(master_key=MASTER_KEY, cors_origins=[]))
    raw = "hsc_" + secrets.token_urlsafe(24)
    async with factory() as db:
        db.add(
            ScimToken(
                id="00000000-0000-0000-0000-00000000sc01",
                team_id="00000000-0000-0000-0000-000000000001",
                label="envelope test",
                token_sha256=hash_token(raw),
                token_hint=raw[-4:],
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, {"Authorization": f"Bearer {raw}"}


async def test_scim_404_reaches_the_client_as_a_scim_error(app_client):
    client, headers = app_client
    r = await client.get("/scim/v2/Users/nonexistent-id", headers=headers)
    assert r.status_code == 404
    body = r.json()
    assert body["schemas"] == [ERROR_SCHEMA]
    assert body["status"] == "404"
    assert body["detail"] == "user not found"
    assert "error" not in body  # the envelope did not swallow it


async def test_scim_400_carries_scim_type(app_client):
    client, headers = app_client
    r = await client.get("/scim/v2/Users", params={"filter": 'emails co "x"'}, headers=headers)
    assert r.status_code == 400
    assert r.json()["scimType"] == "invalidFilter"


async def test_scim_401_for_a_bad_token_is_scim_too(app_client):
    client, _ = app_client
    r = await client.get("/scim/v2/Users", headers={"Authorization": "Bearer hsc_nope"})
    assert r.status_code == 401
    assert r.json()["schemas"] == [ERROR_SCHEMA]


async def test_the_product_envelope_still_wraps_api_v1(app_client):
    """The pass-through is scoped to SCIM bodies. Our own API must keep the
    full envelope — `error` PLUS the mirrored legacy `detail` — because dropping
    the mirror would be a silent wire-format change for every existing client."""
    client, _ = app_client
    r = await client.get("/api/v1/me")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"]
    assert body["detail"]["error"]["code"] == body["error"]["code"]


async def test_a_malformed_body_is_a_scim_400_not_a_422(app_client):
    """SCIM defines no 422 and connectors branch on `scimType`, so a body that
    fails schema validation leaves as the protocol's own bad-syntax error."""
    client, headers = app_client
    r = await client.request(
        "PATCH",
        "/scim/v2/Users/00000000-0000-0000-0000-000000000001",
        json={"Operations": "not-a-list"},
        headers=headers,
    )
    assert r.status_code == 400
    body = r.json()
    assert body["schemas"] == [ERROR_SCHEMA]
    assert body["status"] == "400"
    assert body["scimType"] == "invalidSyntax"


def test_the_pass_through_predicate_is_exact():
    """Scope guard for the guard itself. The pass-through must key on the SCIM
    marker and nothing else: the contract envelope's own
    `{"error": {...}}` body, and any other dict a route might raise, must keep
    being normalized. Without this, "any dict passes" is behaviourally identical
    under the routes we happen to have today — and a silent change waiting for
    the next dict-detail route to appear."""
    from harness_identity.envelope_middleware import _is_scim_error

    assert _is_scim_error(
        {"schemas": [ERROR_SCHEMA], "status": "404", "detail": "user not found"}
    )
    assert not _is_scim_error({"error": {"code": "X", "message": "y"}})
    assert not _is_scim_error({"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"]})
    assert not _is_scim_error("missing bearer token")
    assert not _is_scim_error(None)
