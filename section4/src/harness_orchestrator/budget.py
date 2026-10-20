"""Wave 9 node B — per-team budget caps enforced BEFORE any model call.

M18-C2 (ONE budget truth): this module is Section 4's consumer of the single
cap resolver in ``harness_contracts.budget``. Cap resolution order (first
non-None tier wins; pinned by ``CAP_RESOLUTION_ORDER``):

1. per-team override from ``teams.budget_usd``   (migration 0007, NULL = unset)
2. plan row ``billing_plans.monthly_budget_usd`` (migration 0010)
3. ``HARNESS_TEAM_BUDGET_USD`` environment default
4. None = unlimited — and EXPLICIT: ``status.capSource == "unlimited(explicit)"``

The month window is the ONE definition (``MONTH_WINDOW_SQL``): DB-clock
``date_trunc('month', now())`` on every monthly evaluation — never all-time,
never an app-computed month start.

Failure posture is ONE — FAIL-CLOSED with an explicit operator-visible flag:
any DB error while resolving a cap or summing spend denies the call
(``allowed=False``, reason prefix from ``DEGRADED_REASON``) and sets
``degraded=True`` so callers surface degradation instead of mistaking it for
an honest cap decision (service.py mirrors it into the run-aggregate API
response).

M18-C3 (O(1) cost-gate latency kill): when migration 0027's
``cost_ledger_daily`` rollup has coverage for this team, closed-day spend
comes from <= N single-row PK lookups and only TODAY's partial day is summed
live through the attribution join (idx_cost_ledger_created_at bounds that
scan) — latency independent of ledger size, exact at the day boundary. While
the rollup is empty/unfed the exact legacy whole-month SUM below remains the
fallback, so correctness never depends on sidecar freshness; any rollup-path
error also degrades to it. Both halves honor the ONE month window: rollup
days before this month's DB-clock start are excluded, so last month's spend
never leaks across the cap boundary.

M23-B (session-2 area 9 — budget warnings): the gate no longer only blocks
at 100% — an ALLOWED decision near the cap now carries ``warned=True`` with
the threshold that was crossed (``warnAtUsd``, default
``HARNESS_TEAM_BUDGET_WARN_PCT``=80 of the resolved cap; 0 or >=100
disables). The service logs the warning once per run and surfaces it on the
run-aggregate API, so an operator sees the walk toward the cap BEFORE the
hard stop — the alert the register flagged as missing.

All statements run identically on PostgreSQL/asyncpg (prod) and
SQLite/aiosqlite (unit tests): no vendor-specific SQL beyond the registered
``date_trunc``/``now()`` user functions the test mirrors provide, and
timestamps are bound as datetime objects (asyncpg rejects ISO strings for
TIMESTAMPTZ).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from harness_contracts import (
    CAP_RESOLUTION_ORDER,
    DEGRADED_REASON,
    ENV_TEAM_BUDGET_USD,
    MONTH_WINDOW_SQL,
    ResolvedCap,
    cap_hit_wire,
    env_budget_default,
    resolve_cap,
    tier_caps,
    to_cap_decimal,
)
from harness_contracts.timeutil import db_now

from harness_orchestrator import notify

logger = logging.getLogger(__name__)

# Re-exported for wiring/tests (M18-C2 shim): Section 4 code keeps importing
# these budget names from harness_orchestrator.budget while the truth lives
# in the frozen contracts package. Single source of each constant.
__all__ = [
    "BudgetStatus",
    "CAP_RESOLUTION_ORDER",
    "DEFAULT_BUDGET_WARN_PCT",
    "DEFAULT_ESTIMATED_CALL_COST_USD",
    "DEGRADED_REASON",
    "ENV_BUDGET_WARN_PCT",
    "ENV_TEAM_BUDGET_USD",
    "MONTH_WINDOW_SQL",
    "ResolvedCap",
    # M22-M2 (additive): tier gate — runs/day volume cap ahead of budget.
    "TierGateStatus",
    "budget_warn_pct",
    "check_budget",
    "check_tier",
    "deliver_budget_warning",
    "reserve_daily_run",
    "env_budget_default",
    "estimate_next_call_cost_usd",
    "resolve_cap",
    "resolve_team_budget_usd",
]

# M23-B: warn BEFORE the hard stop. Percentage of the resolved cap; the
# valid window is strictly (0, 100) — anything else disables warnings
# (documented escape hatch, never the default).
ENV_BUDGET_WARN_PCT = "HARNESS_TEAM_BUDGET_WARN_PCT"
DEFAULT_BUDGET_WARN_PCT = 80.0


def budget_warn_pct() -> float:
    """Warning threshold as a percentage of the cap; 0.0 = disabled.

    Malformed env falls back to the 80% default; values outside (0, 100)
    disable warnings explicitly.
    """
    raw = os.environ.get(ENV_BUDGET_WARN_PCT, "")
    try:
        pct = float(raw) if raw.strip() else DEFAULT_BUDGET_WARN_PCT
    except ValueError:
        return DEFAULT_BUDGET_WARN_PCT
    return pct if 0 < pct < 100 else 0.0

# Frozen table names (Section 1 owns them); this module only READS.
#
# M18-C2: the spend window is the ONE month definition — the literal
# ``created_at >= date_trunc('month', now())`` fragment from MONTH_WINDOW_SQL
# is spliced in verbatim so a drift between this file and the contract fails
# the parity test instead of shipping two windows.
_SPEND_SQL = f"""
SELECT COALESCE(SUM(l.cost_usd), 0) AS spent_usd
FROM cost_ledger l
JOIN agent_runs r ON l.run_id = r.id
JOIN projects p ON r.project_id = p.id
JOIN teams t ON p.team_id = t.id
WHERE t.id = :team_id
  AND l.{MONTH_WINDOW_SQL}
