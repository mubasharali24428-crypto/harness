"""Durable run-claim queue: crash-resumable execution + per-team caps.

All timestamps are UTC ISO-8601 TEXT so every statement runs identically on
PostgreSQL (prod) and SQLite/aiosqlite (tests).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


async def claim_next_run(factory, worker_id: str, max_per_team: int) -> dict[str, Any] | None:
    """Atomically claim the oldest queued run under its team's running cap."""
    from sqlalchemy import text

    async with factory() as db:
        row = (
            await db.execute(
                text(_CLAIM_SQL),
                {
                    "now": _now_iso(),
                    "worker_id": worker_id,
                    "max_per_team": max_per_team,
                },
            )
        ).mappings().first()
        await db.commit()
        return dict(row) if row is not None else None


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

    cutoff = (datetime.now(UTC) - timedelta(seconds=stale_seconds)).isoformat()
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
