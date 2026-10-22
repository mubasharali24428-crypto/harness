"""M13-G5a — API hardening regression guards for Section 4 (Squad ALPHA).

1. Oracle probe: a foreign team's run/project answers EXACTLY what an unknown
   id answers — 404 NOT_FOUND in the frozen error envelope, never a 403 that
   would confirm existence across the tenant boundary. Role denials stay 403
   (identity-first is deliberate); auth failures stay 401.
2. OpenAPI typing islands: the top orchestrator routes expose concrete
   components in openapi.json instead of bare objects.
"""

import asyncio as _asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.plan_gate import configure_plan_gate
from harness_orchestrator.plan_gate import router as plan_gate_router
from harness_orchestrator.service import OrchestratorService


def _principal(
    user_id: str = "u-own", team_id: str = "team-a", role: Role = Role.ADMIN
) -> Principal:
    return Principal(userId=user_id, teamId=team_id, role=role)


def _error_code(body: dict) -> str:
    """Error code from either wiring of the app under test: the F1 envelope
    middleware ({"error": {...}}) or a bare app raising HTTPException
    ({"detail": {"error": {...}}})."""
    err = body.get("error") or (body.get("detail") or {}).get("error")
    return (err or {}).get("code", "<missing>")


def _client(monkeypatch, service: OrchestratorService, principal: Principal) -> TestClient:
    """Bare app with BOTH routers mounted — the deploy wiring in
    deploy/runners/services.py includes plan_gate on the same app."""
    monkeypatch.setattr(orch_api, "_service", service)
    configure_plan_gate(service)
    app = FastAPI()
    app.include_router(router)
    app.include_router(plan_gate_router)
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Oracle probe — foreign ids are indistinguishable from unknown ids
# ---------------------------------------------------------------------------

# NOTE: bare unit mode (no DB) auto-seeds every id via _ensure_run, so there is
# no meaningful foreign-vs-unknown probe without a wired projects table; the
# DB-backed test below carries the oracle law for run AND project ids.


def _mk_db_service():
    """Service wired to a sqlite DB with two teams' project rows."""
    engine = create_async_engine("sqlite+aiosqlite://")

    async def _create():
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE projects ("
                    "id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE agent_runs ("
                    "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36))"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO projects (id, team_id) VALUES ('proj-theirs', 'team-b'),"
                    " ('proj-ours', 'team-a')"
                )
            )

    _asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return OrchestratorService(session_factory=factory), engine


def test_propose_plan_rejects_foreign_project_with_404(monkeypatch):
    service, engine = _mk_db_service()
    try:
        client = _client(monkeypatch, service, _principal(team_id="team-a"))
        res = client.post(
            "/api/v1/orchestrator/runs/run-g5a/plan",
            json={"goal": "g", "projectId": "proj-theirs"},
        )
        assert res.status_code == 404, res.text
        assert _error_code(res.json()) == "NOT_FOUND"

        unknown = client.post(
            "/api/v1/orchestrator/runs/run-g5a/plan",
            json={"goal": "g", "projectId": "proj-never-existed"},
        )
        assert unknown.status_code == 404
        # No-oracle: foreign and unknown ids are indistinguishable.
        assert res.json()["detail"]["error"] == unknown.json()["detail"]["error"]

        # Own-team project still binds fine (201).
        own = client.post(
            "/api/v1/orchestrator/runs/run-g5a/plan",
            json={"goal": "g", "projectId": "proj-ours"},
        )
        assert own.status_code == 201, own.text
        assert own.json()["runId"] == "run-g5a"

        # The bound run is now foreign to team-b: identical NOT_FOUND there.
        other = _client(monkeypatch, service, _principal(team_id="team-b"))
        foreign_run = other.get("/api/v1/orchestrator/runs/run-g5a/status")
        unknown_run = other.get("/api/v1/orchestrator/runs/run-unknown/status")
        assert foreign_run.status_code == 404
        # M18-C1: unknown ids no longer auto-seed-and-allow past the guard —
        # the ownership half denies them (OwnershipDeniedError), which this
        # seam maps to the SAME 404 NOT_FOUND envelope as a foreign run
        # (M13-G5a: "unknown or foreign run" share one answer).
        assert unknown_run.status_code == 404
        assert _error_code(unknown_run.json()) == "NOT_FOUND"
        assert _error_code(foreign_run.json()) == "NOT_FOUND"
    finally:
        _asyncio.run(engine.dispose())