"""

_CAP_OVERRIDE_SQL = "SELECT budget_usd FROM teams WHERE id = :team_id"

_PLAN_BUDGET_SQL = """
SELECT monthly_budget_usd FROM billing_plans WHERE team_id = :team_id
"""

# M22-M2: the team's TIER (billing_plans.plan, migration 0010) plus its
# optional per-team cap overrides (migration 0031). Read alongside the budget
# row so the tier gate costs no extra round-trip.
_TIER_CAPS_SQL = """
SELECT plan, runs_per_day_override, context_index_max_chunks_override
FROM billing_plans WHERE team_id = :team_id
"""

DEFAULT_ESTIMATED_CALL_COST_USD = 0.05

# M22-M2: the tier gate answers BEFORE the budget gate in the run-start
# chain (service._budget_gate). A tier denial is a VOLUME cap (upgrade
# sells relief); a budget denial is SPEND (M18-C2). Both block; only the
# tier one carries the structured upgrade CTA.


@dataclass(frozen=True)
class TierGateStatus:
    """Wire fields camelCase per api-conventions.md (mirrors BudgetStatus).

    ``allowed=False`` means a TIER cap refused the run. ``capHit`` then
    carries the exact structured CTA payload from
    harness_contracts.cap_hit_wire ({tier, cap, capField, used, upgradeTo,
    cta}) — never None: a bare tier denial without upgrade info is the dead
    end this gate exists to prevent. ``degraded=True`` keeps ONE failure
    posture with the budget gate (fail-closed on an unreadable store).
    """

    allowed: bool
    tier: str = "free"
    reason: str = ""
    degraded: bool = False
    capHit: dict | None = None


async def _runs_today(db, team_id: str) -> int:
    """Count THIS TEAM's runs created today by DB clock.

    The day window is ``r.created_at >= date_trunc('day', now())`` — same
    DB-clock discipline as MONTH_WINDOW_SQL (never an app-computed boundary).
    agent_runs has no team column; ownership rides the frozen join chain
    projects -> teams exactly like the spend SUM above.

    Scratch mirrors predating the created_at column (0006-era fixtures)
    return 0 — an ABSENT schema is not an outage and must not fail the gate
    closed for every legacy fixture.
    """
    from sqlalchemy import text

    try:
        row = (
            await db.execute(
                text(
                    "SELECT COUNT(*) AS n FROM agent_runs r "
                    "JOIN projects p ON r.project_id = p.id "
                    "JOIN teams t ON p.team_id = t.id "
                    "WHERE t.id = :team_id "
                    "AND r.created_at >= date_trunc('day', now())"
                ),
                {"team_id": team_id},
            )
        ).first()
    except Exception:  # noqa: BLE001 — absent mirror column ⇒ honest zero
        logger.debug("runs/day count unavailable for %s; treating as 0", team_id)
        if db.in_transaction():
            await db.rollback()
        return 0
    return int(row.n or 0)


def _decide_tier(caps, used_today: int):
    """Compare today's usage against the resolved tier caps.

    Returns None when allowed; otherwise the (cap_field, cap, used) triple of
    the FIRST cap hit — runs/day is evaluated before index size because it is
    the run-START concern (the index cap is enforced at the S11 API).
    """
    if caps.runs_per_day is not None and used_today >= caps.runs_per_day:
        return ("runsPerDay", caps.runs_per_day, used_today)
    return None


async def check_tier(
    factory,
    team_id: str,
    *,
    env: dict[str, str] | None = None,
) -> TierGateStatus:
    """Tier gate: does this team's PLAN still allow another run TODAY?

    Reads billing_plans.plan (+ 0031 override columns) and counts today's
    runs. Absent/unreadable plan rows mean the free-tier DEFAULTS apply —
    enforcement must not depend on a PUT /billing/plan having happened.
    Failure posture is the platform's ONE posture: FAIL-CLOSED with an
    explicit ``degraded=True`` flag (an outage must never open the gate).
    """
    from sqlalchemy import text

    try:
        async with factory() as db:
            # Absent-tier semantics mirror check_budget's billing_plans read:
            # a missing table (pre-0010 / scratch mirrors) is NOT an outage —
            # the default tier applies.
            try:
                row = (
                    await db.execute(text(_TIER_CAPS_SQL), {"team_id": team_id})
                ).first()
            except Exception:  # noqa: BLE001 — absent tier, not an outage
                logger.debug("billing_plans unreadable for %s; tier defaults", team_id)
                row = None
                if db.in_transaction():
                    await db.rollback()

            overrides = (
                (
                    int(row.runs_per_day_override)
                    if row.runs_per_day_override is not None
                    else None,
                    (
                        int(row.context_index_max_chunks_override)
                        if row.context_index_max_chunks_override is not None
                        else None
                    ),
                )
                if row is not None
                else (None, None)
            )
            caps = tier_caps(
                row.plan if row is not None else "free",
                env=env,
                runs_per_day_override=overrides[0],
                context_index_max_chunks_override=overrides[1],
            )

            used_today = await _runs_today(db, team_id)
    except Exception:  # noqa: BLE001 — M18-C2 posture: DB error ⇒ fail-closed
        logger.exception("tier check could not read plan/runs for team %s", team_id)
        return TierGateStatus(
            allowed=False,
            degraded=True,
            reason=f"{DEGRADED_REASON} (team '{team_id}')",
        )

    hit = _decide_tier(caps, used_today)
    if hit is None:
        return TierGateStatus(allowed=True, tier=caps.tier)
    cap_field, cap, used = hit
    message = (
        (
            f"Free plan limit reached: {used} of {cap} daily runs used."
            " Upgrade to Pro for unlimited runs."
        )
        if caps.upgrade_to
        else f"Daily run limit reached: {used} of {cap} runs used."
    )
    return TierGateStatus(
        allowed=False,
        tier=caps.tier,
        reason=f"tier cap reached: {cap_field} {used}/{cap} on plan '{caps.tier}'",
        capHit=cap_hit_wire(
            tier=caps.tier,
            cap=cap,
            cap_field=cap_field,
            used=used,
            message=message,
        ),
    )


async def reserve_daily_run(factory, team_id: str, run_id: str) -> TierGateStatus:
    """Atomically reserve this run against the team's DB-clock daily cap.

    The unique run id makes retries idempotent. A team-scoped transaction lock
    serializes PostgreSQL reservations; legacy runs created before this table
    are counted once as unreserved rows during the cutover day.
    """
    from sqlalchemy import text

    try:
        async with factory() as db:
            dialect = db.bind.dialect.name
            if dialect == "postgresql":
                await db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:team_id, 1))"),
                    {"team_id": team_id},
                )
                day_start = "date_trunc('day', now())"
            elif dialect == "sqlite":
                await db.execute(text("BEGIN IMMEDIATE"))
                day_start = "date('now')"
            else:
                raise RuntimeError(f"unsupported database dialect for daily cap: {dialect}")
            existing = (
                await db.execute(
                    text("SELECT 1 FROM team_daily_run_reservations WHERE run_id=:r"),
                    {"r": run_id},
                )
            ).first()
            row = (
                await db.execute(text(_TIER_CAPS_SQL), {"team_id": team_id})
            ).first()
            caps = tier_caps(
                row.plan if row else "free",
                runs_per_day_override=(
                    int(row.runs_per_day_override)
                    if row and row.runs_per_day_override is not None
                    else None
                ),
            )
            if existing:
                await db.commit()
                return TierGateStatus(allowed=True, tier=caps.tier)
            used = int((await db.execute(text(
                "SELECT COUNT(*) FROM team_daily_run_reservations "
                f"WHERE team_id=:t AND reserved_at >= {day_start}"
            ), {"t": team_id})).scalar_one())
            hit = _decide_tier(caps, used)
            if hit:
                await db.rollback()
                _, cap, current = hit
                message = (
                    f"Free plan limit reached: {current} of {cap} daily runs used."
                    " Upgrade to Pro for unlimited runs."
                    if caps.upgrade_to
                    else f"Daily run limit reached: {current} of {cap} runs used."
                )
                return TierGateStatus(
                    allowed=False,
                    tier=caps.tier,
                    reason=f"Daily run limit reached: {current} of {cap} runs used.",
                    capHit=cap_hit_wire(
                        tier=caps.tier, cap=cap, cap_field="runsPerDay",
                        used=current, message=message,
                    ),
                )
            await db.execute(text(
                "INSERT INTO team_daily_run_reservations (team_id, run_id, reserved_at) "
                "VALUES (:t,:r,CURRENT_TIMESTAMP)"
            ), {"t": team_id, "r": run_id})
            await db.commit()
            return TierGateStatus(allowed=True, tier=caps.tier)
    except Exception as exc:
        missing_schema = ("no such table", "does not exist", "undefinedtable")
        if any(token in str(exc).lower() for token in missing_schema):
            logger.debug("daily reservation migration absent; using legacy tier check")
            return await check_tier(factory, team_id)
        logger.exception("daily run reservation failed for team %s", team_id)
        return TierGateStatus(
            allowed=False, degraded=True,
            reason=f"{DEGRADED_REASON} (daily run reservation for team '{team_id}')",
        )


@dataclass(frozen=True)
class BudgetStatus:
    """Wire fields are camelCase per api-conventions.md; python stays snake_case.

    ``degraded=True`` (M18-C2): the money store was unreachable while this
    decision was being made. The decision itself is FAIL-CLOSED
    (``allowed=False``); operators watch this flag to distinguish a real cap
    from an outage-shaped denial.

    ``warned=True`` (M23-B): the call was ALLOWED but the projected spend
    crossed the warning threshold (``warnAtUsd``) on the way to the cap.
    Never set alongside a denial — the block IS the story then.
    """

    allowed: bool
    spentUsd: float
    capUsd: float | None
    reason: str = ""
    degraded: bool = False
    capSource: str | None = None
    warned: bool = False
    warnAtUsd: float | None = None


def _coerce_cap(value) -> float | None:
    """NUMERIC comes back as Decimal on asyncpg / float on sqlite; normalize."""
    dec = to_cap_decimal(value)
    return None if dec is None else float(dec)


def _app_clock_month_start_utc() -> str:
    """SQLite-mirror fallback for the ONE month window.

    Production always evaluates MONTH_WINDOW_SQL on the DB clock; this helper
    exists ONLY so scratch SQLite mirrors that never registered the
    date_trunc/now() user functions still get identical month semantics via a
    bound parameter. Returns an ISO string (SQLite TEXT comparison, same
    convention the ephemeral stores use).
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.strftime("%Y-%m-%d %H:%M:%S")


