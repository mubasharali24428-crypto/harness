"""The egress law, pinned at the seam — one rule, both callers.

`egress.validate_public_https` is what every customer-supplied outbound
destination goes through (webhooks since Wave 9, SIEM collectors since W16). It
is asserted here twice on purpose: as a pure rule, and again through a real
route, because when the rule was extracted from `webhooks.py` nothing covered it
— `grep INVALID_WEBHOOK_URL section1/tests` returned zero hits. A refactor of a
security check with no test behind it is a rewrite.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.auth import hash_token
from harness_identity.config import Settings
from harness_identity.crypto import KeyVault
from harness_identity.egress import allowed_hosts, host_is_public, validate_public_https
from harness_identity.models import AuthSession, Base, Team, TeamMember, User
from harness_identity.webhooks import router as webhooks_router

GOOD = "https://hooks.example.com/a"
RULE = dict(code="X", label="thing")
ALLOWED = {"hooks.example.com", "siem.example.com"}


# ------------------------------------------------------------------ the rule


def test_https_and_default_port_only():
    assert validate_public_https("https://hooks.example.com/a", allowed=ALLOWED, **RULE) == GOOD
    for bad in (
        "http://hooks.example.com/a",
        "https://hooks.example.com:8443/a",
        "https://hooks.example.com/a?token=1",
        "https://hooks.example.com/a#frag",
        "https://user:pw@hooks.example.com/a",
        "https://not-allowed.example.com/a",
        "https://localhost/a",
        "https://anything.local/a",
        "https://127.0.0.1/a",
        "https://10.0.0.5/a",
        "https://169.254.169.254/latest",
        "https:///no-host",
        "not a url",
    ):
        with pytest.raises(HTTPException) as exc:
            validate_public_https(bad, allowed=ALLOWED, **RULE)
        assert exc.value.status_code == 422
        assert exc.value.detail["error"]["code"] == "X"


def test_allow_list_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_ALLOWED", "A.Example.COM., b.example.com ,")
    assert allowed_hosts("HARNESS_TEST_ALLOWED") == {"a.example.com", "b.example.com"}
    monkeypatch.setenv("HARNESS_TEST_ALLOWED", "")
    assert allowed_hosts("HARNESS_TEST_ALLOWED") == set()


def test_host_resolution_refuses_private_answers():
    assert host_is_public("127.0.0.1") is False
    assert host_is_public("10.1.2.3") is False
    assert host_is_public("no-such-host.invalid") is False


# --------------------------------------------------- the same rule, through a route


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("HARNESS_WEBHOOK_ALLOWED_HOSTS", "hooks.example.com")
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(id="u1", email="a@x.z", name="a"))
        s.add(Team(id="t1", name="A"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
        s.add(
            AuthSession(
                token_hash=hash_token("wtok"),
                user_id="u1",
                team_id="t1",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()
    app = FastAPI()
    app.include_router(webhooks_router)
    app.state.session_factory = factory
    app.state.vault = KeyVault(b"test-master-key-0123456789abcdef")
    app.state.settings = Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await engine.dispose()


async def test_webhook_routes_apply_the_shared_rule(client):
    """Same refusal, same code, reached through the pre-existing webhook API."""
    ok = await client.post(
        "/api/v1/teams/t1/settings/integrations/v1/webhooks",
        json={"endpointUrl": "https://hooks.example.com/a", "eventTypes": ["run.paused"]},
        headers={"Authorization": "Bearer wtok"},
    )
    assert ok.status_code == 201, ok.text
    for bad in ("http://hooks.example.com/a", "https://169.254.169.254/x"):
        r = await client.post(
            "/api/v1/teams/t1/settings/integrations/v1/webhooks",
            json={"endpointUrl": bad, "eventTypes": ["run.paused"]},
            headers={"Authorization": "Bearer wtok"},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["error"]["code"] == "INVALID_WEBHOOK_URL"
