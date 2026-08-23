import asyncio
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated

import harness_contracts as contracts
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from harness_contracts import Principal as ContractsPrincipal
from harness_contracts import Role
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .audit import register_append_only_guard
from .auth import get_principal, hash_token, issue_otp, login_or_register, verify_otp
from .config import Settings
from .crypto import KeyVault
from .ephemeral import mint_ws_ticket, redeem_ws_ticket
from .eventbus import EventBus
from .models import AuthSession, TeamMember
from .models import Role as DbRole

LOGIN_RATE_LIMIT = 20  # attempts
LOGIN_RATE_WINDOW_S = 60
async def _issue_ws_ticket(app_state, principal, session_factory, project_id: str) -> dict:
    await _require_project_access(session_factory, principal, project_id)
    ticket, ttl = await mint_ws_ticket(session_factory, principal, project_id)
    return {"ticket": ticket, "expiresInSeconds": ttl}


def _login_rate_limited(app_state, client_ip: str) -> bool:
    """Per-app sliding window keyed by client IP (module-global would leak across
    apps and trip multi-tenant test suites sharing one localhost address)."""
    if not hasattr(app_state, "login_buckets"):
        app_state.login_buckets = defaultdict(deque)
    now = time.monotonic()
    bucket = app_state.login_buckets[client_ip]
    while bucket and now - bucket[0] > LOGIN_RATE_WINDOW_S:
        bucket.popleft()
    if len(bucket) >= LOGIN_RATE_LIMIT:
        return True
    bucket.append(now)
    return False


async def _require_project_access(
    session_factory: async_sessionmaker,
    principal: ContractsPrincipal,
    project_id: str,
) -> None:
    """Cross-tenant guard: a principal may only touch projects owned by its team."""
    from .models import Project

    async with session_factory() as db:
        row = await db.get(Project, project_id)
    if row is None or row.team_id != principal.teamId:
        raise HTTPException(status_code=403, detail="project not found for this team")


def make_app(session_factory, settings: Settings):
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    app = FastAPI(title="harness-identity", version="0.1.0")
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
    app.state.event_bus = EventBus(session_factory)
    # E-smtp: SMTP when configured, visible dev log printer otherwise.
    from .notifier import make_notifier

    app.state.notifier = make_notifier(settings)
    register_append_only_guard()

    # Wave9-E: baseline security headers (nosniff/DENY/referrer, HSTS behind
    # HARNESS_TLS_ENABLED, report-only CSP on HTML).
    from .security_headers import install_security_headers

    install_security_headers(app)

    @app.get("/health")
    async def health(request: Request, deep: int = 0):
        if not deep:
            return {"ok": True}
        from sqlalchemy import text

        try:
            factory: async_sessionmaker = request.app.state.session_factory
            async with factory() as db:
                await db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — health must answer, not stack-trace
            return JSONResponse(status_code=503, content={"ok": False, "db": "error"})
        return {"ok": True, "db": "ok"}

    app.include_router(router)

    # Wave9-C merge wiring: token-based team invites + members roster
    # (invites.py exposes its own APIRouter; legacy direct-add invite on the
    # main router lives at a different path — both coexist).
    from .invites import router as invites_router

    app.include_router(invites_router)
    return app


router = APIRouter(prefix=contracts.API_PREFIX)


async def _principal_from_token(
    session_factory: async_sessionmaker, token: str
) -> ContractsPrincipal | None:
    if not token:
        return None

    async with session_factory() as db:
        row = (
            await db.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        ).scalar_one_or_none()
        if row is None:
            return None
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
        if expires < datetime.now(UTC):
            return None
        member = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == row.team_id, TeamMember.user_id == row.user_id
                )
            )
        ).scalar_one_or_none()
        if member is None:
            return None
        return ContractsPrincipal(
            userId=row.user_id, teamId=row.team_id, role=DbRole(member.role)
        )


class LoginRequest(BaseModel):
    email: str
    teamId: str | None = None
    teamName: str | None = None