def resolve_team_budget_usd() -> float | None:
    """Deploy-time default cap for wiring (env tier of the ONE resolution)."""
    value = env_budget_default()
    return None if value is None else float(value)


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


def _decide(
    resolved: ResolvedCap,
    spent: float,
    estimate: float,
    warn_pct: float | None = None,
) -> BudgetStatus:
    """Compare projected month-to-date spend against the RESOLVED cap.

    M23-B: an allowed decision also reports whether the projection crossed
    the warning threshold (same micro-dollar rounding discipline as the cap
    boundary, so warn-at-80% and block-at-100% agree at the edge).
    """
    if resolved.cap_usd is None:
        # Explicit unlimited — a real resolution, not an absent one.
        return BudgetStatus(
            allowed=True,
            spentUsd=spent,
            capUsd=None,
            capSource=resolved.source,
        )
    cap = float(resolved.cap_usd)
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
                f"team budget exhausted: ${spent:.4f} spent + "
                f"${estimate:.4f} estimated next call exceeds ${cap:.2f} cap"
            ),
            capSource=resolved.source,
        )
    pct = budget_warn_pct() if warn_pct is None else warn_pct
    warned = False
    warn_at: float | None = None
    if 0 < pct < 100:
        warn_at = round(cap * pct / 100.0, 6)
        warned = projected >= warn_at
    return BudgetStatus(
        allowed=True,
        spentUsd=spent,
        capUsd=cap,
        capSource=resolved.source,
        warned=warned,
        warnAtUsd=warn_at,
    )


