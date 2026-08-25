"""M18-C2 — ONE budget truth: cap resolution, month window, failure posture.

Commander's intent (Mission 18 lane C2): exactly one answer to "what is this
team's monthly budget cap", one definition of "this month", and one failure
posture when the money store cannot be consulted. This module is that single
answer; every budget consumer resolves caps THROUGH it.

Cap-resolution order (first non-None tier wins):

1. ``teams.budget_usd``                  — per-team override (migration 0007;
                                           NULL = deliberately unset)
2. ``billing_plans.monthly_budget_usd``  — plan row (migration 0010; latest
                                           row wins via ``updated_at``)
3. ``HARNESS_TEAM_BUDGET_USD``           — deploy-wide env default
4. ``None``                              — unlimited, and EXPLICITLY so: the
                                           resolver reports source
                                           ``"unlimited(explicit)"`` instead of a
                                           silent absence. Uncapped is a real,
                                           uniform answer — every consumer allows
                                           spend when it resolves, and labels it
                                           (S4 ``capSource``, S3 predicate allows
                                           the claim, S1 display renders the
                                           unbounded meter).

A stored ``0`` is a REAL cap (everything blocks), not "unset" — only SQL NULL /
missing rows / unusable env values fall through to the next tier.

Consumers (grep ``CAP_RESOLUTION_ORDER`` to find every site that must agree):

- Section 3 atomic guard: ``budget_guard.RESERVE_SQL`` encodes this exact
  order in its headroom predicate (the serialized write path).
- Section 4 orchestration gate: ``budget.check_budget`` reads the two DB tiers
  and delegates to :func:`resolve_cap`.
- Section 1 billing display: ``billing._quota_payload`` reports the same
  resolved cap (previously ``max(plan, team)`` — a fourth, disagreeing truth).

Month window: ONE definition — the DB clock's
``date_trunc('month', now())``, i.e. the current UTC calendar month as the
DATABASE sees it (never the app clock; never all-time). Every monthly
evaluation filters ``created_at >=`` :data:`MONTH_WINDOW_SQL`. SQLite test
mirrors register equivalent ``now()``/``date_trunc`` user functions
(section1 tests/test_budget_atomic.py) so the identical SQL runs verbatim.

Failure posture: ONE — FAIL-CLOSED. A DB error while evaluating a budget cap
must DENY the spend and surface an explicit ``degraded=true`` flag so
operators see degradation instead of mistaking it for a crash. Contracts
carries the wire field name (:data:`DEGRADED_FLAG_WIRE`) and the canonical
reason prefix; section4's gate attaches the flag to its BudgetStatus and the
run-aggregate API response.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

# --------------------------------------------------------------------------
# Cap tiers
# --------------------------------------------------------------------------

ENV_TEAM_BUDGET_USD = "HARNESS_TEAM_BUDGET_USD"

#: Machine-checkable statement of the order — consumers pin their SQL/code
#: against these names so a reorder cannot happen silently in one consumer.
CAP_RESOLUTION_ORDER = (
    "teams.budget_usd",
    "billing_plans.monthly_budget_usd",
    f"env:{ENV_TEAM_BUDGET_USD}",
    "unlimited(explicit)",
)

_MONEY_Q = Decimal("0.000001")  # NUMERIC(12,6) scale — the platform money grid

_UNSET = object()  # sentinel: "caller did not pass an env value; read os.environ"


def parse_env_budget_usd(raw: str | None) -> Decimal | None:
    """Parse one ``HARNESS_TEAM_BUDGET_USD`` value into an exact Decimal cap.

    Unset / blank / garbage / non-finite / negative => ``None`` (the tier is
    ABSENT and resolution falls through) — a config typo must never become a
    runtime error or silently change the meaning of the tier below. A valid
    value clamps at 0 (negative caps are nonsense, not unlimited).
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except Exception:  # noqa: BLE001 — garbage env is an ABSENT tier, logged
        return None
    if not value.is_finite():
        return None
    return max(value, Decimal("0"))


