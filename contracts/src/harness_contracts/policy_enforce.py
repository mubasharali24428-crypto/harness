"""Part 38.005 - one enforcement seam for the policy engine, shared by identity and orchestrator.

Every HTTP route is classified by a table (``IDENTITY_RULES`` / ``ORCHESTRATOR_RULES``) into the
permission it needs, or ``EXEMPT``. ``install_policy_gate`` adds an HTTP middleware that, for a
caller whose team belongs to an organization (``teams.org_id``), evaluates the org's policy with
the pure engine and refuses with 403 ``POLICY_DENIED`` when it says no.

Composition rule (documented in docs/permissions.md): the gate is ADDITIVE to the legacy role
check each route still performs - a request needs both. Teams without an org are untouched, so
the pilot behaves exactly as before until a team is linked. A user of an org team who is not an
org member is evaluated with their team role only (org deny rules still bind them).

Sections may import only this package, so storage is read with raw SQL against the 0039 tables.
Heavy deps (sqlalchemy, starlette) are imported lazily, like ``fastapi_auth``.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from .policy_engine import (
    Binding,
    Decision,
    EmergencyGrant,
    Node,
    PolicyDoc,
    Subject,
    aware,
    evaluate,
)
from .roles import Principal

EXEMPT = "-"

# (methods, path-template regex, permission). First match wins. "*" = any method.
# Permission semantics: read=GET; steer=any write an engineer could do before; approve=decide
# plans/changes; administer=admin-only legacy routes; export=audit export; billing=plan changes.
IDENTITY_RULES: tuple[tuple[str, str, str], ...] = (
    ("*", r"/(health|ready|metrics)", EXEMPT),
    ("*", r"/api/v1/auth/.*", EXEMPT),  # before a session exists
    ("*", r"/api/v1/(csp-report|invites/redeem)", EXEMPT),
    ("*", r"/api/v1/orgs(/.*)?", EXEMPT),  # org routes carry their own org-scoped checks
    ("*", r"/api/v1/policy/.*", EXEMPT),  # the policy API gates itself (policy._gate)
    ("*", r"/api/v1/stepup/verify", EXEMPT),
    ("POST", r"/api/v1/ws-ticket", "read"),
    ("GET", r"/api/v1/me", "read"),
    ("GET", r"/api/v1/audit", "administer"),
    ("GET", r"/api/v1/audit/export", "export"),
    ("GET", r"/api/v1/billing/state", "read"),
    ("PUT", r"/api/v1/billing/plan", "billing"),
    ("GET", r"/api/v1/(keys|projects|projects/[^/]+/(rules|events/replay))", "read"),
    ("POST|DELETE", r"/api/v1/keys(/[^/]+)?", "steer"),
    ("POST", r"/api/v1/projects", "steer"),
    ("PUT|DELETE", r"/api/v1/projects/[^/]+/rules", "steer"),
    ("GET", r"/api/v1/teams/[^/]+/(members|invites|settings/.*)", "read"),
    ("*", r"/api/v1/teams/[^/]+/(members/.*|invites.*|seats/agent|settings/.*)",
     "administer"),
    ("*", r"/api/v1/teams/[^/]+/sso/config", "administer"),
    ("GET", r"/api/v1/workspaces(/.*)?", "read"),
    ("*", r"/api/v1/workspaces/(allowlist.*|templates|[^/]+/delete)", "administer"),
    ("*", r"/api/v1/workspaces(/.*)?", "steer"),
    ("GET", r"/api/v1/(sessions|agents)(/.*)?", "read"),
    ("*", r"/api/v1/sessions/[^/]+/(delete|purge|archive|restore)", "administer"),
    ("DELETE", r"/api/v1/sessions/[^/]+", "administer"),
    ("*", r"/api/v1/agents/select", "steer"),
    ("*", r"/api/v1/agents(/.*)?", "administer"),
    ("*", r"/api/v1/sessions(/.*)?", "steer"),
)

ORCHESTRATOR_RULES: tuple[tuple[str, str, str], ...] = (
    ("*", r"/(health|ready|metrics)", EXEMPT),
    ("*", r"/api/v1/orchestrator/metrics", EXEMPT),
    ("GET", r"/api/v1/orchestrator/.*", "read"),
    # Participant self-service (observers and viewers included): presence and participation.
    ("POST", r"/api/v1/orchestrator/runs/[^/]+/(presence/.*|participation/.*)", "read"),
    ("PUT", r"/api/v1/orchestrator/runs/[^/]+/participation/prefs", "read"),
    ("PUT", r"/api/v1/orchestrator/presence-settings", "administer"),
    (
        "POST",
        r"/api/v1/orchestrator/(plans/[^/]+/(approve|reject)"
        r"|runs/[^/]+/(plan/(approve|reject)|approvals/[^/]+/decision))",
        "approve",
    ),
    ("*", r"/api/v1/orchestrator/.*", "steer"),
)

_LEVEL_PATHS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project", re.compile(r"/projects/([^/]+)")),
    ("workspace", re.compile(r"/workspaces/(?!allowlist|templates)([^/]+)")),
    ("run", re.compile(r"/runs/([^/]+)")),
)
_MISSING_SCHEMA = ("no such table", "no such column", "does not exist", "undefinedtable",
                   "undefinedcolumn")

ResolvePrincipal = Callable[[Any], Awaitable[Principal | None]]


def classify(rules: tuple[tuple[str, str, str], ...], method: str, path: str) -> str | None:
    """Permission (or EXEMPT) for a route template; None when no rule covers it."""
    for methods, pattern, perm in rules:
        if (methods == "*" or method in methods.split("|")) and re.fullmatch(pattern, path):
            return perm
    return None


def chain_for(org_id: str, team_id: str, path: str) -> list[Node]:
    """org > team > (project | workspace | run named in the path), in level order."""
    chain = [Node("org", org_id), Node("team", team_id)]
    for level, pattern in _LEVEL_PATHS:
        m = pattern.search(path)
        if m:
            chain.append(Node(level, m.group(1)[:64]))
    return chain


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, (str, bytes)) else v


def _ts(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return aware(v)


async def team_org(db: Any, team_id: str) -> str | None:
    from sqlalchemy import text

    row = (await db.execute(text("SELECT org_id FROM teams WHERE id = :t"), {"t": team_id})).first()
    return str(row[0]) if row and row[0] else None


async def decide_request(
    db: Any, org_id: str, principal: Principal, permission: str, chain: list[Node], now: datetime
) -> Decision:
    """Load the org's policy with raw SQL and evaluate it for ``principal`` at ``chain``."""
    from sqlalchemy import text

    org, uid, p = org_id, principal.userId, {"o": org_id}
    docs = {
        (r[0], r[1]): PolicyDoc.model_validate(_json(r[2]))
        for r in (
            await db.execute(
                text("SELECT level, scope_id, doc FROM policy_documents WHERE org_id = :o"), p
            )
        ).all()
    }
    custom = {
        r[0]: frozenset(_json(r[1]))
        for r in (
            await db.execute(
                text("SELECT name, permissions FROM policy_custom_roles WHERE org_id = :o"), p
            )
        ).all()
    }
    binds = [Binding("team", principal.teamId, str(principal.role.value).lower())]
    mem = (
        await db.execute(
            text(
                "SELECT role, expires_at FROM org_members"
                " WHERE org_id = :o AND user_id = :u AND removed_at IS NULL"
            ),
            {"o": org, "u": uid},
        )
    ).first()
    if mem is not None and (_ts(mem[1]) is None or _ts(mem[1]) > now):
        binds.append(Binding("org", org, str(mem[0]).lower()))
    for r in (
        await db.execute(
            text(
                "SELECT level, scope_id, role, expires_at FROM policy_assignments"
                " WHERE org_id = :o AND user_id = :u"
            ),
            {"o": org, "u": uid},
        )
    ).all():
        if _ts(r[3]) is None or _ts(r[3]) > now:
            binds.append(Binding(r[0], r[1], r[2]))
    emergency = [
        EmergencyGrant(r[0], uid, r[1], r[2], frozenset(_json(r[3])), _ts(r[4]), _ts(r[5]))
        for r in (
            await db.execute(
                text(
                    "SELECT id, level, scope_id, permissions, starts_at, expires_at"
                    " FROM policy_emergency_grants"
                    " WHERE org_id = :o AND user_id = :u AND status = 'active'"
                ),
                {"o": org, "u": uid},
            )
        ).all()
    ]
    return evaluate(Subject(uid, tuple(binds)), permission, chain, docs, custom, emergency, now=now)


