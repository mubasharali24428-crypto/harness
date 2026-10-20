"""Mission 19 N3 — partitioned retention tests (pbc1 F4+F8, 10x-plan C9).

Extends test_events_retention.py (M12 G1) without disturbing its pins:
partition mechanics, the outbox/audit prune paths, and — the OPS-15 lesson —
a fresh-install migrate() walk that actually executes migrate() over every
numbered migration (suites otherwise create_all and never run it).

Dialect split, per deploy/runners/events_retention.py docstring: cutoff/
batching logic runs on plain SQLite tables (create_all schema); partition
catalog/DETACH/DROP mechanics are PG-only and prove out under HARNESS_TEST_PG
against a scratch database that is dropped afterwards.
"""

import asyncio
import importlib.util
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from harness_identity.models import AgentEvent, AuditLog, Project, Team
from harness_identity.notifier import NotificationOutbox

HARNESS_ROOT = Path(__file__).resolve().parents[2]
RUNNERS_DIR = HARNESS_ROOT / "deploy" / "runners"

# Same by-path import trick as test_events_retention.py / test_migrate_drift_gate.py.
_spec = importlib.util.spec_from_file_location(
    "events_retention_m19", RUNNERS_DIR / "events_retention.py"
)
assert _spec is not None and _spec.loader is not None  # noqa: S101 — structural, not a claim
er = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(RUNNERS_DIR))
_spec.loader.exec_module(er)

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


async def _make_project(db) -> str:
    """agent_events.project_id has an FK into projects; satisfy it honestly."""
    async with db() as session:
        team = Team(name="m19n3-team")
        session.add(team)
        await session.flush()
        project = Project(team_id=team.id, name="m19n3-proj")
        session.add(project)
        await session.commit()
        return project.id


async def _add_event(db, project_id: str, age_days: float) -> int:
    ts = NOW - timedelta(days=age_days)
    async with db() as session:
        row = AgentEvent(project_id=project_id, type="audit.test", payload={}, ts=ts)
        session.add(row)
        await session.flush()
        seq = int(row.seq)
        await session.commit()
        return seq


# ---------------------------------------------------------------------------
# Partition-name math (pure functions — dialect free).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dt", "expected"),
    [
        (datetime(2026, 8, 23, 12, 0, tzinfo=UTC), "agent_events_2026_08"),
        (datetime(2026, 1, 1, tzinfo=UTC), "agent_events_2026_01"),
        (datetime(2026, 12, 31, 23, 59, tzinfo=UTC), "agent_events_2026_12"),
    ],
)
def test_partition_name_format(dt, expected):
    assert er.partition_name(er.month_start(dt)) == expected


def test_next_month_crosses_year():
    assert er.next_month(datetime(2026, 12, 5, tzinfo=UTC)) == datetime(2027, 1, 1, tzinfo=UTC)


def test_month_start_normalizes():
    ms = er.month_start(datetime(2026, 8, 23, 15, 42, 7, tzinfo=UTC))
    assert (ms.day, ms.hour, ms.minute, ms.second, ms.microsecond) == (1, 0, 0, 0, 0)


def test_bound_parser_reads_both_shapes():
    legacy = "FOR VALUES FROM (MINVALUE) TO ('2026-09-01 00:00:00+00')"
    monthly = "FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00')"
    assert er._bound_upper(legacy) == datetime(2026, 9, 1, tzinfo=UTC)
    assert er._bound_upper(monthly) == datetime(2026, 10, 1, tzinfo=UTC)
    assert er._bound_upper("FOR VALUES FROM (MINVALUE) TO (MAXVALUE)") is None


# ---------------------------------------------------------------------------
# Config additions (additive keys; existing pins in test_events_retention.py hold).
# ---------------------------------------------------------------------------


def test_config_includes_f8_windows():
    cfg = er.retention_config_from_env({})
    assert cfg["outbox_retention_days"] == 30
    assert cfg["audit_retention_days"] == 365


def test_config_parses_new_env_knobs():
    cfg = er.retention_config_from_env(
        {"OUTBOX_RETENTION_DAYS": "14", "AUDIT_RETENTION_DAYS": "90"}
    )
    assert cfg["outbox_retention_days"] == 14
    assert cfg["audit_retention_days"] == 90


@pytest.mark.parametrize("key", ["OUTBOX_RETENTION_DAYS", "AUDIT_RETENTION_DAYS"])
def test_config_rejects_non_positive_new_knobs(key):
    with pytest.raises(ValueError, match="positive"):
        er.retention_config_from_env({key: "-1"})


