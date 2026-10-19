"""Part 38.005 on the orchestrator: every run/plan route is classified, and an org policy deny
binds an org-linked team's caller on run control while an unlinked team passes through."""

import json
import re

import pytest
from fastapi import APIRouter, FastAPI
from harness_contracts import Principal, Role
from harness_contracts.policy_enforce import ORCHESTRATOR_RULES, classify, install_policy_gate
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import (
    agent_tree,
    artifacts,
    control,
    discussion,
    execution,
    exports,
    participation,
    plans,
    presence,
    search,
)
from harness_orchestrator.api import router as run_router
from harness_orchestrator.demo import router as demo_router
from harness_orchestrator.plan_gate import router as plan_router
from harness_orchestrator.runs_index import router as index_router

_DDL = """
CREATE TABLE teams (id TEXT PRIMARY KEY, org_id TEXT);
CREATE TABLE org_members (org_id TEXT, user_id TEXT, role TEXT, expires_at TEXT, removed_at TEXT);
CREATE TABLE policy_documents (id TEXT, org_id TEXT, level TEXT, scope_id TEXT, doc TEXT);
CREATE TABLE policy_custom_roles (org_id TEXT, name TEXT, permissions TEXT);
CREATE TABLE policy_assignments (org_id TEXT, user_id TEXT, level TEXT, scope_id TEXT, role TEXT,
    expires_at TEXT);
CREATE TABLE policy_emergency_grants (id TEXT, org_id TEXT, user_id TEXT, level TEXT, scope_id TEXT,
    permissions TEXT, status TEXT, starts_at TEXT, expires_at TEXT);
CREATE TABLE policy_decision_log (id TEXT, org_id TEXT, ts TEXT, subject TEXT, permission TEXT,
    resource TEXT, decision TEXT, reason TEXT, rule_ids TEXT);
CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, actor_member_id TEXT,
    actor_kind TEXT, action TEXT, target_type TEXT, target_id TEXT, detail TEXT, ts TEXT);
"""


def test_every_orchestrator_route_is_classified():
    app = FastAPI()
    for r in (
        run_router,
        plan_router,
        index_router,
        demo_router,
        presence.router,
        participation.router,
        control.router,
        agent_tree.router,
        discussion.router,
        plans.router,
        execution.router,
        artifacts.router,
        search.router,
        exports.router,
    ):
        app.include_router(r)
    paths = app.openapi()["paths"]
    assert len(paths) >= 110  # guard against a vacuous walk
    missing = [
        f"{m.upper()} {p}"
        for p, ops in paths.items()
        for m in ops
        if m in {"get", "post", "put", "patch", "delete"}
        and classify(ORCHESTRATOR_RULES, m.upper(), re.sub(r"\{[^}]+\}", "abc123", p)) is None
    ]
    assert missing == []


def test_classification_examples():
    c = lambda m, p: classify(ORCHESTRATOR_RULES, m, p)  # noqa: E731
    base = "/api/v1/orchestrator"
    assert c("GET", f"{base}/runs/r1/status") == "read"
    assert c("POST", f"{base}/runs/r1/pause") == "steer"
    assert c("POST", f"{base}/runs/r1/approvals/a1/decision") == "approve"
    assert c("POST", f"{base}/plans/p1/approve") == "approve"
    assert c("POST", f"{base}/runs/r1/plan/reject") == "approve"


@pytest.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/g.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for stmt in _DDL.split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
        await conn.execute(text("INSERT INTO teams VALUES ('t-org', 'o1'), ('t-free', NULL)"))
        rule = {"id": "no-steer", "effect": "deny", "permissions": ["steer"], "subjects": ["*"]}
        await conn.execute(
            text("INSERT INTO policy_documents VALUES ('d', 'o1', 'org', 'o1', :d)"),
            {"d": json.dumps({"rules": [rule]})},
        )
    app = FastAPI()
    pr = APIRouter()

    @pr.post("/api/v1/orchestrator/runs/{runId}/pause")
    async def pause(runId: str):
        return {"ok": True}

    @pr.get("/api/v1/orchestrator/runs/{runId}/status")
    async def status(runId: str):
        return {"ok": True}

    app.include_router(pr)

    async def who(request):
        team = request.headers.get("x-team")
        return Principal(userId="u1", teamId=team, role=Role.ENGINEER) if team else None

    install_policy_gate(app, ORCHESTRATOR_RULES, who, lambda _r: factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.factory = factory
        yield c
    await engine.dispose()


async def test_org_deny_blocks_run_control_but_not_reads_or_unlinked_teams(client):
    pause = "/api/v1/orchestrator/runs/r1/pause"
    r = await client.post(pause, headers={"x-team": "t-org"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "POLICY_DENIED"
    assert (
        await client.get(pause.replace("pause", "status"), headers={"x-team": "t-org"})
    ).status_code == 200
    assert (await client.post(pause, headers={"x-team": "t-free"})).status_code == 200
    assert (await client.post(pause)).status_code == 200  # no session: the route answers
    async with client.factory() as db:
        denied = (
            await db.execute(
                text("SELECT target_id, detail FROM audit_log WHERE action='policy.denied'")
            )
        ).all()
        logged = (
            await db.execute(text("SELECT resource, decision FROM policy_decision_log"))
        ).all()
    assert len(denied) == 1 and json.loads(denied[0][1])["permission"] == "steer"
    assert [tuple(x) for x in logged] == [("org:o1/team:t-org/run:r1", "deny")]
