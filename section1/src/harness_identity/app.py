import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated, Any

import harness_contracts as contracts
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from harness_contracts import Principal as ContractsPrincipal
from harness_contracts import Role, error_body
from harness_contracts.policy_enforce import socket_decision
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from . import domain_metrics, realtime_metrics
from .audit import register_append_only_guard
from .audit_chain import verify_window
from .audit_rows import read_audit_page
from .auth import get_principal, hash_token, issue_otp, login_or_register, verify_otp
from .config import Settings
from .crypto import KeyVault
from .ephemeral import (
    FINGERPRINT_HEX_LEN,
    compute_client_fingerprint,
    mint_ws_ticket,
    redeem_ws_ticket,
)
from .eventbus import (
    CLOSE_ACCESS_REVOKED,
    CLOSE_OVERFLOW,
    CLOSE_RATE_LIMITED,
    COLLAB_SCHEMA_VERSION,
    ConnectionGuard,
    EventBus,
    ReplayPage,
    SocketPolicyClose,
    principal_still_valid,
    ws_revalidate_seconds,
)
from .limits import (
    LOGIN_EMAIL_MAX,
    LOGIN_TEAM_ID_MAX,
    LOGIN_TEAM_NAME_MAX,
    OTP_CODE_MAX,
    PROJECT_NAME_MAX,
    PROJECT_REPO_FULL_NAME_MAX,
    WS_TICKET_PROJECT_ID_MAX,
)
from .models import AuthSession, TeamMember, User
from .models import Role as DbRole
from .ratelimit import effective_client_ip
from .webhooks import router as webhooks_router

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
    Behind a trusted proxy that adds XFF the mint binds the forwarded client
    IP, so the upgrade must derive its IP with the same function
    (``_ws_authenticate`` does; before session 12 it used the raw peer and
    refused every ticketed socket behind such a proxy).
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
    from . import stripe_adapter as _stripe_adapter_module  # noqa: F401 — ORM side effect

    # Registering StripeWebhookEvent on the shared declarative Base is a
    # STARTUP side effect, not route wiring: without it Base.metadata never
    # sees the 0032 table and the FK inventory / fresh-DB provisioning miss
    # it entirely. The ROUTE stays behind HARNESS_BILLING as before.
    from .billing import router as billing_router
    from .stripe_adapter import make_client_from_env
    from .stripe_adapter import router as stripe_adapter

    app.include_router(billing_router)

    # Session 7 (part 49): versioned, team-admin-managed outbound webhooks.
    # The model import registers migration 0051's ORM mirror for test schemas.
    app.include_router(webhooks_router)

    # Mission22-M3 merge wiring: LIVE Stripe adapter + checkout/webhook routes
    # behind the SAME flag. With HARNESS_BILLING unset this injects nothing —
    # the routes 503 honestly and the M10 behavior is bit-for-bit unchanged.
    stripe_client = make_client_from_env()
    if stripe_client is not None:
        app.state.stripe_client = stripe_client
        app.include_router(stripe_adapter)

    # Mission22-M4 merge wiring: OIDC SSO login edges + per-team IdP config
    # CRUD. Unconfigured teams answer 404 SSO_NOT_CONFIGURED and every other
    # route is untouched — OTP stays the default login path.
    from .sso import router as sso_router

    app.include_router(sso_router)

    # Marathon 2.0 W16 slice 3: SCIM 2.0 subset. Two mounts, because the protocol
    # path is not ours to rename — `router` is the admin-facing token CRUD under
    # /api/v1, `scim_router` is the RFC-pinned /scim/v2 surface an external IdP
    # calls (deploy/Caddyfile routes it to this service).
    from .scim import router as scim_router
    from .scim import scim_router as scim_protocol_router

    app.include_router(scim_router)
    app.include_router(scim_protocol_router)

    # Marathon 2.0 W16 slice 4: SIEM sinks. The API is admin-only configuration;
    # the delivery step itself runs in the `siem-push` worker, which imports the
    # same module rather than a second copy of the batching rules.
    from .siem_sink import router as siem_router

    app.include_router(siem_router)

    # Mission12-E3a merge wiring: roster removal/role-change (W2-C1 server).
    from .teams_api import router as teams_api_router

    app.include_router(teams_api_router)

    # Mission12-G10 merge wiring: user-authored project rules CRUD
    # (.cursorrules equivalent; advisory prompt block via S4 rules_loader).
    from .rules_api import router as rules_router

    app.include_router(rules_router)

    # Marathon 2.0 W12: governed team skill catalog (migration 0066) and
    # project-scoped memory (0067) — both advisory prompt blocks via S4 loaders.
    from .memory_api import router as memory_router
    from .skills_api import router as skills_router

    app.include_router(skills_router)
    app.include_router(memory_router)

    # Marathon 2.0 W13: bring-your-own runner control plane (migration 0068).
    # Admin registration/revocation/assignment; token-authenticated runner
    # heartbeat, job claim, lease heartbeat and finish with nonce replay proof.
    from .runner_api import router as runner_router

    app.include_router(runner_router)

    # M15-G6b: per-team settings CRUD (reviewer thresholds, admin-gated).
    from .settings_api import router as settings_api_router

    app.include_router(settings_api_router)

    # Session 3 (parts 37-39): organizations, policy engine, workspaces.
    from .orgs import router as orgs_router
    from .policy import router as policy_router
    from .workspaces import router as workspaces_router

    app.include_router(orgs_router)
    app.include_router(policy_router)
    app.include_router(workspaces_router)

    # Session 4 (parts 40, 45): collaboration sessions and the agent registry.
    from .agents import router as agents_router
    from .sessions import router as sessions_router

    app.include_router(sessions_router)
    app.include_router(agents_router)

    # Session 5 (part 47): in-app notification inbox, preferences, mute, unsubscribe.
    from .notifications import router as notifications_router

    app.include_router(notifications_router)

    # Parts 31/58: a person erases their own account.
    from .erasure import router as erasure_router

    app.include_router(erasure_router)

    # M15-W5-S1 merge wiring: browser CSP violation collector (unauthed,
    # size-capped, allow-listed logging — see csp_report.py).
    from .csp_report import router as csp_report_router

    app.include_router(csp_report_router)

    # Part 38.005: org policy applies to every classified route of org-linked teams
    # (additive to the legacy role checks; teams without an org are untouched).
    from harness_contracts.policy_enforce import IDENTITY_RULES, install_policy_gate

    async def _policy_principal(request):
        try:
            return await get_principal(request)
        except HTTPException:
            return None  # the route's own dependency answers 401

    install_policy_gate(
        app, IDENTITY_RULES, _policy_principal, lambda r: r.app.state.session_factory
    )
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
        account = (
            await db.execute(select(User).where(User.id == row.user_id))
        ).scalar_one_or_none()
        if account is None or not account.is_active:
            # W16: the socket is a fourth door, and it obeys the same rule as
            # `get_principal` and the shared bearer dependency — the seat decides,
            # not the session row. A deactivated user must not keep streaming a
            # project's events on a connection minted while they still had one.
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


