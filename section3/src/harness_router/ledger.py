"""Wave 9 node A — cost metering: real writers for the frozen ``cost_ledger``.

``record_usage`` computes cost via the existing ``PricingEngine`` and INSERTs
one row into Section 1's frozen schema::

    cost_ledger(seq BIGSERIAL PK, run_id FK agent_runs NULLABLE,
                agent_role VARCHAR(20), model VARCHAR(120),
                input_tokens INT, output_tokens INT,
                cost_usd NUMERIC(12,6), latency_ms INT,
                created_at TIMESTAMPTZ DEFAULT now())

Fire-and-forget contract: ledger failures are logged and swallowed — they
must NEVER propagate into the model-call path. Timestamps are bound as
``datetime`` objects (asyncpg rejects ISO strings for TIMESTAMPTZ) and
``now()`` lives in the column default, never in our SQL. The DB pool is
duck-typed (asyncpg pool in prod; fakes in tests), so this module has no
hard driver import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from harness_contracts.signatures import ModelRole

from harness_router.pricing import PricingEngine

logger = logging.getLogger(__name__)

# Param order: run_id, agent_role, model, input_tokens, output_tokens,
#              cost_usd, latency_ms. created_at comes from the column default;
# seq from BIGSERIAL. Positional $n placeholders are canonical asyncpg.
INSERT_USAGE_SQL = (
    "INSERT INTO cost_ledger "
    "(run_id, agent_role, model, input_tokens, output_tokens, cost_usd, latency_ms) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7) "
    "RETURNING seq, created_at"
)

# One parameterized GROUP BY statement; optional filters are expressed as
# "$n IS NULL OR ..." so a single prepared statement serves every
# team/project/since combination. team/project arrive as text ids compared
# against their uuid columns cast to text.
SPEND_SQL = (
    "SELECT l.model AS model, "
    "COALESCE(SUM(l.input_tokens), 0) AS input_tokens, "
    "COALESCE(SUM(l.output_tokens), 0) AS output_tokens, "
    "COALESCE(SUM(l.cost_usd), 0) AS cost_usd, "
    "date_trunc('day', l.created_at) AS day "
    "FROM cost_ledger l "
    "LEFT JOIN agent_runs r ON l.run_id = r.id "
    "LEFT JOIN projects p ON r.project_id = p.id "
    "WHERE ($1::text IS NULL OR p.team_id::text = $1) "
    "AND ($2::text IS NULL OR p.id::text = $2) "
    "AND ($3::timestamptz IS NULL OR l.created_at >= $3) "
    "GROUP BY l.model, date_trunc('day', l.created_at) "
    "ORDER BY day ASC, l.model ASC"
)


@dataclass(frozen=True)
class LedgerRow:
    """One recorded usage row (mirrors the frozen cost_ledger columns)."""

    seq: int | None
    run_id: str | None
    agent_role: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    created_at: datetime | None


def _role_str(role: ModelRole | str) -> str:
    """ModelRole is a str Enum; persist its bare value ('primary'), not repr."""
    value = getattr(role, "value", role)
    return str(value)


def build_usage_params(
    *,
    role: ModelRole | str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    run_id: str | None = None,
) -> tuple[Any, ...]:
    """Bind-parameters tuple for INSERT_USAGE_SQL, in $1..$7 order.

    Exposed so tests can assert exact bind shapes without touching a driver.
    """
    cost_usd = PricingEngine.calculate_cost(model, input_tokens, output_tokens)
    return (
        run_id,
        _role_str(role)[:20],  # agent_role VARCHAR(20)
        model[:120],  # model VARCHAR(120)
        int(input_tokens),
        int(output_tokens),
        round(float(cost_usd), 6),  # NUMERIC(12,6)
        int(latency_ms),
    )


async def record_usage(
    pool: Any,
    agent_id: str,
    role: ModelRole | str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    run_id: str | None = None,
) -> LedgerRow | None:
    """Insert one cost_ledger row; NEVER raises into the caller.

    Returns the persisted row on success, ``None`` on any ledger failure
    (which is logged at warning level instead of propagating).
    """
    try:
        params = build_usage_params(
            role=role,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            run_id=run_id,
        )
        row = await pool.execute(INSERT_USAGE_SQL, *params)
        return LedgerRow(
            seq=int(row[0]) if row else None,
            run_id=str(params[0]) if params[0] is not None else None,
            agent_role=str(params[1]),
            model=str(params[2]),
            input_tokens=int(params[3]),
            output_tokens=int(params[4]),
            cost_usd=float(params[5]),
            latency_ms=int(params[6]),
            created_at=row[1] if row and len(row) > 1 else None,
        )
    except Exception:
        logger.warning("cost_ledger write failed; continuing without metering", exc_info=True)
        return None


def _since_cutoff(since_days: int | None) -> datetime | None:
    if not since_days or since_days <= 0:
        return None
    return datetime.now(UTC) - timedelta(days=since_days)


def summarize_spend(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Fold per-(model, day) rows into the wire shape (camelCase).

    Row shape: (model, input_tokens, output_tokens, cost_usd, day) where day
    is a datetime from ``date_trunc('day', ...)``.
    """
    total_in = 0
    total_out = 0
    total_cost = 0.0
    by_model: dict[str, dict[str, Any]] = {}
    by_day: dict[str, float] = {}
    for model, input_tokens, output_tokens, cost_usd, day in rows:
        day_key = day.date().isoformat() if isinstance(day, datetime) else str(day or "unknown")
        total_in += int(input_tokens or 0)
        total_out += int(output_tokens or 0)
        total_cost += float(cost_usd or 0)
        entry = by_model.setdefault(model, {"model": model, "costUsd": 0.0, "tokens": 0})
        entry["costUsd"] = round(entry["costUsd"] + float(cost_usd or 0), 6)
        entry["tokens"] += int(input_tokens or 0) + int(output_tokens or 0)
        by_day[day_key] = round(by_day.get(day_key, 0.0) + float(cost_usd or 0), 6)
    return {
        "totals": {
            "inputTokens": total_in,
            "outputTokens": total_out,
            "costUsd": round(total_cost, 6),
        },
        "byModel": sorted(by_model.values(), key=lambda e: -e["costUsd"]),
        "byDay": [{"day": d, "costUsd": c} for d, c in sorted(by_day.items())],
    }


async def spend_summary(
    pool: Any,
    *,
    team_id: str | None = None,
    project_id: str | None = None,
    since_days: int | None = None,
) -> dict[str, Any]:
    """Aggregate spend for optional team/project filters over N days.

    The cutoff is bound as a timezone-aware ``datetime`` (never an ISO string).
    """
    rows = await pool.fetch(SPEND_SQL, team_id, project_id, _since_cutoff(since_days))
    return summarize_spend([tuple(r) for r in rows])


__all__ = [
    "INSERT_USAGE_SQL",
    "LedgerRow",
    "SPEND_SQL",
    "build_usage_params",
    "record_usage",
    "spend_summary",
    "summarize_spend",
]
