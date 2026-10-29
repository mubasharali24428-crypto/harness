"""W16 slice 1 — a revoked seat is revoked everywhere.

`users.is_active` used to be a hint: it changed what notifications targeted
(`notifier.py:660`, `notifications.py:934`) and nothing else. `login_or_register`
looked the user up by email with no `is_active` term, `get_principal` checked only
session expiry and membership, and the SSO email mapping skipped it while its own
docstring promised "an ACTIVE human member". So a deactivated account could
request a fresh code and mint a new session, and any session that outlived the
revocation kept acting.

These tests pin the surfaces that must now agree, because an enterprise answer to
"can my directory take a seat away?" depends on all of them:

1. an existing bearer token dies the moment the seat is revoked;
2. no new session can be minted — the OTP round-trip refuses;
3. the legacy direct exchange refuses the same account;
4. SSO email-mapping distinguishes "not a member" from "member, but disabled";
5. the rule is `is_active`, not the clock — an unexpired session still dies;
6. the socket handshake and a pre-authorized WS ticket obey it too;
7. a section OTHER than identity refuses through the shared dependency (proved in
   `section11/tests/test_api.py`, which runs the real contract code).

This codebase resolves a bearer token five ways — Section 1's `get_principal`,
the shared `contracts.make_bearer_dep` (sections 2/8/11, the deployed runners, the
extensions server), the WS handshake, the WS ticket redemption, and the SSO email
mapping — so changing one of them changes nothing.

Deliberately NOT changed: `issue_otp` stays enumeration-safe and dumb. It writes
the code and answers 200 whether or not the address exists, so a disabled account
still receives a code it cannot use — the refusal belongs at verification, where
the caller has already proven inbox control and can be told something actionable.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.auth import get_principal, login_or_register
from harness_identity.config import Settings
from harness_identity.models import Base, User
from harness_identity.sso import MAP_DISABLED, MAP_NO_MEMBER, MAP_OK, _map_email_to_member

MASTER = b"test-master-key-0123456789abcdef"

pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _run(loop, coro):
    return loop.run_until_complete(coro)


@pytest.fixture
def stack():
    """One loop for the async DB helpers and the TestClient's app engine: two
    loops would put the aiosqlite connections on a loop the client never pumps."""
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    loop = asyncio.new_event_loop()
    try:

        async def _create():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                # `ws_tickets` is created by migration 0005 (+0025's fingerprint
                # column), not by the ORM metadata, so create_all leaves it out.
                # The ticket door under test is exactly this table.
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS ws_tickets ("
                        "token_hash VARCHAR(64) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,"
                        " team_id VARCHAR(36) NOT NULL, role VARCHAR(20) NOT NULL,"
                        " project_id VARCHAR(36) NOT NULL, expires_at TEXT NOT NULL,"
                        " created_at TEXT NOT NULL, fingerprint VARCHAR(32))"
                    )
                )

        _run(loop, _create())
        app = make_app(
            factory, Settings(master_key=MASTER, cors_origins=[], require_ownership=True)
        )
        sent: list[tuple[str, str]] = []
        app.state.notifier = lambda email, code: sent.append((email, code))
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            yield client, factory, sent, loop
    finally:
        _run(loop, engine.dispose())
        loop.close()


def _signed_in(client, sent, email: str) -> tuple[str, str]:
    """Verified OTP round-trip; returns (token, userId)."""
    asked = client.post("/api/v1/auth/request-login", json={"email": email})
    assert asked.status_code == 200, asked.text
    verified = client.post(
        "/api/v1/auth/verify-otp", json={"email": email, "code": sent[-1][1]}
    )
    assert verified.status_code == 200, verified.text
    body = verified.json()
    return body["token"], body["principal"]["userId"]


def _deactivate(loop, factory, user_id: str) -> None:
    """Exactly what the store says after an org removal or an erasure."""

    async def _go():
        async with factory() as db:
            await db.execute(
                text("UPDATE users SET is_active = false WHERE id = :u"), {"u": user_id}
            )
            await db.commit()

    _run(loop, _go())


def _team_of(loop, factory, user_id: str) -> str:
    async def _go():
        async with factory() as db:
            row = (
                await db.execute(
                    text("SELECT team_id FROM team_members WHERE user_id = :u LIMIT 1"),
                    {"u": user_id},
                )
            ).first()
            return row[0] if row else ""

    return _run(loop, _go())


# --------------------------------------------------------------- surface 1 + 5


def test_a_revoked_seat_loses_its_existing_session(stack):
    client, factory, sent, loop = stack
    token, user_id = _signed_in(client, sent, "seat@example.com")
    assert client.get("/api/v1/me", headers=_bearer(token)).status_code == 200

    _deactivate(loop, factory, user_id)

    dead = client.get("/api/v1/me", headers=_bearer(token))
    assert dead.status_code == 401, dead.text
    # the no-oracle shape is preserved: a revoked seat is indistinguishable from
    # an expired session, so a live token cannot be probed for account status
    assert "invalid or expired session" in dead.text
    # and a write is refused too, not just the identity read
    refused = client.post("/api/v1/projects", headers=_bearer(token), json={"name": "x"})
    assert refused.status_code == 401, refused.text


def test_the_rule_is_the_seat_not_the_clock(stack):
    """A token whose expiry was pushed to 2099 still dies: `is_active` decides,
    so revocation cannot be undone by clock skew or a long TTL."""
    client, factory, sent, loop = stack
    token, user_id = _signed_in(client, sent, "skew@example.com")

    async def _extend_and_disable():
        async with factory() as db:
            await db.execute(
                text("UPDATE auth_sessions SET expires_at = :t WHERE user_id = :u"),
                {"t": datetime.now(UTC).replace(year=2099), "u": user_id},
            )
            await db.execute(
                text("UPDATE users SET is_active = false WHERE id = :u"), {"u": user_id}
            )
            await db.commit()

    _run(loop, _extend_and_disable())
    assert client.get("/api/v1/me", headers=_bearer(token)).status_code == 401


# ------------------------------------------------------------------ surface 2


def test_a_revoked_seat_cannot_mint_a_new_session(stack):
    client, factory, sent, loop = stack
    _token, user_id = _signed_in(client, sent, "seat2@example.com")
    _deactivate(loop, factory, user_id)

    asked = client.post("/api/v1/auth/request-login", json={"email": "seat2@example.com"})
    assert asked.status_code == 200, asked.text  # enumeration-safety unchanged
    refused = client.post(
        "/api/v1/auth/verify-otp", json={"email": "seat2@example.com", "code": sent[-1][1]}
    )
    assert refused.status_code == 403, refused.text
    detail = refused.json()["detail"]
    assert (detail.get("error") or {}).get("code") == "ACCOUNT_DISABLED"
    # the message is actionable for the person who owns the inbox
    assert "deactivated" in refused.text.lower()


# ------------------------------------------------------------------ surface 3


def _asgi_request(app, token: str) -> Request:
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "app": app,
    }
    return Request(scope)


def test_get_principal_is_the_single_source_of_seat_truth(stack):
    """A token minted while active must not survive revocation even when no
    `auth_sessions` row was deleted — defence in depth beside `_revoke_access`."""
    client, factory, sent, loop = stack
    token, user_id = _signed_in(client, sent, "depth@example.com")

    async def _who():
        return await get_principal(_asgi_request(client.app, token))

    assert _run(loop, _who()).userId == user_id

    _deactivate(loop, factory, user_id)
    with pytest.raises(HTTPException) as excinfo:
        _run(loop, _who())
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "invalid or expired session"


def test_the_legacy_direct_exchange_refuses_a_disabled_account(stack):
    """POST /auth/login shares `login_or_register` with the OTP flow, so the
    refusal cannot live only in the OTP route."""
    _client, factory, _sent, loop = stack

    async def _go():
        async with factory() as db:
            db.add(
                User(id="u-disabled", email="direct@example.com", name="D", is_active=False)
            )
            await db.commit()
        with pytest.raises(HTTPException) as excinfo:
            await login_or_register(factory(), "direct@example.com")
        assert excinfo.value.status_code == 403
        assert "ACCOUNT_DISABLED" in str(excinfo.value.detail)
        # a stranger is still provisioned: the refusal is specific to disabled rows
        token, principal = await login_or_register(factory(), "fresh@example.com")
        assert token and principal.userId

    _run(loop, _go())


# ------------------------------------------------------------------ surface 4


# ------------------------------------------------------------------ surface 5


def test_a_revoked_seat_cannot_open_the_socket(stack):
    """The WebSocket handshake resolves its own way (`_principal_from_token`),
    so it needs the same rule. A seat revoked after a token was minted must not
    stream a project's events."""
    from harness_identity.app import _principal_from_token

    client, factory, sent, loop = stack
    token, user_id = _signed_in(client, sent, "ws@example.com")
    assert _run(loop, _principal_from_token(factory, token)).userId == user_id

    _deactivate(loop, factory, user_id)
    assert _run(loop, _principal_from_token(factory, token)) is None


