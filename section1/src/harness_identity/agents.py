"""Agent registry (master prompt part 45, Session 4 lane A). See docs/agents.md.

Raw SQLAlchemy Core over migration 0044. Posture: deny by default, team-scoped, an
agent the caller cannot see answers 404 (no existence oracle), every consequential
change is audited. Runtime spawn / tracking lives in section4 ``agent_tree.py``.
"""

import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from harness_contracts import Principal, Role, error_body
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .audit_emit import _emit as emit_audit
from .auth import get_principal
from .timeutil import now_utc

router = APIRouter(prefix="/api/v1/agents")

STATES = ("proposed", "approved", "retired")
# An agent may never hold approve / administer / billing / export: humans decide those.
AGENT_PERMISSIONS = ("read", "comment", "steer")
HARD_MAX_DEPTH = 5
HARD_MAX_CHILDREN = 10
MAX_BUDGET_USD = Decimal("1000")
MICRO = 1_000_000
SELF_APPROVAL_ENV = "HARNESS_AGENT_ALLOW_SELF_APPROVAL"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,79}$")
_CAP_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,39}$")
_TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,59}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
_CRED_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _err(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, msg))


def _iso(v: Any) -> str | None:
    return None if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))


def _self_approval_allowed() -> bool:
    return os.environ.get(SELF_APPROVAL_ENV, "") == "1"


# ------------------------------------------------------------- validation


def _str_list(v: list[str], rx: re.Pattern[str], what: str, limit: int) -> list[str]:
    out = sorted({s.strip() for s in v})
    if len(out) > limit or any(not rx.match(s) for s in out):
        raise _err(422, "VALIDATION_ERROR",
                   f"{what}: at most {limit} entries matching {rx.pattern}")
    return out


def _paths(v: list[str]) -> list[str]:
    out = sorted({p.strip() for p in v})
    for p in out:
        if (not p or len(p) > 256 or "\x00" in p or "\\" in p or p.startswith("/")
                or ".." in p.split("/")):
            raise _err(422, "VALIDATION_ERROR",
                       "allowedPaths must be relative path prefixes without '..'")
    if len(out) > 50:
        raise _err(422, "VALIDATION_ERROR", "allowedPaths: at most 50 entries")
    return out


def _budget_micro(v: Decimal) -> int:
    try:
        if v < 0 or v > MAX_BUDGET_USD:
            raise InvalidOperation
        return int((v * MICRO).to_integral_value())
    except InvalidOperation as e:
        raise _err(422, "VALIDATION_ERROR",
                   f"maxBudgetUsd must be between 0 and {MAX_BUDGET_USD}") from e


class DefBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=200)
    description: str = Field(default="", max_length=500)
    capabilities: list[str] = Field(default_factory=list, max_length=60)
    toolPermissions: list[str] = Field(default_factory=list, max_length=100)
    permissions: list[str] = Field(default_factory=lambda: ["read"], max_length=10)
    allowedPaths: list[str] = Field(default_factory=list, max_length=100)
    networkAllow: list[str] = Field(default_factory=list, max_length=40)
    credentialRefs: list[str] = Field(default_factory=list, max_length=40)
    model: str = Field(max_length=200)
    fallbackModel: str | None = Field(default=None, max_length=200)
    toolFallbacks: dict[str, str] = Field(default_factory=dict, max_length=50)
    maxDepth: int = Field(default=1, ge=0, le=HARD_MAX_DEPTH)
    maxChildren: int = Field(default=3, ge=0, le=HARD_MAX_CHILDREN)
    maxBudgetUsd: Decimal = Field(default=Decimal("1"))


class SelectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: list[str] = Field(default_factory=list, max_length=60)
    tools: list[str] = Field(default_factory=list, max_length=100)
    name: str | None = Field(default=None, max_length=80)


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=500)


