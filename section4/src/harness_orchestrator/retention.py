"""P3.2 retention: purge sensitive leftovers of long-finished runs.

Eligible run: finished before the cutoff and no approval still
pending/approved/executing. "Finished" = an engine_completed / engine_failed /
pr_opened event or a run.stopped audit row (``agent_runs.state`` is not maintained).
Withheld material awaiting a decision is never deleted. For eligible runs this removes
the staging baseline, withheld files, per-run scratch (.run-tmp) and engine session
directory, blanks approval
diffs, and deletes collaboration events.
Run outputs (the per-run workspace) are kept unless ``include_workspace`` is set.
The audit log and approval metadata (digest, paths, state) are never purged.

Dry-run by default from the CLI::

    python -m harness_orchestrator.retention --days 30            # report only
    python -m harness_orchestrator.retention --days 30 --apply

Needs HARNESS_DATABASE_URL; roots default to the engine's DSH_WORKSPACE / DSH_SESSION_ROOT.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from harness_orchestrator.runs_index import _FINISHED_EVENT_TYPES

DAYS_ENV = "HARNESS_RETENTION_DAYS"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_OPEN = ("pending", "approved", "executing")
_FINAL = ("completed", "failed", "rejected", "expired", "invalidated")


def _aware(v: Any) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _rm(path: str, root: str) -> bool:
    """Remove ``path`` only when it really lives under ``root`` (no symlink escapes)."""
    real_root = os.path.realpath(root)
    if os.path.islink(path) or os.path.commonpath([real_root, os.path.realpath(path)]) != real_root:
        return False
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path)
    return True


async def purge(
    factory: Any,
    workspace_root: str,
    session_root: str,
    days: int,
    apply: bool = False,
    include_workspace: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=max(1, days))
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT r.id AS id,"
                    " (SELECT MAX(e.ts) FROM agent_events e WHERE e.run_id = r.id"
                    f" AND e.type IN {_FINISHED_EVENT_TYPES!r}) AS event_ts,"
                    " (SELECT MAX(a.ts) FROM audit_log a WHERE a.target_id = r.id"
                    " AND a.action = 'run.stopped') AS stop_ts,"
                    " (SELECT MAX(e.ts) FROM agent_events e WHERE e.run_id = r.id) AS last_ts"
                    " FROM agent_runs r WHERE NOT EXISTS (SELECT 1 FROM run_retention_holds l"
                    " WHERE l.run_id = r.id)"
                    " AND NOT EXISTS (SELECT 1 FROM high_impact_approvals h"
                    " WHERE h.run_id = r.id AND h.state IN ('pending', 'approved', 'executing'))"
                )
            )
        ).all()
        runs = sorted(
            r.id
            for r in rows
            if _SAFE_ID.match(r.id)
            and (done := max((t for t in (r.event_ts, r.stop_ts) if t), default=None))
            and _aware(done) < cutoff
            and (r.last_ts is None or _aware(r.last_ts) < cutoff)  # resumed since => still live
        )
        report: dict[str, Any] = {"runs": runs, "applied": apply, "removedDirs": 0}
        if not apply:
            return report
        for rid in runs:
            targets = [
                (f"{workspace_root}/.baseline/{rid}", workspace_root),
                (f"{workspace_root}/.staged/{rid}", workspace_root),
                (f"{workspace_root}/.run-tmp/{rid}", workspace_root),
                (f"{session_root}/{rid}", session_root),
            ]
            if include_workspace:
                targets.append((f"{workspace_root}/{rid}", workspace_root))
            report["removedDirs"] += sum(_rm(p, root) for p, root in targets)
            await db.execute(
                text(
                    "UPDATE high_impact_approvals SET diff = '' WHERE run_id = :r"
                    f" AND state IN {_FINAL!r}"
                ),
                {"r": rid},
            )
            await db.execute(text("DELETE FROM run_collab_events WHERE run_id = :r"), {"r": rid})
        await db.commit()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=int(os.environ.get(DAYS_ENV, "30")))
    ap.add_argument("--apply", action="store_true", help="delete (default: report only)")
    ap.add_argument("--include-workspace", action="store_true")
    args = ap.parse_args()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ["HARNESS_DATABASE_URL"]
    workspace = os.path.realpath(os.environ.get("DSH_WORKSPACE", "/tmp/harness-dsh-workspace"))
    sessions = os.path.realpath(os.environ.get("DSH_SESSION_ROOT", "/tmp/harness-dsh-sessions"))

    async def run() -> dict[str, Any]:
        engine = create_async_engine(url)
        try:
            return await purge(
                async_sessionmaker(engine, expire_on_commit=False),
                workspace, sessions, args.days, args.apply, args.include_workspace,
            )
        finally:
            await engine.dispose()

    print(asyncio.run(run()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
