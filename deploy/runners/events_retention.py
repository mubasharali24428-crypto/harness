"""Retention sweeps: agent_events (partitioned), notification_outbox, audit_log.

Mission 12 G1 (raw-05 #1) introduced the batched agent_events DELETE loop;
Mission 19 N3 (pbc1 F4+F8, 10x-plan C9) turns agent_events retention into a
METADATA operation wherever possible and gives notification_outbox + audit_log
their first pruning paths:

  * migration 0028 swaps agent_events to a monthly RANGE-partitioned table on
    ts. This runner pre-creates forward month partitions on every sweep
    (ensure_forward_partitions), retires whole expired partitions with one
    DETACH PARTITION + DROP TABLE each (drop_expired_partitions — pure catalog
    metadata, no row churn, no dead tuples, no autovacuum debt), and keeps the
    Mission-12 bounded DELETE as the drain for boundary-month stragglers (the
    legacy partition spans -infinity..2026-09, so in-horizon rows inside it
    still leave via row deletes until its whole bound passes the cutoff).
  * notification_outbox: sent/failed rows older than OUTBOX_RETENTION_DAYS are
    purged in batches. QUEUED rows survive regardless of age — they are
    undelivered mail and need operator attention, not silent deletion.
  * audit_log: compliance-grade append-only, but prunable on a LONGER window
    (AUDIT_RETENTION_DAYS default 365 vs 30 for events/outbox) using the same
    batched shape; 0028's (ts, seq) index keeps every batch an index walk.

Wired as the profiled 'events-retention' compose sidecar (mirrors the Mission
10 walg pattern): `--profile events-retention up -d events-retention` runs the
loop; `run --rm events-retention python /opt/harness/deploy/runners/
events_retention.py --once` runs a single sweep (host cron friendly).

Deliberately self-contained: it creates its own engine from
HARNESS_DATABASE_URL and imports NOTHING from deploy.runners.services, whose
module-level HARNESS_MASTER_KEY fail-fast would kill a pure-DB sidecar that
needs no app secrets.

Replay honesty note: agent_events is the WS replay source. Retiring old
partitions means a reconnect with afterSeq older than the retention horizon
replays only the surviving tail — a seq gap at the boundary, which clients
already treat as "jump ahead" (app.py: overflow detection is bus-side flagged,
never seq arithmetic). Retention trades ancient replay for bounded growth;
30 days default keeps every operationally relevant window.

SQLite compatibility (which pattern, per the mission brief): section1 suites
build schema via harness_identity.models Base.metadata.create_all on
aiosqlite and NEVER execute numbered migrations (deploy/runners/migrate.py is
Postgres-only — 0023's DO bodies already rely on that). So this runner keeps
EVERY statement dual-dialect: partition catalog probes and DETACH/DROP are
guarded to postgresql and no-op (return 0) on sqlite, while the row-prune
DELETEs use the one shape both dialects accept (SQLite allows a LIMIT-ed
same-table subquery inside DELETE, unlike MySQL — verified empirically,
Mission 12 G1). Tests therefore exercise cutoff/batching/termination logic on
plain SQLite tables, and partition mechanics prove out on real PG
(HARNESS_TEST_PG probes / make pg-probe-migrate).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
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
# M19-N3 (F8): outbox matches the events window; audit_log is compliance-grade
# and gets the long window. Both still prunable — "append-only" was never
# supposed to mean "grows forever until backups hurt".
DEFAULT_OUTBOX_RETENTION_DAYS = 30
DEFAULT_AUDIT_RETENTION_DAYS = 365
DEFAULT_PARTITION_AHEAD_MONTHS = 2

EVENTS_TABLE = "agent_events"
OUTBOX_TABLE = "notification_outbox"
AUDIT_TABLE = "audit_log"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Partition bound text, e.g. FOR VALUES FROM ('2026-09-01 00:00:00+00') TO
# ('2026-10-01 00:00:00+00'); MINVALUE/MAXVALUE mark open-ended edges.
_BOUNDS_RE = re.compile(r"FROM \((MINVALUE|'[^']+')\) TO \((MINVALUE|MAXVALUE|'[^']+')\)")


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
        # M19-N3 additions (F8): defaults keep the dict additive — existing
        # deployments change behavior only by opting into shorter windows.
        "outbox_retention_days": get_int(
            "OUTBOX_RETENTION_DAYS", DEFAULT_OUTBOX_RETENTION_DAYS
        ),
        "audit_retention_days": get_int("AUDIT_RETENTION_DAYS", DEFAULT_AUDIT_RETENTION_DAYS),
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


def _as_utc(dt: datetime | None = None) -> datetime:
    """Normalize injected clocks the same way cutoff_for does."""
    dt = dt if dt is not None else datetime.now(UTC)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def build_prune_sql(table: str, ts_column: str, dialect_name: str) -> str:
    """Batched DELETE honoring a ts cutoff, parameterized throughout.

    One statement shape serves both dialects (SQLite allows a LIMIT-ed same-
    table subquery inside DELETE, unlike MySQL): an inner SELECT picks the
    oldest BATCH_SIZE expired keys and the outer DELETE removes them by PK —
    index-friendly (0028 gave audit_log (ts, seq); agent_events keeps the
    0018 ts probe) and race-safe under concurrent writers. `table`/`ts_column`
    come ONLY from this module's internal call sites, never user input.
    """
    if dialect_name in ("postgresql", "sqlite"):
        return (
            f"DELETE FROM {table} WHERE seq IN ("
            f"SELECT seq FROM {table} "
            f"WHERE {ts_column} < :cutoff ORDER BY seq LIMIT :batch)"
        )
    raise ValueError(f"unsupported dialect for retention sweep: {dialect_name}")


def build_delete_sql(dialect_name: str) -> str:
    """Mission-12 G1 entry point kept verbatim; now delegates to build_prune_sql."""
    return build_prune_sql(EVENTS_TABLE, "ts", dialect_name)


def build_outbox_prune_sql(dialect_name: str) -> str:
    """F8: purge finished outbox mail (sent/failed) older than the cutoff.

    QUEUED rows are deliberately exempt whatever their age — undelivered mail
    is an operator signal, not garbage (see module docstring). Harvest orders
    by created_at so the 0028 composite (status, created_at) answers the probe
    and the outer delete removes by PK.
    """
    if dialect_name in ("postgresql", "sqlite"):
        return (
            f"DELETE FROM {OUTBOX_TABLE} WHERE id IN ("
            f"SELECT id FROM {OUTBOX_TABLE} "
            f"WHERE created_at < :cutoff AND status IN ('sent', 'failed') "
            f"ORDER BY created_at LIMIT :batch)"
        )
    raise ValueError(f"unsupported dialect for retention sweep: {dialect_name}")


def _engine_of(factory):
    engine = factory.kw["bind"]
    return engine, engine.url.get_backend_name()


async def _loop_batches(session_factory, stmt, params, *, batch_size, max_batches) -> int:
    """Shared batch loop: commit-per-batch, stop on the first short batch."""
    deleted_total = 0
    batches = 0
    while True:
        async with session_factory() as session:
            result = await session.execute(stmt, params)
            await session.commit()
        deleted = int(result.rowcount or 0)
        deleted_total += deleted
        batches += 1
        if deleted < batch_size:
            break
        if max_batches is not None and batches >= max_batches:
            break
    return deleted_total


async def sweep_once(factory, *, retention_days: int, batch_size: int,
                     max_batches: int | None = None, now: datetime | None = None) -> int:
    """Mission-12 G1 contract, unchanged: batched DELETEs of agent_events past
    the cutoff until nothing is expired (or max_batches caps iterations).

    Under 0028 partitioning this is the DRAIN for boundary months: whole
    expired partitions never reach it (drop_expired_partitions removes them
    first), so in steady state it touches only the one month containing the
    cutoff boundary. Returns total rows deleted.
    """
    cutoff = cutoff_for(retention_days, now)
    _, dialect = _engine_of(factory)
    delete_sql = text(build_delete_sql(dialect))
    deleted_total = await _loop_batches(
        factory, delete_sql, {"cutoff": cutoff, "batch": batch_size},
        batch_size=batch_size, max_batches=max_batches,
    )
    logger.info(
        "retention sweep done: %d rows deleted, cutoff=%s",
        deleted_total, cutoff.isoformat(),
    )
    return deleted_total


async def prune_table_once(factory, *, table: str, ts_column: str, retention_days: int,
                           batch_size: int, max_batches: int | None = None,
                           now: datetime | None = None) -> int:
    """Generic batched ts-cutoff prune (M19-N3): audit_log today, any future
    growing append-only table tomorrow. Same termination semantics as
    sweep_once; caller supplies the table's own retention window."""
    cutoff = cutoff_for(retention_days, now)
    _, dialect = _engine_of(factory)
    stmt = text(build_prune_sql(table, ts_column, dialect))
    deleted = await _loop_batches(
        factory, stmt, {"cutoff": cutoff, "batch": batch_size},
        batch_size=batch_size, max_batches=max_batches,
    )
    logger.info("%s prune done: %d rows deleted, cutoff=%s", table, deleted, cutoff.isoformat())
    return deleted


