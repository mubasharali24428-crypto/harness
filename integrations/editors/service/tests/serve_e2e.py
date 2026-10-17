"""Boots the REAL developer-workflow service (fake identity/sandbox upstream only) on a free
port, seeds one session + a two-hunk change set, prints one JSON line, then serves forever.
Used by section7/e2e-review (real browser <-> real service)."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx
import uvicorn
from conftest import FakeUpstream, make_repo

from harness_devworkflow.app import build_service, create_app
from harness_devworkflow.config import Config

P = "/api/v1/developer-workflow"


async def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="dw-e2e-"))
    body = "".join(f"line {i}\n" for i in range(1, 41))
    repo = make_repo(root, {"src/app.py": body})
    cfg = Config(
        identity_base="http://up",
        sandbox_base="http://up",
        orchestrator_base=None,
        context_base=None,
        data_dir=root / "data",
        allowed_origins=("*",),
        repo_roots=(str(root),),
    )
    up = FakeUpstream()
    svc = build_service(cfg, httpx.AsyncClient(transport=httpx.ASGITransport(app=up.app)))
    app = create_app(cfg, svc)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x") as c:
        h = {"authorization": "Bearer tok-eng-a"}
        s = (
            await c.post(
                f"{P}/code-sessions", headers=h, json={"projectId": "proj-a", "repoRef": str(repo)}
            )
        ).json()["sessionId"]
        new = body.replace("line 2\n", "TWO\n").replace("line 38\n", "THIRTY-EIGHT\n")
        cs = (
            await c.post(
                f"{P}/code-sessions/{s}/change-sets",
                headers=h,
                json={
                    "title": "Tweak two lines",
                    "edits": [{"path": "src/app.py", "newContent": new}],
                },
            )
        ).json()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    print(
        json.dumps(
            {
                "base": f"http://127.0.0.1:{port}",
                "changeSetId": cs["changeSetId"],
                "sessionId": s,
                "file": str(root / "data" / "sessions" / s / "workspace" / "src" / "app.py"),
            }
        ),
        flush=True,
    )
    await task


asyncio.run(main())
