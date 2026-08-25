"""M17-B4 (MAC2 F2) — plan-gate team-scoping tests.

Phase A finding MAC2-F2: plan_gate's propose/approve/reject/start gated on
ROLE only; any engineer/admin of Team B who learned a plan id minted inside
Team A's run could approve or reject it — a cross-team WRITE primitive driving
the target run's state machine.

Fix under test: approve/reject resolve the owning run and enforce team
ownership with the SAME no-oracle discipline as api.py's run routes (M13-G5a):
a foreign team's plan answers EXACTLY what an unknown plan id answers —
404 NOT_FOUND, identical envelope. Role checks stay first (viewer/guest keep
their uniform 403 for every plan id, leaking nothing).

Coverage:
  - foreign-team approve  -> 404, indistinguishable from unknown id
  - foreign-team reject   -> 404, indistinguishable from unknown id
  - same-team decide still works (bare + DB-backed happy paths intact)
  - viewer/guest 403 ordering unchanged
  - DB-backed runs (agent_runs.project_id -> projects.team_id join) enforce
    the same scoping, including a confirmed-mismatch deny

No repo conftest exists for section4; this module builds its own apps, same
as test_plan_gate.py.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Shared-time seam mirrors plan_gate.py: section1's src tree appended exactly
# like api.py's wave-1 audit seam (imports below carry noqa: E402).
_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from harness_contracts.roles import Principal, Role  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_orchestrator.plan_gate import (  # noqa: E402
    _plan_gate_principal,
    configure_plan_gate,
    router,
)
from harness_orchestrator.service import OrchestratorService  # noqa: E402

TEAM_A = "team-alpha"
TEAM_B = "team-beta"


def _principal(
    role: Role = Role.ADMIN, user_id: str = "u-x", team_id: str = TEAM_B
) -> Principal:
    return Principal(userId=user_id, teamId=team_id, role=role)


def _client(service: OrchestratorService, principal: Principal) -> TestClient:
    """Fresh app per call: plan_gate router + principal override (no bearer)."""
    configure_plan_gate(service)
    app = FastAPI()
    app.include_router(router)

    async def _override() -> Principal:
        return principal

    app.dependency_overrides[_plan_gate_principal] = _override
    return TestClient(app)


def _post(client: TestClient, path: str, body: dict[str, Any] | None = None):
    return client.post(path, json=body if body is not None else {})


@pytest.fixture()
def service() -> OrchestratorService:
    """Bare in-memory orchestrator (no DB): pure unit mode."""
    return OrchestratorService()


class RecordingBus:
    """Duck-typed EventBus recording publishes (mirrors test_plan_gate)."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}:{json.dumps(payload or {}, default=str)}")

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        return None


