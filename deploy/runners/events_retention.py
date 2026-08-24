"""agent_events retention sweep (Mission 12 G1, raw-05 #1).

agent_events grows without bound — every EventBus publish lands here and
nothing ever prunes it. This runner deletes rows older than RETENTION_DAYS in
BATCH_SIZE chunks inside SEPARATE short transactions, so a huge backlog never
holds one long lock/transaction open against the hottest table in the system.

Wired as the profiled 'events-retention' compose sidecar (mirrors the Mission
10 walg pattern): `--profile events-retention up -d events-retention` runs the
loop; `run --rm events-retention python /opt/harness/deploy/runners/
events_retention.py --once` runs a single sweep (host cron friendly).

Deliberately self-contained: it creates its own engine from
HARNESS_DATABASE_URL and imports NOTHING from deploy.runners.services, whose
module-level HARNESS_MASTER_KEY fail-fast would kill a pure-DB sidecar that
needs no app secrets.

Replay honesty note: agent_events is the WS replay source. Deleting old rows
means a reconnect with afterSeq older than the retention horizon replays only
the surviving tail — a seq gap at the boundary, which clients already treat as
"jump ahead" (app.py: overflow detection is bus-side flagged, never seq
arithmetic). Retention trades ancient replay for bounded growth; 30 days
default keeps every operationally relevant window.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

HARNESS_ROOT = Path(__file__).resolve().parents[2]
for section in sorted(HARNESS_ROOT.glob("section*")):
    src = section / "src"
    if src.exists():
        sys.path.insert(0, str(src))

logger = logging.getLogger("events-retention")

DEFAULT_DATABASE_URL = "postgresql+asyncpg://harness:harness@db:5432/harness"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_BATCH_SIZE = 5000
DEFAULT_RUN_INTERVAL_SECONDS = 6 * 60 * 60  # four sweeps a day


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw.strip() else default
    except ValueError:
        logger.warning("%s=%r is not an integer; using default %s", name, raw, default)
        return default


def retention_config_from_env(env: Mapping[str, str] | None = None) -> dict[str, int]:
    """Sane-knob parsing, injectable for tests (defaults when unset/blank/garbage)."""
    env_map: Mapping[str, str] = os.environ if env is None else env

    def get_int(name: str, default: int) -> int:
        try:
            return int(env_map.get(name, "") or default)
        except ValueError:
            logger.warning(
                "%s=%r is not an integer; using default %s", name, env_map.get(name), default
            )
            return default

    cfg = {
        "retention_days": get_int("RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
        "batch_size": get_int("BATCH_SIZE", DEFAULT_BATCH_SIZE),
        "run_interval_seconds": get_int("RUN_INTERVAL_SECONDS", DEFAULT_RUN_INTERVAL_SECONDS),
    }
    for key, value in cfg.items():
        if value <= 0:
            raise ValueError(f"{key} must be positive, got {value}")
    return cfg


def cutoff_for(retention_days: int, now: datetime | None = None) -> datetime:
    """The delete horizon. Injected clock keeps tests deterministic.

    Naive datetimes are assumed UTC (SQLite stores naive; Postgres TIMESTAMPTZ
    columns read back aware). Comparisons happen between like-shaped values.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now - timedelta(days=retention_days)


def build_delete_sql(dialect_name: str) -> str:
    """Batched DELETE honoring the cutoff, parameterized throughout.

    One statement shape serves both dialects (SQLite allows a LIMIT-ed same-
    table subquery inside DELETE, unlike MySQL): an inner SELECT picks the
    oldest BATCH_SIZE expired seqs and the outer DELETE removes them by PK —
    index-friendly (0014's ts index serves the probe; the PK set drives the
    delete) and race-safe under concurrent writers.
    """
    if dialect_name in ("postgresql", "sqlite"):
        return (
            "DELETE FROM agent_events WHERE seq IN ("
            "SELECT seq FROM agent_events "
            "WHERE ts < :cutoff ORDER BY seq LIMIT :batch)"
        )
    raise ValueError(f"unsupported dialect for retention sweep: {dialect_name}")


async def sweep_once(factory, *, retention_days: int, batch_size: int,
                     max_batches: int | None = None, now: datetime | None = None) -> int:
    """One full sweep: loop batched DELETEs until nothing is expired.

    Each iteration commits its own short transaction; the loop terminates when
    a batch deletes fewer than `batch_size` rows (nothing left past cutoff).
    Returns total rows deleted. `max_batches` caps iterations (tests).
    """
    cutoff = cutoff_for(retention_days, now)
    engine = factory.kw["bind"]
    dialect = engine.url.get_backend_name()
    delete_sql = text(build_delete_sql(dialect))
    deleted_total = 0
    batches = 0
    while True:
        async with factory() as session:
            result = await session.execute(delete_sql, {"cutoff": cutoff, "batch": batch_size})
            await session.commit()
        deleted = int(result.rowcount or 0)
        deleted_total += deleted
        batches += 1
        if deleted < batch_size:
            break
        if max_batches is not None and batches >= max_batches:
            break
    logger.info(
        "retention sweep done: %d rows deleted across %d batch(es), cutoff=%s",
        deleted_total, batches, cutoff.isoformat(),
    )
    return deleted_total


async def run_loop(factory, *, retention_days: int, batch_size: int,
                   run_interval_seconds: int) -> None:
    """Long-lived sidecar mode: sweep forever, sleeping between runs."""
    while True:
        started = time.monotonic()
        try:
            await sweep_once(
                factory, retention_days=retention_days, batch_size=batch_size
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Transient DB outages must not kill the sidecar (compose restarts
            # are a last resort); log loudly and retry next interval.
            logger.exception("retention sweep failed; retrying next interval")
        slept = run_interval_seconds - (time.monotonic() - started)
        await asyncio.sleep(max(slept, 1.0))


def resolve_once_mode(*, flag_once: bool, flag_loop: bool, env_mode: str) -> bool:
    """Mode precedence: --once/--loop flags > EVENTS_RETENTION_MODE env > loop."""
    if flag_once:
        return True
    if flag_loop:
        return False
    return env_mode.strip().lower() == "once"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Batched agent_events retention sweep")
    parser.add_argument("--once", action="store_true", help="single sweep then exit")
    parser.add_argument(
        "--loop", action="store_true", help="sweep forever every RUN_INTERVAL_SECONDS"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    once = resolve_once_mode(
        flag_once=args.once,
        flag_loop=args.loop,
        env_mode=os.environ.get("EVENTS_RETENTION_MODE", ""),
    )

    database_url = os.environ.get("HARNESS_DATABASE_URL", DEFAULT_DATABASE_URL)
    cfg = retention_config_from_env()
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if once:
            await sweep_once(
                factory, retention_days=cfg["retention_days"], batch_size=cfg["batch_size"]
            )
        else:
            await run_loop(
                factory,
                retention_days=cfg["retention_days"],
                batch_size=cfg["batch_size"],
                run_interval_seconds=cfg["run_interval_seconds"],
            )
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