def validate_def(b: DefBody) -> dict[str, Any]:
    """Normalised column values for a definition body (422 on anything off-schema)."""
    caps = _str_list(b.capabilities, _CAP_RE, "capabilities", 60)
    tools = _str_list(b.toolPermissions, _TOOL_RE, "toolPermissions", 100)
    perms = sorted(set(b.permissions))
    if not perms or any(p not in AGENT_PERMISSIONS for p in perms):
        raise _err(422, "VALIDATION_ERROR",
                   f"permissions must be a non-empty subset of {list(AGENT_PERMISSIONS)}")
    model = b.model.strip()
    if not _MODEL_RE.match(model):
        raise _err(422, "VALIDATION_ERROR", "model: invalid identifier")
    fb = b.fallbackModel.strip() if b.fallbackModel else None
    if fb is not None and (not _MODEL_RE.match(fb) or fb == model):
        raise _err(422, "VALIDATION_ERROR", "fallbackModel must be a different valid identifier")
    for k, v in b.toolFallbacks.items():
        if k not in tools or v not in tools or k == v:
            raise _err(422, "VALIDATION_ERROR",
                       "toolFallbacks must map one allowed tool to a different allowed tool")
    return {
        "description": b.description.strip(),
        "capabilities": caps, "tool_permissions": tools, "permissions": perms,
        "allowed_paths": _paths(b.allowedPaths),
        "network_allow": _str_list([h.lower() for h in b.networkAllow], _HOST_RE,
                                   "networkAllow", 40),
        "credential_refs": _str_list(b.credentialRefs, _CRED_RE, "credentialRefs", 40),
        "model": model, "fallback_model": fb,
        "tool_fallbacks": dict(sorted(b.toolFallbacks.items())),
        "max_depth": b.maxDepth, "max_children": b.maxChildren,
        "max_budget_microusd": _budget_micro(b.maxBudgetUsd),
    }


_JSON_COLS = ("capabilities", "tool_permissions", "permissions", "allowed_paths",
              "network_allow", "credential_refs", "tool_fallbacks")


def _out(r) -> dict[str, Any]:
    j = {c: json.loads(r[c]) for c in _JSON_COLS}
    return {
        "id": r["id"], "teamId": r["team_id"], "name": r["name"], "version": r["version"],
        "state": r["state"], "description": r["description"],
        "capabilities": j["capabilities"], "toolPermissions": j["tool_permissions"],
        "permissions": j["permissions"], "allowedPaths": j["allowed_paths"],
        "networkAllow": j["network_allow"], "credentialRefs": j["credential_refs"],
        "model": r["model"], "fallbackModel": r["fallback_model"],
        "toolFallbacks": j["tool_fallbacks"], "maxDepth": r["max_depth"],
        "maxChildren": r["max_children"],
        "maxBudgetUsd": str(Decimal(r["max_budget_microusd"]) / MICRO),
        "createdBy": r["created_by"], "createdAt": _iso(r["created_at"]),
        "approvedBy": r["approved_by"], "approvedAt": _iso(r["approved_at"]),
        "retiredBy": r["retired_by"], "retiredAt": _iso(r["retired_at"]),
        "retiredReason": r["retired_reason"],
    }


def _snapshot(vals: dict[str, Any]) -> dict[str, Any]:
    """Audit before/after view: values minus nothing secret (refs are names only)."""
    return {k: v for k, v in vals.items() if k != "description"}


# ------------------------------------------------------------- access


def _require_admin(p: Principal) -> None:
    if p.role != Role.ADMIN:
        raise _err(403, "FORBIDDEN", "admin only")


def _require_selector(p: Principal) -> None:
    if p.role not in (Role.ADMIN, Role.ENGINEER):
        raise _err(403, "FORBIDDEN", "admin or engineer role required")


async def _load(db, p: Principal, agent_id: str):
    row = (await db.execute(text("SELECT * FROM agent_definitions WHERE id=:i"),
                            {"i": agent_id})).mappings().first()
    if row is None or row["team_id"] != p.teamId:
        raise _err(404, "NOT_FOUND", "agent not found")
    return row


