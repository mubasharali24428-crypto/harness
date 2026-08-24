"""Comprehensive stress tests for Section 4: Agent Orchestration Engine."""

import pytest
from harness_contracts.events import EventType

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.planner import PlanNotApprovedError, PlanService
from harness_orchestrator.reviewer import ReviewerSubAgent, ReviewFindings
from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import (
    AgentState,
    AgentStateMachine,
    InvalidStateTransitionError,
)

# ================================================================
# STATE MACHINE - EDGE CASES
# ================================================================


class TestStateMachineEdgeCases:
    def test_initial_state_is_idle(self):
        sm = AgentStateMachine(run_id="run-init")
        assert sm.state == AgentState.IDLE

    def test_cannot_start_planning_from_executing(self):
        sm = AgentStateMachine(run_id="run-bad")
        sm.plan_approved = True
        sm.transition_to(AgentState.EXECUTING)
        with pytest.raises(InvalidStateTransitionError):
            sm.start_planning()

    def test_cannot_execute_without_approval(self):
        sm = AgentStateMachine(run_id="run-noplan")
        assert sm.plan_approved is False
        with pytest.raises(InvalidStateTransitionError):
            sm.start_execution()

    def test_pause_from_non_executing_is_noop(self):
        sm = AgentStateMachine(run_id="run-noop")
        original_state = sm.state
        sm.pause()
        assert sm.state == original_state  # no change

    def test_resume_from_non_paused_is_noop(self):
        sm = AgentStateMachine(run_id="run-noop2")
        sm.plan_approved = True
        sm.transition_to(AgentState.EXECUTING)
        original_state = sm.state
        sm.resume()
        assert sm.state == original_state  # already executing

    def test_multiple_pause_resume_cycles(self):
        events = []
        sm = AgentStateMachine(run_id="run-cycle", on_event=lambda t, p: events.append(t))
        sm.plan_approved = True
        sm.transition_to(AgentState.EXECUTING)

        for _ in range(10):
            sm.pause()
            assert sm.state == AgentState.PAUSED
            sm.resume()
            assert sm.state == AgentState.EXECUTING

        pause_events = [e for e in events if e == EventType.PAUSED]
        resume_events = [e for e in events if e == EventType.RESUMED]
        assert len(pause_events) == 10
        assert len(resume_events) == 10

    def test_human_edit_sets_is_human_editing_flag(self):
        sm = AgentStateMachine(run_id="run-flag")
        sm.plan_approved = True
        sm.transition_to(AgentState.EXECUTING)
        sm.submit_human_edit("src/main.py")
        assert sm.is_human_editing is True
        assert sm.state == AgentState.PAUSED

    def test_signal_done_clears_human_editing_flag(self):
        sm = AgentStateMachine(run_id="run-done")
        sm.plan_approved = True
        sm.transition_to(AgentState.EXECUTING)
        sm.submit_human_edit("src/main.py")
        assert sm.is_human_editing is True
        sm.signal_done()
        assert sm.is_human_editing is False
        assert sm.state == AgentState.REVIEWING

    def test_block_for_clarification_stores_question(self):
        sm = AgentStateMachine(run_id="run-block")
        sm.plan_approved = True
        sm.transition_to(AgentState.EXECUTING)
        sm.block_for_clarification("What should the error format be?")
        assert sm.state == AgentState.BLOCKED
        assert sm.clarification_question == "What should the error format be?"

    def test_resume_from_blocked_clears_to_executing(self):
        sm = AgentStateMachine(run_id="run-unblock")
        sm.plan_approved = True
        sm.transition_to(AgentState.EXECUTING)
        sm.block_for_clarification("question?")
        assert sm.state == AgentState.BLOCKED
        sm.resume()
        assert sm.state == AgentState.EXECUTING

    def test_reviewer_passed_transitions_to_executing(self):
        sm = AgentStateMachine(run_id="run-rev-pass")
        sm.transition_to(AgentState.REVIEWING)
        sm.reviewer_passed({"summary": "all good"})
        assert sm.state == AgentState.EXECUTING

    def test_reviewer_failed_transitions_to_blocked(self):
        sm = AgentStateMachine(run_id="run-rev-fail")
        sm.transition_to(AgentState.REVIEWING)
        sm.reviewer_failed({"issues": ["conflict markers"]})
        assert sm.state == AgentState.BLOCKED

    def test_open_pr_transitions_to_pr_opened(self):
        sm = AgentStateMachine(run_id="run-pr")
        sm.plan_approved = True
        sm.transition_to(AgentState.EXECUTING)
        sm.open_pr({"number": 42, "url": "https://github.com/org/repo/pull/42"})
        assert sm.state == AgentState.PR_OPENED

    def test_finish_transitions_to_done(self):
        sm = AgentStateMachine(run_id="run-fin")
        sm.finish()
        assert sm.state == AgentState.DONE

    def test_all_events_captured(self):
        events = []
        sm = AgentStateMachine(run_id="run-ev", on_event=lambda t, p: events.append(t))
        sm.start_planning()
        sm.plan_proposed("plan-1")
        sm.approve_plan("plan-1", "user-1")
        sm.start_execution()
        sm.pause()
        sm.resume()
        sm.submit_human_edit("f.py")
        sm.signal_done()
        sm.reviewer_passed({})
        assert EventType.PLAN_PROPOSED in events
        assert EventType.PLAN_APPROVED in events
        assert EventType.EXECUTION_STARTED in events
        assert EventType.PAUSED in events
        assert EventType.RESUMED in events
        assert EventType.DONE_SIGNAL_RECEIVED in events
        assert EventType.REVIEW_PASSED in events


