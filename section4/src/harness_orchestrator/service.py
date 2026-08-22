import asyncio
import json
import logging
from typing import Any

from harness_contracts.events import EventType
from harness_contracts.plan import AgentPlan
from harness_contracts.signatures import Orchestrator

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.planner import PlanNotApprovedError, PlanService
from harness_orchestrator.reviewer import ReviewerSubAgent, ReviewFindings
from harness_orchestrator.state_machine import AgentState, AgentStateMachine

logger = logging.getLogger(__name__)

_REHYDRATE_SQL = """
    SELECT type AS event_type, payload AS payload
    FROM agent_events
    WHERE run_id = :run_id
    ORDER BY seq ASC
"""

_PLAN_SQL = """
    SELECT id, goal, files_likely_to_change, steps, tests, risks,
           estimated_cost_usd, forbidden_paths, expected_result, status
    FROM agent_plans
    WHERE run_id = :run_id
    ORDER BY created_at DESC, id DESC
    LIMIT 1
"""

_EVENT_STATE_MAP: dict[str, AgentState] = {
    EventType.PLAN_PROPOSED.value: AgentState.AWAITING_PLAN_APPROVAL,
    EventType.EXECUTION_STARTED.value: AgentState.EXECUTING,
    EventType.PAUSED.value: AgentState.PAUSED,
    EventType.RESUMED.value: AgentState.EXECUTING,
    EventType.BLOCKED_AWAITING_CLARIFICATION.value: AgentState.BLOCKED,
    EventType.DONE_SIGNAL_RECEIVED.value: AgentState.REVIEWING,
    EventType.REVIEW_PASSED.value: AgentState.EXECUTING,
    EventType.REVIEW_FAILED.value: AgentState.BLOCKED,
    EventType.PR_OPENED.value: AgentState.PR_OPENED,
}


