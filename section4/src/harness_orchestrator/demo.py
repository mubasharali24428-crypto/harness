"""M19-N4 — the canned demo (C10): zero-provider-key product value in minutes.

``POST /api/v1/orchestrator/demo/seed`` materializes a COMPLETE agent run from
RECORDED material — the shipped demo task, its canonical diff, the
deterministic reviewer verdict and a PR binding — so a brand-new account sees
real product value without ever touching a provider key.

HONESTY LAW (plan §C10 / odelta L1 #6): this is REPLAY, not simulation of live
work. Nothing here calls an engine, mints fake telemetry, or claims production
behavior:

- every event row carries ``payload.demo = {"replay": true, "source": ...}``
  so consumers can label replayed truth as such (the UI does);
- the run's engine mode reports ``replay`` on the wire aggregate;
- ids are minted per team with the stable ``demo-`` prefix; the recorded task
  text, diff, review verdict strings and PR identity come verbatim from
  ``demo_replay.json``'s curated sources (see its provenance block);
- the seed is IDEMPOTENT: re-seeding returns the same project/run/PR instead
  of piling up duplicates. Idempotency keys: project name + repoFullName,
  then the team's latest ``demo-``-prefixed run (fresh teams always get a
  fresh demo; a deleted run simply reseeds).

Gating: admin/engineer only (frozen roles vocabulary) — viewers/guests get
the uniform ROLE_DENIED 403; unauthenticated requests never reach the handler
(bearer dependency). The route writes ONLY rows owned by the caller's team;
it never mutates another team's data.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from harness_contracts.api import error_body
from harness_contracts.events import EventType
from harness_contracts.plan import AgentPlan
from harness_contracts.roles import Principal
from pydantic import BaseModel

from harness_orchestrator.api import RequirePrincipal, get_orchestrator_service
from harness_orchestrator.authz import RoleDeniedError, require_role
from harness_orchestrator.demo_replay import REPLAY_MARKER, load_fixture
from harness_orchestrator.planner import PlanService
from harness_orchestrator.service import OrchestratorService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["demo"])

#: Stable prefix for every demo instance id (project/run/plan/pr). Operators
#: and tests can tell seeded rows from organic ones at a glance.
DEMO_ID_PREFIX = "demo-"

#: Wire name of the plan-rejection event (state_machine.PLAN_REJECTED_EVENT).
#: Rehydration ignores unknown names by design, so replaying it stays inert.
_PLAN_REJECTED_EVENT = "plan.rejected"


def _load_fixture() -> dict[str, Any]:
    """Parse the versioned replay fixture (shipped with the package).

    Raises 500 DEMO_FIXTURE_INVALID on malformed content rather than seeding a
    half-truth: the honesty law extends to the seeder itself.
    """
    try:
        return load_fixture()
    except Exception as e:  # noqa: BLE001 — a broken fixture must fail LOUDLY
        logger.exception("demo replay fixture unreadable")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_body(
                "DEMO_FIXTURE_INVALID",
                "demo replay fixture missing or malformed; nothing was seeded",
            ),
        ) from e


def _bind_tokens(value: Any, *, run_id: str, plan_id: str, pr_id: str) -> Any:
    """Recursively replace __DEMO_*__ placeholders with the minted ids.

    The fixture records SHAPES; per-team instance ids are bound at seed time
    so concurrent strangers each get their own consistent graph.
    """
    if isinstance(value, dict):
        return {
            k: _bind_tokens(v, run_id=run_id, plan_id=plan_id, pr_id=pr_id)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_bind_tokens(v, run_id=run_id, plan_id=plan_id, pr_id=pr_id) for v in value]
    if isinstance(value, str):
        return (
            value.replace("__DEMO_RUN_ID__", run_id)
            .replace("__DEMO_PLAN_ID__", plan_id)
            .replace("__DEMO_PR_ID__", pr_id)
        )
    return value


def _demo_id(kind: str) -> str:
    return f"{DEMO_ID_PREFIX}{kind}-{uuid.uuid4().hex[:12]}"


async def _fetch_one(db, sql: Any, params: dict[str, Any]) -> Any:
    return (await db.execute(sql, params)).first()


class DemoSeedResponse(BaseModel):
    """Wire shape of POST /demo/seed (camelCase per api-conventions.md)."""

    projectId: str
    projectName: str
    repoFullName: str | None = None
    runId: str
    state: str
    planId: str
    prId: str
    prUrl: str | None = None
    costUsd: float | None = None
    replay: bool = True


def _require_demo_role(principal: Principal) -> None:
    """admin/engineer gate (viewer/guest keep their uniform ROLE_DENIED 403)."""
    try:
        require_role(principal, "write")
    except RoleDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_body("ROLE_DENIED", "role may not seed the demo"),
        ) from e


async def _seed_project(db, team_id: str, name: str, repo_full_name: str) -> tuple[str, bool]:
    """Find-or-create the demo project (name-scoped within the caller's team).

    Returns (project_id, created). Same-value reconnects are a no-op, matching
    deploy/seed_demo.py's idempotency posture.
    """
    from sqlalchemy import text

    row = await _fetch_one(
        db,
        text(
            "SELECT id, repo_full_name FROM projects"
            " WHERE team_id = :team AND name = :name LIMIT 1"
        ),
        {"team": team_id, "name": name},
    )
    if row is not None:
        project_id = row.id
        if row.repo_full_name != repo_full_name:
            await db.execute(
                text("UPDATE projects SET repo_full_name = :repo WHERE id = :p"),
                {"repo": repo_full_name, "p": project_id},
            )
        return project_id, False
    project_id = _demo_id("proj")
    await db.execute(
        text("INSERT INTO projects (id, team_id, name, repo_full_name)"
             " VALUES (:id, :team, :name, :repo)"),
        {"id": project_id, "team": team_id, "name": name, "repo": repo_full_name},
    )
    return project_id, True


async def _latest_demo_run(db, project_id: str) -> str | None:
    """The team's most recent demo run id, or None."""
    from sqlalchemy import text

    row = await _fetch_one(
        db,
        text(
            "SELECT r.id FROM agent_runs r WHERE r.project_id = :p"
            " AND r.id LIKE :prefix ORDER BY r.created_at DESC, r.id DESC LIMIT 1"
        ),
        {"p": project_id, "prefix": f"{DEMO_ID_PREFIX}run-%"},
    )
    return row.id if row is not None else None


def _register_plan(service: OrchestratorService, plan_id: str, plan: AgentPlan) -> None:
    """Mirror the replayed plan into the service's in-memory planner registry.

    GET /runs/{id}/plan reads through PlanService.get_plan; without this the
    seeded run would answer 404 for its own plan after a fresh boot.
    """
    planner: PlanService = service.planner
    if plan_id not in planner.plans:
        planner.plans[plan_id] = plan
        planner.plan_approvals[plan_id] = True


async def _seed_run_rows(db, service: OrchestratorService, spec: dict[str, Any],
                         *, project_id: str, run_id: str, plan_id: str,
                         pr_id: str, team_id: str) -> float:
    """Write the completed run: agent_runs, agent_plans mirror, pull_requests,
    cost_ledger and the ordered agent_events trail. Returns the exact cost.

    ``spec`` is the token-bound RUN spec (the fixture's ``replay.run``).
    """
    from harness_contracts.timeutil import db_now
    from sqlalchemy import text

    run = spec
    plan_spec = run["plan"]

    # agent_runs parent. pr_id starts NULL: agent_runs.pr_id -> pull_requests.id
    # and pull_requests.run_id -> agent_runs.id form a FK CYCLE (migrations
    # 0017 + 0023), so the back-link can only be written AFTER the PR row.
    # This mirrors S2's producer discipline (pr_open.py: INSERT run ->
    # INSERT pull_requests -> UPDATE agent_runs SET pr_id) and is the write
    # order real Postgres enforces — SQLite's unit lane never had FKs on,
    # which is exactly how the E2E journey caught the original ordering bug.
    await db.execute(
        text(
            "INSERT INTO agent_runs (id, project_id, branch, state, created_at)"
            " VALUES (:id, :p, :branch, :state, :ts)"
        ),
        {
            "id": run_id,
            "p": project_id,
            "branch": f"agent/{run_id}",
            "state": EventType.PR_OPENED.value,
            "ts": db_now(),
        },
    )

    # agent_plans mirror (same columns/shape _persist_plan writes; approved)
    plan = AgentPlan(goal=run["goal"], **plan_spec)
    await db.execute(
        text(
            "INSERT INTO agent_plans (id, run_id, goal, files_likely_to_change,"
            " steps, tests, risks, estimated_cost_usd, forbidden_paths,"
            " expected_result, status, created_at)"
            " VALUES (:id, :run_id, :goal, :files, :steps, :tests, :risks,"
            " :cost, :forbidden, :expected, 'approved', :created_at)"
        ),
        {
            "id": plan_id,
            "run_id": run_id,
            "goal": run["goal"],
            "files": json.dumps(plan.filesLikelyToChange),
            "steps": json.dumps(plan.steps),
            "tests": json.dumps(plan.tests),
            "risks": json.dumps(plan.risks),
            "cost": float(plan.estimatedCostUsd),
            "forbidden": json.dumps(plan.forbiddenPaths),
            "expected": plan.expectedResult,
            "created_at": db_now(),
        },
    )
    _register_plan(service, plan_id, plan)

    # pull_requests row, then the cycle's second leg (same SQL as
    # pr_open.py's _SET_RUN_PR_SQL): only now can the FK accept the back-link.
    pr = run["pullRequest"]
    await db.execute(
        text(
            "INSERT INTO pull_requests (id, project_id, run_id, github_pr_number,"
            " pr_url, summary_md, status)"
            " VALUES (:id, :p, :run_id, :num, :url, :summary_md, :status)"
        ),
        {
            "id": pr_id,
            "p": project_id,
            "run_id": run_id,
            "num": int(pr["number"]),
            "url": pr["url"],
            "summary_md": pr.get("summaryMd", ""),
            "status": pr.get("status", "open"),
        },
    )
    await db.execute(
        text("UPDATE agent_runs SET pr_id = :pr_id WHERE id = :run_id"),
        {"pr_id": pr_id, "run_id": run_id},
    )

    # cost_ledger: one exact-money row priced by the REAL PricingEngine for
    # the recorded model + tokens — the same metering the completion path
    # runs, so the replayed spend is catalog truth rather than an invented
    # number.
    from harness_contracts.pricing import PricingEngine

    usage = run["usage"]
    cost_dec = PricingEngine.calculate_cost(
        usage["model"], int(usage["inputTokens"]), int(usage["outputTokens"])
    )
    await db.execute(
        text(
            "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens,"
            " output_tokens, cost_usd, latency_ms)"
            " VALUES (:r, 'primary', :model, :inp, :outp, :cost, :latency)"
        ),
        {
            "r": run_id,
            "model": usage["model"],
            "inp": int(usage["inputTokens"]),
            "outp": int(usage["outputTokens"]),
            "cost": str(cost_dec.quantize(Decimal("0.000001"))),
            "latency": int(usage.get("latencyMs") or 0),
        },
    )

    # Ordered event trail — the same rows the S1 bus persists for live runs,
    # each honestly marked as replay.
    for evt in run["events"]:
        payload = dict(evt["payload"])
        payload["demo"] = REPLAY_MARKER
        await db.execute(
            text(
                "INSERT INTO agent_events (project_id, run_id, type, payload, ts)"
                " VALUES (:p, :r, :t, :payload, :ts)"
            ),
            {
                "p": project_id,
                "r": run_id,
                "t": evt["type"],
                "payload": json.dumps(payload),
                "ts": datetime.fromisoformat(evt["ts"]),
            },
        )
    return float(cost_dec)


@router.post(
    "/demo/seed",
    status_code=status.HTTP_201_CREATED,
    response_model=DemoSeedResponse,
    responses={
        403: {"description": "ROLE_DENIED"},
        500: {"description": "DEMO_FIXTURE_INVALID"},
    },
)
async def seed_demo(request: Request, principal: RequirePrincipal) -> dict[str, Any]:
    """Materialize the canned demo for the CALLER'S team (M19-N4 / C10).

    One project + repo binding + one COMPLETED run carrying its real diff,
    real review verdict and real PR binding, replayed from recorded material.
    Zero provider keys involved. Idempotent: repeat calls return the same
    demo graph. Every persisted payload carries the replay marker.
    """
    service: OrchestratorService = get_orchestrator_service()
    _require_demo_role(principal)

    factory = getattr(service, "session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_body(
                "SERVICE_NOT_CONFIGURED",
                "no database wired; the demo cannot be seeded honestly",
            ),
        )

    replay = _load_fixture()
    proj_spec = replay["project"]
    run_id = _demo_id("run")
    plan_id = _demo_id("plan")
    pr_id = _demo_id("pr")
    run = _bind_tokens(
        replay["run"], run_id=run_id, plan_id=plan_id, pr_id=pr_id
    )

    from sqlalchemy import text

    try:
        async with factory() as db:
            existing = await _fetch_one(
                db,
                text(
                    "SELECT p.id AS project_id, ar.id IS NOT NULL AS has_run"
                    " FROM projects p LEFT JOIN agent_runs ar ON ar.project_id = p.id"
                    " AND ar.id LIKE :prefix"
                    " WHERE p.team_id = :team AND p.name = :name LIMIT 1"
                ),
                {
                    "team": principal.teamId,
                    "name": proj_spec["name"],
                    "prefix": f"{DEMO_ID_PREFIX}run-%",
                },
            )
            if existing is not None and existing.has_run:
                project_id = existing.project_id
                run_id_existing: str = await _latest_demo_run(db, project_id) or run_id
                aggregate = await service.get_status(run_id_existing)
                plan_id_out = aggregate.get("planId")
                return DemoSeedResponse(
                    projectId=str(project_id),
                    projectName=proj_spec["name"],
                    repoFullName=proj_spec["repoFullName"],
                    runId=str(aggregate["runId"]),
                    state=str(aggregate["state"]),
                    planId=str(plan_id_out) if plan_id_out else "",
                    prId="",
                    prUrl=None,
                    costUsd=None,
                ).model_dump()

            # Autobegin note: the idempotency probe above already opened the
            # session's transaction, so the writes below ride THAT transaction
            # and land atomically at ONE commit (any failure rolls back via
            # the session context manager before we ever reach commit).
            project_id, _created = await _seed_project(
                db, principal.teamId, proj_spec["name"], proj_spec["repoFullName"]
            )
            cost = await _seed_run_rows(
                db,
                service,
                run,
                project_id=project_id,
                run_id=run_id,
                plan_id=plan_id,
                pr_id=pr_id,
                team_id=principal.teamId,
            )
            await db.commit()
        # The response carries the exact recorded cost (scale-6 float).
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — a failed seed must look like one
        logger.exception("demo seed failed for team %s", principal.teamId)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_body("DEMO_SEED_FAILED", f"demo seed failed: {e}"),
        ) from e

    pr_url = run["pullRequest"]["url"]
    return DemoSeedResponse(
        projectId=project_id,
        projectName=proj_spec["name"],
        repoFullName=proj_spec["repoFullName"],
        runId=run_id,
        state="pr_opened",
        planId=plan_id,
        prId=pr_id,
        prUrl=pr_url,
        costUsd=cost,
        replay=True,
    ).model_dump()
