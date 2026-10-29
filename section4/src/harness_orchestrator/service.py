import asyncio
import inspect
import json
import logging
import os
import queue
import secrets
import socket
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from harness_contracts.events import EventType
from harness_contracts.plan import AgentPlan
from harness_contracts.signatures import Orchestrator
from harness_contracts.timeutil import db_now
from sqlalchemy import text

from harness_orchestrator import (
    collab,
    domain_metrics,
    memory_loader,
    run_checkpoints,
    skills_loader,
    staging,
)
from harness_orchestrator.budget import (
    BudgetStatus,
    check_budget,
    deliver_budget_warning,
    reserve_daily_run,
)
from harness_orchestrator.context_resolver import (
    DEFAULT_MIN_SCORE,
    context_settings_from_env,
    resolve_context,
    seed_files_from_bundle,
)
from harness_orchestrator.diffstore import resolve_diff, save_diff
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.engine_runtime import (
    CircuitOpenError,
    EngineRuntime,
    EngineRunTimeout,
    accepts_keyword,
)
from harness_orchestrator.goal_images import GoalImages, resolve_goal_images
from harness_orchestrator.lifecycle import lifecycle_view
from harness_orchestrator.loop_guard import LoopGuard, loop_repeat_limit
from harness_orchestrator.planner import PlanNotApprovedError, PlanService
from harness_orchestrator.redact import scrub_secrets
from harness_orchestrator.reviewer import ReviewerSubAgent, ReviewFindings
from harness_orchestrator.rules_loader import build_rules_block, load_project_rules
from harness_orchestrator.run_metering import record_engine_tool_calls, record_run_cost
from harness_orchestrator.run_queue import (
    claim_next_run,
    enqueue_run,
    executing_run_ids,
    finish_run,
    heartbeat_age_seconds,
    heartbeat_run,
    queue_available,
    reclaim_orphans,
    release_timed_out_claims,
    requeue_claim,
    resolve_heartbeat_timeout_seconds,
)
from harness_orchestrator.session_gate import (
    check_session_run_start,
    enforce_session_run_start,
)
from harness_orchestrator.signals import StagedApprovalPendingError, gate_done_signal
from harness_orchestrator.state_machine import (
    PLAN_REJECTED_EVENT,
    TERMINAL_STATES,
    AgentState,
    AgentStateMachine,
    InvalidStateTransitionError,
    approval_expired,
    approved_at_from_wire,
    plan_approval_ttl_seconds,
)
from harness_orchestrator.workspace_gate import check_run_start, enforce_run_start

logger = logging.getLogger(__name__)


def durable_event_text(value: Any) -> str:
    """Text that can actually be stored as an event payload.

    PostgreSQL's text/JSONB types cannot hold U+0000, and asyncpg raises
    ``UntranslatableCharacterError`` — the *whole* event insert fails. Observed
    in the W4 credential drill: a bash call whose result was
    ``cat /proc/self/environ`` produced a NUL-separated dump, the tool_result
    row was dropped, and the transcript showed a tool call with no result. A
    lost result is worse than a lossy one, so NUL becomes U+FFFD (visible) and
    lone surrogates are normalised; everything else is preserved verbatim.
    """
    text = str(value if value is not None else "").replace("\x00", "\ufffd")
    return text.encode("utf-8", "replace").decode("utf-8")


class PlanAlreadyDecidedError(Exception):
    """Optimistic-concurrency loss (M12-F2): the plan's version column moved
    under us, so a conditional decision UPDATE matched zero rows.

    The API layer maps this to 409 with code PLAN_ALREADY_DECIDED; legacy
    service-level callers treat it as best-effort (log + continue), matching
    the mirror's never-break-planning posture.
    """

    code = "PLAN_ALREADY_DECIDED"


class RunNotFoundError(KeyError):
    """M14-W4 F3a: the requested run has no durable existence.

    Raised by ``_run_aggregate`` only when a DB is wired AND the run has no
    ``agent_runs`` row AND it carries no in-memory lifecycle (never bound,
    planned, executed, or rehydrated from events). The API layer maps this to
    the 404 NOT_FOUND envelope; a foreign team's run answers the exact same
    envelope (no-oracle law lives in the authz dependency).
    """


_HEARTBEAT_INTERVAL_SECONDS = 10.0

_REHYDRATE_SQL = """
    SELECT type AS event_type, payload AS payload, seq AS seq
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

# M19-N1 (stateless orchestrator phase 1): the mirror row IS the decision of
# record. Reads consult it first; the in-RAM planner dict is a cache that a
# replica converges FROM the store, never the other way around. ``version``
# rides along so callers can hand the seen version straight back into the
# decision CAS without a second round-trip.
_PLAN_DECISION_SQL = """
    SELECT status, version FROM agent_plans WHERE id = :plan_id
"""

# Terminal runs are evicted from RAM (cache-only): their lifecycle truth lives
# entirely in agent_events + agent_plans + agent_runs, all of which replay.
_ENGINE_RESULT_FALLBACK_SQL = """
    SELECT payload AS payload FROM agent_events
    WHERE run_id = :run_id AND type IN (:completed_type, :failed_type)
    ORDER BY seq DESC
    LIMIT 1
"""

# Durable liveness fence for start/resume: a run whose event history already
# shows execution beginning may not be re-driven from another replica's
# stale cache — exactly-once is decided by the STORE, not by whichever
# process answers the request. plan.rejected rows are inert here by design.
_LIFECYCLE_HEAD_SQL = """
    SELECT type AS event_type FROM agent_events
    WHERE run_id = :run_id AND type != :rejected_type
    ORDER BY seq DESC
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

# Part 47: run events that put a row in the run participants' inbox -> (kind, summary_code).
_RUN_PROBLEMS: dict[str, tuple[str, str]] = {
    EventType.ENGINE_FAILED.value: ("failure", "run_failed"),
    EventType.REVIEW_FAILED.value: ("failure", "review_failed"),
    EventType.BLOCKED_AWAITING_CLARIFICATION.value: ("blocked", "run_blocked"),
}


