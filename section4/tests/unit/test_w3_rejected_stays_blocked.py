"""W3 — a rejected plan stays blocked.

The gate clause is "a rejected action stays blocked". The engine-start gate
already refuses a run whose plan is not approved; this pins that a REJECTION (not
merely "never approved") leaves the run unrunnable and starts nothing.

Kept deliberately narrow: it asserts the refusal and the absence of work, not the
particular state name, so a future state-machine rename does not make a security
claim look broken.
"""

import pytest
from unit.test_goal_image_execution import _factory, run

from harness_orchestrator.planner import PlanNotApprovedError
from harness_orchestrator.service import OrchestratorService


class StubEngine:
    api_key = None

    def __init__(self):
        self.calls = 0

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        self.calls += 1
        return {"sessionId": session_id, "engine": "stub", "status": "completed",
                "finalResponse": "done", "toolCalls": []}


def test_a_rejected_plan_cannot_start(tmp_path):
    engine = StubEngine()
    svc = OrchestratorService(engine=engine, session_factory=_factory(tmp_path))

    async def go():
        await svc.propose_plan("r1", goal="do it", project_id="p1")
        plan_id = svc.runs["r1"]["plan_id"]
        await svc.reject_plan("r1", plan_id, "u-test")
        # The gate's own input, not a state name: a rejection must not read as
        # approved no matter what the state machine calls itself afterwards.
        assert await svc._plan_is_approved(plan_id) is False
        with pytest.raises(PlanNotApprovedError):
            await svc.start_execution("r1")

    run(go())

    assert engine.calls == 0, "the engine must never be invoked for a rejected plan"
    assert svc.runs["r1"].get("task") is None, "a refusal must not leave a task behind"


def test_an_approved_plan_reads_as_approved_so_the_refusal_is_about_the_rejection(tmp_path):
    """The control. Without it the test above would pass if the gate refused
    everything — including plans nobody had rejected."""
    engine = StubEngine()
    svc = OrchestratorService(engine=engine, session_factory=_factory(tmp_path))

    async def go():
        await svc.propose_plan("r1", goal="do it", project_id="p1")
        plan_id = svc.runs["r1"]["plan_id"]
        await svc.approve_plan("r1", plan_id, "u-test")
        return await svc._plan_is_approved(plan_id)

    assert run(go()) is True
