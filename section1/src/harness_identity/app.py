import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated, Any

import harness_contracts as contracts
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from harness_contracts import Principal as ContractsPrincipal
from harness_contracts import Role, error_body
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .audit import register_append_only_guard
from .auth import get_principal, hash_token, issue_otp, login_or_register, verify_otp
from .config import Settings
from .crypto import KeyVault
from .ephemeral import (
    FINGERPRINT_HEX_LEN,
    compute_client_fingerprint,
    mint_ws_ticket,
    redeem_ws_ticket,
)
from .eventbus import EventBus, ReplayPage
from .limits import (
    LOGIN_EMAIL_MAX,
    LOGIN_TEAM_ID_MAX,
    LOGIN_TEAM_NAME_MAX,
    OTP_CODE_MAX,
    PROJECT_NAME_MAX,
    PROJECT_REPO_FULL_NAME_MAX,
    WS_TICKET_PROJECT_ID_MAX,
)
from .models import AuthSession, TeamMember
from .models import Role as DbRole
from .ratelimit import effective_client_ip

LOGIN_RATE_LIMIT = 20  # attempts
LOGIN_RATE_WINDOW_S = 60

logger = logging.getLogger(__name__)


async def _issue_ws_ticket(
    app_state, principal, session_factory, project_id: str,
    user_agent: str = "", client_ip: str = "",
) -> dict:
    await _require_project_access(session_factory, principal, project_id)
    ticket, ttl = await mint_ws_ticket(
        session_factory, principal, project_id,
        user_agent=user_agent, client_ip=client_ip,
    )
    return {
        "ticket": ticket,
        "expiresInSeconds": ttl,
        # M18-C5 (MAC2-F5): echo the stored binding so the upgrading request
        # can present it as X-WS-Fingerprint / ?fingerprint=.
        "fingerprint": compute_client_fingerprint(user_agent, client_ip),
    }


def _client_ip(request: Request) -> str:
    """Client identity for WS-ticket fingerprint binding (M17-B4 policy).

    X-Forwarded-For is trusted ONLY when the direct peer sits inside
    ``settings.trusted_proxy_cidrs`` (M17-B4 / MAC2-F8); otherwise the peer
    address is used verbatim and a client-supplied XFF is ignored — exactly
    so it can neither forge fingerprints at mint nor break them at upgrade.
    Behind a reverse proxy the mint and upgrade peers are both the proxy, so
    the binding still holds either way (it just binds to the proxy).
    """
    settings: Settings = request.app.state.settings
    return effective_client_ip(
        request.headers,
        request.client.host if request.client else "",
        settings.trusted_proxy_cidrs,
    )