# ================================================================
# PLANNER - EDGE CASES
# ================================================================


class TestPlannerEdgeCases:
    def test_plan_id_uniqueness(self):
        ps = PlanService()
        ids = set()
        for _ in range(50):
            pid, _ = ps.create_plan(goal="task")
            ids.add(pid)
        assert len(ids) == 50

    def test_plan_fields_are_populated(self):
        ps = PlanService()
        pid, plan = ps.create_plan(
            goal="Add auth",
            files_likely_to_change=["auth.py"],
            steps=["step1", "step2"],
            tests=["test_auth"],
            risks=["regression"],
            estimated_cost_usd=0.10,
            forbidden_paths=["/etc/shadow"],
            expected_result="Tests pass",
        )
        assert plan.goal == "Add auth"
        assert plan.filesLikelyToChange == ["auth.py"]
        assert plan.steps == ["step1", "step2"]
        assert plan.tests == ["test_auth"]
        assert plan.risks == ["regression"]
        assert plan.estimatedCostUsd == 0.10
        assert plan.forbiddenPaths == ["/etc/shadow"]
        assert plan.expectedResult == "Tests pass"

    def test_plan_default_values(self):
        ps = PlanService()
        _, plan = ps.create_plan(goal="minimal")
        assert plan.filesLikelyToChange == []
        assert len(plan.steps) > 0  # has defaults
        assert len(plan.tests) > 0
        assert len(plan.risks) > 0
        assert plan.estimatedCostUsd == 0.05

    def test_approve_and_reject_toggle(self):
        ps = PlanService()
        pid, _ = ps.create_plan(goal="test")
        assert ps.is_approved(pid) is False
        ps.approve_plan(pid)
        assert ps.is_approved(pid) is True
        ps.reject_plan(pid)
        assert ps.is_approved(pid) is False

    def test_get_nonexistent_plan_raises(self):
        ps = PlanService()
        with pytest.raises(KeyError):
            ps.get_plan("plan-nonexistent")

    def test_approve_nonexistent_plan_raises(self):
        ps = PlanService()
        with pytest.raises(KeyError):
            ps.approve_plan("plan-nonexistent")


# ================================================================
# REVIEWER - EDGE CASES
# ================================================================


