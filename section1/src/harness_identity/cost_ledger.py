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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

logger = logging.getLogger(__name__)

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
    cost_usd: float
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
    cost_usd: float,
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
    """
    try:
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
                {
                    "run_id": str(resolved_run_id),
                    "agent_role": _role_str(agent_role),
                    "model": str(model)[:120],  # model VARCHAR(120)
                    "input_tokens": int(input_tokens),
                    "output_tokens": int(output_tokens),
                    "cost_usd": round(float(cost_usd), 6),  # NUMERIC(12,6)
                    "latency_ms": int(latency_ms),
                },
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
            cost_usd=round(float(cost_usd), 6),
            latency_ms=int(latency_ms),
            created_at=datetime.now(UTC),
        )
    except Exception:
        logger.warning("cost_ledger write failed; continuing without metering", exc_info=True)
        return None


__all__ = ["UsageRecord", "record_usage"]