# ---------------------------------------------------------------------------
# SQL builders — parameterization + dialect law (SQLite test path documented).
# ---------------------------------------------------------------------------


def test_build_prune_sql_is_parameterized_and_dialect_aware():
    pg = er.build_prune_sql("audit_log", "ts", "postgresql")
    lite = er.build_prune_sql("audit_log", "ts", "sqlite")
    for sql in (pg, lite):
        assert ":cutoff" in sql and ":batch" in sql
        assert "DELETE FROM audit_log" in sql and "ts < :cutoff" in sql
    with pytest.raises(ValueError, match="dialect"):
        er.build_prune_sql("audit_log", "ts", "mysql")


def test_build_delete_sql_still_serves_the_mission12_contract():
    sql = er.build_delete_sql("postgresql")
    assert ":cutoff" in sql and ":batch" in sql and "LIMIT :batch" in sql
    assert "agent_events" in sql and "ts < :cutoff" in sql


def test_outbox_prune_spares_queued_rows():
    for dialect in ("postgresql", "sqlite"):
        sql = er.build_outbox_prune_sql(dialect)
        assert "status IN ('sent', 'failed')" in sql
        assert ":cutoff" in sql and ":batch" in sql
    with pytest.raises(ValueError, match="dialect"):
        er.build_outbox_prune_sql("mysql")


# ---------------------------------------------------------------------------
# Outbox prune behavior (F8) — SQLite fixture path.
# ---------------------------------------------------------------------------


async def _add_outbox(db, status: str, age_days: float) -> str:
    async with db() as session:
        row = NotificationOutbox(
            to_email="ops@example.com",
            template="run_finished",
            payload={},
            status=status,
            created_at=NOW - timedelta(days=age_days),
        )
        session.add(row)
        await session.commit()
        return str(row.id)


async def _outbox_count(db) -> int:
    async with db() as session:
        rows = await session.execute(text("SELECT count(*) FROM notification_outbox"))
        return int(rows.scalar_one())


async def test_outbox_prune_deletes_only_old_finished_rows(db):
    await _add_outbox(db, "sent", 40.0)
    await _add_outbox(db, "failed", 35.0)
    recent = await _add_outbox(db, "sent", 5.0)  # recent finished survives
    stale_queued = await _add_outbox(db, "queued", 40.0)  # OLD BUT QUEUED survives

    deleted = await er.prune_outbox_once(db, retention_days=30, batch_size=5000, now=NOW)

    assert deleted == 2
    assert await _outbox_count(db) == 2
    async with db() as session:
        ids = {str(r[0]) for r in (
            await session.execute(text("SELECT id FROM notification_outbox"))
        ).fetchall()}
    assert ids == {recent, stale_queued}


async def test_outbox_prune_batches_and_terminates(db):
    for i in range(7):
        await _add_outbox(db, "sent", 40.0 + i)
    for _ in range(3):  # old queued rows must survive a multi-batch run untouched
        await _add_outbox(db, "queued", 50.0)

    deleted = await er.prune_outbox_once(db, retention_days=30, batch_size=3, now=NOW)

    assert deleted == 7
    assert await _outbox_count(db) == 3
    async with db() as session:
        statuses = (await session.execute(
            text("SELECT DISTINCT status FROM notification_outbox")
        )).scalars().all()
    assert statuses == ["queued"]


# ---------------------------------------------------------------------------
# Audit prune behavior (F8) — longer window, same batched shape.
# ---------------------------------------------------------------------------


async def _add_audit(db, age_days: float) -> int:
    async with db() as session:
        row = AuditLog(
            actor_kind="human",
            action="team.login",
            target_type="team",
            target_id="t-1",
            detail={},
            ts=NOW - timedelta(days=age_days),
        )
        session.add(row)
        await session.flush()
        seq = int(row.seq)
        await session.commit()
        return seq


async def test_audit_prune_honors_long_window(db):
    for age in (400.0, 380.0, 370.0):  # past the 365-day horizon
        await _add_audit(db, age)
    survivors = {await _add_audit(db, age) for age in (300.0, 100.0)}  # inside window

    deleted = await er.prune_table_once(
        db, table="audit_log", ts_column="ts",
        retention_days=365, batch_size=5000, now=NOW,
    )

    assert deleted == 3
    async with db() as session:
        seqs = {int(r[0]) for r in (
            await session.execute(text("SELECT seq FROM audit_log"))
        ).fetchall()}
    assert seqs == survivors