def _login_rate_limited(app_state, client_ip: str) -> bool:
    """Per-app sliding window keyed by client IP (module-global would leak across
    apps and trip multi-tenant test suites sharing one localhost address).

    M17-B4 (MAC2-F8): ``client_ip`` is the NORMALIZED identity (IPv6 → /64,
    XFF only through trusted proxies), so rotating addresses inside one IPv6
    block cannot mint fresh login budgets here either — same policy as the
    DB-backed limiter in ratelimit.py.
    """
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
    """Cross-tenant guard: a principal may only touch projects owned by its team.

    M13-G5a no-oracle law: a foreign project answers EXACTLY what an unknown
    id answers — same status, same error code, same message — so ids cannot be
    probed for existence across teams. Auth failures stay 401 at get_principal;
    this guard only ever speaks 404.
    """
    from .models import Project

    async with session_factory() as db:
        row = await db.get(Project, project_id)
    if row is None or row.team_id != principal.teamId:
        raise HTTPException(
            status_code=404,
            detail=error_body("NOT_FOUND", "project not found"),
        )


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

    # M13-G5a gate repair: wire lane F1's error envelope so EVERY error path —
    # including 401s from get_principal and route HTTPExceptions raised with a
    # bare string detail (e.g. "missing bearer token") — answers the frozen
    # contract shape {"error": {code, message}} (+ legacy detail mirror).
    # Previously only tests that installed it manually got the envelope.
    from .envelope_middleware import install_error_envelope

    install_error_envelope(app)

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

    # Mission10-A1 merge wiring: BYO keys CRUD (encrypted at rest, hint-only
    # exposure, soft revoke).
    from .api_keys import router as api_keys_router

    app.include_router(api_keys_router)

    # Mission10-B1 merge wiring: billing state/plan behind HARNESS_BILLING.
    from .billing import router as billing_router
    from .stripe_adapter import make_client_from_env
    from .stripe_adapter import router as stripe_adapter

    app.include_router(billing_router)

    # Mission22-M3 merge wiring: LIVE Stripe adapter + checkout/webhook routes
    # behind the SAME flag. With HARNESS_BILLING unset this injects nothing —
    # the routes 503 honestly and the M10 behavior is bit-for-bit unchanged.
    stripe_client = make_client_from_env()
    if stripe_client is not None:
        app.state.stripe_client = stripe_client
        app.include_router(stripe_adapter)

    # Mission12-E3a merge wiring: roster removal/role-change (W2-C1 server).
    from .teams_api import router as teams_api_router

    app.include_router(teams_api_router)

    # Mission12-G10 merge wiring: user-authored project rules CRUD
    # (.cursorrules equivalent; advisory prompt block via S4 rules_loader).
    from .rules_api import router as rules_router

    app.include_router(rules_router)

    # M15-G6b: per-team settings CRUD (reviewer thresholds, admin-gated).
    from .settings_api import router as settings_api_router

    app.include_router(settings_api_router)

    # M15-W5-S1 merge wiring: browser CSP violation collector (unauthed,
    # size-capped, allow-listed logging — see csp_report.py).
    from .csp_report import router as csp_report_router

    app.include_router(csp_report_router)
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
    # M12-G3: bounded free-text (limits are additive; see harness_identity.limits).
    email: str = Field(max_length=LOGIN_EMAIL_MAX)
    teamId: str | None = Field(default=None, max_length=LOGIN_TEAM_ID_MAX)
    teamName: str | None = Field(default=None, max_length=LOGIN_TEAM_NAME_MAX)


class PrincipalResponse(BaseModel):
    """The authenticated caller (camelCase wire shape per api-conventions.md)."""

    userId: str
    teamId: str
    role: str


class SessionResponse(BaseModel):
    """M13-G5a OpenAPI typing island: the auth session minted on login."""

    token: str
    principal: PrincipalResponse


class OkResponse(BaseModel):
    ok: bool


class ErrorDetail(BaseModel):
    """One error entry of the FROZEN contract envelope (ERROR_BODY_EXAMPLE)."""

    code: str
    message: str


class ErrorEnvelope(BaseModel):
    """M13-G5a: the frozen contract error shape for OpenAPI responses={} docs."""

    error: ErrorDetail


def _dev_notifier(email: str, code: str) -> None:
    # Dev delivery ONLY: prints so `docker logs` shows it regardless of log level.
    # Production must supply SMTP via the same hook (ledger: E-smtp).
    import sys

    print(f"HARNESS-OTP login code for {email}: {code}", file=sys.stderr, flush=True)


@router.post("/auth/request-login", response_model=OkResponse)
async def request_login(body: LoginRequest, request: Request) -> dict:
    client_ip = (
        effective_client_ip(
            request.headers,
            request.client.host if request.client else "",
            request.app.state.settings.trusted_proxy_cidrs,
        )
        if request.client
        else "unknown"
    )
    if _login_rate_limited(request.app.state, client_ip):
        raise HTTPException(status_code=429, detail="too many login attempts; slow down")
    settings: Settings = request.app.state.settings
    notify = getattr(request.app.state, "notifier", None) or _dev_notifier
    async with request.app.state.session_factory() as db:
        await issue_otp(db, settings, notify, body.email)
    return {"ok": True}


