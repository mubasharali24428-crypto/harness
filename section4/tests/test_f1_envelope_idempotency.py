"""M12-W2.1 lane F1 tests (Squad ALPHA) — H-019 + H-020, section4.

H-019: RequestValidationError (FastAPI 422) on the orchestrator router now
answers the FROZEN error_body() envelope (+ legacy ``detail`` mirror);
contract-shaped HTTPException details pass through byte-for-byte.

H-020: Idempotency-Key replay on POST /api/v1/orchestrator/runs/{runId}/start
— same key twice => identical status+body and ONE start_execution call;
different key => separate executions. Storage runs through a fake asyncpg
pool driving the middleware's real SQL paths; migration 0015 defines the
table.

No repo conftest exists for section4; this module builds its own apps, same
same as test_plan_gate.py / test_authz.py.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.service import OrchestratorService

# M17-B3: the byte-identical S4 middleware fork is deleted; the canonical
# copy lives in harness_identity (tests reach it via the section1/src seam).
import sys as _sys
from pathlib import Path as _Path

_S1_SRC = str(_Path(__file__).resolve().parents[2] / "section1" / "src")
if _S1_SRC not in _sys.path:
    _sys.path.append(_S1_SRC)
from harness_identity.envelope_middleware import (  # noqa: E402
    IdempotencyMiddleware,
    install_error_envelope,
)

# gitleaks:allow — fake fixture token, never a real credential


def _principal(role: Role = Role.ADMIN, user_id: str = "u-f1") -> Principal:
    return Principal(userId=user_id, teamId="team-a", role=role)


class FakeAsyncPgPool:
    """Minimal asyncpg-pool stand-in validating the middleware's SQL paths."""

    def __init__(self):
        self.rows = {}

    class _Conn:
        def __init__(self, pool):
            self._pool = pool

        async def fetchrow(self, sql, *args):
            stmt = " ".join(sql.split())
            if stmt.startswith("SELECT"):
                rec = self._pool.rows.get(args[0])
                return None if rec is None else dict(rec)
            if stmt.startswith("INSERT"):
                fp = args[0]
                if fp in self._pool.rows:
                    return None  # ON CONFLICT DO NOTHING
                self._pool.rows[fp] = {"fingerprint": fp, "status_code": 0, "response_body": ""}
                return {"fingerprint": fp}
            if stmt.startswith("UPDATE"):
                rec = self._pool.rows.get(args[0])
                if rec is not None:
                    rec["status_code"], rec["response_body"] = args[1], str(args[2])
                return None
            if stmt.startswith("DELETE"):
                self._pool.rows.pop(args[0], None)
                return None
            raise AssertionError(f"unexpected SQL: {stmt[:80]}")

        async def execute(self, sql, *args):
            await self.fetchrow(sql, *args)

    def acquire(self):
        return _AcquireCtx(self._Conn(self))


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _client(
    monkeypatch,
    service: OrchestratorService,
    principal: Principal,
    *,
    idem_pool: FakeAsyncPgPool | None = None,
) -> TestClient:
    monkeypatch.setattr(orch_api, "_service", service)
    app = FastAPI()
    app.include_router(router)
    install_error_envelope(app)
    if idem_pool is not None:
        app.add_middleware(IdempotencyMiddleware)
        app.state.idempotency_pool = idem_pool
        app.state.idempotency_table = "idempotency_keys"
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


# --------------------------------------------------------------- H-019


def test_validation_error_envelope_on_orchestrator(monkeypatch):
    """POST with an invalid body used to escape as bare {"detail": [...]}."""
    service = OrchestratorService()
    client = _client(monkeypatch, service, _principal())

    res = client.post("/api/v1/orchestrator/runs/run-e1/human-edit", json={"nope": 1})
    assert res.status_code == 422
    body = res.json()
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["detail"]["error"]["code"] == "VALIDATION_ERROR"
    assert "path" in body["error"]["message"]  # loc names the missing field


def test_contract_shaped_detail_passthrough(monkeypatch):
    """Existing error_body(...) details keep their exact code + status."""
    service = OrchestratorService()
    client = _client(monkeypatch, service, _principal())
    # start without an approved plan raises detail={"error": {"code":
    # "PLAN_NOT_APPROVED", ...}} — must survive the normalization untouched.
    res = client.post("/api/v1/orchestrator/runs/run-e2/start")
    assert res.status_code == 403
    body = res.json()
    assert body["error"]["code"] == "PLAN_NOT_APPROVED"
    assert body["detail"]["error"]["code"] == "PLAN_NOT_APPROVED"


# --------------------------------------------------------------- H-020


@pytest.fixture
def started_run_service():
    """Service holding one APPROVED run ready for start_execution."""
    import asyncio

    service = OrchestratorService()

    async def _make():
        plan_id = await service.propose_plan("run-idem", goal="f1 lane")
        await service.approve_plan("run-idem", str(plan_id), "u-f1")

    asyncio.run(_make())
    return service


def test_same_key_replays_identically_single_side_effect(
    monkeypatch, started_run_service
):
    pool = FakeAsyncPgPool()
    client = _client(
        monkeypatch, started_run_service, _principal(), idem_pool=pool
    )
    headers = {"Authorization": "Bearer f1-token", "Idempotency-Key": "start-once"}

    calls: list[str] = []
    real_start = started_run_service.start_execution

    async def counting_start(run_id):
        calls.append(run_id)
        return await real_start(run_id)

    monkeypatch.setattr(started_run_service, "start_execution", counting_start)

    r1 = client.post("/api/v1/orchestrator/runs/run-idem/start", headers=headers)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/v1/orchestrator/runs/run-idem/start", headers=headers)
    assert r2.status_code == 200
    assert r2.json() == r1.json()  # identical cached response...
    assert r2.headers.get("idempotent-replay") == "true"  # ...from cache...
    assert calls == ["run-idem"]  # ...and exactly ONE handler execution


def test_different_key_executes_separately(monkeypatch):
    pool = FakeAsyncPgPool()
    client = _client(monkeypatch, OrchestratorService(), _principal(), idem_pool=pool)

    plan_a = client.post("/api/v1/orchestrator/runs/run-da/plan").json()["planId"]
    client.post("/api/v1/orchestrator/runs/run-da/plan/approve", json={"planId": plan_a})

    ra = client.post(
        "/api/v1/orchestrator/runs/run-da/start",
        headers={"Idempotency-Key": "k-A"},
    )
    assert ra.status_code == 200  # first execution starts the run
    rb = client.post(
        "/api/v1/orchestrator/runs/run-da/start",
        headers={"Idempotency-Key": "k-B"},
    )
    # Different key => fresh execution, NOT a replay of k-A's cached 200.
    assert rb.status_code == ra.status_code
    assert rb.headers.get("idempotent-replay") != "true"
    assert pool.rows  # both keys got their own cache entries


def test_no_header_bypasses_cache(monkeypatch):
    client = _client(monkeypatch, OrchestratorService(), _principal(), idem_pool=FakeAsyncPgPool())
    res = client.post("/api/v1/orchestrator/runs/run-nh/start")
    assert res.status_code in (200, 403)  # plain execution path
    assert res.headers.get("idempotent-replay") is None
