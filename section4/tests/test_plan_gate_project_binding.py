# ruff: noqa: F811, F401
"""B1 (multiplayer S4): POST /plans binds the minted run to a validated project.

Found live: dashboard-created runs had no project binding and no agent_runs row,
so plan persistence FK-failed and every run-scoped route 404'd for them.
"""

import asyncio

from sqlalchemy import text

from tests.test_plan_gate_scoping import (  # noqa: F401  (db_parts is a fixture)
    TEAM_A,
    TEAM_B,
    _client,
    _principal,
    db_parts,
)


class PersistingBus:
    """Like EventBus.ensure_run: idempotently create the agent_runs parent row."""

    def __init__(self, factory):
        self.factory = factory

    async def publish(self, *a, **k):
        return None

    async def ensure_run(self, project_id, run_id):
        async with self.factory() as db:
            exists = (
                await db.execute(text("SELECT 1 FROM agent_runs WHERE id = :r"), {"r": run_id})
            ).first()
            if not exists:
                await db.execute(
                    text("INSERT INTO agent_runs (id, project_id, state) VALUES (:r, :p, 'idle')"),
                    {"r": run_id, "p": project_id},
                )
                await db.commit()


def _setup(db_parts, projects):
    f, svc = db_parts["factory"], db_parts["service"]
    svc.bus = PersistingBus(f)

    async def seed():
        async with f() as db:
            for pid, team in projects:
                await db.execute(
                    text("INSERT INTO projects (id, team_id, name) VALUES (:p, :t, 'n')"),
                    {"p": pid, "t": team},
                )
            await db.commit()

    asyncio.run(seed())
    return svc, f


def _run_row(f, run_id):
    async def go():
        async with f() as db:
            return (
                await db.execute(
                    text("SELECT project_id FROM agent_runs WHERE id = :r"), {"r": run_id}
                )
            ).first()

    return asyncio.run(go())


def _plan_count(f, run_id):
    async def go():
        async with f() as db:
            return (
                await db.execute(
                    text("SELECT count(*) AS n FROM agent_plans WHERE run_id = :r"), {"r": run_id}
                )
            ).first().n

    return asyncio.run(go())


def _propose(svc, body, team=TEAM_A):
    c = _client(svc, _principal(team_id=team, user_id="u-a"))
    r = c.post("/api/v1/orchestrator/plans", json=body)
    return r


def _run_id(svc):
    return next(iter(svc.runs))


def test_explicit_project_binds_run_and_persists_plan(db_parts):
    svc, f = _setup(db_parts, [("proj-1", TEAM_A), ("proj-2", TEAM_A)])
    r = _propose(svc, {"goal": "g", "projectId": "proj-2"})
    assert r.status_code == 201, r.text
    rid = _run_id(svc)
    assert svc.runs[rid]["projectId"] == "proj-2"
    assert _run_row(f, rid).project_id == "proj-2"  # agent_runs row exists, bound
    assert _plan_count(f, rid) == 1  # plan persisted (FK satisfied)


def test_foreign_or_unknown_project_is_indistinguishable_404(db_parts):
    svc, f = _setup(db_parts, [("proj-b", TEAM_B)])
    foreign = _propose(svc, {"goal": "g", "projectId": "proj-b"})
    unknown = _propose(svc, {"goal": "g", "projectId": "nope"})
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
    assert svc.runs == {}  # nothing minted


def test_omitted_project_binds_the_teams_only_project(db_parts):
    svc, f = _setup(db_parts, [("proj-only", TEAM_A), ("proj-b", TEAM_B)])
    assert _propose(svc, {"goal": "g"}).status_code == 201
    assert svc.runs[_run_id(svc)]["projectId"] == "proj-only"


def test_omitted_project_with_several_stays_unbound_as_before(db_parts):
    svc, f = _setup(db_parts, [("p1", TEAM_A), ("p2", TEAM_A)])
    assert _propose(svc, {"goal": "g"}).status_code == 201
    assert svc.runs[_run_id(svc)].get("projectId") is None