async def prune_outbox_once(factory, *, retention_days: int, batch_size: int,
                            max_batches: int | None = None,
                            now: datetime | None = None) -> int:
    """F8: notification_outbox gets its first pruning path — sent/failed rows
    older than the window go in batches; queued rows are never touched."""
    cutoff = cutoff_for(retention_days, now)
    _, dialect = _engine_of(factory)
    stmt = text(build_outbox_prune_sql(dialect))
    deleted = await _loop_batches(
        factory, stmt, {"cutoff": cutoff, "batch": batch_size},
        batch_size=batch_size, max_batches=max_batches,
    )
    logger.info("notification_outbox prune done: %d rows deleted, cutoff=%s",
                deleted, cutoff.isoformat())
    return deleted


# ---------------------------------------------------------------------------
# M19-N3 — partition mechanics (Postgres only; no-ops on SQLite).
# ---------------------------------------------------------------------------


def month_start(dt: datetime) -> datetime:
    """First instant of dt's month, normalized like every other clock here."""
    d = _as_utc(dt)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month(dt: datetime) -> datetime:
    """First instant of the following month (day-28 +4d rollforward trick)."""
    d = month_start(dt).replace(day=28)
    return (d + timedelta(days=4)).replace(day=1)


def partition_name(month_first: datetime) -> str:
    """Canonical child-table name for a monthly range partition."""
    return f"{EVENTS_TABLE}_{month_first:%Y_%m}"