def _degraded_denial(team_id: str) -> BudgetStatus:
    """THE failure posture: deny + explicit degraded flag (M18-C2)."""
    logger.warning("budget gate degraded for team %s: %s", team_id, DEGRADED_REASON)
    return BudgetStatus(
        allowed=False,
        spentUsd=0.0,
        capUsd=None,
        reason=f"{DEGRADED_REASON} (team '{team_id}')",
        degraded=True,
    )


async def _month_to_date_spend(db, team_id: str) -> float:
    """Month-to-date team spend (M18-C3 fast path over the M18-C2 window).

    Covered team ⇒ closed days of THIS month from the ``cost_ledger_daily``
    rollup (one row per day, PK lookups) plus TODAY summed live through the
    attribution join. Not covered ⇒ the exact legacy whole-month SUM. Any
    rollup-path error falls back to the exact SUM — the sidecar is an
    optimization, never a correctness dependency.
    """
    from sqlalchemy import text

    try:
        rows = (
            await db.execute(
                text(
                    "SELECT d.total_usd FROM cost_ledger_daily d "
                    "WHERE d.team_id = :team_id "
                    "AND d.day >= date_trunc('month', now())"
                ),
                {"team_id": team_id},
            )
        ).fetchall()
    except Exception:  # noqa: BLE001 — missing/unreadable sidecar ⇒ exact path
        logger.debug("rollup read unavailable for %s; using exact SUM", team_id)
        rows = []
        # The sidecar read failed INSIDE the same session; SQLite (and PG)
        # abort the current transaction on error, so the fallback needs a
        # clean one. Roll back if a transaction is still open.
        if db.in_transaction():
            await db.rollback()

    if not rows:
        # SQLite scratch mirrors (section4 unit tests) predate the registered
        # date_trunc/now() user functions: degrade to a bound-parameter window
        # computed on the app clock — same semantics, no dialect dependency.
        try:
            result = await db.execute(text(_SPEND_SQL), {"team_id": team_id})
            return _coerce_cap(result.scalar_one()) or 0.0
        except Exception:
            if db.in_transaction():
                await db.rollback()
            month_start = _app_clock_month_start_utc()
            result = await db.execute(
                text(
                    "SELECT COALESCE(SUM(l.cost_usd), 0) AS spent_usd "
                    "FROM cost_ledger l "
                    "JOIN agent_runs r ON l.run_id = r.id "
                    "JOIN projects p ON r.project_id = p.id "
                    "WHERE p.team_id = :team_id AND l.created_at >= :since"
                ),
                {"team_id": team_id, "since": month_start},
            )
            return _coerce_cap(result.scalar_one()) or 0.0

    closed_total = 0.0
    for row in rows:
        value = _coerce_cap(row[0])
        if value is not None:
            closed_total += value

    # Today's partial day stays LIVE (the sidecar never stores today), bounded
    # by idx_cost_ledger_created_at instead of scanning the whole ledger.
    live_row = (
        await db.execute(
            text(
                "SELECT COALESCE(SUM(l.cost_usd), 0) AS spent_usd "
                "FROM cost_ledger l "
                "JOIN agent_runs r ON l.run_id = r.id "
                "JOIN projects p ON r.project_id = p.id "
                "WHERE p.team_id = :team_id "
                "AND l.created_at >= date_trunc('day', now())"
            ),
            {"team_id": team_id},
        )
    ).scalar_one()
    return closed_total + (_coerce_cap(live_row) or 0.0)