class VerifyOtpRequest(BaseModel):
    # M12-G3: bounded fields (limits are additive; see harness_identity.limits).
    email: str = Field(max_length=LOGIN_EMAIL_MAX)
    code: str = Field(max_length=OTP_CODE_MAX)


@router.post(
    "/auth/verify-otp",
    response_model=SessionResponse,
    responses={
        401: {"description": "OTP invalid, expired or already redeemed", "model": ErrorEnvelope},
    },
)
async def verify_otp_route(body: VerifyOtpRequest, request: Request) -> dict:
    settings: Settings = request.app.state.settings
    async with request.app.state.session_factory() as db:
        token, principal = await verify_otp(db, settings, body.email, body.code)
    return {"token": token, "principal": principal.model_dump()}


@router.post(
    "/auth/login",
    response_model=SessionResponse,
    responses={
        401: {"description": "bad or missing credentials", "model": ErrorEnvelope},
        403: {"description": "ownership proof required (H2r)", "model": ErrorEnvelope},
        429: {"description": "login rate limit exceeded", "model": ErrorEnvelope},
    },
)
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
    client_ip = (
        effective_client_ip(
            request.headers,
            request.client.host if request.client else "",
            request.app.state.settings.trusted_proxy_cidrs,
        )
        if request.client
        else "unknown"
    )
    if _login_rate_limited(request.app.state, client_ip):
        raise HTTPException(status_code=429, detail="too many login attempts; slow down")
    async with request.app.state.session_factory() as db:
        token, principal = await login_or_register(db, body.email, body.teamId, body.teamName)
    return {"token": token, "principal": principal.model_dump()}


@router.post("/auth/logout", response_model=OkResponse)
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


@router.get("/me", response_model=PrincipalResponse, responses={401: {"model": ErrorEnvelope}})
async def me(principal: Annotated[object, Depends(get_principal)]) -> dict:
    return principal.model_dump()


class ProjectOut(BaseModel):
    """M13-G5a typing island: a project row on the wire (camelCase)."""

    id: str
    name: str
    repoFullName: str | None = None


class ProjectListResponse(BaseModel):
    items: list[ProjectOut]


class ProjectCreatedResponse(ProjectOut):
    """201 mint view of POST /projects (same fields as the projection)."""


class WsTicketResponse(BaseModel):
    ticket: str
    expiresInSeconds: int
    # M18-C5 (MAC2-F5): the exact client-binding value this ticket was minted
    # with. Since X-WS-Fingerprint presentation is now REQUIRED at upgrade,
    # non-browser callers need the authoritative value to present; browser
    # clients keep their UA-only local derivation (useEventStream.ts).
    fingerprint: str


class EventEnvelopeOut(BaseModel):
    """M13-G5a typing island: the FROZEN contracts EventEnvelope on the wire."""

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: str
    seq: int
    projectId: str
    runId: str | None = None


class EventReplayResponse(BaseModel):
    items: list[EventEnvelopeOut]
    nextCursor: int
    # M16-X3 (additive, optional): present only when the requested window was
    # capped and the NEWEST-N slice was returned. oldestSeqSent is the lowest
    # seq actually sent; anything below it in the gap was NOT delivered.
    replayTruncated: bool | None = None
    oldestSeqSent: int | None = None


class CreateProjectBody(BaseModel):
    # M12-G3: bounded free-text (limits are additive; see harness_identity.limits).
    name: str = Field(max_length=PROJECT_NAME_MAX)
    repoFullName: str | None = Field(default=None, max_length=PROJECT_REPO_FULL_NAME_MAX)