async def test_audit_prune_batching_terminates(db):
    for _ in range(9):
        await _add_audit(db, 400.0)

    deleted = await er.prune_table_once(
        db, table="audit_log", ts_column="ts",
        retention_days=365, batch_size=4, now=NOW,
    )

    assert deleted == 9
    async with db() as session:
        n = int((await session.execute(text("SELECT count(*) FROM audit_log"))).scalar_one())
    assert n == 0


# ---------------------------------------------------------------------------
# run_sweep orchestration — SQLite degrades partition steps to honest no-ops.
# ---------------------------------------------------------------------------


async def test_run_sweep_covers_all_three_tables_on_sqlite(db):
    project = await _make_project(db)
    await _add_event(db, project, 40.0)          # events past horizon
    await _add_outbox(db, "sent", 40.0)          # outbox past horizon
    await _add_outbox(db, "queued", 40.0)        # queued survives
    await _add_audit(db, 400.0)                  # audit past ITS horizon
    await _add_audit(db, 300.0)                  # inside audit window

    summary = await er.run_sweep(
        db, retention_days=30, batch_size=5000,
        outbox_retention_days=30, audit_retention_days=365, now=NOW,
    )

    assert summary["partitions_created"] == 0
    assert summary["partitions_dropped"] == 0
    assert summary["agent_events_deleted"] == 1
    assert summary["notification_outbox_deleted"] == 1
    assert summary["audit_log_deleted"] == 1


async def test_run_loop_passes_f8_windows_through(monkeypatch, db):
    seen: dict = {}

    async def fake_run_sweep(factory, **kwargs):
        seen.update(kwargs)
        return {"partitions_created": 0, "partitions_dropped": 0}

    monkeypatch.setattr(er, "run_sweep", fake_run_sweep)
    task = asyncio.create_task(
        er.run_loop(db, retention_days=30, batch_size=10, run_interval_seconds=1,
                    outbox_retention_days=14, audit_retention_days=200)
    )
    try:
        for _ in range(300):
            if seen:
                break
            await asyncio.sleep(0.01)
        assert seen.get("outbox_retention_days") == 14
        assert seen.get("audit_retention_days") == 200
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# Fresh-install probe (OPS-15 lesson): suites never exercised migrate().
#
# Dialect honesty: migrate() is Postgres-only by construction — the shipped
# chain contains plpgsql bodies (0003/0023) no SQLite can parse, which is
# WHY nothing ever exercised it from pytest. The portable half below pins
# what SQLite CAN prove (chain integrity, splitter, drift gate); the REAL
# full-walk-from-empty-DB proof runs under HARNESS_TEST_PG in
# test_pg_fresh_install_partitions_end_to_end and nightly via
# `make pg-probe-migrate`.
# ---------------------------------------------------------------------------


def test_fresh_install_chain_has_no_duplicate_prefixes():
    """Duplicate version prefixes kill every fresh install at the ledger PK
    insert (W5 close-out 2026-08-24). The ls-check, pinned forever."""
    versions = Path(HARNESS_ROOT, "section1", "migrations").glob("*.sql")
    prefixes = [p.name.split("_", 1)[0] for p in sorted(versions)]
    assert len(prefixes) == len(set(prefixes)), "duplicate migration prefix on disk"


def test_fresh_install_splitter_handles_every_shipped_file():
    """Every migration file must yield executable non-empty statements through
    THE splitter migrate() actually uses ($$-aware; comment lines stripped)."""
    sys.path.insert(0, str(RUNNERS_DIR))
    import migrate as migrate_runner

    migrations_dir = Path(HARNESS_ROOT, "section1", "migrations")
    for path in sorted(migrations_dir.glob("*.sql")):
        stmts = migrate_runner.split_statements(path.read_text())
        assert stmts, f"{path.name} split to zero statements"


async def test_fresh_install_drift_gate_accepts_empty_db():
    """A brand-new database (no schema_migrations at all) is 'nothing applied
    yet', never drift — the M18-C4 lesson; pinned here because this is the
    state every fresh install boots from."""
    sys.path.insert(0, str(RUNNERS_DIR))
    import migrate as migrate_runner

    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        await migrate_runner.check_drift(engine)  # must not raise
    finally:
        await engine.dispose()


