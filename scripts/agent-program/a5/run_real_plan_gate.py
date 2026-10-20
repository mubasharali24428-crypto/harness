"""Run the REAL Section 4 orchestrator + plan gate for A5-S3 smoke tests.

Boots the section4 FastAPI app in bare-unit mode (no DB — the plan gate's
documented local mode; HARNESS_AUTHZ_TEST_MODE=1 is the upstream-sanctioned
escape hatch for exactly this) with a bearer-token principal wired into
app.state.get_principal, and prints ``READY <port>`` once uvicorn is listening.

Run with section4's own venv:
    section4/.venv/bin/python scripts/agent-program/a5/run_real_plan_gate.py

Env:
    PLAN_GATE_TOKEN   bearer token clients must present (required)

Ownership: scripts/agent-program/a5/** (Agent A5). Read-only consumption of
section4 code — nothing in this script modifies the orchestrator.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "section4" / "src"))

os.environ.setdefault("HARNESS_AUTHZ_TEST_MODE", "1")

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from harness_contracts.roles import Principal, Role
from harness_orchestrator.api import configure_orchestrator, router
from harness_orchestrator.plan_gate import configure_plan_gate
from harness_orchestrator.plan_gate import router as plan_gate_router
from harness_orchestrator.service import OrchestratorService


def build_app(token: str) -> FastAPI:
    service = OrchestratorService()  # bare mode: no DB, in-memory run table
    configure_orchestrator(service)
    configure_plan_gate(service)

    app = FastAPI(title="a5-real-plan-gate-runner")
    app.include_router(router)
    app.include_router(plan_gate_router)

    async def get_principal(request: Request) -> Principal:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {token}":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "UNAUTHENTICATED", "message": "bad token"}},
            )
        return Principal(userId="a5-smoke", teamId="team-a", role=Role.ADMIN)

    app.state.get_principal = get_principal
    return app


async def main() -> None:
    token = os.environ["PLAN_GATE_TOKEN"]
    app = build_app(token)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    print(f"READY {port}", flush=True)
    await task


if __name__ == "__main__":
    asyncio.run(main())