class OrchestratorService(Orchestrator):
    """Section 4 Orchestration Engine implementing the Orchestrator Protocol.

    Persistence model (dsh-inspired): the S1 ``agent_events`` log is the source of
    truth. When a ``session_factory`` is provided, every state-machine transition is
    published to the bus (which persists it) and plans are mirrored into
    ``agent_plans``; a run missing from memory is rehydrated by replaying its event
    history. Without a factory the service behaves exactly as a pure in-memory
    engine (unit tests).
    """

    def __init__(
        self,
        planner: PlanService | None = None,
        reviewer: ReviewerSubAgent | None = None,
        engine: DeepSeekHarnessWrapper | None = None,
        on_event=None,
        session_factory=None,
        bus=None,
    ) -> None:
        self.planner = planner or PlanService()
        self.reviewer = reviewer or ReviewerSubAgent()
        self.engine = engine or DeepSeekHarnessWrapper()
        self.on_event = on_event
        self.session_factory = session_factory
        self.bus = bus  # duck-typed EventBus: publish/ensure_run coroutines (S1 delivery)

        self.runs: dict[str, dict[str, Any]] = {}
        self._bg_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ events

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """Single emission bridge: sync test callback first, then bus fan-out."""
        if self.on_event:
            self.on_event(event_type, payload)
        if self.bus is None:
            return
        run_id = payload.get("runId") if isinstance(payload, dict) else None
        project_id = self.runs.get(run_id or "", {}).get("projectId")
        if not project_id or not run_id:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # sync context (unit tests): DB delivery handled by API layer
        task = loop.create_task(self._publish_safe(event_type, project_id, payload, run_id))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _publish_safe(self, event_type, project_id, payload, run_id) -> None:
        try:
            await self.bus.publish(event_type, project_id, dict(payload), run_id)
        except Exception:  # noqa: BLE001 — delivery must never break orchestration
            logger.exception("eventbus publish failed for run %s", run_id)

    # ------------------------------------------------------------------- runs

    async def _ensure_run(self, run_id: str, project_id: str | None = None) -> dict[str, Any]:
        if run_id in self.runs:
            return self.runs[run_id]

        sm = AgentStateMachine(run_id=run_id, on_event=self._emit)
        run_data: dict[str, Any] = {
            "runId": run_id,
            "state_machine": sm,
            "plan_id": None,
            "goal": "Build feature from brief",
            "human_edits": [],
            "projectId": project_id,
            "task": None,
            "cancel_requested": False,
        }

        rehydrated = await self._rehydrate(run_id, sm, run_data)
        if not rehydrated and self.session_factory is not None and self.bus is not None:
            try:
                await self.bus.ensure_run(project_id or "", run_id)
            except Exception:  # noqa: BLE001 — FK seeding is best-effort pre-first-event
                logger.exception("ensure_run failed for %s", run_id)
        self.runs[run_id] = run_data
        return run_data

    async def _rehydrate(self, run_id: str, sm: AgentStateMachine, rd: dict[str, Any]) -> bool:
        if self.session_factory is None:
            return False
        from sqlalchemy import text

        try:
            async with self.session_factory() as db:
                rows = (
                    (await db.execute(text(_REHYDRATE_SQL), {"run_id": run_id})).mappings().all()
                )
                plan_row = (
                    (await db.execute(text(_PLAN_SQL), {"run_id": run_id})).mappings().first()
                )
                run_res = await db.execute(
                    text("SELECT project_id FROM agent_runs WHERE id = :r"), {"r": run_id}
                )
                run_row = run_res.first()
        except Exception:  # noqa: BLE001 — no table yet / no DB in unit mode
            return False

        if run_row is not None and rd.get("projectId") is None:
            rd["projectId"] = run_row.project_id

        if plan_row is not None:
            rd["plan_id"] = plan_row["id"]
            rd["goal"] = plan_row["goal"]
            plan = self._plan_from_row(plan_row)
            if plan is not None:
                self.planner.plans[plan_row["id"]] = plan
                self.planner.plan_approvals[plan_row["id"]] = plan_row["status"] == "approved"
                sm.plan_approved = plan_row["status"] == "approved"

        last_seq_state = False
        for row in rows:
            etype = row["event_type"]
            payload = row["payload"]
            if isinstance(payload, str):  # sqlite JSON columns come back as str
                try:
                    payload = json.loads(payload)
                except Exception:  # noqa: BLE001
                    payload = {}
            if etype == EventType.PLAN_APPROVED.value:
                sm.plan_approved = True
                approved_plan = (payload or {}).get("planId") if isinstance(payload, dict) else None
                if approved_plan:
                    rd["plan_id"] = approved_plan
                    self.planner.plan_approvals[approved_plan] = True
                continue
            new_state = _EVENT_STATE_MAP.get(etype)
            if new_state is not None:
                sm.state = new_state
                last_seq_state = True
        if rows:
            rd.setdefault("_rehydrated", True)
            return bool(rows) or last_seq_state
        return bool(plan_row is not None)

    def _plan_from_row(self, row) -> AgentPlan | None:
        try:
            return AgentPlan(
                goal=row["goal"],
                filesLikelyToChange=list(row["files_likely_to_change"] or []),
                steps=list(row["steps"] or []),
                tests=list(row["tests"] or []),
                risks=list(row["risks"] or []),
                estimatedCostUsd=float(row["estimated_cost_usd"] or 0),
                forbiddenPaths=list(row["forbidden_paths"] or []),
                expectedResult=str(row["expected_result"] or ""),
            )
        except Exception:  # noqa: BLE001 — corrupt row must not kill rehydration
            return None

    async def _persist_plan(self, run_id: str, plan_id: str, plan: AgentPlan, status: str) -> None:
        if self.session_factory is None:
            return
        from sqlalchemy import text

        sql = text(
            """
            INSERT INTO agent_plans (id, run_id, goal, files_likely_to_change, steps, tests,
                                     risks, estimated_cost_usd, forbidden_paths,
                                     expected_result, status, created_at)
            VALUES (:id, :run_id, :goal, :files, :steps, :tests, :risks, :cost, :forbidden,
                    :expected, :status, now())
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
            """
        )
        try:
            async with self.session_factory() as db:
                await db.execute(
                    sql,
                    {
                        "id": plan_id,
                        "run_id": run_id,
                        "goal": plan.goal,
                        "files": json.dumps(plan.filesLikelyToChange),
                        "steps": json.dumps(plan.steps),
                        "tests": json.dumps(plan.tests),
                        "risks": json.dumps(plan.risks),
                        "cost": float(plan.estimatedCostUsd),
                        "forbidden": json.dumps(plan.forbiddenPaths),
                        "expected": plan.expectedResult,
                        "status": status,
                    },
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — mirror write must never break planning
            logger.exception("plan persistence failed for %s", plan_id)

    # ------------------------------------------------------------------- api

    async def propose_plan(self, run_id: str, goal: str | None = None,
                           project_id: str | None = None) -> str:
        run_data = await self._ensure_run(run_id, project_id=project_id)
        sm: AgentStateMachine = run_data["state_machine"]
        if goal:
            run_data["goal"] = goal
        if project_id:
            run_data["projectId"] = project_id

        sm.start_planning()
        plan_id, plan = self.planner.create_plan(goal=run_data["goal"])
        run_data["plan_id"] = plan_id
        await self._persist_plan(run_id, plan_id, plan, status="proposed")

        sm.plan_proposed(plan_id)
        return plan_id

    async def approve_plan(self, run_id: str, plan_id: str, principal_user_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        expected = run_data.get("plan_id")
        if plan_id != expected:
            raise KeyError(f"Plan '{plan_id}' does not belong to run '{run_id}'.")
        self.planner.approve_plan(plan_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.approve_plan(plan_id, principal_user_id)
        await self._persist_plan_status(run_id, plan_id, "approved")

    async def reject_plan(self, run_id: str, plan_id: str, principal_user_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        if plan_id != run_data.get("plan_id"):
            raise KeyError(f"Plan '{plan_id}' does not belong to run '{run_id}'.")
        self.planner.reject_plan(plan_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.reject_plan(plan_id, principal_user_id)
        await self._persist_plan_status(run_id, plan_id, "rejected")

    async def start_execution(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        plan_id = run_data.get("plan_id")
        if not plan_id or not self.planner.is_approved(plan_id):
            raise PlanNotApprovedError(f"Cannot execute run '{run_id}': Plan is not approved.")

        sm: AgentStateMachine = run_data["state_machine"]
        sm.start_execution()

        plan = self.planner.get_plan(plan_id)
        run_data["cancel_requested"] = False
        run_data["task"] = asyncio.create_task(self._execute(run_data, plan))

    async def _execute(self, run_data: dict[str, Any], plan: AgentPlan) -> None:
        run_id = run_data["runId"]
        try:
            result = await asyncio.to_thread(
                self.engine.execute_goal, goal=plan.goal, session_id=run_id
            )
        except asyncio.CancelledError:
            run_data["engine_result"] = {
                "sessionId": run_id,
                "engine": "cancelled",
                "status": "cancelled",
                "finalResponse": "Execution cancelled via pause/stop.",
            }
            return
        except Exception as exc:  # noqa: BLE001 — surface engine failures to the UI
            run_data["engine_result"] = {
                "sessionId": run_id,
                "engine": "error",
                "status": "error",
                "finalResponse": f"DeepSeek Harness run failed: {exc}",
            }
            return
        if run_data.get("cancel_requested"):
            return  # zombie result from a cancelled live call — discard
        run_data["engine_result"] = result

    async def get_engine_result(self, run_id: str) -> dict[str, Any]:
        run_data = await self._ensure_run(run_id)
        return run_data.get(
            "engine_result",
            {"status": "not_started", "finalResponse": None},
        )

    async def pause_agent(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.pause(reason="user_requested")
        self._cancel_live_task(run_data)

    async def resume_agent(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.resume()

    async def stop_agent(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.finish()
        self._cancel_live_task(run_data)

    def _cancel_live_task(self, run_data: dict[str, Any]) -> None:
        task = run_data.get("task")
        if task is not None and not task.done():
            run_data["cancel_requested"] = True
            task.cancel()

    async def submit_human_edit(self, run_id: str, path: str, content: str) -> None:
        run_data = await self._ensure_run(run_id)
        run_data["human_edits"].append({"path": path, "content": content})
        sm: AgentStateMachine = run_data["state_machine"]
        sm.submit_human_edit(path=path)
        self._cancel_live_task(run_data)

    async def signal_done(self, run_id: str, diff_text: str = "") -> ReviewFindings:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.signal_done()
        self._cancel_live_task(run_data)

        findings = self.reviewer.evaluate_changes(
            brief_summary=run_data["goal"],
            diff_text=diff_text,
        )
        if findings.passed:
            sm.reviewer_passed(findings.model_dump(by_alias=True))
        else:
            sm.reviewer_failed(findings.model_dump(by_alias=True))

        return findings

    async def get_plan(self, run_id: str) -> AgentPlan:
        run_data = await self._ensure_run(run_id)
        plan_id = run_data.get("plan_id")
        if not plan_id:
            raise KeyError(f"No plan proposed yet for run '{run_id}'.")
        return self.planner.get_plan(plan_id)

    async def get_status(self, run_id: str) -> dict[str, Any]:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        task = run_data.get("task")
        return {
            "runId": run_id,
            "state": sm.state,
            "planId": run_data.get("plan_id"),
            "planApproved": sm.plan_approved,
            "isHumanEditing": sm.is_human_editing,
            "executing": bool(task is not None and not task.done()),
        }

    async def _persist_plan_status(self, run_id: str, plan_id: str, status: str) -> None:
        if self.session_factory is None:
            return
        from sqlalchemy import text

        try:
            async with self.session_factory() as db:
                await db.execute(
                    text("UPDATE agent_plans SET status = :s WHERE id = :p"),
                    {"s": status, "p": plan_id},
                )
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("plan status update failed for %s", plan_id)