async def check_budget(
    factory,
    team_id: str,
    *,
    budget_usd: float | None = None,
    estimated_call_cost_usd: float | None = None,
    model_router=None,
    reviewer_role=None,
) -> BudgetStatus:
    """Sum the team's THIS-MONTH ledger spend + next-call estimate vs its cap.

    Args:
        factory: SQLAlchemy async session factory (same duck-type the
            orchestrator already uses for run_queue/rehydration).
        team_id: owning team.
        budget_usd: pre-resolved cap override (callers that already hold the
            resolved value); when omitted, the two DB tiers are read and the
            ONE resolver order applies.
        estimated_call_cost_usd: explicit next-call estimate; computed via
            ``estimate_next_call_cost_usd`` when omitted.
        model_router / reviewer_role: optional contract router used only for
            the next-call cost estimate.

    Fail-CLOSED (M18-C2): any DB error denies the call with ``degraded=True``
    — an outage must never open the gate, and the flag makes the degradation
    visible instead of crash-shaped.
    """
    from sqlalchemy import text

    team_override = None
    plan_budget = None
    warning_pct = None
    spent = 0.0

    try:
        async with factory() as db:
            if budget_usd is None:
                row = (
                    await db.execute(text(_CAP_OVERRIDE_SQL), {"team_id": team_id})
                ).first()
                team_override = getattr(row, "budget_usd", None) if row is not None else None
                # M18-C2 tiers 2-4 with ABSENT-TIER semantics: a missing
                # billing_plans table (pre-0010 / scratch mirrors) means the
                # tier is absent — resolution falls through, the store is NOT
                # degraded. A genuinely broken store (dead connection) still
                # fails closed below.
                try:
                    plan_row = (
                        await db.execute(text(_PLAN_BUDGET_SQL), {"team_id": team_id})
                    ).first()
                    plan_budget = plan_row[0] if plan_row is not None else None
                except Exception:  # noqa: BLE001 — absent tier, not an outage
                    logger.debug(
                        "billing_plans unreadable for %s; skipping tier 2", team_id
                    )
                    plan_budget = None
                    if db.in_transaction():
                        await db.rollback()
                try:
                    warning_row = (
                        await db.execute(
                            text(
                                "SELECT budget_warn_pct_override FROM billing_plans "
                                "WHERE team_id = :team_id"
                            ),
                            {"team_id": team_id},
                        )
                    ).first()
                    if warning_row is not None and warning_row[0] is not None:
                        warning_pct = float(warning_row[0])
                except Exception:  # noqa: BLE001 — old scratch schemas use the env default
                    if db.in_transaction():
                        await db.rollback()
            try:
                spent = await _month_to_date_spend(db, team_id)
            except Exception:
                if db.in_transaction():
                    await db.rollback()
                raise
    except Exception:  # noqa: BLE001 — M18-C2: DB error ⇒ FAIL-CLOSED + flag
        logger.exception("budget check could not read spend for team %s", team_id)
        return _degraded_denial(team_id)

    if budget_usd is not None:
        # Caller-supplied override short-circuits the tiers deliberately
        # (tests / callers that already resolved); it IS tier-1 semantics.
        resolved = ResolvedCap(cap_usd=to_cap_decimal(budget_usd), source="caller-override")
    else:
        resolved = resolve_cap(team_override, plan_budget)
    assert resolved.source in (*CAP_RESOLUTION_ORDER, "caller-override")
    return _decide(resolved, spent, (
        estimated_call_cost_usd
        if estimated_call_cost_usd is not None
        else estimate_next_call_cost_usd(model_router=model_router, role=reviewer_role)
    ), warn_pct=warning_pct)