@pytest.fixture()
def db_parts(tmp_path):
    """SQLite-backed service with the frozen tables scoping touches.

    Adds ``projects`` + ``agent_runs`` beside the plan-gate trio so the
    ownership join (agent_runs.project_id -> projects.team_id) executes for
    real against seeded rows.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/plan_gate_scope.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE agent_events ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "project_id VARCHAR(36) NOT NULL,"
                    "run_id VARCHAR(36),"
                    "type VARCHAR(60) NOT NULL,"
                    "payload TEXT NOT NULL,"
                    "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_plans ("
                    "id VARCHAR(36) PRIMARY KEY,"
                    "run_id VARCHAR(36) NOT NULL,"
                    "goal TEXT NOT NULL,"
                    "files_likely_to_change JSON NOT NULL,"
                    "steps JSON NOT NULL,"
                    "tests JSON NOT NULL,"
                    "risks JSON NOT NULL,"
                    "estimated_cost_usd NUMERIC(12,6) NOT NULL,"
                    "forbidden_paths JSON NOT NULL,"
                    "expected_result TEXT NOT NULL,"
                    "status VARCHAR(20) NOT NULL,"
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE audit_log ("
                    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "actor_member_id VARCHAR(36),"
                    "actor_kind VARCHAR(16) NOT NULL,"
                    "action VARCHAR(80) NOT NULL,"
                    "target_type VARCHAR(60) NOT NULL,"
                    "target_id VARCHAR(80) NOT NULL,"
                    "detail JSON NOT NULL,"
                    "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE projects ("
                    "id VARCHAR(36) PRIMARY KEY,"
                    "team_id VARCHAR(36) NOT NULL,"
                    "name VARCHAR(200) NOT NULL,"
                    "repo_full_name VARCHAR(400),"
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_runs ("
                    "id VARCHAR(36) PRIMARY KEY,"
                    "project_id VARCHAR(36) NOT NULL,"
                    "brief_id VARCHAR(36),"
                    "branch VARCHAR(300),"
                    "state VARCHAR(40) NOT NULL,"
                    "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=RecordingBus())
    yield {"service": service, "factory": factory}
    asyncio.run(engine.dispose())


def _seed_owned_run(factory, *, run_id: str, project_id: str, team_id: str, plan_id: str):
    """Seed projects/agent_runs/agent_plans for a DB-backed decision."""

    async def _seed():
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO projects (id, team_id, name)"
                    " VALUES (:p, :t, 'seeded')"
                ),
                {"p": project_id, "t": team_id},
            )
            await db.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state)"
                    " VALUES (:r, :p, 'awaiting_plan_approval')"
                ),
                {"r": run_id, "p": project_id},
            )
            await db.execute(
                text(
                    "INSERT INTO agent_plans (id, run_id, goal,"
                    " files_likely_to_change, steps, tests, risks,"
                    " estimated_cost_usd, forbidden_paths, expected_result,"
                    " status)"
                    " VALUES (:i, :r, 'seeded goal', '[]', '[]', '[]', '[]',"
                    " 0, '[]', 'nothing', 'proposed')"
                ),
                {"i": plan_id, "r": run_id},
            )
            await db.commit()

    asyncio.run(_seed())


# ------------------------------------------------------- cross-team probes


def test_foreign_team_cannot_approve_bare_mode(service):
    """The Phase-A exploit, bare mode: Team B's admin approves a plan minted
    inside Team A's run. Post-fix this is a 404 indistinguishable from an
    unknown plan id — no existence oracle across the tenant boundary."""
    team_a = _client(service, _principal(Role.ADMIN, user_id="u-a", team_id=TEAM_A))
    plan_id = _post(team_a, "/api/v1/orchestrator/plans", {"goal": "A's plan"}).json()[
        "planId"
    ]
    intruder = _client(service, _principal(Role.ADMIN, user_id="u-b", team_id=TEAM_B))

    res = _post(intruder, f"/api/v1/orchestrator/plans/{plan_id}/approve")
    unknown = _post(intruder, "/api/v1/orchestrator/plans/plan-nope/approve")

    assert res.status_code == 404, res.json()
    # Bare TestClient (no envelope middleware): errors surface via the
    # legacy detail mirror, exactly like every other plan-gate test here.
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
    # No-oracle: foreign probe answers EXACTLY what an unknown id answers.
    assert unknown.status_code == 404
    assert res.json() == unknown.json()
    # And nothing moved behind the denial: the plan is still decidable by
    # its OWN team (state machine untouched by the foreign probe).
    assert _post(team_a, f"/api/v1/orchestrator/plans/{plan_id}/approve").status_code == 200


def test_foreign_team_cannot_reject_bare_mode(service):
    """Same probe on the reject verb — the audited half of the pair."""
    team_a = _client(service, _principal(Role.ENGINEER, user_id="u-a", team_id=TEAM_A))
    plan_id = _post(team_a, "/api/v1/orchestrator/plans", {"goal": "A's plan"}).json()[
        "planId"
    ]
    intruder = _client(service, _principal(Role.ENGINEER, user_id="u-b", team_id=TEAM_B))

    res = _post(intruder, f"/api/v1/orchestrator/plans/{plan_id}/reject")
    unknown = _post(intruder, "/api/v1/orchestrator/plans/plan-nope/reject")

    assert res.status_code == 404, res.json()
    assert unknown.status_code == 404
    assert res.json() == unknown.json()


def test_foreign_team_cannot_approve_db_backed_run(db_parts):
    """Confirmed mismatch against the real join: seeded run belongs to
    team-alpha's project; team-beta's engineer must get the same 404."""
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    _seed_owned_run(
        factory,
        run_id="run-owned",
        project_id="proj-alpha",
        team_id=TEAM_A,
        plan_id="plan-owned",
    )

    intruder = _client(service, _principal(Role.ENGINEER, user_id="u-b", team_id=TEAM_B))
    res = _post(intruder, "/api/v1/orchestrator/plans/plan-owned/approve")

    assert res.status_code == 404, res.json()
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"

    async def _status():
        async with factory() as db:
            return (
                await db.execute(
                    text("SELECT status FROM agent_plans WHERE id = 'plan-owned'")
                )
            ).scalar()

    assert asyncio.run(_status()) == "proposed"  # decision never landed


def test_foreign_engineer_cannot_start_foreign_teams_run(db_parts):
    """start_run rides the same locator; a foreign team cannot drive a
    DB-backed run's state machine either (defense in depth beside api.py's
    own _authz_dep)."""
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    _seed_owned_run(
        factory,
        run_id="run-owned",
        project_id="proj-alpha",
        team_id=TEAM_A,
        plan_id="plan-owned",
    )
    intruder = _client(service, _principal(Role.ADMIN, user_id="u-b", team_id=TEAM_B))

    res = _post(intruder, "/api/v1/orchestrator/runs/run-owned/start")

    assert res.status_code == 404, res.json()
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------ legit flows intact


def test_same_team_still_proposes_and_decides(db_parts):
    """Scoping must not break the production walk for the OWNER team:
    DB-backed seeded run decided by its own team's engineer."""
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    _seed_owned_run(
        factory,
        run_id="run-mine",
        project_id="proj-beta",
        team_id=TEAM_B,
        plan_id="plan-mine",
    )
    owner = _client(service, _principal(Role.ENGINEER, user_id="u-b", team_id=TEAM_B))

    approved = _post(owner, "/api/v1/orchestrator/plans/plan-mine/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json() == {"runId": "run-mine"}

    async def _status():
        async with factory() as db:
            return (
                await db.execute(
                    text("SELECT status FROM agent_plans WHERE id = 'plan-mine'")
                )
            ).scalar()

    assert asyncio.run(_status()) == "approved"


def test_bare_mode_same_team_walk_intact(service):
    """Bare unit mode keeps working for one team end to end (propose →
    approve → reject of a second plan)."""
    owner = _client(service, _principal(Role.ADMIN, user_id="u-a", team_id=TEAM_A))
    first = _post(owner, "/api/v1/orchestrator/plans", {"goal": "g1"}).json()["planId"]
    second = _post(owner, "/api/v1/orchestrator/plans", {"goal": "g2"}).json()["planId"]
    assert _post(owner, f"/api/v1/orchestrator/plans/{first}/approve").status_code == 200
    assert _post(owner, f"/api/v1/orchestrator/plans/{second}/reject").status_code == 202


# ------------------------------------------------------------- role order


def test_viewer_still_gets_uniform_403(service):
    """Role gate stays FIRST: a viewer probing any plan id (own or foreign)
    answers the same 403 ROLE_DENIED — unchanged semantics, no new leak."""
    team_a = _client(service, _principal(Role.ADMIN, user_id="u-a", team_id=TEAM_A))
    plan_id = _post(team_a, "/api/v1/orchestrator/plans", {"goal": "g"}).json()["planId"]

    viewer_b = _client(
        service, _principal(Role.VIEWER, user_id="u-v", team_id=TEAM_B)
    )
    res = _post(viewer_b, f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"

    viewer_a = _client(
        service, _principal(Role.VIEWER, user_id="u-va", team_id=TEAM_A)
    )
    own = _post(viewer_a, f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert own.status_code == 403  # identical answer for their own team's plan
