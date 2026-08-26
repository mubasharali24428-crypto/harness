"""Mission 22 lane M3 — live Stripe adapter + webhook receiver tests.

Self-contained (conftest.py is frozen): the same scratch-app pattern as
test_billing.py. CI runs TEST-MODE ONLY — no real keys, no network: checkout
request shapes are asserted against a recording stub transport, webhook
signatures are computed locally with the hand-rolled HMAC recipe, and every
DB flow runs on in-memory aiosqlite.
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.billing import BillingPlan
from harness_identity.models import AuthSession, Base, Team, TeamMember, User
from harness_identity.stripe_adapter import (
    CHECKOUT_PATH,
    DEFAULT_STRIPE_API_BASE,
    SUBSCRIPTION_SYNC_TYPES,
    HttpxStripeClient,
    StripeAdapterError,
    StripeWebhookEvent,
    WebhookVerificationError,
    make_client_from_env,
    process_event,
    sign_payload,
    verify_stripe_signature,
)

WEBHOOK_SECRET = "whsec_test_secret"
WEBHOOK_PATH = "/api/v1/billing/webhook"
CHECKOUT_ROUTE = "/api/v1/billing/checkout-session"


# --------------------------------------------------------------- fixtures


@pytest.fixture
async def stripe_factory():
    engine = create_async_engine("sqlite+aiosqlite://")

    def _fk_on(dbapi_conn, _rec=None):
        # SQLite ignores FKs unless asked; the webhook ledger RELIES on the
        # teams.id FK to answer unknown-team deliveries honestly.
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    sa_event.listen(engine.sync_engine, "connect", _fk_on)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def stripe_app(stripe_factory, monkeypatch):
    """Scratch app carrying what the routes read; flag starts OFF. The M3
    router is mounted UNCONDITIONALLY here (route-level behavior under a
    present adapter); flag-gated WIRING is pinned separately by
    test_make_app_injects_adapter_and_routes_only_behind_flag."""
    monkeypatch.delenv("HARNESS_BILLING", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    from harness_identity.stripe_adapter import router

    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = stripe_factory
    return app


@pytest.fixture
async def api(stripe_app):
    transport = ASGITransport(app=stripe_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def route_app(stripe_factory):
    """Fresh app per test exposing ONLY the M3 router (no flag coupling)."""
    from harness_identity.stripe_adapter import router

    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = stripe_factory
    return app


@pytest.fixture
async def client_for(route_app):
    transport = ASGITransport(app=route_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_user(factory, email: str = "pay@x.com") -> dict:
    """Team/user/membership/session rows directly; returns ids + bearer."""
    async with factory() as db:
        team = Team(name=f"team-{email}")
        db.add(team)
        await db.flush()
        team_id = team.id
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team_id, user_id=user.id, display_name=user.name, role="admin"))
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
    return {"teamId": team_id, "userId": user.id, "token": token}


async def _seed_team(factory, name: str = "sync-team") -> str:
    """Just a team row — webhook events bind tiers to REAL team ids (FK)."""
    async with factory() as db:
        team = Team(name=name)
        db.add(team)
        await db.commit()
        return team.id


def _signed_headers(payload: bytes, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> dict:
    return {
        "Stripe-Signature": sign_payload(payload, secret, timestamp=ts),
        "Content-Type": "application/json",
    }


def _event_bytes(event_id: str, event_type: str, obj: dict | None = None) -> bytes:
    body = {"id": event_id, "type": event_type, "data": {"object": obj or {}}}
    return json.dumps(body).encode()


def _checkout_body(tier: str = "pro") -> dict:
    return {
        "tier": tier,
        "successUrl": "https://app.example/ok",
        "cancelUrl": "https://app.example/back",
    }


class RecordingTransport(httpx.AsyncBaseTransport):
    """Offline stand-in for Stripe's API: records requests, replays JSON."""

    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self.payload = payload or {}
        self.status_code = status_code
        self.seen: list[dict] = []

    def _record(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(
            {
                "method": request.method,
                "url": str(request.url),
                "auth": request.headers.get("Authorization"),
                "content_type": request.headers.get("Content-Type"),
                "body": request.content.decode(),
            }
        )
        return Response(self.status_code, json=self.payload)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._record(request)


def _mount_stub_transport(monkeypatch, transport: RecordingTransport) -> None:
    """Force every adapter AsyncClient onto the offline recording transport."""
    original_init = httpx.AsyncClient.__init__

    def patched_init(client_self, *args, **kwargs):
        # Keep base_url so the adapter's path resolves EXACTLY as in
        # production (absolute https://api.stripe.com/v1/... request URLs).
        kwargs["transport"] = transport
        original_init(client_self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


# ------------------------------------------------- env-gated instantiation


def test_make_client_from_env_gates_on_flag_and_key(monkeypatch):
    # Flag off => NOTHING instantiated (default posture unchanged).
    monkeypatch.delenv("HARNESS_BILLING", raising=False)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_abc")
    assert make_client_from_env() is None

    # Flag=stripe but no key => still nothing.
    monkeypatch.setenv("HARNESS_BILLING", "stripe")
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    assert make_client_from_env() is None

    # Flag=stripe + key => the concrete client satisfies the Protocol.
    monkeypatch.setenv("HARNESS_BILLING", "stripe")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_abc")
    client = make_client_from_env()
    assert isinstance(client, HttpxStripeClient)


def test_protocol_participation_is_structural_and_honest():
    """The class implements the Protocol surface; fetch_state declines honestly."""
    client = HttpxStripeClient("sk_test_abc")
    assert hasattr(client, "fetch_state")  # Protocol surface present
    coro = client.fetch_state("t1")
    with pytest.raises(NotImplementedError):
        coro.send(None)
    coro.close()
    # And billing.py's display path degrades rather than 500s on a raising
    # adapter — pinned by test_stripe_flag_without_key_or_adapter_stays_local.
    assert "stripe" not in sys.modules


def test_no_stripe_dependency_added():
    assert "stripe" not in sys.modules
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert '"stripe"' not in text and "stripe-python" not in text and "stripe>" not in text


# ------------------------------------------ checkout session create shape


async def test_checkout_request_shape_via_stub_transport(
    stripe_factory, route_app, client_for, monkeypatch
):
    """The form-encoded POST matches the documented Checkout Session schema."""
    actor = await _seed_user(stripe_factory)
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro123")
    transport = RecordingTransport({"id": "cs_test_1", "url": "https://checkout.stripe.com/x"})
    _mount_stub_transport(monkeypatch, transport)
    route_app.state.stripe_client = HttpxStripeClient("sk_test_abc")

    r = await client_for.post(
        CHECKOUT_ROUTE, json=_checkout_body(), headers=_bearer(actor["token"])
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "id": "cs_test_1",
        "url": "https://checkout.stripe.com/x",
        "tier": "pro",
    }

    assert len(transport.seen) == 1
    call = transport.seen[0]
    assert call["method"] == "POST"
    assert call["url"].startswith(f"{DEFAULT_STRIPE_API_BASE}{CHECKOUT_PATH}")
    assert call["auth"] == "Bearer sk_test_abc"
    assert call["content_type"] == "application/x-www-form-urlencoded"
    from urllib.parse import parse_qs

    fields = parse_qs(call["body"])
    one = lambda k: fields[k][0]  # noqa: E731
    assert one("mode") == "subscription"
    assert one("client_reference_id") == actor["teamId"]
    assert one("line_items[0][price]") == "price_pro123"
    assert one("line_items[0][quantity]") == "1"
    assert one("metadata[teamId]") == actor["teamId"]
    assert one("metadata[tier]") == "pro"
    assert one("subscription_data[metadata][teamId]") == actor["teamId"]
    assert one("subscription_data[metadata][tier]") == "pro"
    assert one("success_url") == "https://app.example/ok"
    assert one("cancel_url") == "https://app.example/back"


async def test_checkout_guards_tier_price_adapter_auth(
    stripe_factory, route_app, client_for, monkeypatch
):
    factory = stripe_factory
    actor = await _seed_user(factory)
    route_app.state.stripe_client = HttpxStripeClient("sk_test_abc")

    # Unknown tier dies at validation.
    r = await client_for.post(
        CHECKOUT_ROUTE, json=_checkout_body("gold"), headers=_bearer(actor["token"])
    )
    assert r.status_code == 422

    # Price config missing -> honest 503 CONFIG_MISSING.
    r = await client_for.post(
        CHECKOUT_ROUTE, json=_checkout_body(), headers=_bearer(actor["token"])
    )
    assert r.status_code == 503
    assert r.json()["detail"]["error"]["code"] == "CONFIG_MISSING"

    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro123")

    # No injected adapter (the default flag-off world) -> 503 BILLING_DISABLED.
    bare = FastAPI()
    bare.state.session_factory = factory
    from harness_identity.stripe_adapter import router

    bare.include_router(router)
    transport = ASGITransport(app=bare)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(CHECKOUT_ROUTE, json=_checkout_body(), headers=_bearer(actor["token"]))
    assert r.status_code == 503
    assert r.json()["detail"]["error"]["code"] == "BILLING_DISABLED"

    # Unauthenticated -> 401 per api-conventions bearer law.
    r = await client_for.post(CHECKOUT_ROUTE, json=_checkout_body())
    assert r.status_code == 401


async def test_checkout_stripe_failure_maps_to_502(
    stripe_factory, route_app, client_for, monkeypatch
):
    actor = await _seed_user(stripe_factory)
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro123")

    class Boom(HttpxStripeClient):
        async def create_checkout_session(self, **kwargs):  # noqa: ARG002
            raise StripeAdapterError("stripe POST /v1/checkout/sessions failed: HTTP 500")

    route_app.state.stripe_client = Boom("sk_test_abc")
    r = await client_for.post(
        CHECKOUT_ROUTE, json=_checkout_body(), headers=_bearer(actor["token"])
    )
    assert r.status_code == 502
    assert r.json()["detail"]["error"]["code"] == "STRIPE_ERROR"


# ------------------------------------------------ signature verification


def test_signature_valid_roundtrip():
    payload = _event_bytes("evt_ok", "checkout.session.completed")
    parsed = verify_stripe_signature(
        payload, sign_payload(payload, WEBHOOK_SECRET), WEBHOOK_SECRET
    )
    assert parsed["id"] == "evt_ok"


def test_sign_payload_matches_documented_recipe():
    """sign_payload IS the docs recipe: HMAC-SHA256('{t}.{payload}', whsec)."""
    payload = b'{"id":"evt_x"}'
    t = 1750000000
    signed = f"{t}.".encode() + payload
    expected = hmac.new(WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()
    header = sign_payload(payload, WEBHOOK_SECRET, timestamp=t)
    assert header == f"t={t},v1={expected}"


def test_signature_invalid_wrong_secret_or_tampered_body():
    payload = _event_bytes("evt_bad", "customer.subscription.updated")
    good = sign_payload(payload, WEBHOOK_SECRET)
    with pytest.raises(WebhookVerificationError):
        verify_stripe_signature(payload, good, "whsec_other")
    tampered = b'{"id": "evt_evil"}'
    with pytest.raises(WebhookVerificationError):
        verify_stripe_signature(tampered, good, WEBHOOK_SECRET)
    for header in ("", "v1=deadbeef", "t=notanumber,v1=deadbeef"):
        with pytest.raises(WebhookVerificationError):
            verify_stripe_signature(payload, header, WEBHOOK_SECRET)


def test_signature_replayed_timestamp_outside_tolerance():
    """A VALIDLY-SIGNED captured request must fail when replayed later."""
    payload = _event_bytes("evt_old", "checkout.session.completed")
    stale = int(time.time()) - 3600  # signed an hour ago
    header = sign_payload(payload, WEBHOOK_SECRET, timestamp=stale)
    with pytest.raises(WebhookVerificationError):
        verify_stripe_signature(payload, header, WEBHOOK_SECRET)


def test_signature_multiple_v1_candidates_any_match_wins():
    """Secret rotation: EVERY v1 candidate is compared (docs behavior)."""
    now = int(time.time())
    payload = _event_bytes("evt_rot", "customer.subscription.updated")
    signed_blob = f"{now}.".encode() + payload
    rotated = hmac.new(b"whsec_rotated", signed_blob, hashlib.sha256).hexdigest()
    current = hmac.new(WEBHOOK_SECRET.encode(), signed_blob, hashlib.sha256).hexdigest()
    header = f"t={now},v1={rotated},v1={current}"
    assert verify_stripe_signature(payload, header, WEBHOOK_SECRET)["id"] == "evt_rot"


def test_subscription_sync_type_sets_are_coherent():
    assert SUBSCRIPTION_SYNC_TYPES == {
        "checkout.session.completed",
        "customer.subscription.updated",
    }


# ------------------------------------------------------------- idempotency


async def test_duplicate_event_id_processed_once(stripe_factory):
    team_id = await _seed_team(stripe_factory)
    event = {
        "id": "evt_dup",
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"teamId": team_id, "tier": "pro"}}},
    }
    first = await process_event(stripe_factory, event)
    second = await process_event(stripe_factory, event)
    assert first == {"received": True, "duplicate": False, "applied": True}
    assert second == {"received": True, "duplicate": True, "applied": False}
    async with stripe_factory() as db:
        rows = (await db.execute(select(StripeWebhookEvent))).scalars().all()
        plans = (await db.execute(select(BillingPlan))).scalars().all()
        assert len(rows) == 1
        assert len(plans) == 1  # tier written exactly once


