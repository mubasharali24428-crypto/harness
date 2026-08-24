"""VT-5 independent CAS proof v3 — verifier scratch, NOT part of repo suite.

Plan A (API surface):   propose -> approve -> re-approve -> late reject
                        = idempotent same-runId, version burned ONCE, reject 409s.
Plan B (durable layer): two SEPARATE engines race CAS(expected_version=0)
                        = exactly one winner bumps 0->1, loser PlanAlreadyDecidedError;
                        stale retry afterwards still loses.
"""
import asyncio
import os
import sys

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from harness_contracts.roles import Principal, Role
from harness_orchestrator.plan_gate import (
    _plan_gate_principal,
    configure_plan_gate,
    router,
)
from harness_orchestrator.service import OrchestratorService, PlanAlreadyDecidedError

_AGENT_PLANS_DDL = (
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
    "version INTEGER NOT NULL DEFAULT 0,"
    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)
_EVENTS_DDL = (
    "CREATE TABLE agent_events ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    "project_id VARCHAR(36) NOT NULL,"
    "run_id VARCHAR(36),"
    "type VARCHAR(60) NOT NULL,"
    "payload TEXT NOT NULL,"
    "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)


class RecordingBus:
    async def publish(self, event_type, project_id, payload=None, run_id=None):
        return None

    async def ensure_run(self, project_id, run_id):
        return None


async def _schema(path):
    db = await aiosqlite.connect(path)
    await db.execute(_EVENTS_DDL)
    await db.execute(_AGENT_PLANS_DDL)
    await db.commit()
    await db.close()


def main():
    tmpdb = "/tmp/vt5_cas_v3.db"
    if os.path.exists(tmpdb):
        os.unlink(tmpdb)
    asyncio.run(_schema(tmpdb))

    eng1 = create_async_engine(f"sqlite+aiosqlite:///{tmpdb}")
    eng2 = create_async_engine(f"sqlite+aiosqlite:///{tmpdb}")
    sm1 = async_sessionmaker(eng1, expire_on_commit=False)
    sm2 = async_sessionmaker(eng2, expire_on_commit=False)
    svc1 = OrchestratorService(session_factory=sm1, bus=RecordingBus())
    OrchestratorService(session_factory=sm2, bus=RecordingBus())

    configure_plan_gate(svc1)
    app = FastAPI()
    app.include_router(router)

    def _principal():
        return Principal(userId="vt5", teamId="team-a", role=Role.ADMIN)

    app.dependency_overrides[_plan_gate_principal] = _principal
    client = TestClient(app)

    async def rowstate(plan_id):
        async with sm1() as db:
            return (await db.execute(
                text("SELECT status, version FROM agent_plans WHERE id = :p"), {"p": plan_id}
            )).first()

    # ================= PLAN A: pure API surface =========================
    res = client.post("/api/v1/orchestrator/plans", json={"goal": "vt5 api walk"})
    assert res.status_code == 201, res.text
    pa = res.json()["planId"]
    print(f"A0 proposed     : {pa}")

    r1 = client.post(f"/api/v1/orchestrator/plans/{pa}/approve")
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"/api/v1/orchestrator/plans/{pa}/approve")
    assert r2.status_code == 200, r2.text
    assert r1.json()["runId"] == r2.json()["runId"], "re-approve must return same runId"
    ra = asyncio.run(rowstate(pa))
    print(f"A1 re-approve   : both 200, runId={r1.json()['runId']}, row={tuple(ra)}")
    assert tuple(ra)[1] == 1, f"version burned by re-approve: {ra}"

    rj = client.post(f"/api/v1/orchestrator/plans/{pa}/reject")
    detail = rj.json().get("detail", {})
    code = detail.get("error", {}).get("code") or detail.get("code")
    print(f"A2 late reject  : HTTP {rj.status_code} body-code={code}")
    assert rj.status_code == 409 and code == "PLAN_ALREADY_DECIDED", rj.text

    # ================= PLAN B: durable-layer two-engine race ============
    res = client.post("/api/v1/orchestrator/plans", json={"goal": "vt5 durable race"})
    assert res.status_code == 201, res.text
    pb = res.json()["planId"]
    run_b = None
    for rid, data in svc1.runs.items():
        sm = data["state_machine"]
        if getattr(sm, "current_plan_id", None) == pb or data.get("plan_id") == pb:
            run_b = rid
            break
    assert run_b, f"no run located for {pb}"
    print(f"B0 proposed     : {pb} (run {run_b})")

    svc2 = OrchestratorService(
        session_factory=async_sessionmaker(create_async_engine(f"sqlite+aiosqlite:///{tmpdb}"),
                                           expire_on_commit=False),
        bus=RecordingBus(),
    )

    async def _race():
        s2 = OrchestratorService(
            session_factory=async_sessionmaker(
                create_async_engine(f"sqlite+aiosqlite:///{tmpdb}"), expire_on_commit=False),
            bus=RecordingBus(),
        )
        return await asyncio.gather(
            svc1._persist_plan_status(run_b, pb, "approved", expected_version=0),
            s2._persist_plan_status(run_b, pb, "approved", expected_version=0),
            return_exceptions=True,
        )

    results = asyncio.run(_race())
    winners = [r for r in results if not isinstance(r, Exception)]
    losers = [r for r in results if isinstance(r, PlanAlreadyDecidedError)]
    others = [r for r in results if isinstance(r, Exception) and not isinstance(r, PlanAlreadyDecidedError)]
    print(f"B1 race         : winners={winners} losers={[type(l).__name__ for l in losers]} others={others!r}")
    assert len(winners) == 1 and winners[0] == 1, "exactly one winner bumping 0->1 required"
    assert len(losers) == 1 and not others, "exactly one clean CAS loser required"

    rb = asyncio.run(rowstate(pb))
    print(f"B2 post-race row: status={rb[0]!r} version={rb[1]}")
    assert (rb[0], rb[1]) == ("approved", 1)

    try:
        asyncio.run(svc2._persist_plan_status(run_b, pb, "approved", expected_version=0))
        raise AssertionError("stale retry must lose")
    except PlanAlreadyDecidedError as e:
        print(f"B3 stale retry  : PlanAlreadyDecidedError({e})")

    async def _cleanup():
        await eng1.dispose()
        await eng2.dispose()

    asyncio.run(_cleanup())
    print("CAS PROOF v3: PASS")


if __name__ == "__main__":
    main()
