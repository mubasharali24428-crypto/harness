import time
import uuid
from typing import Annotated

import harness_contracts as contracts
import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from harness_contracts import KeyVault, Principal, make_bearer_dep
from pydantic import BaseModel
from sqlalchemy import text

from .client import GITHUB_WEB, MergeGuardViolation
from .config import Settings

router = APIRouter(prefix=contracts.API_PREFIX)
bearer_scheme = HTTPBearer(auto_error=False)

OAUTH_STATE_TTL = 600


def make_app(session_factory, settings: Settings):
    app = FastAPI(title="harness-github", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.vault = KeyVault(settings.master_key)
    app.state.transport = None  # injectable httpx transport for tests
    app.state.oauth_states: dict[str, tuple[str, float]] = {}  # state -> (user_id, expiry)
    get_principal = make_bearer_dep(session_factory)
    app.state.get_principal = get_principal

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.include_router(router)
    return app


async def service_dep(request: Request):
    from .service import GitHubService

    get_principal = request.app.state.get_principal
    principal = await get_principal(request)
    db = request.app.state.session_factory()
    try:
        yield GitHubService(db, request.app.state.vault, request.app.state.transport), principal
    finally:
        await db.close()


ServiceDep = Annotated[tuple, Depends(service_dep)]


class ConnectBody(BaseModel):
    projectId: str
    repoFullName: str


@router.get("/health-extended")
async def health_extended() -> dict:
    return {"github": "ready"}


@router.post("/github/oauth/url")
async def oauth_url(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    if not settings.github_client_id:
        raise HTTPException(status_code=503, detail="GitHub OAuth not configured")
    get_principal = request.app.state.get_principal
    principal: Principal = await get_principal(request)
    state = uuid.uuid4().hex
    request.app.state.oauth_states[state] = (
        principal.userId,
        time.time() + OAUTH_STATE_TTL,
    )
    url = (
        f"{GITHUB_WEB}/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={settings.github_redirect_uri}"
        "&scope=repo%20read:user"
        f"&state={state}"
    )
    return {"authorizeUrl": url}


@router.get("/github/oauth/callback")
async def oauth_callback(code: str, state: str, request: Request) -> dict:
    settings: Settings = request.app.state.settings
    entry = request.app.state.oauth_states.pop(state, None)
    if entry is None or entry[1] < time.time():
        raise HTTPException(status_code=400, detail="invalid or expired OAuth state")
    user_id = entry[0]

    async with httpx.AsyncClient(timeout=30, transport=request.app.state.transport) as client:
        token_resp = await client.post(
            f"{GITHUB_WEB}/login/oauth/access_token",
            json={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.github_redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="token exchange failed")
        from .client import GitHubClient

        user = await GitHubClient(access_token, request.app.state.transport).get_user()

    encrypted = request.app.state.vault.encrypt(access_token)
    async with request.app.state.session_factory() as db:
        await db.execute(
            text("DELETE FROM github_connections WHERE user_id = :u"),
            {"u": user_id},
        )
        await db.execute(
            text(
                """
                INSERT INTO github_connections (id, user_id, github_login,
                    access_token_encrypted, scopes, created_at)
                VALUES (:id, :u, :l, :t, :s, CURRENT_TIMESTAMP)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "u": user_id,
                "l": user["login"],
                "t": encrypted,
                "s": '["repo","read:user"]',
            },
        )
        await db.commit()
    return {"connected": True, "login": user["login"]}


@router.post("/projects/{project_id}/repo/connect")
async def connect_repo(project_id: str, body: ConnectBody, service: ServiceDep) -> dict:
    svc, principal = service
    try:
        result = await svc.connectRepo(project_id, principal.teamId, body.repoFullName)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.get("/projects/{project_id}/tree")
async def read_tree(project_id: str, ref: str, service: ServiceDep) -> dict:
    svc, principal = service
    return {"items": await svc.readRepoTree(principal.userId, project_id, ref)}


@router.get("/projects/{project_id}/file")
async def read_file(project_id: str, ref: str, path: str, service: ServiceDep) -> dict:
    svc, principal = service
    return await svc.readFile(principal.userId, project_id, ref, path)


@router.post("/projects/{project_id}/branches")
async def create_branch(project_id: str, baseRef: str, newBranch: str, service: ServiceDep) -> dict:
    svc, principal = service
    return await svc.createBranch(principal.userId, project_id, baseRef, newBranch)


class CommitBody(BaseModel):
    branch: str
    message: str
    files: list[dict]


@router.post("/projects/{project_id}/commits")
async def commit_files(project_id: str, body: CommitBody, service: ServiceDep) -> dict:
    svc, principal = service
    try:
        return await svc.commitFiles(
            principal.userId, project_id, body.branch, body.message, body.files
        )
    except MergeGuardViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class DraftPRBody(BaseModel):
    branch: str
    baseBranch: str
    title: str
    bodyMd: str


@router.post("/projects/{project_id}/pulls/draft")
async def open_draft_pr(project_id: str, body: DraftPRBody, service: ServiceDep) -> dict:
    svc, principal = service
    return await svc.openDraftPR(
        principal.userId, project_id, body.branch, body.baseBranch, body.title, body.bodyMd
    )


@router.get("/projects/{project_id}/pulls/{pr_number}")
async def pr_status(project_id: str, pr_number: int, service: ServiceDep) -> dict:
    svc, principal = service
    return await svc.getPRStatus(principal.userId, project_id, pr_number)


@router.get("/projects/{project_id}/pulls/{pr_number}/diff")
async def pr_diff(project_id: str, pr_number: int, service: ServiceDep) -> dict:
    svc, principal = service
    return await svc.getPRDiff(principal.userId, project_id, pr_number)


