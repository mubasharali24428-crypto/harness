"""Mission 11 wave 1 node A3 — team-attributed cost_ledger writer.

``record_usage`` is the thin wrapper other sections call to meter one model
call against the frozen ``cost_ledger`` table, attributing it to a TEAM via
the frozen attribution chain::

    cost_ledger.run_id -> agent_runs.project_id -> projects.team_id

The frozen table has no ``team_id`` column (contracts/ cannot gain envelope
shapes), so the caller passes the owning team's id explicitly; the writer
resolves that team's project/run linkage and refuses silently-but-loudly when
attribution cannot be resolved rather than writing an unattributable row.

The pre-existing router-path writer (:func:`harness_router.ledger.record_usage`)
is untouched and stays intact for Section 3's internal path.

Money typing (H-023): ``cost_usd`` is carried as an exact ``Decimal``
(NUMERIC(12,6) scale) end to end. It is BOUND AS TEXT — asyncpg infers NUMERIC
from str and SQLite (scratch test schemas) stores the text exactly — never as
a binary float, so no 0.1+0.2-class drift can enter the ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# NUMERIC(12,6) quantum — mirrors harness_router.ledger.to_money (S3 keeps its
# own copy: section venvs are deliberately decoupled, same as _role_str below).
_MONEY_Q = Decimal("0.000001")


def _to_money(value) -> Decimal:
    """Normalize any money shape (Decimal/float/int/str/None) into an exact
    NUMERIC(12,6)-scale Decimal. Floats convert via repr (shortest round-trip
    decimal — never the binary expansion); None means zero."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, str):
        dec = Decimal(value)
    elif isinstance(value, float):
        dec = Decimal(repr(value))
    else:
        dec = Decimal(value)
    return dec.quantize(_MONEY_Q, rounding=ROUND_HALF_EVEN)

# Resolve one attributable run id for the team: prefer the team's most recent
# run so usage lands on a real FK target even without a per-call run id.
# Named binds (:team_id) — the session-factory pattern used across Section 1.
_RESOLVE_RUN_SQL = text(
    "SELECT r.id FROM agent_runs r "
    "JOIN projects p ON p.id = r.project_id "
    "WHERE p.team_id = :team_id "
    "ORDER BY r.created_at DESC LIMIT 1"
)

_INSERT_USAGE_SQL = text(
    "INSERT INTO cost_ledger "
    "(run_id, agent_role, model, input_tokens, output_tokens, cost_usd, latency_ms) "
    "VALUES (:run_id, :agent_role, :model, :input_tokens, :output_tokens, :cost_usd, :latency_ms) "
    "RETURNING seq"
)


def usage_bind_params(
    *,
    run_id: str | None,
    agent_role,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    cost_usd,
) -> dict[str, Any]:
    """Bind-parameter dict for _INSERT_USAGE_SQL (exposed for tests).

    Money binds AS EXACT BASE-10 TEXT (H-023): asyncpg infers NUMERIC from
    str, SQLite stores the text verbatim into its numeric-affinity column —
    neither driver ever sees a binary float for cost_usd.
    """
    return {
        "run_id": str(run_id),
        "agent_role": _role_str(agent_role),
        "model": str(model)[:120],  # model VARCHAR(120)
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": f"{_to_money(cost_usd):.6f}",  # NUMERIC(12,6), exact
        "latency_ms": int(latency_ms),
    }


@dataclass(frozen=True)
class UsageRecord:
    """One recorded usage row as persisted (mirrors frozen columns)."""

    entry_id: int | None
    team_id: str | None
    run_id: str | None
    agent_role: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    created_at: datetime


def _role_str(role) -> str:
    """Accept ModelRole enums or raw strings; persist the bare value."""
    value = getattr(role, "value", role)
    return str(value)[:20]  # agent_role VARCHAR(20)


async def record_usage(
    session_factory,
    *,
    team_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: Any,
    run_id: str | None = None,
    agent_role="primary",
    latency_ms: int = 0,
) -> UsageRecord | None:
    """Insert one team-attributed cost_ledger row; NEVER raises into callers.

    ``run_id=None`` resolves the team's latest run so the frozen FK stays
    satisfiable; if no attributable run exists the write is skipped and None
    is returned with a warning (never fabricate unattributable spend).
    Fire-and-forget contract: any failure logs at warning level and returns
    None instead of propagating into the model-call path.

    ``cost_usd`` accepts Decimal/float/int/str and is normalized ONCE through
    :func:`_to_money` into an exact NUMERIC(12,6)-scale Decimal before
    anything else happens; it is bound as text.
    """
    try:
        # Exact money normalization BEFORE any DB contact (H-023): quantize to
        # NUMERIC(12,6) scale, then bind as text — exact on both drivers.
        cost_dec = _to_money(cost_usd)
        cost_bind = f"{cost_dec:.6f}"
        async with session_factory() as db:
            resolved_run_id = run_id
            if resolved_run_id is None:
                row = (
                    await db.execute(_RESOLVE_RUN_SQL, {"team_id": team_id})
                ).first()
                resolved_run_id = row[0] if row is not None else None
            if resolved_run_id is None:
                logger.warning(
                    "cost_ledger write skipped: no attributable run for team %s", team_id
                )
                return None
            result = await db.execute(
                _INSERT_USAGE_SQL,
                usage_bind_params(
                    run_id=resolved_run_id,
                    agent_role=agent_role,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                ),
            )
            entry_id = result.scalar_one()
            await db.commit()
        return UsageRecord(
            entry_id=int(entry_id),
            team_id=str(team_id),
            run_id=str(resolved_run_id),
            agent_role=_role_str(agent_role),
            model=str(model)[:120],
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cost_usd=cost_dec.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN),
            latency_ms=int(latency_ms),
            created_at=datetime.now(UTC),
        )
    except Exception:
        logger.warning("cost_ledger write failed; continuing without metering", exc_info=True)
        return None


__all__ = ["UsageRecord", "record_usage"]
