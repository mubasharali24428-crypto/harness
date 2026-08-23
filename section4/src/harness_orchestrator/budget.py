"""Wave 9 node B: per-team budget caps enforced BEFORE any model call.

``check_budget`` aggregates the team's spend from ``cost_ledger`` (READ-ONLY
raw SQL against frozen Section-1 table names: cost_ledger → agent_runs →
projects → teams) and compares it — plus the estimated cost of the next model
call — against the team's cap. Cap resolution order (first non-None wins):

1. per-team override from ``teams.budget_usd`` (migration 0007, NULL = unset)
2. ``HARNESS_TEAM_BUDGET_USD`` environment default
3. None = unlimited

All statements run identically on PostgreSQL/asyncpg (prod) and
SQLite/aiosqlite (unit tests): no vendor-specific SQL, and timestamps are
bound as datetime objects (asyncpg rejects ISO strings for TIMESTAMPTZ).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENV_TEAM_BUDGET_USD = "HARNESS_TEAM_BUDGET_USD"

# Frozen table names (Section 1 owns them); this module only READS.
_SPEND_SQL = """
SELECT COALESCE(SUM(l.cost_usd), 0) AS spent_usd
FROM cost_ledger l
JOIN agent_runs r ON l.run_id = r.id
JOIN projects p ON r.project_id = p.id
JOIN teams t ON p.team_id = t.id
WHERE t.id = :team_id
"""

_CAP_OVERRIDE_SQL = "SELECT budget_usd FROM teams WHERE id = :team_id"

DEFAULT_ESTIMATED_CALL_COST_USD = 0.05


@dataclass(frozen=True)
class BudgetStatus:
    """Wire fields are camelCase per api-conventions.md; python stays snake_case."""

    allowed: bool
    spentUsd: float
    capUsd: float | None
    reason: str = ""


def env_budget_default() -> float | None:
    """Parse ``HARNESS_TEAM_BUDGET_USD``; unset/blank/garbage => unlimited."""
    raw = os.environ.get(ENV_TEAM_BUDGET_USD, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("ignoring non-numeric %s=%r", ENV_TEAM_BUDGET_USD, raw)
        return None
    return max(value, 0.0)


def _coerce_cap(value) -> float | None:
    """NUMERIC comes back as Decimal on asyncpg / float on sqlite; normalize."""
    if value is None:
        return None
    return float(value)


def _resolve_cap(team_override, env_default) -> float | None:
    if team_override is not None:
        return _coerce_cap(team_override)
    return env_default


def estimate_next_call_cost_usd(
    model_router=None,
    role=None,
    estimated_cost_usd: float = DEFAULT_ESTIMATED_CALL_COST_USD,
) -> float:
    """Best-effort estimate of the next model call's cost.

    Uses the frozen ModelRouter contract's own estimator when one is wired;
    otherwise falls back to the planner's default plan-cost figure.
    Never raises: an estimator failure must not block a budget check.
    """
    if model_router is not None and role is not None:
        try:
            est = float(model_router.estimate_cost(role, input_tokens=4096, output_tokens=512))
            if est >= 0:
                return est
        except Exception:  # noqa: BLE001 — estimation is best-effort by design
            logger.exception("model_router.estimate_cost failed; using fallback estimate")
    return estimated_cost_usd


async def check_budget(
    factory,
    team_id: str,
    *,
    budget_usd: float | None = None,
    estimated_call_cost_usd: float | None = None,
    model_router=None,
    reviewer_role=None,
) -> BudgetStatus:
    """Sum the team's ledger spend + next-call estimate vs its cap.

    Args:
        factory: SQLAlchemy async session factory (same duck-type the
            orchestrator already uses for run_queue/rehydration).
        team_id: owning team.
        budget_usd: pre-resolved cap override; when omitted, the per-team
            ``teams.budget_usd`` column is read and falls back to the
            ``HARNESS_TEAM_BUDGET_USD`` env default (None = unlimited).
        estimated_call_cost_usd: explicit next-call estimate; computed via
            ``estimate_next_call_cost_usd`` when omitted.
        model_router / reviewer_role: optional contract router used only for
            the next-call cost estimate.

    Read-only against the frozen schema; any DB failure degrades to
    "allowed" so an outage can never brick orchestration (spend enforcement
    is advisory at the storage layer, mandatory at the decision layer).
    """
    from sqlalchemy import text

    env_default = env_budget_default()
    team_override = None
    spent = 0.0

    try:
        async with factory() as db:
            if budget_usd is None:
                row = (
                    await db.execute(text(_CAP_OVERRIDE_SQL), {"team_id": team_id})
                ).first()
                team_override = getattr(row, "budget_usd", None) if row is not None else None
            rows = (
                await db.execute(text(_SPEND_SQL), {"team_id": team_id})
            ).fetchall()
            if rows:
                spent = _coerce_cap(rows[0][0]) or 0.0
    except Exception:  # noqa: BLE001 — ledger read failure must not brick runs
        logger.exception("budget check could not read spend for team %s", team_id)

    cap = budget_usd if budget_usd is not None else _resolve_cap(team_override, env_default)
    if cap is None:
        return BudgetStatus(allowed=True, spentUsd=spent, capUsd=None)

    estimate = (
        estimated_call_cost_usd
        if estimated_call_cost_usd is not None
        else estimate_next_call_cost_usd(model_router=model_router, role=reviewer_role)
    )
    # Micro-dollar rounding (ledger is NUMERIC(12,6)) keeps the boundary
    # honest against float noise: spend+estimate landing ON the cap blocks.
    projected = round(spent + estimate, 6)
    cap_rounded = round(cap, 6)
    if projected >= cap_rounded:
        return BudgetStatus(
            allowed=False,
            spentUsd=spent,
            capUsd=cap,
            reason=(
                f"team '{team_id}' budget exhausted: ${spent:.4f} spent + "
                f"${estimate:.4f} estimated next call exceeds ${cap:.2f} cap"
            ),
        )
    return BudgetStatus(allowed=True, spentUsd=spent, capUsd=cap)


def resolve_team_budget_usd() -> float | None:
    """Deploy-time default cap for wiring (env only; DB column wins per team)."""
    return env_budget_default()
