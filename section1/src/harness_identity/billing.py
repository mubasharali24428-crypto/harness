"""Mission 10 node B1 — billing API behind the HARNESS_BILLING flag.

Server side of contract M10-C3. Own router module so nothing else has to touch
app.py this mission: exposes `router` (same prefix convention as invites.py /
api_keys.py) and ATLAS registers it at merge with `app.include_router(router)`.

Contract M10-C3:
- GET /api/v1/billing/state ->
  {enabled, plan, quota: {monthlyBudgetUsd, usedUsd}, source}
- PUT /api/v1/billing/plan body {plan} admin-only -> updated state

Flag semantics (read PER REQUEST via os.environ so operators flip it with a
restart-free env change and tests can monkeypatch it):
- unset / empty / "off" / "none" => honest disabled state, HTTP 200,
  {enabled: false, plan: null, quota: null, source: "flag-off"}.
- any other value => enabled. Source resolution:
  * "stripe" only when HARNESS_BILLING=stripe AND STRIPE_API_KEY present AND an
    adapter was injected at app.state.stripe_client. The adapter satisfies the
    StripeClient Protocol below — a THIN CLIENT INTERFACE. The real `stripe`
    package is NEVER imported here (no new installs, hard mission rule); the
    concrete adapter is wired by whoever deploys behind the flag.
  * otherwise "local": plan persisted in billing_plans (migration 0010),
    usedUsd = SUM(cost_ledger.cost_usd) for the caller's team THIS calendar
    month joined projects -> agent_runs (READ-only against frozen tables).
  * a missing or failing adapter degrades to "local" rather than erroring —
    availability over strictness (same posture as the wave9 rate-limit node).

Quota math (local, M18-C2 ONE budget truth): the displayed cap is the SAME
resolution every other consumer uses — contracts.budget.resolve_cap:
teams.budget_usd > billing_plans.monthly_budget_usd > env
HARNESS_TEAM_BUDGET_USD > unlimited-explicit. (This display previously used
max(plan, team) — a fourth disagreeing truth.) The usage window is the ONE
month definition: DB-clock date_trunc('month', now()) via MONTH_WINDOW_SQL,
not an app-computed month start.

PUT stores the plan row LOCALLY even in stripe mode (the local row stays the
system of record until a provider-sync wiring lands); admin-only via the
contract Role.ADMIN gate — engineers/viewers/guests get 403 in the frozen
error_body() envelope.
"""

import os
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import (
    DEGRADED_FLAG_WIRE,
    MONTH_WINDOW_SQL,
    Principal,
    Role,
    error_body,
    resolve_cap,
)
from pydantic import BaseModel
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from .audit_emit import emit_billing_plan_changed
from .auth import get_principal
from .models import Base, uid

router = APIRouter(prefix="/api/v1")

PLAN_TIERS = ("free", "pro", "team")

# app.state key holding the injected thin client (StripeClient protocol).
_STRIPE_CLIENT_ATTR = "stripe_client"


class BillingPlan(Base):
    """Mirror of migration 0010_billing_plans.sql.

    Lives HERE (not models.py) because file ownership gives this node only
    billing.py; the class registers on the shared declarative Base so scratch
    test apps pick it up via Base.metadata.create_all.
    """

    __tablename__ = "billing_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    # M15-D1 sweep (W5-C2): plan dies with its team.
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    plan: Mapped[str] = mapped_column(String(30), nullable=False)
    # H-023: NUMERIC(12,6) per migration 0020 (was 12,2). Python side carries
    # exact Decimal; SQLAlchemy converts on the way in/out.
    monthly_budget_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StripeClient(Protocol):
    """Thin-client interface for a real Stripe integration.

    Implementations are injected at app.state.stripe_client by deployment
    wiring; this module NEVER imports the stripe package. fetch_state returns
    camelCase quota fields per contract M10-C3.
    """

    async def fetch_state(self, team_id: str) -> dict:
        """Return {plan: str | None, monthlyBudgetUsd: float, usedUsd: float}.

        Values are JSON numbers per contract M10-C3 (wire format unchanged);
        internal money math below stays in exact Decimal (H-023).
        """
        ...  # pragma: no cover - interface only


def _billing_mode() -> str | None:
    """Normalized HARNESS_BILLING flag value, or None when effectively off."""
    raw = os.environ.get("HARNESS_BILLING")
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in ("", "off", "none"):
        return None
    return value


def _resolve_stripe_adapter(request: Request) -> StripeClient | None:
    """The injected adapter only when flag=stripe AND a key is configured."""
    if _billing_mode() != "stripe":
        return None
    if not os.environ.get("STRIPE_API_KEY"):
        return None
    return getattr(request.app.state, _STRIPE_CLIENT_ATTR, None)


