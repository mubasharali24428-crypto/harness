"""Local fake providers + a contract-true plan-gate stub (NO real services).

Every fake mirrors the documented provider API surface used by the connectors:
Slack chat.postMessage, Linear GraphQL, Jira REST v3 comments, Teams incoming
webhook, and the Section 4 plan-gate contract (POST /api/v1/orchestrator/plans
-> 201 {planId, plan}). The plan gate records goals and can simulate upstream
outages (500s) for the outage-recovery evidence."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


@contextlib.asynccontextmanager
async def _serve(app: Starlette) -> AsyncIterator[str]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    uv = uvicorn.Server(config)
    task = asyncio.create_task(uv.serve())
    try:
        for _ in range(250):
            if uv.started:
                break
            await asyncio.sleep(0.02)
        else:
            raise RuntimeError("fake provider did not start")
        port = uv.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        uv.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=10)


class FakePlanGate:
    def __init__(self) -> None:
        self.goals: list[str] = []
        self.fail_next = 0  # when > 0: answer 500 to that many propose calls
        self.bearer_required = "gate-token"

    async def propose(self, request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self.bearer_required}":
            return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "no"}}, status_code=401)
        body = await request.json()
        if self.fail_next > 0:
            self.fail_next -= 1
            return JSONResponse({"detail": "gate down"}, status_code=500)
        self.goals.append(body.get("goal", ""))
        plan_id = f"plan-{uuid.uuid4().hex[:10]}"
        return JSONResponse(
            {"planId": plan_id, "plan": {"goal": body.get("goal"), "status": "proposed"}},
            status_code=201,
        )

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/api/v1/orchestrator/plans", self.propose, methods=["POST"])])


@contextlib.asynccontextmanager
async def fake_plan_gate() -> AsyncIterator[tuple[str, FakePlanGate]]:
    gate = FakePlanGate()
    async with _serve(gate.app()) as base:
        yield base, gate


class FakeSlack:
    def __init__(self, bot_token: str = "xoxb-test-token") -> None:
        self.bot_token = bot_token
        self.posts: list[dict] = []

    async def chat_post_message(self, request: Request) -> JSONResponse:
        if request.headers.get("authorization") != f"Bearer {self.bot_token}":
            return JSONResponse({"ok": False, "error": "invalid_auth"}, status_code=401)
        self.posts.append(await request.json())
        return JSONResponse({"ok": True, "ts": f"{len(self.posts)}.001"})

    def app(self) -> Starlette:
        return Starlette(
            routes=[Route("/api/chat.postMessage", self.chat_post_message, methods=["POST"])]
        )


@contextlib.asynccontextmanager
async def fake_slack() -> AsyncIterator[tuple[str, FakeSlack]]:
    fake = FakeSlack()
    async with _serve(fake.app()) as base:
        yield base, fake


class FakeLinear:
    def __init__(self, api_key: str = "lin_test_key") -> None:
        self.api_key = api_key
        self.comments: list[dict] = []

    async def graphql(self, request: Request) -> JSONResponse:
        if request.headers.get("authorization") != self.api_key:
            return JSONResponse({"errors": [{"message": "unauthorized"}]}, status_code=401)
        body = await request.json()
        variables = body.get("variables", {}).get("input", {})
        self.comments.append(variables)
        return JSONResponse(
            {
                "data": {
                    "commentCreate": {
                        "success": True,
                        "comment": {"id": f"c{len(self.comments)}", "url": "https://linear/c1"},
                    }
                }
            }
        )

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/graphql", self.graphql, methods=["POST"])])


@contextlib.asynccontextmanager
async def fake_linear() -> AsyncIterator[tuple[str, FakeLinear]]:
    fake = FakeLinear()
    async with _serve(fake.app()) as base:
        yield base, fake


class FakeJira:
    def __init__(self, email: str = "bot@example.com", token: str = "jira_api_token") -> None:
        self.email = email
        self.token = token
        self.comments: list[dict] = []

    async def post_comment(self, request: Request) -> JSONResponse:
        import base64

        expected = base64.b64encode(f"{self.email}:{self.token}".encode()).decode()
        if request.headers.get("authorization") != f"Basic {expected}":
            return JSONResponse({"errorMessages": ["unauthorized"]}, status_code=401)
        body = await request.json()
        issue = request.path_params["issue_key"]
        self.comments.append({"issue": issue, "body": body})
        return JSONResponse({"id": f"1000{len(self.comments)}"}, status_code=201)

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/rest/api/3/issue/{issue_key}/comment", self.post_comment, methods=["POST"])
            ]
        )


@contextlib.asynccontextmanager
async def fake_jira() -> AsyncIterator[tuple[str, FakeJira]]:
    fake = FakeJira()
    async with _serve(fake.app()) as base:
        yield base, fake


class FakeTeams:
    def __init__(self) -> None:
        self.cards: list[dict] = []

    async def receive(self, request: Request) -> JSONResponse:
        self.cards.append(await request.json())
        return JSONResponse({"ok": True})

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/webhook", self.receive, methods=["POST"])])


@contextlib.asynccontextmanager
async def fake_teams() -> AsyncIterator[tuple[str, FakeTeams]]:
    fake = FakeTeams()
    async with _serve(fake.app()) as base:
        yield base, fake
