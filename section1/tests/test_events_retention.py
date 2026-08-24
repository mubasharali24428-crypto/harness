"""Mission 12 G1 — agent_events retention sweep tests (raw-05 #1).

Covers deploy/runners/events_retention.py against the frozen conftest sqlite
fixture: batched deletion honors the cutoff, the LIMIT loop terminates, the
sweep is a no-op when nothing is expired, plus knob parsing, dialect SQL
shapes, mode resolution, and the repeat/cancel behavior of loop mode.
"""

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from harness_identity.models import AgentEvent, Project, Team

_RUNNER = Path(__file__).resolve().parents[2] / "deploy" / "runners" / "events_retention.py"
_spec = importlib.util.spec_from_file_location("events_retention", _RUNNER)
assert _spec is not None and _spec.loader is not None  # noqa: S101 — structural, not a claim
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


async def _make_project(db) -> str:
    """agent_events.project_id has an FK into projects; satisfy it honestly."""
    async with db() as session:
        team = Team(name="retention-team")
        session.add(team)
        await session.flush()
        project = Project(team_id=team.id, name="retention-proj")
        session.add(project)
        await session.commit()
        return project.id


async def _add_event(db, project_id: str, age_days: float) -> int:
    ts = NOW - timedelta(days=age_days)
    async with db() as session:
        row = AgentEvent(project_id=project_id, type="audit.test", payload={}, ts=ts)
        session.add(row)
        await session.flush()  # seq assigned while object is live
        seq = int(row.seq)
        await session.commit()
        return seq


async def _event_seqs(db) -> list[int]:
    async with db() as session:
        rows = await session.execute(select(AgentEvent.seq).order_by(AgentEvent.seq))
        return [int(r[0]) for r in rows]


async def _count(db) -> int:
    async with db() as session:
        total = (await session.execute(select(func.count()).select_from(AgentEvent))).scalar_one()
        return int(total)


# --------------------------------------------------------------- cutoff honor


async def test_batched_deletion_honors_cutoff(db):
    project = await _make_project(db)
    for age in (40.0, 35.0, 31.0):  # past the 30-day horizon
        await _add_event(db, project, age)
    for age in (29.0, 1.0):  # inside the window — must survive
        await _add_event(db, project, age)

    deleted = await er.sweep_once(
        db, retention_days=30, batch_size=5000, now=NOW
    )

    assert deleted == 3
    assert await _count(db) == 2


async def test_boundary_row_exactly_at_cutoff_survives(db):
    """Strict comparison: ts == cutoff is NOT expired (ts < cutoff deletes)."""
    project = await _make_project(db)
    cutoff = NOW - timedelta(days=30)
    async with db() as session:
        session.add(AgentEvent(project_id=project, type="audit.at", payload={}, ts=cutoff))
        session.add(
            AgentEvent(
                project_id=project,
                type="audit.just_past",
                payload={},
                ts=cutoff - timedelta(microseconds=1),
            )
        )
        await session.commit()

    deleted = await er.sweep_once(db, retention_days=30, batch_size=5000, now=NOW)

    assert deleted == 1
    seqs = await _event_seqs(db)
    assert len(seqs) == 1


# ------------------------------------------------------------ loop terminates


async def test_loop_terminates_and_batches_across_transactions(db, monkeypatch):
    """10 expired rows at batch_size=4 -> exactly 3 DELETE statements (4+4+2),
    then the loop stops because the last batch came up short."""
    project = await _make_project(db)
    for i in range(10):
        await _add_event(db, project, 31.0 + i)

    calls = {"n": 0}
    original_execute = AsyncSession.execute

    async def counting_execute(self, statement, *args, **kwargs):
        calls["n"] += 1
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", counting_execute)

    deleted = await er.sweep_once(db, retention_days=30, batch_size=4, now=NOW)

    assert deleted == 10
    assert calls["n"] == 3  # two full batches + one short terminating batch
    assert await _count(db) == 0


async def test_max_batches_cap_stops_the_loop_early(db):
    project = await _make_project(db)
    for i in range(10):
        await _add_event(db, project, 31.0 + i)

    deleted = await er.sweep_once(
        db, retention_days=30, batch_size=4, max_batches=1, now=NOW
    )

    assert deleted == 4  # one batch only; remainder waits for the next sweep
    assert await _count(db) == 6


# ------------------------------------------------------------------- no-op


async def test_noop_when_nothing_is_old(db):
    project = await _make_project(db)
    for age in (0.0, 5.0, 29.999):
        await _add_event(db, project, age)

    deleted = await er.sweep_once(db, retention_days=30, batch_size=5000, now=NOW)

    assert deleted == 0
    assert await _count(db) == 3


