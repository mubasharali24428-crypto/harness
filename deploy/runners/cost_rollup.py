"""M18-C3 — daily per-team cost rollup sidecar (F6, m17-findings-pbc1.md).

cost_ledger grows one row per model call (~20K-100K rows/day at load); the
budget gate used to SUM it whole on every run start. Migration 0027 adds
``cost_ledger_daily`` (one row per team per CLOSED day) and this runner keeps
it fed so ``section4 budget.check_budget`` reads <= N single-row lookups plus
one indexed live SUM for today's partial day — O(days), independent of ledger
size.

Recompute, not +=: release() legitimately DELETES reserved cost_ledger rows
(section3 budget_guard), which an additive upsert can never undo. Every pass
recomputes closed days from source-of-truth cost_ledger with DELETE+INSERT in
one transaction per pass, so any drift (missed runs, manual deletes) self-heals
on the next sweep.

"Closed" = created_at < date_trunc('day', now()) evaluated by the DB clock,
never by app clocks — one month-window definition across the money paths
(same posture as section3 budget_guard.RESERVE_SQL). Today's rows are NEVER
stored here; the gate always sums them live, keeping the boundary exact while
the sidecar runs.

Wired as the opt-in 'cost-rollup' compose sidecar (mirrors Mission-12
'events-retention'): `--profile cost-rollup up -d cost-rollup` runs the loop;
`run --rm cost-rollup python /opt/harness/deploy/runners/cost_rollup.py --once`
runs a single backfill+sweep (host cron friendly). The first pass backfills
ALL history, which flips the gate from its exact legacy fallback onto the
rollup path.

Deliberately self-contained: creates its own engine from HARNESS_DATABASE_URL
and imports NOTHING from deploy.runners.services, whose module-level
HARNESS_MASTER_KEY fail-fast would kill a pure-DB sidecar.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import text

HARNESS_ROOT = Path(__file__).resolve().parents[2]
for _section in sorted(HARNESS_ROOT.glob("section*")):
    _src = _section / "src"
    if _src.exists():
        sys.path.insert(0, str(_src))

logger = logging.getLogger("cost-rollup")

DEFAULT_DATABASE_URL = "postgresql+asyncpg://harness:harness@db:5432/harness"
DEFAULT_RUN_INTERVAL_SECONDS = 6 * 60 * 60  # four sweeps a day

# Two statements, ONE transaction (rollup_once executes them on a single
# session): the DELETE clears exactly the recomputed keys, then the INSERT
# refills them from source-of-truth cost_ledger through the frozen
# attribution chain. Readers see either the previous complete snapshot or
# the new one -- never a half-fed table. Released/deleted reserved ledger
# rows cannot leave phantom spend behind. SQLite accepts this shape verbatim
# (LIMIT-less grouped same-table subquery inside DELETE; same law
# events_retention relies on; MySQL is the outlier that forbids it).
ROLLUP_DELETE_SQL = (
    "DELETE FROM cost_ledger_daily WHERE (team_id, day) IN ("
    "SELECT p.team_id AS team_id, date_trunc('day', l.created_at) AS day "
    "FROM cost_ledger l "
    "JOIN agent_runs r ON r.id = l.run_id "
    "JOIN projects p ON p.id = r.project_id "
    "WHERE l.created_at < date_trunc('day', now()) "
    "GROUP BY p.team_id, date_trunc('day', l.created_at))"
)
ROLLUP_INSERT_SQL = (
    "INSERT INTO cost_ledger_daily (team_id, day, total_usd) "
    "SELECT p.team_id AS team_id, date_trunc('day', l.created_at) AS day, "
    "SUM(l.cost_usd) AS total_usd "
    "FROM cost_ledger l "
    "JOIN agent_runs r ON r.id = l.run_id "
    "JOIN projects p ON p.id = r.project_id "
    "WHERE l.created_at < date_trunc('day', now()) "
    "GROUP BY p.team_id, date_trunc('day', l.created_at)"
)


def run_interval_from_env(env: Mapping[str, str] | None = None) -> int:
    """RUN_INTERVAL_SECONDS knob, injectable for tests (default when unset/blank/garbage)."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    raw = env_map.get("RUN_INTERVAL_SECONDS", "")
    try:
        value = int(raw) if raw.strip() else DEFAULT_RUN_INTERVAL_SECONDS
    except ValueError:
        logger.warning(
            "RUN_INTERVAL_SECONDS=%r is not an integer; using default %s",
            raw,
            DEFAULT_RUN_INTERVAL_SECONDS,
        )
        return DEFAULT_RUN_INTERVAL_SECONDS
    if value <= 0:
        logger.warning(
            "RUN_INTERVAL_SECONDS=%d must be positive; using default %s",
            value,
            DEFAULT_RUN_INTERVAL_SECONDS,
        )
        return DEFAULT_RUN_INTERVAL_SECONDS
    return value


async def rollup_once(factory) -> int:
    """One full recompute of all closed days. Returns upserted row count.

    The DELETE and the INSERT run back-to-back on ONE session (one
    transaction on both drivers): readers either see the previous complete
    snapshot or the new one, never a half-fed table. Any DB failure
    propagates to the caller (loop mode logs and retries next interval;
    compose restarts remain the last resort).
    """
    async with factory() as session:
        await session.execute(text(ROLLUP_DELETE_SQL))
        result = await session.execute(text(ROLLUP_INSERT_SQL))
        await session.commit()
    inserted = int(result.rowcount or 0)
    logger.info("cost rollup done: %d closed-day row(s) upserted", inserted)
    return inserted


async def run_loop(factory, *, run_interval_seconds: int) -> None:
    """Long-lived sidecar mode: recompute forever, sleeping between passes."""
    while True:
        started = time.monotonic()
        try:
            await rollup_once(factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Transient DB outages must not kill the sidecar; retry next interval.
            logger.exception("cost rollup failed; retrying next interval")
        slept = run_interval_seconds - (time.monotonic() - started)
        await asyncio.sleep(max(slept, 1.0))


def resolve_once_mode(*, flag_once: bool, flag_loop: bool, env_mode: str) -> bool:
    """Mode precedence: --once/--loop flags > COST_ROLLUP_MODE env > loop."""
    if flag_once:
        return True
    if flag_loop:
        return False
    return env_mode.strip().lower() == "once"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Daily per-team cost rollup recompute")
    parser.add_argument("--once", action="store_true", help="single pass then exit")
    parser.add_argument(
        "--loop", action="store_true", help="recompute forever every RUN_INTERVAL_SECONDS"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    once = resolve_once_mode(
        flag_once=args.once,
        flag_loop=args.loop,
        env_mode=os.environ.get("COST_ROLLUP_MODE", ""),
    )

    database_url = os.environ.get("HARNESS_DATABASE_URL", DEFAULT_DATABASE_URL)
    # M13-W3-H1a: engines come from the shared factory (pool math + doc law);
    # NullPool applies automatically on sqlite URLs.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from db import build_engine, make_session_factory

    engine = build_engine(database_url)
    factory = make_session_factory(engine)
    try:
        if once:
            await rollup_once(factory)
        else:
            await run_loop(factory, run_interval_seconds=run_interval_from_env())
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
