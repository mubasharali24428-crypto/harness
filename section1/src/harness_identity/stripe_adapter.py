"""Mission 22 lane M3 — LIVE Stripe adapter behind HARNESS_BILLING=stripe.

Closes the O-DELTA killer gap "StripeClient Protocol never instantiated":
until this node the M10 billing module carried only the Protocol (a thin-client
interface) and whoever deployed behind the flag had to hand-roll an adapter.
This module IS that concrete adapter, wired by app.py at startup:

- HARNESS_BILLING unset/off  => NOTHING changes (flag off => honest disabled
  state; no adapter injected; the webhook/checkout routes answer 503).
- HARNESS_BILLING=stripe + STRIPE_API_KEY => app.state.stripe_client is an
  HttpxStripeClient (satisfies billing.StripeClient) and the checkout +
  webhook routes go live. Test-mode keys (sk_test_/whsec_) work unchanged;
  STRIPE_API_BASE overrides the endpoint so CI points at a stub server and
  NEVER touches network (hard mission rule: mocked Stripe responses only).
  Migration slot: 0032 (0031 was taken concurrently by M2's lane — see
  test_migration_slot_is_exclusive_across_lanes).

Transport: the REST API via httpx (form-encoded per Stripe docs). The
`stripe` package stays UNINSTALLED — zero new deps, matching the hard rule
the M10 tests pin (test_stripe_package_never_imported).

Webhook receiver (POST /api/v1/billing/webhook, unauthenticated like
csp_report.py — authenticity comes from the signature, not a bearer):
1. Raw body read BEFORE any parsing; Stripe-signature header verified
   hand-rolled per docs: HMAC-SHA256 over "{t}.{payload}" keyed by
   STRIPE_WEBHOOK_SECRET, all v1 candidates compared constant-time, and the
   timestamp enforced within a tolerance so captured requests REPLAY stale.
2. Idempotency: the event id is the PRIMARY KEY of stripe_webhook_events
   (migration 0031). Insert + tier sync share ONE transaction, so a duplicate
   delivery dies on the PK conflict and answers {duplicate: true} having
   changed nothing, and a crashed sync rolls the claim back so Stripe's
   retry re-applies it (exactly-once effect).
3. Tier sync: checkout.session.completed / customer.subscription.updated /
   customer.subscription.deleted map the subscription onto the EXISTING
   per-team tier storage — billing_plans.plan (migration 0010, the same rows
   PUT /billing/plan upserts and GET /billing/state reads). Tier resolution:
   our own checkout metadata (teamId/tier) first, then the price-id -> tier
   map from STRIPE_PRICE_{FREE,PRO,TEAM}. Unmatched teams are recorded and
   IGNORED (200) — Stripe must not retry forever on a team we cannot find.
   Plan changes are audited via the existing billing.plan_changed action with
   actor_kind="system" (ids only in detail — the scrubber drops secret-shaped
   keys, and no Stripe secret ever enters audit or logs).

fetch_state note (honesty over completeness): quota NUMBERS stay the local
ONE truth (M18-C2 resolve_cap chain) — no fabricated zeros are shown as a
Stripe-sourced budget. The adapter therefore implements the Protocol surface
used by checkout/webhooks and deliberately declines fetch_state; billing.py's
availability-over-strictness posture degrades the DISPLAY to local rows,
which this module keeps stripe-correct via the webhook sync above.
"""

import hashlib
import hmac
import logging
import os
import time
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, error_body
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .audit_emit import emit_billing_plan_changed
from .auth import get_principal
from .billing import PLAN_TIERS, BillingPlan
from .http_client import env_client
from .models import Base

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# NOTE (M22 coordination): the tier ladder is read through billing's
# PLAN_TIERS — locally defined at Mission-10 baseline and a verbatim re-export
# of the FROZEN harness_contracts.tiers ladder once M22-M2 lands, so this
# module is correct under EITHER merge order of the two lanes.

# Env-var names per tier (price IDs live in operator config, never in code —
# secrets/config never enter logs or model context).
TIER_PRICE_VARS = {
    "free": "STRIPE_PRICE_FREE",
    "pro": "STRIPE_PRICE_PRO",
    "team": "STRIPE_PRICE_TEAM",
}

