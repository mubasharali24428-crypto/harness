"""Test harness: real service + app wired to an in-process fake of the baseline upstreams.

The fake stands in for Section 1 identity and Section 5 sandbox ONLY at the HTTP
seam; the service, store, workspace and git plumbing under test are production code.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException, Request

from harness_devworkflow.app import create_app
from harness_devworkflow.config import Config

TOKENS = {
    "tok-eng-a": {"userId": "u-a", "teamId": "team-a", "role": "engineer"},
    "tok-admin-a": {"userId": "u-adm", "teamId": "team-a", "role": "admin"},
    "tok-view-a": {"userId": "u-v", "teamId": "team-a", "role": "viewer"},
    "tok-guest-a": {"userId": "u-g", "teamId": "team-a", "role": "guest"},
    "tok-eng-b": {"userId": "u-b", "teamId": "team-b", "role": "engineer"},
}
PROJECTS = {"team-a": [{"id": "proj-a", "teamId": "team-a"}], "team-b": [{"id": "proj-b"}]}


class FakeUpstream:
    def __init__(self) -> None:
        self.edits: list[dict[str, Any]] = []
        self.execs: list[str] = []
        self.exec_result: dict[str, Any] = {
            "status": "passed",
            "summary": "ok",
            "exit_code": 0,
            "diagnostics": [],
        }
        self.fail_edit_for: str | None = None
        self.app = self._build()

    def _principal(self, authorization: str | None) -> dict[str, Any]:
        token = (authorization or "").removeprefix("Bearer ").strip()
        who = TOKENS.get(token)
        if who is None:
            raise HTTPException(401, {"error": {"code": "UNAUTHORIZED", "message": "bad token"}})
        return who

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/api/v1/me")
        async def me(authorization: str | None = Header(default=None)):
            return self._principal(authorization)

        @app.get("/api/v1/projects")
        async def projects(authorization: str | None = Header(default=None)):
            who = self._principal(authorization)
            return {"items": PROJECTS[who["teamId"]]}

        @app.post("/api/v1/sandbox/tasks")
        async def create_task(request: Request, authorization: str | None = Header(default=None)):
            self._principal(authorization)
            return await request.json()

        @app.post("/api/v1/sandbox/tasks/{task_id}/edits")
        async def edits(task_id: str, request: Request, authorization: str | None = Header(None)):
            self._principal(authorization)
            body = await request.json()
            if self.fail_edit_for and body["path"] == self.fail_edit_for:
                raise HTTPException(
                    403, {"error": {"code": "FORBIDDEN_PATH", "message": "blocked by S5"}}
                )
            self.edits.append(body)
            return {"ok": True}

        @app.post("/api/v1/sandbox/tasks/{task_id}/exec")
        async def exec_(task_id: str, request: Request, authorization: str | None = Header(None)):
            self._principal(authorization)
            body = await request.json()
            self.execs.append(body["command"])
            return self.exec_result

        @app.get("/api/v1/sandbox/{project_id}/checkpoints")
        async def checkpoints(project_id: str, authorization: str | None = Header(None)):
            self._principal(authorization)
            return {"items": [{"sha": "cp1"}]}

        return app


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


def make_repo(root: Path, files: dict[str, str]) -> Path:
    repo = root / "origin"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
        "HOME": str(root),
    }
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, check=True, env=env, capture_output=True
    )
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, env=env, capture_output=True
    )
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    body = "".join(f"line {i}\n" for i in range(1, 41))
    return make_repo(
        tmp_path, {"src/app.py": body, "README.md": "hello\n", "secrets/key.txt": "s3cret\n"}
    )


@pytest.fixture
async def client(tmp_path: Path, upstream: FakeUpstream, repo: Path):
    data = tmp_path / "data"
    config = Config(
        identity_base="http://up",
        sandbox_base="http://up",
        orchestrator_base=None,
        context_base=None,
        data_dir=data,
        repo_roots=(str(tmp_path),),
    )
    from harness_devworkflow.app import build_service

    up_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream.app))
    service = build_service(config, up_http)
    app = create_app(config, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dw") as c:
        c.service = service  # type: ignore[attr-defined]
        c.repo = repo  # type: ignore[attr-defined]
        yield c


def auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


P = "/api/v1/developer-workflow"


async def new_session(client, token="tok-eng-a", **kw) -> str:
    body = {"projectId": "proj-a", "repoRef": str(client.repo), "branch": "main", **kw}
    r = await client.post(f"{P}/code-sessions", json=body, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["sessionId"]