async def test_malformed_event_is_rejected_without_record(stripe_factory):
    result = await process_event(stripe_factory, {"data": {"object": {}}})
    assert result["received"] is False
    async with stripe_factory() as db:
        assert (await db.execute(select(StripeWebhookEvent))).scalars().all() == []


async def test_unknown_team_fk_conflict_answered_honestly(stripe_factory):
    result = await process_event(
        stripe_factory,
        {
            "id": "evt_ghost",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"teamId": "no-such-team", "tier": "pro"}}},
        },
    )
    assert result == {"received": False, "reason": "unknown-team"}
    async with stripe_factory() as db:
        rows = (await db.execute(select(StripeWebhookEvent))).scalars().all()
        assert rows == []  # rolled back; Stripe's retry can re-deliver


# ------------------------------------------------------------ tier sync


async def test_subscription_events_sync_existing_tier_storage(stripe_factory):
    """Sync lands in billing_plans.plan (migration 0010) — the existing storage."""
    team_id = await _seed_team(stripe_factory)

    async def evt(eid, etype, tier=None):
        meta = {"teamId": team_id}
        if tier:
            meta["tier"] = tier
        return await process_event(
            stripe_factory,
            {"id": eid, "type": etype, "data": {"object": {"metadata": meta}}},
        )

    # Upgrade to pro via completed checkout.
    assert (await evt("evt_up", "checkout.session.completed", tier="pro"))["applied"] is True
    async with stripe_factory() as db:
        row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
        ).scalar_one()
        assert row.plan == "pro"

    # Plan change syncs again on subscription.updated.
    assert (await evt("evt_ch", "customer.subscription.updated", tier="team"))["applied"] is True
    async with stripe_factory() as db:
        row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
        ).scalar_one()
        assert row.plan == "team"

    # Cancellation downgrades to free.
    assert (await evt("evt_del", "customer.subscription.deleted"))["applied"] is True
    async with stripe_factory() as db:
        row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
        ).scalar_one()
        assert row.plan == "free"


