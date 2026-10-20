"""Interval jobs the orchestrator process runs for itself.

Four maintenance functions existed with the note "nothing schedules this": the search-index sync,
the export-file purge, the discussion-retention purge and the artifact-retention purge. Each one
is idempotent (upserts, or work guarded by "not yet purged"), so two replicas running the same job
repeat effort but never corrupt state; that is why there is no leader election here. A failing run
is logged and retried on the next tick; one job's failure never stops the others.

Deliberately NOT scheduled: ``retention.purge`` (deletes workspaces by age and defaults to a dry
run, an operator decision) and session ``purge`` (an explicit admin route).
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


def maintenance_jobs(
    factory: Any, env: Mapping[str, str] | None = None
) -> list[tuple[str, Callable[[], Awaitable[Any]], float]]:
    """The enabled jobs as ``(label, callable, interval seconds)``."""
    from . import artifacts, discussion, exports, search, workspace_gate

    env = os.environ if env is None else env
    calls: dict[str, Callable[[], Awaitable[Any]]] = {
        "search-sync": lambda: search.sync_index(factory),
        "export-purge": lambda: exports.purge_expired_exports(factory),
        "discussion-purge": lambda: discussion.purge_expired(factory),
        "artifact-purge": lambda: artifacts.purge_expired(factory),
        "workspace-purge": lambda: workspace_gate.purge_expired_workspaces(factory),
    }
    out = []
    for label, (var, default) in JOBS.items():
        every = float(env.get(var, default))
        if every > 0:
            out.append((label, calls[label], every))
    return out