DEFAULT_STRIPE_API_BASE = "https://api.stripe.com"
CHECKOUT_PATH = "/v1/checkout/sessions"

# Events that carry an actionable subscription state for a team. Deletion
# flavors land the team back on the free tier; everything else in this set
# syncs the resolved tier verbatim.
SUBSCRIPTION_SYNC_TYPES = frozenset(
    {"checkout.session.completed", "customer.subscription.updated"}
)
SUBSCRIPTION_DOWNGRADE_TYPES = frozenset({"customer.subscription.deleted"})

# Stripe recommends rejecting timestamps older than a few minutes; docs use
# five minutes for the SDK default tolerance.
SIGNATURE_TOLERANCE_S = 300


class StripeAdapterError(Exception):
    """A Stripe API call failed (HTTP-level). Message is safe to surface."""


class WebhookVerificationError(Exception):
    """The Stripe-signature header did not authenticate this payload."""


class StripeWebhookEvent(Base):
    """Mirror of migration 0031_stripe_webhook_events.sql.

    The Stripe event id IS the primary key: the idempotency ledger. Lives
    HERE (not models.py) for the same file-ownership reason as BillingPlan;
    registers on the shared declarative Base so scratch test apps pick it up
    via Base.metadata.create_all.
    """

    __tablename__ = "stripe_webhook_events"

    # evt_* ids from Stripe; no uid() default — the PROVIDER id is the key.
    id: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(nullable=False, default="")
    # Nullable: events we cannot bind to a team are still recorded.
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    customer_id: Mapped[str | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        nullable=False, default=lambda: datetime.now(UTC)
    )


# ---------------------------------------------------------------- transport


def _import_httpx():
    """Import httpx lazily so the module loads even where it is absent."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - dev deps always ship it
        raise StripeAdapterError("httpx is required for the Stripe adapter") from exc
    return httpx


class HttpxStripeClient:
    """Concrete billing.StripeClient speaking the Stripe REST API via httpx."""

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 10.0):
        self._api_key = api_key
        self._base_url = (
            base_url or os.environ.get("STRIPE_API_BASE") or DEFAULT_STRIPE_API_BASE
        ).rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Stripe-Version": "2024-06-20",
        }

    async def _request(self, method: str, path: str, data: dict) -> dict:
        _import_httpx()
        async with env_client(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.request(method, path, data=data, headers=self._headers())
        if response.status_code >= 400:
            # Surface Stripe's own message (safe: it never contains our keys).
            raise StripeAdapterError(
                f"stripe {method} {path} failed: HTTP {response.status_code}"
            )
        return response.json()

    async def create_checkout_session(
        self, *, team_id: str, tier: str, price_id: str, success_url: str, cancel_url: str
    ) -> dict:
        """Create a subscription-mode Checkout Session bound to the team.

        teamId/tier ride BOTH the session metadata and the subscription
        metadata, so every downstream webhook (session.completed AND future
        subscription.*) can resolve the team without extra Stripe expansion
        round-trips. client_reference_id duplicates teamId per Stripe's own
        recommendation for checkout binding.
        """
        data = {
            "mode": "subscription",
            "client_reference_id": team_id,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price]": price_id,
            "metadata[teamId]": team_id,
            "metadata[tier]": tier,
            "subscription_data[metadata][teamId]": team_id,
            "subscription_data[metadata][tier]": tier,
        }
        return await self._request("POST", CHECKOUT_PATH, data)

    async def fetch_state(self, team_id: str) -> dict:
        """Protocol participant, HONESTLY declining: raise, never fabricate.

        Quota NUMBERS stay the local ONE truth (M18-C2 resolve_cap chain);
        this client has no honest source for them (usage lives in OUR
        cost_ledger, not Stripe), so per availability-over-strictness in
        billing.py the display degrades to local rows — which THIS module
        keeps stripe-correct through the webhook tier sync.
        """
        raise NotImplementedError(
            "quota numbers are local truth (M18-C2); stripe mode syncs tiers "
            "via webhooks and displays local rows"
        )


def make_client_from_env() -> HttpxStripeClient | None:
    """The live adapter only when flag=stripe AND a key is configured."""
    if os.environ.get("HARNESS_BILLING", "").strip().lower() != "stripe":
        return None
    api_key = os.environ.get("STRIPE_API_KEY")
    if not api_key:
        return None
    return HttpxStripeClient(api_key)


# ---------------------------------------------------- signature verification


def sign_payload(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Produce a Stripe-signature header value (test helper symmetry)."""
    t = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(
        secret.encode(), f"{t}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={t},v1={mac}"


def verify_stripe_signature(
    payload: bytes, header: str, secret: str, *, tolerance_s: int = SIGNATURE_TOLERANCE_S
) -> dict:
    """Verify a Stripe webhook signature per the hand-rolled docs recipe.

    Scheme (docs: https://docs.stripe.com/webhooks#verify-manually):
    signed_payload = "{timestamp}.{raw_body}";
    expected = HMAC-SHA256(signed_payload, key=webhook_secret), hex.
    EVERY v1 candidate is compared (constant-time) — Stripe rotates secrets
    and may present multiple valid signatures. The timestamp is additionally
    enforced within ``tolerance_s`` so a captured (validly-signed) request
    cannot be REPLAYED later.
    """
    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                raise WebhookVerificationError("unparsable signature timestamp") from None
        elif key == "v1":
            candidates.append(value)
    if timestamp is None or not candidates:
        raise WebhookVerificationError("signature header missing t= or v1=")

    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256)
    expected_hex = expected.hexdigest()
    if not any(hmac.compare_digest(expected_hex, candidate) for candidate in candidates):
        raise WebhookVerificationError("signature mismatch")

    age = abs(time.time() - timestamp)
    if age > tolerance_s:
        raise WebhookVerificationError(f"signature timestamp outside tolerance ({age:.0f}s)")

    import json

    try:
        return json.loads(payload)
    except ValueError as exc:
        raise WebhookVerificationError("payload is not JSON") from exc


