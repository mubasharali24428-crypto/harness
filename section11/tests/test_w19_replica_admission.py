"""M2-W19: context-index admission is enforced ACROSS replicas, not per-process.

The historical guard was an in-memory set (`ContextService._active_projects`) —
two replicas of the context service, each with its own set, could both index the
same project on one shared database. These tests use TWO ContextService objects
over the SAME `db` factory (the shape of a multi-replica deployment) and prove
the durable reservation now blocks the second replica, reclaims a dead owner's
expired lease, and frees on completion. No network: HashingEmbedding.
"""

import asyncio

import pytest
from sqlalchemy import text

from harness_context.embeddings import HashingEmbedding
from harness_context.service import ContextService, RepoIndexError

PROJECT = "w19-project"


def _replica(db):
    return ContextService(db, HashingEmbedding(dims=256))


def _park_on_cap(svc, entered, release):
    async def hang_cap(project_id):
        entered.set()
        await release.wait()

    svc._enforce_context_cap = hang_cap


async def _held_count(db):
    async with db() as s:
        return await s.scalar(
            text("SELECT COUNT(*) FROM project_index_reservations WHERE project_id = :p"),
            {"p": PROJECT},
        )


async def test_second_replica_refused_while_first_holds_lease(db, mini_repo):
    a, b = _replica(db), _replica(db)
    entered, release = asyncio.Event(), asyncio.Event()
    _park_on_cap(a, entered, release)
    first = asyncio.create_task(a.start_index(str(mini_repo), PROJECT))
    await asyncio.wait_for(entered.wait(), 2)  # A took the durable lease, now parked
    try:
        assert await _held_count(db) == 1
        # B is a different process (its own empty in-memory set); only the shared
        # durable row can stop it — and it must, with a cross-replica reason.
        with pytest.raises(RepoIndexError, match="another replica"):
            await b.start_index(str(mini_repo), PROJECT)
        assert await _held_count(db) == 1, "a refused admission must not steal the lease"
    finally:
        release.set()
        await first


async def test_expired_lease_is_reclaimed_by_another_replica(db, mini_repo):
    a, b = _replica(db), _replica(db)
    entered, release = asyncio.Event(), asyncio.Event()
    _park_on_cap(a, entered, release)
    first = asyncio.create_task(a.start_index(str(mini_repo), PROJECT))
    await asyncio.wait_for(entered.wait(), 2)
    second = None
    try:
        # Simulate replica A dying: force its lease into the past.
        async with db() as s:
            async with s.begin():
                await s.execute(
                    text("UPDATE project_index_reservations "
                         "SET lease_expires_at = '1999-01-01T00:00:00+00:00' "
                         "WHERE project_id = :p"),
                    {"p": PROJECT},
                )
        # B reclaims the dead owner's expired lease and passes admission.
        b_entered = asyncio.Event()

        async def hang_b(project_id):
            b_entered.set()
            await release.wait()

        b._enforce_context_cap = hang_b
        second = asyncio.create_task(b.start_index(str(mini_repo), PROJECT))
        await asyncio.wait_for(b_entered.wait(), 2)  # B reached its cap => it won the lease
        assert await _held_count(db) == 1  # still exactly one owner, now B
    finally:
        release.set()
        await first
        if second is not None and not second.cancelled():
            await second


async def test_the_admission_path_provisions_its_own_table(db, mini_repo):
    """A driver that only ran `DDL_SQLITE` must still be able to admit a project.

    Migration 0072 creates `project_index_reservations` in a deployment and
    `ensure_schema` does it for this suite, but the indexing path cannot assume
    WHO bootstrapped the schema — it used to fail with `no such table`.
    """
    async with db() as s:
        async with s.begin():
            await s.execute(text("DROP TABLE IF EXISTS project_index_reservations"))
    a = _replica(db)
    job = await a.start_index(str(mini_repo), PROJECT)
    assert await _held_count(db) == 1, "the table was recreated, and the lease taken"
    for _ in range(200):
        status = a.get_job(job.job_id)
        if status and status.status in ("completed", "failed"):
            break
        await asyncio.sleep(0.02)
    assert await _held_count(db) == 0


async def test_reservation_released_after_index_completes(db, mini_repo):
    a = _replica(db)
    job = await a.start_index(str(mini_repo), PROJECT)
    assert await _held_count(db) == 1, "a running index holds its reservation"
    for _ in range(200):
        status = a.get_job(job.job_id)
        if status and status.status in ("completed", "failed"):
            break
        await asyncio.sleep(0.02)
    assert await _held_count(db) == 0, "a finished index must free its durable reservation"
    # A later replica can then take it.
    b = _replica(db)
    job_b = await b.start_index(str(mini_repo), PROJECT)
    assert job_b.job_id != job.job_id