async def _audit(db, action: str, p: Principal, agent_id: str, detail: dict) -> None:
    await emit_audit(db, action, user_id=p.userId, team_id=p.teamId,
                     target_type="agent", target_id=agent_id, detail=detail)


Auth = Annotated[Principal, Depends(get_principal)]


# ------------------------------------------------------------- routes
# (static paths are registered BEFORE /{agent_id} routes)


@router.post("/select")
async def select_agents(body: SelectBody, request: Request, p: Auth) -> dict:
    """Approved agents (latest approved version per name) able to do a task."""
    _require_selector(p)
    caps = set(body.capabilities)
    tools = set(body.tools)
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(
            text("SELECT * FROM agent_definitions WHERE team_id=:t AND state='approved'"
                 " ORDER BY name_key, version"), {"t": p.teamId})).mappings().all()
        latest: dict[str, Any] = {}
        for r in rows:
            latest[r["name_key"]] = r  # ascending version, so the last row wins
        items = []
        for r in latest.values():
            o = _out(r)
            if body.name and body.name.strip().lower() != r["name_key"]:
                continue
            if caps <= set(o["capabilities"]) and tools <= set(o["toolPermissions"]):
                items.append(o)
        items.sort(key=lambda o: (len(set(o["capabilities"]) - caps), o["name"].lower()))
        await _audit(db, "agent.selected", p, items[0]["id"] if items else "-", {
            "capabilities": sorted(caps), "tools": sorted(tools),
            "matched": [o["id"] for o in items]})
    return {"items": items}


@router.post("", status_code=201)
async def register(body: DefBody, request: Request, p: Auth) -> dict:
    _require_admin(p)
    if not body.name or not _NAME_RE.match(body.name.strip()):
        raise _err(422, "VALIDATION_ERROR",
                   "name must be 2-80 chars: letters, digits, space, . _ - (start alphanumeric)")
    name = body.name.strip()
    vals = validate_def(body)
    async with request.app.state.session_factory() as db:
        exists = (await db.execute(
            text("SELECT 1 FROM agent_definitions WHERE team_id=:t AND name_key=:k"),
            {"t": p.teamId, "k": name.lower()})).first()
        if exists:
            raise _err(409, "NAME_TAKEN", "an agent with this name exists; PUT a new version")
        row = await _insert(db, p, name, 1, vals)
        await _audit(db, "agent.registered", p, row["id"], {
            "name": name, "version": 1, "after": _snapshot(vals)})
    return _out(row)


async def _insert(db, p: Principal, name: str, version: int, vals: dict[str, Any]):
    aid, now = str(uuid4()), now_utc()
    params = {k: (json.dumps(v, sort_keys=True) if k in _JSON_COLS else v)
              for k, v in vals.items()}
    try:
        await db.execute(
            text("INSERT INTO agent_definitions (id, team_id, name, name_key, version, state,"
                 " description, capabilities, tool_permissions, permissions, allowed_paths,"
                 " network_allow, credential_refs, model, fallback_model, tool_fallbacks,"
                 " max_depth, max_children, max_budget_microusd, created_by, created_at,"
                 " updated_at) VALUES (:id,:t,:n,:k,:v,'proposed',:description,:capabilities,"
                 ":tool_permissions,:permissions,:allowed_paths,:network_allow,:credential_refs,"
                 ":model,:fallback_model,:tool_fallbacks,:max_depth,:max_children,"
                 ":max_budget_microusd,:u,:now,:now)"),
            {**params, "id": aid, "t": p.teamId, "n": name, "k": name.lower(), "v": version,
             "u": p.userId, "now": now})
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise _err(409, "VERSION_CONFLICT", "agent saved concurrently; retry") from e
    return (await db.execute(text("SELECT * FROM agent_definitions WHERE id=:i"),
                             {"i": aid})).mappings().first()