async def deliver_budget_warning(factory, team_id: str, code: str = "budget_warning") -> None:
    """Part 57.005: tell the team's billing managers that spend crossed the warning line, or with
    ``code="budget_exhausted"`` that the monthly cap blocked a run, or with
    ``code="plan_limit_reached"`` that the daily run cap did (never for a degraded denial).

    Billing managers follow section1 ``billing._billing_manager``: team admins, or for a team in an
    org the org's owners and billing admins. In-app, once per person per team per UTC month (per
    UTC day for the daily cap; the dedupe key, one per code); email only if that person opted in.
    An org manager with no seat in the capped team gets the row in their first other team of the
    org (the inbox only shows the team a person is signed in to); one with no seat in the org's
    teams has nowhere to see it. Never raises into the run.
    """
    from sqlalchemy import text

    try:
        async with factory() as db:
            try:
                async with db.begin_nested():
                    org_id = (
                        await db.execute(
                            text("SELECT org_id FROM teams WHERE id = :t"), {"t": team_id}
                        )
                    ).scalar_one_or_none()
            except Exception:  # noqa: BLE001 — pre-0038 store: no org linkage, team admins decide
                org_id = None
            if org_id:
                rows = await db.execute(
                    text(
                        "SELECT m.user_id, (SELECT x.team_id FROM team_members x JOIN teams tt"
                        " ON tt.id = x.team_id WHERE x.user_id = m.user_id AND tt.org_id = :o"
                        " ORDER BY CASE WHEN x.team_id = :t THEN 0 ELSE 1 END, x.team_id"
                        " LIMIT 1) FROM org_members m JOIN organizations o"
                        " ON o.id = m.org_id WHERE m.org_id = :o"
                        " AND m.role IN ('owner', 'billing_admin')"
                        " AND m.removed_at IS NULL AND o.status = 'active'"
                    ),
                    {"o": org_id, "t": team_id},
                )
            else:
                rows = await db.execute(
                    text(
                        "SELECT user_id, team_id FROM team_members WHERE team_id = :t"
                        " AND lower(role) = 'admin' AND user_id IS NOT NULL"
                    ),
                    {"t": team_id},
                )
            period = "%Y-%m-%d" if code == "plan_limit_reached" else "%Y-%m"
            key = f"{code.replace('_', '-')}:{team_id}:{db_now().strftime(period)}"
            for user_id, home_team in sorted({(r[0], r[1]) for r in rows if r[1]}):
                await notify.emit(
                    db, team_id=home_team, user_id=user_id, kind="budget", source_kind="team",
                    source_id=team_id, summary_code=code, dedupe_key=key,
                )
            await db.commit()
    except Exception:  # noqa: BLE001 — a notification must never disturb a run
        logger.warning("%s delivery failed for team %s", code, team_id, exc_info=True)
