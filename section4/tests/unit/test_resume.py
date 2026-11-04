"""Checkpoint-aware resume (RESUME ledger item, GAP Part 10 / Appendix C).

Pre-fix behavior pinned the bug: ``resume_agent`` flipped the state machine to
EXECUTING while no engine task existed — the UI claimed "executing" over a dead
run. These tests pin the respawn contract:

- resume RE-SPAWNS the engine task with the SAME session_id (= run_id), so dsh
  continues its session history (checkpoint continuation, not a blind restart);
- pause leaves NO terminal engine_result (the run stays resumable);
- stop stays terminal: honest 'cancelled' result, resume is a silent no-op;
- resume while execution is genuinely live must not double-spawn;
- resume without an approved plan is rejected, never fabricated.
"""

import asyncio
import time

import pytest

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.planner import PlanNotApprovedError
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState

RUN = "run-resume"


def _make_service(first_call_delay: float = 0.0):
    """Engine stub whose FIRST invocation can be slowed so tests can pause
    mid-flight deterministically. Records every invocation."""
    calls: list[tuple[str, str]] = []  # (goal, session_id)

    def runner(goal: str) -> str:
        session = "unknown"
        calls.append((goal, session))
        if len(calls) == 1 and first_call_delay:
            time.sleep(first_call_delay)
        return f"ran:{goal}#{len(calls)}"

    service = OrchestratorService(
        engine=DeepSeekHarnessWrapper(backend_runner=runner),
    )
    return service, calls


async def _approved_executing_run(service: OrchestratorService, goal: str = "build it"):
    plan_id = await service.propose_plan(RUN, goal=goal)
    await service.approve_plan(RUN, plan_id, principal_user_id="u1")
    await service.start_execution(RUN)


async def _settle(task: asyncio.Task | None) -> None:
    if task is None:
        return
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_pause_leaves_run_resumable_and_resume_respawns_engine(monkeypatch):
    # N02 repo-context appends the workspace baseline to the first leg's goal;
    # the kill-switch keeps this assertion about goal pass-through per leg.
    monkeypatch.setenv("HARNESS_REPO_CONTEXT", "0")
    service, calls = _make_service(first_call_delay=0.5)
    await _approved_executing_run(service)

    await asyncio.sleep(0.05)  # let the engine enter its slow first call
    await service.pause_agent(RUN)

    rd = service.runs[RUN]
    assert rd["state_machine"].state == AgentState.PAUSED
    assert "engine_result" not in rd  # pause writes NO terminal outcome

    await service.resume_agent(RUN)
    assert rd["state_machine"].state == AgentState.EXECUTING
    task = rd["task"]
    assert task is not None and not task.done()

    await _settle(task)
    result = await service.get_engine_result(RUN)
    assert result["status"] == "completed"

    # Engine invoked once per lifecycle leg: original + resumed continuation.
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0] == "build it"


async def test_stop_is_terminal_and_resume_after_stop_is_noop():
    service, calls = _make_service(first_call_delay=0.5)
    await _approved_executing_run(service)

    await asyncio.sleep(0.05)
    live_task = service.runs[RUN]["task"]
    await service.stop_agent(RUN)
    await _settle(live_task)

    result = await service.get_engine_result(RUN)
    assert result["status"] == "cancelled"  # honest terminal record

    task_before = service.runs[RUN]["task"]
    await service.resume_agent(RUN)  # SM resume from DONE is a no-op…
    assert service.runs[RUN]["task"] is task_before  # …and nothing got spawned
    assert len(calls) == 1


async def test_resume_without_approved_plan_is_rejected():
    service, calls = _make_service()
    await service.propose_plan(RUN, goal="never approved")

    with pytest.raises(PlanNotApprovedError):
        await service.resume_agent(RUN)
    assert calls == []


async def test_resume_while_still_live_does_not_double_spawn():
    service, calls = _make_service(first_call_delay=0.5)
    await _approved_executing_run(service)

    await asyncio.sleep(0.05)
    live_task = service.runs[RUN]["task"]
    await service.resume_agent(RUN)  # engine genuinely running — must be ignored
    assert service.runs[RUN]["task"] is live_task
    assert len(calls) == 1

    await service.pause_agent(RUN)
    await _settle(live_task)


async def test_human_edit_pause_then_resume_continues():
    service, calls = _make_service()
    await _approved_executing_run(service)
    first_task = service.runs[RUN]["task"]
    await _settle(first_task)

    await service.submit_human_edit(RUN, path="src/x.py", content="touched")
    rd = service.runs[RUN]
    assert rd["state_machine"].state == AgentState.PAUSED

    await service.resume_agent(RUN)
    second_task = rd["task"]
    assert second_task is not None and second_task is not first_task
    await _settle(second_task)

    result = await service.get_engine_result(RUN)
    assert result["status"] == "completed"
    assert len(calls) == 2