def _scratch_url(base_url: str, dbname: str) -> str:
    head, _, _ = base_url.rpartition("/")
    return f"{head}/{dbname}"


# The live probes below were authored against the Aug-2026 epoch the migration
# chain pins: 0028 ships STATIC 2026_09/2026_10 partitions and the seed
# events/cutoffs name absolute dates. Passing `now` explicitly (the retention
# runner's deterministic seam) keeps them honest forever instead of rotting
# with the wall clock.
_FROZEN_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@pytest.mark.skipif(not os.environ.get("HARNESS_TEST_PG"), reason="needs HARNESS_TEST_PG")
async def test_pg_fresh_install_partitions_end_to_end():
    """The REAL probe: empty scratch PG DB -> full migrate() -> partitioned
    agent_events (legacy + forward partitions), identity intact, NOTIFY
    trigger reattached, inserts keep working, forward partitions pre-create."""
    sys.path.insert(0, str(RUNNERS_DIR))
    import migrate as migrate_runner

    base_url = os.environ["HARNESS_DATABASE_URL"]
    dbname = f"harness_n3_probe_{uuid.uuid4().hex[:8]}"
    scratch = _scratch_url(base_url, dbname)

    admin_engine = create_async_engine(base_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    engine = create_async_engine(scratch, poolclass=NullPool)
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))

        applied = await migrate_runner.migrate(engine)
        assert any(v.startswith("0028") for v in applied), applied

        async with engine.connect() as conn:
            kind = (await conn.execute(text(
                "SELECT relkind FROM pg_class WHERE oid = 'agent_events'::regclass"
            ))).scalar_one()
            parts = (await conn.execute(text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_inherits i ON i.inhrelid = c.oid "
                "WHERE i.inhparent = 'agent_events'::regclass ORDER BY c.relname"
            ))).fetchall()
            trig = (await conn.execute(text(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgrelid = 'agent_events'::regclass "
                "AND tgname = 'trg_notify_agent_events' AND NOT tgisinternal"
            ))).scalar_one()
            default_is_seq = (await conn.execute(text(
                "SELECT column_default LIKE 'nextval%' FROM information_schema.columns "
                "WHERE table_name = 'agent_events' AND column_name = 'seq'"
            ))).scalar_one()

        assert (kind.decode() if isinstance(kind, bytes) else str(kind)) == "p"
        names = {str(r[0]) for r in parts}
        assert "agent_events_legacy" in names
        assert {"agent_events_2026_09", "agent_events_2026_10"} <= names
        assert int(trig) >= 1
        assert bool(default_is_seq)

        # Identity continuity + FK law intact on the new parent.
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO teams (id, name) VALUES ('t-n3', 'n3-team')"
            ))
            await conn.execute(text(
                "INSERT INTO projects (id, team_id, name) VALUES ('p-n3', 't-n3', 'n3-probe')"
            ))
            await conn.execute(text(
                "INSERT INTO agent_events (project_id, type, payload, ts) "
                "VALUES ('p-n3', 'probe.event', '{}', now())"
            ))
        async with engine.connect() as conn:
            seqs = (await conn.execute(text("SELECT seq FROM agent_events"))).fetchall()
        assert len(seqs) == 1 and int(seqs[0][0]) >= 1

        # Retention pass against the live-partitioned table: must not error,
        # must not drop anything (cutoff is 30d back; every partition's upper
        # bound is in the future).
        factory = async_sessionmaker(engine, expire_on_commit=False)
        summary = await er.run_sweep(
            factory, retention_days=30, batch_size=5000,
            outbox_retention_days=30, audit_retention_days=365,
            now=_FROZEN_NOW,
        )
        assert summary["partitions_dropped"] == 0

        # Coverage-aware forward roll: legacy covers THIS month, 09/10 exist
        # from 0028 and migrate() rolled on to the real month + 2, so reaching
        # one month past that must create exactly that one (and skip the
        # covered windows without overlap errors — the bug the first probe
        # run caught live).
        real = datetime.now(UTC)
        ahead = ((real.year - _FROZEN_NOW.year) * 12 + real.month - _FROZEN_NOW.month
                 + er.DEFAULT_PARTITION_AHEAD_MONTHS + 1)
        created = await er.ensure_forward_partitions(factory, ahead_months=ahead, now=_FROZEN_NOW)
        assert created == 1
    finally:
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        await admin_engine.dispose()


