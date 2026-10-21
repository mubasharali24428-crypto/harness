"""Interval jobs the orchestrator process runs for itself.

Four maintenance functions existed with the note "nothing schedules this": the search-index sync,
the export-file purge, the discussion-retention purge and the artifact-retention purge. Each one
is idempotent (upserts, or work guarded by "not yet purged"), so two replicas running the same job
would repeat effort but never corrupt state. Parts 48/50 (session 12): on PostgreSQL each tick is
still leader-elected (``leader_only``), so one replica runs it and the others skip that tick. A
failing run is logged and retried on the next tick; one job's failure never stops the others.

Part 40 and 42 (session 12) add the session purge (retention elapsed, audited as a system action)
and the lease sweep (lapsed control leases released, audited). Deliberately NOT scheduled:
``retention.purge`` (deletes workspaces by age and defaults to a dry run, an operator decision).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

#: label -> (environment variable, default seconds). 0 or negative turns a job off.
JOBS: dict[str, tuple[str, float]] = {
    "search-sync": ("HARNESS_SEARCH_SYNC_SECONDS", 60.0),
    "export-purge": ("HARNESS_EXPORT_PURGE_SECONDS", 3600.0),
    "discussion-purge": ("HARNESS_DISCUSSION_PURGE_SECONDS", 3600.0),
    "artifact-purge": ("HARNESS_ARTIFACT_PURGE_SECONDS", 3600.0),
    "workspace-purge": ("HARNESS_WORKSPACE_PURGE_SECONDS", 3600.0),
    "session-purge": ("HARNESS_SESSION_PURGE_SECONDS", 3600.0),
    "lease-sweep": ("HARNESS_LEASE_SWEEP_SECONDS", 30.0),
    "notification-purge": ("HARNESS_NOTIFY_PURGE_SECONDS", 3600.0),
}


async def run_periodically(
    label: str, job: Callable[[], Awaitable[Any]], interval: float
) -> None:
    """Run ``job`` now and then every ``interval`` seconds until cancelled."""
    while True:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a bad tick must not end the loop
            logger.exception("scheduled job %s failed", label)
        await asyncio.sleep(interval)


async def leader_only(factory: Any, label: str, job: Callable[[], Awaitable[Any]]) -> Any:
    """Run one tick of ``job`` in at most one replica at a time. On PostgreSQL the tick holds a
    session advisory lock keyed by ``label`` on its own connection; a replica that cannot take it
    skips the tick (returns None). The lock dies with the connection, so a crashed leader frees it.
    Other databases (SQLite dev, single process) just run the job."""
    from sqlalchemy import text

    engine = getattr(factory, "kw", {}).get("bind")
    if engine is None or engine.dialect.name != "postgresql":
        return await job()
    key = {"k": f"harness-job:{label}"}
    async with engine.connect() as conn:
        got = await conn.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), key)
        if not got.scalar():
            return None
        try:
            return await job()
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), key)


def maintenance_jobs(
    factory: Any, env: Mapping[str, str] | None = None
) -> list[tuple[str, Callable[[], Awaitable[Any]], float]]:
    """The enabled jobs as ``(label, callable, interval seconds)``."""
    from . import (
        artifacts,
        collab,
        discussion,
        exports,
        notify,
        search,
        session_purge,
        workspace_gate,
    )

    env = os.environ if env is None else env
    calls: dict[str, Callable[[], Awaitable[Any]]] = {
        "search-sync": lambda: search.sync_index(factory),
        "export-purge": lambda: exports.purge_expired_exports(factory),
        "discussion-purge": lambda: discussion.purge_expired(factory),
        "artifact-purge": lambda: artifacts.purge_expired(factory),
        "workspace-purge": lambda: workspace_gate.purge_expired_workspaces(factory),
        "session-purge": lambda: session_purge.purge_due(factory),
        "lease-sweep": lambda: collab.sweep_expired_leases(factory),
        "notification-purge": lambda: notify.purge_old(factory),
    }
    out = []
    for label, (var, default) in JOBS.items():
        every = float(env.get(var, default))
        if every > 0:
            call = calls[label]
            out.append((label, lambda c=call, n=label: leader_only(factory, n, c), every))
    return out