def test_a_revoked_seat_cannot_redeem_its_ws_ticket(stack):
    """A ticket is a pre-authorized handle carrying a role snapshot; its short
    TTL is not a revocation. Redemptions after the seat dies answer nothing."""
    from harness_contracts import Principal, Role

    from harness_identity.ephemeral import mint_ws_ticket, redeem_ws_ticket

    client, factory, sent, loop = stack
    _token, user_id = _signed_in(client, sent, "ticket@example.com")
    team_id = _team_of(loop, factory, user_id)
    project_id = "p-ticket"

    async def _mint_and_redeem():
        ticket, _ttl = await mint_ws_ticket(
            factory, Principal(userId=user_id, teamId=team_id, role=Role.ADMIN), project_id
        )
        return await redeem_ws_ticket(factory, ticket, project_id)

    assert _run(loop, _mint_and_redeem()).principal.userId == user_id

    _deactivate(loop, factory, user_id)
    assert _run(loop, _mint_and_redeem()) is None


def test_sso_mapping_distinguishes_disabled_from_stranger(stack):
    """The SSO contract promised an ACTIVE member; the query never asked. A
    disabled member must read differently from someone who never joined."""
    client, factory, sent, loop = stack
    _token, user_id = _signed_in(client, sent, "sso@example.com")
    team_id = _team_of(loop, factory, user_id)
    assert team_id, "the OTP flow provisions a solo team"

    async def _map(email: str):
        return await _map_email_to_member(factory, team_id, email)

    mapping = _run(loop, _map("sso@example.com"))
    assert mapping.outcome == MAP_OK and mapping.principal.userId == user_id

    _deactivate(loop, factory, user_id)
    mapping = _run(loop, _map("sso@example.com"))
    assert mapping.outcome == MAP_DISABLED and mapping.principal is None

    async def _active_stranger():
        async with factory() as db:
            db.add(User(id="u-stranger", email="stranger@example.com", name="S"))
            await db.commit()

    _run(loop, _active_stranger())
    mapping = _run(loop, _map("stranger@example.com"))
    assert mapping.outcome == MAP_NO_MEMBER and mapping.principal is None