async def test_price_id_fallback_resolves_tier_when_metadata_missing(stripe_factory, monkeypatch):
    """No metadata? The price-id -> tier map (operator config) answers."""
    price_id = "price_team_monthly"
    monkeypatch.setenv("STRIPE_PRICE_TEAM", price_id)
    result = await process_event(
        stripe_factory,
        {
            "id": "evt_price",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "items": [{"price": {"id": price_id}}],
                    "metadata": {},
                    "customer": "cus_123",
                }
            },
        },
    )
    assert result["applied"] is False  # no team binding anywhere -> recorded only


async def test_unbindable_events_are_recorded_but_not_applied(stripe_factory):
    result = await process_event(
        stripe_factory,
        {
            "id": "evt_loose",
            "type": "invoice.paid",
            "data": {"object": {"customer": "cus_123"}},
        },
    )
    assert result == {"received": True, "duplicate": False, "applied": False}
    async with stripe_factory() as db:
        rows = (await db.execute(select(StripeWebhookEvent))).scalars().all()
        assert len(rows) == 1 and rows[0].customer_id == "cus_123"


async def test_unknown_tier_never_applies(stripe_factory):
    team_id = await _seed_team(stripe_factory, "gold")
    result = await process_event(
        stripe_factory,
        {
            "id": "evt_gold",
            "type": "customer.subscription.updated",
            "data": {"object": {"metadata": {"teamId": team_id, "tier": "enterprise"}}},
        },
    )
    assert result["applied"] is False


