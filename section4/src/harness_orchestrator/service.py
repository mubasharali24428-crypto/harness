import asyncio
import json
import logging
import os
import secrets
import socket
from typing import Any

from harness_contracts.events import EventType
from harness_contracts.plan import AgentPlan
from harness_contracts.signatures import Orchestrator

from harness_orchestrator.budget import BudgetStatus, check_budget
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.planner import PlanNotApprovedError, PlanService
from harness_orchestrator.reviewer import ReviewerSubAgent, ReviewFindings
from harness_orchestrator.run_queue import (
    claim_next_run,
    enqueue_run,
    finish_run,
    heartbeat_run,
    reclaim_orphans,
)
from harness_orchestrator.signals import gate_done_signal
from harness_orchestrator.state_machine import (
    PLAN_REJECTED_EVENT,
    AgentState,
    AgentStateMachine,
)

logger = logging.getLogger(__name__)


class PlanAlreadyDecidedError(Exception):
    """Optimistic-concurrency loss (M12-F2): the plan's version column moved
    under us, so a conditional decision UPDATE matched zero rows.

    The API layer maps this to 409 with code PLAN_ALREADY_DECIDED; legacy
    service-level callers treat it as best-effort (log + continue), matching
    the mirror's never-break-planning posture.
    """

    code = "PLAN_ALREADY_DECIDED"