class TestReviewerEdgeCases:
    def test_empty_diff_passes(self):
        r = ReviewerSubAgent()
        f = r.evaluate_changes("brief", "", "passed")
        assert f.passed is True

    def test_syntax_error_in_diff_fails(self):
        r = ReviewerSubAgent()
        f = r.evaluate_changes("brief", "SyntaxError: unexpected EOF", "passed")
        assert f.passed is False
        assert f.syntax_ok is False

    def test_failed_tests_fail_review(self):
        r = ReviewerSubAgent()
        f = r.evaluate_changes("brief", "clean diff", "failed")
        assert f.passed is False
        assert "test suite failed" in f.issues[0].lower()

    def test_conflict_plus_syntax_error_multiple_issues(self):
        r = ReviewerSubAgent()
        diff = "<<<<<<< HEAD\nSyntaxError: bad\n>>>>>>>"
        f = r.evaluate_changes("brief", diff, "passed")
        assert f.passed is False
        assert len(f.issues) >= 2

    def test_findings_are_pydantic_model(self):
        r = ReviewerSubAgent()
        f = r.evaluate_changes("brief", "", "passed")
        assert isinstance(f, ReviewFindings)
        d = f.model_dump()
        assert "passed" in d
        assert "syntax_ok" in d
        assert "conflicts_detected" in d
        assert "issues" in d


# ================================================================
# ENGINE WRAPPER
# ================================================================


class TestEngineWrapper:
    def test_default_engine_is_honest_stub(self):
        e = DeepSeekHarnessWrapper()
        result = e.execute_goal("Write tests")
        assert result["status"] == "not_configured"
        assert "not configured" in result["finalResponse"]

    def test_custom_backend_runner(self):
        e = DeepSeekHarnessWrapper(backend_runner=lambda goal: f"CUSTOM: {goal}")
        result = e.execute_goal("task-42")
        assert result["finalResponse"] == "CUSTOM: task-42"

    def test_session_id_passed_through(self):
        e = DeepSeekHarnessWrapper()
        result = e.execute_goal("goal", session_id="sess-xyz")
        assert result["sessionId"] == "sess-xyz"


# ================================================================
# ORCHESTRATOR SERVICE - FULL LIFECYCLE
# ================================================================