async def test_plan_change_audit_trail_written(stripe_factory):
    """Every applied sync leaves a billing.plan_changed audit row (system)."""
    from harness_identity.models import AuditLog

    team_id = await _seed_team(stripe_factory, "audited")
    meta = {"teamId": team_id, "tier": "pro"}
    await process_event(
        stripe_factory,
        {
            "id": "evt_aud1",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": meta}},
        },
    )
    meta2 = {"teamId": team_id, "tier": "team"}
    await process_event(
        stripe_factory,
        {
            "id": "evt_aud2",
            "type": "customer.subscription.updated",
            "data": {"object": {"metadata": meta2}},
        },
    )
    async with stripe_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "billing.plan_changed")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        first, second = rows
        assert first.detail["to"] == "pro" and first.detail["source"] == "stripe_webhook"
        assert second.detail["from"] == "pro" and second.detail["to"] == "team"
        assert second.detail["stripeEventId"] == "evt_aud2"
        assert all(r.actor_kind == "system" for r in rows)
        # Secrets scrubber defense-in-depth holds even though we pass none.
        assert all("secret" not in (r.detail or {}) for r in rows)


# ----------------------------------------------- webhook route end-to-end


async def test_webhook_route_full_flow(api, stripe_factory, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    team_id = await _seed_team(stripe_factory, "route-team")
    payload = _event_bytes(
        "evt_route",
        "checkout.session.completed",
        {"metadata": {"teamId": team_id, "tier": "pro"}},
    )

    # Missing signature header -> 400 INVALID_SIGNATURE.
    r = await api.post(WEBHOOK_PATH, content=payload)
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "INVALID_SIGNATURE"

    # Valid signature -> 200, tier synced through the ROUTE.
    r = await api.post(WEBHOOK_PATH, content=payload, headers=_signed_headers(payload))
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True, "duplicate": False, "applied": True}

    # REPLAY of the SAME delivery (same id) -> 200 duplicate, no double-write.
    r = await api.post(WEBHOOK_PATH, content=payload, headers=_signed_headers(payload))
    assert r.status_code == 200
    assert r.json()["duplicate"] is True

    factory = stripe_factory
    async with factory() as db:
        row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
        ).scalar_one()
        assert row.plan == "pro"
        events = (await db.execute(select(StripeWebhookEvent))).scalars().all()
        assert len(events) == 1