def _month_start_utc() -> datetime:
    """DEPRECATED app-clock helper kept only for backwards-compatible imports.

    M18-C2: quota math no longer uses it — the usage window is the ONE
    definition (MONTH_WINDOW_SQL, DB clock). Retained because external
    callers/tests imported it before the unification; do not use in new code.
    """
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _used_this_month_stmt(team_id: str):
    """SUM(cost_ledger.cost_usd) for the team THIS month, READ-only join
    across the frozen tables cost_ledger -> agent_runs -> projects.

    M18-C2: the window is the ONE month definition — the literal DB-clock
    ``created_at >= date_trunc('month', now())`` fragment from
    contracts.MONTH_WINDOW_SQL is spliced in verbatim (a drift between this
    file and the contract fails the parity test instead of shipping a second
    window). SUM runs in SQL over NUMERIC(12,6) — exact base-10 aggregation
    (H-023); COALESCEs to exact Decimal zero, never a float.
    """
    return text(
        "SELECT COALESCE(SUM(l.cost_usd), 0) AS used_usd "
        "FROM cost_ledger l "
        "JOIN agent_runs r ON r.id = l.run_id "
        "JOIN projects p ON p.id = r.project_id "
        f"WHERE p.team_id = :team_id AND l.{MONTH_WINDOW_SQL}"
    ).bindparams(team_id=team_id)


def _money_wire(value) -> float:
    """Exact Decimal -> JSON number for the M10-C3 wire (format unchanged)."""
    dec = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    return float(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


async def _quota_payload(session_factory, team_id: str) -> dict:
    """Quota block from the ONE budget truth (M18-C2).

    The displayed cap is contracts.budget.resolve_cap's answer — teams
    override > plan row > env default > explicit unlimited — never the old
    ``max(plan, team)`` heuristic. When the money store cannot be consulted
    the payload degrades HONESTLY: zeros plus the contract
    ``degraded: true`` flag (DEGRADED_FLAG_WIRE), so operators see
    degradation instead of fabricated numbers. Availability-over-strictness
    still holds for the DISPLAY path (200 + flag, not a 500); the ENFORCEMENT
    paths fail closed in their own modules.
    """
    degraded = False
    try:
        async with session_factory() as db:
            plan_row = (
                await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
            ).scalar_one_or_none()
            used_usd = (await db.execute(_used_this_month_stmt(team_id))).scalar_one()
            override_row = (
                await db.execute(
                    text("SELECT budget_usd FROM teams WHERE id = :team_id"),
                    {"team_id": team_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 — display degrades with an honest flag
        degraded = True
        return {
            "monthlyBudgetUsd": 0.0,
            "usedUsd": 0.0,
            DEGRADED_FLAG_WIRE: True,
        }

    resolved = resolve_cap(
        getattr(override_row, "budget_usd", None) if override_row is not None else None,
        plan_row.monthly_budget_usd if plan_row else None,
    )
    monthly_budget = Decimal("0") if resolved.cap_usd is None else resolved.cap_usd
    payload = {
        "monthlyBudgetUsd": _money_wire(monthly_budget),
        "usedUsd": _money_wire(used_usd),
    }
    if degraded:
        payload[DEGRADED_FLAG_WIRE] = True
    return payload


async def _disabled_state() -> dict:
    return {"enabled": False, "plan": None, "quota": None, "source": "flag-off"}


async def _state_payload(request: Request, principal: Principal) -> dict:
    """Full enabled state; source resolved stripe -> local (documented order)."""
    session_factory = request.app.state.session_factory
    adapter = _resolve_stripe_adapter(request)
    if adapter is not None:
        try:
            remote = await adapter.fetch_state(principal.teamId)
            return {
                "enabled": True,
                "plan": remote.get("plan") or "free",
                "quota": {
                    "monthlyBudgetUsd": _money_wire(remote.get("monthlyBudgetUsd", 0)),
                    "usedUsd": _money_wire(remote.get("usedUsd", 0)),
                },
                "source": "stripe",
            }
        except Exception:  # noqa: BLE001 — adapter failure degrades to local
            pass
    quota = await _quota_payload(session_factory, principal.teamId)
    async with session_factory() as db:
        plan_row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == principal.teamId))
        ).scalar_one_or_none()
    return {
        "enabled": True,
        "plan": plan_row.plan if plan_row is not None else "free",
        "quota": quota,
        "source": "local",
    }


@router.get("/billing/state")
async def billing_state(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Current billing state for the caller's team (any authenticated role)."""
    if _billing_mode() is None:
        return await _disabled_state()
    return await _state_payload(request, principal)


class PlanUpdateBody(BaseModel):
    plan: Literal["free", "pro", "team"]


@router.put("/billing/plan")
async def update_billing_plan(
    body: PlanUpdateBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict:
    """Upsert the team's billing_plans row (admin-only) and return the state."""
    if principal.role != Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=error_body("FORBIDDEN", "admin role required to change the billing plan"),
        )
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == principal.teamId))
        ).scalar_one_or_none()
        previous = None if row is None else row.plan
        if row is None:
            row = BillingPlan(
                team_id=principal.teamId,
                plan=body.plan,
                monthly_budget_usd=Decimal("0"),
            )
            db.add(row)
        else:
            row.plan = body.plan
        row.updated_at = datetime.now(UTC)
        await db.commit()
        # C-002 audit: plan changes are money-adjacent and admin-gated. Emitted
        # after commit; detail carries plan names only, never provider secrets.
        await emit_billing_plan_changed(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=row.id,
            detail={"from": previous, "to": body.plan},
        )
        return await _state_payload(request, principal)