class TestOrchestratorServiceStress:
    async def test_propose_plan_from_idle(self):
        svc = OrchestratorService()
        pid = await svc.propose_plan("run-1")
        assert pid.startswith("plan-")
        status = await svc.get_status("run-1")
        assert status["state"] == AgentState.AWAITING_PLAN_APPROVAL

    async def test_cannot_execute_before_approval(self):
        svc = OrchestratorService()
        await svc.propose_plan("run-2")
        with pytest.raises(PlanNotApprovedError):
            await svc.start_execution("run-2")

    async def test_reject_plan_returns_to_idle(self):
        svc = OrchestratorService()
        pid = await svc.propose_plan("run-rej")
        await svc.reject_plan("run-rej", pid, "user-1")
        status = await svc.get_status("run-rej")
        assert status["state"] == AgentState.IDLE

    async def test_full_lifecycle_happy_path(self):
        events = []
        svc = OrchestratorService(on_event=lambda t, p: events.append(t))

        pid = await svc.propose_plan("run-happy")
        await svc.approve_plan("run-happy", pid, "user-1")
        await svc.start_execution("run-happy")

        status = await svc.get_status("run-happy")
        assert status["state"] == AgentState.EXECUTING

        await svc.pause_agent("run-happy")
        assert (await svc.get_status("run-happy"))["state"] == AgentState.PAUSED

        await svc.resume_agent("run-happy")
        assert (await svc.get_status("run-happy"))["state"] == AgentState.EXECUTING

        await svc.stop_agent("run-happy")
        assert (await svc.get_status("run-happy"))["state"] == AgentState.DONE

    async def test_pause_cancels_live_engine_task(self):
        """F7 regression guard: pause must flip cancel flag and stop the task ref."""
        svc = OrchestratorService(
            engine=DeepSeekHarnessWrapper(backend_runner=lambda goal: f"CUSTOM: {goal}")
        )
        pid = await svc.propose_plan("run-cancel")
        await svc.approve_plan("run-cancel", pid, "user-1")
        await svc.start_execution("run-cancel")
        rd = svc.runs["run-cancel"]
        assert rd["task"] is not None
        await svc.pause_agent("run-cancel")
        assert rd["cancel_requested"] is True

    async def test_human_edit_then_done_signal_reviewer_pass(self):
        svc = OrchestratorService()
        pid = await svc.propose_plan("run-edit")
        await svc.approve_plan("run-edit", pid, "user-1")
        await svc.start_execution("run-edit")

        await svc.submit_human_edit("run-edit", "main.py", "new code")
        assert (await svc.get_status("run-edit"))["state"] == AgentState.PAUSED

        findings = await svc.signal_done("run-edit", diff_text="clean diff here")
        assert findings.passed is True
        assert (await svc.get_status("run-edit"))["state"] == AgentState.EXECUTING

    async def test_human_edit_then_done_signal_reviewer_fail(self):
        svc = OrchestratorService()
        pid = await svc.propose_plan("run-fail")
        await svc.approve_plan("run-fail", pid, "user-1")
        await svc.start_execution("run-fail")

        await svc.submit_human_edit("run-fail", "main.py", "bad code")
        findings = await svc.signal_done("run-fail", diff_text="<<<<<<< HEAD\n=======\n>>>>>>>")
        assert findings.passed is False
        assert (await svc.get_status("run-fail"))["state"] == AgentState.BLOCKED

    async def test_multiple_runs_isolated(self):
        svc = OrchestratorService()
        p1 = await svc.propose_plan("run-A")
        await svc.propose_plan("run-B")
        await svc.approve_plan("run-A", p1, "u1")
        await svc.start_execution("run-A")

        assert (await svc.get_status("run-A"))["state"] == AgentState.EXECUTING
        assert (await svc.get_status("run-B"))["state"] == AgentState.AWAITING_PLAN_APPROVAL

    async def test_get_status_auto_creates_run(self):
        svc = OrchestratorService()
        status = await svc.get_status("run-new")
        assert status["state"] == AgentState.IDLE
        assert status["planId"] is None

    async def test_human_edits_are_tracked(self):
        svc = OrchestratorService()
        pid = await svc.propose_plan("run-track")
        await svc.approve_plan("run-track", pid, "u1")
        await svc.start_execution("run-track")
        await svc.submit_human_edit("run-track", "a.py", "content-a")
        assert len(svc.runs["run-track"]["human_edits"]) == 1
        assert svc.runs["run-track"]["human_edits"][0]["path"] == "a.py"


# ================================================================
# REST API - EDGE CASES
# ================================================================


