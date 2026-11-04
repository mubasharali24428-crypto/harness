"""An authenticated request must not roll back an in-flight index write."""

import asyncio

import pytest
from conftest import AUTH_HEADERS
from sqlalchemy import text

from harness_context.service import IndexJob, RepoIndexError


async def test_authentication_during_index_preserves_all_chunks(client, db, mini_repo):
    svc = client.app.state.context_service
    original = svc.store.upsert_chunk
    request_made = False

    async def upsert_with_request(session, **kwargs):
        nonlocal request_made
        await original(session, **kwargs)
        if not request_made:
            request_made = True
            response = await client.get("/api/v1/healthz", headers=AUTH_HEADERS)
            assert response.status_code == 200

    svc.store.upsert_chunk = upsert_with_request
    job = IndexJob(id="w19-isolation", project_id="w19-project", repo_path=str(mini_repo))
    await svc.reindex(job, mini_repo)

    async with db() as session:
        count = await session.scalar(
            text("SELECT COUNT(*) FROM context_chunks WHERE project_id = :project"),
            {"project": job.project_id},
        )
    assert request_made
    assert count == job.chunks_indexed


async def test_project_admission_reserved_while_cap_check_is_pending(client, mini_repo):
    svc = client.app.state.context_service
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_cap_check(project_id):
        entered.set()
        await release.wait()

    svc._enforce_context_cap = delayed_cap_check
    first = asyncio.create_task(svc.start_index(str(mini_repo), "w19-project"))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with pytest.raises(RepoIndexError, match="already running"):
            await asyncio.wait_for(svc.start_index(str(mini_repo), "w19-project"), 2)
    finally:
        release.set()
    accepted = await first
    for _ in range(500):
        result = svc.get_job(accepted.job_id)
        if result.status in {"completed", "failed"}:
            break
        await asyncio.sleep(0.01)
    assert result.status == "completed"
    again = await svc.start_index(str(mini_repo), "w19-project")
    for _ in range(500):
        result = svc.get_job(again.job_id)
        if result.status in {"completed", "failed"}:
            break
        await asyncio.sleep(0.01)
    assert result.status == "completed"


async def test_cancelled_cap_check_releases_reservation(client, mini_repo):
    svc = client.app.state.context_service
    entered = asyncio.Event()

    async def delayed_cap_check(project_id):
        entered.set()
        await asyncio.Event().wait()

    svc._enforce_context_cap = delayed_cap_check
    task = asyncio.create_task(svc.start_index(str(mini_repo), "w19-cancelled"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "w19-cancelled" not in svc._active_projects


async def test_failed_job_releases_project_for_retry(client, mini_repo):
    svc = client.app.state.context_service
    original = svc.reindex

    async def fail(job, root):
        raise RuntimeError("synthetic indexing failure")

    svc.reindex = fail
    first = await svc.start_index(str(mini_repo), "w19-failed")
    for _ in range(500):
        result = svc.get_job(first.job_id)
        if result.status == "failed":
            break
        await asyncio.sleep(0.01)
    assert result.status == "failed"
    svc.reindex = original
    second = await svc.start_index(str(mini_repo), "w19-failed")
    for _ in range(500):
        result = svc.get_job(second.job_id)
        if result.status in {"completed", "failed"}:
            break
        await asyncio.sleep(0.01)
    assert result.status == "completed"