# S1-R05e: operator read surface over ``audit_log``. Before this route the
# audit trail was write-only — no way to answer "who stopped/started what"
# or "who was denied" without raw DB access.
AUDIT_PAGE_MAX = 200
AUDIT_PAGE_DEFAULT = 50


@router.get("/audit")
async def list_audit(
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
    limit: int = AUDIT_PAGE_DEFAULT,
) -> dict:
    """Admin-only, newest-first, bounded page of audit rows (team-scoped).

    Scope rule: rows bound to the principal's team members, PLUS unbound
    rows (denials of non-members — the common denied case) whose ``detail``
    names the principal's team. Rows of other teams are never returned.
    """
    if principal.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="admin only")
    limit = max(1, min(int(limit), AUDIT_PAGE_MAX))
    # The writer's shape is json.dumps (", " separator): needle matches
    # ``"teamId": "<team>"``. LIKE is a conservative superset — rows are
    # still parsed below; foreign rows cannot pass the lower bound because
    # bound rows are filtered by membership, not by text.
    #
    # M18-C8 (E2E-found): detail is JSONB on real Postgres (migration 0001)
    # and TEXT in the SQLite lanes — bare `detail LIKE ...` is
    # `jsonb ~~ text` there and 500s the audit page. CAST to TEXT works on
    # both dialects; Postgres' jsonb text form uses the same `", "`/`": "`
    # separators as json.dumps, so the needle matches byte-for-byte.
    needle = f'"teamId": "{principal.teamId}"'
    async with request.app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT seq, actor_member_id, actor_kind, action,"
                        " target_type, target_id, detail, ts"
                        " FROM audit_log"
                        " WHERE actor_member_id IN ("
                        "   SELECT id FROM team_members WHERE team_id = :team)"
                        " OR (actor_member_id IS NULL"
                        "     AND CAST(detail AS TEXT) LIKE '%' || :needle || '%')"
                        " ORDER BY seq DESC LIMIT :limit"
                    ),
                    {
                        "team": principal.teamId,
                        "needle": needle,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )
    items = []
    for row in rows:
        detail = row["detail"]
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = {}
        items.append(
            {
                "seq": row["seq"],
                "actorMemberId": row["actor_member_id"],
                "actorKind": row["actor_kind"],
                "action": row["action"],
                "targetType": row["target_type"],
                "targetId": row["target_id"],
                "detail": detail,
                "ts": str(row["ts"]) if row["ts"] is not None else None,
            }
        )
    return {"items": items, "limit": limit}


