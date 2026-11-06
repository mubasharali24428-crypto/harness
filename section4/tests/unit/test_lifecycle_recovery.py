# ruff: noqa: F811 - imported fixtures are re-requested as test parameters
"""Part 10 / 23: worker crash, service restart, reclaim without duplicate execution.

Two OrchestratorService replicas share ONE sqlite store (same fixture as the N1
suite); "dead" workers are modelled by claims whose heartbeat stopped.
"""

import asyncio
import os
import threading
from pathlib import Path

from sqlalchemy import text

from harness_orchestrator import service as service_mod
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.run_queue import claim_next_run, get_queue_snapshot
from harness_orchestrator.state_machine import AgentState
from tests.test_stateless_orchestrator_n1 import (  # noqa: F401 - fixture + helpers
    _drain_publish_chains,
    _event_types,
    replica_pair,
)

HERE = Path(__file__).resolve().parent
SECTION4 = HERE.parents[1]
RUN = "life-run"


class CountingEngine:
    """Backend stub: counts calls, optionally parks until released."""

    def __init__(self, park: bool = False) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self._park = park

    def build(self) -> DeepSeekHarnessWrapper:
        def runner(goal: str) -> str:
            self.calls += 1
            self.entered.set()
            if self._park:
                self.release.wait(10)
            return "done"

        return DeepSeekHarnessWrapper(backend_runner=runner)


async def _start_in_queue(svc, run_id=RUN):
    plan_id = await svc.propose_plan(run_id, goal="recover me", project_id="proj-race")
    await svc.approve_plan(run_id, plan_id, "user-a")
    await svc.start_execution(run_id)


async def _age_claim(factory, run_id, seconds=3600):
    old = "2000-01-01T00:00:00+00:00"
    async with factory() as db:
        await db.execute(
            text("UPDATE run_claims SET heartbeat_at = :o WHERE run_id = :r"),
            {"o": old, "r": run_id},
        )
        await db.commit()


# ------------------------------------------------------------------ worker crash


async def test_worker_crash_blocks_honestly_and_stays_resumable(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_WORKER_MODULE", "standin_worker")
    monkeypatch.setenv("PYTHONPATH", f"{HERE}:{SECTION4}/src:{SECTION4.parent}/contracts/src")
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "off")
    root = Path(os.path.realpath(tmp_path))
    engine = DeepSeekHarnessWrapper(
        workspace_dir=str(root / "ws"),
        session_root=str(root / "sess"),
        api_key="sk-realistic-0123456789abcdef",
        base_url="http://127.0.0.1:9/v1",
        model="deepseek-chat",
        live_mode="live",
        isolated=True,
    )
    from harness_orchestrator.service import OrchestratorService

    events: list[str] = []
    svc = OrchestratorService(
        engine=engine, on_event=lambda et, p: events.append(getattr(et, "value", str(et)))
    )
    plan_id = await svc.propose_plan("crash-run", goal="crash")
    await svc.approve_plan("crash-run", plan_id, "u1")
    await svc.start_execution("crash-run")
    await asyncio.wait_for(svc.runs["crash-run"]["task"], timeout=30)

    result = await svc.get_engine_result("crash-run")
    assert result["status"] == "error" and "exited 7" in result["finalResponse"]
    assert svc.runs["crash-run"]["state_machine"].state is AgentState.BLOCKED
    assert events.count("engine_failed") == 1 and "engine_completed" not in events
    assert svc.engine_quiesced("crash-run")  # a dead worker cannot mutate files

    await svc.resume_agent("crash-run")  # resumable: a new leg starts
    await asyncio.wait_for(svc.runs["crash-run"]["task"], timeout=30)
    assert events.count("resumed") == 1 and events.count("engine_failed") == 2


# ------------------------------------------------------- service restart recovery


async def test_restart_after_dead_worker_reaps_blocks_and_resumes_exactly_once(replica_pair):
    factory = replica_pair["factory"]
    replica_a, replica_b = replica_pair["replicas"]
    engine_b = CountingEngine()
    replica_b.engine = engine_b.build()
    replica_a.engine = CountingEngine().build()  # A never executes: it "dies" after claiming

    await _start_in_queue(replica_a)
    claim = await claim_next_run(factory, "dead-worker", 4)  # worker claimed, then crashed
    assert claim and claim["run_id"] == RUN
    await _age_claim(factory, RUN)
    await _drain_publish_chains(replica_a)

    # Fresh replica = restarted service: it has no memory of the run.
    assert RUN not in replica_b.runs
    replica_b.heartbeat_timeout_seconds = 60
    assert await replica_b.reap_zombies() == [RUN]
    await _drain_publish_chains(replica_b)

    status = await replica_b.get_status(RUN)
    assert status["state"] == AgentState.BLOCKED
    assert (await _event_types(factory, RUN))[-1] == "engine_failed"
    assert await get_queue_snapshot(factory) == []  # dead claim released
    assert engine_b.calls == 0  # nothing ran during recovery

    await replica_b.resume_agent(RUN)
    assert await replica_b.poll_once() == RUN
    await asyncio.wait_for(replica_b.runs[RUN]["task"], timeout=10)
    assert engine_b.calls == 1, await replica_b.get_engine_result(RUN)
    assert await get_queue_snapshot(factory) == []