@router.get("")
async def list_agents(request: Request, p: Auth, state: str | None = Query(default=None),
                      name: str | None = Query(default=None, max_length=80)) -> dict:
    if state is not None and state not in STATES:
        raise _err(422, "VALIDATION_ERROR", f"state must be one of {list(STATES)}")
    q, params = "SELECT * FROM agent_definitions WHERE team_id=:t", {"t": p.teamId}
    if state:
        q, params["s"] = q + " AND state=:s", state
    if name:
        q, params["k"] = q + " AND name_key=:k", name.strip().lower()
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(text(q + " ORDER BY name_key, version"),
                                 params)).mappings().all()
    return {"items": [_out(r) for r in rows]}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        return _out(await _load(db, p, agent_id))


@router.put("/{agent_id}")
async def new_version(agent_id: str, body: DefBody, request: Request, p: Auth) -> dict:
    """Save a NEW version (state proposed) of the agent family; old versions are untouched."""
    _require_admin(p)
    vals = validate_def(body)
    async with request.app.state.session_factory() as db:
        cur = await _load(db, p, agent_id)
        if body.name is not None and body.name.strip().lower() != cur["name_key"]:
            raise _err(422, "VALIDATION_ERROR", "name cannot change between versions")
        if cur["state"] == "retired":
            raise _err(409, "ILLEGAL_STATE", "agent version is retired; edit a live version")
        latest = (await db.execute(
            text("SELECT COALESCE(MAX(version),0) FROM agent_definitions"
                 " WHERE team_id=:t AND name_key=:k"),
            {"t": p.teamId, "k": cur["name_key"]})).scalar()
        row = await _insert(db, p, cur["name"], latest + 1, vals)
        before = {c: (json.loads(cur[c]) if c in _JSON_COLS else cur[c]) for c in vals}
        await _audit(db, "agent.updated", p, row["id"], {
            "name": cur["name"], "version": latest + 1, "fromVersion": cur["version"],
            "before": _snapshot(before), "after": _snapshot(vals)})
    return _out(row)


async def _transition(request: Request, p: Principal, agent_id: str, target: str,
                      reason: str | None) -> dict:
    _require_admin(p)
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, agent_id)
        before = row["state"]
        if before == target:  # idempotent: no write, no audit
            return {**_out(row), "changed": False}
        allowed = {"approved": ("proposed",), "retired": ("proposed", "approved")}[target]
        if before not in allowed:
            raise _err(409, "ILLEGAL_TRANSITION", f"cannot go from {before} to {target}")
        if (target == "approved" and row["created_by"] == p.userId
                and not _self_approval_allowed()):
            raise _err(403, "SELF_APPROVAL", "a second admin must approve this agent version")
        now = now_utc()
        if target == "approved":
            sql = ("UPDATE agent_definitions SET state='approved', approved_by=:u, approved_at=:t,"
                   " updated_at=:t WHERE id=:i AND state='proposed'")
        else:
            sql = ("UPDATE agent_definitions SET state='retired', retired_by=:u, retired_at=:t,"
                   " retired_reason=:r, updated_at=:t WHERE id=:i AND state IN"
                   " ('proposed','approved')")
        r = await db.execute(text(sql), {"u": p.userId, "t": now, "i": agent_id,
                                         "r": (reason or "")[:500] or None})
        await db.commit()
        if r.rowcount != 1:  # lost a race with a concurrent transition
            raise _err(409, "ILLEGAL_TRANSITION", "agent state changed concurrently")
        await _audit(db, "agent.approved" if target == "approved" else "agent.retired", p,
                     agent_id, {"name": row["name"], "version": row["version"],
                                "before": {"state": before}, "after": {"state": target},
                                "reason": reason})
        return {**_out(await _load(db, p, agent_id)), "changed": True}


@router.post("/{agent_id}/approve")
async def approve(agent_id: str, request: Request, p: Auth) -> dict:
    return await _transition(request, p, agent_id, "approved", None)


@router.post("/{agent_id}/retire")
async def retire(agent_id: str, request: Request, p: Auth, body: ReasonBody | None = None) -> dict:
    return await _transition(request, p, agent_id, "retired", body.reason if body else None)