# Part 11.009: authorised, team-scoped, bounded export of audit evidence and of the
# product (collaboration) event history. Ascending + cursor-paged so an auditor can
# walk the whole range and verify the hash chain offline (chainPos/prevHash/rowHash).
EXPORT_PAGE_MAX = 1000
EXPORT_PAGE_DEFAULT = 200
EXPORT_STREAMS = ("audit", "collab")
# W16 slice 4: `ndjson` exists for machines that append lines to a log store.
EXPORT_FORMATS = ("json", "ndjson")
NDJSON_MEDIA = "application/x-ndjson"

# W16 slice 4: the customer-facing chain check. Bounded because the response
# carries a per-row verdict — an unbounded window would be a way to pull the
# whole trail out through an integrity endpoint.
VERIFY_ROWS_MAX = 1000
VERIFY_ROWS_DEFAULT = 500

# The team-scope SQL rule lives in audit_rows.py (W16 slice 4): the export
# endpoint and the SIEM push must not carry two copies of it.


def _as_dict(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


@router.get("/audit/export")
async def export_audit(
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
    stream: str = "audit",
    afterSeq: int = 0,
    limit: int = EXPORT_PAGE_DEFAULT,
    runId: str = "",
    format: str = "json",
) -> dict:  # an ndjson request returns a Response; FastAPI passes it through
    
    """Admin-only evidence export for the CALLER's team (there is no team parameter, so a
    foreign team's rows can never be requested). ``stream=audit`` is the security audit
    log; ``stream=collab`` is the product event history of shared runs (optionally one
    ``runId``). Ascending by ``seq`` from ``afterSeq``; at most EXPORT_PAGE_MAX rows per
    call; follow ``nextCursor`` while ``hasMore``. Each export is itself audited.

    ``format=ndjson`` streams the same rows one JSON object per line for a SIEM
    collector that appends lines. A stream of lines has no place to put a cursor, so
    ``X-Harness-Next-Cursor`` / ``X-Harness-More`` carry it; the rows themselves are
    byte-identical to the JSON body's `items` entries, chain fields included."""
    if principal.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="admin only")
    if stream not in EXPORT_STREAMS:
        raise HTTPException(status_code=422, detail=f"stream must be one of {EXPORT_STREAMS}")
    if format not in EXPORT_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", f"format must be one of {EXPORT_FORMATS}"),
        )
    limit = max(1, min(int(limit), EXPORT_PAGE_MAX))
    after = max(0, int(afterSeq))
    factory = request.app.state.session_factory
    async with factory() as db:
        # Part 38.010: org policy may demand a fresh re-auth before evidence leaves the system.
        from .policy import require_team_stepup

        await require_team_stepup(db, request, principal, principal.teamId, "export")
        if stream == "audit":
            # W16 slice 4: the reader moved to audit_rows.py because the SIEM
            # pusher must hand a collector EXACTLY what an auditor exports. Two
            # copies of a team-scoping rule eventually disagree, and the
            # disagreement is invisible from the receiving side.
            items, has_more = await read_audit_page(
                db, principal.teamId, after_seq=after, limit=limit
            )
        else:
            try:
                rows = (
                    (
                        await db.execute(
                            text(
                                "SELECT seq, run_id, actor_user_id, kind, request_id, payload, ts"
                                " FROM run_collab_events WHERE team_id = :team AND seq > :after"
                                " AND (:run = '' OR run_id = :run) AND kind <> 'guidance.delivered'"
                                " ORDER BY seq ASC LIMIT :n"
                            ),
                            {"team": principal.teamId, "after": after, "run": runId,
                             "n": limit + 1},
                        )
                    )
                    .mappings()
                    .all()
                )
            except Exception as exc:  # noqa: BLE001 - table absent: fail closed, say why
                raise HTTPException(
                    status_code=503, detail="collaboration events unavailable"
                ) from exc
            items = [
                {
                    "seq": r["seq"],
                    "runId": r["run_id"],
                    "actor": r["actor_user_id"],
                    "kind": r["kind"],
                    "requestId": r["request_id"],
                    "payload": _as_dict(r["payload"]),
                    "ts": str(r["ts"]) if r["ts"] is not None else None,
                }
                for r in rows[:limit]
            ]
            has_more = len(rows) > limit
    await _audit_export_access(request, principal, stream, after, len(items))
    if format == "ndjson":
        cursor = items[-1]["seq"] if items else after
        body = "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in items)
        return Response(
            content=body,
            media_type=NDJSON_MEDIA,
            headers={
                "X-Harness-Next-Cursor": str(cursor),
                "X-Harness-More": "true" if has_more else "false",
            },
        )
    return {
        "stream": stream,
        "items": items,
        "limit": limit,
        "hasMore": has_more,
        "nextCursor": items[-1]["seq"] if items else after,
    }


