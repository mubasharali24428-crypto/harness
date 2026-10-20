"""A5-S3 smoke: signed connector intake -> REAL Section 4 plan gate.

Proves, against the real orchestrator process (not a fixture):
  1. a signed Slack webhook through the connector service results in a real
     plan (planId minted by harness_orchestrator) awaiting human approval;
  2. a duplicate delivery does not mint a second plan;
  3. the human approve path works on the real API and is idempotent;
  4. the approved run's plan carries the untrusted-content banner (what the
     human is approving is visibly attacker-controlled context);
  5. the connector surface has NO approve/start/execute routes (no automatic
     execution by construction).

Run with the extensions venv (needs harness_workflow_connectors + httpx):
    packages/extensions/.venv/bin/python scripts/agent-program/a5/smoke_real_plan_gate.py

Ownership: scripts/agent-program/a5/** (Agent A5). The section-4 process is
run read-only; nothing under section4/ is modified.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
CONNECTOR_TESTS = REPO_ROOT / "integrations" / "connectors" / "workflows" / "tests"
sys.path.insert(0, str(CONNECTOR_TESTS))

from conftest import SECRETS, sign_payload
from harness_workflow_connectors import (
    IdempotencyStore,
    IntakePipeline,
    RateLimiter,
    SignatureScheme,
    TenantConfig,
    TenantMap,
)
from harness_workflow_connectors import slack as slack_mod
from harness_workflow_connectors.plan_gate import ProposePlanGate
from harness_workflow_connectors.service import make_service_app

TOKEN = "a5-smoke-gate-token"


async def wait_ready(proc: subprocess.Popen) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"runner exited: rc={proc.poll()} {proc.stderr.read()}")
        if line.startswith("READY "):
            return line.split()[1].strip()
    raise TimeoutError("runner never reported READY")


def slack_body(event_id: str) -> bytes:
    return json.dumps(
        {
            "type": "event_callback",
            "event_id": event_id,
            "team_id": "T123",
            "event": {"type": "message", "text": "prod API 5xx spike after deploy 42",
                      "ts": "1729.001", "channel": "C1"},
        }
    ).encode()


async def main() -> int:
    runner = subprocess.Popen(  # noqa: ASYNC220 - script orchestration, not library code
        [
            str(REPO_ROOT / "section4" / ".venv" / "bin" / "python"),
            str(REPO_ROOT / "scripts" / "agent-program" / "a5" / "run_real_plan_gate.py"),
        ],
        env={**os.environ, "PLAN_GATE_TOKEN": TOKEN, "HARNESS_AUTHZ_TEST_MODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port = await wait_ready(runner)
        server_root = f"http://127.0.0.1:{port}"
        print(f"real plan gate listening on {server_root}")

        tmp = tempfile.mkdtemp(prefix="a5-smoke-")
        tenants = TenantMap(
            tenants={"slack": {"T123": TenantConfig(team_id="team-a")}}
        )
        plan_gate = ProposePlanGate(server_root, bearer_token=TOKEN)
        pipelines = {
            "slack": IntakePipeline(
                provider="slack",
                scheme=SignatureScheme(
                    name=slack_mod.SLACK_SCHEME_NAME, **slack_mod.SLACK_SIGNATURE
                ),
                secret=SECRETS["slack"],
                tenants=tenants,
                idempotency=IdempotencyStore(str(Path(tmp) / "deliveries.sqlite")),
                limiter=RateLimiter(),
                plan_gate=plan_gate,
                parse=slack_mod.parse_slack_events,
            )
        }
        app = make_service_app(pipelines=pipelines)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://connector.test") as connector, \
                   httpx.AsyncClient(base_url=f"{server_root}/api/v1",
                                     headers={"Authorization": f"Bearer {TOKEN}"}) as human:
            body = slack_body("a5-smoke-1")
            ts = str(int(time.time()))
            res = await connector.post("/webhooks/slack", content=body,
                                       headers=sign_payload("slack", body, ts))
            assert res.status_code == 202, res.text
            first = res.json()
            assert first["awaitingApproval"] is True and first["planId"].startswith("plan-")
            print(f"1. intake -> plan {first['planId']} awaiting human approval (202)")

            dup = await connector.post("/webhooks/slack", content=body,
                                       headers=sign_payload("slack", body, ts))
            assert dup.status_code == 200 and dup.json() == {"status": "duplicate"}, dup.text
            print("2. duplicate delivery -> 200 duplicate, no second plan")

            approve = await human.post(f"/orchestrator/plans/{first['planId']}/approve")
            assert approve.status_code == 200, approve.text
            run_id = approve.json()["runId"]
            again = await human.post(f"/orchestrator/plans/{first['planId']}/approve")
            assert again.status_code == 200 and again.json()["runId"] == run_id
            print(f"3. human approve -> run {run_id}; idempotent re-approve same run")

            plan = await human.get(f"/orchestrator/runs/{run_id}/plan")
            assert plan.status_code == 200, plan.text
            goal = plan.json()["goal"]
            assert "untrusted context, requires human plan approval" in goal
            assert "prod API 5xx spike" in goal
            print("4. approved plan goal carries the untrusted-content banner")

            status_res = await human.get(f"/orchestrator/runs/{run_id}/status")
            assert status_res.status_code == 200, status_res.text
            status = status_res.json()
            assert status["executing"] is False, status
            assert "running" not in status["state"], status
            assert status["planApproved"] is True, status
            print(f"5. run state after approve: {status} (approved, NOT executing)")

            for path in ("/plans/plan-1/approve", "/plans/plan-1/start", "/execute"):
                r = await connector.post(path, json={})
                assert r.status_code == 404, (path, r.status_code)
            print("6. connector exposes no approve/start/execute routes (404)")

        await plan_gate.aclose()
        print("SMOKE PASS — real plan gate integration verified")
        return 0
    finally:
        runner.terminate()
        try:
            runner.wait(timeout=10)
        except subprocess.TimeoutExpired:
            runner.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