def _dev_notifier(email: str, code: str) -> None:
    # Dev delivery ONLY: prints so `docker logs` shows it regardless of log level.
    # Production must supply SMTP via the same hook (ledger: E-smtp).
    import sys

    print(f"HARNESS-OTP login code for {email}: {code}", file=sys.stderr, flush=True)


@router.post("/auth/request-login")
async def request_login(body: LoginRequest, request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    if _login_rate_limited(request.app.state, client_ip):
        raise HTTPException(status_code=429, detail="too many login attempts; slow down")
    settings: Settings = request.app.state.settings
    notify = getattr(request.app.state, "notifier", None) or _dev_notifier
    async with request.app.state.session_factory() as db:
        await issue_otp(db, settings, notify, body.email)
    return {"ok": True}


class VerifyOtpRequest(BaseModel):
    email: str
    code: str


@router.post("/auth/verify-otp")
async def verify_otp_route(body: VerifyOtpRequest, request: Request) -> dict:
    settings: Settings = request.app.state.settings
    async with request.app.state.session_factory() as db:
        token, principal = await verify_otp(db, settings, body.email, body.code)
    return {"token": token, "principal": principal.model_dump()}


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request) -> dict:
    settings: Settings = request.app.state.settings
    if getattr(settings, "require_ownership", False):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "OWNERSHIP_REQUIRED",
                    "message": (
                        "prove inbox ownership via /auth/request-login + /auth/verify-otp"
                    ),
                }
            },
        )
    client_ip = request.client.host if request.client else "unknown"
    if _login_rate_limited(request.app.state, client_ip):
        raise HTTPException(status_code=429, detail="too many login attempts; slow down")
    async with request.app.state.session_factory() as db:
        token, principal = await login_or_register(db, body.email, body.teamId, body.teamName)
    return {"token": token, "principal": principal.model_dump()}


@router.post("/auth/logout")
async def logout(
    request: Request, principal: Annotated[object, Depends(get_principal)]
) -> dict:
    """Revoke the presenting session immediately."""
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(AuthSession).where(AuthSession.token_hash == hash_token(token))
            )
        ).scalar_one_or_none()
        if row is not None:
            await db.delete(row)
            await db.commit()
    return {"ok": True}


@router.get("/me")
async def me(principal: Annotated[object, Depends(get_principal)]) -> dict:
    return principal.model_dump()


class InviteBody(BaseModel):
    email: str


class CreateProjectBody(BaseModel):
    name: str
    repoFullName: str | None = None


@router.post("/projects", status_code=201)
async def create_project(
    body: CreateProjectBody,
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
) -> dict:
    from .models import Project

    if not body.name.strip():
        raise HTTPException(status_code=422, detail="project name required")
    project = Project(
        team_id=principal.teamId, name=body.name.strip(), repo_full_name=body.repoFullName
    )
    async with request.app.state.session_factory() as db:
        db.add(project)
        await db.commit()
        await db.refresh(project)
        return {"id": project.id, "name": project.name, "repoFullName": project.repo_full_name}


@router.get("/projects")
async def list_projects(
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
    limit: int = 100,
) -> dict:
    from sqlalchemy import select

    from .models import Project

    limit = max(1, min(int(limit), 500))
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(Project)
                    .where(Project.team_id == principal.teamId)
                    .order_by(Project.created_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return {
            "items": [
                {"id": r.id, "name": r.name, "repoFullName": r.repo_full_name} for r in rows
            ]
        }


@router.post("/teams/{team_id}/seats/agent")
async def add_agent_seat(
    team_id: str,
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
) -> dict:
    if principal.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="admin only")
    if principal.teamId != team_id:
        raise HTTPException(status_code=403, detail="wrong team")
    member = TeamMember(
        team_id=team_id,
        user_id=None,
        display_name=f"agent-{principal.userId[:6]}",
        role=Role.ENGINEER.value,
    )
    async with request.app.state.session_factory() as db:
        db.add(member)
        await db.commit()
        await db.refresh(member)
        return {"seat": {"id": member.id, "displayName": member.display_name, "role": member.role}}