class AuditVerifyRequest(BaseModel):
    afterChainPos: int = Field(default=0, ge=0)
    maxRows: int = Field(default=VERIFY_ROWS_DEFAULT, ge=1, le=VERIFY_ROWS_MAX)


@router.post("/audit/verify")
async def verify_audit(
    body: AuditVerifyRequest,
    request: Request,
    principal: Annotated[object, Depends(get_principal)],
) -> dict:
    """W16 slice 4: verify the hash chain over a window of the trail, so a
    customer holding an export can be told — row by row — whether it is still
    what the database sealed. Admin only, like every other evidence door.

    The verdict can only come from re-deriving the hashes with the same trigger
    function the chain was sealed by. That function is PostgreSQL-only; on a lane
    without it the endpoint refuses (503) rather than reporting a check it did not
    perform, because ``ok: true`` from a code path that verified nothing is the
    most dangerous answer this endpoint could give.
    """
    if principal.role != Role.ADMIN:
        raise HTTPException(
            status_code=403, detail=error_body("FORBIDDEN", "admin only")
        )
    async with request.app.state.session_factory() as db:
        have_chain = db.bind is not None and db.bind.dialect.name == "postgresql"
        verdict = None
        if have_chain:
            try:
                verdict = await verify_window(
                    db, after_chain_pos=body.afterChainPos, max_rows=body.maxRows
                )
            except Exception:  # noqa: BLE001 - no chain functions => no verdict
                logger.warning("audit chain verification unavailable", exc_info=True)
        if verdict is None:
            # Asked and answered: the question is recorded even when the engine
            # cannot answer it, so an admin probing the trail's integrity is
            # traceable either way.
            await _audit_verify_access(
                request, principal, body, rows_checked=0, ok=False, checked=False
            )
            raise HTTPException(
                status_code=503,
                detail=error_body(
                    "AUDIT_CHAIN_UNAVAILABLE",
                    "chain verification needs the deployment's PostgreSQL database",
                ),
            )
    verdict["verifiedAt"] = datetime.now(UTC).isoformat()
    await _audit_verify_access(
        request,
        principal,
        body,
        rows_checked=verdict["rowsChecked"],
        ok=verdict["ok"],
        checked=True,
    )
    return verdict