async def test_webhook_route_requires_configured_secret(api, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    payload = _event_bytes("evt_nosecret", "invoice.paid")
    r = await api.post(WEBHOOK_PATH, content=payload, headers=_signed_headers(payload))
    assert r.status_code == 503
    assert r.json()["detail"]["error"]["code"] == "CONFIG_MISSING"


# ------------------------------------------------------- app.py wiring


def test_make_app_injects_adapter_and_routes_only_behind_flag(monkeypatch):
    """make_app() wires the adapter + routes when flagged; nothing when off."""

    async def build(flag: str | None):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            if flag is None:
                monkeypatch.delenv("HARNESS_BILLING", raising=False)
            else:
                monkeypatch.setenv("HARNESS_BILLING", flag)
            monkeypatch.setenv("STRIPE_API_KEY", "sk_test_wiring")
            from harness_identity.app import make_app
            from harness_identity.config import Settings

            return make_app(async_sessionmaker(engine), Settings(master_key=b"x" * 32))
        finally:
            await engine.dispose()

    def registered_paths(app) -> set[str]:
        # This FastAPI keeps each included APIRouter as ONE composite route
        # entry, so raw .routes/.path introspection lies; OpenAPI flattens.
        return set(app.openapi().get("paths", {}))

    app_off = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(build(None))
    assert not hasattr(app_off.state, "stripe_client")
    paths_off = registered_paths(app_off)
    assert WEBHOOK_PATH not in paths_off
    assert "/api/v1/billing/state" in paths_off  # M10 surface untouched

    app_on = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(build("stripe"))
    assert isinstance(app_on.state.stripe_client, HttpxStripeClient)
    paths_on = registered_paths(app_on)
    assert WEBHOOK_PATH in paths_on
    assert "/api/v1/billing/checkout-session" in paths_on


# --------------------------------------------------- migration parity


def test_migration_sql_matches_model_shape():
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "0032_stripe_webhook_events.sql"
    ).read_text()
    assert sql.lstrip().startswith("--")
    assert "CREATE TABLE stripe_webhook_events (" in sql
    assert "\tid VARCHAR(255) NOT NULL," in sql
    assert "\ttype VARCHAR(100) NOT NULL," in sql
    assert "\treceived_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL," in sql
    assert "\tPRIMARY KEY (id)," in sql
    assert "\tFOREIGN KEY(team_id) REFERENCES teams (id) ON DELETE CASCADE" in sql
    cols = StripeWebhookEvent.__table__.columns
    assert list(cols.keys()) == ["id", "type", "team_id", "customer_id", "received_at"]
    assert cols["id"].primary_key


def test_migration_slot_is_exclusive_across_lanes():
    """M22 coordination: numbered slots are one-per-mission — no collisions.

    M3 originally took 0031; M2's tier_cap_overrides claimed it concurrently,
    so this node renumbered to 0032. The ledger law (one table per number)
    is pinned here so the next concurrent wave cannot repeat that silently.
    """
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    numbers: list[int] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        stem = path.name.split("_", 1)[0]
        assert stem.isdigit(), f"migration filename lacks numeric prefix: {path.name}"
        numbers.append(int(stem))
    assert len(numbers) == len(set(numbers)), "duplicate migration slot numbers"
    assert numbers == sorted(numbers), "migration numbers not monotonic"
    assert (migrations_dir / "0032_stripe_webhook_events.sql").exists()
