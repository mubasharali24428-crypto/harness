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

import functools
import json
import re
import time
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
    # Part 38 (session 12): org routes are engine-gated. The gate only evaluates the caller's own
    # org (teams.org_id), never an org named in the path, so these rows classify the operation.
    ("POST", r"/api/v1/orgs/invites/accept", "read"),  # the invitee's own action
    ("GET", r"/api/v1/orgs/service/whoami", "read"),
    ("DELETE", r"/api/v1/orgs/me/identity", "read"),  # self-service identity removal
    ("GET", r"/api/v1/orgs", "read"),
    ("POST", r"/api/v1/orgs", "administer"),
    ("GET", r"/api/v1/orgs/[^/]+(/(members|invites|execution-control))?", "read"),
    ("*", r"/api/v1/orgs(/.*)?", "administer"),  # history, service identities, all writes
    ("*", r"/api/v1/policy/.*", EXEMPT),  # the policy API gates itself (policy._gate)
    ("*", r"/api/v1/stepup/verify", EXEMPT),
    ("POST", r"/api/v1/ws-ticket", "read"),
    ("GET", r"/api/v1/me", "read"),
    ("GET", r"/api/v1/audit", "administer"),
    ("GET", r"/api/v1/audit/export", "export"),
    # Marathon 2.0 W16 slice 4: asking whether the hash chain still verifies is
    # the same evidence door as pulling the export, so it carries the same
    # permission rather than a new one an org policy would have to learn.
    ("POST", r"/api/v1/audit/verify", "export"),
    ("GET", r"/api/v1/billing/state", "read"),
    ("PUT", r"/api/v1/billing/plan", "billing"),
    ("GET", r"/api/v1/(keys|projects|projects/[^/]+/(rules|events/replay))", "read"),
    ("POST|DELETE", r"/api/v1/keys(/[^/]+)?", "steer"),
    ("POST", r"/api/v1/projects", "steer"),
    ("PUT|DELETE", r"/api/v1/projects/[^/]+/rules", "steer"),
    # Marathon 2.0 W12: team skills (reviewed instruction bundles) and project
    # memory. Reads are reads; authoring and toggling steer; review and removal
    # are admin-only, so the gate itself refuses an engineer, matching the
    # handlers' role checks (defense in depth, not a second opinion).
    ("GET", r"/api/v1/teams/[^/]+/skills", "read"),
    ("POST", r"/api/v1/teams/[^/]+/skills/[^/]+/review", "administer"),
    ("DELETE", r"/api/v1/teams/[^/]+/skills/[^/]+", "administer"),
    ("*", r"/api/v1/teams/[^/]+/skills(/.*)?", "steer"),
    ("GET", r"/api/v1/projects/[^/]+/memory", "read"),
    ("POST", r"/api/v1/projects/[^/]+/memory/[^/]+/review", "administer"),
    ("*", r"/api/v1/projects/[^/]+/memory(/.*)?", "steer"),
    # Marathon 2.0 W13: bring-your-own runners. Admin surface follows the
    # catalog posture (read to list, administer to mint/revoke/assign). The
    # /runner/* routes authenticate a machine token instead of a Principal;
    # with no principal the gate passes through to the route's own check, and
    # these entries keep the route-classification law total.
    ("GET", r"/api/v1/team-runners", "read"),
    ("POST", r"/api/v1/team-runners/[^/]+/revoke", "administer"),
    ("POST", r"/api/v1/team-runners/[^/]+/jobs", "administer"),
    ("*", r"/api/v1/team-runners(/.*)?", "administer"),
    ("GET", r"/api/v1/runner/jobs", "read"),
    ("POST", r"/api/v1/runner/heartbeat", "read"),
    ("POST", r"/api/v1/runner/jobs/[^/]+/claim", "steer"),
    ("POST", r"/api/v1/runner/jobs/[^/]+/heartbeat", "steer"),
    ("POST", r"/api/v1/runner/jobs/[^/]+/finish", "steer"),
    # W13 execution leg: the plan-derived spec is read-only; reporting tool events
    # writes to the run's transcript, so it is a steering act like finish.
    ("GET", r"/api/v1/runner/jobs/[^/]+/spec", "read"),
    ("POST", r"/api/v1/runner/jobs/[^/]+/events", "steer"),
    ("GET", r"/api/v1/teams/[^/]+/(members|invites|settings/.*)", "read"),
    ("*", r"/api/v1/teams/[^/]+/(members/.*|invites.*|seats/agent|settings/.*)",
     "administer"),
    ("*", r"/api/v1/teams/[^/]+/sso/config", "administer"),
    # Marathon 2.0 W16: SCIM. Minting/revoking a directory credential is an
    # admin act; the /scim/v2 protocol itself authenticates a MACHINE token, so
    # there is no Principal for the gate to evaluate - it is EXEMPT and the
    # route's own token check is the authority (same posture as /runner/*, whose
    # entries exist to keep the classification law total).
    ("GET", r"/api/v1/teams/[^/]+/scim/tokens", "read"),
    ("*", r"/api/v1/teams/[^/]+/scim/tokens(/.*)?", "administer"),
    # Marathon 2.0 W16 slice 4: a SIEM sink is an outbound egress decision plus a
    # live delivery cursor over the team's evidence, so every verb on it is an
    # admin act — there is no read-only view that an engineer should hold.
    ("*", r"/api/v1/teams/[^/]+/siem/sinks(/.*)?", "administer"),
    ("*", r"/scim/v2(/.*)?", EXEMPT),
    ("GET", r"/api/v1/workspaces(/.*)?", "read"),
    ("*", r"/api/v1/workspaces/(allowlist.*|templates|[^/]+/delete)", "administer"),
    ("*", r"/api/v1/workspaces(/.*)?", "steer"),
    ("GET", r"/api/v1/(sessions|agents)(/.*)?", "read"),
    ("*", r"/api/v1/sessions/[^/]+/(delete|purge|archive|restore)", "administer"),
    ("POST", r"/api/v1/sessions/[^/]+/owner", "administer"),  # Part 40: ownership transfer
    ("DELETE", r"/api/v1/sessions/[^/]+", "administer"),
    ("*", r"/api/v1/agents/select", "steer"),
    ("*", r"/api/v1/agents(/.*)?", "administer"),
    ("*", r"/api/v1/sessions(/.*)?", "steer"),
    # Session 5: a user's own inbox and preferences are self-service (viewers included).
    ("*", r"/api/v1/notifications(/.*)?", "read"),
    # Parts 31/58: erasing your own account is self-service (viewers included).
    ("POST", r"/api/v1/account/erasure", "read"),
    # Part 38 (session 12): the realtime sockets are reads; the identity app evaluates them at
    # upgrade and on every recheck (`socket_decision`), not through the HTTP middleware.
    ("GET", r"/api/v1/ws/[^/]+(/runs/[^/]+/collab)?", "read"),
)