async def test_noop_on_empty_table(db):
    await _make_project(db)
    assert await er.sweep_once(db, retention_days=30, batch_size=5000, now=NOW) == 0
    assert await _count(db) == 0


async def test_retention_zero_days_keeps_everything_except_future_rows(db):
    """RETENTION_DAYS=0 is validated positive upstream; retention_days=0 here
    only removes rows stamped in the future (clock-skew guard semantics)."""
    project = await _make_project(db)
    await _add_event(db, project, -1.0)  # ts one day in the future
    await _add_event(db, project, 0.5)

    deleted = await er.sweep_once(db, retention_days=0, batch_size=5000, now=NOW)

    assert deleted == 1
    assert await _count(db) == 1


# ------------------------------------------------------- knobs / clock / SQL


def test_config_defaults_when_env_empty():
    cfg = er.retention_config_from_env({})
    assert cfg == {
        "retention_days": 30,
        "batch_size": 5000,
        "run_interval_seconds": 6 * 60 * 60,
    }


def test_config_parses_and_sanitizes():
    cfg = er.retention_config_from_env(
        {"RETENTION_DAYS": "7", "BATCH_SIZE": "1000", "RUN_INTERVAL_SECONDS": "60"}
    )
    assert cfg == {"retention_days": 7, "batch_size": 1000, "run_interval_seconds": 60}
    # Garbage falls back to the documented default instead of crashing boot.
    fallback = er.retention_config_from_env({"RETENTION_DAYS": "thirty"})
    assert fallback["retention_days"] == 30


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_config_rejects_non_positive(bad):
    with pytest.raises(ValueError, match="positive"):
        er.retention_config_from_env({"RETENTION_DAYS": bad})


def test_cutoff_math_handles_naive_as_utc():
    assert er.cutoff_for(30, NOW) == datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)
    naive = datetime(2026, 8, 23, 12, 0, 0)
    assert er.cutoff_for(30, naive) == datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)


def test_build_delete_sql_is_parameterized_and_dialect_aware():
    pg = er.build_delete_sql("postgresql")
    lite = er.build_delete_sql("sqlite")
    # Named bind params only — retention values never enter the SQL text.
    # One shape serves both dialects (see runner docstring).
    for sql in (pg, lite):
        assert ":cutoff" in sql and ":batch" in sql and "LIMIT :batch" in sql
        assert "agent_events" in sql and "ts < :cutoff" in sql
    with pytest.raises(ValueError, match="dialect"):
        er.build_delete_sql("mysql")


# ------------------------------------------------------------------ run loop


async def test_run_loop_repeats_until_cancelled(db, monkeypatch):
    runs = {"n": 0}

    async def fake_sweep(factory, *, retention_days, batch_size):
        runs["n"] += 1
        return 0

    monkeypatch.setattr(er, "sweep_once", fake_sweep)
    task = asyncio.create_task(
        er.run_loop(db, retention_days=30, batch_size=5000, run_interval_seconds=1)
    )
    try:
        for _ in range(300):
            if runs["n"] >= 2:
                break
            await asyncio.sleep(0.01)
        assert runs["n"] >= 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_loop_survives_a_failing_sweep(db, monkeypatch):
    """A transient DB error must not kill the sidecar: next interval retries."""
    attempts = {"n": 0}

    async def flaky_sweep(factory, *, retention_days, batch_size):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("db restarted mid-sweep")
        return 0

    monkeypatch.setattr(er, "sweep_once", flaky_sweep)
    task = asyncio.create_task(
        er.run_loop(db, retention_days=30, batch_size=5000, run_interval_seconds=1)
    )
    try:
        for _ in range(300):
            if attempts["n"] >= 2:
                break
            await asyncio.sleep(0.01)
        assert attempts["n"] >= 2  # retried after the failure
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --------------------------------------------------------------------- modes


@pytest.mark.parametrize(
    ("flag_once", "flag_loop", "env_mode", "expected"),
    [
        (True, False, "", True),
        (False, True, "once", False),  # explicit --loop beats env
        (False, False, "once", True),
        (False, False, "ONCE", True),  # case-insensitive
        (False, False, "", False),  # default is loop
        (False, False, "bogus", False),
    ],
)
def test_resolve_once_mode(flag_once, flag_loop, env_mode, expected):
    assert (
        er.resolve_once_mode(flag_once=flag_once, flag_loop=flag_loop, env_mode=env_mode)
        is expected
    )