def env_budget_default() -> Decimal | None:
    """Tier 3: today's ``HARNESS_TEAM_BUDGET_USD`` environment value."""
    return parse_env_budget_usd(os.environ.get(ENV_TEAM_BUDGET_USD))


def to_cap_decimal(value: Any) -> Decimal | None:
    """Normalize any stored money shape into an exact NUMERIC(12,6) Decimal.

    asyncpg hands back ``Decimal``, SQLAlchemy/SQLite may hand back float/str/
    int; ``None`` stays ``None`` (tier absent). Floats convert via ``repr``
    (shortest round-trip decimal, H-023 convention) and the result is
    quantized onto the NUMERIC(12,6) grid so comparisons are exact base-10.
    """
    if value is None:
        return None
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return dec.quantize(_MONEY_Q, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class ResolvedCap:
    """Outcome of one cap resolution.

    ``cap_usd is None`` means UNLIMITED — and explicit: ``source`` says so
    rather than leaving the caller to guess whether tiers were skipped.
    """

    cap_usd: Decimal | None
    source: str


def resolve_cap(
    team_override: Any = None,
    plan_monthly_budget: Any = None,
    *,
    env_default: Any = _UNSET,
) -> ResolvedCap:
    """THE cap-resolution function (order pinned by :data:`CAP_RESOLUTION_ORDER`).

    Args:
        team_override: raw ``teams.budget_usd`` cell (any money shape; None =
            unset).
        plan_monthly_budget: raw ``billing_plans.monthly_budget_usd`` (None =
            no row).
        env_default: pre-read env value (str/None) to resolve instead of
            touching ``os.environ`` — tests inject here; omit to read live.

    A ``0`` at any tier WINS (a zero cap blocks everything); only None/absent
    tiers fall through.
    """
    team = to_cap_decimal(team_override)
    if team is not None:
        return ResolvedCap(cap_usd=team, source=CAP_RESOLUTION_ORDER[0])
    plan = to_cap_decimal(plan_monthly_budget)
    if plan is not None:
        return ResolvedCap(cap_usd=plan, source=CAP_RESOLUTION_ORDER[1])
    env_value = (
        parse_env_budget_usd(env_default)
        if env_default is not _UNSET
        else env_budget_default()
    )
    if env_value is not None:
        return ResolvedCap(cap_usd=env_value, source=CAP_RESOLUTION_ORDER[2])
    return ResolvedCap(cap_usd=None, source=CAP_RESOLUTION_ORDER[3])


# --------------------------------------------------------------------------
# Month window — ONE definition
# --------------------------------------------------------------------------

#: The ONLY month-window predicate a monthly cap evaluation may use: the DB
#: clock's current UTC calendar month. Consumers splice this fragment into
#: their WHERE clause (it is deliberately vendor-honest PostgreSQL; SQLite
#: test mirrors register matching user functions). Never substitute an
#: app-clock month start — clock skew between app and DB would create two
#: windows over one ledger.
MONTH_WINDOW_SQL = "created_at >= date_trunc('month', now())"


# --------------------------------------------------------------------------
# Failure posture — ONE, fail-closed, operator-visible
# --------------------------------------------------------------------------

#: Wire field every budget-bearing API response uses to declare "the money
#: store was unreachable, the cap decision degraded to DENY".
DEGRADED_FLAG_WIRE = "degraded"

#: Canonical reason string for the degraded denial (consumers may embed it,
#: tests pin it).
DEGRADED_REASON = "budget store unavailable — failing closed"


__all__ = [
    "CAP_RESOLUTION_ORDER",
    "DEGRADED_FLAG_WIRE",
    "DEGRADED_REASON",
    "ENV_TEAM_BUDGET_USD",
    "MONTH_WINDOW_SQL",
    "ResolvedCap",
    "env_budget_default",
    "parse_env_budget_usd",
    "resolve_cap",
    "to_cap_decimal",
]