ORCHESTRATOR_RULES: tuple[tuple[str, str, str], ...] = (
    ("*", r"/(health|ready|metrics)", EXEMPT),
    ("*", r"/api/v1/orchestrator/metrics", EXEMPT),
    ("GET", r"/api/v1/orchestrator/(runs|sessions)/[^/]+/comments/export", "export"),
    # Session 6: bulk export jobs (status, download) need `export`.
    ("GET", r"/api/v1/orchestrator/exports(/.*)?", "export"),
    ("GET", r"/api/v1/orchestrator/.*", "read"),
    # Session 5 discussion: commenting needs `comment`; turning a comment into guidance is steering.
    ("POST", r"/api/v1/orchestrator/(runs|sessions)/[^/]+/comments/[^/]+/promote", "steer"),
    ("POST", r"/api/v1/orchestrator/(runs|sessions)/[^/]+/comments/[^/]+/moderate",
     "administer"),
    ("PUT", r"/api/v1/orchestrator/discussion-settings", "administer"),
    ("POST|PATCH|DELETE",
     r"/api/v1/orchestrator/(runs|sessions)/[^/]+/comments(/[^/]+(/reactions)?)?", "comment"),
    # Session 6 plans and execution (part 44): proposing edits and plan comments are `comment`;
    # deciding an edit, accepting or rejecting completion, reviewing a change and merge approval
    # are `approve`. Everything else on those resources is the steer fallback below.
    ("POST", r"/api/v1/orchestrator/runs/[^/]+/plan/versions/[^/]+/(comments|edits)", "comment"),
    ("POST", r"/api/v1/orchestrator/runs/[^/]+/plan/versions/[^/]+/edits/[^/]+/decide", "approve"),
    ("POST", r"/api/v1/orchestrator/runs/[^/]+/plan/versions/[^/]+/approve", "approve"),
    ("POST", r"/api/v1/orchestrator/runs/[^/]+/(completion/(accept|reject)|merge-approval"
            r"|changes/[^/]+/review)", "approve"),
    # Session 6 export and deletion requests (part 50): exports need `export`; a deletion request
    # is self-service (the route limits it to the caller's own data unless an admin), its decision
    # is admin-only.
    ("POST", r"/api/v1/orchestrator/exports", "export"),
    ("POST", r"/api/v1/orchestrator/deletion-requests", "read"),
    ("POST", r"/api/v1/orchestrator/deletion-requests/[^/]+/(approve|reject)", "administer"),
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
    ("project", re.compile(r"/ws/([^/]+)")),  # realtime sockets: /api/v1/ws/{project}/runs/{run}
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


# Part 38 (session 12): the policy reads of an org-linked request (about six) are cached for
# POLICY_CACHE_TTL seconds. The cache is process-local: a write made by this process clears it at
# once (see install_policy_cache_listeners); another replica's write is seen within the TTL.
POLICY_CACHE_TTL = 5.0
_CACHE_LIMIT = 4096
_cache: dict[tuple, tuple[float, Any]] = {}
_generation = [0]  # bumped on every clear: a load that raced a write is not stored
_DIRTY = "policy_cache_dirty"
_POLICY_TABLES = frozenset(
    {"policy_documents", "policy_custom_roles", "policy_assignments",
     "policy_emergency_grants", "org_members", "teams"}
)
_WRITE_SQL = re.compile(r"^\s*(insert|update|delete)\b", re.IGNORECASE)
_TABLE_SQL = re.compile(r"\b(" + "|".join(sorted(_POLICY_TABLES)) + r")\b")


def invalidate() -> None:
    """Forget every cached policy read in this process."""
    _cache.clear()
    _generation[0] += 1


async def _cached(db: Any, key: tuple, load: Callable[[], Awaitable[Any]]) -> Any:
    # Namespaced by the engine the read ran on: a read from another database can never answer
    # for this one (matters only when several databases share a process, as in the test suites).
    key = (getattr(db, "bind", None), *key)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    generation = _generation[0]
    value = await load()
    if generation == _generation[0]:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[key] = (now + POLICY_CACHE_TTL, value)
    return value


async def _rows(db: Any, key: tuple, sql: str, params: dict[str, Any]) -> list[tuple]:
    from sqlalchemy import text

    async def load() -> list[tuple]:
        return [tuple(r) for r in (await db.execute(text(sql), params)).all()]

    return await _cached(db, key, load)


@functools.cache
def install_policy_cache_listeners() -> None:
    """Clear the cache when a session writes a policy or org table: at execution, and again at
    commit (a read between the two could repopulate it with pre-commit rows). Runs once."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.expression import TextClause

    def writes_policy(state: Any) -> bool:
        stmt = state.statement
        if isinstance(stmt, TextClause):
            return bool(_WRITE_SQL.match(stmt.text) and _TABLE_SQL.search(stmt.text))
        table = getattr(stmt, "table", None)
        return bool(state.is_insert or state.is_update or state.is_delete) and (
            table is not None and table.name in _POLICY_TABLES
        )

    @event.listens_for(Session, "do_orm_execute")
    def _on_execute(state: Any) -> None:
        if writes_policy(state):
            state.session.info[_DIRTY] = True
            invalidate()

    @event.listens_for(Session, "before_flush")
    def _on_flush(session: Any, flush_context: Any, instances: Any) -> None:
        # ORM unit-of-work writes (session.add, attribute changes) do not pass through execute().
        objects = (*session.new, *session.dirty, *session.deleted)
        names = {o.__table__.name for o in objects if hasattr(o, "__table__")}
        if names & _POLICY_TABLES:
            session.info[_DIRTY] = True
            invalidate()

    @event.listens_for(Session, "after_commit")
    def _on_commit(session: Any) -> None:
        if session.info.pop(_DIRTY, False):
            invalidate()


def is_missing_schema(exc: BaseException) -> bool:
    return any(k in str(exc).lower() for k in _MISSING_SCHEMA)


async def team_org(db: Any, team_id: str) -> str | None:
    from sqlalchemy import text

    async def load() -> str | None:
        result = await db.execute(text("SELECT org_id FROM teams WHERE id = :t"), {"t": team_id})
        row = result.first()
        return str(row[0]) if row and row[0] else None

    return await _cached(db, ("team", team_id), load)


async def decide_request(
    db: Any, org_id: str, principal: Principal, permission: str, chain: list[Node], now: datetime
) -> Decision:
    """Load the org's policy with raw SQL and evaluate it for ``principal`` at ``chain``."""
    org, uid = org_id, principal.userId
    docs = {
        (r[0], r[1]): PolicyDoc.model_validate(_json(r[2]))
        for r in await _rows(
            db, ("docs", org),
            "SELECT level, scope_id, doc FROM policy_documents WHERE org_id = :o", {"o": org},
        )
    }
    custom = {
        r[0]: frozenset(_json(r[1]))
        for r in await _rows(
            db, ("custom", org),
            "SELECT name, permissions FROM policy_custom_roles WHERE org_id = :o", {"o": org},
        )
    }
    binds = [Binding("team", principal.teamId, str(principal.role.value).lower())]
    mem = await _rows(
        db, ("member", org, uid),
        "SELECT role, expires_at FROM org_members"
        " WHERE org_id = :o AND user_id = :u AND removed_at IS NULL",
        {"o": org, "u": uid},
    )
    if mem and (_ts(mem[0][1]) is None or _ts(mem[0][1]) > now):
        binds.append(Binding("org", org, str(mem[0][0]).lower()))
    for r in await _rows(
        db, ("assign", org, uid),
        "SELECT level, scope_id, role, expires_at FROM policy_assignments"
        " WHERE org_id = :o AND user_id = :u",
        {"o": org, "u": uid},
    ):
        if _ts(r[3]) is None or _ts(r[3]) > now:
            binds.append(Binding(r[0], r[1], r[2]))
    emergency = [
        EmergencyGrant(r[0], uid, r[1], r[2], frozenset(_json(r[3])), _ts(r[4]), _ts(r[5]))
        for r in await _rows(
            db, ("emerg", org, uid),
            "SELECT id, level, scope_id, permissions, starts_at, expires_at"
            " FROM policy_emergency_grants"
            " WHERE org_id = :o AND user_id = :u AND status = 'active'",
            {"o": org, "u": uid},
        )
    ]
    return evaluate(Subject(uid, tuple(binds)), permission, chain, docs, custom, emergency, now=now)


async def socket_decision(
    db: Any, principal: Principal, path: str, clock: Callable[[], datetime] | None = None
) -> Decision | None:
    """Part 38: the org policy for a realtime socket's upgrade or recheck, as a ``read`` at the
    chain named by ``path``. The caller must already have verified the path's project and run
    belong to the principal's team. None when the team has no org (or the schema is bare)."""
    try:
        org_id = await team_org(db, principal.teamId)
    except Exception as e:  # noqa: BLE001 - a bare schema has no teams.org_id: nothing to enforce
        if is_missing_schema(e):
            return None
        raise
    if org_id is None:
        return None
    now = aware(clock() if clock else datetime.now(UTC))
    chain = chain_for(org_id, principal.teamId, path)
    d = await decide_request(db, org_id, principal, "read", chain, now)
    if not d.allowed:
        await _record(db, org_id, principal, "read", chain, d, now, "GET", path)
    return d


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

    install_policy_cache_listeners()

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