_HEARTBEAT_INTERVAL_SECONDS = 10.0

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
    EventType.ENGINE_COMPLETED.value: AgentState.AWAITING_DONE_SIGNAL,
    EventType.ENGINE_FAILED.value: AgentState.BLOCKED,
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
        run_queue_factory=None,
        max_per_team: int = 4,
        worker_id: str | None = None,
        stale_seconds: int = 90,
    ) -> None:
        self.planner = planner or PlanService()
        self.reviewer = reviewer or ReviewerSubAgent()
        self.engine = engine or DeepSeekHarnessWrapper()
        self.on_event = on_event
        self.session_factory = session_factory
        self.bus = bus  # duck-typed EventBus: publish/ensure_run coroutines (S1 delivery)
        self.run_queue_factory = run_queue_factory or session_factory
        self.max_per_team = max_per_team
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"
        self.stale_seconds = stale_seconds

        self.runs: dict[str, dict[str, Any]] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        # Wave 6 (D3): per-run publish chains. Fire-and-forget tasks raced under
        # fast successive actions — execution_started once landed BEFORE the
        # plan_approved that caused it (live smoke evidence). Each run's publishes
        # now await the previous one, so bus seq order matches causal order.
        self._publish_chains: dict[str, asyncio.Task] = {}

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
        prev = self._publish_chains.get(run_id)
        task = loop.create_task(
            self._publish_ordered(prev, event_type, project_id, payload, run_id)
        )
        self._bg_tasks.add(task)
        self._publish_chains[run_id] = task

        def _chain_done(done: asyncio.Task, rid: str = run_id) -> None:
            self._bg_tasks.discard(done)
            if self._publish_chains.get(rid) is done:
                del self._publish_chains[rid]

        task.add_done_callback(_chain_done)

    async def _publish_ordered(
        self, prev: asyncio.Task | None, event_type, project_id, payload, run_id
    ) -> None:
        """Wave 6 (D3): serialize this run's publishes behind the previous one."""
        if prev is not None:
            try:
                await prev
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — predecessor failure must not stall us
                pass
        await self._publish_safe(event_type, project_id, payload, run_id)

    async def _publish_safe(self, event_type, project_id, payload, run_id) -> None:
        try:
            await self.bus.publish(event_type, project_id, dict(payload), run_id)
        except Exception:  # noqa: BLE001 — delivery must never break orchestration
            logger.exception("eventbus publish failed for run %s", run_id)

    # ------------------------------------------------------------- budget

    async def _budget_gate(self, run_data: dict[str, Any], plan: AgentPlan | None) -> bool:
        """Wave 9 node B: refuse to spend before a model call happens.

        Returns True when the team is over budget (run blocked honestly via
        ENGINE_FAILED — a frozen contract event). Any budget-read failure
        degrades to "allowed" so an outage cannot brick orchestration.
        """
        factory = self.run_queue_factory or self.session_factory
        if factory is None:
            return False  # bare in-memory mode: nothing to aggregate against
        team_id = await self._team_for_run(run_data)
        if not team_id:
            return False  # unbindable run: no cap applies
        status: BudgetStatus = await check_budget(
            factory,
            team_id,
            estimated_call_cost_usd=(
                float(plan.estimatedCostUsd) if plan is not None else None
            ),
        )
        if status.allowed:
            return False
        reason = f"Budget cap reached: {status.reason}"
        logger.warning("blocking run %s pre-call: %s", run_data.get("runId"), reason)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.execution_failed(reason)
        return True

    async def _team_for_run(self, run_data: dict[str, Any]) -> str | None:
        """Resolve the owning team id for a run (agent_runs → projects → teams).

        Prefers the run record's project_id; rehydrates it from agent_runs
        when this process never saw the bind. Read-only, failure-tolerant.
        """
        project_id = run_data.get("projectId")
        factory = self.run_queue_factory or self.session_factory
        if factory is None:
            return None
        from sqlalchemy import text

        try:
            async with factory() as db:
                if project_id:
                    row = (
                        await db.execute(
                            text("SELECT team_id FROM projects WHERE id = :p"),
                            {"p": project_id},
                        )
                    ).first()
                    if row is not None:
                        return row.team_id
                run_id = run_data.get("runId")
                if not run_id:
                    return None
                row = (
                    await db.execute(
                        text(
                            "SELECT p.team_id AS team_id FROM agent_runs r "
                            "JOIN projects p ON r.project_id = p.id WHERE r.id = :r"
                        ),
                        {"r": run_id},
                    )
                ).first()
                return row.team_id if row is not None else None
        except Exception:  # noqa: BLE001 — ownership lookup must never crash a run
            logger.exception("team lookup failed for run %s", run_data.get("runId"))
            return None

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
        # created_at must be a real datetime object: asyncpg rejects ISO strings
        # for TIMESTAMPTZ (verified live 2026-08-23), while SQLite accepts the
        # object via its adapter. This column is never compared as text.
        from datetime import UTC, datetime

        from sqlalchemy import text

        sql = text(
            """
            INSERT INTO agent_plans (id, run_id, goal, files_likely_to_change, steps, tests,
                                     risks, estimated_cost_usd, forbidden_paths,
                                     expected_result, status, created_at)
            VALUES (:id, :run_id, :goal, :files, :steps, :tests, :risks, :cost, :forbidden,
                    :expected, :status, :created_at)
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
                        # timezone-aware datetime: correct on asyncpg (TIMESTAMPTZ)
                        # and accepted by SQLite's adapter; never compared as text.
                        "created_at": datetime.now(UTC),
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
        # M12-F2: legacy surface stays best-effort. A concurrent decision that
        # won the version CAS must not 500 a caller of the in-process API.
        try:
            await self._persist_plan_status(run_id, plan_id, "approved", expected_version=0)
        except PlanAlreadyDecidedError:
            logger.info(
                "approve_plan on already-decided plan %s (concurrent winner kept)",
                plan_id,
            )

    async def reject_plan(self, run_id: str, plan_id: str, principal_user_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        if plan_id != run_data.get("plan_id"):
            raise KeyError(f"Plan '{plan_id}' does not belong to run '{run_id}'.")
        self.planner.reject_plan(plan_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.reject_plan(plan_id, principal_user_id)
        # M12-F2: same best-effort posture as approve_plan — a concurrent
        # decision that won the version CAS is logged, never raised here.
        try:
            await self._persist_plan_status(run_id, plan_id, "rejected", expected_version=0)
        except PlanAlreadyDecidedError:
            logger.info(
                "reject_plan on already-decided plan %s (concurrent winner kept)",
                plan_id,
            )
        # S-010: persist the rejection as an agent_events row (run-scoped, no
        # project FK) so it survives restart and is queryable alongside the
        # audit row. The S1 bus requires an EventType member + project FK and
        # the frozen contracts enum has no PLAN_REJECTED member, so this write
        # goes direct with the same wire name the state machine emits.
        await self._persist_rejection_event(run_id, plan_id, principal_user_id)
        await self._persist_plan_status(run_id, plan_id, "rejected")
        # W1-A2 seam: audited money-adjacent decision (plan.rejected is in the
        # frozen append-only allowlist). The event above carries runId/planId/
        # rejectedBy on the bus; this row makes it queryable + tamper-evident.
        # Best-effort by construction — the S1 helper never raises into us.
        if self.session_factory is not None:
            try:
                from harness_identity.audit_emit import emit_plan_rejected

                async with self.session_factory() as db:
                    await emit_plan_rejected(
                        db,
                        actor_user_id=principal_user_id,
                        team_id=await self._team_for_run(run_data) or "",
                        target_id=run_id,
                        detail={"planId": plan_id},
                        session_owner=True,
                    )
            except Exception:  # noqa: BLE001 — audit must never break rejection
                logger.exception("plan.rejected audit emission failed for %s", run_id)

    async def start_execution(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        plan_id = run_data.get("plan_id")
        if not plan_id or not self.planner.is_approved(plan_id):
            raise PlanNotApprovedError(f"Cannot execute run '{run_id}': Plan is not approved.")

        sm: AgentStateMachine = run_data["state_machine"]
        sm.start_execution()

        plan = self.planner.get_plan(plan_id)
        if self.run_queue_factory is not None:
            queued = await enqueue_run(
                self.run_queue_factory, run_id, run_data.get("projectId") or ""
            )
            if queued:
                return  # worker loop will claim and spawn
            # Not queued = a live claim already exists (double start) OR the
            # queue table is absent (bare sqlite in-memory test DB). Spawn
            # directly unless the run is genuinely executing right now.
            live = run_data.get("task")
            if live is not None and not live.done():
                logger.info("run %s already has a live claim; ignoring double start", run_id)
                return
        self._spawn_execution(run_data, plan)

    def _spawn_execution(self, run_data: dict[str, Any], plan: AgentPlan) -> None:
        """Start one live engine task. Shared by start_execution and resume_agent
        so a resumed run re-invokes the engine with the SAME session_id (= run_id);
        the dsh SDK continues that session instead of starting a fresh one."""
        run_data["cancel_requested"] = False
        run_data["_cancel_reason"] = None
        run_data["task"] = asyncio.create_task(self._execute(run_data, plan))

    async def _execute(self, run_data: dict[str, Any], plan: AgentPlan) -> None:
        run_id = run_data["runId"]
        sm: AgentStateMachine = run_data["state_machine"]
        heartbeat_task = None
        if self.run_queue_factory is not None:

            async def _heartbeat() -> None:
                while True:
                    try:
                        await heartbeat_run(self.run_queue_factory, run_id, self.worker_id)
                    except Exception:  # noqa: BLE001 — heartbeat must not kill execution
                        logger.exception("heartbeat failed for run %s", run_id)
                    await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)

            heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            if await self._budget_gate(run_data, plan):  # Wave 9 B: no model call over cap
                return
            try:
                result = await asyncio.to_thread(
                    self.engine.execute_goal, goal=plan.goal, session_id=run_id
                )
            except asyncio.CancelledError:
                # Pause keeps the run resumable: no terminal result written. Stop and
                # other terminal cancellations record an honest 'cancelled' outcome.
                if run_data.get("_cancel_reason") != "pause":
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
                # Wave 6 (D4): honest failure lands on the bus and blocks the run
                # (no-op unless still EXECUTING — pause/stop already told their story).
                sm.execution_failed(f"DeepSeek Harness run failed: {exc}")
                return
            if run_data.get("cancel_requested"):
                return  # zombie result from a cancelled live call — discard
            run_data["engine_result"] = result
            # Wave 6 (D4): engine settled while still EXECUTING → emit + advance.
            # Completed opens the human done-gate; any other status blocks honestly.
            summary = str(result.get("finalResponse") or "")
            status = str(result.get("status") or "")
            if status == "completed":
                sm.execution_completed(summary[:4000])
            else:
                sm.execution_failed(summary or f"engine status={status or 'unknown'}")
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
            if self.run_queue_factory is not None:
                await finish_run(self.run_queue_factory, run_id)

    async def get_engine_result(self, run_id: str) -> dict[str, Any]:
        run_data = await self._ensure_run(run_id)
        return run_data.get(
            "engine_result",
            {"status": "not_started", "finalResponse": None},
        )

    async def pause_agent(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        run_data["_cancel_reason"] = "pause"
        sm.pause(reason="user_requested")
        self._cancel_live_task(run_data)

    async def resume_agent(self, run_id: str) -> None:
        """Resume a paused/blocked run by RE-SPAWNING the engine task.

        The pre-fix version only flipped the state machine to EXECUTING while no
        task existed — a state lie (Appendix C). Re-invoking execute_goal with
        the same run_id as session_id is the checkpoint continuation: dsh resumes
        its own session history rather than restarting the goal blind."""
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]

        live = run_data.get("task")
        if live is not None and not live.done():
            if not run_data.get("cancel_requested"):
                return  # genuinely running — nothing to resurrect
            # A just-cancelled task may not have processed its CancelledError
            # yet (pause→resume in the same tick): let it finish dying, then
            # respawn. Blocks only if the engine thread ignores cancellation,
            # in which case waiting for the zombie leg is still correct.
            try:
                await live
            except asyncio.CancelledError:
                pass

        plan_id = run_data.get("plan_id")
        if not plan_id or not self.planner.is_approved(plan_id):
            raise PlanNotApprovedError(f"Cannot resume run '{run_id}': Plan is not approved.")
        plan = self.planner.get_plan(plan_id)

        sm.resume()  # PAUSED/BLOCKED → EXECUTING (+ RESUMED event); no-op otherwise
        if sm.state is not AgentState.EXECUTING:
            return  # resume from IDLE/DONE/… is a no-op — nothing to resurrect
        if self.run_queue_factory is not None:
            queued = await enqueue_run(
                self.run_queue_factory, run_id, run_data.get("projectId") or ""
            )
            if not queued:
                logger.info("run %s already has a live claim; ignoring double resume", run_id)
                return
            return  # worker loop will claim and spawn
        self._spawn_execution(run_data, plan)

    async def stop_agent(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        run_data["_cancel_reason"] = "stop"  # terminal: overwrites any earlier 'pause'
        sm.finish()
        self._cancel_live_task(run_data)

    def _cancel_live_task(self, run_data: dict[str, Any]) -> None:
        task = run_data.get("task")
        if task is not None and not task.done():
            run_data["cancel_requested"] = True
            task.cancel()

    async def recover_orphans(self) -> list[str]:
        """Requeue claims whose worker went silent (crash detector)."""
        if self.run_queue_factory is None:
            return []
        return await reclaim_orphans(self.run_queue_factory, stale_seconds=self.stale_seconds)

    async def poll_once(self) -> str | None:
        """Claim one queued run and spawn its engine task; returns the run_id."""
        if self.run_queue_factory is None:
            return None
        claim = await claim_next_run(self.run_queue_factory, self.worker_id, self.max_per_team)
        if claim is None:
            return None
        run_id = claim["run_id"]
        run_data = await self._ensure_run(run_id, project_id=claim.get("project_id"))
        sm: AgentStateMachine = run_data["state_machine"]
        if sm.state is not AgentState.EXECUTING:
            await finish_run(self.run_queue_factory, run_id)
            return None
        live = run_data.get("task")
        if live is not None and not live.done():
            return None
        plan_id = run_data.get("plan_id")
        if not plan_id or not self.planner.is_approved(plan_id):
            logger.error("claimed run %s lacks an approved plan; dropping claim", run_id)
            await finish_run(self.run_queue_factory, run_id)
            return None
        plan = self.planner.get_plan(plan_id)
        if await self._budget_gate(run_data, plan):  # Wave 9 B: never spawn over cap
            await finish_run(self.run_queue_factory, run_id)
            return None
        self._spawn_execution(run_data, plan)
        return run_id

    async def submit_human_edit(self, run_id: str, path: str, content: str) -> None:
        run_data = await self._ensure_run(run_id)
        run_data["human_edits"].append({"path": path, "content": content})
        sm: AgentStateMachine = run_data["state_machine"]
        run_data["_cancel_reason"] = "pause"  # human edit pauses, keeps run resumable
        sm.submit_human_edit(path=path)
        self._cancel_live_task(run_data)

    async def gate_done_signal(
        self, run_id: str, principal: Any, diff_text: str
    ) -> None:
        """H-007: validate a done-signal BEFORE any transition or review spend.

        Centralizes the signals.py gate at the service seam so the FastAPI
        route and any internal caller enforce identical role/evidence/liveness
        semantics. Raises the typed DoneSignalRejectedError subclasses (the
        route maps them to 403/409/400).
        """
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        gate_done_signal(principal, sm.state, diff_text)

    async def signal_done(self, run_id: str, diff_text: str = "") -> ReviewFindings:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.signal_done()
        self._cancel_live_task(run_data)

        # Wave 9 node B: the reviewer's model_router call is a real spend —
        # check the cap BEFORE invoking it. Over budget ⇒ no model call; the
        # reviewer falls back to deterministic heuristics and records why.
        budget_block: str | None = None
        factory = self.run_queue_factory or self.session_factory
        if factory is not None and self.reviewer.model_router is not None:
            team_id = await self._team_for_run(run_data)
            if team_id:
                status: BudgetStatus = await check_budget(factory, team_id)
                if not status.allowed:
                    budget_block = status.reason
                    logger.warning(
                        "reviewer model call withheld for run %s: %s", run_id, budget_block
                    )

        findings = self.reviewer.evaluate_changes(
            brief_summary=run_data["goal"],
            diff_text=diff_text,
            budget_blocked_reason=budget_block,
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
        return await self._run_aggregate(run_id)

    async def _run_aggregate(self, run_id: str) -> dict[str, Any]:
        """One minimal aggregate read for a single run (M12-W2 E4a).

        Base shape is get_status()'s wire dict; when a DB is wired it adds
        ``prId`` (agent_runs.pr_id, migration 0014 — set by S2's PR-open
        producer) so the review flow can resolve run → PR without replaying
        events. Read-only + failure-tolerant: any DB trouble degrades to
        prId=None rather than breaking the status read.
        """
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        task = run_data.get("task")
        # M11-W1-B1: expose the EXPLICIT engine mode on the wire so clients
        # never re-derive liveness from key presence (finding C-004).
        engine_mode = getattr(self.engine, "mode", None)
        aggregate: dict[str, Any] = {
            "runId": run_id,
            "state": sm.state,
            "planId": run_data.get("plan_id"),
            "planApproved": sm.plan_approved,
            "isHumanEditing": sm.is_human_editing,
            "executing": bool(task is not None and not task.done()),
            "engineMode": getattr(engine_mode, "value", engine_mode),
            # camelCase per api-conventions; None until a PR is opened.
            "prId": None,
        }
        factory = self.session_factory
        if factory is None:
            return aggregate
        from sqlalchemy import text

        try:
            async with factory() as db:
                row = (
                    (
                        await db.execute(
                            text("SELECT pr_id FROM agent_runs WHERE id = :r"), {"r": run_id}
                        )
                    )
                    .mappings()
                    .first()
                )
            if row is not None and row["pr_id"]:
                aggregate["prId"] = row["pr_id"]
        except Exception:  # noqa: BLE001 — aggregate must never fail on prId
            logger.exception("prId lookup failed for run %s", run_id)
        return aggregate

    async def _persist_plan_status(
        self,
        run_id: str,
        plan_id: str,
        status: str,
        expected_version: int | None = None,
    ) -> int | None:
        """Mirror a decision into ``agent_plans`` (M12-F2 optimistic concurrency).

        With ``expected_version`` set, the write is a compare-and-swap:
        ``UPDATE ... WHERE id = :id AND version = :seen``. Zero rows updated
        means another principal decided this plan first — the mirror is left
        untouched and :class:`PlanAlreadyDecidedError` is raised so the API
        layer can answer 409 PLAN_ALREADY_DECIDED instead of both callers
        believing they won. On success the NEW version is returned.

        With ``expected_version=None`` the update stays unconditional (legacy
        behavior for non-decision writers). Best-effort posture is unchanged:
        infrastructure failures are logged, never raised, into orchestration.
        """
        if self.session_factory is None:
            return None
        from sqlalchemy import text

        try:
            async with self.session_factory() as db:
                if expected_version is None:
                    await db.execute(
                        text("UPDATE agent_plans SET status = :s WHERE id = :p"),
                        {"s": status, "p": plan_id},
                    )
                    await db.commit()
                    return None
                try:
                    result = await db.execute(
                        text(
                            "UPDATE agent_plans SET status = :s,"
                            " version = version + 1"
                            " WHERE id = :p AND version = :v"
                        ),
                        {"s": status, "p": plan_id, "v": expected_version},
                    )
                except Exception:  # noqa: BLE001
                    # Mirror predates migration 0013 (no ``version`` column):
                    # concurrency enforcement cannot apply, so degrade to the
                    # exact legacy behavior — one unconditional decision write
                    # — instead of failing the decision.
                    logger.info(
                        "agent_plans CAS unavailable for %s;"
                        " falling back to unconditional decision write",
                        plan_id,
                    )
                    await db.rollback()
                    await db.execute(
                        text("UPDATE agent_plans SET status = :s WHERE id = :p"),
                        {"s": status, "p": plan_id},
                    )
                    await db.commit()
                    return None
                if result.rowcount == 0:
                    # Zero rows is ambiguous: stale version (someone decided
                    # first → conflict) vs mirror row never written (legacy
                    # silent-skip posture). Probe existence to tell them apart.
                    exists = (
                        await db.execute(
                            text("SELECT 1 FROM agent_plans WHERE id = :p"),
                            {"p": plan_id},
                        )
                    ).first()
                    if exists is None:
                        # No mirror row: nothing to compare-and-swap against;
                        # behave exactly like the legacy unconditional update
                        # on a missing row (log-free silent skip).
                        await db.commit()
                        return None
                    await db.rollback()
                    raise PlanAlreadyDecidedError(
                        f"Plan '{plan_id}' was already decided (stale version"
                        f" {expected_version})."
                    )
                new_version: int | None = (
                    await db.execute(
                        text("SELECT version FROM agent_plans WHERE id = :p"),
                        {"p": plan_id},
                    )
                ).scalar()
                await db.commit()
                return new_version
        except PlanAlreadyDecidedError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("plan status update failed for %s", plan_id)
            return None

    async def _persist_rejection_event(
        self, run_id: str, plan_id: str, principal_user_id: str
    ) -> None:
        """Write the ``plan.rejected`` row into agent_events (best-effort).

        The frozen schema makes ``project_id`` NOT NULL + FK'd, so the owner
        project is resolved first (in-memory bind, else the agent_runs row)
        and an unbound run simply skips persistence. Rehydration ignores
        unknown event names by design, so replaying this row cannot resurrect
        anything — it exists for observability/audit.
        """
        factory = self.session_factory
        if factory is None:
            return
        from sqlalchemy import text

        try:
            async with factory() as db:
                project_id = self.runs.get(run_id, {}).get("projectId")
                if not project_id:
                    row = (
                        await db.execute(
                            text("SELECT project_id FROM agent_runs WHERE id = :r"),
                            {"r": run_id},
                        )
                    ).first()
                    project_id = row.project_id if row is not None else None
                if not project_id:
                    return  # frozen schema: no nullable escape hatch — skip
                await db.execute(
                    text(
                        "INSERT INTO agent_events (project_id, run_id, type, payload)"
                        " VALUES (:project_id, :run_id, :etype, :payload)"
                    ),
                    {
                        "project_id": project_id,
                        "run_id": run_id,
                        "etype": PLAN_REJECTED_EVENT,
                        "payload": json.dumps(
                            {
                                "runId": run_id,
                                "planId": plan_id,
                                "rejectedBy": principal_user_id,
                            }
                        ),
                    },
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — rejection must never fail on telemetry
            logger.exception("plan.rejected event persistence failed for %s", run_id)