async def _record(
    db: Any, org_id: str, principal: Principal, perm: str, chain: list[Node], d: Decision,
    now: datetime, method: str, path: str,
) -> None:
    """Decision log for every deny and every non-read allow; security audit row for denials."""
    from sqlalchemy import text

    resource = "/".join(f"{n.level}:{n.id}" for n in chain)[:512]
    await db.execute(
        text(
            "INSERT INTO policy_decision_log (id, org_id, ts, subject, permission, resource,"
            " decision, reason, rule_ids) VALUES (:i, :o, :t, :s, :p, :r, :d, :w, :ids)"
        ),
        {
            "i": uuid.uuid4().hex, "o": org_id, "t": now,
            "s": principal.userId[:64], "p": perm, "r": resource,
            "d": "allow" if d.allowed else "deny", "w": d.reason[:500],
            "ids": json.dumps(d.rule_ids),
        },
    )
    if not d.allowed:
        await db.execute(
            text(
                "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type,"
                " target_id, detail, ts) VALUES (NULL, 'human', 'policy.denied', 'route',"
                " :tid, :detail, :ts)"
            ),
            {
                "tid": path[:80], "ts": now,
                "detail": json.dumps({
                    "orgId": org_id, "teamId": principal.teamId, "userId": principal.userId,
                    "permission": perm, "method": method, "reason": d.reason[:300],
                    "ruleIds": d.rule_ids,
                }),
            },
        )
    await db.commit()