# ------------------------------------------------------------ tier syncing


def _event_object(event: dict) -> dict:
    return (event.get("data") or {}).get("object") or {}


def _resolve_team_and_tier(event: dict) -> tuple[str | None, str | None]:
    """Team + tier from our checkout metadata; price-id map as the fallback."""
    obj = _event_object(event)
    metadata = obj.get("metadata") or {}
    team_id = metadata.get("teamId") or obj.get("client_reference_id")
    tier = metadata.get("tier")
    if tier not in PLAN_TIERS:
        tier = None
        for item in obj.get("items") or obj.get("line_items") or []:
            raw_price = item.get("price")
            price_id = (raw_price if isinstance(raw_price, dict) else {}).get("id")
            if not price_id:
                continue
            for candidate, env_name in TIER_PRICE_VARS.items():
                if price_id == os.environ.get(env_name):
                    tier = candidate
    return team_id, tier


def _target_tier(event: dict, resolved_tier: str | None) -> str | None:
    if event.get("type") in SUBSCRIPTION_DOWNGRADE_TYPES:
        return "free"
    return resolved_tier


async def _apply_tier(db, team_id: str, tier: str) -> str | None:
    """Upsert billing_plans.plan (the EXISTING M2-tier storage, migration 0010).

    Same rows PUT /billing/plan manages — one storage, one truth. Returns the
    previous plan for the audit trail (None when the row is new).
    """
    row = (
        await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
    ).scalar_one_or_none()
    previous = None
    if row is None:
        db.add(BillingPlan(team_id=team_id, plan=tier, monthly_budget_usd=Decimal("0")))
    else:
        previous = row.plan
        row.plan = tier
        row.updated_at = datetime.now(UTC)
    return previous


