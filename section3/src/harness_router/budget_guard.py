"""Mission 11 wave 1 node A3 — atomic budget guard (reserve/release).

The Section 4 ``check_budget`` is a read-then-decide check that is TOCTOU
prone: two concurrent runs can both see headroom and both proceed. This guard
makes the decision ATOMIC by folding the check into the ledger INSERT itself —
a single statement against Postgres::

    INSERT INTO cost_ledger (...) SELECT ... WHERE <headroom predicate>
    RETURNING seq

If no row comes back the team is over budget; nothing was inserted, so there
is no compensation to run. On success the reservation IS the ledger row.

Frozen schema reality: ``cost_ledger`` carries NO ``team_id`` column — team
attribution flows cost_ledger.run_id -> agent_runs.project_id ->
projects.team_id (the same join billing.py uses for its quota read). The
predicate therefore joins through that chain. ``billing_plans`` (migration
0010) supplies monthly_budget_usd; latest-row wins via ORDER BY updated_at.
The pool/session factory is duck-typed exactly like S3's ledger.py (asyncpg
pool in prod, fakes in tests) so this module has no hard driver import and no
new dependencies.

Money-path posture: FAIL-CLOSED. Any ledger DB error => allowed=False with an
explicit reason — never an open gate on infrastructure failure. Rejected work
compensates via release(entry_id), which deletes the reserved ledger row in
its own transaction. Cost estimates outside the NUMERIC(12,6) envelope
(|cost| > 999999.999999) are rejected BEFORE any DB contact with an explicit
reason — never silently dropped, never mislabeled as an outage.

Concurrency (M17-B1): on the production asyncpg path the reserve transaction
first takes pg_advisory_xact_lock over the team, so concurrent reserves
serialize and the $8-vs-$5 overspend is impossible (see RESERVE_SQL below).

Money typing (H-023): ``estimated_cost_usd`` accepts any numeric shape
(Decimal/float/int/str) and is normalized ONCE through :func:`to_money` into
an exact NUMERIC(12,6)-scale Decimal, then bound as TEXT — asyncpg infers
NUMERIC from str and SQLite takes str natively (a raw Decimal bind crashes
sqlite3). The headroom comparison stays entirely inside SQL, where NUMERIC
arithmetic is exact.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from harness_contracts import (
    DEGRADED_REASON,
    ENV_TEAM_BUDGET_USD,
    MONTH_WINDOW_SQL,
    parse_env_budget_usd,
)
from harness_router.ledger import to_money

logger = logging.getLogger(__name__)

# One statement = one decision. The INSERT..SELECT runs the headroom check
# and the ledger INSERT atomically, so a losing claim writes NOTHING (there
# is no compensation to run).
#
# Serialization (M17-B1, M-ALPHA F-2): this statement ALONE does not
# serialize — under READ COMMITTED, N racing reserves snapshot the same
# pre-race SUM and ALL win (live probe: 8x$1 vs $5 => 8 winners, 60%
# overrun). The production asyncpg path therefore takes
# pg_advisory_xact_lock(hashtextextended(team_id)) inside the SAME
# transaction, immediately before this statement (see ADVISORY_LOCK_SQL);
# contenders queue on the lock and each evaluation sees every earlier
# COMMITTED claim. SQLite/SQLAlchemy consumers run the bare statement —
# their suites serialize writers via BEGIN IMMEDIATE and SQLite is
# single-writer anyway — which keeps this file runnable on both backends
# verbatim (pg_* functions must stay OUT of RESERVE_SQL).
#
# Named binds: :run_id, :agent_role, :model, :input_tokens, :output_tokens,
# :cost_usd, :team_id, :env_budget_usd.
#
# Cap truth (M18-C2 — ONE budget truth): the subselect resolves the cap in the
# SAME order every other consumer uses (contracts.CAP_RESOLUTION_ORDER):
# teams.budget_usd > billing_plans.monthly_budget_usd > env
# HARNESS_TEAM_BUDGET_USD > unlimited(explicit). The env tier is resolved in
# PYTHON via contracts.budget.parse_env_budget_usd and bound as
# :env_budget_usd so all three tiers share one parser and SQLite mirrors need
# no SQL-side env access. Unlimited is EXPLICIT and uniform: when no tier is
# set (:env_budget_usd IS NULL AND both columns are NULL) the COALESCE falls
# to +infinity and the claim is ALLOWED — the same "uncapped" answer every
# other consumer resolves, labeled by contracts.budget. A stored 0 at any
# tier WINS (zero cap = block everything); only NULL / missing rows fall
# through to the next tier. The month window is the ONE definition
# (contracts.MONTH_WINDOW_SQL): DB-clock date_trunc('month', now()).
RESERVE_SQL = """
INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens,
                         output_tokens, cost_usd, latency_ms)
