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

Quota math (local): effective budget = max(billing_plans.monthly_budget_usd,
teams.budget_usd from migration 0007 when the column exists). The 0007 column
is NOT mapped on the frozen Team model, so it is read with a guarded raw SQL
SELECT that cleanly yields None on schemas predating 0007 (scratch sqlite test
apps) — never a crash, never fabricated numbers.

PUT stores the plan row LOCALLY even in stripe mode (the local row stays the
system of record until a provider-sync wiring lands); admin-only via the
contract Role.ADMIN gate — engineers/viewers/guests get 403 in the frozen
error_body() envelope.
"""

import os
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, error_body
from pydantic import BaseModel
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from .auth import get_principal
from .models import AgentRun, Base, CostLedger, Project, uid

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
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id"), nullable=False, unique=True, index=True
    )
    plan: Mapped[str] = mapped_column(String(30), nullable=False)
    monthly_budget_usd: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
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
        """Return {plan: str | None, monthlyBudgetUsd: float, usedUsd: float}."""
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


async def _team_budget_override(session_factory, team_id: str) -> float | None:
    """Read teams.budget_usd (migration 0007) without mapping it on Team.

    Returns None when the column/table answer is unavailable (pre-0007 schema,
    scratch sqlite apps) — callers fall back to the plan-row budget. A broad
    guard is deliberate: quota display must never hard-fail on schema drift.
    """
    try:
        async with session_factory() as db:
            row = (
                await db.execute(
                    text("SELECT budget_usd FROM teams WHERE id = :team_id"),
                    {"team_id": team_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 — degrade to None, see docstring
        return None
    if row is None or row[0] is None:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def _month_start_utc() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _used_this_month_stmt(team_id: str):
    """SUM(cost_ledger.cost_usd) for the team this month, READ-only join
    across the frozen tables cost_ledger -> agent_runs -> projects."""
    return (
        select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0))
        .select_from(CostLedger)
        .join(AgentRun, AgentRun.id == CostLedger.run_id)
        .join(Project, Project.id == AgentRun.project_id)
        .where(Project.team_id == team_id, CostLedger.created_at >= _month_start_utc())
    )


async def _quota_payload(session_factory, team_id: str) -> dict:
    async with session_factory() as db:
        plan_row = (
            await db.execute(select(BillingPlan).where(BillingPlan.team_id == team_id))
        ).scalar_one_or_none()
        used_usd = (await db.execute(_used_this_month_stmt(team_id))).scalar_one()

    monthly_budget = float(plan_row.monthly_budget_usd) if plan_row is not None else 0.0
    override = await _team_budget_override(session_factory, team_id)
    if override is not None:
        # Both signals are caps someone set deliberately: the larger governs.
        monthly_budget = max(monthly_budget, override)
    return {
        "monthlyBudgetUsd": round(monthly_budget, 2),
        "usedUsd": round(float(used_usd), 2),
    }


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
                    "monthlyBudgetUsd": round(float(remote.get("monthlyBudgetUsd", 0)), 2),
                    "usedUsd": round(float(remote.get("usedUsd", 0)), 2),
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
        if row is None:
            row = BillingPlan(
                team_id=principal.teamId,
                plan=body.plan,
                monthly_budget_usd=0,
            )
            db.add(row)
        else:
            row.plan = body.plan
        row.updated_at = datetime.now(UTC)
        await db.commit()
    return await _state_payload(request, principal)
