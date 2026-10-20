"""Part 38 (Lane P) - permissions and policy model.

Layout
------
1. PURE engine (no I/O, no clock reads, no randomness): ``evaluate`` and friends.
   Same inputs always give the same ``Decision``. The precedence rule is in
   ``docs/permissions.md`` and summarised on ``evaluate``.
2. DB layer (SQLAlchemy Core ``text``/``Table`` against the 0039 tables, plus
   ``org_members`` read via SQL only): ``authorize`` loads policy, evaluates, and
   writes the permission decision log.
3. ``router`` (``/api/v1/policy/...`` and ``/api/v1/stepup/verify``). The lead
   mounts it with ``app.include_router(policy.router)``; this lane never edits app.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from harness_contracts import Principal, error_body

# ---------------------------------------------------------------------------
# 1. Pure engine
# ---------------------------------------------------------------------------
from harness_contracts.policy_engine import (  # noqa: F401 - re-exported API of this module
    _ALL,
    _NAME_RE,
    _SUBJECT_RE,
    DEFAULT_EMERGENCY_MAX_MINUTES,
    DEFAULT_STEPUP_ACTIONS,
    DEFAULT_STEPUP_MAX_AGE,
    LEVELS,
    PERMISSIONS,
    ROLE_DEFAULTS,
    STEPUP_ACTIONS,
    Binding,
    Decision,
    Docs,
    EmergencyGrant,
    Node,
    PolicyDoc,
    PolicyError,
    Rule,
    RuleRef,
    Subject,
    aware,
    evaluate,
    evaluate_delegated,
    missing_for_grant,
    role_permissions,
    stepup_fresh,
    stepup_required,
    validate_chain,
    validate_custom_role,
)
from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_emit import _emit
from .auth import get_principal, hash_token
from .models import TeamMember, User

# ---------------------------------------------------------------------------
# 2. Storage (tables created by migrations/0039_policy.sql; sqlite tests use create_all)
# ---------------------------------------------------------------------------

_md = MetaData()
_J = JSON().with_variant(JSONB(), "postgresql")
_TS = DateTime(timezone=True)

policy_documents = Table(
    "policy_documents",
    _md,
    Column("id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False),
    Column("level", String(16), nullable=False),
    Column("scope_id", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("doc", _J, nullable=False),
    Column("updated_by", String(36), nullable=False),
    Column("updated_at", _TS, nullable=False),
    UniqueConstraint("org_id", "level", "scope_id", name="uq_policy_documents_scope"),
)
policy_change_requests = Table(
    "policy_change_requests",
    _md,
    Column("id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False),
    Column("level", String(16), nullable=False),
    Column("scope_id", String(64), nullable=False),
    Column("proposed_doc", _J, nullable=False),
    Column("expected_version", Integer, nullable=False),
    Column("requested_by", String(36), nullable=False),
    Column("requested_at", _TS, nullable=False),
    Column("status", String(16), nullable=False),
    Column("decided_by", String(36)),
    Column("decided_at", _TS),
)
policy_custom_roles = Table(
    "policy_custom_roles",
    _md,
    Column("org_id", String(36), primary_key=True),
    Column("name", String(32), primary_key=True),
    Column("permissions", _J, nullable=False),
    Column("created_by", String(36), nullable=False),
    Column("updated_at", _TS, nullable=False),
)
policy_assignments = Table(
    "policy_assignments",
    _md,
    Column("id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False),
    Column("user_id", String(36), nullable=False),
    Column("level", String(16), nullable=False),
    Column("scope_id", String(64), nullable=False),
    Column("role", String(32), nullable=False),
    Column("granted_by", String(36), nullable=False),
    Column("expires_at", _TS),
    Column("created_at", _TS, nullable=False),
    UniqueConstraint("org_id", "user_id", "level", "scope_id", "role", name="uq_policy_assignment"),
)
policy_emergency_grants = Table(
    "policy_emergency_grants",
    _md,
    Column("id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False),
    Column("user_id", String(36), nullable=False),
    Column("level", String(16), nullable=False),
    Column("scope_id", String(64), nullable=False),
    Column("permissions", _J, nullable=False),
    Column("reason", Text, nullable=False),
    Column("duration_minutes", Integer, nullable=False),
    Column("status", String(12), nullable=False),  # pending|active|revoked|expired
    Column("requested_by", String(36), nullable=False),
    Column("approved_by", String(36)),
    Column("starts_at", _TS),
    Column("expires_at", _TS),
    Column("revoked_at", _TS),
    Column("created_at", _TS, nullable=False),
    Index("ix_policy_emergency_org_user", "org_id", "user_id", "status"),
)
policy_decision_log = Table(
    "policy_decision_log",
    _md,
    Column("id", String(36), primary_key=True),
    Column("org_id", String(36), nullable=False),
    Column("ts", _TS, nullable=False),
    Column("subject", String(64), nullable=False),
    Column("permission", String(16), nullable=False),
    Column("resource", String(512), nullable=False),
    Column("decision", String(5), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("rule_ids", _J, nullable=False),
    Index("ix_policy_decision_log_org_ts", "org_id", "ts"),
)
policy_stepup = Table(
    "policy_stepup",
    _md,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("stepup_at", _TS, nullable=False),
)

_ready: set[int] = set()


async def ensure_schema(db: AsyncSession) -> None:
    """sqlite (tests/dev) only: Postgres gets its tables from migration 0039."""
    conn = await db.connection()
    if conn.dialect.name != "sqlite" or id(conn.engine) in _ready:
        return
    await conn.run_sync(_md.create_all)
    await db.commit()
    _ready.add(id(conn.engine))


def _uid() -> str:
    return uuid.uuid4().hex


def _resource(chain: Sequence[Node]) -> str:
    return "/".join(f"{n.level}:{n.id}" for n in chain)[:512]


def _parse_ts(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return aware(v)


async def org_role(db: AsyncSession, org_id: str, user_id: str, now: datetime) -> str | None:
    """Active org_members role (owned by Lane O; read via SQL only)."""
    row = (
        await db.execute(
            text(
                "SELECT role, expires_at FROM org_members "
                "WHERE org_id = :o AND user_id = :u AND removed_at IS NULL"
            ),
            {"o": org_id, "u": user_id},
        )
    ).first()
    if row is None:
        return None
    exp = _parse_ts(row[1])
    if exp is not None and exp <= aware(now):
        return None
    return str(row[0]).lower()


async def load_docs(db: AsyncSession, org_id: str) -> dict[tuple[str, str], PolicyDoc]:
    rows = (
        await db.execute(select(policy_documents).where(policy_documents.c.org_id == org_id))
    ).all()
    return {(r.level, r.scope_id): PolicyDoc.model_validate(r.doc) for r in rows}


async def load_custom_roles(db: AsyncSession, org_id: str) -> dict[str, frozenset[str]]:
    rows = (
        await db.execute(select(policy_custom_roles).where(policy_custom_roles.c.org_id == org_id))
    ).all()
    return {r.name: frozenset(r.permissions) for r in rows}


async def load_subject(
    db: AsyncSession, org_id: str, user_id: str, chain: Sequence[Node], now: datetime
) -> Subject | None:
    """Bindings from org_members (org), team_members (team nodes), policy_assignments.
    None when the user is not an active member of the org."""
    role = await org_role(db, org_id, user_id, now)
    if role is None:
        return None
    binds = [Binding("org", org_id, role)]
    for n in chain:
        if n.level == "team":
            tr = (
                await db.execute(
                    select(TeamMember.role).where(
                        TeamMember.team_id == n.id, TeamMember.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            if tr is not None:
                binds.append(Binding("team", n.id, str(getattr(tr, "value", tr)).lower()))
    rows = (
        await db.execute(
            select(policy_assignments).where(
                policy_assignments.c.org_id == org_id, policy_assignments.c.user_id == user_id
            )
        )
    ).all()
    for r in rows:
        exp = _parse_ts(r.expires_at)
        if exp is None or exp > aware(now):
            binds.append(Binding(r.level, r.scope_id, r.role))
    return Subject(user_id, tuple(binds))


async def _active_emergency(
    db: AsyncSession, org_id: str, user_id: str, now: datetime
) -> list[EmergencyGrant]:
    """Also lazily flips lapsed grants to ``expired`` and audits emergency.expired once."""
    t = policy_emergency_grants
    rows = (await db.execute(select(t).where(t.c.org_id == org_id, t.c.status == "active"))).all()
    live: list[EmergencyGrant] = []
    for r in rows:
        exp = aware(r.expires_at)
        if exp <= aware(now):
            res = await db.execute(
                update(t).where(t.c.id == r.id, t.c.status == "active").values(status="expired")
            )
            await db.commit()
            if res.rowcount:
                await _emit(
                    db,
                    "emergency.expired",
                    user_id=r.user_id,
                    target_type="emergency_grant",
                    target_id=r.id,
                    detail={"orgId": org_id},
                )
        elif r.user_id == user_id:
            live.append(
                EmergencyGrant(
                    r.id,
                    r.user_id,
                    r.level,
                    r.scope_id,
                    frozenset(r.permissions),
                    aware(r.starts_at),
                    exp,
                )
            )
    return live


@dataclass
class Context:
    docs: dict[tuple[str, str], PolicyDoc]
    custom_roles: dict[str, frozenset[str]]
    subject: Subject | None
    emergency: list[EmergencyGrant] = field(default_factory=list)


async def load_context(
    db: AsyncSession, org_id: str, user_id: str, chain: Sequence[Node], now: datetime
) -> Context:
    await ensure_schema(db)
    return Context(
        await load_docs(db, org_id),
        await load_custom_roles(db, org_id),
        await load_subject(db, org_id, user_id, chain, now),
        await _active_emergency(db, org_id, user_id, now),
    )


def decide(ctx: Context, permission: str, chain: Sequence[Node], now: datetime) -> Decision:
    """The one place DB-loaded context becomes a Decision (preview uses it too)."""
    if ctx.subject is None:
        return Decision(
            False, permission, "not an active member of the organization (default deny)"
        )
    return evaluate(
        ctx.subject, permission, chain, ctx.docs, ctx.custom_roles, ctx.emergency, now=now
    )


async def authorize(
    db: AsyncSession,
    *,
    org_id: str,
    user_id: str,
    permission: str,
    chain: Sequence[Node],
    now: datetime,
    team_id: str | None = None,
    log: bool = True,
) -> Decision:
    """Integration point for other routes: evaluate, write the decision log, audit
    emergency use. Callers map ``allowed=False`` to 403/404 themselves."""
    chain = list(chain)
    validate_chain(chain)
    if chain[0].id != org_id:
        raise PolicyError("chain org does not match org_id")
    ctx = await load_context(db, org_id, user_id, chain, now)
    d = decide(ctx, permission, chain, now)
    if log:
        await db.execute(
            insert(policy_decision_log).values(
                id=_uid(),
                org_id=org_id,
                ts=aware(now),
                subject=user_id[:64],
                permission=permission,
                resource=_resource(chain),
                decision="allow" if d.allowed else "deny",
                reason=d.reason[:500],
                rule_ids=d.rule_ids,
            )
        )
        await db.commit()
    if d.allowed:
        for r in d.chain:
            if r.status == "decisive" and r.source == "emergency":
                await _emit(
                    db,
                    "emergency.used",
                    user_id=user_id,
                    team_id=team_id,
                    target_type="emergency_grant",
                    target_id=r.rule_id.removeprefix("emergency:"),
                    detail={"permission": permission, "resource": _resource(chain)},
                )
    return d


# ---------------------------------------------------------------------------
# 3. Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1")

Verifier = Callable[[Request, AsyncSession, Principal, str], Awaitable[bool]]


def _err(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, msg))


def _now(request: Request) -> datetime:
    clock = getattr(request.app.state, "policy_clock", None)
    return aware(clock() if clock else datetime.now(UTC))


def _token_hash(request: Request) -> str:
    return hash_token(request.headers.get("authorization", "").removeprefix("Bearer ").strip())


class ChainNode(BaseModel):
    level: str
    id: str = Field(min_length=1, max_length=64)


def _chain(org_id: str, nodes: Sequence[ChainNode] | None) -> list[Node]:
    chain = [Node("org", org_id)] + [Node(n.level, n.id) for n in (nodes or []) if n.level != "org"]
    try:
        validate_chain(chain)
    except PolicyError as e:
        raise _err(422, "VALIDATION_ERROR", str(e)) from e
    return chain


async def _session(request: Request):
    async with request.app.state.session_factory() as db:
        await ensure_schema(db)
        yield db


Db = Annotated[AsyncSession, Depends(_session)]
Who = Annotated[Principal, Depends(get_principal)]


async def require_stepup(
    db: AsyncSession,
    request: Request,
    principal: Principal,
    org_id: str,
    action: str,
    now: datetime | None = None,
) -> None:
    """403 STEPUP_REQUIRED unless the org's policy does not list ``action`` or the caller's
    token holds a fresh step-up. Shared by ``_gate`` and the legacy team routes."""
    now = now or _now(request)
    doc = (await load_docs(db, org_id)).get(("org", org_id))
    if not stepup_required(action, doc):
        return
    max_age = doc.stepup_max_age_seconds if doc else DEFAULT_STEPUP_MAX_AGE
    at = (
        await db.execute(
            select(policy_stepup.c.stepup_at).where(
                policy_stepup.c.token_hash == _token_hash(request)
            )
        )
    ).scalar_one_or_none()
    if stepup_fresh(at, now, max_age):
        return
    await _emit(
        db,
        "stepup.required",
        user_id=principal.userId,
        team_id=principal.teamId,
        target_type="org",
        target_id=org_id,
        detail={"action": action},
    )
    raise _err(403, "STEPUP_REQUIRED", f"recent re-authentication required for {action}")


async def require_team_stepup(
    db: AsyncSession, request: Request, principal: Principal, team_id: str, action: str
) -> None:
    """Step-up for routes keyed by team (role change, export). No-op for teams that belong
    to no organization, so existing pilot teams are unaffected until they are linked."""
    try:
        async with db.begin_nested():  # savepoint: dev SQLite lacks teams.org_id (0038 adds it)
            org_id = (
                await db.execute(text("SELECT org_id FROM teams WHERE id = :t"), {"t": team_id})
            ).scalar_one_or_none()
    except DBAPIError:
        return
    if org_id:
        await require_stepup(db, request, principal, org_id, action)


async def _gate(
    request: Request,
    db: AsyncSession,
    principal: Principal,
    org_id: str,
    permission: str,
    nodes: Sequence[ChainNode] | None = None,
    action: str | None = None,
) -> datetime:
    """404 for non-members (no existence oracle), 403 when denied, 403 STEPUP_REQUIRED
    for high-risk actions without a fresh re-auth. Every check lands in the decision log."""
    now = _now(request)
    if await org_role(db, org_id, principal.userId, now) is None:
        raise _err(404, "NOT_FOUND", "organization not found")
    d = await authorize(
        db,
        org_id=org_id,
        user_id=principal.userId,
        permission=permission,
        chain=_chain(org_id, nodes),
        now=now,
        team_id=principal.teamId,
    )
    if not d.allowed:
        raise _err(403, "FORBIDDEN", d.reason)
    if action:
        await require_stepup(db, request, principal, org_id, action, now)
    return now


class StepUpBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)


async def otp_verifier(request: Request, db: AsyncSession, principal: Principal, code: str) -> bool:
    """Default verifier: re-uses the existing email-OTP mechanism (auth.verify_otp). The
    user first requests a code through the normal /auth/request-login route. verify_otp
    also mints a throwaway AuthSession (and 409s for multi-team users AFTER the code was
    validated and consumed); we treat 409 as success and delete the throwaway session."""
    from .auth import verify_otp
    from .models import AuthSession

    email = (
        await db.execute(select(User.email).where(User.id == principal.userId))
    ).scalar_one_or_none()
    if email is None:
        return False
    try:
        token, _ = await verify_otp(db, request.app.state.settings, email, code)
    except HTTPException as e:
        return e.status_code == 409
    await db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    await db.commit()
    return True


@router.post("/stepup/verify")
async def stepup_verify(body: StepUpBody, request: Request, db: Db, principal: Who) -> dict:
    """Pluggable: ``app.state.stepup_verifier`` (async (request, db, principal, code) -> bool)
    overrides the default OTP verifier (tests use a double)."""
    verifier: Verifier = getattr(request.app.state, "stepup_verifier", None) or otp_verifier
    if not await verifier(request, db, principal, body.code):
        raise _err(401, "INVALID_CODE", "step-up verification failed")
    now, th = _now(request), _token_hash(request)
    if (
        await db.execute(select(policy_stepup.c.token_hash).where(policy_stepup.c.token_hash == th))
    ).first():
        await db.execute(
            update(policy_stepup).where(policy_stepup.c.token_hash == th).values(stepup_at=now)
        )
    else:
        await db.execute(
            insert(policy_stepup).values(token_hash=th, user_id=principal.userId, stepup_at=now)
        )
    await db.commit()
    await _emit(
        db,
        "stepup.verified",
        user_id=principal.userId,
        team_id=principal.teamId,
        target_type="user",
        target_id=principal.userId,
    )
    return {"verified": True, "stepupAt": now.isoformat()}


class DocBody(BaseModel):
    doc: PolicyDoc
    expectedVersion: int | None = None


def _check_level(level: str) -> None:
    if level not in LEVELS:
        raise _err(422, "VALIDATION_ERROR", f"level must be one of {list(LEVELS)}")


@router.get("/policy/orgs/{org_id}/documents")
async def list_documents(org_id: str, request: Request, db: Db, principal: Who) -> dict:
    await _gate(request, db, principal, org_id, "administer")
    rows = (
        await db.execute(
            select(policy_documents)
            .where(policy_documents.c.org_id == org_id)
            .order_by(policy_documents.c.level, policy_documents.c.scope_id)
        )
    ).all()
    return {
        "documents": [
            {"level": r.level, "scopeId": r.scope_id, "version": r.version, "doc": r.doc}
            for r in rows
        ]
    }


@router.put("/policy/orgs/{org_id}/documents/{level}/{scope_id}")
async def put_document(
    org_id: str, level: str, scope_id: str, body: DocBody, request: Request, db: Db, principal: Who
) -> dict:
    _check_level(level)
    if level == "org" and scope_id != org_id:
        raise _err(422, "VALIDATION_ERROR", "org-level document scope must be the org id")
    now = await _gate(request, db, principal, org_id, "administer", action="policy.save")
    t = policy_documents
    cur = (
        await db.execute(
            select(t).where(t.c.org_id == org_id, t.c.level == level, t.c.scope_id == scope_id)
        )
    ).first()
    if body.expectedVersion is not None and body.expectedVersion != (cur.version if cur else 0):
        raise _err(409, "VERSION_CONFLICT", "document changed since it was read")
    data = body.doc.model_dump(mode="json")
    version = (cur.version if cur else 0) + 1
    dual = bool((await db.execute(
        text("SELECT require_dual_approval FROM organizations WHERE id=:o"), {"o": org_id}
    )).scalar_one())
    sensitive_permissions = {"approve", "administer", "billing", "export"}

    def is_high_risk(doc: dict[str, Any] | None) -> bool:
        if not doc:
            return False
        return bool(doc.get("allow_lower_grants")) or any(
            rule.get("effect") == "deny"
            or bool(sensitive_permissions.intersection(rule.get("permissions", [])))
            for rule in doc.get("rules", [])
        )

    high_risk = is_high_risk(data) or is_high_risk(cur.doc if cur else None)
    if dual and high_risk:
        request_id = _uid()
        await db.execute(
            insert(policy_change_requests).values(
                id=request_id, org_id=org_id, level=level, scope_id=scope_id,
                proposed_doc=data, expected_version=(cur.version if cur else 0),
                requested_by=principal.userId, requested_at=now, status="pending",
            )
        )
        await db.commit()
        await _emit(
            db, "policy.change_requested", user_id=principal.userId,
            team_id=principal.teamId, target_type="policy_change_request", target_id=request_id,
            detail={"orgId": org_id, "level": level, "scopeId": scope_id},
        )
        return {"pendingApproval": True, "requestId": request_id,
                "level": level, "scopeId": scope_id, "version": cur.version if cur else 0}
    if cur:
        await db.execute(
            update(t)
            .where(t.c.id == cur.id)
            .values(doc=data, version=version, updated_by=principal.userId, updated_at=now)
        )
    else:
        await db.execute(
            insert(t).values(
                id=_uid(),
                org_id=org_id,
                level=level,
                scope_id=scope_id,
                version=version,
                doc=data,
                updated_by=principal.userId,
                updated_at=now,
            )
        )
    await db.commit()
    await _emit(
        db,
        "policy.saved",
        user_id=principal.userId,
        team_id=principal.teamId,
        target_type="policy_document",
        target_id=f"{org_id}/{level}/{scope_id}",
        detail={"version": version, "rules": len(body.doc.rules)},
    )
    return {"level": level, "scopeId": scope_id, "version": version}


@router.get("/policy/orgs/{org_id}/change-requests")
async def list_policy_change_requests(
    org_id: str, request: Request, db: Db, principal: Who
) -> dict:
    await _gate(request, db, principal, org_id, "administer")
    rows = (await db.execute(
        select(policy_change_requests).where(
            policy_change_requests.c.org_id == org_id,
            policy_change_requests.c.status == "pending",
        ).order_by(policy_change_requests.c.requested_at)
    )).all()
    return {"requests": [
        {"id": r.id, "level": r.level, "scopeId": r.scope_id,
         "proposedDoc": r.proposed_doc, "expectedVersion": r.expected_version,
         "requestedBy": r.requested_by, "requestedAt": r.requested_at.isoformat()}
        for r in rows
    ]}


async def _decide_policy_change(
    org_id: str, request_id: str, request: Request, db: AsyncSession,
    principal: Principal, approve: bool,
) -> dict:
    now = await _gate(request, db, principal, org_id, "administer", action="policy.save")
    await require_stepup(db, request, principal, org_id, "policy.save")
    change = (await db.execute(select(policy_change_requests).where(
        policy_change_requests.c.id == request_id,
        policy_change_requests.c.org_id == org_id,
    ))).first()
    if change is None or change.status != "pending":
        raise _err(404, "NOT_FOUND", "pending policy change not found")
    if change.requested_by == principal.userId:
        raise _err(409, "SECOND_APPROVER_REQUIRED", "the requester cannot approve their own change")
    if approve:
        current = (await db.execute(select(policy_documents).where(
            policy_documents.c.org_id == org_id,
            policy_documents.c.level == change.level,
            policy_documents.c.scope_id == change.scope_id,
        ))).first()
        version = current.version if current else 0
        if version != change.expected_version:
            raise _err(409, "VERSION_CONFLICT", "policy changed after this request was submitted")
        if current:
            await db.execute(update(policy_documents).where(
                policy_documents.c.id == current.id
            ).values(doc=change.proposed_doc, version=version + 1,
                     updated_by=principal.userId, updated_at=now))
        else:
            await db.execute(insert(policy_documents).values(
                id=_uid(), org_id=org_id, level=change.level, scope_id=change.scope_id,
                version=1, doc=change.proposed_doc, updated_by=principal.userId, updated_at=now,
            ))
    status = "approved" if approve else "rejected"
    await db.execute(update(policy_change_requests).where(
        policy_change_requests.c.id == request_id
    ).values(status=status, decided_by=principal.userId, decided_at=now))
    await db.commit()
    await _emit(
        db, "policy.change_decided", user_id=principal.userId,
        team_id=principal.teamId, target_type="policy_change_request", target_id=request_id,
        detail={"orgId": org_id, "status": status},
    )
    return {"requestId": request_id, "status": status}


@router.post("/policy/orgs/{org_id}/change-requests/{request_id}/approve")
async def approve_policy_change(org_id: str, request_id: str, request: Request,
                               db: Db, principal: Who) -> dict:
    return await _decide_policy_change(org_id, request_id, request, db, principal, True)


@router.post("/policy/orgs/{org_id}/change-requests/{request_id}/reject")
async def reject_policy_change(org_id: str, request_id: str, request: Request,
                               db: Db, principal: Who) -> dict:
    return await _decide_policy_change(org_id, request_id, request, db, principal, False)


class RoleBody(BaseModel):
    permissions: list[str]


@router.get("/policy/orgs/{org_id}/roles")
async def list_roles(org_id: str, request: Request, db: Db, principal: Who) -> dict:
    await _gate(request, db, principal, org_id, "read")
    custom = await load_custom_roles(db, org_id)
    return {
        "builtin": {k: sorted(v) for k, v in ROLE_DEFAULTS.items()},
        "custom": {k: sorted(v) for k, v in sorted(custom.items())},
    }


@router.put("/policy/orgs/{org_id}/roles/{name}")
async def put_role(
    org_id: str, name: str, body: RoleBody, request: Request, db: Db, principal: Who
) -> dict:
    try:
        perms = validate_custom_role(name, body.permissions)
    except PolicyError as e:
        raise _err(422, "VALIDATION_ERROR", str(e)) from e
    now = await _gate(request, db, principal, org_id, "administer", action="role.change")
    chain = [Node("org", org_id)]
    ctx = await load_context(db, org_id, principal.userId, chain, now)
    assert ctx.subject is not None
    lacking = missing_for_grant(
        ctx.subject, perms, chain, ctx.docs, ctx.custom_roles, ctx.emergency, now=now
    )
    if lacking:
        raise _err(403, "ESCALATION", f"cannot create a role with permissions you lack: {lacking}")
    t = policy_custom_roles
    exists = (
        await db.execute(select(t.c.name).where(t.c.org_id == org_id, t.c.name == name))
    ).first()
    vals = {"permissions": sorted(perms), "created_by": principal.userId, "updated_at": now}
    if exists:
        await db.execute(update(t).where(t.c.org_id == org_id, t.c.name == name).values(**vals))
    else:
        await db.execute(insert(t).values(org_id=org_id, name=name, **vals))
    await db.commit()
    await _emit(
        db,
        "policy.custom_role_saved",
        user_id=principal.userId,
        team_id=principal.teamId,
        target_type="custom_role",
        target_id=f"{org_id}/{name}",
        detail={"permissions": sorted(perms)},
    )
    return {"name": name, "permissions": sorted(perms)}


class AssignBody(BaseModel):
    userId: str
    level: str
    scopeId: str = Field(min_length=1, max_length=64)
    role: str
    expiresAt: datetime | None = None


@router.post("/policy/orgs/{org_id}/assignments", status_code=201)
async def assign_role(
    org_id: str, body: AssignBody, request: Request, db: Db, principal: Who
) -> dict:
    """Grant a role on a node below the org. Anti-escalation: grantor must hold every
    permission the role carries at that node. Target must be an active org member."""
    _check_level(body.level)
    if body.level == "org":
        raise _err(422, "VALIDATION_ERROR", "org roles are managed through org membership")
    now = await _gate(request, db, principal, org_id, "administer", action="role.change")
    chain = _chain(org_id, [ChainNode(level=body.level, id=body.scopeId)])
    ctx = await load_context(db, org_id, principal.userId, chain, now)
    perms = role_permissions(body.role, ctx.custom_roles)
    if not perms:
        raise _err(422, "VALIDATION_ERROR", "unknown role")
    if await org_role(db, org_id, body.userId, now) is None:
        raise _err(404, "NOT_FOUND", "user is not a member of this organization")
    assert ctx.subject is not None
    lacking = missing_for_grant(
        ctx.subject, perms, chain, ctx.docs, ctx.custom_roles, ctx.emergency, now=now
    )
    if lacking:
        raise _err(403, "ESCALATION", f"cannot grant permissions you lack: {lacking}")
    aid = _uid()
    await db.execute(
        insert(policy_assignments).values(
            id=aid,
            org_id=org_id,
            user_id=body.userId,
            level=body.level,
            scope_id=body.scopeId,
            role=body.role,
            granted_by=principal.userId,
            expires_at=body.expiresAt,
            created_at=now,
        )
    )
    await db.commit()
    await _emit(
        db,
        "policy.saved",
        user_id=principal.userId,
        team_id=principal.teamId,
        target_type="policy_assignment",
        target_id=aid,
        detail={"targetUserId": body.userId, "role": body.role, "level": body.level},
    )
    return {"id": aid}


class CheckBody(BaseModel):
    permission: str
    chain: list[ChainNode] = []
    userId: str | None = None


@router.post("/policy/orgs/{org_id}/check")
async def check(org_id: str, body: CheckBody, request: Request, db: Db, principal: Who) -> dict:
    """Explainable decision for yourself (any member) or another user (administer)."""
    target = body.userId or principal.userId
    await _gate(
        request,
        db,
        principal,
        org_id,
        "administer" if target != principal.userId else "read",
        body.chain,
    )
    if body.permission not in _ALL:
        raise _err(422, "VALIDATION_ERROR", "unknown permission")
    d = await authorize(
        db,
        org_id=org_id,
        user_id=target,
        permission=body.permission,
        chain=_chain(org_id, body.chain),
        now=_now(request),
        team_id=principal.teamId,
    )
    return d.as_dict()


class Draft(BaseModel):
    level: str
    scopeId: str
    doc: PolicyDoc | None = None  # None == delete this document in the draft


class PreviewBody(BaseModel):
    drafts: list[Draft] = []
    customRoles: dict[str, list[str]] | None = None  # full replacement of custom roles in the draft
    userIds: list[str] = Field(min_length=1, max_length=50)
    permissions: list[str] = list(PERMISSIONS)
    chain: list[ChainNode] = []


@router.post("/policy/orgs/{org_id}/preview")
async def preview(org_id: str, body: PreviewBody, request: Request, db: Db, principal: Who) -> dict:
    """Effective-permission diff (current vs draft) for the given users. Reads only; the
    same ``decide`` as real enforcement, so preview == post-save evaluation."""
    now = await _gate(request, db, principal, org_id, "administer", body.chain)
    chain = _chain(org_id, body.chain)
    bad = [p for p in body.permissions if p not in _ALL]
    if bad:
        raise _err(422, "VALIDATION_ERROR", f"unknown permissions {bad}")
    try:
        draft_roles = (
            {n: validate_custom_role(n, p) for n, p in body.customRoles.items()}
            if body.customRoles is not None
            else None
        )
        for dr in body.drafts:
            _check_level(dr.level)
    except PolicyError as e:
        raise _err(422, "VALIDATION_ERROR", str(e)) from e
    out = []
    for uid in body.userIds:
        cur = await load_context(db, org_id, uid, chain, now)
        docs = dict(cur.docs)
        for dr in body.drafts:
            if dr.doc is None:
                docs.pop((dr.level, dr.scopeId), None)
            else:
                docs[(dr.level, dr.scopeId)] = dr.doc
        new = Context(
            docs,
            draft_roles if draft_roles is not None else cur.custom_roles,
            cur.subject,
            cur.emergency,
        )
        for p in body.permissions:
            a, b = decide(cur, p, chain, now), decide(new, p, chain, now)
            out.append(
                {
                    "userId": uid,
                    "permission": p,
                    "current": a.as_dict(),
                    "draft": b.as_dict(),
                    "changed": a.allowed != b.allowed,
                }
            )
    return {"results": out, "changes": [r for r in out if r["changed"]]}


@router.get("/policy/orgs/{org_id}/decisions")
async def decisions(
    org_id: str,
    request: Request,
    db: Db,
    principal: Who,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    since: datetime | None = None,
    until: datetime | None = None,
    subject: str | None = None,
    decision: Annotated[str | None, Query(pattern="^(allow|deny)$")] = None,
) -> dict:
    """Admin query of the decision log. Bounded: <=200 rows, window <=31 days (default 24h)."""
    now = await _gate(request, db, principal, org_id, "administer")
    until = aware(until) if until else now
    since = aware(since) if since else until - timedelta(days=1)
    if since >= until or until - since > timedelta(days=31):
        raise _err(422, "VALIDATION_ERROR", "window must be positive and at most 31 days")
    t = policy_decision_log
    q = (
        select(t)
        .where(t.c.org_id == org_id, t.c.ts >= since, t.c.ts <= until)
        .order_by(t.c.ts.desc(), t.c.id)
        .limit(limit)
    )
    if subject:
        q = q.where(t.c.subject == subject)
    if decision:
        q = q.where(t.c.decision == decision)
    rows = (await db.execute(q)).all()
    return {
        "decisions": [
            {
                "id": r.id,
                "ts": aware(r.ts).isoformat(),
                "subject": r.subject,
                "permission": r.permission,
                "resource": r.resource,
                "decision": r.decision,
                "reason": r.reason,
                "ruleIds": r.rule_ids,
            }
            for r in rows
        ]
    }


class EmergencyBody(BaseModel):
    permissions: list[str] = Field(min_length=1)
    level: str = "org"
    scopeId: str | None = None
    reason: str = Field(min_length=10, max_length=500)
    durationMinutes: int = Field(ge=5, le=1440)


@router.post("/policy/orgs/{org_id}/emergency", status_code=201)
async def emergency_request(
    org_id: str, body: EmergencyBody, request: Request, db: Db, principal: Who
) -> dict:
    """Any org member requests time-boxed access for themselves; someone else approves."""
    _check_level(body.level)
    now = _now(request)
    if await org_role(db, org_id, principal.userId, now) is None:
        raise _err(404, "NOT_FOUND", "organization not found")
    if not set(body.permissions) <= _ALL:
        raise _err(422, "VALIDATION_ERROR", "unknown permissions")
    scope = org_id if body.level == "org" else body.scopeId
    if not scope:
        raise _err(422, "VALIDATION_ERROR", "scopeId required below org level")
    doc = (await load_docs(db, org_id)).get(("org", org_id))
    cap = doc.emergency_max_minutes if doc else DEFAULT_EMERGENCY_MAX_MINUTES
    if body.durationMinutes > cap:
        raise _err(422, "VALIDATION_ERROR", f"duration exceeds the maximum of {cap} minutes")
    gid = _uid()
    await db.execute(
        insert(policy_emergency_grants).values(
            id=gid,
            org_id=org_id,
            user_id=principal.userId,
            level=body.level,
            scope_id=scope,
            permissions=sorted(set(body.permissions)),
            reason=body.reason,
            duration_minutes=body.durationMinutes,
            status="pending",
            requested_by=principal.userId,
            created_at=now,
        )
    )
    await db.commit()
    return {"id": gid, "status": "pending"}


async def _grant(db: AsyncSession, org_id: str, gid: str):
    t = policy_emergency_grants
    g = (await db.execute(select(t).where(t.c.id == gid, t.c.org_id == org_id))).first()
    if g is None:
        raise _err(404, "NOT_FOUND", "emergency grant not found")
    return g


@router.post("/policy/orgs/{org_id}/emergency/{gid}/approve")
async def emergency_approve(
    org_id: str, gid: str, request: Request, db: Db, principal: Who
) -> dict:
    now = await _gate(request, db, principal, org_id, "administer", action="emergency.grant")
    g = await _grant(db, org_id, gid)
    if g.status != "pending":
        raise _err(409, "BAD_STATE", f"grant is {g.status}")
    if g.requested_by == principal.userId:
        raise _err(403, "SELF_APPROVAL", "requester cannot approve their own emergency access")
    chain = _chain(org_id, None if g.level == "org" else [ChainNode(level=g.level, id=g.scope_id)])
    ctx = await load_context(db, org_id, principal.userId, chain, now)
    assert ctx.subject is not None
    lacking = missing_for_grant(
        ctx.subject, g.permissions, chain, ctx.docs, ctx.custom_roles, ctx.emergency, now=now
    )
    if lacking:
        raise _err(403, "ESCALATION", f"approver lacks {lacking}")
    t = policy_emergency_grants
    exp = now + timedelta(minutes=g.duration_minutes)
    await db.execute(
        update(t)
        .where(t.c.id == gid)
        .values(status="active", approved_by=principal.userId, starts_at=now, expires_at=exp)
    )
    await db.commit()
    await _emit(
        db,
        "emergency.granted",
        user_id=principal.userId,
        team_id=principal.teamId,
        target_type="emergency_grant",
        target_id=gid,
        detail={
            "forUser": g.user_id,
            "permissions": g.permissions,
            "reason": g.reason,
            "expiresAt": exp.isoformat(),
        },
    )
    return {"id": gid, "status": "active", "expiresAt": exp.isoformat()}


@router.post("/policy/orgs/{org_id}/emergency/{gid}/revoke")
async def emergency_revoke(org_id: str, gid: str, request: Request, db: Db, principal: Who) -> dict:
    now = await _gate(request, db, principal, org_id, "administer")
    g = await _grant(db, org_id, gid)
    if g.status not in ("pending", "active"):
        raise _err(409, "BAD_STATE", f"grant is {g.status}")
    t = policy_emergency_grants
    await db.execute(update(t).where(t.c.id == gid).values(status="revoked", revoked_at=now))
    await db.commit()
    await _emit(
        db,
        "emergency.revoked",
        user_id=principal.userId,
        team_id=principal.teamId,
        target_type="emergency_grant",
        target_id=gid,
        detail={"forUser": g.user_id},
    )
    return {"id": gid, "status": "revoked"}


@router.get("/policy/orgs/{org_id}/emergency")
async def emergency_list(org_id: str, request: Request, db: Db, principal: Who) -> dict:
    now = await _gate(request, db, principal, org_id, "administer")
    await _active_emergency(db, org_id, principal.userId, now)  # sweeps lapsed grants
    t = policy_emergency_grants
    rows = (
        await db.execute(
            select(t).where(t.c.org_id == org_id).order_by(t.c.created_at.desc()).limit(100)
        )
    ).all()
    return {
        "grants": [
            {
                "id": r.id,
                "userId": r.user_id,
                "level": r.level,
                "scopeId": r.scope_id,
                "permissions": r.permissions,
                "reason": r.reason,
                "status": r.status,
                "requestedBy": r.requested_by,
                "approvedBy": r.approved_by,
                "expiresAt": aware(r.expires_at).isoformat() if r.expires_at else None,
            }
            for r in rows
        ]
    }


__all__ = [
    "LEVELS",
    "PERMISSIONS",
    "ROLE_DEFAULTS",
    "Binding",
    "Decision",
    "EmergencyGrant",
    "Node",
    "PolicyDoc",
    "PolicyError",
    "Rule",
    "Subject",
    "authorize",
    "evaluate",
    "evaluate_delegated",
    "ensure_schema",
    "missing_for_grant",
    "router",
    "stepup_fresh",
    "stepup_required",
    "validate_custom_role",
]