@router.post("/teams/{team_id}/members/invite")
async def invite_member(
    team_id: str,
    body: InviteBody,
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
) -> dict:
    """Admin invites a teammate by email (MVP: basic email invite; engineer role)."""
    from .models import User

    if principal.teamId != team_id or principal.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="admin of this team only")
    async with request.app.state.session_factory() as db:
        user = (
            await db.execute(
                select(User).where(User.email == body.email.lower().strip())
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(email=body.email.lower().strip(), name=body.email.split("@")[0])
            db.add(user)
            await db.flush()
        existing = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == team_id, TeamMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="already a member")
        member = TeamMember(
            team_id=team_id, user_id=user.id, display_name=user.name, role=Role.ENGINEER.value
        )
        db.add(member)
        await db.commit()
        return {"member": {"userId": user.id, "role": member.role}}


class WsTicketRequest(BaseModel):
    projectId: str


@router.post("/ws-ticket")
async def issue_ws_ticket(
    body: WsTicketRequest,
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
) -> dict:
    """Mint a one-time, 30s, project-bound ticket so the WS upgrade never needs
    a long-lived bearer token in a URL (H8)."""
    return await _issue_ws_ticket(
        request.app.state, principal, request.app.state.session_factory, body.projectId
    )


@router.websocket("/ws/{project_id}")
async def project_ws(
    websocket: WebSocket, project_id: str, token: str = "", afterSeq: int = 0,
    ticket: str = "",
):
    if ticket:
        # One-time redemption binds the principal AND the project in one step.
        principal = await redeem_ws_ticket(
            websocket.app.state.session_factory, ticket, project_id
        )
    elif token:
        principal = await _principal_from_token(websocket.app.state.session_factory, token)
    else:
        header = websocket.headers.get("authorization", "")
        principal = (
            await _principal_from_token(
                websocket.app.state.session_factory, header.removeprefix("Bearer ").strip()
            )
            if header.startswith("Bearer ")
            else None
        )
    if principal is None:
        await websocket.close(code=4401)
        return
    try:
        await _require_project_access(
            websocket.app.state.session_factory, principal, project_id
        )
    except HTTPException:
        await websocket.close(code=4403)
        return

    bus: EventBus = websocket.app.state.event_bus
    await websocket.accept()

    # Subscribe BEFORE replaying: live events published while we send history are
    # buffered in the queue instead of lost. Duplicates from the overlap window are
    # removed client-side by seq dedup (section7 mergeEvents).
    queue = await bus.subscribe(project_id)

    async def _watch_disconnect() -> None:
        # A silent client never triggers a send, so receiving here is the ONLY way
        # to observe its disconnect. Racing this against queue.get() is what lets
        # the handler release dead subscriptions instead of parking forever.
        while True:
            await websocket.receive()

    watcher = asyncio.ensure_future(_watch_disconnect())
    try:
        last_seq = int(afterSeq)
        for envelope in await bus.replay(project_id, last_seq):
            await websocket.send_json(envelope.model_dump())
            last_seq = max(last_seq, envelope.seq)
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _pending = await asyncio.wait(
                {getter, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done:
                # Client disconnected (or sent anything — clients are read-only).
                getter.cancel()
                break
            envelope = getter.result()
            # Overflow detection via bus-side flag ONLY. Never seq arithmetic:
            # agent_events.seq is table-global, so per-project jumps are normal
            # whenever other projects publish.
            if getattr(queue, "overflowed", False):
                await websocket.close(code=1013)
                break
            await websocket.send_json(envelope.model_dump())
            last_seq = envelope.seq
    except WebSocketDisconnect:
        pass
    finally:
        watcher.cancel()
        await bus.unsubscribe(project_id, queue)


@router.get("/projects/{project_id}/events/replay")
async def replay_events(
    project_id: str,
    request: Request,
    after_seq: int = 0,
    limit: int = 0,
    principal: Annotated[object, Depends(get_principal)] = None,
) -> dict:
    await _require_project_access(request.app.state.session_factory, principal, project_id)
    bus: EventBus = request.app.state.event_bus
    envelopes = await bus.replay(project_id, after_seq, limit or 500)
    return {
        "items": [e.model_dump() for e in envelopes],
        "nextCursor": envelopes[-1].seq if envelopes else after_seq,
    }