async def test_restart_while_paused_resumes_from_durable_state(replica_pair):
    replica_a, replica_b = replica_pair["replicas"]
    eng_a, eng_b = CountingEngine(park=True), CountingEngine()
    replica_a.engine, replica_b.engine = eng_a.build(), eng_b.build()
    await _start_in_queue(replica_a)
    assert await replica_a.poll_once() == RUN
    assert await _entered(eng_a)
    await replica_a.pause_agent(RUN)
    eng_a.release.set()
    await _drain_publish_chains(replica_a)

    status = await replica_b.get_status(RUN)  # "restarted" process
    assert status["state"] == AgentState.PAUSED
    await replica_b.resume_agent(RUN)
    assert await replica_b.poll_once() == RUN
    await asyncio.wait_for(replica_b.runs[RUN]["task"], timeout=10)
    assert eng_b.calls == 1


# ------------------------------------------------ reclaim never runs a run twice


async def test_reclaimed_run_fences_the_original_worker(replica_pair, monkeypatch):
    """A's heartbeat stalls, B's reaper takes the claim and B re-runs the run. When
    A's heartbeat finally sees it lost the claim it must stop ITS run, and must not
    delete B's claim while unwinding."""
    monkeypatch.setattr(service_mod, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    factory = replica_pair["factory"]
    replica_a, replica_b = replica_pair["replicas"]
    eng_a, eng_b = CountingEngine(park=True), CountingEngine(park=True)
    replica_a.engine, replica_b.engine = eng_a.build(), eng_b.build()

    # A's heartbeat must ACTUALLY stall for the stale-claim step. Shortening its
    # interval is not a stall: under load a tick lands between _age_claim and
    # reap_zombies, refreshes heartbeat_at, and B finds nothing to reap. Park A's
    # ticks until B holds the claim, then let it see the claim is gone and fence.
    resume_heartbeat = asyncio.Event()
    real_heartbeat = service_mod.heartbeat_run

    async def stalled_heartbeat(factory_, run_id, worker_id):
        if worker_id == replica_a.worker_id:
            await resume_heartbeat.wait()
        return await real_heartbeat(factory_, run_id, worker_id)

    monkeypatch.setattr(service_mod, "heartbeat_run", stalled_heartbeat)

    await _start_in_queue(replica_a)
    assert await replica_a.poll_once() == RUN
    assert await _entered(eng_a)
    a_task = replica_a.runs[RUN]["task"]

    # B decides A is dead (heartbeat looks stale), reaps, and re-drives the run.
    await _age_claim(factory, RUN)
    replica_b.heartbeat_timeout_seconds = 60
    assert await replica_b.reap_zombies() == [RUN]
    await replica_b.resume_agent(RUN)
    assert await replica_b.poll_once() == RUN
    assert await _entered(eng_b)
    resume_heartbeat.set()  # A's stalled heartbeat wakes onto B's claim

    # A's next heartbeat notices the claim is gone and fences its own run.
    a_run = replica_a.runs[RUN]
    await asyncio.wait_for(asyncio.shield(_settle(a_task)), timeout=5)
    assert a_run["engine_result"]["status"] == "cancelled"

    rows = [r for r in await get_queue_snapshot(factory) if r["run_id"] == RUN]
    assert len(rows) == 1 and rows[0]["worker_id"] == replica_b.worker_id  # B's claim survived
    assert rows[0]["state"] == "running"

    eng_a.release.set()
    eng_b.release.set()
    await asyncio.wait_for(replica_b.runs[RUN]["task"], timeout=10)
    await _drain_publish_chains(replica_a, replica_b)
    types = await _event_types(factory, RUN)
    assert types.count("engine_completed") == 1  # only the new owner completed it


async def test_stop_on_another_replica_reaches_the_engine_and_never_claims_stopped_early(
    replica_pair, monkeypatch
):
    """Stop lands on replica B while A runs the engine. B cannot see A's process, so it
    reports `stopping`; releasing the claim makes A's heartbeat fence A's engine, and the
    stop is durable (DONE) for everyone."""
    monkeypatch.setattr(service_mod, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    factory = replica_pair["factory"]
    replica_a, replica_b = replica_pair["replicas"]
    eng_a = CountingEngine(park=True)
    replica_a.engine = eng_a.build()
    await _start_in_queue(replica_a)
    assert await replica_a.poll_once() == RUN
    assert await _entered(eng_a)
    a_task = replica_a.runs[RUN]["task"]

    assert await replica_b.stop_agent(RUN) is True
    view = (await replica_b.get_status(RUN))["lifecycle"]
    assert view["phase"] == "stopping" and view["mutationPossible"] is True  # not "stopped"
    assert await get_queue_snapshot(factory) == []  # claim released: A can no longer run it

    a_run = replica_a.runs[RUN]
    await asyncio.wait_for(_settle(a_task), timeout=5)  # A's heartbeat noticed and fenced it
    assert a_run["engine_result"]["status"] == "cancelled"
    await _drain_publish_chains(replica_a, replica_b)
    # A's stale copy is dropped: its next read replays the durable stop
    assert (await replica_a.get_status(RUN))["state"] == AgentState.DONE
    eng_a.release.set()
    await _drain_publish_chains(replica_a, replica_b)
    types = await _event_types(factory, RUN)
    assert "engine_completed" not in types  # the stopped run never completes


async def _settle(task):
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _entered(eng: CountingEngine) -> bool:
    for _ in range(300):  # never block the loop: the engine task needs it to start
        if eng.entered.is_set():
            return True
        await asyncio.sleep(0.01)
    return False