class TestOrchestratorRestApiStress:
    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from harness_contracts.roles import Principal, Role

        from harness_orchestrator.api import get_principal, router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_principal] = lambda: Principal(
            userId="u-stress", teamId="t-stress", role=Role.ADMIN
        )
        return TestClient(app)

    def test_propose_plan_returns_201(self, client):
        res = client.post("/api/v1/orchestrator/runs/api-s1/plan")
        assert res.status_code == 201
        assert "planId" in res.json()

    def test_start_without_approval_returns_403(self, client):
        client.post("/api/v1/orchestrator/runs/api-s2/plan")
        res = client.post("/api/v1/orchestrator/runs/api-s2/start")
        assert res.status_code == 403

    def test_full_api_lifecycle(self, client):
        # Propose
        res = client.post("/api/v1/orchestrator/runs/api-s3/plan")
        pid = res.json()["planId"]

        # Approve
        res = client.post(
            "/api/v1/orchestrator/runs/api-s3/plan/approve",
            json={"planId": pid, "userId": "u1"},
        )
        assert res.status_code == 200

        # Start
        res = client.post("/api/v1/orchestrator/runs/api-s3/start")
        assert res.status_code == 200

        # Pause
        res = client.post("/api/v1/orchestrator/runs/api-s3/pause")
        assert res.status_code == 200

        # Resume
        res = client.post("/api/v1/orchestrator/runs/api-s3/resume")
        assert res.status_code == 200

        # Human edit
        res = client.post(
            "/api/v1/orchestrator/runs/api-s3/human-edit",
            json={"path": "f.py", "content": "x"},
        )
        assert res.status_code == 200

        # Done signal
        res = client.post(
            "/api/v1/orchestrator/runs/api-s3/done-signal",
            json={"diffText": "clean"},
        )
        assert res.status_code == 200
        assert res.json()["findings"]["passed"] is True

        # Status
        res = client.get("/api/v1/orchestrator/runs/api-s3/status")
        assert res.status_code == 200
        assert "state" in res.json()

    def test_stop_returns_200(self, client):
        res = client.post("/api/v1/orchestrator/runs/api-s4/stop")
        assert res.status_code == 200


# ================================================================
# ENGINE SINGLE-MODEL EXECUTION (W1-B1: downgrade ladder deleted)
# ================================================================

class TestEngineSingleModel:
    """The configured model is the ONLY model. Failure is an honest
    status="error"; no other model is ever consulted (AGENTS.md law,
    raw-02 H1). Attribution names the model that answered (L1)."""

    def _wrapper(self, failing_model):
        from harness_orchestrator.engine import DeepSeekHarnessWrapper

        class StubWrapper(DeepSeekHarnessWrapper):
            def __init__(self):
                # M11-W1-B1: live mode validates ALL THREE config values at
                # construction (key, model, base_url) — the stub supplies a
                # syntactically valid base_url so validation passes and the
                # overridden _run_live below stays the thing under test.
                super().__init__(
                    api_key="***",
                    model="primary-model",
                    base_url="http://127.0.0.1:11434/v1",
                    live_mode="live",
                )
                self.calls = []

            def _run_live(self, goal, session_id, model=None):
                self.calls.append(model)
                if model == failing_model:
                    # M14-W4 F1a: 4th element = raw SDK events (usage source).
                    return "provider down", "error", model, []
                return f"ok via {model}", "completed", model, []

        return StubWrapper()

    def test_primary_success_single_attempt(self):
        w = self._wrapper(failing_model="never")
        out = w.execute_goal("goal", "s1")
        assert out["status"] == "completed"
        assert out["finalResponse"] == "ok via primary-model"
        assert out["engine"] == "deepseek-harness-sdk:primary-model"
        assert w.calls == ["primary-model"]

    def test_primary_failure_is_honest_error_no_downgrade(self):
        w = self._wrapper(failing_model="primary-model")
        out = w.execute_goal("goal", "s2")
        assert out["status"] == "error"
        assert "provider down" in out["finalResponse"]
        # Exactly ONE attempt against the configured model — no backup rung.
        assert w.calls == ["primary-model"]
        # L1 honesty: the engine string still names the model that ran.
        assert out["engine"] == "deepseek-harness-sdk:primary-model"

    def test_engine_exception_never_escapes_execute_goal(self):
        from harness_orchestrator.engine import DeepSeekHarnessWrapper

        class ExplodingWrapper(DeepSeekHarnessWrapper):
            def __init__(self):
                super().__init__(
                    api_key="k",
                    model="m1",
                    base_url="http://127.0.0.1:11434/v1",
                    live_mode="live",
                )

            def _run_live(self, goal, session_id, model=None):
                raise RuntimeError(f"{model} down")

        out = ExplodingWrapper().execute_goal("goal", "s3")
        assert out["status"] == "error"
        assert "m1" in out["finalResponse"]
        assert "m1" in out["engine"]