SELECT :run_id, :agent_role, :model, :input_tokens,
       :output_tokens, CAST(:cost_usd AS NUMERIC), :latency_ms
WHERE COALESCE((
          SELECT SUM(l.cost_usd)
          FROM cost_ledger l
          JOIN agent_runs r ON r.id = l.run_id
          JOIN projects p ON p.id = r.project_id
          WHERE p.team_id = :team_id
            AND l.created_at >= date_trunc('month', now())
      ), 0) + CAST(:cost_usd AS NUMERIC)
      <= COALESCE(
          (
              SELECT t.budget_usd
              FROM teams t
              WHERE t.id = :team_id AND t.budget_usd IS NOT NULL
          ),
          (
              SELECT bp.monthly_budget_usd
              FROM billing_plans bp
              WHERE bp.team_id = :team_id
              ORDER BY bp.updated_at DESC
              LIMIT 1
          ),
          CAST(:env_budget_usd AS NUMERIC),
          1e30
      )
RETURNING seq
"""

# M18-C2 law: the guard's month window IS the one contracts month window —
# if someone edits either side independently this module refuses to import.
assert MONTH_WINDOW_SQL.split("created_at ")[1] in RESERVE_SQL

# Transaction-scoped team serialization (asyncpg path only). Taken INSIDE the
# same transaction as RESERVE_SQL so the headroom SUM cannot race a concurrent
# reserve: the second contender blocks here until the first commits/rolls
# back. Xact-scoped => self-releasing on commit/rollback/disconnect; no
# session state to clean up. Keyed by 64-bit hash of team_id — collision risk
# across two teams is ~2^-64 and a false share only costs contention, never
# correctness. hashtextextended is stable within a cluster lifetime, which is
# all a lock needs to be.
ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"

# NUMERIC(12,6) envelope: max |value| that fits without overflow.
MONEY_MAX = Decimal("999999.999999")

# Compensation for rejected-after-reserve: remove the reservation row. Single
# DELETE, own transaction, idempotent-by-absence (deleting an already-released
# or never-inserted id affects 0 rows and is not an error).
RELEASE_SQL = """
DELETE FROM cost_ledger WHERE seq = :entry_id
"""


@dataclass(frozen=True)
class ReserveResult:
    """Outcome of an atomic reserve attempt.

    ``allowed=True`` means the ledger row EXISTS and its id is in entry_id —
    the reservation itself is durable spend attribution. ``allowed=False``
    means nothing was written; ``reason`` says why ("over budget" / "ledger
    unavailable").
    """

    allowed: bool
    reason: str | None = None
    entry_id: int | None = None


def _num(value):
    """Best-effort float coercion for NUMERIC results (Decimal/int/str/None)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_positional_params(sql: str, params: Mapping[str, Any]) -> tuple[str, list[Any]]:
    """Translate named ``:bind`` SQL + params to asyncpg ``$n`` positional form.

    asyncpg only accepts positional arguments, so the guard's named-bind SQL
    needs this translation for the production Postgres driver. ``::type``
    casts are preserved untouched; bind order is first-appearance in the SQL.
    """
    ordered: list[str] = []
    seen: dict[str, int] = {}

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in seen:
            seen[name] = len(ordered) + 1
            ordered.append(name)
        return f"${seen[name]}"

    translated = re.sub(r"(?<![:\w]):(\w+)", _sub, sql)
    return translated, [params[name] for name in ordered]


def _is_asyncpg_pool(pool: Any) -> bool:
    """True when ``pool`` is a real asyncpg pool (production driver).

    asyncpg only accepts $n-positional SQL and returns plain strings from
    execute(); SQLAlchemy session adapters take named binds and RowMapping
    status objects. The guard supports both by sniffing the pool's origin
    module — fakes in tests keep the SQLAlchemy shape.
    """
    return pool.__class__.__module__.startswith("asyncpg")


async def reserve(
    pool: Any,
    *,
    team_id: str,
    estimated_cost_usd,
    agent_role: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    run_id: str | None = None,
    env_budget_usd=None,
) -> ReserveResult:
    """Atomically check-and-reserve budget as a cost_ledger row.

    On the production asyncpg driver this runs inside one explicit
    transaction that FIRST takes pg_advisory_xact_lock over ``team_id``
    (ADVISORY_LOCK_SQL), then runs the single-statement INSERT..SELECT..
    WHERE..RETURNING (RESERVE_SQL, binds translated via
    :func:`to_positional_params`). Concurrent reserves serialize on the
    advisory lock and each evaluation sees every earlier committed claim —
    the 8x$1-vs-$5 overspend M-ALPHA probed is impossible. A returned row
    means the estimate fit under the team's remaining monthly budget and is
    now recorded as spend; no row means over budget; any exception fails
    CLOSED.

    Estimates outside the NUMERIC(12,6) envelope (|cost| > MONEY_MAX) are
    rejected before any DB contact with reason "cost out of range" — honest,
    never a silent ledger drop.

    M18-C2: the cap comes from the ONE resolver order (contracts
    CAP_RESOLUTION_ORDER); the env tier is parsed once here via
    ``parse_env_budget_usd`` and bound as ``:env_budget_usd`` so the SQL
    predicate needs no environment access. With no cap tier set anywhere the
    predicate resolves to explicit-unlimited and allows — the same labeled
    answer every consumer resolves; DB errors still fail CLOSED below.
    """
    try:
        # Exact money normalization happens BEFORE the DB sees the value;
        # quantized to NUMERIC(12,6) scale and bound as text (H-023).
        cost_dec = to_money(estimated_cost_usd)
        if abs(cost_dec) > MONEY_MAX:
            logger.warning(
                "budget reserve rejected: cost %s outside NUMERIC(12,6) envelope",
                cost_dec,
            )
            return ReserveResult(allowed=False, reason="cost out of range")
        # Env tier of the ONE cap resolution (M18-C2): explicit kwarg wins,
        # else today's HARNESS_TEAM_BUDGET_USD via the contract parser. None
        # when unset/garbage — the SQL COALESCEs it after the two DB tiers.
        if env_budget_usd is not None:
            env_budget = parse_env_budget_usd(env_budget_usd)
        else:
            env_budget = parse_env_budget_usd(os.environ.get(ENV_TEAM_BUDGET_USD))
        params = {
            "run_id": run_id,
            "agent_role": str(agent_role)[:20],  # agent_role VARCHAR(20)
            "model": str(model)[:120],  # model VARCHAR(120)
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cost_usd": f"{cost_dec:.6f}",
            "latency_ms": int(latency_ms),
            "team_id": team_id,
            # Bound as NUMERIC(12,6) text; NULL + no DB tier means
            # unlimited-explicit (predicate falls to 1e30, claims allowed).
            "env_budget_usd": None if env_budget is None else f"{env_budget:.6f}",
        }
        if _is_asyncpg_pool(pool):
            sql, args = to_positional_params(RESERVE_SQL, params)
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.fetchval(ADVISORY_LOCK_SQL, team_id)
                    row = await conn.fetchrow(sql, *args)
        else:
            # SQLAlchemy / test adapter path: named binds run unmodified.
            row = await pool.fetchrow(RESERVE_SQL, params)
    except Exception:  # noqa: BLE001 — money path must fail closed
        logger.warning("%s", DEGRADED_REASON, exc_info=True)
        return ReserveResult(allowed=False, reason="ledger unavailable")
    if row is None:
        # Predicate false => projected month-to-date + this claim exceeds the
        # resolved cap ("over budget"). Nothing was inserted; the caller must
        # not proceed. (Unlimited-explicit resolves to +Infinity in the
        # predicate, so a refusal always means a REAL cap was hit.)
        return ReserveResult(allowed=False, reason="over budget")
    return ReserveResult(allowed=True, reason=None, entry_id=int(row["seq"]))


async def release(pool: Any, entry_id: int) -> bool:
    """Compensate a rejected-after-reserve flow by deleting the ledger row.

    Returns True when a row was deleted. Absence (already released / unknown
    id) returns False and is not an error. Never raises into the caller.
    """
    try:
        if _is_asyncpg_pool(pool):
            sql, args = to_positional_params(RELEASE_SQL, {"entry_id": int(entry_id)})
            status = await pool.execute(sql, *args)  # e.g. the string "DELETE 1"
            match = re.search(r"(\d+)\s*$", str(status))
            return bool(match and int(match.group(1)) > 0)
        status = await pool.execute(RELEASE_SQL, {"entry_id": int(entry_id)})
        deleted = getattr(status, "rowcount", None)
        try:
            return bool(deleted) and int(deleted) > 0
        except (TypeError, ValueError):
            return False
    except Exception:  # noqa: BLE001 — release failure logged, not propagated
        logger.warning("budget release failed", exc_info=True)
        return False