async def process_event(session_factory, event: dict) -> dict:
    """Record the event idempotently and sync the tier in ONE transaction.

    The event-id INSERT is the idempotency claim: a duplicate delivery trips
    the primary-key conflict, rolls back untouched, and reports duplicate.
    Because the tier sync shares the transaction, a mid-sync crash rolls the
    claim back too — Stripe's retry then re-applies cleanly.
    """
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if not event_id or not event_type:
        return {"received": False, "reason": "malformed-event"}

    obj = _event_object(event)
    team_id, resolved_tier = _resolve_team_and_tier(event)
    target_tier = _target_tier(event, resolved_tier)
    applies = bool(team_id) and target_tier in PLAN_TIERS

    async with session_factory() as db:
        record = StripeWebhookEvent(
            id=event_id,
            type=event_type,
            team_id=team_id if isinstance(team_id, str) else None,
            customer_id=obj.get("customer") if isinstance(obj.get("customer"), str) else None,
        )
        db.add(record)
        try:
            previous = None
            if applies:
                assert isinstance(team_id, str)
                previous = await _apply_tier(db, team_id, str(target_tier))
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            # A FK violation means the event binds an UNKNOWN team — record
            # that honestly rather than claiming idempotent success.
            if "fk_stripe_webhook_events" in str(exc).lower() or "FOREIGN KEY" in str(exc):
                return {"received": False, "reason": "unknown-team"}
            return {"received": True, "duplicate": True, "applied": False}

        if applies:
            assert isinstance(team_id, str)
            # Post-commit audit (never throws into the webhook path). System
            # actor: no human performed this; ids only in detail. Empty
            # user id resolves to a NULL actor_member_id inside _emit.
            await emit_billing_plan_changed(
                db,
                actor_user_id="",
                team_id=team_id,
                target_id=team_id,
                detail={
                    "from": previous,
                    "to": target_tier,
                    "source": "stripe_webhook",
                    "stripeEventId": event_id,
                },
                actor_kind="system",
            )

    return {"received": True, "duplicate": False, "applied": applies}


# ------------------------------------------------------------------- routes


class CheckoutSessionBody(BaseModel):
    """Tier-upgrade checkout request (camelCase wire per api-conventions.md)."""

    tier: str = Field(min_length=1, max_length=30)
    successUrl: str = Field(min_length=1, max_length=2048)
    cancelUrl: str = Field(min_length=1, max_length=2048)


def _require_live_mode(request: Request) -> HttpxStripeClient:
    """503 unless the flag+key say stripe AND the app carries the adapter."""
    client = getattr(request.app.state, "stripe_client", None)
    if not isinstance(client, HttpxStripeClient):
        raise HTTPException(
            status_code=503,
            detail=error_body(
                "BILLING_DISABLED",
                "stripe billing is not enabled (set HARNESS_BILLING=stripe and STRIPE_API_KEY)",
            ),
        )
    return client


# B008: FastAPI's Depends must be called at import time, not in a default.
_principal_dep = Depends(get_principal)


@router.post("/billing/checkout-session")
async def create_checkout_session(
    body: CheckoutSessionBody,
    request: Request,
    principal: Principal = _principal_dep,
) -> dict:
    """Start the tier-upgrade flow: mint a Stripe Checkout Session."""
    if body.tier not in PLAN_TIERS:
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", f"unknown tier: {body.tier}"),
        )
    client = _require_live_mode(request)
    price_var = TIER_PRICE_VARS[body.tier]
    price_id = os.environ.get(price_var)
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=error_body("CONFIG_MISSING", f"{price_var} is not configured"),
        )
    try:
        session = await client.create_checkout_session(
            team_id=principal.teamId,
            tier=body.tier,
            price_id=price_id,
            success_url=body.successUrl,
            cancel_url=body.cancelUrl,
        )
    except StripeAdapterError as exc:
        logger.warning("stripe checkout create failed: %s", exc)
        raise HTTPException(
            status_code=502, detail=error_body("STRIPE_ERROR", "stripe checkout unavailable")
        ) from exc
    return {"id": session.get("id"), "url": session.get("url"), "tier": body.tier}


@router.post("/billing/webhook")
async def billing_webhook(request: Request) -> dict:
    """Signature-verified, idempotent Stripe webhook receiver.

    Unauthenticated BY DESIGN (csp_report.py precedent): authenticity is the
    HMAC, not a bearer. Always 200 after a verified signature — even for
    events we ignore — so Stripe stops retrying; only bad signatures 400.
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail=error_body("CONFIG_MISSING", "STRIPE_WEBHOOK_SECRET is not configured"),
        )
    payload = await request.body()
    header = request.headers.get("stripe-signature", "")
    try:
        event = verify_stripe_signature(payload, header, secret)
    except WebhookVerificationError as exc:
        raise HTTPException(
            status_code=400, detail=error_body("INVALID_SIGNATURE", str(exc))
        ) from exc
    result = await process_event(request.app.state.session_factory, event)
    return {"received": True, **result}