def test_role_denial_stays_403_not_404(monkeypatch):
    """Identity-first ordering is deliberate: an in-team viewer gets ROLE_DENIED
    wherever they probe — that is NOT a tenant oracle and must not regress."""
    client = _client(monkeypatch, OrchestratorService(), _principal(role=Role.VIEWER))
    res = client.post("/api/v1/orchestrator/runs/run-v5a/start")
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"


# ---------------------------------------------------------------------------
# 2. OpenAPI typing islands
# ---------------------------------------------------------------------------

_TYPED_S4_ROUTES = [
    ("post", "/api/v1/orchestrator/plans"),
    ("post", "/api/v1/orchestrator/plans/{planId}/approve"),
    ("post", "/api/v1/orchestrator/plans/{planId}/reject"),
    ("post", "/api/v1/orchestrator/runs/{runId}/start"),
    ("post", "/api/v1/orchestrator/runs/{runId}/pause"),
    ("post", "/api/v1/orchestrator/runs/{runId}/resume"),
    ("post", "/api/v1/orchestrator/runs/{runId}/stop"),
    ("post", "/api/v1/orchestrator/runs/{runId}/done-signal"),
    ("get", "/api/v1/orchestrator/runs/{runId}/status"),
]


def _concrete_refs(node: object) -> list[str]:
    """All $ref component names reachable from one OpenAPI schema node,
    descending into arrays and anyOf/oneOf unions."""
    found: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "$ref" in cur:
                found.append(str(cur["$ref"]).split("/")[-1])
            for key in ("items", "prefixItems"):
                if key in cur:
                    stack.append(cur[key])
            for key in ("anyOf", "oneOf", "allOf"):
                for sub in cur.get(key, []):
                    stack.append(sub)
    return found


def test_s4_top_routes_have_concrete_openapi_components(monkeypatch):
    monkeypatch.setattr(orch_api, "_service", OrchestratorService())
    configure_plan_gate(OrchestratorService())
    app = FastAPI()
    app.include_router(router)
    app.include_router(plan_gate_router)
    schema = app.openapi()
    missing = []
    for method, path in _TYPED_S4_ROUTES:
        op = schema["paths"][path][method]
        refs: list[str] = []
        for code in ("200", "201", "202"):
            resp = op["responses"].get(code)
            if not resp:
                continue
            node = resp.get("$ref") or (
                resp.get("content", {}).get("application/json", {}).get("schema")
            )
            if node:
                refs = _concrete_refs(node)
            if refs:
                break
        if not refs or not all(n in schema["components"]["schemas"] for n in refs):
            missing.append(f"{method.upper()} {path}")
    assert not missing, f"routes without concrete components: {missing}"


def test_propose_requires_project_and_cannot_rebind_foreign_run(monkeypatch):
    """Audit S4: omitted projectId and foreign-run rebinding both deny."""
    service, engine = _mk_db_service()
    try:
        import asyncio

        async def _seed_run():
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO agent_runs (id, project_id) "
                        "VALUES ('run-team-b', 'proj-theirs')"
                    )
                )

        asyncio.run(_seed_run())
        client = _client(monkeypatch, service, _principal(team_id="team-a"))

        missing = client.post("/api/v1/orchestrator/runs/run-new/plan", json={"goal": "g"})
        assert missing.status_code == 404
        assert _error_code(missing.json()) == "NOT_FOUND"

        hijack = client.post(
            "/api/v1/orchestrator/runs/run-team-b/plan",
            json={"goal": "g", "projectId": "proj-ours"},
        )
        assert hijack.status_code == 404
        assert _error_code(hijack.json()) == "NOT_FOUND"
        assert "run-team-b" not in service.runs
    finally:
        asyncio.run(engine.dispose())
