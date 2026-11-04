"""W17 lost-lease re-dispatch — attempt-keyed runner jobs.

A lost lease leaves the claim ``orphaned`` and the job ``released``; the host
may still be executing, so the server must never re-queue. A *human* re-offer
is the only way back in: it mints the next attempt for the same run, marks the
run's max event seq as that attempt's evidence baseline, and scopes the settling
verdict to events after that mark — a dead host's partial failure cannot poison
the retry, and its passes cannot be borrowed.

Semantics pinned here (docs/handoff/W17-lost-lease-redispatch.md):
- a claim re-dispatched reaches the queue (``queued``) exactly as a fresh run does;
- at most one attempt per run is ever live (offered/leased/running);
- ``finish`` derives the verdict from attempts' own evidence only.
"""

from datetime import UTC, datetime, timedelta

import pytest
import test_runner_api as base
import test_runner_exec as exec_base
from fastapi import FastAPI
from harness_contracts import EventType
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from harness_identity.eventbus import EventBus
from harness_identity.models import (
    AgentEvent,
    RunnerJob,
)
from harness_identity.runner_api import router

runner_factory = base.runner_factory
_seed, _register, _runner_headers, _assign = (
    base._seed,
    base._register,
    base._runner_headers,
    base._assign,
)
_stage_run, _leased = exec_base._stage_run, exec_base._leased

CMD_A = exec_base.CMD_A
CMD_B = exec_base.CMD_B


@pytest.fixture
async def attempts_client(runner_factory):
    async with runner_factory.kw["bind"].begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE run_intent_versions (run_id TEXT NOT NULL, version INTEGER NOT NULL, "
                "scope_paths TEXT NOT NULL DEFAULT '[]')"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS run_execution_limits (run_id TEXT PRIMARY KEY, "
                "max_tool_calls INTEGER)"
            )
        )
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = runner_factory
    app.state.event_bus = EventBus(runner_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _expire_attempt_lease(factory, run_id: str) -> None:
    async with factory() as db:
        job = (
            await db.execute(
                select(RunnerJob)
                .where(RunnerJob.run_id == run_id)
                .order_by(RunnerJob.attempt.desc())
                .limit(1)
            )
        ).scalar_one()
        job.lease_expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db.commit()


def _post(client, job, suffix, body=None):
    return client.post(
        f"/api/v1/runner/jobs/{job['runId']}/{suffix}",
        json=body if body is not None else {},
        headers=_runner_headers(job["token"]),
    )


def _call(call_id, command):
    return {"phase": "call", "callId": call_id, "command": command}


def _result(call_id, ok=True, output="ok", exit_code=0):
    return {
        "phase": "result",
        "callId": call_id,
        "ok": ok,
        "exitCode": exit_code,
        "output": output,
        "durationMs": 12,
    }


async def _log_seqs(factory, run_id):
    async with factory() as db:
        rows = (
            await db.execute(
                select(AgentEvent.seq).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq)
            )
        ).scalars().all()
    return list(rows)


async def test_redispatch_of_an_orphaned_run_mints_attempt_2(attempts_client, runner_factory):
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    created = await _register(attempts_client, env["admin"]["token"])
    runner_id, token = created["runner"]["id"], created["token"]

    first = await _assign(attempts_client, env["admin"]["token"], runner_id, env["runA"])
    assert first.status_code == 201
    assert first.json()["job"]["attempt"] == 1
    claim = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert claim.status_code == 200, claim.text

    await _expire_attempt_lease(runner_factory, env["runA"])
    heartbeat = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/heartbeat", headers=_runner_headers(token)
    )
    assert heartbeat.status_code == 409  # lost lease expires → released + orphaned

    second = await _assign(attempts_client, env["admin"]["token"], runner_id, env["runA"])
    assert second.status_code == 201, second.text
    body = second.json()["job"]
    assert body["attempt"] == 2 and body["state"] == "offered"

    async with runner_factory() as db:
        states = (
            await db.execute(
                text(
                    "SELECT attempt, state FROM runner_jobs WHERE run_id = :r ORDER BY attempt"
                ),
                {"r": env["runA"]},
            )
        ).all()
        assert states == [(1, "released"), (2, "offered")]
        claim_state = (
            await db.execute(
                text("SELECT state FROM run_claims WHERE run_id = :r"), {"r": env["runA"]}
            )
        ).scalar_one()
        # The dead attempt's lease was the only claim; re-dispatch re-queues it.
        assert claim_state == "queued"

    reclaimed = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert reclaimed.status_code == 200, reclaimed.text


async def test_a_live_attempt_blocks_reoffer_and_is_reported(attempts_client, runner_factory):
    env = await _seed(runner_factory)
    created = await _register(attempts_client, env["admin"]["token"])
    first = await _assign(
        attempts_client, env["admin"]["token"], created["runner"]["id"], env["runA"]
    )
    assert first.status_code == 201

    second = await _assign(
        attempts_client, env["admin"]["token"], created["runner"]["id"], env["runA"]
    )
    assert second.status_code == 409
    assert "attempt 1 is still offered" in second.json()["detail"]["error"]["message"]


