"""Part 10 / 23: lifecycle races, idempotent stop, honest status.

Bare-mode services (no DB) with a controllable engine: the engine blocks on a
threading.Event so each test decides exactly when the provider call returns,
which is what makes pause/stop/complete interleavings deterministic.
"""

import asyncio
import threading

import pytest
from harness_contracts.events import EventType

from harness_orchestrator import domain_metrics
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState, InvalidStateTransitionError

RUN = "run-race"


class Rig:
    """Service + engine whose calls park until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0
        self.events: list[str] = []

        def runner(goal: str) -> str:
            self.calls += 1
            self.entered.set()
            self.release.wait(10)
            return f"ran:{goal}"

        self.service = OrchestratorService(
            engine=DeepSeekHarnessWrapper(backend_runner=runner),
            on_event=lambda et, payload: self.events.append(
                et.value if hasattr(et, "value") else str(et)
            ),
        )

    async def start(self) -> asyncio.Task:
        plan_id = await self.service.propose_plan(RUN, goal="race goal")
        await self.service.approve_plan(RUN, plan_id, principal_user_id="u1")
        await self.service.start_execution(RUN)
        task = self.service.runs[RUN]["task"]
        for _ in range(100):  # let the engine thread park inside the provider call
            if self.entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert self.entered.is_set()
        return task

    @property
    def sm(self):
        return self.service.runs[RUN]["state_machine"]

    def count(self, event: EventType) -> int:
        return self.events.count(event.value)


async def _settle(task):
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.fixture(autouse=True)
def _fresh_counters():
    domain_metrics.reset()


async def test_stop_then_pause_is_refused_and_run_stays_stopped():
    rig = Rig()
    task = await rig.start()
    await rig.service.stop_agent(RUN)
    with pytest.raises(InvalidStateTransitionError):
        await rig.service.pause_agent(RUN)  # API maps this to 409 RUN_TERMINAL
    rig.release.set()
    await _settle(task)
    assert rig.sm.state is AgentState.DONE
    assert domain_metrics.value("harness_run_cancel_total", "refused") == 1


async def test_pause_then_stop_ends_stopped_and_a_late_resume_cannot_revive_it():
    rig = Rig()
    task = await rig.start()
    await rig.service.pause_agent(RUN)
    assert rig.sm.state is AgentState.PAUSED
    await rig.service.stop_agent(RUN)  # stop wins over an earlier pause
    assert rig.sm.state is AgentState.DONE
    await rig.service.resume_agent(RUN)  # nothing to resurrect
    rig.release.set()
    await _settle(task)
    assert rig.sm.state is AgentState.DONE
    assert rig.calls == 1  # the engine ran once and was never re-spawned


async def test_concurrent_pause_and_stop_settle_on_done_whatever_the_order():
    for order in ("pause_first", "stop_first"):
        rig = Rig()
        task = await rig.start()
        pause, stop = rig.service.pause_agent(RUN), rig.service.stop_agent(RUN)
        coros = (pause, stop) if order == "pause_first" else (stop, pause)
        results = await asyncio.gather(*coros, return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        assert all(isinstance(e, InvalidStateTransitionError) for e in errors), results
        assert rig.sm.state is AgentState.DONE, order
        rig.release.set()
        await _settle(task)
        assert rig.calls == 1


async def test_stop_wins_over_a_completion_that_arrives_after_it():
    """The provider call returns successfully AFTER stop: the result is discarded,
    no ENGINE_COMPLETED is announced, and the run never reaches the done-gate."""
    rig = Rig()
    task = await rig.start()
    await rig.service.stop_agent(RUN)
    rig.release.set()  # late successful result
    await _settle(task)
    assert rig.sm.state is AgentState.DONE
    assert rig.count(EventType.ENGINE_COMPLETED) == 0
    assert (await rig.service.get_engine_result(RUN))["status"] == "cancelled"


async def test_pause_then_late_completion_is_discarded_and_resume_reruns_once():
    rig = Rig()
    task = await rig.start()
    await rig.service.pause_agent(RUN)
    rig.release.set()  # the paused leg's provider call finishes late
    await _settle(task)
    assert rig.sm.state is AgentState.PAUSED  # not advanced by the zombie result
    assert rig.count(EventType.ENGINE_COMPLETED) == 0

    await rig.service.resume_agent(RUN)
    await _settle(rig.service.runs[RUN]["task"])
    assert rig.calls == 2  # exactly one continuation leg
    assert rig.count(EventType.ENGINE_COMPLETED) == 1
    assert rig.sm.state is AgentState.AWAITING_DONE_SIGNAL


async def test_completion_then_stop_is_terminal_and_keeps_the_real_result():
    rig = Rig()
    task = await rig.start()
    rig.release.set()
    await _settle(task)
    assert rig.sm.state is AgentState.AWAITING_DONE_SIGNAL
    await rig.service.pause_agent(RUN)  # nothing executing: accepted, no change
    assert rig.sm.state is AgentState.AWAITING_DONE_SIGNAL
    await rig.service.stop_agent(RUN)
    assert rig.sm.state is AgentState.DONE
    assert (await rig.service.get_engine_result(RUN))["status"] == "completed"


async def test_repeated_and_concurrent_stop_is_idempotent():
    rig = Rig()
    task = await rig.start()
    results = await asyncio.gather(*(rig.service.stop_agent(RUN) for _ in range(5)))
    assert results.count(True) == 1 and results.count(False) == 4
    assert await rig.service.stop_agent(RUN) is False
    rig.release.set()
    await _settle(task)
    paused = [e for e in rig.events if e == EventType.PAUSED.value]
    assert len(paused) == 1  # exactly one stop tombstone, not five
    assert domain_metrics.value("harness_run_cancel_total", "requested") == 1
    assert domain_metrics.value("harness_run_cancel_total", "noop") == 5


async def test_aggregate_never_says_stopped_while_the_engine_thread_lives():
    rig = Rig()
    task = await rig.start()
    await rig.service.stop_agent(RUN)  # thread is parked in the provider call
    agg = await rig.service.get_status(RUN)
    assert agg["lifecycle"]["phase"] == "stopping"
    assert agg["lifecycle"]["mutationPossible"] is True
    assert agg["lifecycle"]["cancelConfirmed"] is False
    rig.release.set()
    await _settle(task)
    for _ in range(100):  # the engine thread unwinds shortly after the task
        if rig.service.engine_quiesced(RUN):
            break
        await asyncio.sleep(0.02)
    agg = await rig.service.get_status(RUN)
    assert agg["lifecycle"]["phase"] == "stopped"
    assert agg["lifecycle"]["mutationPossible"] is False
    assert agg["lifecycle"]["cancelConfirmed"] is True


async def test_pause_does_not_claim_paused_while_the_engine_thread_lives():
    rig = Rig()
    task = await rig.start()
    await rig.service.pause_agent(RUN)
    agg = await rig.service.get_status(RUN)
    assert agg["state"] == "paused"
    assert agg["lifecycle"]["phase"] == "pausing"
    assert agg["lifecycle"]["mutationPossible"] is True
    rig.release.set()
    await _settle(task)


@pytest.mark.parametrize("action", ["stop_agent", "pause_agent"])
async def test_a_cancelled_call_is_still_metered(monkeypatch, action):
    """W23 F4: the cancel path used to return without metering, so a stopped
    run's real model turn never reached cost_ledger. Only provable usage is
    billed (allow_estimate=False); delta metering covers a later resume."""
    from harness_orchestrator import service as service_module

    metered = []

    async def recorder(svc, run_data, plan, *, allow_estimate=True):
        metered.append((run_data["runId"], allow_estimate))

    monkeypatch.setattr(service_module, "record_run_cost", recorder)
    rig = Rig()
    task = await rig.start()
    await getattr(rig.service, action)(RUN)
    await _settle(task)
    rig.release.set()
    assert metered == [(RUN, False)]
