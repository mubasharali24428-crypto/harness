"""Marathon 2.0 W13 execution leg - Section 4 side: the BYO dispatch seam.

A run offered to a bring-your-own runner (``runner_jobs``, Section 1) must never be
executed by the orchestrator's own worker: ``start_execution`` still announces and
queues it (the queued row is what the runner claims), but the local spawner skips
it, the server-side budget/tier gate still applies, and the run's state converges
from the events the runner's ``finish`` appends to the shared log.
"""

import pytest
from sqlalchemy import text

import tests.test_stateless_orchestrator_n1 as n1
from harness_orchestrator.run_queue import claim_next_run, get_queue_snapshot
from harness_orchestrator.state_machine import AgentState

replica_pair = n1.replica_pair  # fixture re-export
_drain_publish_chains, _event_types = n1._drain_publish_chains, n1._event_types

_RUNNER_JOBS_DDL = (
    "CREATE TABLE runner_jobs ("
    "run_id VARCHAR(36) PRIMARY KEY, runner_id VARCHAR(36) NOT NULL,"
    " project_id VARCHAR(36) NOT NULL, state VARCHAR(16) NOT NULL DEFAULT 'offered')"
)


async def _with_runner_jobs(factory) -> None:
    async with factory() as db:
        await db.execute(text(_RUNNER_JOBS_DDL))
        await db.commit()


async def _offer(factory, run_id: str, state: str = "offered") -> None:
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO runner_jobs (run_id, runner_id, project_id, state)"
                " VALUES (:r, 'runner-1', 'proj-race', :s)"
            ),
            {"r": run_id, "s": state},
        )
        await db.commit()


async def _queue(factory, run_id: str) -> None:
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at,"
                " worker_id) VALUES (:r, 'proj-race', 'queued', '2026-10-30T00:00:00+00:00',"
                " '2026-10-30T00:00:00+00:00', 'enqueuer')"
            ),
            {"r": run_id},
        )
        await db.commit()


@pytest.mark.parametrize(
    "state", ["offered", "leased", "running", "released", "completed", "failed"]
)
async def test_the_local_queue_never_claims_a_run_with_a_runner_job(replica_pair, state):
    factory = replica_pair["factory"]
    await _with_runner_jobs(factory)
    await _queue(factory, "byo-run")
    await _offer(factory, "byo-run", state)
    await _queue(factory, "local-run")

    first = await claim_next_run(factory, "worker-1", max_per_team=5)
    assert first is not None and first["run_id"] == "local-run"
    # Only the BYO row is left and it stays queued for the runner to claim.
    assert await claim_next_run(factory, "worker-1", max_per_team=5) is None
    rows = {r["run_id"]: r for r in await get_queue_snapshot(factory)}
    assert rows["byo-run"]["state"] == "queued"


async def _approved(svc, run_id: str) -> str:
    plan_id = await svc.propose_plan(run_id, goal="byo goal", project_id="proj-race")
    await svc.approve_plan(run_id, plan_id, "user-a")
    return plan_id


async def test_start_execution_queues_a_byo_run_but_skips_the_local_spawner(replica_pair):
    factory = replica_pair["factory"]
    svc, _ = replica_pair["replicas"]
    await _with_runner_jobs(factory)
    await _approved(svc, "byo-start")
    await _offer(factory, "byo-start")

    def no_local_spawn(*_a, **_k):
        raise AssertionError("a BYO run must not be spawned by the orchestrator")

    svc._spawn_execution = no_local_spawn
    await svc.start_execution("byo-start")
    await _drain_publish_chains(svc)

    assert "execution_started" in await _event_types(factory, "byo-start")
    rows = [r for r in await get_queue_snapshot(factory) if r["run_id"] == "byo-start"]
    assert [r["state"] for r in rows] == ["queued"]  # the runner's to claim
    assert await svc.poll_once() is None  # the local worker finds nothing to take
    assert svc.runs["byo-start"].get("task") is None


async def test_a_byo_start_still_passes_the_server_side_budget_gate(replica_pair):
    """Metering/tier policy stays server-side: a blocked BYO run is not left claimable."""
    factory = replica_pair["factory"]
    svc, _ = replica_pair["replicas"]
    await _with_runner_jobs(factory)
    await _approved(svc, "byo-blocked")
    await _offer(factory, "byo-blocked")

    gated: list[str] = []

    async def blocked(run_data, plan):
        gated.append(run_data["runId"])
        run_data["state_machine"].execution_failed("Budget cap reached: test")
        return True

    svc._budget_gate = blocked
    await svc.start_execution("byo-blocked")
    await _drain_publish_chains(svc)

    assert gated == ["byo-blocked"]
    assert [r for r in await get_queue_snapshot(factory) if r["run_id"] == "byo-blocked"] == []
    assert "engine_failed" in await _event_types(factory, "byo-blocked")
    assert svc.runs["byo-blocked"]["state_machine"].state is AgentState.BLOCKED


@pytest.mark.parametrize(
    ("settle", "expected"),
    [
        ("engine_completed", AgentState.AWAITING_DONE_SIGNAL),
        ("engine_failed", AgentState.BLOCKED),
    ],
)
async def test_the_orchestrator_converges_on_what_the_runner_finish_logs(
    replica_pair, settle, expected
):
    """S1's finish appends ENGINE_* to the shared log; a replica that started the run
    (and holds it cached as EXECUTING) replays it instead of trusting its own copy."""
    factory = replica_pair["factory"]
    svc, other = replica_pair["replicas"]
    await _with_runner_jobs(factory)
    await _approved(svc, "byo-settle")
    await _offer(factory, "byo-settle")
    await svc.start_execution("byo-settle")
    await _drain_publish_chains(svc)
    assert svc.runs["byo-settle"]["state_machine"].state is AgentState.EXECUTING

    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO agent_events (project_id, run_id, type, payload)"
                " VALUES ('proj-race', 'byo-settle', :t, '{\"runId\": \"byo-settle\"}')"
            ),
            {"t": settle},
        )
        await db.commit()

    for replica in (svc, other):
        run_data = await replica._ensure_run("byo-settle")
        assert run_data["state_machine"].state is expected
