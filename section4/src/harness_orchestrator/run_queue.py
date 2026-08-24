"""Durable run-claim queue: crash-resumable execution + per-team caps.

Timestamps here stay UTC ISO-8601 TEXT because the FROZEN run_claims schema
(section1/migrations/0006_run_claims.sql) deliberately types claimed_at /
heartbeat_at as TEXT so every statement runs identically on PostgreSQL
(prod) and SQLite/aiosqlite (tests). They are never bound to TIMESTAMPTZ.
Every instant originates from harness_identity.timeutil (M13-H4b law: all
DB timestamps timezone-aware UTC; asyncpg gets aware datetime objects for
TIMESTAMPTZ columns and ISO text ONLY where the column is TEXT).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Shared time helpers live in section1; this venv does not carry
# harness_identity as an installed distribution, so the src tree is appended
# exactly like api.py's wave-1 audit seam does at call time.
_ROOT = Path(__file__).resolve().parents[3]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

from harness_identity.timeutil import as_utc, now_utc, utcnow_iso  # noqa: E402

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Aware-UTC ISO text for the frozen TEXT-typed run_claims columns."""
    return utcnow_iso()


# M13-H1b: heartbeat staleness threshold. Env override keeps the default (90s
# = 9 missed beats at the service's 10s interval) tunable per deployment.
HEARTBEAT_TIMEOUT_SECONDS = int(
    os.environ.get("HEARTBEAT_TIMEOUT_SECONDS", "90")
)


def _parse_heartbeat(raw: Any) -> datetime | None:
    """Parse a stored heartbeat_at value into an aware UTC datetime.

    The frozen column is TEXT ISO-8601, but SQLite may hand back other shapes;
    anything unparseable yields None so callers skip instead of guessing.
    """
    if isinstance(raw, datetime):
        return as_utc(raw)
    if not raw:
        return None
    try:
        return as_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except ValueError:
        return None


def heartbeat_age_seconds(heartbeat_at: Any, now: datetime | None = None) -> float | None:
    """Seconds since ``heartbeat_at`` (aware-UTC), or None when unparseable.

    Staleness is always DB-stored instant vs current aware-UTC time — never
    wall-clock bookkeeping — so a restarted process judges correctly.
    """
    hb = _parse_heartbeat(heartbeat_at)
    if hb is None:
        return None
    return max((now or now_utc()) - hb, timedelta(0)).total_seconds()


def heartbeat_timed_out(heartbeat_at: Any) -> bool:
    """True iff a stored heartbeat is older than HEARTBEAT_TIMEOUT_SECONDS."""
    age = heartbeat_age_seconds(heartbeat_at)
    return age is not None and age >= HEARTBEAT_TIMEOUT_SECONDS


async def claim_next_run(factory, worker_id: str, max_per_team: int) -> dict[str, Any] | None:
    """Atomically claim the oldest queued run under its team's running cap.

    On PostgreSQL the inner SELECT takes ``FOR UPDATE SKIP LOCKED`` so two
    workers polling concurrently can never serialize behind each other (or
    double-claim): rows already locked by another claim simply fall out of the
    candidate set. SQLite (aiosqlite tests + single-writer reality) has no row
    locks — the clause would be a syntax error — so it is appended only when
    the bound engine's dialect is postgresql.
    """
    from sqlalchemy import text

    async with factory() as db:
        sql = _CLAIM_SQL
        if db.bind.dialect.name == "postgresql":
            sql += "\nFOR UPDATE OF rc SKIP LOCKED"
        row = (
            await db.execute(
                text(sql),
                {
                    "now": _now_iso(),
                    "worker_id": worker_id,
                    "max_per_team": max_per_team,
                },
            )
        ).mappings().first()
        await db.commit()
        return dict(row) if row is not None else None


async def enqueue_run(factory, run_id: str, project_id: str) -> bool:
    """Queue a run; True iff this call made it claimable."""
    from sqlalchemy import text

    now = _now_iso()
    async with factory() as db:
        try:
            inserted = (
                await db.execute(
                    text(
                        "INSERT INTO run_claims "
                        "(run_id, project_id, state, claimed_at, heartbeat_at, worker_id) "
                        "VALUES (:run_id, :project_id, 'queued', :now, :now, 'enqueuer') "
                        "ON CONFLICT (run_id) DO NOTHING"
                    ),
                    {"run_id": run_id, "project_id": project_id, "now": now},
                )
            ).rowcount
            await db.commit()
        except Exception:
            # No queue table (bare sqlite in-memory test DBs): the run cannot
            # be enqueued durably — report not-queued so the caller spawns
            # directly instead of silently dropping the start request.
            logger.info("run_claims unavailable; caller falls back to direct spawn")
            return False
        if inserted:
            return True
        updated = (
            await db.execute(
                text(
                    "UPDATE run_claims SET state = 'queued', claimed_at = :now, "
                    "heartbeat_at = :now WHERE run_id = :run_id AND state = 'orphaned'"
                ),
                {"run_id": run_id, "now": now},
            )
        ).rowcount
        await db.commit()
        return bool(updated)


_CLAIM_SQL = """
UPDATE run_claims
SET state = 'running',
    claimed_at = :now,
    heartbeat_at = :now,
    worker_id = :worker_id
WHERE run_id = (
    SELECT rc.run_id
    FROM run_claims rc
    JOIN projects p ON rc.project_id = p.id
    WHERE rc.state = 'queued'
      AND (SELECT COUNT(*)
           FROM run_claims r2
           JOIN projects p2 ON r2.project_id = p2.id
           WHERE p2.team_id = p.team_id AND r2.state = 'running') < :max_per_team
    ORDER BY rc.claimed_at ASC
    LIMIT 1
)
RETURNING run_id, project_id
"""


async def heartbeat_run(factory, run_id: str, worker_id: str) -> bool:
    from sqlalchemy import text

    async with factory() as db:
        matched = (
            await db.execute(
                text(
                    "UPDATE run_claims SET heartbeat_at = :now "
                    "WHERE run_id = :run_id AND state = 'running' AND worker_id = :worker_id"
                ),
                {"run_id": run_id, "worker_id": worker_id, "now": _now_iso()},
            )
        ).rowcount
        await db.commit()
        return bool(matched)


async def finish_run(factory, run_id: str) -> None:
    from sqlalchemy import text

    async with factory() as db:
        try:
            await db.execute(text("DELETE FROM run_claims WHERE run_id = :r"), {"r": run_id})
            await db.commit()
        except Exception:  # noqa: BLE001 — no queue table (bare test DB): nothing to clear
            logger.info("run_claims unavailable; finish_run skipped for %s", run_id)


async def reclaim_orphans(factory, stale_seconds: int = 90) -> list[str]:
    """Flip silent running claims to orphaned; returns their run_ids."""
    from sqlalchemy import text

    cutoff = (now_utc() - timedelta(seconds=stale_seconds)).isoformat()
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "UPDATE run_claims SET state = 'orphaned' "
                    "WHERE state = 'running' AND heartbeat_at < :cutoff "
                    "RETURNING run_id"
                ),
                {"cutoff": cutoff},
            )
        ).fetchall()
        await db.commit()
        return [row[0] for row in rows]


async def get_queue_snapshot(factory) -> list[dict[str, Any]]:
    from sqlalchemy import text

    async with factory() as db:
        result = await db.execute(text("SELECT * FROM run_claims ORDER BY claimed_at ASC"))
        rows = result.mappings().all()
        return [dict(row) for row in rows]