@pytest.mark.skipif(not os.environ.get("HARNESS_TEST_PG"), reason="needs HARNESS_TEST_PG")
async def test_pg_partition_pruning_explain_and_detach_drop():
    """F4's headline claims, proven against the real planner:
      1. EXPLAIN prunes: a ts-bounded month query touches exactly that
         month's child — never the legacy wide partition.
      2. Retention as metadata op: drop_expired_partitions DETACHes+DROPes
         whole expired partitions in one pass each; the parent keeps serving;
         ensure_forward_partitions re-seeds the rolling window afterwards."""
    sys.path.insert(0, str(RUNNERS_DIR))
    import migrate as migrate_runner

    base_url = os.environ["HARNESS_DATABASE_URL"]
    dbname = f"harness_n3_prune_{uuid.uuid4().hex[:8]}"
    scratch = _scratch_url(base_url, dbname)

    admin_engine = create_async_engine(base_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    engine = create_async_engine(scratch, poolclass=NullPool)
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
        await migrate_runner.migrate(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        # Seed forward months so November inserts have a home (0028 ships
        # 09+10; legacy covers August).
        await er.ensure_forward_partitions(factory, ahead_months=4, now=_FROZEN_NOW)

        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO teams (id, name) VALUES ('t-p', 'prune')"))
            await conn.execute(text(
                "INSERT INTO projects (id, team_id, name) VALUES ('p-p', 't-p', 'prune-proj')"
            ))
            await conn.execute(text(
                "INSERT INTO agent_events (project_id, type, payload, ts) VALUES "
                "('p-p', 'old.event', '{}', '2026-05-10T00:00:00Z'), "
                "('p-p', 'sep.event', '{}', '2026-09-15T12:00:00Z'), "
                "('p-p', 'nov.event', '{}', '2026-11-20T08:00:00Z'), "
                "('p-p', 'dec.event', '{}', '2026-12-05T08:00:00Z')"
            ))

        # --- (1) Partition-pruning EXPLAIN proof -------------------------
        async with engine.connect() as conn:
            plan_rows = (
                await conn.execute(text(
                    "EXPLAIN SELECT count(*) FROM agent_events "
                    "WHERE ts >= '2026-09-01T00:00:00Z' "
                    "AND ts < '2026-10-01T00:00:00Z'"
                ))
            ).fetchall()
        plan = "\n".join(str(r[0]) for r in plan_rows)
        assert "agent_events_2026_09" in plan, plan  # the right month IS scanned
        assert "agent_events_legacy" not in plan, plan  # history is PRUNED away
        assert "agent_events_2026_10" not in plan, plan  # neighbors too

        async with engine.connect() as conn:
            n_sep = int((await conn.execute(text(
                "SELECT count(*) FROM agent_events "
                "WHERE ts >= '2026-09-01T00:00:00Z' AND ts < '2026-10-01T00:00:00Z'"
            ))).scalar_one())
        assert n_sep == 1  # and the pruned plan still answers correctly

        # --- (2) Detach+drop retention run --------------------------------
        # ensure_forward_partitions(ahead_months=4) from late August seeded
        # Sep..Dec on top of legacy. Cutoff = 2026-12-01 expires every
        # partition whose UPPER bound <= cutoff (same strict law as the row
        # sweeps): legacy (-inf..Sep), Sep (..Oct), Oct (..Nov), Nov (..Dec)
        # go; Dec (..Jan) survives.
        dropped = await er.drop_expired_partitions(
            factory, cutoff=datetime(2026, 12, 1, tzinfo=UTC)
        )
        assert dropped == 4

        async with engine.connect() as conn:
            survivors = {str(r[0]) for r in (
                await conn.execute(text(
                    "SELECT c.relname FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid "
                    "WHERE i.inhparent = 'agent_events'::regclass"
                ))
            ).fetchall()}
            counts = {str(r[0]): int(r[1]) for r in (
                await conn.execute(text(
                    "SELECT type, count(*) FROM agent_events GROUP BY type"
                ))
            ).fetchall()}
        assert survivors == {"agent_events_2026_12"}
        # Boundary month died WHOLESALE with its partition (nov.event gone);
        # the later month is untouched by the metadata op.
        assert counts.get("nov.event") is None
        assert counts.get("dec.event") == 1

        # The runner re-seeds the rolling window next sweep.
        reseeded = await er.ensure_forward_partitions(factory, now=_FROZEN_NOW)
        assert reseeded >= 1  # August et al. come back as fresh monthlies
    finally:
        await engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        await admin_engine.dispose()
