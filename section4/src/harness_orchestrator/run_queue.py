"""Durable run-claim queue: crash-resumable execution + per-team caps.

Timestamps here stay UTC ISO-8601 TEXT because the FROZEN run_claims schema
(section1/migrations/0006_run_claims.sql) deliberately types claimed_at /
heartbeat_at as TEXT so every statement runs identically on PostgreSQL
(prod) and SQLite/aiosqlite (tests). They are never bound to TIMESTAMPTZ.
Every instant originates from harness_contracts.timeutil (M13-H4b law: all
DB timestamps timezone-aware UTC; asyncpg gets aware datetime objects for
TIMESTAMPTZ columns and ISO text ONLY where the column is TEXT).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from harness_contracts.timeutil import as_utc, now_utc, utcnow_iso

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Aware-UTC ISO text for the frozen TEXT-typed run_claims columns."""
    return utcnow_iso()


# M13-H1b: heartbeat staleness threshold. Default 90s = 9 missed beats at the
# service's 10s heartbeat interval; the HEARTBEAT_TIMEOUT_SECONDS env var
# overrides it per deployment. This module constant is resolved ONCE at import;
# every service resolves its OWN threshold at construction time via
# resolve_heartbeat_timeout_seconds(), so a restart re-reads deployment env.
_DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 90


def resolve_heartbeat_timeout_seconds(environ=None) -> int:
    """Heartbeat-staleness threshold in seconds: env override, default 90."""
    env = os.environ if environ is None else environ
    raw = str(env.get("HEARTBEAT_TIMEOUT_SECONDS", "")).strip()
    if not raw:
        return _DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
    try:
        return int(raw)
    except ValueError:
        logger.warning("ignoring non-integer HEARTBEAT_TIMEOUT_SECONDS=%r", raw)
        return _DEFAULT_HEARTBEAT_TIMEOUT_SECONDS


HEARTBEAT_TIMEOUT_SECONDS = resolve_heartbeat_timeout_seconds()


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
    """True iff a stored heartbeat is older than the staleness threshold."""
    age = heartbeat_age_seconds(heartbeat_at)
    return age is not None and age >= HEARTBEAT_TIMEOUT_SECONDS


# M13-H1b: staleness is decided by the DATABASE clock (now() vs the stored
# TEXT instant), never by an application process's wall clock — a restarted
# or clock-skewed worker must judge exactly as the DB does. SQLite exposes
# julianday() over ISO-8601 text; PostgreSQL compares the cast timestamptz
# against its own now(). Both predicates mean "heartbeat older than :timeout
# seconds ago", evaluated inside the engine.
_HEARTBEAT_STALE_SQL = {
    "sqlite": (
        "julianday(heartbeat_at) <= julianday("
        "strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', -:timeout || ' seconds'))"
    ),
    "postgresql": "(heartbeat_at::timestamptz) <= (now() - make_interval(secs => :timeout))",
}


def _stale_predicate(dialect_name: str) -> str | None:
    """DB-clock staleness predicate for this dialect, or None if unsupported.

    An unsupported dialect fails CLOSED (returns None): the reaper reaps
    nothing rather than guessing with the wrong clock.
    """
    return _HEARTBEAT_STALE_SQL.get(dialect_name)


async def release_timed_out_claims(factory, timeout_seconds: int) -> list[dict[str, Any]]:
    """Zombie reaper sweep: atomically release timed-out running claims.

    One DELETE ... RETURNING selects ``running`` rows whose stored heartbeat
    is stale **by the database's own clock** and deletes them — the same
    terminal shape finish_run uses — so no worker can re-claim between the
    staleness decision and the release. Returns one mapping per released
    claim: run_id, project_id, worker_id.
    """
    from sqlalchemy import text

    async with factory() as db:
        predicate = _stale_predicate(db.bind.dialect.name)
        if predicate is None:
            logger.warning(
                "no DB-clock staleness predicate for dialect %r; skipping zombie reap",
                db.bind.dialect.name,
            )
            return []
        rows = (
            await db.execute(
                text(
                    "DELETE FROM run_claims WHERE state = 'running' AND "
                    + predicate
                    + " RETURNING run_id, project_id, worker_id"
                ),
                {"timeout": timeout_seconds},
            )
        ).mappings().all()
        await db.commit()
        return [dict(row) for row in rows]


def _inject_skip_locked(sql: str) -> str:
    """Place ``FOR UPDATE OF rc SKIP LOCKED`` inside the claim subquery.

    The lock clause belongs where the ``rc`` alias rows are selected — before
    the statement-level RETURNING — which is the only placement PostgreSQL
    accepts for this shape (verified live against PostgreSQL 18). The seam is
    ``)\\nRETURNING``: the closing paren of the candidate-row subquery. The
    paren is REPLACED here, never duplicated (a stray second ``)`` is itself
    a syntax error).
    """
    head, sep, tail = sql.partition(")\nRETURNING")
    if not sep:  # defensive: unexpected template shape — return untouched
        logger.warning("claim template missing RETURNING seam; SKIP LOCKED skipped")
        return sql
    return f"{head}\nFOR UPDATE OF rc SKIP LOCKED\n){sep[1:]}{tail}"


async def claim_next_run(factory, worker_id: str, max_per_team: int) -> dict[str, Any] | None:
    """Atomically claim the oldest queued run under its team's running cap.

    On PostgreSQL the inner SELECT takes ``FOR UPDATE OF rc SKIP LOCKED`` so two
    workers polling concurrently can never serialize behind each other (or
    double-claim): rows already locked by another claim simply fall out of the
    candidate set. The clause lives INSIDE the subquery (before RETURNING) —
    that is where the rc alias's rows are selected and the only position PG
    accepts for this statement shape (verified live against PostgreSQL 18:
    a clause after RETURNING is a syntax error). SQLite (aiosqlite tests +
    single-writer reality) has no row locks — the clause would be a syntax
    error there — so the injection happens only when the bound engine's
    dialect is postgresql.
    """
    from sqlalchemy import text

    async with factory() as db:
        sql = _CLAIM_SQL
        if db.bind.dialect.name == "postgresql":
            # Lock the candidate row inside its subquery, before RETURNING.
            sql = _inject_skip_locked(sql)
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