async def _audit_verify_access(
    request: Request,
    principal,
    body: "AuditVerifyRequest",
    *,
    rows_checked: int,
    ok: bool,
    checked: bool,
) -> None:
    """Asking whether the evidence was tampered with is a consequential read."""
    from .audit_emit import emit_audit_verified

    try:
        async with request.app.state.session_factory() as db:
            await emit_audit_verified(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                detail={
                    "afterChainPos": body.afterChainPos,
                    "maxRows": body.maxRows,
                    "rowsChecked": rows_checked,
                    "ok": ok,
                    "performed": checked,
                },
            )
    except Exception:  # noqa: BLE001 - never fail the verdict on the audit write
        logger.warning("audit verification access row not written", exc_info=True)


async def _audit_export_access(
    request: Request, principal, stream: str, after: int, n: int
) -> None:
    """Reading evidence is itself a consequential action: leave a row (best-effort)."""
    from .audit_emit import emit_audit_exported

    try:
        async with request.app.state.session_factory() as db:
            await emit_audit_exported(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                detail={"stream": stream, "afterSeq": after, "rows": n},
            )
    except Exception:  # noqa: BLE001 - never fail the export on the audit write
        logger.warning("audit export access row not written", exc_info=True)


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


async def _ws_authenticate(
    websocket: WebSocket, project_id: str, token: str, ticket: str, fingerprint: str
):
    """Shared WebSocket admission: ticket (preferred) / legacy token / bearer header,
    client-fingerprint binding, then team access to the project. Returns the Principal,
    or None after closing the socket with the matching code (4401 unauthenticated,
    4403 fingerprint mismatch, 4404 unknown or foreign project - no oracle)."""
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
        return None

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
        # The mint bound the ticket to _client_ip (XFF honoured only from a trusted proxy); the
        # upgrade must derive its IP the same way, or every socket behind a proxy that adds XFF
        # (the deploy Caddyfile) is refused. Found by the Part 54 socket load probe (session 12).
        upgrade_ip = effective_client_ip(
            websocket.headers,
            websocket.client.host if websocket.client else "",
            websocket.app.state.settings.trusted_proxy_cidrs,
        )
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
                return None
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
                return None
        if bound is not None and bound != upgrade_fp:
            logger.warning(
                "WS upgrade rejected: TICKET_FINGERPRINT_MISMATCH "
                "(ticket bound to a different client; project=%s)",
                project_id,
            )
            await websocket.close(code=4403, reason="TICKET_FINGERPRINT_MISMATCH")
            return None
    try:
        await _require_project_access(
            websocket.app.state.session_factory, principal, project_id
        )
    except HTTPException:
        # M13-G5a no-oracle law: foreign-project upgrades close with the same
        # code as an unknown project — 4403 is reserved for auth failures.
        await websocket.close(code=4404)
        return None

    return principal


async def _socket_policy_allows(websocket, principal) -> bool:
    """Part 38: the org policy binds realtime sockets too, as a `read` at the path's project
    and run. Callers verify those ids belong to the principal's team first. Raises on a store
    fault; the upgrade then never completes, and a recheck keeps the socket (see below)."""
    async with websocket.app.state.session_factory() as db:
        clock = getattr(websocket.app.state, "policy_clock", None)
        decision = await socket_decision(db, principal, websocket.scope["path"], clock)
    return decision is None or decision.allowed


async def _admit_socket(websocket, principal) -> bool:
    """Upgrade-time policy admission: a non-overridable or unoverridden deny closes with 4403."""
    if await _socket_policy_allows(websocket, principal):
        return True
    await websocket.close(code=4403, reason="POLICY_DENIED")
    return False