def _parse_bound_edge(token: str) -> datetime | None:
    """One bound edge; MINVALUE/MAXVALUE (or garbage) -> None = open-ended."""
    token = token.strip()
    if token in ("MINVALUE", "MAXVALUE"):
        return None
    try:
        parsed = datetime.fromisoformat(token.strip("'"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _bound_upper(bound: str) -> datetime | None:
    """Upper range edge from pg_get_expr(relpartbound) text; None = open-ended
    or unparseable (logged by the caller)."""
    m = _BOUNDS_RE.search(bound or "")
    return _parse_bound_edge(m.group(2)) if m else None


def _bound_lower(bound: str) -> datetime | None:
    """Lower range edge from pg_get_expr(relpartbound) text."""
    m = _BOUNDS_RE.search(bound or "")
    return _parse_bound_edge(m.group(1)) if m else None


async def list_event_partitions(factory) -> list[tuple[str, datetime | None, datetime | None]]:
    """Direct children of agent_events as (name, lower_bound, upper_bound).

    Catalog-only read (pg_inherits/pg_class) — no locks beyond AccessShare.
    On SQLite there is no inheritance machinery: returns [].
    """
    _, dialect = _engine_of(factory)
    if dialect != "postgresql":
        return []
    sql = text(
        "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound "
        "FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid "
        f"WHERE i.inhparent = '{EVENTS_TABLE}'::regclass ORDER BY c.relname"
    )
    async with factory() as session:
        rows = (await session.execute(sql)).fetchall()
    return [
        (str(r[0]), _bound_lower(str(r[1])), _bound_upper(str(r[1]))) for r in rows
    ]


async def ensure_forward_partitions(factory, *, now: datetime | None = None,
                                    ahead_months: int = DEFAULT_PARTITION_AHEAD_MONTHS) -> int:
    """Pre-create this month plus `ahead_months` forward month partitions.

    Runs FIRST in every sweep so writers never hit a missing-partition error
    while a sweep is mid-flight (and fresh installs work before the first
    sweep ever runs — 0028 seeds two months, this keeps rolling forward).
    Idempotent: existing partitions are skipped, not rebuilt.
    """
    _, dialect = _engine_of(factory)
    if dialect != "postgresql":
        return 0  # SQLite test path: plain tables, nothing to pre-create
    base = month_start(_as_utc(now))
    # Rolling cursor (NOT a listcomp of next_month(base): that yields the SAME
    # month ahead_months+1 times and silently creates nothing new).
    months: list[datetime] = []
    cursor = base
    for _ in range(ahead_months + 1):
        months.append(cursor)
        cursor = next_month(cursor)
    # Coverage-aware: a wide partition (e.g. 0028's legacy twin owning
    # -infinity..2026-09) already covers the current month's window; creating
    # a monthly child there would overlap-error. Only uncovered months get
    # children. (Caught live: the very first probe run hit exactly this.)
    existing = await list_event_partitions(factory)
    covered_windows = [(lo, hi) for _, lo, hi in existing]

    def _covered(m_start: datetime) -> bool:
        m_end = next_month(m_start)
        for lo, hi in covered_windows:
            lo_ok = lo is None or lo <= m_start
            hi_ok = hi is None or hi >= m_end
            if lo_ok and hi_ok:
                return True
        return False

    created = 0
    for m_start in months:
        name = partition_name(m_start)
        if _covered(m_start) or not _IDENT_RE.match(name):
            continue
        lo, hi = m_start, next_month(m_start)
        # PK(seq) per child mirrors migration 0028's forward partitions —
        # parent-level unique constraints are illegal on a ts-partitioned
        # table, so the uniqueness law lives per partition (see 0028 header).
        ddl = text(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {EVENTS_TABLE} "
            f"FOR VALUES FROM ('{lo:%Y-%m-%dT%H:%M:%SZ}') TO ('{hi:%Y-%m-%dT%H:%M:%SZ}')"
        )
        pk_ddl = text(f"ALTER TABLE {name} ADD PRIMARY KEY (seq)")
        async with factory() as session:
            await session.execute(ddl)
            await session.execute(pk_ddl)
            await session.commit()
        created += 1
        logger.info("created partition %s [%s .. %s)", name, lo.date(), hi.date())
    return created


async def drop_expired_partitions(factory, *, cutoff: datetime,
                                  max_partitions: int | None = None) -> int:
    """Retire every partition whose UPPER bound <= cutoff: one DETACH PARTITION
    + DROP TABLE per partition, each in its own short transaction.

    This replaces the DELETE churn war: N million rows vanish as catalog
    metadata — no dead tuples, no autovacuum debt, no index bloat. The legacy
    wide partition (-infinity..2026-09) qualifies naturally the month its
    upper bound finally passes the cutoff; before that its in-horizon rows
    drain via sweep_once's bounded DELETEs. Unparseable bounds are skipped
    loudly rather than guessed at.
    """
    _, dialect = _engine_of(factory)
    if dialect != "postgresql":
        return 0
    dropped = 0
    for name, _lower, upper in await list_event_partitions(factory):
        if max_partitions is not None and dropped >= max_partitions:
            break
        if upper is None:
            logger.warning("partition %s has an unreadable bound; skipping", name)
            continue
        if upper > cutoff or not _IDENT_RE.match(name):
            continue
        async with factory() as session:
            await session.execute(text(f"ALTER TABLE {EVENTS_TABLE} DETACH PARTITION {name}"))
            await session.execute(text(f"DROP TABLE IF EXISTS {name}"))
            await session.commit()
        dropped += 1
        logger.info("dropped expired partition %s (upper %s <= cutoff %s)",
                    name, upper.date(), cutoff.date())
    return dropped


async def run_sweep(factory, *, retention_days: int, batch_size: int,
                    outbox_retention_days: int, audit_retention_days: int,
                    now: datetime | None = None) -> dict[str, int]:
    """One full retention pass across all three tables + partition hygiene.

    Order matters: partitions are pre-created before anything writes, expired
    ones are retired BEFORE row deletes (cheaper first: metadata beats churn),
    then the boundary drain and the two F8 prunes run. Summary dict is for
    logs/tests; every step degrades independently to a SQLite no-op.
    """
    now_utc = _as_utc(now)
    events_cutoff = cutoff_for(retention_days, now_utc)
    summary = {
        "partitions_created": await ensure_forward_partitions(factory, now=now_utc),
        "partitions_dropped": await drop_expired_partitions(factory, cutoff=events_cutoff),
        "agent_events_deleted": await sweep_once(
            factory, retention_days=retention_days, batch_size=batch_size, now=now_utc
        ),
        "notification_outbox_deleted": await prune_outbox_once(
            factory, retention_days=outbox_retention_days, batch_size=batch_size, now=now_utc
        ),
        "audit_log_deleted": await prune_table_once(
            factory, table=AUDIT_TABLE, ts_column="ts",
            retention_days=audit_retention_days, batch_size=batch_size, now=now_utc,
        ),
    }
    logger.info("retention pass summary: %s", summary)
    return summary


async def run_loop(factory, *, retention_days: int, batch_size: int,
                   run_interval_seconds: int, outbox_retention_days: int =
                   DEFAULT_OUTBOX_RETENTION_DAYS,
                   audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS) -> None:
    """Long-lived sidecar mode: full retention pass forever, sleep between."""
    while True:
        started = time.monotonic()
        try:
            await run_sweep(
                factory,
                retention_days=retention_days,
                batch_size=batch_size,
                outbox_retention_days=outbox_retention_days,
                audit_retention_days=audit_retention_days,
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
    parser = argparse.ArgumentParser(description="Batched retention sweeps (events/outbox/audit)")
    parser.add_argument("--once", action="store_true", help="single pass then exit")
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
    # M13-W3-H1a: engines come from the shared factory (pool math + doc law);
    # NullPool applies automatically on sqlite URLs.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from db import build_engine, make_session_factory

    engine = build_engine(database_url)
    factory = make_session_factory(engine)
    try:
        if once:
            await run_sweep(
                factory,
                retention_days=cfg["retention_days"],
                batch_size=cfg["batch_size"],
                outbox_retention_days=cfg["outbox_retention_days"],
                audit_retention_days=cfg["audit_retention_days"],
            )
        else:
            await run_loop(
                factory,
                retention_days=cfg["retention_days"],
                batch_size=cfg["batch_size"],
                run_interval_seconds=cfg["run_interval_seconds"],
                outbox_retention_days=cfg["outbox_retention_days"],
                audit_retention_days=cfg["audit_retention_days"],
            )
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
