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
its own transaction.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# One statement = one decision. The INSERT..SELECT runs under the row locks
# taken by the aggregate scan + the billing_plans read inside a single
# implicit transaction, so concurrent reserves serialize instead of racing:
# each sees all previously COMMITTED ledger rows for the team's month.
#
# Named binds: :run_id, :agent_role, :model, :input_tokens, :output_tokens,
# :cost_usd, :latency_ms, :team_id.
RESERVE_SQL = """
INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens,
                         output_tokens, cost_usd, latency_ms)
SELECT :run_id, :agent_role, :model, :input_tokens,
       :output_tokens, :cost_usd, :latency_ms
WHERE COALESCE((
          SELECT SUM(l.cost_usd)
          FROM cost_ledger l
          JOIN agent_runs r ON r.id = l.run_id
          JOIN projects p ON p.id = r.project_id
          WHERE p.team_id = :team_id
            AND l.created_at >= date_trunc('month', now())
      ), 0) + :cost_usd
      <= (
          SELECT bp.monthly_budget_usd
          FROM billing_plans bp
          WHERE bp.team_id = :team_id
          ORDER BY bp.updated_at DESC
          LIMIT 1
      )
RETURNING seq
"""

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
) -> ReserveResult:
    """Atomically check-and-reserve budget as a cost_ledger row.

    Runs the single-statement INSERT..SELECT..WHERE..RETURNING above against
    the duck-typed asyncpg-style pool. A returned row means the estimate fit
    under the team's remaining monthly budget and is now recorded as spend;
    no row means over budget; any exception fails CLOSED.
    """
    try:
        row = await pool.fetchrow(RESERVE_SQL, {
            "run_id": run_id,
            "agent_role": str(agent_role)[:20],  # agent_role VARCHAR(20)
            "model": str(model)[:120],  # model VARCHAR(120)
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cost_usd": round(float(estimated_cost_usd), 6),
            "latency_ms": int(latency_ms),
            "team_id": team_id,
        })
    except Exception:  # noqa: BLE001 — money path must fail closed
        logger.warning("budget reserve failed; failing closed", exc_info=True)
        return ReserveResult(allowed=False, reason="ledger unavailable")
    if row is None:
        # Predicate false => projected month-to-date + this claim exceeds the
        # plan budget. Nothing was inserted; the caller must not proceed.
        return ReserveResult(allowed=False, reason="over budget")
    return ReserveResult(allowed=True, reason=None, entry_id=int(row["seq"]))


async def release(pool: Any, entry_id: int) -> bool:
    """Compensate a rejected-after-reserve flow by deleting the ledger row.

    Returns True when a row was deleted. Absence (already released / unknown
    id) returns False and is not an error. Never raises into the caller.
    """
    try:
        status = await pool.execute(RELEASE_SQL, {"entry_id": int(entry_id)})
    except Exception:  # noqa: BLE001 — release failure logged, not propagated
        logger.warning("budget release failed", exc_info=True)
        return False
    deleted = getattr(status, "rowcount", None)
    try:
        return bool(deleted) and int(deleted) > 0
    except (TypeError, ValueError):
        return False