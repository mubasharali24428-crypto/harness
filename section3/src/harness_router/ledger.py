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

Money typing (H-023): every money value is a ``Decimal`` end to end.
``cost_usd`` is BOUND AS TEXT — asyncpg infers NUMERIC from str and SQLite
(numeric-affinity columns in scratch test schemas) stores it exactly — and
read back via :func:`to_money`, which accepts whatever the driver returns
(Decimal from Postgres, str/float/int from SQLite). Aggregation sums Decimals;
the wire keeps JSON numbers per contract M10-C3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
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

_MONEY_Q = Decimal("0.000001")
# NUMERIC(12,6) envelope (M17-B1 / M-ALPHA F-4): max |value| the column can
# store. Anything larger raises NumericValueOutOfRangeError on Postgres and
# used to vanish into record_usage's blanket except — silent metering loss.
MONEY_MAX = Decimal("999999.999999")


class CostOutOfRangeError(ValueError):
    """Money magnitude cannot be represented in the frozen NUMERIC(12,6)
    column (|value| > 999999.999999)."""


@dataclass(frozen=True)
class LedgerRow:
    """One recorded usage row (mirrors the frozen cost_ledger columns)."""

    seq: int | None
    run_id: str | None
    agent_role: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    created_at: datetime | None


def _role_str(role: ModelRole | str) -> str:
    """ModelRole is a str Enum; persist its bare value ('primary'), not repr."""
    value = getattr(role, "value", role)
    return str(value)


def to_money(value: Any) -> Decimal:
    """Normalize any driver's NUMERIC read into an exact Decimal at (12,6).

    Accepts Decimal (asyncpg/SQLAlchemy), str (SQLite text storage), and the
    historical float/int shapes. Floats convert via repr so the shortest
    round-tripping decimal wins, never the binary expansion.

    Raises :class:`CostOutOfRangeError` when |value| exceeds the column's
    representable magnitude — writers convert that into an explicit,
    honest rejection instead of a silently dropped ledger row.
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, str):
        dec = Decimal(value)
    else:
        dec = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    out = dec.quantize(_MONEY_Q, rounding=ROUND_HALF_EVEN)
    if abs(out) > MONEY_MAX:
        raise CostOutOfRangeError(
            f"cost {out} exceeds NUMERIC(12,6) envelope (+/-{MONEY_MAX})"
        )
    return out


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
    Money binds as TEXT: asyncpg infers NUMERIC from str; SQLite takes it
    natively (a raw Decimal bind crashes sqlite3).
    """
    cost_usd = PricingEngine.calculate_cost(model, input_tokens, output_tokens)
    return (
        run_id,
        _role_str(role)[:20],  # agent_role VARCHAR(20)
        model[:120],  # model VARCHAR(120)
        int(input_tokens),
        int(output_tokens),
        f"{cost_usd:.6f}",  # NUMERIC(12,6) — exact base-10 bind
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
            cost_usd=to_money(params[5]),
            latency_ms=int(params[6]),
            created_at=row[1] if row and len(row) > 1 else None,
        )
    except CostOutOfRangeError:
        # Distinct from "ledger unavailable": a DATA problem — the computed
        # estimate itself overflows NUMERIC(12,6). The row is refused with an
        # explicit warning, never silently dropped; exc_info carries the value.
        # Fire-and-forget contract holds: no exception into the call path.
        logger.warning(
            "cost_ledger write REJECTED — spend NOT metered, computed cost "
            "out of range (check pricing inputs; do not retry blindly)",
            exc_info=True,
        )
        return None
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
    is a datetime from ``date_trunc('day', ...)``. Money folds EXACTLY in
    Decimal (no float drift across many rows); only the serialized wire
    values are converted back to JSON numbers (contract M10-C3).
    """
    total_in = 0
    total_out = 0
    total_cost = Decimal("0")
    by_model: dict[str, dict[str, Any]] = {}
    by_day: dict[str, Decimal] = {}
    for model, input_tokens, output_tokens, cost_usd, day in rows:
        day_key = day.date().isoformat() if isinstance(day, datetime) else str(day or "unknown")
        total_in += int(input_tokens or 0)
        total_out += int(output_tokens or 0)
        total_cost += to_money(cost_usd)
        entry = by_model.setdefault(model, {"model": model, "costUsd": Decimal("0"), "tokens": 0})
        entry["costUsd"] += to_money(cost_usd)
        entry["tokens"] += int(input_tokens or 0) + int(output_tokens or 0)
        by_day[day_key] = by_day.get(day_key, Decimal("0")) + to_money(cost_usd)

    def _usd(value: Decimal) -> float:
        # Wire format unchanged (JSON numbers); conversion happens only here,
        # after all arithmetic has already happened exactly.
        return float(value.quantize(_MONEY_Q, rounding=ROUND_HALF_EVEN))

    return {
        "totals": {
            "inputTokens": total_in,
            "outputTokens": total_out,
            "costUsd": _usd(total_cost),
        },
        "byModel": [
            {
                "model": e["model"],
                "costUsd": _usd(e["costUsd"]),
                "tokens": e["tokens"],
            }
            for e in sorted(by_model.values(), key=lambda e: -e["costUsd"])
        ],
        "byDay": [{"day": d, "costUsd": _usd(c)} for d, c in sorted(by_day.items())],
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
    "CostOutOfRangeError",
    "INSERT_USAGE_SQL",
    "LedgerRow",
    "MONEY_MAX",
    "SPEND_SQL",
    "build_usage_params",
    "record_usage",
    "spend_summary",
    "summarize_spend",
    "to_money",
]