def _refuse(status: int, code: str, message: str) -> Any:
    from starlette.responses import JSONResponse

    body = {"error": {"code": code, "message": message}}
    return JSONResponse(status_code=status, content={**body, "detail": body})


def install_policy_gate(
    app: Any,
    rules: tuple[tuple[str, str, str], ...],
    resolve_principal: ResolvePrincipal,
    session_factory: Callable[[Any], Any],
) -> None:
    """Add the gate middleware. ``resolve_principal(request)`` returns None for callers without
    a valid session (the route's own dependency then answers 401); ``session_factory(request)``
    returns the async session factory to read policy with."""

    @app.middleware("http")
    async def policy_gate(request, call_next):
        path = request.url.path
        perm = classify(rules, request.method, path)
        if perm == EXEMPT or (perm is None and not path.startswith("/api/")):
            return await call_next(request)
        principal = await resolve_principal(request)
        factory = session_factory(request)
        if principal is None or factory is None:
            return await call_next(request)
        clock = getattr(request.app.state, "policy_clock", None)
        now = aware(clock() if clock else datetime.now(UTC))
        try:
            async with factory() as db:
                org_id = await team_org(db, principal.teamId)
                if org_id is not None and perm is None:
                    return _refuse(403, "POLICY_UNCLASSIFIED",
                                   f"no permission is defined for {request.method} {path}")
                if org_id is not None:
                    chain = chain_for(org_id, principal.teamId, path)
                    d = await decide_request(db, org_id, principal, perm, chain, now)
                    if not d.allowed or perm != "read":
                        await _record(
                            db, org_id, principal, perm, chain, d, now, request.method, path
                        )
                    if not d.allowed:
                        return _refuse(403, "POLICY_DENIED", d.reason)
        except Exception as e:  # noqa: BLE001 - classify below; unknown faults fail closed
            if not any(k in str(e).lower() for k in _MISSING_SCHEMA):
                return _refuse(503, "POLICY_UNAVAILABLE", "policy could not be evaluated")
            # bare schema: no orgs/policy tables exist, so nothing to enforce
        return await call_next(request)
