"""Shared test plumbing: principals, a REAL fake orchestrator (plan/approve
routes), a stateful webhook receiver, and Slack action-body helpers."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import uvicorn
from harness_telemetry import Archive, build_records, fixtures, parse_cost, parse_stream
from harness_telemetry.events import scrub_text
from harness_workflow_connectors.core import SignatureScheme, sign
from harness_workflow_connectors.slack import SLACK_SIGNATURE
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

SLACK_SCHEME = SignatureScheme(name="slack-v0", **SLACK_SIGNATURE)


@dataclass
class Principal:
    userId: str
    teamId: str
    role: str


def principal_dep(mapping: dict[str, Principal]):
    def resolve(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return mapping.get(token)

    return resolve


def fixture_events_costs():
    return (
        parse_stream(fixtures.events()),
        [parse_cost(c) for c in fixtures.costs()],
    )


def fixture_records():
    events, costs = fixture_events_costs()
    return build_records(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)


def fixture_archive(path: str) -> Archive:
    events, costs = fixture_events_costs()
    archive = Archive(path)
    archive.ingest(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
    return archive


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def _serve(app) -> AsyncIterator[tuple[str, uvicorn.Server]]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        import asyncio

        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}", server
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@contextlib.asynccontextmanager
async def fake_orchestrator() -> AsyncIterator[tuple[str, dict]]:
    """Real HTTP orchestrator subset: propose + idempotent approve."""
    state: dict = {"plans": {}, "approve_calls": [], "plan_seq": 0, "run_seq": 0}

    async def propose(request: Request) -> JSONResponse:
        state["plan_seq"] += 1
        plan_id = f"plan-{state['plan_seq']}"
        body = await request.json()
        state["plans"][plan_id] = {"goal": body.get("goal", ""), "runId": None}
        return JSONResponse({"planId": plan_id}, status_code=202)

    async def approve(request: Request) -> JSONResponse:
        plan_id = request.path_params["planId"]
        state["approve_calls"].append(plan_id)
        plan = state["plans"].get(plan_id)
        if plan is None:
            # The real route 404s unknown plans; the double auto-registers so
            # handler tests do not need to replay a propose first.
            plan = state["plans"][plan_id] = {"goal": "", "runId": None}
        if plan["runId"] is None:
            state["run_seq"] += 1
            plan["runId"] = f"run-{state['run_seq']}"
        return JSONResponse({"runId": plan["runId"]})

    async def status(request: Request) -> JSONResponse:
        run_id = request.path_params["runId"]
        return JSONResponse({"runId": run_id, "planApproved": True, "executing": False})

    app = Starlette(routes=[
        Route("/api/v1/orchestrator/plans", propose, methods=["POST"]),
        Route("/api/v1/orchestrator/plans/{planId}/approve", approve, methods=["POST"]),
        Route("/api/v1/orchestrator/runs/{runId}/status", status),
    ])
    async with _serve(app) as (base, _server):
        yield base, state


@contextlib.asynccontextmanager
async def fake_receiver(codes: list[int] | None = None) -> AsyncIterator[tuple[str, dict]]:
    """Stateful webhook receiver: pops the next scripted status code (200 when
    exhausted), captures every request, and dedupes deliveryIds."""
    state: dict = {"received": [], "seen": set(), "codes": list(codes or [])}

    async def receive(request: Request) -> JSONResponse:
        body = await request.body()
        delivery_id = request.headers.get("x-harness-delivery", "-")
        first = delivery_id not in state["seen"]
        state["seen"].add(delivery_id)
        state["received"].append(
            {"headers": dict(request.headers), "body": body, "firstSeen": first}
        )
        code = state["codes"].pop(0) if state["codes"] else 200
        return JSONResponse({"ok": code < 300}, status_code=code)

    app = Starlette(routes=[Route("/hook", receive, methods=["POST"])])
    async with _serve(app) as (base, _server):
        yield f"{base}/hook", state


def slack_action_body(
    *, signing_secret: str, action_id: str, token: str, user: str = "U123",
    ts: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    payload = json.dumps(
        {
            "type": "block_actions",
            "user": {"id": user},
            "actions": [{"action_id": action_id, "value": token}],
        }
    )
    raw = urlencode({"payload": payload}).encode()
    ts = ts or str(int(time.time()))
    signature = sign(SLACK_SCHEME, signing_secret, raw, ts)
    headers = {
        "X-Slack-Signature": signature,
        "X-Slack-Request-Timestamp": ts,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return raw, headers


def scrubbed(value: str) -> str:
    return scrub_text(value)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=5.0)