class OrchestratorService(Orchestrator):
    """Section 4 Orchestration Engine implementing the Orchestrator Protocol.

    Persistence model (dsh-inspired): the S1 ``agent_events`` log is the source of
    truth. When a ``session_factory`` is provided, every state-machine transition is
    published to the bus (which persists it) and plans are mirrored into
    ``agent_plans``; a run missing from memory is rehydrated by replaying its event
    history. Without a factory the service behaves exactly as a pure in-memory
    engine (unit tests).

    M19-N1 (stateless orchestrator, phase 1): durable-first reads and N-replica
    safety. The in-RAM ``self.runs`` dict is a CACHE of the durable lifecycle, not
    the truth:

    - plan decisions read ``agent_plans.status``/``version`` FIRST; the planner's
      dict is refreshed FROM the store before any decision is acted on;
    - approve/reject are compare-and-swap operations on migration 0013's version
      column — exactly one concurrent decision across ALL replicas flips version
      0 → 1, and only the winner emits/mutates memory (losers converge);
    - TERMINAL runs are evicted from RAM: their state replays deterministically
      from the event log on the next access, so no replica can resurrect a
      finished run from stale cache.
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
        engine_runtime: EngineRuntime | None = None,
        context_transport=None,
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
        # M13-H1b: staleness threshold for the zombie reaper. Default 90s (9
        # missed beats at the 10s heartbeat interval); HEARTBEAT_TIMEOUT_SECONDS
        # env var overrides per deployment (resolved at construction so a
        # restart re-reads the deployment's current value).
        self.heartbeat_timeout_seconds = resolve_heartbeat_timeout_seconds()
        # W8 slice 2: minimum age of a per-run network before the boundary
        # sweeper may judge it. It is NOT the safety argument (only runs the
        # durable lifecycle calls terminal are ever touched) — it is protection
        # for the window between a peer creating a boundary and that peer's run
        # row being readable. Env-tuned per deployment like the heartbeat knob.
        self.boundary_sweep_grace_seconds = float(
            os.environ.get("BOUNDARY_SWEEP_GRACE_SECONDS", "300") or 300
        )

        # M17-B5 (PBC1 F2): engine calls leave the inherited default thread
        # pool for a DEDICATED bounded executor with a hard per-run deadline
        # and a hung-provider circuit breaker (see engine_runtime.py). One
        # runtime per service; tests may inject their own via engine_runtime=.
        self.engine_runtime = engine_runtime or EngineRuntime()

        # M21-X2: optional httpx transport for the @refs resolver's calls to
        # Section 11's frozen search API. None = real sockets (production);
        # tests inject httpx.MockTransport to serve frozen-shape responses
        # without touching sibling code (isolation law) or ports.
        self.context_transport = context_transport

        # M19-N1: terminal runs are evicted from this cache — the durable
        # lifecycle (agent_events/agent_plans/agent_runs) is the source of
        # truth and replays deterministically, so RAM holds only live runs.
        self.runs: dict[str, dict[str, Any]] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        self._fence_tasks: set[asyncio.Task] = set()  # claim-lost fences in flight
        # run_id -> monotonic deadline: another replica's worker held this run when it was
        # stopped/paused here. We cannot observe that process, so the run reads
        # stopping/pausing (never stopped/paused) until its heartbeat has had time to
        # notice the released claim and fence its own engine.
        self._foreign_engine_until: dict[str, float] = {}
        # Wave 6 (D3): per-run publish chains. Fire-and-forget tasks raced under
        # fast successive actions — execution_started once landed BEFORE the
        # plan_approved that caused it (live smoke evidence). Each run's publishes
        # now await the previous one, so bus seq order matches causal order.
        self._publish_chains: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ events

    async def _guard_stopped(
        self,
        run_id: str,
        run_data: dict[str, Any],
        sm: Any,
        reason: str,
        guard: LoopGuard,
        set_shell_policy: Any,
        shell_policy: dict[str, Any] | None,
    ) -> None:
        """Stop a run the event stream says is stuck, and say so durably.

        W3 closed the gap this docstring used to admit: the stop now reaches the
        worker. `_kill_engine` was measured killing a confined worker and its
        relay in under a second (`docs/week-3-plan.md`, `w3_stop_spike.py`), so a
        guarded run no longer keeps burning until the wall clock. Four things
        happen, in this order:

        * the stop is durable and visible immediately, as ``ENGINE_FAILED`` —
          the same event the deadline path uses to fail one run honestly;
        * every further shell call is refused, by writing the budget the engine's
          shell plugin reads down to zero rather than leaving it at the stale
          snapshot taken before the run started;
        * the worker is killed, and the kill's own answer is recorded: an
          unconfirmed kill must not read as a confirmed one in the event text;
        * ``_guard_stopped`` is set so the late result is discarded instead of
          letting a run that was told to stop come back and mark itself done.

        Idempotent on purpose: the live forwarder and the post-run drain can both
        reach this coroutine before either has set the flag (each awaits an emit
        first), and a second failure event for one stop says nothing new.
        """
        if run_data.get("_guard_stopped"):
            return
        run_data["_guard_stopped"] = reason
        detail = f"{reason} after {guard.calls_seen} tool calls"
        if set_shell_policy is not None and shell_policy is not None:
            set_shell_policy(run_id, {**shell_policy, "maxToolCalls": 0})
        killed = await self._kill_engine(run_id)
        run_data["_guard_killed"] = killed
        sm.execution_failed(
            detail if killed else f"{detail} (worker kill could not be confirmed)"
        )

    # ------------------------------------------------------- W3 run checkpoints

    def _checkpoint_workspace(self, run_id: str) -> str | None:
        """The run's own workspace, when the engine can name one.

        Capability-probed: a stub engine in a test has no workspace, and a run
        must not fail because it cannot be checkpointed.
        """
        workspace_of = getattr(self.engine, "run_workspace", None)
        if workspace_of is None:
            return None
        try:
            return str(workspace_of(run_id))
        except Exception:  # noqa: BLE001 — an unusable workspace is not a run failure
            logger.warning("no workspace for checkpoint of %s", run_id, exc_info=True)
            return None

    async def _checkpoint_run(self, run_id: str, phase: str) -> None:
        """Best-effort snapshot. A failure is logged, never raised into the run.

        Not raising is deliberate and it is not silent: a run with no checkpoints
        answers "this run has no checkpoints" on rewind, which is true. Failing
        the engine because a snapshot did not fit would trade the work for the
        undo.
        """
        workspace = self._checkpoint_workspace(run_id)
        if workspace is None:
            return
        try:
            # The attempt number comes from what the STORE already holds, not from
            # an in-RAM counter: a resumed run is a fresh run_data, so a counter
            # restarts at 1 and labels collide ("before attempt 1" twice), which
            # makes the history unreadable exactly when someone is trying to
            # reconstruct an interrupted attempt.
            existing = await asyncio.to_thread(
                run_checkpoints.list_snapshots, workspace, run_id
            )
            label = f"{phase} attempt {len(existing) // 2 + 1}"
            snap = await asyncio.to_thread(
                run_checkpoints.snapshot, workspace, run_id, label
            )
            logger.info("checkpoint %s for %s (%d files)", snap["id"], run_id, len(snap["files"]))
        except Exception:  # noqa: BLE001
            logger.warning("checkpoint %r for %s failed", label, run_id, exc_info=True)

    async def list_run_checkpoints(self, run_id: str) -> dict[str, Any]:
        workspace = self._checkpoint_workspace(run_id)
        if workspace is None:
            return {"runId": run_id, "items": []}
        items = await asyncio.to_thread(run_checkpoints.list_snapshots, workspace, run_id)
        return {"runId": run_id, "items": items}

    async def rewind_run(self, run_id: str, checkpoint_id: str | None = None) -> dict[str, Any]:
        """Restore the run's workspace to a checkpoint.

        Refused while the run is live: resetting files under a worker that is
        still writing would race the agent and could restore a tree the agent
        never saw. Stop or pause first — that is what makes this the inverse of
        an attempt rather than a surprise mid-step.
        """
        run_data = await self._ensure_run(run_id)
        task = run_data.get("task")
        if task is not None and not task.done():
            raise InvalidStateTransitionError(
                "rewind requires the run to be stopped or paused first"
            )
        workspace = self._checkpoint_workspace(run_id)
        if workspace is None:
            raise run_checkpoints.CheckpointError("this run has no workspace to rewind")
        return await asyncio.to_thread(
            run_checkpoints.rewind, workspace, run_id, checkpoint_id
        )

    @staticmethod
    def _executor_context_prefix(run_data: dict[str, Any]) -> str:
        """Project rules, reviewed skills, project memory, then the curated
        @ref bundle, then whatever calls it.

        All blocks are assembled while planning and were stored "for engine
        prompt assembly" that never happened for rules: the executor received
        ``plan.goal + guidance`` and nothing else. They stay advisory — an absent
        or blank block contributes nothing rather than an empty heading, so a run
        with neither produces exactly the prompt it always did.
        """
        parts: list[str] = []
        rules = str(run_data.get("rules_prompt_block") or "").strip()
        if rules:
            parts.append(rules)
        skills = str(run_data.get("skills_prompt_block") or "").strip()
        if skills:
            parts.append(skills)
        memory = str(run_data.get("memory_prompt_block") or "").strip()
        if memory:
            parts.append(memory)
        bundle = run_data.get("context_bundle")
        rendered = str(bundle.get("rendered") or "").strip() if isinstance(bundle, dict) else ""
        if rendered:
            parts.append(rendered)
        return "\n\n".join(parts) + "\n\n" if parts else ""

    def _emit_tool_record(self, run_id: str, record: dict[str, Any]) -> None:
        """Bridge one engine tool record onto the project bus.

        Payloads follow the contract note on ``EventType.TOOL_CALL``. Both
        strings are scrubbed here rather than at the source: the arguments and
        output are model- and tool-authored text, and the transcript is durable.
        """
        key = getattr(self.engine, "api_key", "") or ""

        def _clean(value: Any, limit: int = 4000) -> str:
            return scrub_secrets(durable_event_text(value), key)[:limit]

        if record.get("phase") == "call":
            self._emit(
                EventType.TOOL_CALL,
                {
                    "runId": run_id,
                    "callId": str(record.get("callId") or ""),
                    "name": _clean(record.get("name"), 80),
                    "arguments": _clean(record.get("arguments")),
                    "turn": record.get("turn"),
                    "step": record.get("step"),
                    "startedAt": record.get("at"),
                },
            )
            return
        self._emit(
            EventType.TOOL_RESULT,
            {
                "runId": run_id,
                "callId": str(record.get("callId") or ""),
                "ok": bool(record.get("ok")),
                "error": _clean(record.get("error"), 500) or None,
                "output": _clean(record.get("output"), 2000),
                "turn": record.get("turn"),
                "step": record.get("step"),
                "finishedAt": record.get("at"),
            },
        )

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
        problem = _RUN_PROBLEMS.get(str(getattr(event_type, "value", event_type)))
        if problem is not None and self.session_factory is not None:
            ntask = loop.create_task(self._notify_run_problem(run_id, *problem))
            self._bg_tasks.add(ntask)
            ntask.add_done_callback(self._bg_tasks.discard)

    async def _notify_run_problem(self, run_id: str, kind: str, code: str) -> None:
        """Part 47: tell the run's leader and participants (not observers) in-app. Own session:
        the event is not inside a request transaction. Never raises into the run."""
        from sqlalchemy import text

        from harness_orchestrator import notify

        try:
            async with self.session_factory() as db:
                rows = (
                    await db.execute(
                        text(
                            "SELECT user_id, team_id FROM run_participants"
                            " WHERE run_id = :r AND role IN ('leader', 'participant')"
                        ),
                        {"r": run_id},
                    )
                ).all()
                for r in rows:
                    await notify.emit(
                        db, team_id=r.team_id, user_id=r.user_id, kind=kind, source_kind="run",
                        source_id=run_id, run_id=run_id, summary_code=code,
                    )
                await db.commit()
        except Exception:  # noqa: BLE001 - a notification must never disturb a run
            logger.warning("run notification failed for %s", run_id, exc_info=True)

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
        """Wave 9 node B / M18-C2 / M22-M2: refuse to spend before a model call.

        Gate order is TIER first, BUDGET second (M22-M2): a tier denial is a
        VOLUME cap — the structured ``upgrade`` CTA rides the ENGINE_FAILED
        payload so clients render an upgrade action, not a dead end. A budget
        denial is SPEND and keeps the M18-C2 semantics below.

        Returns True when a gate blocks the run OR the money store is
        unreachable — both block honestly via ENGINE_FAILED. The M18-C2
        failure posture stays ONE and FAIL-CLOSED: a DB error can no longer
        degrade to "allowed"; the denial carries ``status.degraded=True`` and
        this gate (1) labels the reason so operators see an outage-shaped
        denial, and (2) records the flag on run_data for the run-aggregate
        API response.
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
            estimated_call_cost_usd=(float(plan.estimatedCostUsd) if plan is not None else None),
        )
        if status.allowed:
            daily_status = await reserve_daily_run(
                factory, team_id, str(run_data.get("runId") or "")
            )
            if not daily_status.allowed:
                run_data["budget_degraded"] = bool(daily_status.degraded)
                reason = (
                    f"Daily run reservation degraded (fail-closed): {daily_status.reason}"
                    if daily_status.degraded
                    else f"Plan limit reached: {daily_status.reason}"
                )
                run_data["upgrade"] = (
                    dict(daily_status.capHit["upgrade"])
                    if daily_status.capHit and daily_status.capHit.get("upgrade")
                    else None
                )
                run_data["state_machine"].execution_failed(reason)
                if not daily_status.degraded:
                    await deliver_budget_warning(factory, team_id, "plan_limit_reached")
                return True
            run_data.pop("budget_degraded", None)
            # M23-B (session-2 area 9): allowed but near the cap — surface
            # the warning once per run (log + aggregate flag), never a block.
            if self._note_budget_warning(run_data, status):
                await deliver_budget_warning(factory, team_id)
            return False
        if status.degraded:
            # Operator-visible degradation: the money store was down, the
            # spend was denied BECAUSE of that, not because a cap was hit.
            run_data["budget_degraded"] = True
            reason = f"Budget gate degraded (fail-closed): {status.reason}"
        else:
            run_data.pop("budget_degraded", None)
            reason = f"Budget cap reached: {status.reason}"
            await deliver_budget_warning(factory, team_id, "budget_exhausted")
        logger.warning("blocking run %s pre-call: %s", run_data.get("runId"), reason)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.execution_failed(reason)
        return True

    def _note_budget_warning(self, run_data: dict[str, Any], status: BudgetStatus) -> bool:
        """M23-B: log + flag the FIRST near-cap decision of this run; True when it was this one,
        so the caller delivers it (part 57.005: ``deliver_budget_warning``, once per month).

        Dedupe is per run (in-process, like the other run_data flags): the
        warning is an operator alert, not a ledger — repeating it on every
        model call would be noise, and the aggregate API keeps the fact.
        """
        if not status.warned or run_data.get("budget_warned"):
            return False
        run_data["budget_warned"] = True
        logger.warning(
            "budget warning for run %s: projected spend crossed $%.4f (%.0f%% of the $%.2f cap)",
            run_data.get("runId"),
            status.warnAtUsd or 0.0,
            (100.0 * (status.warnAtUsd or 0.0) / status.capUsd) if status.capUsd else 0.0,
            status.capUsd or 0.0,
        )
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
        cached = self.runs.get(run_id)
        if cached is not None:
            task = cached.get("task")
            # Part 54 (session 12): this replica's copy is authoritative only while it drives the
            # run (a live local task) or still has events of its own to publish. Otherwise another
            # replica may have advanced the run (its queue worker claimed it), so a log that moved
            # past what this copy replayed means: drop it and replay the durable truth.
            idle_here = (task is None or task.done()) and run_id not in self._publish_chains
            if idle_here and (cached.get("_stale") or await self._log_moved_past(run_id, cached)):
                # fenced: replay the durable truth instead. Only drop the copy we checked: a
                # concurrent read may already have replaced it during the await above.
                if self.runs.get(run_id) is cached:
                    del self.runs[run_id]
        if run_id in self.runs:
            return self.runs[run_id]

        sm = AgentStateMachine(run_id=run_id, on_event=self._emit)
        run_data: dict[str, Any] = {
            "runId": run_id,
            "state_machine": sm,
            # M22-M2: structured upgrade CTA when a tier cap blocked this run
            # (set by _budget_gate; None until a cap actually bites).
            "upgrade": None,
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
        # M19-N1: a replay that lands on a TERMINAL state leaves the run
        # cache-only — its truth lives in the event log, so RAM must NOT
        # resurrect it (the old code cached every rehydrated run forever).
        if not await self._evict_if_terminal(run_data):
            self.runs[run_id] = run_data
        return run_data

    async def _log_moved_past(self, run_id: str, run_data: dict[str, Any]) -> bool:
        """True when the run's event log has a newer event than this copy last replayed.
        One indexed max() per read of a run this replica is not driving; False without a store."""
        if self.session_factory is None:
            return False
        from sqlalchemy import text

        try:
            async with self.session_factory() as db:
                newest = (
                    await db.execute(
                        text("SELECT max(seq) FROM agent_events WHERE run_id = :r"), {"r": run_id}
                    )
                ).scalar()
        except Exception:  # noqa: BLE001 — no table / no DB: keep the cached copy
            return False
        return newest is not None and int(newest) > int(run_data.get("_seq") or 0)

    async def _rehydrate(self, run_id: str, sm: AgentStateMachine, rd: dict[str, Any]) -> bool:
        """Replay a run's durable history into a fresh machine.

        M19-N1 durable-first ordering: the ``agent_plans`` mirror row decides
        plan/goal/approval when one exists (replica B sees replica A's
        decision); PLAN_APPROVED events then only CONFIRM what the store
        already said instead of being the sole source of approval. Terminal
        replays are left uncached by ``_ensure_run``.
        """
        if self.session_factory is None:
            return False
        from sqlalchemy import text

        try:
            async with self.session_factory() as db:
                rows = (await db.execute(text(_REHYDRATE_SQL), {"run_id": run_id})).mappings().all()
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
        rd["_seq"] = max((int(r["seq"]) for r in rows), default=0)  # see _log_moved_past

        if plan_row is not None:
            rd["plan_id"] = plan_row["id"]
            rd["goal"] = plan_row["goal"]
            plan = self._plan_from_row(plan_row)
            if plan is not None:
                self.planner.plans[plan_row["id"]] = plan
            # Durable decision FIRST: the mirror row outranks both the fresh
            # machine's default and this process's planner cache.
            sm.plan_approved = plan_row["status"] == "approved"
            self.planner.plan_approvals[plan_row["id"]] = sm.plan_approved

        last_seq_state = False
        done_gate = False
        stopped = False
        for row in rows:
            etype = row["event_type"]
            payload = row["payload"]
            if isinstance(payload, str):  # sqlite JSON columns come back as str
                try:
                    payload = json.loads(payload)
                except Exception:  # noqa: BLE001
                    payload = {}
            if sm.state in TERMINAL_STATES:
                # Section-B adversarial (replay terminal sealing): the live
                # machine accepts NO transition after DONE, so replay must
                # treat the rest of the log as inert. Legacy logs from
                # pre-H-008 revisions kept emitting after acceptance — a
                # trailing RESUMED resurrected the finished run straight into
                # EXECUTING here (adversarial test
                # tests/unit/test_replay_terminal_sealing.py). This is the
                # S1-R05c stop-tombstone law, generalized to every terminal
                # path. Breaks, not continues: nothing later can matter.
                break
            if stopped:
                # S1-R05c: stop is terminal — events logged after a stop
                # tombstone (old logs may even carry a post-stop done-signal)
                # must not resurrect or advance the run on replay.
                continue
            if (
                etype == EventType.PAUSED.value
                and isinstance(payload, dict)
                and payload.get("reason") == "user_requested_stop"
            ):
                # S1-R05c stop tombstone: the live path reached DONE in RAM
                # (finish() is silent), so the PAUSED(reason=stop) envelope is
                # the only durable witness. Replay maps it to DONE — without
                # this, a restarted process read the run as resumable PAUSED
                # and a later done-signal could drive it forward.
                sm.state = AgentState.DONE
                stopped = True
                rd["_stopped"] = True  # lifecycle view: DONE-by-stop, not DONE-by-review
                last_seq_state = True
                continue
            if etype == EventType.PLAN_APPROVED.value:
                # Confirmation only: the mirror row above already decided.
                approved_plan = (payload or {}).get("planId") if isinstance(payload, dict) else None
                if approved_plan:
                    if rd.get("plan_id") is None:
                        # NOT setdefault: _ensure_run pre-seeds plan_id=None,
                        # so the key always exists and the legacy-log fallback
                        # below would silently never fire.
                        rd["plan_id"] = approved_plan
                    if plan_row is None:
                        # No mirror row to consult (pre-mirror legacy row):
                        # the event itself is the only durable witness.
                        sm.plan_approved = True
                        self.planner.plan_approvals[approved_plan] = True
                    # M23-A: recover the approval instant from the payload
                    # (legacy pre-M23 events carry none; the start-path
                    # durable check then falls back to the row ts).
                    if isinstance(payload, dict) and sm.plan_approved_at is None:
                        sm.plan_approved_at = approved_at_from_wire(payload.get("approvedAt"))
                continue
            # M19-N1: replay the done-gate PATH like the live machine does
            # (H-008 semantics, previously missing from rehydration): a
            # REVIEW_PASSED behind an opened done-gate seals DONE — the old
            # flat map resurrected finished runs as eternal "executing".
            if etype == EventType.ENGINE_COMPLETED.value and sm.state is AgentState.EXECUTING:
                sm.state = AgentState.AWAITING_DONE_SIGNAL
                done_gate = True
                last_seq_state = True
                continue
            if etype == EventType.DONE_SIGNAL_RECEIVED.value:
                if sm.state is AgentState.AWAITING_DONE_SIGNAL:
                    done_gate = True
                sm.state = AgentState.REVIEWING
                last_seq_state = True
                continue
            if etype == EventType.REVIEW_PASSED.value:
                sm.state = AgentState.DONE if done_gate else AgentState.EXECUTING
                done_gate = False
                last_seq_state = True
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
            def _json_list(value: Any) -> list:
                # PostgreSQL drivers return JSON columns as Python lists, but
                # SQLite scratch/live databases expose the same column as a
                # JSON string. `list("[]")` produces two truthy characters,
                # which can falsely trigger the forbidden-path fail-closed
                # guard after a worker restart.
                if isinstance(value, str):
                    value = json.loads(value)
                return value if isinstance(value, list) else []

            return AgentPlan(
                goal=row["goal"],
                filesLikelyToChange=_json_list(row["files_likely_to_change"]),
                steps=_json_list(row["steps"]),
                tests=_json_list(row["tests"]),
                risks=_json_list(row["risks"]),
                estimatedCostUsd=float(row["estimated_cost_usd"] or 0),
                forbiddenPaths=_json_list(row["forbidden_paths"]),
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
        # M13-H4b: instant sourced from harness_contracts.timeutil.db_now()
        # (aware UTC by construction — see timeutil module law).
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
                        "created_at": db_now(),
                    },
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — mirror write must never break planning
            logger.exception("plan persistence failed for %s", plan_id)

    # ------------------------------------------------------------------- api

    async def propose_plan(
        self, run_id: str, goal: str | None = None, project_id: str | None = None
    ) -> str:
        run_data = await self._ensure_run(run_id, project_id=project_id)
        sm: AgentStateMachine = run_data["state_machine"]
        if goal:
            run_data["goal"] = goal
        if project_id:
            run_data["projectId"] = project_id

        sm.start_planning()
        # M12-G10: user-authored project rules are advisory prompt context.
        # Loaded best-effort (missing table/DB ⇒ ""), never blocks planning;
        # stored on the run so tests can assert the assembled prompt block
        # and future engine calls can carry it into system context.
        rules_text = await load_project_rules(self.session_factory, run_data.get("projectId"))
        run_data["rules_prompt_block"] = build_rules_block(rules_text)
        # M2-W12: reviewed team skills and project memory join project rules
        # as advisory prompt context. Loaded best-effort, never blocks
        # planning; stored on the run for the executor prompt assembly.
        team_id = await skills_loader.project_team_id(
            self.session_factory, run_data.get("projectId")
        )
        skills = await skills_loader.load_team_skills(self.session_factory, team_id)
        run_data["skills_prompt_block"] = skills_loader.build_skills_block(skills)
        memory = await memory_loader.load_project_memory(
            self.session_factory, run_data.get("projectId")
        )
        run_data["memory_prompt_block"] = memory_loader.build_memory_block(memory)
        # M21-X2: @refs in the brief text resolve (advisory, best-effort) into
        # a bounded context bundle that AUGMENTS the pasted goal. Resolution
        # is OFF unless CONTEXT_SEARCH_BASE_URL is set; any failure degrades
        # to planning exactly as before M21-X2. Resolved chunk paths seed the
        # plan's filesLikelyToChange; the rendered block rides on the run for
        # engine prompt assembly and test assertions.
        bundle = None
        ctx = context_settings_from_env()
        if ctx.base_url:
            bundle = await resolve_context(
                run_data["goal"],
                project_id=run_data.get("projectId"),
                base_url=ctx.base_url,
                token=ctx.token,
                transport=self.context_transport,
            )
            if bundle.chunks:
                run_data["context_bundle"] = {
                    "rendered": bundle.render(),
                    "maxTokens": bundle.max_tokens,
                    "droppedChunks": bundle.dropped_chunks,
                    "paths": seed_files_from_bundle(bundle),
                }
        seeded_files = seed_files_from_bundle(bundle) if bundle else []
        # Hand the SAME curated bundle to the RAG planner (M21-X4): it derives
        # steps citing path:line ranges. min_chunk_score is aligned to THIS
        # module's resolution floor so the planner gate confirms — not
        # re-litigates — resolution decisions (confirmed @file hits can carry
        # LOW cosine scores by design; see context_resolver).
        #
        # Capability-gated, not assumed: parallel-lane merge traffic can
        # revert planner.py independently of this file (M11/M12 ATLAS
        # hazard). On a pre-RAG planner, planning degrades to exactly the
        # legacy call instead of dying on unknown kwargs.
        rag_kwargs: dict[str, Any] = {}
        if bundle and bundle.chunks:
            params = inspect.signature(self.planner.create_plan).parameters
            if "context_bundle" in params:
                rag_kwargs["context_bundle"] = bundle
                rag_kwargs["min_chunk_score"] = DEFAULT_MIN_SCORE
        plan_id, plan = self.planner.create_plan(
            goal=run_data["goal"],
            files_likely_to_change=seeded_files or None,
            **rag_kwargs,
        )
        if seeded_files:
            logger.info("plan %s seeded %d file(s) from resolved @refs", plan_id, len(seeded_files))
        run_data["plan_id"] = plan_id
        await self._persist_plan(run_id, plan_id, plan, status="proposed")

        sm.plan_proposed(plan_id)
        return plan_id

    async def adopt_plan_version(self, run_id: str, content: dict[str, Any]) -> str:
        """Make a reviewed plan VERSION (part 44.002/003) the run's open plan.

        The engine only ever runs the planner's ``AgentPlan`` for ``run_data["plan_id"]``, so a
        version that was created or edited natively becomes executable by being registered here:
        a fresh legacy plan id, an ``agent_plans`` mirror row (so a restart rehydrates the SAME
        plan) and a new PLAN_PROPOSED event. The previous open plan is rejected in the mirror so it
        cannot be approved later. Deciding the returned id is the ordinary CAS-first
        :meth:`approve_plan`. Refused once a plan is approved or the run is past planning.
        """
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        if sm.plan_approved or sm.state not in (
            AgentState.IDLE,
            AgentState.PLANNING,
            AgentState.AWAITING_PLAN_APPROVAL,
        ):
            raise InvalidStateTransitionError(
                f"Run '{run_id}' ({sm.state}) has its plan decided; the plan can no longer change."
            )
        plan = AgentPlan(
            goal=content["goal"],
            filesLikelyToChange=list(content.get("filesLikelyToChange") or []),
            steps=list(content.get("steps") or []),
            tests=list(content.get("tests") or []),
            risks=list(content.get("risks") or []),
            estimatedCostUsd=float(content.get("estimatedCostUsd") or 0),
            forbiddenPaths=list(content.get("forbiddenPaths") or []),
            expectedResult=str(content.get("expectedResult") or ""),
        )
        previous = run_data.get("plan_id")
        plan_id = f"plan-{secrets.token_hex(4)}"
        self.planner.plans[plan_id] = plan
        self.planner.plan_approvals[plan_id] = False
        run_data["plan_id"] = plan_id
        await self._persist_plan(run_id, plan_id, plan, status="proposed")
        if previous and previous != plan_id:
            await self._persist_plan_status(run_id, previous, "rejected")
        sm.plan_proposed(plan_id)
        return plan_id

    async def _decide_plan(
        self,
        run_id: str,
        plan_id: str,
        principal_user_id: str,
        *,
        decision: str,
    ) -> None:
        """Shared CAS-first decision walk (M19-N1): durable compare-and-swap
        on migration 0013's version column BEFORE any memory mutation or
        event emission.

        Exactly one concurrent decision — across replicas, since the CAS lives
        in the shared store — flips the undecided version row. The WINNER
        emits the state-machine event and converges its cache; a LOSER raises
        :class:`PlanAlreadyDecidedError` with memory untouched, so replica B
        can never double-apply replica A's decision. Bare in-memory mode (no
        factory) keeps legacy behavior: the planner flip is the only witness.
        """
        run_data = await self._ensure_run(run_id)
        expected = run_data.get("plan_id")
        if plan_id != expected:
            raise KeyError(f"Plan '{plan_id}' does not belong to run '{run_id}'.")
        sm: AgentStateMachine = run_data["state_machine"]

        factory = self.session_factory
        if factory is None:
            # Bare unit mode: no durable mirror, no CAS token — legacy walk.
            flip = self.planner.approve_plan if decision == "approved" else self.planner.reject_plan
            announce = sm.approve_plan if decision == "approved" else sm.reject_plan
            flip(plan_id)
            announce(plan_id, principal_user_id)
            return

        seen = await self._plan_decision_row(plan_id)
        if seen is not None and seen["status"] != "proposed":
            # Converge the cache with the store BEFORE refusing: this replica
            # must not keep acting on a decision another replica overturned.
            self._refresh_plan_cache(plan_id, seen["status"])
            raise PlanAlreadyDecidedError(
                f"Plan '{plan_id}' was already decided "
                f"(durable status '{seen['status']}', version {seen['version']})."
            )
        expected_version = seen["version"] if seen is not None else 0
        new_version = await self._persist_plan_status(
            run_id, plan_id, decision, expected_version=expected_version
        )
        if new_version is None:
            # Mirror row absent (legacy silent-skip posture) or store trouble:
            # nothing durable raced us that we can see; keep the legacy
            # best-effort walk instead of failing an undecidable request.
            logger.warning(
                "plan %s decided without a durable CAS (mirror absent); continuing best-effort",
                plan_id,
            )

        flipped = (
            self.planner.approve_plan(plan_id)
            if decision == "approved"
            else self.planner.reject_plan(plan_id)
        )
        if new_version is not None and flipped is None:
            # The durable CAS moved but this cache already showed the same
            # decision: another path (or replica-convergence) got here first.
            logger.info(
                "plan %s CAS won at version %s but cache was pre-flipped; skipping emit",
                plan_id,
                new_version,
            )
            return

        if decision == "approved":
            sm.approve_plan(plan_id, principal_user_id)
        else:
            sm.reject_plan(plan_id, principal_user_id)

    async def approve_plan(self, run_id: str, plan_id: str, principal_user_id: str) -> None:
        await self._decide_plan(run_id, plan_id, principal_user_id, decision="approved")

    async def reject_plan(self, run_id: str, plan_id: str, principal_user_id: str) -> None:
        await self._decide_plan(run_id, plan_id, principal_user_id, decision="rejected")
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
        if self.session_factory is not None:
            try:
                await self._audit_plan_rejected(
                    run_id,
                    plan_id=plan_id,
                    actor_user_id=principal_user_id,
                    # M19-N1: _decide_plan no longer holds a run_data local;
                    # team resolution re-reads the durable ownership chain.
                    team_id=await self._team_for_run({"runId": run_id}) or "",
                )
            except Exception:  # noqa: BLE001 — audit must never break rejection
                logger.exception("plan.rejected audit emission failed for %s", run_id)

    async def _audit_plan_rejected(
        self,
        run_id: str,
        *,
        plan_id: str,
        actor_user_id: str,
        team_id: str,
    ) -> None:
        """Persist the plan.rejected audit row directly into the FROZEN
        ``audit_log`` table (M17-B3 inline of the former cross-section import
        of harness_identity.audit_emit.emit_plan_rejected — S1 helpers pull
        S1 ORM models, which sections may not import).

        Same contract as the helper it replaces: best-effort by construction
        is enforced by the CALLER (blanket except above); member resolution,
        secret scrubbing and the append-only action registry live in Section
        1 — this writer only fills the frozen columns it needs:

            audit_log(actor_member_id NULL, actor_kind, action='plan.rejected',
                      target_type='agent_run', target_id=run_id, detail JSONB)
        """
        detail = {"planId": plan_id}
        async with self.session_factory() as db:
            member_id = None
            if actor_user_id and team_id:
                row = (
                    await db.execute(
                        text(
                            "SELECT m.id FROM team_members m "
                            "WHERE m.user_id = :uid AND m.team_id = :tid LIMIT 1"
                        ),
                        {"uid": actor_user_id, "tid": team_id},
                    )
                ).first()
                member_id = row[0] if row else None
            await db.execute(
                text(
                    "INSERT INTO audit_log "
                    "(actor_member_id, actor_kind, action, target_type, target_id, detail) "
                    "VALUES (:actor_member_id, :actor_kind, :action, "
                    ":target_type, :target_id, :detail)"
                ),
                {
                    "actor_member_id": member_id,
                    "actor_kind": "agent",
                    "action": "plan.rejected",
                    "target_type": "agent_run",
                    "target_id": run_id,
                    "detail": json.dumps(detail),
                },
            )
            await db.commit()

    async def start_execution(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        plan_id = run_data.get("plan_id")
        # M19-N1: durable-first approval read — replica B must see replica A's
        # approval through the mirror row, not its own cache.
        if not plan_id or not await self._plan_is_approved(plan_id):
            raise PlanNotApprovedError(f"Cannot execute run '{run_id}': Plan is not approved.")

        # M19-N1 durable exactly-once fence: the shared event log decides
        # whether execution already began. Whichever replica answers a second
        # start request, the engine is driven at most once per lifecycle;
        # unwired stores answer False so bare unit mode keeps legacy behavior.
        if await self._execution_already_started(run_id):
            logger.info("run %s already started durably; ignoring re-start", run_id)
            return

        await enforce_run_start(self, run_id, run_data.get("projectId"))  # part 39.008
        from harness_orchestrator.org_execution_gate import enforce_organization_execution

        await enforce_organization_execution(self, run_id, run_data.get("projectId"))
        await enforce_session_run_start(self, run_id)  # part 40.005
        # Part 44.005: execution limits, abandoned runs. Imported here, not at module level
        # (execution.py imports api.py, which imports this module).
        from harness_orchestrator.execution import enforce_execution_start

        await enforce_execution_start(self, run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        # M23-A (session-2 area 16): the approval above is durable, but is it
        # still FRESH? The event log is the witness for approvals this
        # process never made (replica B / post-restart); a stale approval is
        # refused here — before any announcement or enqueue — so it can
        # never be replayed into spend.
        await self._assert_plan_approval_fresh(run_id, sm)
        # Durable proof above ⇒ the machine reflects it even when THIS process
        # lost the original decision race on another replica (M19-N1
        # convergence): the spawner must be able to announce the lifecycle.
        sm.plan_approved = True
        plan = self.planner.get_plan(plan_id)
        if self.run_queue_factory is not None and await queue_available(self.run_queue_factory):
            # M19-N1 exactly-once spawner election: the INSERT race elects
            # the ONE replica that may announce + queue this lifecycle. The
            # loser returns BEFORE any state mutation or EXECUTION_STARTED
            # emission, so the shared log carries exactly one announcement
            # no matter how many replicas answer the request. (The winner's
            # worker loop performs the actual claim + spawn.)
            elected = await enqueue_run(
                self.run_queue_factory, run_id, run_data.get("projectId") or ""
            )
            if not elected:
                logger.info(
                    "run %s already queued/claimed; this replica will not re-drive it",
                    run_id,
                )
                return
            sm.start_execution()
            return
        # Bare / scratch-store mode: this process owns the lifecycle
        # end-to-end (legacy direct-spawn semantics preserved).
        sm.start_execution()
        self._spawn_execution(run_data, plan)

    def _spawn_execution(self, run_data: dict[str, Any], plan: AgentPlan) -> None:
        """Start one live engine task. Shared by start_execution and resume_agent
        so a resumed run re-invokes the engine with the SAME session_id (= run_id);
        the dsh SDK continues that session instead of starting a fresh one."""
        run_data["cancel_requested"] = False
        run_data["_cancel_reason"] = None
        reset = getattr(self.engine, "reset_cancel", None)
        if reset is not None:
            reset(run_data["runId"])  # fresh start/resume clears a sticky kill flag
        run_data["task"] = asyncio.create_task(self._execute(run_data, plan))

    async def _team_model_for_run(self, run_id: str) -> str | None:
        """The team's saved primary model, or None for the deployment default.

        Storage trouble (missing table on an old DB, transient outage) must
        never block a run: it degrades to the configured engine model.
        """
        if self.session_factory is None:
            return None
        try:
            async with self.session_factory() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT m.primary_model FROM agent_runs r "
                            "JOIN projects p ON p.id = r.project_id "
                            "JOIN team_model_prefs m ON m.team_id = p.team_id "
                            "WHERE r.id = :rid"
                        ),
                        {"rid": run_id},
                    )
                ).first()
        except Exception:  # noqa: BLE001 — preference lookup is best-effort
            logger.warning("team model lookup failed for run %s", run_id, exc_info=True)
            return None
        return str(row[0]) if row and row[0] else None

    async def _execute(self, run_data: dict[str, Any], plan: AgentPlan) -> None:
        run_id = run_data["runId"]
        sm: AgentStateMachine = run_data["state_machine"]
        heartbeat_task = None
        if self.run_queue_factory is not None:

            async def _heartbeat() -> None:
                while True:
                    try:
                        owns_claim = await heartbeat_run(
                            self.run_queue_factory, run_id, self.worker_id
                        )
                    except Exception:  # noqa: BLE001 — heartbeat must not kill execution
                        logger.exception("heartbeat failed for run %s", run_id)
                    else:
                        if not owns_claim:
                            # Claim reaped/re-queued/re-claimed elsewhere: another worker may
                            # now own this run. Stop OUR engine (fence) so a reclaim can never
                            # run the same run twice at once.
                            # Own task: _execute's unwind cancels THIS heartbeat task.
                            fence = asyncio.create_task(self._fence_lost_claim(run_data, sm))
                            self._fence_tasks.add(fence)
                            fence.add_done_callback(self._fence_tasks.discard)
                            return
                        await self._stop_if_organization_disabled(run_data)
                    await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)

            heartbeat_task = asyncio.create_task(_heartbeat())
        elif self.session_factory is not None:

            async def _organization_watch() -> None:
                while True:
                    await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
                    await self._stop_if_organization_disabled(run_data)

            heartbeat_task = asyncio.create_task(_organization_watch())
        try:
            set_shell_policy = getattr(self.engine, "set_shell_policy", None)
            if getattr(plan, "forbiddenPaths", None) and set_shell_policy is None:
                # An engine whose shell is not mediated by Section 5's path
                # policy cannot honour forbidden paths: never treat plan
                # metadata or prompt text as a hard constraint, refuse first.
                # DeepSeekHarnessWrapper routes bash through the shell gate.
                reason = (
                    "execution refused: this runtime cannot enforce plan forbidden paths; "
                    "use an engine whose shell goes through the Section 5 gate"
                )
                run_data["engine_result"] = {
                    "sessionId": run_id,
                    "engine": "policy_blocked",
                    "status": "error",
                    "finalResponse": reason,
                }
                sm.execution_failed(reason)
                return
            if await self._budget_gate(run_data, plan):  # Wave 9 B: no model call over cap
                return
            if self.session_factory is not None:
                # Multiplayer: no engine call while a high-impact approval is
                # pending. BLOCKED is resumable once the leader decides.
                open_approval = await collab.pending_approval(self.session_factory, run_id)
                if open_approval:
                    reason = f"awaiting leader approval ({open_approval})"
                    run_data["engine_result"] = {
                        "sessionId": run_id,
                        "engine": "blocked",
                        "status": "blocked",
                        "finalResponse": reason,
                    }
                    sm.execution_failed(reason)
                    return
                extra, run_data["guidance_seq"] = await collab.guidance_since(
                    self.session_factory, run_id, run_data.get("guidance_seq", 0)
                )
            else:
                extra = ""
            try:
                await self._ensure_baseline(run_data)
                budget: int | None = None
                shell_policy: dict[str, Any] | None = None
                if set_shell_policy is not None:
                    # Part 52/44/42: the engine's bash tool enforces the plan's forbidden
                    # paths, the intent's scope paths and the run's remaining tool calls.
                    from harness_orchestrator.execution import remaining_tool_calls

                    factory = self.session_factory
                    budget = await remaining_tool_calls(factory, run_id) if factory else None
                    shell_policy = {
                        "forbiddenPaths": list(getattr(plan, "forbiddenPaths", None) or []),
                        "scopePaths": (
                            await collab.intent_scope_paths(factory, run_id) if factory else []
                        ),
                        "maxToolCalls": budget,
                    }
                    set_shell_policy(run_id, shell_policy)
                # M17-B5 (PBC1 F2): the dedicated bounded executor + hard
                # deadline replaces asyncio.to_thread's invisible ~16–36 slot
                # pool. A hung provider now fails THIS run at the deadline
                # instead of occupying a slot forever.
                #
                # W1: the engine runs on its own OS thread, so it cannot emit
                # onto the bus (no running loop there). It hands records to a
                # queue and this task forwards them, which is what makes the
                # transcript live instead of post-hoc. Capability is probed,
                # not assumed: a stub/custom engine without the keyword simply
                # gets no stream, and no forwarder is wasted on it.
                records: queue.Queue[dict[str, Any]] = queue.Queue()
                progress_kwargs: dict[str, Any] = {}
                forwarder: asyncio.Task[None] | None = None
                consume_record = None
                if accepts_keyword(getattr(self.engine, "execute_goal", None), "on_progress"):
                    # W2: the transcript stream is also the only place that sees
                    # every call as it happens. `budget` above is a snapshot taken
                    # before the run, from a table `record_engine_tool_calls` only
                    # populates after it, so nothing else in the stack knows a run
                    # has gone round the same hole 30 times.
                    guard = LoopGuard(max_calls=budget, repeat_limit=loop_repeat_limit())

                    def _progress(record: dict[str, Any]) -> None:
                        records.put(record)

                    async def _consume(record: dict[str, Any]) -> None:
                        self._emit_tool_record(run_id, record)
                        reason = guard.observe(record)
                        if reason and not run_data.get("_guard_stopped"):
                            await self._guard_stopped(
                                run_id,
                                run_data,
                                sm,
                                reason,
                                guard,
                                set_shell_policy,
                                shell_policy,
                            )

                    async def _forward_tool_records() -> None:
                        while True:
                            try:
                                record = records.get_nowait()
                            except queue.Empty:
                                await asyncio.sleep(0.05)
                                continue
                            await _consume(record)

                    consume_record = _consume
                    progress_kwargs["on_progress"] = _progress
                    forwarder = asyncio.create_task(_forward_tool_records())
                # M06: the goal may carry screenshots. Resolution reads only
                # artifacts this run owns and reports everything it declines;
                # an engine that cannot take images never has them resolved.
                run_model = await self._team_model_for_run(run_id)
                if accepts_keyword(getattr(self.engine, "execute_goal", None), "images"):
                    # teamId is this process's stamp and is not a durable
                    # column: a replica that never saw the bind, or a resume
                    # after a restart, resolves it the way the budget gate does.
                    team_id = run_data.get("teamId") or await self._team_for_run(run_data)
                    if run_data.get("_goal_images_sent"):
                        # The SDK session continues across a resume, so the
                        # image is already in its history. Re-sending it would
                        # attach the same screenshot twice to one conversation.
                        # Known limit, stated rather than papered over: this
                        # marker is per-process. A resume that lands on a
                        # DIFFERENT replica re-attaches, because nothing in the
                        # frozen schema records what a session already saw.
                        images = GoalImages(team_scoped=bool(team_id))
                    else:
                        images = await resolve_goal_images(
                            self.session_factory, run_id, team_id, run_model
                        )
                    if images.refused:
                        reason = f"execution refused: {images.refused}"
                        run_data["engine_result"] = {
                            "sessionId": run_id,
                            "engine": "image_policy",
                            "status": "error",
                            "finalResponse": reason,
                        }
                        sm.execution_failed(reason)
                        return
                    if images.blocks:
                        progress_kwargs["images"] = images.blocks
                        run_data["_goal_images_sent"] = True
                else:
                    images = None
                run_data["goalImages"] = images.summary() if images is not None else None
                if images is not None and (images.selected or images.skipped):
                    # Durable, because "the agent ignored my screenshot" is a
                    # claim about what happened, not about this process's
                    # memory: a replayed run must reproduce it.
                    self._emit(EventType.GOAL_IMAGES, {"runId": run_id, **images.summary()})
                # W3: a checkpoint BEFORE the engine touches the tree and one
                # after it settles. Two snapshots per attempt are what make
                # "rewind to the last checkpoint" and "undo this attempt"
                # different, useful choices instead of the same one.
                await self._checkpoint_run(run_id, "before")
                try:
                    result = await self.engine_runtime.run(
                        self.engine,
                        goal=self._executor_context_prefix(run_data) + plan.goal + extra,
                        session_id=run_id,
                        model=run_model,
                        **progress_kwargs,
                    )
                finally:
                    # Drain what the worker already produced before stopping;
                    # a record that arrived during teardown still belongs in
                    # the transcript — and still counts against the guard,
                    # because a loop that starts in the last second is the same
                    # loop. An engine without the progress keyword has no stream
                    # to judge, so the drain stays a plain emit.
                    while True:
                        try:
                            record = records.get_nowait()
                        except queue.Empty:
                            break
                        if consume_record is not None:
                            await consume_record(record)
                        else:
                            self._emit_tool_record(run_id, record)
                    # The tree as the attempt actually left it — including a
                    # half-written one when the worker was killed, which is
                    # exactly the state a rewind may need to undo.
                    await self._checkpoint_run(run_id, "after")
                    if forwarder is not None:
                        forwarder.cancel()
                if isinstance(result, dict) and images is not None and (
                    images.selected or images.skipped
                ):
                    # What the model actually saw, and what it did not: the
                    # run page shows this instead of letting a skipped
                    # screenshot read as if it had been understood.
                    result = {**result, "goalImages": images.summary()}
                context = result.get("context") if isinstance(result, dict) else None
                if isinstance(context, dict) and context.get("compactions"):
                    self._emit(
                        EventType.CONTEXT_COMPACTED,
                        {
                            "runId": run_id,
                            "compactions": context.get("compactions"),
                            "compactedTokens": context.get("compactedTokens"),
                            "contextWindow": context.get("contextWindow"),
                        },
                    )
                if extra:  # persist the cursor so a restart does not re-deliver
                    await collab.mark_guidance_delivered(
                        self.session_factory, run_id, run_data["guidance_seq"]
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
            except CircuitOpenError as exc:
                # Hung-provider circuit OPEN: fail fast BEFORE burning a
                # bounded slot behind a provider that is already timing out.
                logger.error("run %s refused by provider circuit breaker: %s", run_id, exc)
                reason = f"provider circuit open ({exc})"
                run_data["engine_result"] = {
                    "sessionId": run_id,
                    "engine": "circuit_open",
                    "status": "error",
                    "finalResponse": f"DeepSeek Harness run failed: {reason}",
                }
                sm.execution_failed(reason)
                return
            except EngineRunTimeout as exc:
                # Deadline expired: this run fails honestly (ENGINE_FAILED,
                # reason 'timeout: …') and its queue claim is released by the
                # finish path below. The timeout also counts toward the
                # provider's breaker streak. NOTE: the OS thread itself cannot
                # be killed — the deadline stops the RUN, not the thread;
                # the breaker is what stops the pile-up.
                logger.error("run %s hit its engine deadline: %s", run_id, exc)
                reason = f"timeout: {exc}"
                await self._stage_outputs(run_data)  # worker was killed by the runtime
                run_data["engine_result"] = {
                    "sessionId": run_id,
                    "engine": "timeout",
                    "status": "error",
                    "finalResponse": f"DeepSeek Harness run failed: {reason}",
                }
                sm.execution_failed(reason)
                return
            except Exception as exc:  # noqa: BLE001 — surface engine failures to the UI
                failed_msg = "DeepSeek Harness run failed: " + scrub_secrets(
                    str(exc), getattr(self.engine, "api_key", None)
                )
                run_data["engine_result"] = {
                    "sessionId": run_id,
                    "engine": "error",
                    "status": "error",
                    "finalResponse": failed_msg,
                }
                # Wave 6 (D4): honest failure lands on the bus and blocks the run
                # (no-op unless still EXECUTING — pause/stop already told their story).
                sm.execution_failed(failed_msg)
                return
            # Part 44: the calls happened whatever happens to the result, so they count first.
            await record_engine_tool_calls(self, run_data, result.get("toolCalls"))
            if run_data.get("cancel_requested"):
                return  # zombie result from a cancelled live call — discard
            if run_data.get("_guard_stopped"):
                # Same reasoning as the cancel above: the guard already failed
                # this run on the record. A worker that finished after being told
                # to stop must not overwrite that with a success it earned only
                # by wandering longer.
                return
            staging_error = await self._stage_outputs(run_data)
            if staging_error:
                run_data["engine_result"] = {
                    "sessionId": run_id,
                    "engine": "staging_failed",
                    "status": "error",
                    "finalResponse": staging_error,
                }
                sm.execution_failed(staging_error)
                return
            if run_data.get("staged"):
                result = {**result, "staged": run_data["staged"]}
            run_data["engine_result"] = result
            # Wave 6 (D4): engine settled while still EXECUTING → emit + advance.
            # Completed opens the human done-gate; any other status blocks honestly.
            summary = str(result.get("finalResponse") or "")
            status = str(result.get("status") or "")
            if status == "completed":
                sm.execution_completed(summary[:4000])
                # M14-W4 F1a: the run's ACTUAL cost lands in cost_ledger when
                # the engine completes (H-011 run-cost gap). Fire-and-forget —
                # metering must never break or delay the completion path.
                await record_run_cost(self, run_data, plan)
            else:
                sm.execution_failed(summary or f"engine status={status or 'unknown'}")
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
            if self.run_queue_factory is not None:
                await finish_run(self.run_queue_factory, run_id, worker_id=self.worker_id)

    async def _fence_lost_claim(self, run_data: dict[str, Any], sm: AgentStateMachine) -> None:
        """Our claim on this run is gone: stop our engine, leave the run to its new owner."""
        run_id = run_data["runId"]
        logger.warning("run %s: claim lost to another worker; fencing local engine", run_id)
        run_data["_cancel_reason"] = "claim_lost"
        domain_metrics.inc("harness_run_cancel_total", "requested")
        self._cancel_live_task(run_data)
        confirmed = await self._kill_engine(run_id)
        domain_metrics.inc("harness_run_cancel_total", "confirmed" if confirmed else "unconfirmed")
        # This replica's copy of the run is now stale (another worker or a stop/pause owns
        # the truth). Drop it on next access so reads replay the durable log instead of
        # showing a local EXECUTING that nobody is driving.
        run_data["_stale"] = True

    async def _stop_if_organization_disabled(self, run_data: dict[str, Any]) -> None:
        from fastapi import HTTPException

        from harness_orchestrator.org_execution_gate import check_organization_execution

        try:
            blocked = await check_organization_execution(
                self,
                run_data["runId"],
                run_data.get("projectId"),
                where="active-run",
            )
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            blocked = "control_unavailable"
        if blocked:
            run_data["_cancel_reason"] = (
                "organization_execution_disabled"
                if blocked != "control_unavailable"
                else "organization_control_unavailable"
            )
            await self.stop_agent(
                run_data["runId"], reason=str(run_data["_cancel_reason"])
            )

    async def get_engine_result(self, run_id: str) -> dict[str, Any]:
        run_data = await self._ensure_run(run_id)
        result = run_data.get("engine_result")
        if result is None:
            # M19-N1: terminal runs are evicted from RAM; their settled
            # outcome replays from the ENGINE_COMPLETED/ENGINE_FAILED event
            # payloads instead of vanishing behind the eviction.
            replayed = await self._load_engine_result(run_id)
            if replayed is not None:
                return replayed
        return result or {
            "status": "not_started",
            "finalResponse": None,
        }

    async def pause_agent(self, run_id: str) -> None:
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        run_data["_cancel_reason"] = "pause"
        try:
            sm.pause(reason="user_requested")
        except InvalidStateTransitionError:
            domain_metrics.inc("harness_run_cancel_total", "refused")
            raise  # pause after stop/finish is refused, never silently accepted
        self._cancel_live_task(run_data)
        domain_metrics.inc("harness_run_cancel_total", "requested")
        foreign = await self._release_claim_for_cancel(run_id)
        if not foreign and await self._kill_engine(run_id):
            domain_metrics.inc("harness_run_cancel_total", "confirmed")
            await self._stage_outputs(run_data)
        else:
            domain_metrics.inc("harness_run_cancel_total", "unconfirmed")

    async def resume_agent(self, run_id: str) -> None:
        """Resume a paused/blocked run by RE-SPAWNING the engine task.

        The pre-fix version only flipped the state machine to EXECUTING while no
        task existed — a state lie (Appendix C). Re-invoking execute_goal with
        the same run_id as session_id is the checkpoint continuation: dsh resumes
        its own session history rather than restarting the goal blind.

        M19-N1: approval is read durable-first, and a run whose DURABLE history
        already shows execution cannot be resurrected by a replica holding a
        stale cache — exactly-once outranks local memory.
        """
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
        if not plan_id or not await self._plan_is_approved(plan_id):
            raise PlanNotApprovedError(f"Cannot resume run '{run_id}': Plan is not approved.")
        if sm.state not in TERMINAL_STATES:  # a terminal resume is a no-op, never a 409
            await enforce_run_start(self, run_id, run_data.get("projectId"))  # part 39.008
        head = await self._durable_lifecycle_head(run_id)
        suspended_heads = {
            EventType.PAUSED.value,
            EventType.BLOCKED_AWAITING_CLARIFICATION.value,
        }
        if (
            head is None
            or head in suspended_heads
            or sm.state
            in (
                AgentState.PAUSED,
                AgentState.BLOCKED,
            )
        ):
            # M19-N1: the plan object itself is no longer needed here — the
            # queue path re-reads everything durable at claim time, and the
            # direct-spawn path re-reads it inside _spawn_execution callers.
            #
            # S1-R05d: drive sm.resume() whenever the run is (or RAM says it
            # is) suspended — including a durable head that ALREADY shows
            # execution started. Skipping it there (the old behavior) meant
            # no RESUMED envelope was persisted; the re-queued claim then
            # replayed PAUSED at dispatch, failed the EXECUTING gate, and
            # the resume was silently dropped.
            sm.resume()  # PAUSED/BLOCKED → EXECUTING (+ RESUMED event); no-op otherwise
            if sm.state is not AgentState.EXECUTING:
                return  # resume from IDLE/DONE/… is a no-op — nothing to resurrect
        else:
            logger.info("run %s already started durably; resume re-queues instead", run_id)
        if self.run_queue_factory is not None:
            queued = await enqueue_run(
                self.run_queue_factory, run_id, run_data.get("projectId") or ""
            )
            if not queued:
                logger.info("run %s already has a live claim; ignoring double resume", run_id)
                return
            return  # worker loop will claim and spawn
        self._spawn_execution(run_data, self.planner.get_plan(plan_id))

    async def _release_claim_for_cancel(self, run_id: str) -> bool:
        """Cancel is durable across replicas: drop the run's queue claim unless THIS worker
        is the one running it (its own unwind releases that). The owning worker's next
        heartbeat then sees the claim gone and fences its engine. True when another
        worker held a running claim, i.e. an engine this process cannot observe."""
        if self.run_queue_factory is None:
            return False
        try:
            async with self.run_queue_factory() as db:
                row = (
                    await db.execute(
                        text("SELECT state, worker_id FROM run_claims WHERE run_id = :r"),
                        {"r": run_id},
                    )
                ).first()
                if row is None or (row.state == "running" and row.worker_id == self.worker_id):
                    return False  # nothing queued, or ours: our own unwind releases it
                await db.execute(
                    text(
                        "DELETE FROM run_claims WHERE run_id = :r"
                        " AND NOT (state = 'running' AND worker_id = :w)"
                    ),
                    {"r": run_id, "w": self.worker_id},
                )
                await db.commit()
        except Exception:  # noqa: BLE001 - no queue table (bare/scratch store): nothing to release
            return False
        foreign = row.state == "running" and row.worker_id != self.worker_id
        if foreign:
            self._foreign_engine_until[run_id] = (
                time.monotonic() + 2 * _HEARTBEAT_INTERVAL_SECONDS + 5
            )
        return foreign

    async def stop_agent(self, run_id: str, *, reason: str = "user_requested_stop") -> bool:
        """Stop the run (terminal). True when THIS call stopped it; False when it
        was already finished (idempotent repeat) - callers audit only real stops."""
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        if sm.state in TERMINAL_STATES:
            domain_metrics.inc("harness_run_cancel_total", "noop")
            return False  # idempotent: a repeated stop on a finished run is a no-op, not a 500
        run_data["_cancel_reason"] = "stop"  # terminal: overwrites any earlier 'pause'
        run_data["_stopped"] = True
        sm.finish()
        domain_metrics.inc("harness_run_cancel_total", "requested")
        # M12-G7: the only transition that reaches DONE without a bus event
        # (finish() is deliberately silent). Emit a paused envelope so
        # notification consumers see a truthful "stopped by user, not an
        # engine failure". Best-effort: telemetry must never break stopping.
        self._emit_paused_event(run_data, reason=reason)
        self._cancel_live_task(run_data)
        foreign = await self._release_claim_for_cancel(run_id)
        if not foreign and await self._kill_engine(run_id):
            domain_metrics.inc("harness_run_cancel_total", "confirmed")
            await self._stage_outputs(run_data)
        else:
            domain_metrics.inc("harness_run_cancel_total", "unconfirmed")
        # M19-N1: DONE is terminal and fully replayable from the event log —
        # when the log corroborates it, the RAM entry dies with it (cache-only).
        await self._evict_if_terminal(run_data)
        return True

    def _emit_paused_event(self, run_data: dict[str, Any], reason: str) -> None:
        """M12-G7: fire-and-forget PAUSED emission for silent terminal paths.

        Uses EventType.PAUSED (frozen contracts) with a G7-specific reason;
        payload keys stay camelCase per api-conventions. Never raises.
        """
        try:
            self._emit(
                EventType.PAUSED,
                {"runId": run_data["runId"], "reason": reason},
            )
        except Exception:  # noqa: BLE001 — telemetry must never break stopping
            logger.exception("paused-event emission failed for %s", run_data.get("runId"))

    def _isolated_engine(self) -> bool:
        return bool(getattr(self.engine, "isolated", False)) and hasattr(
            self.engine, "run_workspace"
        )

    async def _ensure_baseline(self, run_data: dict[str, Any]) -> None:
        if not self._isolated_engine():
            return
        rid = run_data["runId"]
        provision = getattr(self.engine, "provision_workspace", self.engine.run_workspace)
        await asyncio.to_thread(provision, rid)
        await asyncio.to_thread(
            staging.ensure_baseline, rid, self.engine.run_workspace(rid), self.engine.workspace
        )

    async def _stage_outputs(self, run_data: dict[str, Any]) -> str | None:
        """B3: withhold high-impact changes and file leader approvals for them.

        Returns an error string when staging could not be trusted (callers fail
        the run closed), else None. Only call once the worker is confirmed dead.
        """
        if not self._isolated_engine():
            return None
        rid = run_data["runId"]
        try:
            report = await asyncio.to_thread(
                staging.stage, rid, self.engine.run_workspace(rid), self.engine.workspace
            )
            if report.groups and self.session_factory is not None:
                team = await self._team_for_run(run_data)
                if team:
                    await collab.ensure_staged_approvals(
                        self.session_factory, team, rid, report.groups
                    )
            run_data["staged"] = {
                "lowImpact": report.low_impact,
                "withheld": [
                    {"action": g.action, "paths": g.paths, "digest": g.digest}
                    for g in report.groups
                ],
            }
            return None
        except Exception as exc:  # noqa: BLE001 - fail closed on any staging doubt
            logger.exception("staging failed for run %s", rid)
            return f"staging failed: {exc}"

    async def _kill_engine(self, run_id: str) -> bool:
        """B4: kill the isolated worker's process group; True when confirmed gone
        (or when the engine has no killable worker, e.g. injected test engines)."""
        cancel = getattr(self.engine, "cancel", None)
        if cancel is None:
            return True
        return bool(await asyncio.to_thread(cancel, run_id))

    def engine_quiesced(self, run_id: str) -> bool:
        """True once no engine thread for this run can still mutate files.

        Stop marks the run DONE immediately (cancel *requested*); the thread
        may outlive it, so cancel is only *confirmed* when this returns True.
        """
        until = self._foreign_engine_until.get(run_id)
        if until is not None:
            if time.monotonic() < until:
                return False  # another replica's engine may still be winding down
            del self._foreign_engine_until[run_id]
        worker = getattr(self.engine, "is_worker_running", None)
        if worker is not None and getattr(self.engine, "isolated", False):
            return not worker(run_id)  # process truth: dead worker cannot mutate files
        return not self.engine_runtime.is_running(run_id)

    def _cancel_live_task(self, run_data: dict[str, Any]) -> None:
        task = run_data.get("task")
        if task is not None and not task.done():
            run_data["cancel_requested"] = True
            task.cancel()

    def shutdown(self) -> None:
        """M17-B5: release the dedicated engine executor (app-shutdown seam).

        Queued-but-unstarted engine calls are dropped; already-running ones
        cannot be interrupted (OS threads) — see EngineRuntime.shutdown.
        """
        self.engine_runtime.shutdown()
        procs = getattr(self.engine, "_procs", None)
        if procs:
            for sid in list(procs):
                self.engine.cancel(sid)  # never orphan confined workers on shutdown

    # ------------------------------------------------- M19-N1 durable reads

    async def _plan_decision_row(self, plan_id: str) -> dict[str, Any] | None:
        """Durable decision of record for a plan: ``{status, version}`` or None.

        Primary source is migration 0013's ``agent_plans`` row; the in-RAM
        planner dict is demoted to a cache. Returns None when no DB is wired,
        the mirror row is absent, or the store is unreadable — callers then
        fall back to the cache (bare unit mode keeps working unchanged).
        """
        factory = self.run_queue_factory or self.session_factory
        if factory is None or not plan_id:
            return None
        try:
            async with factory() as db:
                row = (await db.execute(text(_PLAN_DECISION_SQL), {"plan_id": plan_id})).first()
        except Exception:  # noqa: BLE001 — unreadable store degrades to cache
            return None
        if row is None:
            return None
        return {"status": row.status, "version": row.version}

    def _refresh_plan_cache(self, plan_id: str, status: str) -> None:
        """Converge the planner CACHE with the durable decision (never the
        reverse). Approved stores flip the planner True; any other durable
        status flips it False so a stale replica cannot keep executing on an
        approval another replica has already overturned."""
        was_approved = bool(self.planner.plan_approvals.get(plan_id))
        now_approved = status == "approved"
        self.planner.plan_approvals[plan_id] = now_approved
        if was_approved and not now_approved:
            logger.info(
                "plan %s converged to durable status '%s'; dropping cached approval",
                plan_id,
                status,
            )

    async def _plan_is_approved(self, plan_id: str) -> bool:
        """Durable-first approval check.

        The mirror row decides when one exists (replica B must see replica A's
        approval); only an absent/unreadable row falls back to the in-process
        cache, preserving bare-unit behavior. Side-effect-free apart from the
        cache convergence.
        """
        decision = await self._plan_decision_row(plan_id)
        if decision is not None:
            self._refresh_plan_cache(plan_id, decision["status"])
        return self.planner.is_approved(plan_id)

    async def _assert_plan_approval_fresh(self, run_id: str, sm: AgentStateMachine) -> None:
        """M23-A (session-2 area 16): refuse to start on a stale approval.

        Enforcement point is the START REQUEST (queue announce / bare spawn):
        the approval instant is the PLAN_APPROVED event's DB ``ts`` — the
        append-only log is the lifecycle of record, and it is the only
        witness for approvals made on another replica or before a restart.
        The in-memory machine's own timestamp (set by ``approve_plan``) is
        used when the store has none.

        Posture: evidence of staleness REFUSES (PlanNotApprovedError, the
        same 409 the unapproved case uses); an absent/unreadable timestamp
        (bare unit mode, scratch mirrors predating the ``ts`` column) stays
        lenient — enforcement must never invent an expiry out of a missing
        fixture column. A run already elected for a start keeps its claim:
        the TTL is evaluated at start time, and re-checking it mid-queue
        would only re-litigate a decision the API already answered.
        """
        approved_at = sm.plan_approved_at
        if approved_at is None:
            factory = self.run_queue_factory or self.session_factory
            if factory is None:
                return
            from sqlalchemy import text

            try:
                async with factory() as db:
                    row = (
                        await db.execute(
                            text(
                                "SELECT ts FROM agent_events"
                                " WHERE run_id = :r AND type = :t"
                                " ORDER BY seq DESC LIMIT 1"
                            ),
                            {"r": run_id, "t": EventType.PLAN_APPROVED.value},
                        )
                    ).first()
            except Exception:  # noqa: BLE001 — absent ts column / store: no evidence
                logger.debug(
                    "approval timestamp unreadable for run %s; TTL check skipped",
                    run_id,
                )
                return
            if row is None:
                return
            approved_at = approved_at_from_wire(row[0])
            sm.plan_approved_at = approved_at
        if approval_expired(approved_at):
            sm.plan_approved = False
            sm.plan_approved_at = None
            raise PlanNotApprovedError(
                f"Cannot execute run '{run_id}': plan approval expired (TTL "
                f"{plan_approval_ttl_seconds()}s). Re-approve the plan."
            )

    async def _durable_lifecycle_head(self, run_id: str) -> str | None:
        """Wire name of the run's latest non-rejection event, or None.

        The event log is the lifecycle of record: whatever it says happened
        LAST outranks this process's cache (which may be stale, or may belong
        to a replica that never saw the run at all).
        """
        factory = self.run_queue_factory or self.session_factory
        if factory is None:
            return None
        try:
            async with factory() as db:
                row = (
                    await db.execute(
                        text(_LIFECYCLE_HEAD_SQL),
                        {
                            "run_id": run_id,
                            "rejected_type": PLAN_REJECTED_EVENT,
                        },
                    )
                ).first()
        except Exception:  # noqa: BLE001 — unreadable store: no durable opinion
            return None
        return row.event_type if row is not None else None

    async def _execution_already_started(self, run_id: str) -> bool:
        """True when the DURABLE history shows execution already began.

        Gates start/resume across replicas: once ``execution_started`` (or any
        later lifecycle event) is persisted, a second start — from this
        process or another replica — must not re-drive the engine. Unknown /
        unwired stores answer False so legacy single-process tests behave
        exactly as before.
        """
        head = await self._durable_lifecycle_head(run_id)
        if head is None:
            return False
        started_order = {
            EventType.EXECUTION_STARTED.value: 0,
            EventType.PAUSED.value: 1,
            EventType.RESUMED.value: 2,
            EventType.BLOCKED_AWAITING_CLARIFICATION.value: 3,
            EventType.ENGINE_COMPLETED.value: 4,
            EventType.ENGINE_FAILED.value: 5,
            EventType.DONE_SIGNAL_RECEIVED.value: 6,
            EventType.REVIEW_PASSED.value: 7,
            EventType.REVIEW_FAILED.value: 8,
            EventType.PR_OPENED.value: 9,
        }
        return head in started_order

    async def _load_engine_result(self, run_id: str) -> dict[str, Any] | None:
        """Rebuild a terminal run's engine result from the event log.

        Terminal runs are evicted from RAM, but their result stays readable:
        the ENGINE_COMPLETED/ENGINE_FAILED payload carries what the engine
        returned (record_run_cost folds usage from it), so replay answers the
        same read the live dict used to. None when nothing is persisted.
        """
        factory = self.run_queue_factory or self.session_factory
        if factory is None:
            return None
        try:
            async with factory() as db:
                row = (
                    await db.execute(
                        text(_ENGINE_RESULT_FALLBACK_SQL),
                        {
                            "run_id": run_id,
                            "completed_type": EventType.ENGINE_COMPLETED.value,
                            "failed_type": EventType.ENGINE_FAILED.value,
                        },
                    )
                ).first()
        except Exception:  # noqa: BLE001 — fallback is best-effort by design
            return None
        if row is None:
            return None
        payload = row.payload
        if isinstance(payload, str):  # sqlite JSON columns come back as str
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {}
        if not isinstance(payload, dict):
            return None
        summary = str(payload.get("summary") or "")
        etype = str(payload.get("_etype") or "")
        if etype == EventType.ENGINE_FAILED.value or "reason" in payload:
            reason = str(payload.get("reason") or summary)
            return {
                "sessionId": run_id,
                "engine": "replayed",
                "status": "error",
                "finalResponse": f"DeepSeek Harness run failed: {reason}",
            }
        return {
            "sessionId": run_id,
            "engine": "replayed",
            "status": "completed",
            "finalResponse": summary,
        }

    #: How long a claim may run ahead of its run's EXECUTION_STARTED before it is judged bogus.
    ANNOUNCE_GRACE_SECONDS = 30.0

    async def _announcement_in_flight(self, run_id: str, durable: AgentState | None) -> bool:
        """True when a claimed run is approved but not yet announced and its newest event is
        recent: the starter's EXECUTION_STARTED is probably still being written. A run that is
        terminal, paused, blocked or quiet for ANNOUNCE_GRACE_SECONDS is not in flight."""
        if durable not in (None, AgentState.IDLE, AgentState.AWAITING_PLAN_APPROVAL):
            return False
        factory = self.run_queue_factory or self.session_factory
        try:
            async with factory() as db:
                newest = (
                    await db.execute(
                        text("SELECT max(ts) FROM agent_events WHERE run_id = :r"), {"r": run_id}
                    )
                ).scalar()
        except Exception:  # noqa: BLE001 — unreadable store: fall back to the old verdict
            return False
        if newest is None:
            return False
        if isinstance(newest, str):
            newest = datetime.fromisoformat(newest)
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        return (datetime.now(UTC) - newest).total_seconds() < self.ANNOUNCE_GRACE_SECONDS

    async def _replayed_durable_state(self, run_id: str | None) -> AgentState | None:
        """Replay ONLY the event log (no memory) into a lifecycle state.

        M19-N1 eviction safety: DONE is a property of the PATH taken (the
        done-gate flag is not an event), so the replay emulates the machine's
        transition rules — ENGINE_COMPLETED opens the gate from EXECUTING, a
        REVIEW_PASSED behind an open gate seals DONE, otherwise the legacy
        loop continues. Returns None when nothing usable is persisted, in
        which case eviction must NOT happen: RAM would be the only remaining
        witness and dropping the entry would destroy the run's truth.
        """
        factory = self.run_queue_factory or self.session_factory
        if factory is None:
            return None
        try:
            async with factory() as db:
                rows = (await db.execute(text(_REHYDRATE_SQL), {"run_id": run_id})).mappings().all()
        except Exception:  # noqa: BLE001 — unreadable store: keep the cache
            return None
        state: AgentState | None = None
        done_gate = False
        for row in rows:
            if state is AgentState.DONE:
                # Section-B adversarial: same terminal seal as _rehydrate —
                # legacy post-terminal rows must not unseal the verdict, or
                # eviction (and every later eviction retry) refuses forever.
                break
            etype = row["event_type"]
            if etype == EventType.EXECUTION_STARTED.value:
                state = AgentState.EXECUTING
            elif etype == EventType.PAUSED.value:
                if state is AgentState.EXECUTING:
                    state = AgentState.PAUSED
            elif etype == EventType.RESUMED.value:
                state = AgentState.EXECUTING
            elif etype == EventType.BLOCKED_AWAITING_CLARIFICATION.value:
                state = AgentState.BLOCKED
            elif etype == EventType.ENGINE_COMPLETED.value:
                if state is AgentState.EXECUTING:
                    state = AgentState.AWAITING_DONE_SIGNAL
                    done_gate = True
            elif etype == EventType.ENGINE_FAILED.value:
                if state is AgentState.EXECUTING:
                    state = AgentState.BLOCKED
            elif etype == EventType.DONE_SIGNAL_RECEIVED.value:
                if state is AgentState.AWAITING_DONE_SIGNAL:
                    done_gate = True
                state = AgentState.REVIEWING
            elif etype == EventType.REVIEW_PASSED.value:
                state = AgentState.DONE if done_gate else AgentState.EXECUTING
                done_gate = False
            elif etype == EventType.REVIEW_FAILED.value:
                state = AgentState.BLOCKED
            elif etype == EventType.PR_OPENED.value:
                state = AgentState.PR_OPENED
        return state

    async def _evict_if_terminal(self, rd: dict[str, Any]) -> bool:
        """Drop a TERMINAL run from the RAM cache; True when evicted.

        DONE seals a run (state_machine law): every later transition raises,
        so keeping its dict buys nothing but unbounded memory (PBC1 F1) and
        stale-cache risk across replicas. Eviction is CONDITIONAL on the
        durable event log replaying the same terminal verdict — a store that
        never persisted the lifecycle (test doubles, partially-wired deploys)
        keeps its RAM entry, because there the dict is the only witness. With
        a healthy bus the entry dies and every later read replays.
        """
        sm: AgentStateMachine | None = rd.get("state_machine")
        if sm is None or sm.state not in TERMINAL_STATES:
            return False
        run_id: str | None = rd.get("runId")
        await self._cleanup_terminal_scratch(run_id)  # terminal in any mode, DB or not
        if self.session_factory is None:
            return False
        # M19-N1 ordering law: this process's own announcements for the run
        # must be FLUSHED to the store before RAM lets go — otherwise the
        # log could miss the very event that justifies the eviction.
        pending = self._publish_chains.get(run_id or "")
        if pending is not None:
            try:
                await pending
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a failed publish must not wedge the run in RAM forever
                logger.exception("publish chain drained with error before evicting %s", run_id)
        if await self._replayed_durable_state(run_id) is not AgentState.DONE:
            return False
        if run_id is not None and self.runs.get(run_id) is rd:
            del self.runs[run_id]
            logger.info("evicted terminal run %s from RAM cache", run_id)
        return True

    async def _cleanup_terminal_scratch(self, run_id: str | None) -> None:
        """A terminal run's per-run scratch dir is dead weight: remove it once the engine
        is confirmed gone. Best-effort (retention is the backstop); never touches outputs."""
        cleanup = getattr(self.engine, "cleanup_scratch", None)
        if cleanup is None or not run_id or not self._isolated_engine():
            return
        if not self.engine_quiesced(run_id):
            return
        try:
            await asyncio.to_thread(cleanup, run_id)
        except Exception:  # noqa: BLE001 - cleanup must never fail a lifecycle transition
            logger.exception("scratch cleanup failed for run %s", run_id)

    async def _has_durable_events(self, run_id: str) -> bool:
        """True when the event log carries ANY history for this run (M19-N1).

        A terminal run evicted from RAM stays a real run: its ``agent_events``
        rows are durable existence even without an ``agent_runs`` anchor, so
        the no-oracle 404 must not fire on an evicted-but-real lifecycle.
        """
        factory = self.run_queue_factory or self.session_factory
        if factory is None:
            return False
        try:
            async with factory() as db:
                row = (
                    await db.execute(
                        text("SELECT 1 FROM agent_events WHERE run_id = :r LIMIT 1"),
                        {"r": run_id},
                    )
                ).first()
        except Exception:  # noqa: BLE001 — unreadable store: no durable witness
            return False
        return row is not None

    async def recover_orphans(self) -> list[str]:
        """Requeue claims whose worker went silent (crash detector)."""
        if self.run_queue_factory is None:
            return []
        return await reclaim_orphans(self.run_queue_factory, stale_seconds=self.stale_seconds)

    async def recover_boundaries(self) -> dict[str, Any]:
        """W8 slice 2: reclaim the Docker equipment a killed process left behind.

        ``ContainerBoundary.close()`` runs in a ``finally``, so exceptions,
        timeouts and cancelled runs all clean up. SIGKILL does not: the network,
        the relay and (because ``docker run`` detaches when its client dies) a
        *running* worker container survive, and nothing else ever removes them.
        W4 measured 17–20 such orphan networks on one dev stack until Docker
        answered "all predefined address pools have been fully subnetted" and NO
        run could start — a leak in the isolation machinery that eventually
        denies service to every run.

        Per network the sweep sees three states: held (a running claim, or a
        boundary this process still owns), settled (the durable ``agent_events``
        log shows the engine call ended), or unknown. Held is never touched.
        Settled is removed
        even if its worker container is still running - that is the detached
        client's corpse, and only the DB proves the run it belongs to finished.
        Unknown is removed only once nothing attached to it is running.

        This is why it runs where the truth is reachable: the orchestrator holds
        the DB and the claims, the worker daemon holds the Docker socket. When
        this process has no socket the sweep reports its own failure instead of
        reporting "no orphans".
        """
        from harness_orchestrator.container_runtime import (
            labelled_boundary_runs,
            sweep_orphan_boundaries,
        )

        held = await executing_run_ids(self.run_queue_factory or self.session_factory)
        if held is None:
            return {"skipped": "claim table unreadable", "removed": [], "kept": []}
        held = set(held)
        for boundary in getattr(self.engine, "live_boundary_run_ids", lambda: [])():
            held.add(boundary)
        candidates = set(labelled_boundary_runs().values())
        settled = await self._settled_run_ids(candidates - held)
        try:
            return sweep_orphan_boundaries(
                held, settled, older_than_seconds=self.boundary_sweep_grace_seconds
            )
        except OSError as exc:  # no Docker socket (bare test host): nothing to reclaim
            return {"skipped": f"docker unavailable: {exc}", "removed": [], "kept": []}

    async def _settled_run_ids(self, candidates: set[str]) -> set[str]:
        """Which of ``candidates`` the durable event log says are past execution.

        ``agent_runs.state`` is NOT the answer — the orchestrator does not write
        that column per transition, and the states the API reports (blocked,
        awaiting_done_signal, done) are replayed from ``agent_events``. So the
        settlement proof is the event stream itself, using the same ordering
        ``_run_started`` reads: any event at or after ENGINE_COMPLETED means the
        engine call is over and no worker can be using the boundary any more.
        """
        from sqlalchemy import bindparam, text

        if not candidates:
            return set()
        post_execution = {
            EventType.ENGINE_COMPLETED.value,
            EventType.ENGINE_FAILED.value,
            EventType.DONE_SIGNAL_RECEIVED.value,
            EventType.REVIEW_PASSED.value,
            EventType.REVIEW_FAILED.value,
            EventType.PR_OPENED.value,
        }
        try:
            async with self.session_factory() as db:
                rows = (
                    await db.execute(
                        text(
                            "SELECT DISTINCT run_id FROM agent_events "
                            "WHERE run_id IN :ids AND type IN :types"
                        ).bindparams(
                            bindparam("ids", expanding=True),
                            bindparam("types", expanding=True),
                        ),
                        {"ids": sorted(candidates), "types": sorted(post_execution)},
                    )
                ).fetchall()
        except Exception:  # noqa: BLE001 — unreadable lifecycle: nothing is provably finished
            return set()
        return {row[0] for row in rows}

    async def reap_zombies(self) -> list[str]:
        """M13-H1b zombie reaper: release timed-out running claims.

        A claim whose stored ``heartbeat_at`` is older than
        ``heartbeat_timeout_seconds`` **by the database's own clock** belongs
        to a dead worker. :func:`release_timed_out_claims` atomically selects
        and deletes those rows (the same terminal shape ``finish_run`` uses),
        and each released run's state machine is moved to BLOCKED with an
        honest ENGINE_FAILED emission carrying reason 'heartbeat_lost', so a
        human can see and re-drive it instead of the queue silently forgetting
        the work. Returns the reaped run_ids; best-effort per run (one bad
        claim must not stop the sweep).
        """
        if self.run_queue_factory is None:
            return []
        try:
            released_rows = await release_timed_out_claims(
                self.run_queue_factory, timeout_seconds=self.heartbeat_timeout_seconds
            )
        except Exception:  # noqa: BLE001 — no queue table: nothing to reap
            logger.info("run_claims unavailable; zombie reaper skipped")
            return []
        released: list[str] = []
        for row in released_rows:
            run_id = row["run_id"]
            try:
                # Materialize the run's machine (replaying its event history
                # when this process never saw it) so BLOCKED is real state,
                # not a fiction.
                try:
                    run_data = await self._ensure_run(run_id, project_id=row.get("project_id"))
                    sm: AgentStateMachine | None = run_data["state_machine"]
                except Exception:  # noqa: BLE001 — machine loss must not stop release
                    logger.exception("state machine unavailable for reaped run %s", run_id)
                    sm = None
                # The engine's mid-flight work was lost when its worker died:
                # say so honestly, once, regardless of what replay shows.
                self._emit(
                    EventType.ENGINE_FAILED,
                    {
                        "runId": run_id,
                        "reason": "heartbeat_lost",
                        "workerId": row.get("worker_id"),
                        "heartbeatTimeoutSeconds": self.heartbeat_timeout_seconds,
                    },
                )
                if sm is not None:
                    sm.state = AgentState.BLOCKED
                released.append(run_id)
            except Exception:  # noqa: BLE001 — one bad claim must not stop the sweep
                logger.exception("zombie reaper failed on run %s", run_id)
        return released

    def _is_heartbeat_stale(self, heartbeat_at: Any) -> bool:
        """True when a stored heartbeat exceeds this service's threshold.

        Application-side mirror of the DB-clock predicate the reaper sweep
        uses (which is authoritative); for judging a single already-fetched
        heartbeat value without another query. ``db_now`` keeps the instant
        sourced from the shared timeutil seam.
        """
        age = heartbeat_age_seconds(heartbeat_at, now=db_now())
        return age is not None and age >= self.heartbeat_timeout_seconds

    async def poll_once(self, max_claims: int | None = None) -> str | list[str] | None:
        """Claim queued run(s) and spawn their engine tasks.

        Legacy shape (``max_claims`` unset/falsy): claim ONE run, return its
        run_id or None — every existing caller and test keeps working. M20-T1
        batch shape (``max_claims=N > 1``, passed by RunQueueWorker from
        WORKER_CLAIM_BATCH): admit UP TO N runs this cycle and return every
        admitted run_id as a list (see :meth:`_poll_batch`).
        """
        if self.run_queue_factory is None:
            return None
        batch = int(max_claims) if max_claims else 1
        if batch > 1:
            return await self._poll_batch(max(batch, 1))
        claim = await claim_next_run(self.run_queue_factory, self.worker_id, self.max_per_team)
        if claim is None:
            return None
        run_id = claim["run_id"]
        if not await self._dispatch_claim(run_id, claim):
            return None
        return run_id

    async def _poll_batch(self, batch: int) -> list[str]:
        """M20-T1: admit up to ``batch`` claims in ONE poll cycle.

        The BASELINE wall was one admission per cycle (≈2 journeys/s at the
        shipped 0.5 s cadence). This loop drains instead: each iteration takes
        the oldest claimable row via the SAME atomic FIFO claim statement, and
        stops at the first empty claim — empty ⇒ queue drained OR every
        remaining team sits at its per-team running cap (a mid-batch stop is
        therefore normal, not an error). A failing claim or dispatch skips
        THAT run only (its row stays 'running', visible to the zombie reaper)
        and never loses the rest of the batch.
        """
        claimed: list[str] = []
        for _ in range(batch):
            try:
                claim = await claim_next_run(
                    self.run_queue_factory, self.worker_id, self.max_per_team
                )
            except Exception:  # noqa: BLE001 — a failed claim keeps earlier admissions
                logger.exception("batched run-queue claim failed")
                break
            if claim is None:
                break
            run_id = claim["run_id"]
            try:
                dispatched = await self._dispatch_claim(run_id, claim)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad claim must not lose the batch
                # Honest loss shape (identical to the legacy single-claim path
                # letting an exception reach the worker loop): the claim row
                # stays 'running' and visible until the zombie reaper releases
                # it — never silently forgotten.
                logger.exception("batched dispatch failed for run %s", run_id)
                continue
            if not dispatched:
                continue
            claimed.append(run_id)
        return claimed

    async def _dispatch_claim(self, run_id: str, claim: dict[str, Any]) -> bool:
        """Walk ONE claim through the unchanged pre-spawn gate chain.

        Extracted verbatim from the former single-claim poll_once body so
        batched admission dispatches each run individually through every gate:
        M19-N1 stale-cache repair (durable replay before dropping a claim),
        the live-task double-dispatch fence (executor-saturation safe), the
        durable-first plan approval read, the Wave 9 B budget pre-spawn gate,
        then _spawn_execution + terminal eviction. True ⇒ engine task spawned.
        """
        run_data = await self._ensure_run(run_id, project_id=claim.get("project_id"))
        sm: AgentStateMachine = run_data["state_machine"]
        if sm.state is not AgentState.EXECUTING:
            # M19-N1: a STALE cache must not cost a durable claim. Another
            # replica may have legitimately announced + enqueued this
            # lifecycle while our copy still said "awaiting approval" — sync
            # from the event log before deciding the claim is bogus.
            durable = await self._replayed_durable_state(run_id)
            if durable is AgentState.EXECUTING:
                sm.state = AgentState.EXECUTING
                sm.plan_approved = True
            elif await self._announcement_in_flight(run_id, durable):
                # Part 54: the starting replica inserts the claim (the exactly-once election)
                # BEFORE its EXECUTION_STARTED commits, so a fast worker can claim inside that
                # gap. Deleting the claim lost the run for good; give it back to the queue.
                await requeue_claim(self.run_queue_factory, run_id, self.worker_id)
                return False
            else:
                await finish_run(self.run_queue_factory, run_id)
                return False
        live = run_data.get("task")
        if live is not None and not live.done():
            return False
        # Part 39.008: a run queued before its workspace was archived must not start.
        if await check_run_start(
            self, run_id, run_data.get("projectId") or claim.get("project_id"), where="claim"
        ):
            await finish_run(self.run_queue_factory, run_id)
            return False
        from harness_orchestrator.org_execution_gate import check_organization_execution

        if await check_organization_execution(
            self, run_id, run_data.get("projectId") or claim.get("project_id"), where="claim"
        ):
            await finish_run(self.run_queue_factory, run_id)
            return False
        # Part 40.005: same for a run whose session was paused/archived while it waited.
        if await check_session_run_start(self, run_id, where="claim"):
            await finish_run(self.run_queue_factory, run_id)
            return False
        # Part 44.005: same for a run that hit an execution limit or was abandoned while it waited.
        from harness_orchestrator.execution import check_execution_start  # import cycle, see above

        if await check_execution_start(self, run_id, where="claim"):
            await finish_run(self.run_queue_factory, run_id)
            return False
        plan_id = run_data.get("plan_id")
        if not plan_id or not await self._plan_is_approved(plan_id):
            logger.error("claimed run %s lacks an approved plan; dropping claim", run_id)
            await finish_run(self.run_queue_factory, run_id)
            return False
        plan = self.planner.get_plan(plan_id)
        if await self._budget_gate(run_data, plan):  # Wave 9 B: never spawn over cap
            await finish_run(self.run_queue_factory, run_id)
            return False
        self._spawn_execution(run_data, plan)
        # M19-N1: a run that reached DONE inside its engine task must not sit
        # in the cache forever — poll cycles are the natural eviction seam.
        await self._evict_if_terminal(run_data)
        return True

    async def submit_human_edit(self, run_id: str, path: str, content: str) -> None:
        run_data = await self._ensure_run(run_id)
        run_data["human_edits"].append({"path": path, "content": content})
        sm: AgentStateMachine = run_data["state_machine"]
        # M12-G9 (first slice): mark this human-edit batch on the bus so the
        # checkpoint panel can correlate the pause with a snapshot pair. The
        # actual pre/post-edit git commits are taken where the files live —
        # S5's SandboxService.apply_edit brackets each sandbox edit batch with
        # ``checkpoint: pre-edit``/``post-edit`` commits. UNVERIFIED seam
        # note: engine-internal edit batches remain E1-owned this wave.
        self._emit(
            EventType.DIFF_PROGRESS,
            {
                "runId": run_id,
                "path": path,
                "checkpoint": {"kind": "human-edit", "source": "orchestrator"},
            },
        )
        run_data["_cancel_reason"] = "pause"  # human edit pauses, keeps run resumable
        sm.submit_human_edit(path=path)
        self._cancel_live_task(run_data)

    async def gate_done_signal(self, run_id: str, principal: Any, diff_text: str) -> None:
        """H-007: validate a done-signal BEFORE any transition or review spend.

        Centralizes the signals.py gate at the service seam so the FastAPI
        route and any internal caller enforce identical role/evidence/liveness
        semantics. Raises the typed DoneSignalRejectedError subclasses (the
        route maps them to 403/409/400).
        """
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        gate_done_signal(principal, sm.state, diff_text)
        await self._gate_staged_completion(run_id)

    async def _gate_staged_completion(self, run_id: str) -> None:
        if self.session_factory is not None and await collab.unapplied_staged_approval(
            self.session_factory, run_id
        ):
            raise StagedApprovalPendingError(
                "Resolve and apply staged changes before completing this run or opening a PR."
            )

    async def signal_done(self, run_id: str, diff_text: str = "") -> ReviewFindings:
        await self._gate_staged_completion(run_id)
        run_data = await self._ensure_run(run_id)
        sm: AgentStateMachine = run_data["state_machine"]
        sm.signal_done()
        self._cancel_live_task(run_data)

        # M19-N2 (F9): the diff BODY never rides the bus. Store it once,
        # content-addressed; the wire (REVIEW_* event payloads) carries a
        # small immutable reference that consumers resolve table-/store-side
        # via get_done_diff. Plain-text callers keep working unchanged.
        diff_ref = save_diff(diff_text)
        run_data["done_diff_ref"] = diff_ref

        # Wave 9 node B / M18-C2: the reviewer's model_router call is a real
        # spend — check the cap BEFORE invoking it. Over budget OR a degraded
        # (unreachable) budget store ⇒ no model call; the reviewer falls back
        # to deterministic heuristics and records why.
        budget_block: str | None = None
        factory = self.run_queue_factory or self.session_factory
        if factory is not None and self.reviewer.model_router is not None:
            team_id = await self._team_for_run(run_data)
            if team_id:
                status: BudgetStatus = await check_budget(factory, team_id)
                if not status.allowed:
                    budget_block = (
                        f"budget gate degraded (fail-closed): {status.reason}"
                        if status.degraded
                        else status.reason
                    )
                    if status.degraded:
                        run_data["budget_degraded"] = True
                    else:
                        await deliver_budget_warning(factory, team_id, "budget_exhausted")
                    logger.warning(
                        "reviewer model call withheld for run %s: %s", run_id, budget_block
                    )
                else:
                    # M23-B: the reviewer call is spend too — same warning
                    # seam as the pre-spawn gate, same once-per-run dedupe.
                    if self._note_budget_warning(run_data, status):
                        await deliver_budget_warning(factory, team_id)

        findings = self.reviewer.evaluate_changes(
            brief_summary=run_data["goal"],
            diff_text=diff_text,
            budget_blocked_reason=budget_block,
        )
        # M19-N2 (F9): stamp the stored reference onto the verdict payload
        # before it reaches the bus — REVIEW_* events carry WHERE the body
        # lives, never the 200KB-class body itself.
        verdict_payload = findings.model_dump(by_alias=True)
        verdict_payload["diffRef"] = diff_ref
        if findings.passed:
            sm.reviewer_passed(verdict_payload)
        else:
            sm.reviewer_failed(verdict_payload)
        # M19-N1: a verdict that seals DONE makes this run cache-only — the
        # event log now replays the whole lifecycle, including the result.
        await self._evict_if_terminal(run_data)

        return findings

    _REVIEW_DIFF_SQL = text(
        """
        SELECT payload
          FROM agent_events
         WHERE run_id = :run_id
           AND type IN ('review_passed', 'review_failed')
         ORDER BY seq DESC
         LIMIT 1
        """
    )

    async def get_done_diff(self, run_id: str) -> str:
        """Fetch-on-wake consumer path (M19-N2): resolve the run's stored
        done-signal body by its wire reference.

        Lookup order: the live run cache first, then the durable REVIEW_*
        event payload (the reference rides the bus, the body never does —
        so the log itself is a valid pointer source even after N1 evicts
        terminal runs from RAM). Raises KeyError for unknown runs / runs
        without a recorded signal, ValueError for dangling references.
        """
        run_data = await self._ensure_run(run_id)
        ref = run_data.get("done_diff_ref")
        if not ref:
            ref = await self._diff_ref_from_log(run_id)
        if not ref:
            raise KeyError(f"No stored diff evidence for run '{run_id}'.")
        try:
            return resolve_diff(ref)
        except ValueError as exc:
            raise ValueError(f"{exc} (run '{run_id}')") from exc

    async def _diff_ref_from_log(self, run_id: str) -> dict[str, Any] | None:
        """Latest durable REVIEW_* verdict's ``diffRef``, or None.

        Raw-SQL against the frozen table name (cross-section law); payload
        arrives as dict on Postgres JSONB and as TEXT on SQLite mirrors.
        """
        factory = self.run_queue_factory or self.session_factory
        if factory is None:
            return None
        try:
            async with factory() as db:
                row = (await db.execute(self._REVIEW_DIFF_SQL, {"run_id": run_id})).first()
        except Exception:  # noqa: BLE001 — unreadable store degrades to None
            return None
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
        if not isinstance(payload, dict):
            return None
        # The verdict rides nested under ``findings`` (state-machine wire
        # shape); older/direct payloads may carry it at top level.
        findings = payload.get("findings")
        ref = None
        if isinstance(findings, dict):
            ref = findings.get("diffRef")
        if not isinstance(ref, dict):
            ref = payload.get("diffRef")
        return ref if isinstance(ref, dict) else None

    async def get_plan(self, run_id: str) -> AgentPlan:
        run_data = await self._ensure_run(run_id)
        plan_id = run_data.get("plan_id")
        if not plan_id:
            raise KeyError(f"No plan proposed yet for run '{run_id}'.")
        return self.planner.get_plan(plan_id)

    async def get_status(self, run_id: str) -> dict[str, Any]:
        return await self._run_aggregate(run_id)

    # M14-W4 F3a (W4-C2): steps summary is capped at the LAST 100 events so a
    # long-lived run cannot bloat the aggregate payload unboundedly.
    _AGGREGATE_STEPS_MAX = 100

    _RUN_ENRICH_SQL = text(
        """
        SELECT r.pr_id AS pr_id,
               pr.pr_url AS pr_url,
               (SELECT SUM(cl.cost_usd)
                  FROM cost_ledger cl
                 WHERE cl.run_id = r.id) AS cost_usd
        FROM agent_runs r
        LEFT JOIN pull_requests pr ON pr.id = r.pr_id
        WHERE r.id = :r
        """
    )

    _STEPS_SQL = text(
        """
        SELECT seq, type, ts, payload
          FROM agent_events
         WHERE run_id = :run_id
         ORDER BY seq DESC
         LIMIT :cap
        """
    )

    @staticmethod
    def _step_wire(row: Any) -> dict[str, Any]:
        """One history step. ``reason`` tells a stop (`paused` + user_requested_stop)
        from a pause, and a failure's cause from a bare `engine_failed`."""
        step: dict[str, Any] = {"seq": int(row["seq"]), "type": str(row["type"]), "ts": row["ts"]}
        payload = row["payload"]
        if isinstance(payload, str):  # sqlite JSON columns come back as str
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = None
        reason = payload.get("reason") if isinstance(payload, dict) else None
        if isinstance(reason, str) and reason:
            step["reason"] = reason[:200]
        return step

    @staticmethod
    def _cost_wire(cost_usd: Any) -> float | None:
        """Decimal→wire number at EXACT scale-6 money (H-023 law).

        NUMERIC(12,6) arrives as Decimal; ``quantize`` pins the 6-dp exponent,
        then one float() conversion. The quantize guarantees the shortest repr
        round-trips to the same value, so JSON carries no binary-float drift.
        Returns None for an absent/NULL total (no ledger rows yet).
        """
        if cost_usd is None:
            return None
        if not isinstance(cost_usd, Decimal):
            cost_usd = Decimal(str(cost_usd))
        return float(cost_usd.quantize(Decimal("0.000001")))

    def _workspace_identity(self, run_id: str) -> dict[str, Any] | None:
        """Part 09.010: which workspace this run owns, without paths or secrets."""
        identify = getattr(self.engine, "workspace_identity", None)
        if identify is None or not self._isolated_engine():
            return None
        try:
            return identify(run_id)
        except ValueError:  # run id not usable as a workspace name: no workspace exists
            return None

    async def _run_aggregate(self, run_id: str) -> dict[str, Any]:
        """One aggregate read for a single run — W4-C2 shape.

        History: M12-W2 E4a added ``prId`` on top of get_status()'s base wire
        dict; M14-W4 F3a extends to the full pair-contract shape with
        ``costUsd``/``url`` (agent_runs ⟕ pull_requests ⟵ cost_ledger) and
        ``steps`` (ordered [{seq, type, ts}] from agent_events, capped at the
        last :data:`_AGGREGATE_STEPS_MAX`). Nullability is explicit per W4-C2:
        every enriched field is present and None when its source row/column
        does not exist yet. Read-only + failure-tolerant: any DB trouble
        degrades each enriched field to None rather than breaking the read.
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
            # ---- W4-C2 enrichment; explicit nulls until the source exists --
            "prId": None,
            "url": None,
            "costUsd": None,
            "steps": [],
            # M18-C2: explicit degraded=false — operators read the aggregate
            # and see at a glance that this run's budget decisions were made
            # against a healthy money store (flips true when a gate ran
            # fail-closed on a DB error).
            "budget": {
                "degraded": bool(run_data.get("budget_degraded")),
                # M23-B: true when any decision on this run was allowed but
                # crossed the warning threshold (see BudgetDegradedOut).
                "warned": bool(run_data.get("budget_warned")),
            },
            # M22-M2: structured upgrade CTA ({tier, cap, capField, used,
            # upgradeTo, cta}) when a TIER cap blocked this run; None = no
            # cap hit. The UI renders an upgrade action from it — never a
            # bare dead-end denial.
            "upgrade": run_data.get("upgrade"),
            # Part 10: honest lifecycle. `stopped`/`paused` appear only once the
            # engine is confirmed gone (process truth), else stopping/pausing.
            "workspace": self._workspace_identity(run_id),
            "lifecycle": lifecycle_view(
                sm.state,
                stop_requested=bool(run_data.get("_stopped")),
                engine_quiesced=self.engine_quiesced(run_id),
            ),
        }
        factory = self.session_factory
        if factory is None:
            return aggregate  # bare unit mode: no durable existence to consult
        try:
            async with factory() as db:
                enrich_row = (
                    (await db.execute(self._RUN_ENRICH_SQL, {"r": run_id})).mappings().first()
                )
                step_rows = (
                    (
                        await db.execute(
                            self._STEPS_SQL,
                            {"run_id": run_id, "cap": self._AGGREGATE_STEPS_MAX},
                        )
                    )
                    .mappings()
                    .all()
                )
            if enrich_row is None:
                # No agent_runs row: the run only exists if this process is
                # currently driving a lifecycle for it (bound project, plan,
                # or live task), replayed one into memory, or left durable
                # event history behind (M19-N1: terminal eviction removed the
                # RAM copy — the event log still witnesses existence).
                # Otherwise it is genuinely unknown → 404.
                in_memory = bool(
                    run_data.get("projectId")
                    or run_data.get("plan_id")
                    or (task is not None and not task.done())
                )
                if (
                    not in_memory
                    and not run_data.get("_rehydrated")
                    and not await self._has_durable_events(run_id)
                ):
                    raise RunNotFoundError(run_id)
            else:
                if enrich_row["pr_id"]:
                    aggregate["prId"] = enrich_row["pr_id"]
                if enrich_row["pr_url"]:
                    aggregate["url"] = enrich_row["pr_url"]
                aggregate["costUsd"] = self._cost_wire(enrich_row["cost_usd"])
            # DESC+LIMIT grabs the last N cheaply; re-reverse to wire order.
            aggregate["steps"] = [
                self._step_wire(row) for row in reversed(step_rows)
            ]
        except RunNotFoundError:
            raise
        except Exception:  # noqa: BLE001 — aggregate must never fail on enrichment
            logger.exception("aggregate enrichment failed for run %s", run_id)
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
                        f"Plan '{plan_id}' was already decided (stale version {expected_version})."
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
