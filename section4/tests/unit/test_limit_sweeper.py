"""Part 44.005: the running-limit sweep is scheduled, survives a failing tick, stops on cancel."""

import asyncio

from harness_orchestrator import execution
from harness_orchestrator.execution import run_limit_sweeper

from .test_execution_runs import limits
from .test_execution_support import env as env  # the shared fixture


async def _until(cond, tries=1000):
    for _ in range(tries):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_the_scheduled_sweep_pauses_a_running_run_over_its_limit(env):
    await env.new_run("r1")
    await limits(env, maxSeconds=3600)
    await env.start("r1")
    # The run was only queued: mark the claim as running (as a queue worker
    # would) and let the fake clock pass the limit. The clock is the same seam
    # the sweep reads, so the age is deterministic — no wall-clock sleep.
    await env.x(
        "UPDATE run_claims SET state = 'running',"
        " claimed_at = :t, heartbeat_at = :t",
        t=env.clock["t"],
    )
    env.tick(seconds=3601)
    assert env.svc.runs["r1"]["state_machine"].state.value == "executing"

    def live_state():
        # pause_agent rehydrates and REPLACES the state machine; a captured
        # reference is stale, and the registry briefly drops the id mid-swap.
        run = env.svc.runs.get("r1")
        return run["state_machine"].state.value if run else None

    task = asyncio.create_task(run_limit_sweeper(env.svc, 0.01))
    try:
        assert await _until(lambda: live_state() == "paused")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert [r.action for r in await env.audit("execution.limit_exceeded")] == [
        "execution.limit_exceeded"
    ]


async def test_a_failing_tick_is_logged_and_the_next_one_runs(env, monkeypatch, caplog):
    calls = []

    async def flaky(service):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db hiccup")
        return []

    monkeypatch.setattr(execution, "sweep_running_limits", flaky)
    task = asyncio.create_task(run_limit_sweeper(env.svc, 0.01))
    try:
        assert await _until(lambda: len(calls) >= 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert "running-limit sweep failed" in caplog.text
    assert task.cancelled() or task.done()


async def test_cancelling_the_sweeper_ends_it_without_an_error(env):
    task = asyncio.create_task(run_limit_sweeper(env.svc, 0.01))
    await asyncio.sleep(0.03)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