async def _still_allowed(websocket, principal, project_id: str, run_id: str | None = None) -> bool:
    """Part 46.011 and Part 38: re-check an open socket's access and its org policy. A store
    error keeps the socket (the next tick tries again); only a definite "no" ends it."""
    try:
        if not await principal_still_valid(
            websocket.app.state.session_factory, principal, project_id, run_id
        ):
            return False
        return await _socket_policy_allows(websocket, principal)
    except Exception:  # noqa: BLE001 — availability over strictness, same posture as the limiter
        logger.warning("ws access re-check failed; keeping the socket", exc_info=True)
        return True


def _revoked() -> SocketPolicyClose:
    return SocketPolicyClose(CLOSE_ACCESS_REVOKED, "ACCESS_REVOKED", "access_revoked")


async def _refuse_over_cap(websocket) -> None:
    realtime_metrics.inc("harness_realtime_connections_total", "refused")
    realtime_metrics.inc("harness_realtime_close_total", "rate_limited")
    await websocket.close(code=CLOSE_RATE_LIMITED, reason="RATE_LIMITED")


def _lag_seconds(ts: str) -> float | None:
    """Seconds between an event's ``ts`` and now (None when the timestamp is unreadable)."""
    try:
        moment = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - moment).total_seconds())


@router.websocket("/ws/{project_id}")
async def project_ws(
    websocket: WebSocket, project_id: str, token: str = "", afterSeq: int = 0,
    ticket: str = "", fingerprint: str = "",
):
    principal = await _ws_authenticate(websocket, project_id, token, ticket, fingerprint)
    if principal is None:
        return
    if not await _admit_socket(websocket, principal):
        return

    bus: EventBus = websocket.app.state.event_bus
    if not bus.open_connection(principal.userId):
        await _refuse_over_cap(websocket)
        return
    await websocket.accept()
    realtime_metrics.inc(
        "harness_realtime_connections_total", "resume" if int(afterSeq) > 0 else "connect"
    )

    # Subscribe BEFORE replaying: live events published while we send history are
    # buffered in the queue instead of lost. Duplicates from the overlap window are
    # removed client-side by seq dedup (section7 mergeEvents).
    queue = await bus.subscribe(project_id)
    guard = ConnectionGuard(websocket)
    # A silent client never triggers a send, so receiving is the ONLY way to observe its
    # disconnect. Racing this against queue.get() is what lets the handler release dead
    # subscriptions instead of parking forever.
    watcher = asyncio.ensure_future(guard.watch())
    getter: asyncio.Future | None = None
    reason = "server_error"
    try:
        # M16-X3: capped, streamed replay. A huge reconnect gap sends the
        # NEWEST up-to-REPLAY_MAX_EVENTS batch; when the gap exceeded the cap
        # the trailing marker frame tells the client exactly what it missed so
        # it can backfill via GET /projects/{id}/events/replay instead of
        # silently believing it has full history.
        last_seq = int(afterSeq)
        page: ReplayPage = await bus.replay_page(project_id, last_seq)
        for envelope in page.envelopes:
            await guard.send(envelope.model_dump(), throttle=False)
            last_seq = max(last_seq, envelope.seq)
            lag = _lag_seconds(envelope.ts)
            if lag is not None:
                realtime_metrics.observe_replay_age(lag)
        domain_metrics.inc("replayed", len(page.envelopes))
        if int(afterSeq) > 0:
            realtime_metrics.inc(
                "harness_realtime_recovered_total", "resumed_events", len(page.envelopes)
            )
        if page.truncated and page.oldest_seq_sent is not None:
            realtime_metrics.inc("harness_realtime_recovered_total", "truncated_replay")
            await guard.send(
                {
                    "replayTruncated": True,
                    "oldestSeqSent": page.oldest_seq_sent,
                    "afterSeq": int(afterSeq),
                },
                throttle=False,
            )
        recheck_every = ws_revalidate_seconds()
        next_check = time.monotonic() + recheck_every
        while True:
            if getter is None:
                getter = asyncio.ensure_future(queue.get())
            done, _pending = await asyncio.wait(
                {getter, watcher}, timeout=recheck_every, return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done:
                # Client disconnected (or sent anything — clients are read-only).
                if watcher.result() == "rate_limited":
                    raise guard.rate_limited()
                reason = "client"
                break
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + recheck_every
                if not await _still_allowed(websocket, principal, project_id):
                    raise _revoked()
            if getter not in done:
                continue
            envelope = getter.result()
            getter = None
            # Overflow detection via bus-side flag ONLY. Never seq arithmetic:
            # agent_events.seq is table-global, so per-project jumps are normal
            # whenever other projects publish.
            if getattr(queue, "overflowed", False):
                reason = "slow_consumer"
                await websocket.close(code=CLOSE_OVERFLOW)
                break
            await guard.send(envelope.model_dump())
            domain_metrics.inc("delivered")
            lag = _lag_seconds(envelope.ts)
            if lag is not None:
                realtime_metrics.observe_lag(lag)
            last_seq = envelope.seq
    except WebSocketDisconnect:
        reason = "client"
    except SocketPolicyClose as stop:
        reason = stop.metric
        await guard.close(stop.code, stop.reason, stop.frame)
    finally:
        watcher.cancel()
        if getter is not None:
            getter.cancel()
        bus.close_connection(principal.userId)
        realtime_metrics.inc("harness_realtime_close_total", reason)
        await bus.unsubscribe(project_id, queue)


COLLAB_WS_POLL_SECONDS_DEFAULT = 2.0


def _collab_poll_seconds() -> float:
    """Safety-net poll interval (NOTIFY normally wakes the socket within milliseconds)."""
    import os

    try:
        value = float(os.environ.get("COLLAB_WS_POLL_SECONDS", ""))
    except ValueError:
        return COLLAB_WS_POLL_SECONDS_DEFAULT
    return value if value > 0 else COLLAB_WS_POLL_SECONDS_DEFAULT


@router.websocket("/ws/{project_id}/runs/{run_id}/collab")
async def run_collab_ws(
    websocket: WebSocket, project_id: str, run_id: str, token: str = "", afterSeq: int = 0,
    ticket: str = "", fingerprint: str = "", schemaVersion: int = 1, control: int = 0,
):
    """Server push of one run's collaboration events (roster, leader, handoff, redirect,
    approval) - the same admission as the project socket (ticket, fingerprint, team
    access), plus the run must belong to that project.

    Protocol (docs/realtime.md is the client contract): frames are JSON ``{channel: "collab",
    seq, prevSeq, kind, actor, payload, ts, runId, projectId, teamId, schemaVersion}``. ``seq``
    is the run's collaboration cursor: it only ever increases on one connection, and ``prevSeq``
    names the previous visible event, so a client can prove nothing is missing. A client resumes
    with ``?afterSeq=<last seq seen>`` (the catch-up is replayed first, capped at
    REPLAY_MAX_EVENTS with a ``replayTruncated`` marker frame, then live delivery follows).
    ``?schemaVersion=N`` declares the newest frame schema it understands; ``?control=1`` asks for a
    ``ready`` control frame once the catch-up is complete. Clients de-duplicate by ``seq``. The HTTP
    endpoint ``GET .../collab-history?after=`` stays available as the polling fallback.
    """
    principal = await _ws_authenticate(websocket, project_id, token, ticket, fingerprint)
    if principal is None:
        return
    factory = websocket.app.state.session_factory
    async with factory() as db:
        owned = (
            await db.execute(
                text("SELECT 1 FROM agent_runs WHERE id = :r AND project_id = :p"),
                {"r": run_id, "p": project_id},
            )
        ).first()
    if owned is None:
        await websocket.close(code=4404)  # same code as an unknown/foreign project
        return
    if not await _admit_socket(websocket, principal):  # the run is verified above
        return

    bus: EventBus = websocket.app.state.event_bus
    if not bus.open_connection(principal.userId):
        await _refuse_over_cap(websocket)
        return
    team_id = principal.teamId
    client_schema = max(1, int(schemaVersion))
    start = max(0, int(afterSeq))
    await websocket.accept()
    realtime_metrics.inc("harness_realtime_connections_total", "resume" if start > 0 else "connect")
    wake = bus.collab_waker(run_id)  # register BEFORE replaying: nothing falls in the gap
    guard = ConnectionGuard(websocket)
    watcher = asyncio.ensure_future(guard.watch())
    reason = "server_error"
    try:
        last_seq = start
        page = await bus.collab_replay_page(
            run_id, team_id, last_seq, client_schema, project_id=project_id
        )
        for frame in page.frames:
            await guard.send(frame, throttle=False)
            last_seq = max(last_seq, frame["seq"])
            lag = _lag_seconds(frame["ts"])
            if lag is not None:
                realtime_metrics.observe_replay_age(lag)
        domain_metrics.inc("replayed", len(page.frames))
        if start > 0:
            realtime_metrics.inc(
                "harness_realtime_recovered_total", "resumed_events", len(page.frames)
            )
        if page.truncated and page.oldest_seq_sent is not None:
            realtime_metrics.inc("harness_realtime_recovered_total", "truncated_replay")
            await guard.send(
                {
                    "channel": "collab",
                    "replayTruncated": True,
                    "oldestSeqSent": page.oldest_seq_sent,
                    "afterSeq": start,
                },
                throttle=False,
            )
        if control:
            head = await bus.collab_head(run_id, team_id)
            await guard.send(
                {
                    "channel": "collab",
                    "control": "ready",
                    "afterSeq": start,
                    "headSeq": head,
                    "cursorAhead": start > head,
                    "schemaVersion": COLLAB_SCHEMA_VERSION,
                    "serverTime": datetime.now(UTC).isoformat(),
                },
                throttle=False,
            )
        poll = _collab_poll_seconds()
        recheck_every = ws_revalidate_seconds()
        next_check = time.monotonic() + recheck_every
        while True:
            waiter = asyncio.ensure_future(wake.wait())
            done, _pending = await asyncio.wait(
                {waiter, watcher}, timeout=poll, return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done:
                waiter.cancel()
                if watcher.result() == "rate_limited":
                    raise guard.rate_limited()
                reason = "client"
                break
            woken = waiter in done
            if not woken:
                waiter.cancel()
            wake.clear()
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + recheck_every
                if not await _still_allowed(websocket, principal, project_id, run_id):
                    raise _revoked()
            sent = 0
            while True:  # drain everything past the cursor, in order
                frames = await bus.collab_since(
                    run_id, team_id, last_seq, client_schema=client_schema, project_id=project_id
                )
                for frame in frames:
                    if frame["seq"] <= last_seq:  # never move the cursor back or repeat
                        domain_metrics.inc("duplicate")
                        continue
                    await guard.send(frame)
                    last_seq = frame["seq"]
                    sent += 1
                    domain_metrics.inc("delivered")
                    lag = _lag_seconds(frame["ts"])
                    if lag is not None:
                        realtime_metrics.observe_lag(lag)
                if len(frames) < 200:
                    break
            if woken and not sent:
                domain_metrics.inc("duplicate")  # a wake-up for an event already delivered
            if sent and not woken:
                # the poll tick found events nobody announced: a lost NOTIFY (or SQLite)
                realtime_metrics.inc("harness_realtime_recovered_total", "poll_recovered", sent)
    except WebSocketDisconnect:
        reason = "client"
    except SocketPolicyClose as stop:
        reason = stop.metric
        await guard.close(stop.code, stop.reason, stop.frame)
    finally:
        watcher.cancel()
        bus.release_collab_waker(run_id, wake)
        bus.close_connection(principal.userId)
        realtime_metrics.inc("harness_realtime_close_total", reason)


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
    before_seq: int = 0,
    principal: Annotated[object, Depends(get_principal)] = None,
) -> dict:
    await _require_project_access(request.app.state.session_factory, principal, project_id)
    bus: EventBus = request.app.state.event_bus
    if before_seq > 0:
        # W22 backfill: the default page keeps the NEWEST events of the gap, so a client
        # paging a truncated gap (after_seq, oldestSeqSent) never reaches its start.
        # Bounded pages go oldest-first; an empty page means the range is complete.
        items = [
            e for e in await bus.replay(project_id, after_seq, limit or 500) if e.seq < before_seq
        ]
        return {
            "items": [e.model_dump() for e in items],
            "nextCursor": items[-1].seq if items else after_seq,
        }
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