@router.post(
    "/projects",
    status_code=201,
    response_model=ProjectCreatedResponse,
    responses={401: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
)
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


@router.get(
    "/projects",
    response_model=ProjectListResponse,
    responses={401: {"model": ErrorEnvelope}},
)
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
        # M13-G5a no-oracle law: a foreign admin probing this path gets exactly
        # what an unknown team id would produce — never a team-existence signal.
        raise HTTPException(status_code=404, detail="team not found")
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


# C-001 (M11-W1-A1): the legacy direct-add invite endpoint was REMOVED —
# it attached any pre-created user to a team as ENGINEER with no consent
# (cross-team account injection). Team joining goes exclusively through the
# token flow in invites.py (mint → redeem), which is audited via audit_emit.


class WsTicketRequest(BaseModel):
    # M12-G3: bounded ids (limits are additive; see harness_identity.limits).
    projectId: str = Field(max_length=WS_TICKET_PROJECT_ID_MAX)


@router.post(
    "/ws-ticket",
    response_model=WsTicketResponse,
    responses={401: {"model": ErrorEnvelope}, 404: {"model": ErrorEnvelope}},
)
async def issue_ws_ticket(
    body: WsTicketRequest,
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
) -> dict:
    """Mint a one-time, 30s, project-bound ticket so the WS upgrade never needs
    a long-lived bearer token in a URL (H8). M15-W5-S2: the ticket is also
    bound to this client's fingerprint (UA + peer IP hash) and only upgrades
    from a request that reproduces it."""
    return await _issue_ws_ticket(
        request.app.state,
        principal,
        request.app.state.session_factory,
        body.projectId,
        user_agent=request.headers.get("user-agent", ""),
        client_ip=_client_ip(request),
    )


@router.websocket("/ws/{project_id}")
async def project_ws(
    websocket: WebSocket, project_id: str, token: str = "", afterSeq: int = 0,
    ticket: str = "", fingerprint: str = "",
):
    # M15-W5-S2: redemption result carries the mint-time client fingerprint
    # (None when no ticket was presented, or for pre-0025 rows — grace path).
    redeemed = None
    if ticket:
        # One-time redemption binds the principal AND the project in one step,
        # and returns the mint-time client fingerprint for upgrade validation.
        redeemed = await redeem_ws_ticket(
            websocket.app.state.session_factory, ticket, project_id
        )
        principal = redeemed.principal if redeemed is not None else None
    elif token:
        # M18-C5 (MAC2-F7) DEPRECATED CHANNEL — one-release warning first.
        # A long-lived session credential in the URL query is exactly what H8
        # forbids (query strings land in access/proxy logs and browser
        # history). No internal caller relies on this anymore (repo-wide grep:
        # only pre-transition tests); it dies next release. Route legacy
        # callers through POST /api/v1/ws-ticket, which mints a short-lived,
        # single-use, fingerprint-bound ticket instead.
        logger.warning(
            "WS upgrade via ?token= query param is DEPRECATED and will be "
            "REMOVED next release (project=%s): mint a ticket at "
            "POST /api/v1/ws-ticket and present ?ticket=… instead",
            project_id,
        )
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

    # M15-W5-S2 client-binding: recompute the fingerprint from THIS upgrade
    # request (its UA + peer address) and compare against what mint stored.
    #
    # M18-C5 (MAC2-F5) — presentation is now REQUIRED when mint recorded a
    # fingerprint. The old absent-header grace path let any replaying attacker
    # evaporate the header half of the binding by simply omitting it, which
    # reduced the whole control to the spoofable server-side recomputation.
    #   absent            -> close 4403 TICKET_FINGERPRINT_MISMATCH
    #                        (pre-0025 rows with a NULL stored fingerprint keep
    #                        their grace path — that NULL case is the only one).
    #   present + matches -> accepted.
    #   present + wrong   -> close 4403 TICKET_FINGERPRINT_MISMATCH.
    # X-WS-Fingerprint header first; the `fingerprint` query param is the
    # browser channel (browsers cannot set custom WS headers). A presented
    # value may be the full sha256(UA+IP)[:16] of THIS upgrade request OR its
    # UA-ONLY derivation sha256(UA)[:16] — the S7 browser client cannot know
    # its own IP and has always shipped the UA-only form (useEventStream.ts),
    # while non-browser callers present the exact value echoed at mint time.
    # Presentation proves the upgrading process COMPUTED the binding input;
    # the authoritative anti-replay check stays server-side below: the stored
    # mint fingerprint must equal the recomputed UA+IP of this request, so a
    # ticket replayed from another host dies here regardless of headers.
    if ticket and redeemed is not None:
        ws_user_agent = websocket.headers.get("user-agent", "")
        upgrade_ip = websocket.client.host if websocket.client else ""
        upgrade_fp = compute_client_fingerprint(ws_user_agent, upgrade_ip)
        presented = websocket.headers.get("x-ws-fingerprint", "").strip()
        if not presented:
            presented = fingerprint.strip()
        bound = redeemed.fingerprint
        if bound is not None:
            if not presented:
                logger.warning(
                    "WS upgrade rejected: missing X-WS-Fingerprint/fingerprint "
                    "(required since M18-C5; project=%s)",
                    project_id,
                )
                await websocket.close(code=4403, reason="TICKET_FINGERPRINT_MISMATCH")
                return
            ua_only_fp = compute_client_fingerprint(ws_user_agent, "")
            if (
                len(presented) != FINGERPRINT_HEX_LEN
                or presented not in (upgrade_fp, ua_only_fp)
            ):
                logger.warning(
                    "WS upgrade rejected: TICKET_FINGERPRINT_MISMATCH "
                    "(project=%s, presented=%s…)",
                    project_id,
                    presented[:4],
                )
                await websocket.close(code=4403, reason="TICKET_FINGERPRINT_MISMATCH")
                return
        if bound is not None and bound != upgrade_fp:
            logger.warning(
                "WS upgrade rejected: TICKET_FINGERPRINT_MISMATCH "
                "(ticket bound to a different client; project=%s)",
                project_id,
            )
            await websocket.close(code=4403, reason="TICKET_FINGERPRINT_MISMATCH")
            return
    try:
        await _require_project_access(
            websocket.app.state.session_factory, principal, project_id
        )
    except HTTPException:
        # M13-G5a no-oracle law: foreign-project upgrades close with the same
        # code as an unknown project — 4403 is reserved for auth failures.
        await websocket.close(code=4404)
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
        # M16-X3: capped, streamed replay. A huge reconnect gap sends the
        # NEWEST up-to-REPLAY_MAX_EVENTS batch; when the gap exceeded the cap
        # the trailing marker frame tells the client exactly what it missed so
        # it can backfill via GET /projects/{id}/events/replay instead of
        # silently believing it has full history.
        last_seq = int(afterSeq)
        page: ReplayPage = await bus.replay_page(project_id, last_seq)
        for envelope in page.envelopes:
            await websocket.send_json(envelope.model_dump())
            last_seq = max(last_seq, envelope.seq)
        if page.truncated and page.oldest_seq_sent is not None:
            await websocket.send_json(
                {
                    "replayTruncated": True,
                    "oldestSeqSent": page.oldest_seq_sent,
                    "afterSeq": int(afterSeq),
                }
            )
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


@router.get(
    "/projects/{project_id}/events/replay",
    response_model=EventReplayResponse,
    responses={401: {"model": ErrorEnvelope}, 404: {"model": ErrorEnvelope}},
)
async def replay_events(
    project_id: str,
    request: Request,
    after_seq: int = 0,
    limit: int = 0,
    principal: Annotated[object, Depends(get_principal)] = None,
) -> dict:
    await _require_project_access(request.app.state.session_factory, principal, project_id)
    bus: EventBus = request.app.state.event_bus
    page = await bus.replay_page(project_id, after_seq, limit or 500)
    return {
        "items": [e.model_dump() for e in page.envelopes],
        "nextCursor": page.envelopes[-1].seq if page.envelopes else after_seq,
        # M16-X3: truncation marker only when the cap bit; absent otherwise so
        # existing consumers see the exact pre-X3 response shape.
        **(
            {"replayTruncated": True, "oldestSeqSent": page.oldest_seq_sent}
            if page.truncated and page.oldest_seq_sent is not None
            else {}
        ),
    }