async def test_redispatch_verdict_excludes_the_dead_hosts_evidence(attempts_client, runner_factory):
    """Attempt 1 logged a passing CMD_A, then died mid-run. Attempt 2 runs only
    CMD_B: the verdict must not borrow attempt 1's CMD_A pass — nor blame the
    dead host for attempt 2's missing work."""
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env, tests=(CMD_A, CMD_B))
    created = await _register(attempts_client, env["admin"]["token"])
    runner_id, token = created["runner"]["id"], created["token"]

    first = await _assign(attempts_client, env["admin"]["token"], runner_id, env["runA"])
    assert first.status_code == 201
    claimed = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert claimed.status_code == 200, claimed.text
    # The dead host's partial success: CMD_A passed.
    sent = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/events",
        json={"events": [_call("c1", CMD_A), _result("c1")]},
        headers=_runner_headers(token),
    )
    assert sent.status_code == 200, sent.text

    mark = (await _log_seqs(runner_factory, env["runA"]))[-1]
    await _expire_attempt_lease(runner_factory, env["runA"])
    assert (
        await attempts_client.post(
            f"/api/v1/runner/jobs/{env['runA']}/heartbeat", headers=_runner_headers(token)
        )
    ).status_code == 409

    second = await _assign(attempts_client, env["admin"]["token"], runner_id, env["runA"])
    assert second.status_code == 201, second.text
    assert second.json()["job"]["attempt"] == 2

    async with runner_factory() as db:
        job2 = (
            await db.execute(
                select(RunnerJob).where(
                    RunnerJob.run_id == env["runA"], RunnerJob.attempt == 2
                )
            )
        ).scalar_one()
        # The re-offer marks the dead attempt's evidence boundary exactly at its last event.
        assert job2.evidence_from_seq == mark

    claimed2 = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert claimed2.status_code == 200, claimed2.text

    # Attempt 2 executes only CMD_B; the log's CMD_A pass belongs to the dead attempt.
    sent2 = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/events",
        json={"events": [_call("c2", CMD_B), _result("c2")]},
        headers=_runner_headers(token),
    )
    assert sent2.status_code == 200, sent2.text

    done = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/finish",
        json={"status": "completed"},
        headers=_runner_headers(token),
    )
    assert done.status_code == 200, done.text
    # CMD_A missing from THIS attempt's evidence → the honest verdict is failed,
    # naming CMD_A — not a clean sweep borrowed from the dead host.
    assert done.json()["state"] == "failed"
    async with runner_factory() as db:
        payloads = (
            await db.execute(
                select(AgentEvent.payload).where(
                    AgentEvent.run_id == env["runA"], AgentEvent.type == "engine_failed"
                )
            )
        ).scalars().all()
    assert payloads and CMD_A in str(payloads[0].get("reason", ""))


async def test_settled_run_is_not_reoffered(attempts_client, runner_factory):
    """A BYO run that finished deletes its claim; re-offering it must not mint an
    attempt for already-settled work."""
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env, tests=(CMD_A,))
    created = await _register(attempts_client, env["admin"]["token"])
    runner_id, token = created["runner"]["id"], created["token"]

    first = await _assign(attempts_client, env["admin"]["token"], runner_id, env["runA"])
    assert first.status_code == 201
    claimed = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert claimed.status_code == 200, claimed.text
    sent = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/events",
        json={"events": [_call("c1", CMD_A), _result("c1")]},
        headers=_runner_headers(token),
    )
    assert sent.status_code == 200, sent.text
    done = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/finish",
        json={"status": "completed"},
        headers=_runner_headers(token),
    )
    assert done.status_code == 200

    second = await _assign(attempts_client, env["admin"]["token"], runner_id, env["runA"])
    assert second.status_code == 409
    assert second.json()["detail"]["error"]["code"] == "JOB_TAKEN"


async def test_claim_publishes_execution_started_with_the_attempt(attempts_client, runner_factory):
    """The host taking a job must leave a durable EXECUTION_STARTED marker —
    without it the run never leaves its pre-execution state and every honest
    BYO finish settles `failed` under the never-resurrect law. The attempt
    number rides along so a re-dispatch re-marks rather than inherits."""
    env = await _seed(runner_factory)
    await _stage_run(runner_factory, env)
    created = await _register(attempts_client, env["admin"]["token"])
    runner_id, token = created["runner"]["id"], created["token"]

    first = await _assign(attempts_client, env["admin"]["token"], runner_id, env["runA"])
    assert first.status_code == 201
    claimed = await attempts_client.post(
        f"/api/v1/runner/jobs/{env['runA']}/claim", headers=_runner_headers(token)
    )
    assert claimed.status_code == 200, claimed.text

    async with runner_factory() as db:
        rows = (
            await db.execute(
                select(AgentEvent.payload).where(
                    AgentEvent.run_id == env["runA"],
                    AgentEvent.type == EventType.EXECUTION_STARTED.value,
                )
            )
        ).scalars().all()
    assert len(rows) >= 1
    markers = [r for r in rows if r.get("runnerId") == runner_id]
    assert markers and markers[0]["attempt"] == 1
