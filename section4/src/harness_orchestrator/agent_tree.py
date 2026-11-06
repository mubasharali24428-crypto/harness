"""Agent spawn and tracking (master prompt part 45, Session 4 lane A). See docs/agents.md.

Sections may import only ``harness_contracts``, so this speaks raw SQL to the tables of
migration 0044 (``agent_definitions``, ``agent_run_pools``, ``agent_assignments``,
``agent_tool_calls``). Rules enforced HERE, server-side, on every spawn:

* the agent definition must be ``approved`` and belong to the caller's team;
* depth, live-children and budget limits are compare-and-set UPDATEs on the parent
  assignment (or on the per-run pool for a root), so concurrent spawns cannot overshoot;
* the granted scope (permissions, tools, paths, network, credential names) is a subset of
  the agent definition, of the parent assignment, and of the DELEGATING USER's effective
  permissions (pure policy engine, org policy included);
* the model falls back only to the fallback the approved definition declares, and the
  fallback is recorded.

Honest limit: the external engine does not call these endpoints yet. They record and gate;
nothing here starts or stops an agent process.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, Literal, NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from harness_contracts.policy_enforce import decide_request, team_org
from harness_contracts.policy_engine import Binding, Node, Subject, missing_for_grant
from harness_contracts.roles import Principal
from harness_contracts.timeutil import db_now
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api as _api
from harness_orchestrator.authz import (
    AuthzUnavailableError,
    OwnershipDeniedError,
    RoleDeniedError,
    _resolve_member_id,
    authorize_run,
    require_role,
)
from harness_orchestrator.budget import check_budget
from harness_orchestrator.engine_runtime import ProviderCircuitBreaker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["agents"])
_service: Any = None

MICRO = 1_000_000
AGENT_PERMISSIONS = ("read", "comment", "steer")
LIVE = ("active", "paused")
DEPTH_ENV, CHILDREN_ENV = "HARNESS_AGENT_MAX_DEPTH", "HARNESS_AGENT_MAX_CHILDREN"
RUN_AGENTS_ENV, RUN_BUDGET_ENV = "HARNESS_AGENT_RUN_MAX_AGENTS", "HARNESS_AGENT_RUN_BUDGET_USD"
UNAVAILABLE_ENV = "HARNESS_AGENT_UNAVAILABLE_MODELS"
_CAP = {"depth": (3, 0, 5), "children": (5, 0, 10), "agents": (20, 1, 200)}
_TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,59}$")
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
_CRED_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SECRETISH = re.compile(r"(?:\b(?:sk|ghp|gho|xox[bp])[-_][A-Za-z0-9_-]{16,}|Bearer\s+\S+)")


def configure_agent_tree(service: Any) -> None:
    """Deploy-time wiring: the OrchestratorService whose session factory serves this router."""
    global _service
    _service = service


def _svc() -> Any:
    return _service if _service is not None else _api.get_orchestrator_service()


def _factory() -> Any:
    f = getattr(_svc(), "session_factory", None)
    if f is None:
        raise _http(503, "AGENTS_UNAVAILABLE", "no database wired")
    return f


def _http(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": msg}})


def _illegal(verb: str, state: str) -> HTTPException:
    return _http(409, "ILLEGAL_TRANSITION", f"cannot {verb} an assignment that is {state}")


def _env_int(name: str, key: str) -> int:
    default, lo, hi = _CAP[key]
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


def _micro(d: Decimal) -> int:
    return int((d * MICRO).to_integral_value())


def _usd(m: int) -> str:
    return format(Decimal(m) / MICRO, "f")


def _iso(v: Any) -> str | None:
    return None if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))


# ------------------------------------------------------------- auth guard


def _guard(action: Literal["read", "write"]):
    async def dep(runId: str, principal: Annotated[Principal, Depends(_api.get_principal)]
                  ) -> Principal:
        try:
            require_role(principal, action)
            await authorize_run(_svc(), runId, principal)
        except AuthzUnavailableError as e:
            raise _http(403, "AUTHZ_UNAVAILABLE", f"authorization unavailable ({e.reason})") from e
        except OwnershipDeniedError as e:  # same answer as an unknown run: no oracle
            raise _http(404, "NOT_FOUND", "run not found") from e
        except RoleDeniedError as e:
            raise _http(403, "ROLE_DENIED", f"role may not {action}") from e
        return principal

    return dep


Reader = Annotated[Principal, Depends(_guard("read"))]
Writer = Annotated[Principal, Depends(_guard("write"))]


# ------------------------------------------------------------- audit


async def _audit(db: Any, p: Principal, action: str, target_id: str, detail: dict[str, Any],
                 target_type: str = "agent_assignment") -> None:
    member = await _resolve_member_id(db, p.teamId, p.userId)
    body = {"teamId": p.teamId, "userId": p.userId, **detail}
    await db.execute(
        text("INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type,"
             " target_id, detail, ts) VALUES (:a, 'human', :act, :tt, :tid, :d, :ts)"),
        {"a": member, "act": action, "tt": target_type, "tid": target_id[:80],
         "d": json.dumps(body, default=str), "ts": db_now()})


async def _refuse(p: Principal, run_id: str, status: int, code: str, msg: str, *,
                  action: str = "agent.spawn_refused",
                  detail: Mapping[str, Any] | None = None) -> NoReturn:
    """Audit (best effort, never masks the refusal) then raise the stable envelope."""
    try:
        async with _factory()() as db:
            await _audit(db, p, action, run_id, {"runId": run_id, "code": code, **(detail or {})},
                         "agent_run")
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("agent refusal audit failed (run=%s code=%s)", run_id, code)
    raise _http(status, code, msg)


# ------------------------------------------------------------- scope and permissions


class ScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permissions: list[str] = Field(default_factory=lambda: ["read"], max_length=10)
    tools: list[str] = Field(default_factory=list, max_length=100)
    paths: list[str] = Field(default_factory=list, max_length=50)
    network: list[str] = Field(default_factory=list, max_length=40)
    credentialRefs: list[str] = Field(default_factory=list, max_length=40)


def _norm_scope(b: ScopeBody) -> dict[str, list[str]]:
    def clean(vals: list[str], rx: re.Pattern[str] | None, what: str) -> list[str]:
        out = sorted({v.strip() for v in vals})
        for v in out:
            bad = rx is not None and not rx.match(v)
            if rx is None:
                bad = (not v or len(v) > 256 or "\x00" in v or "\\" in v or v.startswith("/")
                       or ".." in v.split("/"))
            if bad:
                raise _http(422, "VALIDATION_ERROR", f"scope.{what}: invalid entry")
        return out

    perms = sorted(set(b.permissions))
    if not perms or any(x not in AGENT_PERMISSIONS for x in perms):
        raise _http(422, "VALIDATION_ERROR",
                    f"scope.permissions must be a non-empty subset of {list(AGENT_PERMISSIONS)}")
    return {
        "permissions": perms,
        "tools": clean(b.tools, _TOOL_RE, "tools"),
        "paths": clean(b.paths, None, "paths"),
        "network": clean([h.lower() for h in b.network], _HOST_RE, "network"),
        "credentialRefs": clean(b.credentialRefs, _CRED_RE, "credentialRefs"),
    }


def _covered(path: str, prefixes: list[str]) -> bool:
    def noise(value: str) -> str:
        # Only cosmetic slash noise is equivalent: a leading ./, a trailing /
        # and doubled /. Keep a leading /, .., internal . and symlinks
        # significant; a bare ./ keeps its single . part so it cannot turn into
        # the empty prefix (which the old comparison reads as absolute).
        absolute = value.startswith("/")
        parts = [p for p in value.split("/") if p]
        while parts and parts[0] == "." and len(parts) > 1:
            parts.pop(0)
        cleaned = "/".join(parts)
        return ("/" if absolute else "") + cleaned

    path_key = noise(path)
    in_workspace = (bool(path_key) and not path_key.startswith("/")
                    and ".." not in path_key.split("/"))
    for q in prefixes:
        if path == q or path.startswith(q.rstrip("/") + "/"):
            return True  # the batch 2 answer is never taken away
        prefix_key = noise(q)
        if prefix_key == "." and in_workspace:
            return True  # a . scope is the whole workspace (scope_roots -> None)
        if path_key == prefix_key or path_key.startswith(prefix_key.rstrip("/") + "/"):
            return True
    return False


def _exceeds(scope: dict[str, list[str]], ceil: dict[str, list[str]]) -> list[str]:
    """Dimensions of ``scope`` that are not within ``ceil`` (empty list = within)."""
    out = [k for k in ("permissions", "tools", "network", "credentialRefs")
           if not set(scope[k]) <= set(ceil[k])]
    if any(not _covered(x, ceil["paths"]) for x in scope["paths"]):
        out.append("paths")
    return out


def _def_ceiling(d: Mapping[str, Any]) -> dict[str, list[str]]:
    return {"permissions": json.loads(d["permissions"]), "tools": json.loads(d["tool_permissions"]),
            "paths": json.loads(d["allowed_paths"]), "network": json.loads(d["network_allow"]),
            "credentialRefs": json.loads(d["credential_refs"])}


async def _org(db: Any, team_id: str) -> str | None:
    try:
        return await team_org(db, team_id)
    except Exception as e:  # noqa: BLE001 - only a schema without orgs means "no org"
        m = str(e).lower()
        if "no such column" in m or "does not exist" in m or "no such table" in m:
            await db.rollback()
            return None
        raise


async def missing_permissions(db: Any, p: Principal, wanted: list[str], run_id: str,
                              project_id: str | None) -> list[str]:
    """Permissions in ``wanted`` the DELEGATING USER does not hold at this run (anti-escalation).

    Uses the org policy engine when the team belongs to an organization, otherwise the pure
    role defaults (``missing_for_grant``). Same inputs as the HTTP policy gate.
    """
    now = db_now()
    org = await _org(db, p.teamId)
    nodes = [Node("org", org or "-"), Node("team", p.teamId)]
    if project_id:
        nodes.append(Node("project", project_id))
    nodes.append(Node("run", run_id))
    if org:
        denied = []
        for w in sorted(set(wanted)):
            if not (await decide_request(db, org, p, w, nodes, now)).allowed:
                denied.append(w)
        return denied
    subject = Subject(p.userId, (Binding("team", p.teamId, p.role.value),))
    return missing_for_grant(subject, wanted, nodes, now=now)


# ------------------------------------------------------------- model fallback


def _model_unavailable(model: str) -> bool:
    listed = {m.strip() for m in os.environ.get(UNAVAILABLE_ENV, "").split(",") if m.strip()}
    if model in listed:
        return True
    breaker = getattr(getattr(_svc(), "engine_runtime", None), "breaker", None)
    return bool(breaker is not None
                and breaker.is_open(ProviderCircuitBreaker.key_for("deepseek-official", model)))


def _resolve_model(d: Mapping[str, Any]) -> tuple[str, str | None]:
    """(model to use, fallback reason). Only the fallback the approved definition declares."""
    if not _model_unavailable(d["model"]):
        return d["model"], None
    fb = d["fallback_model"]
    if fb and not _model_unavailable(fb):
        return fb, f"primary model {d['model']} unavailable"
    raise _http(503, "AGENT_MODEL_UNAVAILABLE",
                "the agent's model is unavailable and no usable fallback is declared")


# ------------------------------------------------------------- row helpers


async def _definition(db: Any, p: Principal, agent_id: str) -> Mapping[str, Any]:
    row = (await db.execute(text("SELECT * FROM agent_definitions WHERE id=:i"),
                            {"i": agent_id})).mappings().first()
    if row is None or row["team_id"] != p.teamId:
        raise _http(404, "NOT_FOUND", "agent not found")
    return row


async def _assignment(db: Any, p: Principal, run_id: str, aid: str) -> Mapping[str, Any]:
    row = (await db.execute(text("SELECT * FROM agent_assignments WHERE id=:i"),
                            {"i": aid})).mappings().first()
    if row is None or row["run_id"] != run_id or row["team_id"] != p.teamId:
        raise _http(404, "NOT_FOUND", "assignment not found")
    return row


async def _live_descendants(db: Any, root_id: str) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    frontier = [root_id]
    while frontier:
        nxt: list[str] = []
        for pid in frontier:
            rows = (await db.execute(
                text("SELECT * FROM agent_assignments WHERE parent_id=:p AND state IN"
                     " ('active','paused') ORDER BY created_at, id"), {"p": pid})).mappings().all()
            out.extend(rows)
            nxt.extend(r["id"] for r in rows)
        frontier = nxt
    return out


def _out(r: Mapping[str, Any], calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    budget, reserved, spent = r["budget_microusd"], r["reserved_microusd"], r["spent_microusd"]
    return {
        "id": r["id"], "runId": r["run_id"], "parentId": r["parent_id"],
        "childRunId": r.get("child_run_id"),  # W28 S1b; None for plain spawns / pre-0075 rows
        "agentId": r["agent_id"], "agentName": r["agent_name"],
        "agentVersion": r["agent_version"], "depth": r["depth"], "title": r["title"],
        "state": r["state"], "pauseReason": r["pause_reason"],
        "delegatedBy": r["delegated_by"], "scope": json.loads(r["scope"]),
        "limits": {"maxDepth": r["max_depth"], "maxChildren": r["max_children"],
                   "liveChildren": r["children_count"]},
        "budget": {"allocatedUsd": _usd(budget), "reservedForChildrenUsd": _usd(reserved),
                   "spentUsd": _usd(spent),
                   "remainingUsd": _usd(max(0, budget - reserved - spent))},
        "model": {"requested": r["model_requested"], "used": r["model_used"],
                  "fallbackReason": r["fallback_reason"]},
        "progress": {"percent": r["progress_percent"], "note": r["progress_note"]},
        "createdAt": _iso(r["created_at"]), "updatedAt": _iso(r["updated_at"]),
        "endedAt": _iso(r["ended_at"]),
        **({"recentToolCalls": calls} if calls is not None else {}),
    }


def _call_out(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "assignmentId": r["assignment_id"], "tool": r["tool"],
            "target": r["target"], "summary": r["summary"], "outcome": r["outcome"],
            "fallbackTool": r["fallback_tool"], "costUsd": _usd(r["cost_microusd"]),
            "durationMs": r["duration_ms"], "reportedBy": r["reported_by"],
            "createdAt": _iso(r["created_at"])}


async def _stop_child_runs(db: Any, root_id: str, reason: str) -> None:
    """Stop the run of ``root_id`` and of every descendant (any state). Idempotent.

    A delegated assignment owns a run (W28 S1b); cancelling the assignment must not leave that run
    live. Called after the cancel commits and again on a repeated cancel, so a failed stop is
    retried instead of leaving an orphan.
    """
    seen, queue = [], [root_id]
    while queue:
        cur = queue.pop()
        row = (await db.execute(text("SELECT child_run_id FROM agent_assignments WHERE id=:i"),
                                {"i": cur})).mappings().first()
        if row and row["child_run_id"]:
            seen.append(row["child_run_id"])
        queue += [r["id"] for r in (await db.execute(
            text("SELECT id FROM agent_assignments WHERE parent_id=:p"), {"p": cur})).mappings()]
    for run_id in seen:
        await _svc().stop_agent(run_id, reason=reason)


async def _terminal(db: Any, row_id: str, new_state: str, now: Any) -> bool:
    """CAS a LEAF assignment to a terminal state and roll its budget up. True when it moved."""
    r = await db.execute(
        text("UPDATE agent_assignments SET state=:n, pause_reason=NULL, ended_at=:t, updated_at=:t"
             " WHERE id=:i AND state IN ('active','paused') AND children_count=0"),
        {"n": new_state, "t": now, "i": row_id})
    if r.rowcount != 1:
        return False
    cur = (await db.execute(text("SELECT parent_id, run_id, budget_microusd, spent_microusd"
                                 " FROM agent_assignments WHERE id=:i"),
                            {"i": row_id})).mappings().one()
    args = {"b": cur["budget_microusd"], "s": cur["spent_microusd"]}
    if cur["parent_id"]:
        await db.execute(
            text("UPDATE agent_assignments SET children_count=children_count-1,"
                 " reserved_microusd=reserved_microusd-:b, spent_microusd=spent_microusd+:s"
                 " WHERE id=:p"), {**args, "p": cur["parent_id"]})
    else:
        await db.execute(
            text("UPDATE agent_run_pools SET reserved_microusd=reserved_microusd-:b,"
                 " spent_microusd=spent_microusd+:s WHERE run_id=:r"),
            {**args, "r": cur["run_id"]})
    return True


# ------------------------------------------------------------- spawn


class SpawnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agentId: str = Field(max_length=36)
    title: str = Field(min_length=1, max_length=200)
    parentAssignmentId: str | None = Field(default=None, max_length=36)
    scope: ScopeBody = Field(default_factory=ScopeBody)
    budgetUsd: Decimal = Field(default=Decimal("0"), ge=0, le=1000)
    requestId: str | None = Field(default=None, max_length=64)


async def _existing_by_request(db: Any, run_id: str, request_id: str | None):
    if not request_id:
        return None
    return (await db.execute(
        text("SELECT * FROM agent_assignments WHERE run_id=:r AND request_id=:q"),
        {"r": run_id, "q": request_id})).mappings().first()


async def _mint_child(
    factory: Any,
    p: Principal,
    run_id: str,
    *,
    request_id: str | None,
    parent_assignment_id: str | None,
    agent_id: str,
    title: str,
    scope: dict[str, list[str]],
    budget_microusd: int,
) -> tuple[dict[str, Any], bool]:
    """Mint one assignment behind the full gate chain.

    The single implementation shared by ``spawn`` and the W28 delegate verb.
    Returns ``(out, duplicate)``. Anything ending in a refusal raises; the
    caller never sees a half-minted child.
    """
    async with factory() as db:
        dup = await _existing_by_request(db, run_id, request_id)
        if dup is not None:
            return _out(dup), True
        d = await _definition(db, p, agent_id)
        parent = (
            await _assignment(db, p, run_id, parent_assignment_id)
            if parent_assignment_id
            else None
        )
        project_id = (await db.execute(text("SELECT project_id FROM agent_runs WHERE id=:r"),
                                       {"r": run_id})).scalar()
        missing = await missing_permissions(db, p, scope["permissions"], run_id, project_id)
    ctx = {"agentId": d["id"], "parentId": parent["id"] if parent else None}

    if d["state"] != "approved":
        await _refuse(p, run_id, 409, "AGENT_NOT_APPROVED",
                      f"agent version is {d['state']}; only approved agents can be spawned",
                      detail=ctx)
    if parent is not None:
        if parent["state"] != "active":
            await _refuse(p, run_id, 409, "PARENT_NOT_ACTIVE",
                          f"parent assignment is {parent['state']}", detail=ctx)
        if parent["max_children"] == 0:
            await _refuse(p, run_id, 409, "SPAWN_NOT_ALLOWED",
                          "the parent agent may not spawn subagents", detail=ctx)
        if parent["depth"] + 1 > parent["max_depth"]:
            await _refuse(p, run_id, 409, "SPAWN_DEPTH_EXCEEDED",
                          f"depth limit {parent['max_depth']} reached", detail=ctx)
    over = _exceeds(scope, _def_ceiling(d))
    if over:
        await _refuse(p, run_id, 403, "SCOPE_EXCEEDS_AGENT",
                      f"requested scope exceeds the agent definition: {over}",
                      action="agent.delegation_denied", detail={**ctx, "exceeds": over})
    if parent is not None:
        over = _exceeds(scope, json.loads(parent["scope"]))
        if over:
            await _refuse(p, run_id, 403, "SCOPE_EXCEEDS_PARENT",
                          f"requested scope exceeds the parent assignment: {over}",
                          action="agent.delegation_denied", detail={**ctx, "exceeds": over})
    if missing:
        await _refuse(p, run_id, 403, "DELEGATION_DENIED",
                      f"you do not hold {missing}; an agent cannot receive more than you have",
                      action="agent.delegation_denied", detail={**ctx, "missing": missing})
    if budget_microusd > d["max_budget_microusd"]:
        await _refuse(p, run_id, 422, "BUDGET_EXCEEDS_AGENT_MAX",
                      f"budget exceeds the agent maximum of {_usd(d['max_budget_microusd'])} USD",
                      detail=ctx)
    try:
        model, fb_reason = _resolve_model(d)
    except HTTPException as e:
        await _refuse(p, run_id, e.status_code, "AGENT_MODEL_UNAVAILABLE",
                      e.detail["error"]["message"], detail=ctx)
    blocked = await check_budget(factory, p.teamId, estimated_call_cost_usd=0.0)
    if not blocked.allowed:
        await _refuse(p, run_id, 409, "TEAM_BUDGET_EXHAUSTED",
                      f"team budget does not allow new agent work: {blocked.reason}", detail=ctx)

    depth = parent["depth"] + 1 if parent else 0
    max_depth = parent["max_depth"] if parent else min(d["max_depth"], _env_int(DEPTH_ENV, "depth"))
    max_children = min(d["max_children"], _env_int(CHILDREN_ENV, "children"))
    aid, now = str(uuid4()), db_now()
    pool_budget = _micro(Decimal(os.environ.get(RUN_BUDGET_ENV, "5")))
    async with factory() as db:  # write phase: first statement is a write, one transaction
        await db.execute(
            text("INSERT INTO agent_run_pools (run_id, team_id, max_agents, total_spawned,"
                 " budget_microusd, reserved_microusd, spent_microusd, created_at)"
                 " VALUES (:r,:t,:m,0,:b,0,0,:n) ON CONFLICT (run_id) DO NOTHING"),
            {"r": run_id, "t": p.teamId, "m": _env_int(RUN_AGENTS_ENV, "agents"),
             "b": pool_budget, "n": now})
        if parent is None:  # a root reserves from the run pool, a child from its parent
            pool_sql = ("UPDATE agent_run_pools SET total_spawned=total_spawned+1,"
                        " reserved_microusd=reserved_microusd+:b WHERE run_id=:r"
                        " AND total_spawned<max_agents"
                        " AND reserved_microusd+spent_microusd+:b<=budget_microusd")
            pool_args: dict[str, Any] = {"r": run_id, "b": budget_microusd}
        else:
            pool_sql = ("UPDATE agent_run_pools SET total_spawned=total_spawned+1"
                        " WHERE run_id=:r AND total_spawned<max_agents")
            pool_args = {"r": run_id}
        got = await db.execute(text(pool_sql), pool_args)
        pool_ok = got.rowcount == 1
        parent_ok = True
        if pool_ok and parent is not None:
            got = await db.execute(
                text("UPDATE agent_assignments SET children_count=children_count+1,"
                     " reserved_microusd=reserved_microusd+:b, updated_at=:n"
                     " WHERE id=:p AND state='active' AND children_count<max_children"
                     " AND spent_microusd+reserved_microusd+:b<=budget_microusd"),
                {"p": parent["id"], "b": budget_microusd, "n": now})
            parent_ok = got.rowcount == 1
        if not (pool_ok and parent_ok):
            await db.rollback()
            code, msg = await _limit_reason(factory, run_id, parent, pool_ok)
            await _refuse(p, run_id, 409, code, msg,
                          detail={**ctx, "budgetMicroUsd": budget_microusd})
        try:
            await db.execute(
                text("INSERT INTO agent_assignments (id, run_id, team_id, parent_id, agent_id,"
                     " agent_name, agent_version, depth, title, state, delegated_by, scope,"
                     " max_depth, max_children, budget_microusd, model_requested, model_used,"
                     " fallback_reason, request_id, created_at, updated_at) VALUES (:id,:r,:t,:p,"
                     ":a,:an,:av,:dp,:ti,'active',:u,:sc,:md,:mc,:b,:mr,:mu,:fr,:q,:n,:n)"),
                {"id": aid, "r": run_id, "t": p.teamId, "p": parent["id"] if parent else None,
                 "a": d["id"], "an": d["name"], "av": d["version"], "dp": depth,
                 "ti": title, "u": p.userId, "sc": json.dumps(scope, sort_keys=True),
                 "md": max_depth, "mc": max_children, "b": budget_microusd, "mr": d["model"],
                 "mu": model,
                 "fr": fb_reason, "q": request_id, "n": now})
        except IntegrityError:  # concurrent retry with the same requestId
            await db.rollback()
            async with factory() as db2:
                dup = await _existing_by_request(db2, run_id, request_id)
            if dup is None:
                raise
            return _out(dup), True
        await _audit(db, p, "agent.spawned", aid, {
            "runId": run_id, "agentId": d["id"], "agentName": d["name"], "version": d["version"],
            "parentId": parent["id"] if parent else None, "depth": depth,
            "scope": {k: scope[k] for k in ("permissions", "tools", "paths", "network")},
            "refCount": len(scope["credentialRefs"]), "budgetMicroUsd": budget_microusd,
            "model": model})
        if fb_reason:
            await _audit(db, p, "agent.fallback_used", aid, {
                "kind": "model", "from": d["model"], "to": model, "reason": fb_reason})
        await db.commit()
        row = (await db.execute(text("SELECT * FROM agent_assignments WHERE id=:i"),
                                {"i": aid})).mappings().one()
    return _out(row), False


@router.post("/runs/{runId}/agents/spawn", status_code=201)
async def spawn(runId: str, body: SpawnBody, response: Response, p: Writer) -> dict[str, Any]:
    scope = _norm_scope(body.scope)
    budget = _micro(body.budgetUsd)
    row, duplicate = await _mint_child(
        _factory(), p, runId,
        request_id=body.requestId,
        parent_assignment_id=body.parentAssignmentId,
        agent_id=body.agentId,
        title=body.title.strip(),
        scope=scope,
        budget_microusd=budget,
    )
    if duplicate:
        response.status_code = 200
        return {**row, "duplicate": True}
    return row


async def _limit_reason(factory: Any, run_id: str, parent: Mapping[str, Any] | None,
                        pool_ok: bool) -> tuple[str, str]:
    async with factory() as db:
        if not pool_ok:
            pool = (await db.execute(text("SELECT * FROM agent_run_pools WHERE run_id=:r"),
                                     {"r": run_id})).mappings().one()
            if pool["total_spawned"] >= pool["max_agents"]:
                return "SPAWN_RUN_LIMIT", f"run agent limit {pool['max_agents']} reached"
            return "SPAWN_BUDGET_EXCEEDED", "the run's agent budget pool cannot cover this budget"
        cur = (await db.execute(text("SELECT * FROM agent_assignments WHERE id=:i"),
                                {"i": parent["id"]})).mappings().one()
    if cur["state"] != "active":
        return "PARENT_NOT_ACTIVE", f"parent assignment is {cur['state']}"
    if cur["children_count"] >= cur["max_children"]:
        return "SPAWN_CHILDREN_EXCEEDED", f"live children limit {cur['max_children']} reached"
    return "SPAWN_BUDGET_EXCEEDED", "the parent's remaining budget cannot cover this budget"


# ------------------------------------------------------------- tracking view


def _line(r: Mapping[str, Any]) -> str:
    pct = f" ({r['progress_percent']}%)" if r["progress_percent"] is not None else ""
    return f"{r['agent_name']} v{r['agent_version']}: {r['title']} - {r['state']}{pct}"


@router.get("/runs/{runId}/agents")
async def list_agents(runId: str, p: Reader) -> dict[str, Any]:
    async with _factory()() as db:
        rows = (await db.execute(
            text("SELECT * FROM agent_assignments WHERE run_id=:r AND team_id=:t"
                 " ORDER BY created_at, id"), {"r": runId, "t": p.teamId})).mappings().all()
        calls = (await db.execute(
            text("SELECT * FROM agent_tool_calls WHERE run_id=:r AND team_id=:t"
                 " ORDER BY created_at DESC, id LIMIT 300"),
            {"r": runId, "t": p.teamId})).mappings().all()
        pool = (await db.execute(text("SELECT * FROM agent_run_pools WHERE run_id=:r"),
                                 {"r": runId})).mappings().first()
    recent: dict[str, list[dict[str, Any]]] = {}
    for c in calls:
        lst = recent.setdefault(c["assignment_id"], [])
        if len(lst) < 3:
            lst.append(_call_out(c))
    items = [_out(r, recent.get(r["id"], [])) for r in rows]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {
        "items": items,
        "summary": {"total": len(rows), "byState": counts,
                    "lines": [_line(r) for r in rows]},
        "pool": None if pool is None else {
            "maxAgents": pool["max_agents"], "totalSpawned": pool["total_spawned"],
            "budgetUsd": _usd(pool["budget_microusd"]),
            "reservedUsd": _usd(pool["reserved_microusd"]),
            "spentUsd": _usd(pool["spent_microusd"])},
    }


# ------------------------------------------------------------- pause / resume / cancel


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=500)


class ReassignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agentId: str = Field(max_length=36)
    reason: str | None = Field(default=None, max_length=500)


async def _cas_state(db: Any, row_id: str, frm: str, to: str, reason: str | None, now: Any) -> bool:
    r = await db.execute(
        text("UPDATE agent_assignments SET state=:to, pause_reason=:pr, updated_at=:n"
             " WHERE id=:i AND state=:f"),
        {"to": to, "pr": reason, "n": now, "i": row_id, "f": frm})
    return r.rowcount == 1


@router.post("/runs/{runId}/agents/{assignmentId}/pause")
async def pause(runId: str, assignmentId: str, p: Writer,
                body: ReasonBody | None = None) -> dict[str, Any]:
    reason = body.reason if body else None
    async with _factory()() as db:
        row = await _assignment(db, p, runId, assignmentId)
        if row["state"] == "paused":
            return {**_out(row), "changed": False}
        if row["state"] != "active":
            raise _illegal("pause", row["state"])
        now = db_now()
        changed = []
        if await _cas_state(db, row["id"], "active", "paused", "user", now):
            changed.append((row, False))
        for d in await _live_descendants(db, row["id"]):  # parent first: no new children appear
            if d["state"] == "active" and await _cas_state(db, d["id"], "active", "paused",
                                                           "parent_paused", now):
                changed.append((d, True))
        for n, cascade in changed:
            await _audit(db, p, "agent.paused", n["id"], {
                "runId": runId, "before": {"state": "active"}, "after": {"state": "paused"},
                "cascade": cascade, "reason": reason})
        await db.commit()
        return {**_out(await _assignment(db, p, runId, assignmentId)), "changed": bool(changed)}


@router.post("/runs/{runId}/agents/{assignmentId}/resume")
async def resume(runId: str, assignmentId: str, p: Writer) -> dict[str, Any]:
    async with _factory()() as db:
        row = await _assignment(db, p, runId, assignmentId)
        if row["state"] == "active":
            return {**_out(row), "changed": False}
        if row["state"] != "paused":
            raise _illegal("resume", row["state"])
        if row["parent_id"]:
            par = (await db.execute(text("SELECT state FROM agent_assignments WHERE id=:i"),
                                    {"i": row["parent_id"]})).scalar()
            if par != "active":
                raise _http(409, "PARENT_NOT_ACTIVE", "resume the parent assignment first")
        if row["spent_microusd"] + row["reserved_microusd"] > row["budget_microusd"]:
            raise _http(409, "AGENT_BUDGET_EXHAUSTED", "assignment is over its budget")
        d = (await db.execute(text("SELECT * FROM agent_definitions WHERE id=:i"),
                              {"i": row["agent_id"]})).mappings().one()
        model, fb_reason = _resolve_model(d)
        now = db_now()
        if not await _cas_state(db, row["id"], "paused", "active", None, now):
            raise _http(409, "ILLEGAL_TRANSITION", "assignment state changed concurrently")
        nodes = [(row, False)]
        for ch in await _live_descendants(db, row["id"]):
            if ch["state"] == "paused" and ch["pause_reason"] == "parent_paused" and \
                    await _cas_state(db, ch["id"], "paused", "active", None, now):
                nodes.append((ch, True))
        if model != row["model_used"]:
            await db.execute(text("UPDATE agent_assignments SET model_used=:m, fallback_reason=:f"
                                  " WHERE id=:i"), {"m": model, "f": fb_reason, "i": row["id"]})
            if fb_reason:
                await _audit(db, p, "agent.fallback_used", row["id"], {
                    "kind": "model", "from": row["model_used"], "to": model, "reason": fb_reason})
        for n, cascade in nodes:
            await _audit(db, p, "agent.resumed", n["id"], {
                "runId": runId, "before": {"state": "paused"}, "after": {"state": "active"},
                "cascade": cascade})
        await db.commit()
        return {**_out(await _assignment(db, p, runId, assignmentId)), "changed": True}


@router.post("/runs/{runId}/agents/{assignmentId}/cancel")
async def cancel(runId: str, assignmentId: str, p: Writer,
                 body: ReasonBody | None = None) -> dict[str, Any]:
    reason = body.reason if body else None
    async with _factory()() as db:
        row = await _assignment(db, p, runId, assignmentId)
        if row["state"] == "cancelled":
            await _stop_child_runs(db, assignmentId, "assignment_cancelled")  # retry-safe
            return {**_out(row), "changed": False}
        if row["state"] not in LIVE:
            raise _illegal("cancel", row["state"])
        # Leaves first. A child spawned mid-cancel makes the parent's leaf-CAS fail, so repeat
        # until the root is terminal (a parent that is not active cannot gain new children).
        for _ in range(_CAP["depth"][2] + 3):
            cur = await _assignment(db, p, runId, assignmentId)
            if cur["state"] not in LIVE:
                break
            nodes = [cur, *await _live_descendants(db, assignmentId)]
            for n in reversed(nodes):
                before = n["state"]
                if await _terminal(db, n["id"], "cancelled", db_now()):
                    await _audit(db, p, "agent.cancelled", n["id"], {
                        "runId": runId, "before": {"state": before},
                        "after": {"state": "cancelled"}, "cascade": n["id"] != assignmentId,
                        "reason": reason})
        else:
            await db.rollback()
            raise _http(409, "CONCURRENT_SPAWN", "assignment kept gaining children; retry")
        await db.commit()
        await _stop_child_runs(db, assignmentId, "assignment_cancelled")
        return {**_out(await _assignment(db, p, runId, assignmentId)), "changed": True}


@router.post("/runs/{runId}/agents/{assignmentId}/reassign")
async def reassign(runId: str, assignmentId: str, body: ReassignBody, p: Writer) -> dict[str, Any]:
    """Hand the assignment to another APPROVED agent; scope and limits never grow."""
    factory = _factory()
    async with factory() as db:
        row = await _assignment(db, p, runId, assignmentId)
        if row["state"] not in LIVE:
            raise _illegal("reassign", row["state"])
        if body.agentId == row["agent_id"]:
            return {**_out(row), "changed": False}
        d = await _definition(db, p, body.agentId)
        scope = json.loads(row["scope"])
        project_id = (await db.execute(text("SELECT project_id FROM agent_runs WHERE id=:r"),
                                       {"r": runId})).scalar()
        missing = await missing_permissions(db, p, scope["permissions"], runId, project_id)
    ctx = {"assignmentId": assignmentId, "agentId": d["id"]}
    if d["state"] != "approved":
        await _refuse(p, runId, 409, "AGENT_NOT_APPROVED", f"agent version is {d['state']}",
                      detail=ctx)
    over = _exceeds(scope, _def_ceiling(d))
    if over:
        await _refuse(p, runId, 403, "SCOPE_EXCEEDS_AGENT",
                      f"the assignment's scope exceeds the new agent: {over}",
                      action="agent.delegation_denied", detail={**ctx, "exceeds": over})
    if missing:
        await _refuse(p, runId, 403, "DELEGATION_DENIED",
                      f"you do not hold {missing}; you cannot hand it to an agent",
                      action="agent.delegation_denied", detail={**ctx, "missing": missing})
    try:
        model, fb_reason = _resolve_model(d)
    except HTTPException as e:
        await _refuse(p, runId, e.status_code, "AGENT_MODEL_UNAVAILABLE",
                      e.detail["error"]["message"], detail=ctx)
    async with factory() as db:
        row = await _assignment(db, p, runId, assignmentId)
        r = await db.execute(
            text("UPDATE agent_assignments SET agent_id=:a, agent_name=:an, agent_version=:av,"
                 " max_children=:mc, model_requested=:mr, model_used=:mu,"
                 " fallback_reason=:fr, updated_at=:n WHERE id=:i AND agent_id=:old"
                 " AND state IN ('active','paused')"),
            {"a": d["id"], "an": d["name"], "av": d["version"],
             "mc": min(row["max_children"], d["max_children"]),
             "mr": d["model"], "mu": model, "fr": fb_reason, "n": db_now(), "i": assignmentId,
             "old": row["agent_id"]})
        if r.rowcount != 1:
            await db.rollback()
            raise _http(409, "ILLEGAL_TRANSITION", "assignment changed concurrently; retry")
        await _audit(db, p, "agent.reassigned", assignmentId, {
            "runId": runId, "reason": body.reason,
            "before": {"agentId": row["agent_id"], "name": row["agent_name"],
                       "version": row["agent_version"], "model": row["model_used"]},
            "after": {"agentId": d["id"], "name": d["name"], "version": d["version"],
                      "model": model}})
        if fb_reason:
            await _audit(db, p, "agent.fallback_used", assignmentId, {
                "kind": "model", "from": d["model"], "to": model, "reason": fb_reason})
        await db.commit()
        return {**_out(await _assignment(db, p, runId, assignmentId)), "changed": True}


# ------------------------------------------------------------- tool-call summaries


class ProgressBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    percent: int = Field(ge=0, le=100)
    note: str = Field(default="", max_length=280)


class ToolCallBody(BaseModel):
    """Summary and outcome only. ``extra=forbid`` rejects fields such as ``reasoning`` or
    ``thoughts`` (422), so hidden reasoning has nowhere to be stored."""

    model_config = ConfigDict(extra="forbid")
    tool: str = Field(max_length=80)
    target: str | None = Field(default=None, max_length=200)
    summary: str = Field(min_length=1, max_length=500)
    outcome: Literal["ok", "error", "unavailable"]
    costUsd: Decimal = Field(default=Decimal("0"), ge=0, le=1000)
    durationMs: int | None = Field(default=None, ge=0, le=3_600_000)
    requestId: str | None = Field(default=None, max_length=64)
    progress: ProgressBody | None = None
    finish: Literal["completed", "failed"] | None = None


@router.post("/runs/{runId}/agents/{assignmentId}/tool-calls", status_code=201)
async def record_tool_call(runId: str, assignmentId: str, body: ToolCallBody, response: Response,
                           p: Writer) -> dict[str, Any]:
    if not _TOOL_RE.match(body.tool):
        raise _http(422, "VALIDATION_ERROR", "tool: invalid identifier")
    summary = _SECRETISH.sub("[redacted]", body.summary.strip())
    target = _SECRETISH.sub("[redacted]", body.target.strip()) if body.target else None
    cost = _micro(body.costUsd)
    async with _factory()() as db:
        row = await _assignment(db, p, runId, assignmentId)
        if body.requestId:
            prior = (await db.execute(
                text("SELECT * FROM agent_tool_calls WHERE assignment_id=:a AND request_id=:q"),
                {"a": assignmentId, "q": body.requestId})).mappings().first()
            if prior is not None:
                response.status_code = 200
                return {**_call_out(prior), "duplicate": True}
        if row["state"] not in LIVE:
            raise _http(409, "ASSIGNMENT_NOT_ACTIVE", f"assignment is {row['state']}")
        if body.finish and row["children_count"] > 0:
            raise _http(409, "CHILDREN_ACTIVE", "cancel or finish the live subagents first")
        scope = json.loads(row["scope"])
        d = (await db.execute(text("SELECT * FROM agent_definitions WHERE id=:i"),
                              {"i": row["agent_id"]})).mappings().first()
        in_scope = body.tool in scope["tools"]
        outcome = body.outcome if in_scope else "denied"
        fallback = None
        if in_scope and outcome == "unavailable" and d is not None:
            alt = json.loads(d["tool_fallbacks"]).get(body.tool)
            fallback = alt if alt in scope["tools"] else None
        prog = body.progress if in_scope else None
        finish = body.finish if in_scope else None
        now, cid = db_now(), str(uuid4())
        upd = await db.execute(
            text("UPDATE agent_assignments SET spent_microusd=spent_microusd+:c, updated_at=:n,"
                 " progress_percent=COALESCE(:pp, progress_percent),"
                 " progress_note=COALESCE(:pn, progress_note)"
                 " WHERE id=:i AND state IN ('active','paused')"),
            {"c": cost if in_scope else 0, "n": now, "i": assignmentId,
             "pp": prog.percent if prog else None, "pn": prog.note if prog else None})
        if upd.rowcount != 1:
            await db.rollback()
            raise _http(409, "ASSIGNMENT_NOT_ACTIVE", "assignment ended concurrently")
        await db.execute(
            text("INSERT INTO agent_tool_calls (id, assignment_id, run_id, team_id, tool, target,"
                 " summary, outcome, fallback_tool, cost_microusd, duration_ms, reported_by,"
                 " request_id, created_at)"
                 " VALUES (:id,:a,:r,:t,:tool,:tg,:s,:o,:fb,:c,:d,:u,:q,:n)"),
            {"id": cid, "a": assignmentId, "r": runId, "t": p.teamId, "tool": body.tool,
             "tg": target, "s": summary, "o": outcome, "fb": fallback,
             "c": cost if in_scope else 0, "d": body.durationMs, "u": p.userId,
             "q": body.requestId, "n": now})
        await _audit(db, p, "agent.tool_called", assignmentId, {
            "runId": runId, "tool": body.tool, "outcome": outcome, "fallbackTool": fallback})
        if fallback:
            await _audit(db, p, "agent.fallback_used", assignmentId, {
                "kind": "tool", "from": body.tool, "to": fallback})
        cur = (await db.execute(text("SELECT * FROM agent_assignments WHERE id=:i"),
                                {"i": assignmentId})).mappings().one()
        exceeded = cur["spent_microusd"] + cur["reserved_microusd"] > cur["budget_microusd"]
        if exceeded and cur["state"] == "active" and await _cas_state(
                db, assignmentId, "active", "paused", "budget_exhausted", now):
            await _audit(db, p, "agent.paused", assignmentId, {
                "runId": runId, "before": {"state": "active"}, "after": {"state": "paused"},
                "reason": "budget_exhausted"})
        if finish and not await _terminal(db, assignmentId, finish, now):
            await db.rollback()
            raise _http(409, "CHILDREN_ACTIVE", "assignment gained children; retry")
        await db.commit()
        call = (await db.execute(text("SELECT * FROM agent_tool_calls WHERE id=:i"),
                                 {"i": cid})).mappings().one()
        final = await _assignment(db, p, runId, assignmentId)
    if not in_scope:
        raise _http(403, "TOOL_NOT_IN_SCOPE",
                    f"tool {body.tool!r} is not in this assignment's scope (recorded as denied)")
    out = {**_call_out(call), "budgetExceeded": exceeded, "assignmentState": final["state"]}
    if body.outcome == "unavailable":
        out["fallback"] = {"kind": "tool", "tool": fallback} if fallback else None
    return out
