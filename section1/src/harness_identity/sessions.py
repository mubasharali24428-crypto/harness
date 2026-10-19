"""Collaboration sessions (master prompt part 40, Session 4 lane S). See docs/sessions.md.

A session is a team-owned container that holds many runs (``agent_runs``), members
(humans and agent identities) and a leader. It is NOT a run: archiving or deleting a
session never edits ``agent_runs``; it only stops NEW runs from starting (the
section4 ``session_gate``). Raw SQLAlchemy Core over migration 0041. Posture: deny by
default, team-scoped, a session the caller cannot see answers 404 (no existence
oracle), every consequential change is audited through the shared audit writer.
"""

import json
import os
import re
from datetime import timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from harness_contracts import Principal, Role, error_body
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .audit_emit import _emit as emit_audit
from .auth import get_principal
from .timeutil import now_utc

router = APIRouter(prefix="/api/v1/sessions")

STATES = ("active", "paused", "completed", "archived", "deleted")
MEMBER_ROLES = ("participant", "observer")  # settable; 'owner' is the creator, 'agent' is implicit
DEFAULT_RETENTION_DAYS = 30
MAX_MEMBERS = 100
# action -> (states it may start from, resulting state)
TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "pause": (frozenset({"active"}), "paused"),
    "complete": (frozenset({"active", "paused"}), "completed"),
    "archive": (frozenset({"active", "paused", "completed"}), "archived"),
    "restore": (frozenset({"paused", "completed", "archived"}), "active"),
    "delete": (frozenset({"archived"}), "deleted"),
}
# who may run each transition: "steer" = owner/admin/leader, "manage" = owner/admin
_TRANSITION_NEEDS = {"pause": "steer", "complete": "steer", "archive": "manage",
                     "restore": "manage", "delete": "manage"}
_AUDIT_ACTION = {"pause": "session.state_changed", "complete": "session.state_changed",
                 "archive": "session.state_changed", "restore": "session.state_changed",
                 "delete": "session.deleted"}
TERMINAL_RUN_PHASES = frozenset({"done"})
_AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,79}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def default_retention_days() -> int:
    return int(os.environ.get("HARNESS_SESSION_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))


def _err(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, msg))


def _iso(v: Any) -> str | None:
    return None if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))


# ------------------------------------------------------------- run lifecycle table (004/005)


def run_phase(event_types: list[str], claim_state: str | None = None) -> str:
    """Run phase replayed from the durable event log (mirror of section4's replay).

    created -> awaiting_plan_approval -> approved -> queued -> executing <-> paused,
    executing -> awaiting_done_signal -> reviewing -> done (terminal) | blocked | pr_opened.
    ``done`` is the ONLY terminal phase; stop and accepted-review both end there.
    """
    state, gate = "created", False
    for t in event_types:
        if state == "done":
            break  # sealed: legacy post-terminal rows never unseal a verdict
        if t == "plan_proposed":
            state = "awaiting_plan_approval"
        elif t == "plan_approved":
            state = "approved"
        elif t == "execution_started":
            state = "executing"
        elif t == "paused":
            state = "paused" if state == "executing" else state
        elif t == "resumed":
            state = "executing"
        elif t == "blocked_awaiting_clarification":
            state = "blocked"
        elif t == "engine_completed" and state == "executing":
            state, gate = "awaiting_done_signal", True
        elif t == "engine_failed" and state == "executing":
            state = "blocked"
        elif t == "done_signal_received":
            gate = gate or state == "awaiting_done_signal"
            state = "reviewing"
        elif t == "review_passed":
            state, gate = ("done" if gate else "executing"), False
        elif t == "review_failed":
            state = "blocked"
        elif t == "pr_opened":
            state = "pr_opened"
    if claim_state == "queued" and state in ("created", "approved", "executing"):
        return "queued"
    return state


# ------------------------------------------------------------- validation


def _clean_text(v: str, field: str, lo: int, hi: int) -> str:
    s = v.strip()
    if not (lo <= len(s) <= hi) or _CTRL_RE.search(s):
        raise _err(422, "VALIDATION_ERROR",
                   f"{field} must be {lo}-{hi} chars without control characters")
    return s


def validate_defaults(d: Any) -> dict[str, Any]:
    """Closed template schema: unknown keys and wrong types are 422."""
    if not isinstance(d, dict):
        raise _err(422, "VALIDATION_ERROR", "defaults must be an object")
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k == "description" and isinstance(v, str) and len(v) <= 1000 and not _CTRL_RE.search(v):
            out[k] = v
        elif (k == "retentionDays" and isinstance(v, int) and not isinstance(v, bool)
              and 1 <= v <= 365):
            out[k] = v
        elif (k == "agentIds" and isinstance(v, list) and len(v) <= 10
              and all(isinstance(a, str) and _AGENT_RE.match(a) for a in v)):
            out[k] = sorted(set(v))
        else:
            raise _err(422, "VALIDATION_ERROR", f"invalid or unknown template default: {k!r}")
    return out


# ------------------------------------------------------------- access


class Access:
    """What the caller may do in one visible session."""

    def __init__(self, p: Principal, row, role: str | None) -> None:
        self.role = role
        self.admin = p.role == Role.ADMIN
        self.leader = row["leader_user_id"] == p.userId
        self.manage = self.admin or role == "owner"
        self.steer = self.manage or self.leader
        self.writer = p.role in (Role.ADMIN, Role.ENGINEER)
        self.can_link = self.writer and (self.steer or role == "participant")


async def _my_role(db, sid: str, user_id: str) -> str | None:
    row = (await db.execute(
        text("SELECT role FROM session_members WHERE session_id=:s AND user_id=:u"
             " AND status='active'"), {"s": sid, "u": user_id})).first()
    return row[0] if row else None


async def _load(db, p: Principal, sid: str, *, need: str | None = None):
    """(row, Access) or 404. need: 'manage' | 'steer' | 'link' -> 403 when missing."""
    row = (await db.execute(text("SELECT * FROM collab_sessions WHERE id=:i"),
                            {"i": sid})).mappings().first()
    nf = _err(404, "NOT_FOUND", "session not found")
    if row is None or row["team_id"] != p.teamId:
        raise nf
    admin = p.role == Role.ADMIN
    role = await _my_role(db, sid, p.userId)
    if (row["state"] == "deleted" and not admin) or (role is None and not admin):
        raise nf
    acc = Access(p, row, role)
    ok = {None: True, "manage": acc.manage, "steer": acc.steer, "link": acc.can_link}[need]
    if not ok:
        raise _err(403, "FORBIDDEN", f"session {need} permission required")
    return row, acc


def _require_mutable(row) -> None:
    if row["state"] not in ("active", "paused"):
        raise _err(409, "ILLEGAL_STATE", f"session is {row['state']}; restore it first")


def _require_writer(p: Principal) -> None:
    if p.role not in (Role.ADMIN, Role.ENGINEER):
        raise _err(403, "FORBIDDEN", "admin or engineer role required")


def _out(row) -> dict[str, Any]:
    return {
        "id": row["id"], "teamId": row["team_id"], "workspaceId": row["workspace_id"],
        "title": row["title"], "description": row["description"], "state": row["state"],
        "stateReason": row["state_reason"], "ownerUserId": row["owner_user_id"],
        "leaderUserId": row["leader_user_id"], "version": row["version"],
        "retentionDays": row["retention_days"],
        "template": ({"name": row["template_name"], "version": row["template_version"]}
                     if row["template_name"] else None),
        "createdAt": _iso(row["created_at"]), "updatedAt": _iso(row["updated_at"]),
        "deletedAt": _iso(row["deleted_at"]), "purgeAfter": _iso(row["purge_after"]),
        "purgedAt": _iso(row["purged_at"]),
    }


async def _audit(db, action: str, p: Principal, sid: str, detail: dict, *,
                 target_type: str = "session") -> None:
    await emit_audit(db, action, user_id=p.userId, team_id=p.teamId,
                     target_type=target_type, target_id=sid, detail=detail)


Auth = Annotated[Principal, Depends(get_principal)]

# ------------------------------------------------------------- bodies


class CreateBody(BaseModel):
    title: str = Field(max_length=300)
    description: str | None = Field(default=None, max_length=1000)
    workspaceId: str | None = Field(default=None, max_length=36)
    retentionDays: int | None = Field(default=None, ge=1, le=365)
    templateName: str | None = Field(default=None, max_length=80)
    templateVersion: int | None = Field(default=None, ge=1)
    clientRequestId: str | None = Field(default=None, min_length=1, max_length=64)


class PatchBody(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=1000)
    retentionDays: int | None = Field(default=None, ge=1, le=365)
    leaderUserId: str | None = Field(default=None, max_length=36)  # explicit null clears
    expectedVersion: int | None = Field(default=None, ge=1)


class MemberBody(BaseModel):
    userId: str | None = Field(default=None, max_length=36)
    agentId: str | None = Field(default=None, max_length=80)
    displayName: str | None = Field(default=None, max_length=200)  # agents only
    role: str = Field(default="participant", max_length=16)


class RoleBody(BaseModel):
    role: str = Field(max_length=16)


class LinkRunBody(BaseModel):
    runId: str = Field(max_length=36)
    parentRunId: str | None = Field(default=None, max_length=36)


class TemplateBody(BaseModel):
    name: str = Field(max_length=200)
    defaults: dict[str, Any]


class ApplyBody(BaseModel):
    name: str = Field(max_length=80)
    version: int | None = Field(default=None, ge=1)


class ReasonBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class DeleteBody(BaseModel):
    reason: str = Field(default="", max_length=500)


# ------------------------------------------------------------- runs of a session


async def _runs(db, sid: str) -> list[dict[str, Any]]:
    links = (await db.execute(
        text("SELECT sr.run_id, sr.parent_run_id, sr.linked_by, sr.linked_at, r.project_id"
             " FROM session_runs sr LEFT JOIN agent_runs r ON r.id = sr.run_id"
             " WHERE sr.session_id=:s ORDER BY sr.linked_at, sr.run_id"),
        {"s": sid})).mappings().all()
    events: dict[str, list[str]] = {}
    for rid, typ in (await db.execute(
            text("SELECT e.run_id, e.type FROM agent_events e JOIN session_runs sr"
                 " ON sr.run_id = e.run_id WHERE sr.session_id=:s ORDER BY e.seq"),
            {"s": sid})).all():
        events.setdefault(rid, []).append(typ)
    claims = {rid: st for rid, st in (await db.execute(
        text("SELECT c.run_id, c.state FROM run_claims c JOIN session_runs sr"
             " ON sr.run_id = c.run_id WHERE sr.session_id=:s"), {"s": sid})).all()}
    out = []
    for r in links:
        phase = run_phase(events.get(r["run_id"], []), claims.get(r["run_id"]))
        out.append({"runId": r["run_id"], "parentRunId": r["parent_run_id"],
                    "projectId": r["project_id"], "phase": phase,
                    "terminal": phase in TERMINAL_RUN_PHASES, "linkedBy": r["linked_by"],
                    "linkedAt": _iso(r["linked_at"])})
    return out


@router.get("/{sid}/runs")
async def list_runs(sid: str, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        await _load(db, p, sid)
        return {"items": await _runs(db, sid)}


@router.post("/{sid}/runs", status_code=201)
async def link_run(sid: str, body: LinkRunBody, request: Request, p: Auth,
                   response: Response) -> dict:
    async with request.app.state.session_factory() as db:
        row, _ = await _load(db, p, sid, need="link")
        run = (await db.execute(
            text("SELECT r.id, pr.workspace_id FROM agent_runs r JOIN projects pr"
                 " ON pr.id = r.project_id WHERE r.id=:r AND pr.team_id=:t"),
            {"r": body.runId, "t": p.teamId})).mappings().first()
        if run is None:
            raise _err(404, "NOT_FOUND", "run not found")
        cur = (await db.execute(
            text("SELECT session_id, parent_run_id FROM session_runs WHERE run_id=:r"),
            {"r": body.runId})).mappings().first()
        if cur is not None:
            if cur["session_id"] == sid and cur["parent_run_id"] == body.parentRunId:
                response.status_code = 200  # idempotent retry
                return {"sessionId": sid, "runId": body.runId, "parentRunId": body.parentRunId,
                        "created": False}
            raise _err(409, "RUN_ALREADY_LINKED", "run is already linked to a session")
        if row["state"] != "active":
            raise _err(409, "SESSION_NOT_ACTIVE",
                       f"session is {row['state']}; it cannot take new runs")
        if row["workspace_id"] and run["workspace_id"] != row["workspace_id"]:
            raise _err(409, "WORKSPACE_MISMATCH", "run's project is not in the session's workspace")
        if body.parentRunId is not None:
            ok = (await db.execute(
                text("SELECT 1 FROM session_runs WHERE run_id=:r AND session_id=:s"),
                {"r": body.parentRunId, "s": sid})).first()
            if ok is None or body.parentRunId == body.runId:
                raise _err(422, "PARENT_NOT_IN_SESSION",
                           "parent run must be linked to this session")
        now = now_utc()
        # Row lock first: serializes with a concurrent complete/archive/delete (see _transition).
        r = await db.execute(
            text("UPDATE collab_sessions SET updated_at=:n WHERE id=:s AND state='active'"),
            {"n": now, "s": sid})
        if r.rowcount != 1:
            await db.rollback()
            raise _err(409, "SESSION_NOT_ACTIVE", "session stopped accepting runs")
        try:
            await db.execute(
                text("INSERT INTO session_runs (run_id, session_id, parent_run_id, linked_by,"
                     " linked_at) VALUES (:r,:s,:pr,:u,:n)"),
                {"r": body.runId, "s": sid, "pr": body.parentRunId, "u": p.userId, "n": now})
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            raise _err(409, "RUN_ALREADY_LINKED", "run is already linked to a session") from e
        await _audit(db, "session.run_linked", p, sid, {
            "runId": body.runId, "parentRunId": body.parentRunId})
    return {"sessionId": sid, "runId": body.runId, "parentRunId": body.parentRunId, "created": True}


@router.delete("/{sid}/runs/{run_id}", status_code=204)
async def unlink_run(sid: str, run_id: str, request: Request, p: Auth) -> None:
    async with request.app.state.session_factory() as db:
        await _load(db, p, sid, need="steer")  # allowed in any state: it lifts the session's gate
        kids = (await db.execute(
            text("SELECT COUNT(*) FROM session_runs WHERE session_id=:s AND parent_run_id=:r"),
            {"s": sid, "r": run_id})).scalar()
        if kids:
            raise _err(409, "RUN_HAS_CHILDREN", "unlink the child runs first")
        r = await db.execute(text("DELETE FROM session_runs WHERE run_id=:r AND session_id=:s"),
                             {"r": run_id, "s": sid})
        if r.rowcount != 1:
            raise _err(404, "NOT_FOUND", "run is not linked to this session")
        await db.commit()
        await _audit(db, "session.run_unlinked", p, sid, {"runId": run_id})


# ------------------------------------------------------------- templates
# (static paths are registered BEFORE /{sid} routes)


async def _template(db, team_id: str, name: str, version: int | None):
    q = "SELECT * FROM session_templates WHERE team_id=:t AND name=:n"
    prm: dict[str, Any] = {"t": team_id, "n": name}
    if version is not None:
        q += " AND version=:v"
        prm["v"] = version
    row = (await db.execute(text(q + " ORDER BY version DESC LIMIT 1"), prm)).mappings().first()
    if row is None:
        raise _err(404, "NOT_FOUND", "template not found")
    return row


@router.post("/templates", status_code=201)
async def save_template(body: TemplateBody, request: Request, p: Auth) -> dict:
    _require_writer(p)
    name = body.name.strip()
    if not _NAME_RE.match(name):
        raise _err(422, "VALIDATION_ERROR",
                   "name must be 2-80 chars: letters, digits, space, . _ -")
    defaults = validate_defaults(body.defaults)
    async with request.app.state.session_factory() as db:
        cur = (await db.execute(
            text("SELECT COALESCE(MAX(version),0) FROM session_templates"
                 " WHERE team_id=:t AND name=:n"), {"t": p.teamId, "n": name})).scalar()
        tid = str(uuid4())
        try:
            await db.execute(
                text("INSERT INTO session_templates (id, team_id, name, version, defaults,"
                     " created_by, created_at) VALUES (:i,:t,:n,:v,:d,:u,:ts)"),
                {"i": tid, "t": p.teamId, "n": name, "v": cur + 1,
                 "d": json.dumps(defaults, sort_keys=True), "u": p.userId, "ts": now_utc()})
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            raise _err(409, "VERSION_CONFLICT", "template saved concurrently; retry") from e
        await _audit(db, "session.template_saved", p, tid,
                     {"name": name, "version": cur + 1, "after": defaults},
                     target_type="session_template")
    return {"id": tid, "name": name, "version": cur + 1, "defaults": defaults}


@router.get("/templates")
async def list_templates(request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(
            text("SELECT * FROM session_templates WHERE team_id=:t ORDER BY name, version"),
            {"t": p.teamId})).mappings().all()
    return {"items": [{"id": r["id"], "name": r["name"], "version": r["version"],
                       "defaults": json.loads(r["defaults"]),
                       "createdAt": _iso(r["created_at"])} for r in rows]}


# ------------------------------------------------------------- members


def _member_out(r, leader: str | None) -> dict[str, Any]:
    return {
        "memberId": r["id"], "kind": r["kind"], "userId": r["user_id"], "agentId": r["agent_id"],
        "role": r["role"], "isLeader": bool(r["user_id"]) and r["user_id"] == leader,
        "displayName": r["display_name"], "status": r["status"],
        # history is kept when someone loses team access; the flag makes that visible
        "accessLost": r["kind"] == "user" and not r["has_access"],
        "joinedAt": _iso(r["joined_at"]), "leftAt": _iso(r["left_at"]),
    }


async def _roster(db, row, *, active_only: bool = False) -> list[dict[str, Any]]:
    rows = (await db.execute(
        text("SELECT m.*, (SELECT COUNT(*) FROM team_members t"
             " WHERE t.team_id=:team AND t.user_id = m.user_id) AS has_access"
             " FROM session_members m WHERE m.session_id=:s"
             + (" AND m.status='active'" if active_only else "")
             + " ORDER BY m.joined_at, m.id"),
        {"s": row["id"], "team": row["team_id"]})).mappings().all()
    return [_member_out(r, row["leader_user_id"]) for r in rows]


async def _upsert_member(db, row, *, user_id: str | None, agent_id: str | None, role: str,
                         display: str, by: str, now) -> tuple[str, dict | None]:
    """Insert or reactivate. Returns (memberId, before) where before is None for a new row."""
    col, val = ("user_id", user_id) if user_id else ("agent_id", agent_id)
    cur = (await db.execute(
        text(f"SELECT * FROM session_members WHERE session_id=:s AND {col}=:v"),
        {"s": row["id"], "v": val})).mappings().first()
    if cur is not None and cur["status"] == "active" and cur["role"] == role:
        return cur["id"], {"role": role, "status": "active", "unchanged": True}
    active = (await db.execute(
        text("SELECT COUNT(*) FROM session_members WHERE session_id=:s AND status='active'"),
        {"s": row["id"]})).scalar()
    if (cur is None or cur["status"] != "active") and active >= MAX_MEMBERS:
        raise _err(409, "MEMBER_LIMIT_REACHED", f"a session holds at most {MAX_MEMBERS} members")
    if cur is not None:
        await db.execute(
            text("UPDATE session_members SET role=:r, status='active', left_at=NULL,"
                 " display_name=:d WHERE id=:i"), {"r": role, "d": display, "i": cur["id"]})
        return cur["id"], {"role": cur["role"], "status": cur["status"]}
    mid = str(uuid4())
    await db.execute(
        text("INSERT INTO session_members (id, session_id, kind, user_id, agent_id, role,"
             " display_name, status, added_by, joined_at) VALUES (:i,:s,:k,:u,:a,:r,:d,"
             "'active',:by,:n)"),
        {"i": mid, "s": row["id"], "k": "user" if user_id else "agent", "u": user_id,
         "a": agent_id, "r": role, "d": display, "by": by, "n": now})
    return mid, None


async def _team_display(db, team_id: str, user_id: str) -> str | None:
    r = (await db.execute(
        text("SELECT display_name FROM team_members WHERE team_id=:t AND user_id=:u"),
        {"t": team_id, "u": user_id})).first()
    return r[0] if r else None


@router.get("/{sid}/members")
async def list_members(sid: str, request: Request, p: Auth, status: str | None = None) -> dict:
    if status not in (None, "active", "left", "removed"):
        raise _err(422, "VALIDATION_ERROR", "status must be active, left or removed")
    async with request.app.state.session_factory() as db:
        row, _ = await _load(db, p, sid)
        items = await _roster(db, row)
    return {"items": [m for m in items if status is None or m["status"] == status]}


@router.post("/{sid}/members", status_code=201)
async def add_member(sid: str, body: MemberBody, request: Request, p: Auth,
                     response: Response) -> dict:
    if (body.userId is None) == (body.agentId is None):
        raise _err(422, "VALIDATION_ERROR", "exactly one of userId or agentId is required")
    if body.userId is not None and body.role not in MEMBER_ROLES:
        raise _err(422, "VALIDATION_ERROR", f"role must be one of {list(MEMBER_ROLES)}")
    if body.agentId is not None and not _AGENT_RE.match(body.agentId):
        raise _err(422, "VALIDATION_ERROR", "agentId: letters, digits and . _ : - (max 80)")
    async with request.app.state.session_factory() as db:
        row, _ = await _load(db, p, sid, need="manage")
        _require_mutable(row)
        if body.userId is not None:
            display = await _team_display(db, p.teamId, body.userId)
            if display is None:
                raise _err(422, "NOT_A_TEAM_MEMBER", "only members of the owning team can be added")
            role = body.role
        else:
            display = _clean_text(body.displayName or body.agentId or "", "displayName", 1, 200)
            role = "agent"
        for attempt in (0, 1):
            try:
                mid, before = await _upsert_member(
                    db, row, user_id=body.userId, agent_id=body.agentId, role=role,
                    display=display, by=p.userId, now=now_utc())
                await db.commit()
                break
            except IntegrityError as e:  # concurrent add of the same identity: retry as update
                await db.rollback()
                if attempt:
                    raise _err(409, "VERSION_CONFLICT", "member changed concurrently") from e
        if before is not None and before.get("unchanged"):
            response.status_code = 200
            return {"memberId": mid, "role": role, "changed": False}
        await _audit(db, "session.member_joined", p, sid, {
            "userId": body.userId, "agentId": body.agentId, "role": role,
            "before": before, "after": {"role": role, "status": "active"}})
    return {"memberId": mid, "role": role, "changed": True}


@router.put("/{sid}/members/{ref}")
async def put_member(sid: str, ref: str, body: RoleBody, request: Request, p: Auth) -> dict:
    if body.role not in MEMBER_ROLES:
        raise _err(422, "VALIDATION_ERROR", f"role must be one of {list(MEMBER_ROLES)}")
    async with request.app.state.session_factory() as db:
        row, _ = await _load(db, p, sid, need="manage")
        _require_mutable(row)
        m = (await db.execute(
            text("SELECT * FROM session_members WHERE session_id=:s AND status='active'"
                 " AND (user_id=:r OR agent_id=:r)"), {"s": sid, "r": ref})).mappings().first()
        if m is None:
            raise _err(404, "NOT_FOUND", "member not found")
        if m["kind"] == "agent":
            raise _err(409, "AGENT_ROLE_FIXED", "agent members keep the agent role")
        if m["role"] == "owner":
            raise _err(409, "OWNER_ROLE_FIXED", "the owner's role cannot be changed")
        if m["role"] == body.role:
            return {"userId": ref, "role": body.role, "changed": False}
        if body.role == "observer" and row["leader_user_id"] == ref:
            raise _err(409, "LEADER_MUST_PARTICIPATE", "change the leader before demoting")
        await db.execute(text("UPDATE session_members SET role=:r WHERE id=:i"),
                         {"r": body.role, "i": m["id"]})
        await db.commit()
        await _audit(db, "session.updated", p, sid, {
            "change": "member_role", "userId": ref, "before": {"role": m["role"]},
            "after": {"role": body.role}})
    return {"userId": ref, "role": body.role, "changed": True}


@router.delete("/{sid}/members/{ref}", status_code=204)
async def remove_member(sid: str, ref: str, request: Request, p: Auth) -> None:
    """Self-leave (status 'left') or manager removal (status 'removed'). Rows are kept."""
    async with request.app.state.session_factory() as db:
        row, acc = await _load(db, p, sid)
        selfie = ref == p.userId
        if not selfie and not acc.manage:
            raise _err(403, "FORBIDDEN", "session manage permission required")
        m = (await db.execute(
            text("SELECT * FROM session_members WHERE session_id=:s"
                 " AND (user_id=:r OR agent_id=:r)"),
            {"s": sid, "r": ref})).mappings().first()
        if m is None:
            raise _err(404, "NOT_FOUND", "member not found")
        if m["status"] != "active":
            return  # idempotent
        if m["role"] == "owner":
            raise _err(409, "OWNER_CANNOT_LEAVE", "the session owner cannot leave or be removed")
        new = "left" if selfie else "removed"
        now = now_utc()
        await db.execute(text("UPDATE session_members SET status=:st, left_at=:n WHERE id=:i"),
                         {"st": new, "n": now, "i": m["id"]})
        cleared = row["leader_user_id"] is not None and row["leader_user_id"] == m["user_id"]
        if cleared:
            await db.execute(
                text("UPDATE collab_sessions SET leader_user_id=NULL, version=version+1,"
                     " updated_at=:n WHERE id=:s"), {"n": now, "s": sid})
        await db.commit()
        await _audit(db, "session.member_left" if selfie else "session.member_removed", p, sid, {
            "userId": m["user_id"], "agentId": m["agent_id"], "leaderCleared": cleared,
            "before": {"role": m["role"], "status": "active"}, "after": {"status": new}})


# ------------------------------------------------------------- create / list / read / patch


async def _visible_workspace(db, p: Principal, wid: str) -> None:
    ws = (await db.execute(text("SELECT team_id, state, visibility FROM workspaces WHERE id=:w"),
                           {"w": wid})).mappings().first()
    nf = _err(404, "NOT_FOUND", "workspace not found")
    if ws is None or ws["team_id"] != p.teamId or ws["state"] == "deleted":
        raise nf
    if ws["visibility"] == "private" and p.role != Role.ADMIN:
        member = (await db.execute(
            text("SELECT 1 FROM workspace_members WHERE workspace_id=:w AND user_id=:u"
                 " AND removed_at IS NULL"), {"w": wid, "u": p.userId})).first()
        if member is None:
            raise nf
    if ws["state"] != "active":
        raise _err(409, "WORKSPACE_NOT_ACTIVE", f"workspace is {ws['state']}")


@router.post("", status_code=201)
async def create_session(body: CreateBody, request: Request, p: Auth, response: Response) -> dict:
    _require_writer(p)
    title = _clean_text(body.title, "title", 2, 120)
    async with request.app.state.session_factory() as db:
        if body.clientRequestId:
            prior = (await db.execute(
                text("SELECT * FROM collab_sessions WHERE team_id=:t AND owner_user_id=:u"
                     " AND client_request_id=:c"),
                {"t": p.teamId, "u": p.userId, "c": body.clientRequestId})).mappings().first()
            if prior is not None:
                response.status_code = 200
                return _out(prior)
        defaults: dict[str, Any] = {}
        tpl = None
        if body.templateName:
            tpl = await _template(db, p.teamId, body.templateName, body.templateVersion)
            defaults = json.loads(tpl["defaults"])
        if body.workspaceId:
            await _visible_workspace(db, p, body.workspaceId)
        sid, now = str(uuid4()), now_utc()
        description = (body.description if body.description is not None
                       else defaults.get("description", ""))
        retention = body.retentionDays or defaults.get("retentionDays") or default_retention_days()
        owner_name = await _team_display(db, p.teamId, p.userId) or "unknown"
        try:
            await db.execute(
                text("INSERT INTO collab_sessions (id, team_id, workspace_id, title, description,"
                     " owner_user_id, state, version, retention_days, template_name,"
                     " template_version, client_request_id, created_at, updated_at)"
                     " VALUES (:id,:t,:w,:ti,:d,:u,'active',1,:ret,:tn,:tv,:c,:n,:n)"),
                {"id": sid, "t": p.teamId, "w": body.workspaceId, "ti": title, "d": description,
                 "u": p.userId, "ret": retention, "tn": tpl["name"] if tpl else None,
                 "tv": tpl["version"] if tpl else None, "c": body.clientRequestId, "n": now})
            await db.execute(
                text("INSERT INTO session_members (id, session_id, kind, user_id, role,"
                     " display_name, status, added_by, joined_at)"
                     " VALUES (:i,:s,'user',:u,'owner',:d,'active',:u,:n)"),
                {"i": str(uuid4()), "s": sid, "u": p.userId, "d": owner_name, "n": now})
            for agent in defaults.get("agentIds", []):
                await db.execute(
                    text("INSERT INTO session_members (id, session_id, kind, agent_id, role,"
                         " display_name, status, added_by, joined_at)"
                         " VALUES (:i,:s,'agent',:a,'agent',:a,'active',:u,:n)"),
                    {"i": str(uuid4()), "s": sid, "a": agent, "u": p.userId, "n": now})
            await db.commit()
        except IntegrityError as e:  # concurrent retry with the same clientRequestId
            await db.rollback()
            prior = (await db.execute(
                text("SELECT * FROM collab_sessions WHERE team_id=:t AND owner_user_id=:u"
                     " AND client_request_id=:c"),
                {"t": p.teamId, "u": p.userId, "c": body.clientRequestId})).mappings().first()
            if prior is None:
                raise _err(409, "VERSION_CONFLICT", "session create conflicted; retry") from e
            response.status_code = 200
            return _out(prior)
        await _audit(db, "session.created", p, sid, {
            "title": title, "workspaceId": body.workspaceId, "retentionDays": retention,
            "after": {"state": "active"}})
        if tpl:
            await _audit(db, "session.template_applied", p, sid, {
                "template": tpl["name"], "templateVersion": tpl["version"], "at": "create"})
        return _out((await _load(db, p, sid))[0])


def _esc(s: str) -> str:
    return s.replace("!", "!!").replace("%", "!%").replace("_", "!_")


@router.get("")
async def list_sessions(request: Request, p: Auth, q: str | None = None, state: str | None = None,
                        workspaceId: str | None = None, limit: int = 25,
                        cursor: str | None = None) -> dict:
    if state is not None and state not in STATES:
        raise _err(422, "VALIDATION_ERROR", f"state must be one of {list(STATES)}")
    limit = max(1, min(int(limit), 100))
    where = ["s.team_id = :team"]
    prm: dict[str, Any] = {"team": p.teamId, "uid": p.userId, "lim": limit + 1}
    if p.role != Role.ADMIN:  # members only; deleted sessions are invisible to non-admins
        where.append("s.state <> 'deleted'")
        where.append("EXISTS (SELECT 1 FROM session_members m WHERE m.session_id = s.id"
                     " AND m.user_id = :uid AND m.status = 'active')")
    if state:
        where.append("s.state = :state")
        prm["state"] = state
    else:
        where.append("s.state <> 'deleted'")
    if workspaceId:
        where.append("s.workspace_id = :ws")
        prm["ws"] = workspaceId
    if q:
        where.append("LOWER(s.title) LIKE :q ESCAPE '!'")
        prm["q"] = f"%{_esc(q.strip().lower())}%"
    if cursor:
        where.append("(s.created_at, s.id) < (SELECT c.created_at, c.id FROM collab_sessions c"
                     " WHERE c.id = :cur AND c.team_id = :team)")
        prm["cur"] = cursor
    sql = ("SELECT s.* FROM collab_sessions s WHERE " + " AND ".join(where)
           + " ORDER BY s.created_at DESC, s.id DESC LIMIT :lim")
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(text(sql), prm)).mappings().all()
    items = [_out(r) for r in rows[:limit]]
    return {"items": items, "nextCursor": items[-1]["id"] if len(rows) > limit else None}


@router.get("/{sid}")
async def get_session(sid: str, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        row, acc = await _load(db, p, sid)
        return {**_out(row), "yourRole": acc.role, "isLeader": acc.leader}


@router.patch("/{sid}")
async def update_session(sid: str, body: PatchBody, request: Request, p: Auth) -> dict:
    sets: dict[str, Any] = {}
    if body.title is not None:
        sets["title"] = _clean_text(body.title, "title", 2, 120)
    if body.description is not None:
        sets["description"] = body.description
    if body.retentionDays is not None:
        sets["retention_days"] = body.retentionDays
    leader_given = "leaderUserId" in body.model_fields_set
    async with request.app.state.session_factory() as db:
        row, _ = await _load(db, p, sid, need="manage")
        _require_mutable(row)
        if body.expectedVersion is not None and body.expectedVersion != row["version"]:
            raise _err(409, "VERSION_CONFLICT", f"session is at version {row['version']}")
        if leader_given:
            new_leader = body.leaderUserId
            if new_leader is not None:
                role = await _my_role(db, sid, new_leader)
                if role not in ("owner", "participant"):
                    raise _err(422, "LEADER_NOT_ELIGIBLE",
                               "leader must be an active owner or participant")
            sets["leader_user_id"] = new_leader
        before = {k: row[k] for k in sets}
        changed = {k: v for k, v in sets.items() if before[k] != v}
        if not changed:
            return _out(row)
        assign = ", ".join(f"{c} = :{c}" for c in changed)
        # column names come from the fixed set above, never from input
        r = await db.execute(
            text(f"UPDATE collab_sessions SET {assign}, version = version + 1, updated_at = :now"
                 " WHERE id = :id AND version = :v"),
            {**changed, "now": now_utc(), "id": sid, "v": row["version"]})
        if r.rowcount != 1:
            await db.rollback()
            raise _err(409, "VERSION_CONFLICT", "session changed concurrently")
        await db.commit()
        await _audit(db, "session.updated", p, sid, {
            "before": {k: before[k] for k in changed}, "after": changed})
        return _out((await _load(db, p, sid))[0])


# ------------------------------------------------------------- templates (apply)


@router.post("/{sid}/apply-template")
async def apply_template(sid: str, body: ApplyBody, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        row, _ = await _load(db, p, sid, need="manage")
        _require_mutable(row)
        tpl = await _template(db, p.teamId, body.name, body.version)
        d = json.loads(tpl["defaults"])
        now = now_utc()
        await db.execute(
            text("UPDATE collab_sessions SET description=:d, retention_days=:r, template_name=:tn,"
                 " template_version=:tv, version=version+1, updated_at=:n WHERE id=:i"),
            {"d": d.get("description", row["description"]),
             "r": d.get("retentionDays", row["retention_days"]), "tn": tpl["name"],
             "tv": tpl["version"], "n": now, "i": sid})
        for agent in d.get("agentIds", []):
            await _upsert_member(db, row, user_id=None, agent_id=agent, role="agent",
                                 display=agent, by=p.userId, now=now)
        await db.commit()
        await _audit(db, "session.template_applied", p, sid, {
            "template": tpl["name"], "templateVersion": tpl["version"], "at": "apply",
            "before": {"description": row["description"], "retentionDays": row["retention_days"]},
            "after": {"description": d.get("description", row["description"]),
                      "retentionDays": d.get("retentionDays", row["retention_days"])}})
        return _out((await _load(db, p, sid))[0])


# ------------------------------------------------------------- resume


@router.get("/{sid}/resume")
async def resume_session(sid: str, request: Request, p: Auth, limit: int = 50) -> dict:
    """History + runs + roster + the caller's CURRENT effective permissions and policy.

    Read-only apart from one ``session.resumed`` audit row. To reopen a paused, completed
    or archived session use ``POST /restore``.
    """
    async with request.app.state.session_factory() as db:
        row, acc = await _load(db, p, sid)
        members = await _roster(db, row)
        runs = await _runs(db, sid)
        hist = (await db.execute(
            text("SELECT a.action, a.ts, a.detail, t.display_name FROM audit_log a"
                 " LEFT JOIN team_members t ON t.id = a.actor_member_id"
                 " WHERE a.target_type='session' AND a.target_id=:s ORDER BY a.seq DESC LIMIT :l"),
            {"s": sid, "l": max(1, min(int(limit), 200))})).mappings().all()
        ws = None
        if row["workspace_id"]:
            w = (await db.execute(text("SELECT id, state FROM workspaces WHERE id=:w"),
                                  {"w": row["workspace_id"]})).mappings().first()
            ws = {"id": w["id"], "state": w["state"]} if w else None
        active = row["state"] == "active"
        ws_ok = ws is None or ws["state"] == "active"
        await _audit(db, "session.resumed", p, sid, {"state": row["state"], "runs": len(runs)})
    return {
        "session": {**_out(row), "yourRole": acc.role, "isLeader": acc.leader},
        "members": members,
        "runs": runs,
        "history": [{"action": h["action"], "at": _iso(h["ts"]), "actor": h["display_name"],
                     "detail": json.loads(h["detail"]) if isinstance(h["detail"], str)
                     else (h["detail"] or {})} for h in hist],
        "you": {"userId": p.userId, "memberRole": acc.role, "teamRole": p.role.value,
                "isLeader": acc.leader, "permissions": {
                    "read": True,
                    "linkRuns": acc.can_link and active,
                    "manageMembers": acc.manage and row["state"] in ("active", "paused"),
                    "changeState": acc.steer, "restore": acc.manage,
                    "delete": acc.manage and row["state"] == "archived",
                    "purge": acc.admin and row["state"] == "deleted"}},
        "policy": {"state": row["state"], "launchAllowed": active and ws_ok,
                   "launchBlockedBy": (None if active and ws_ok else
                                       "SESSION_NOT_ACTIVE" if not active
                                       else "WORKSPACE_NOT_ACTIVE"),
                   "retentionDays": row["retention_days"], "purgeAfter": _iso(row["purge_after"]),
                   "workspace": ws},
    }


# ------------------------------------------------------------- lifecycle


async def _transition(request: Request, p: Principal, sid: str, action: str,
                      reason: str | None) -> dict:
    allowed_from, target = TRANSITIONS[action]
    async with request.app.state.session_factory() as db:
        row, _ = await _load(db, p, sid, need=_TRANSITION_NEEDS[action])
        if row["state"] == target:  # idempotent: no change, no audit
            return {**_out(row), "changed": False}
        if row["state"] not in allowed_from:
            if action == "delete" and row["state"] != "deleted":
                raise _err(409, "SESSION_NOT_ARCHIVED", "archive the session before deleting")
            raise _err(409, "ILLEGAL_TRANSITION", f"cannot {action} a {row['state']} session")
        now = now_utc()
        prm: dict[str, Any] = {"new": target, "reason": reason, "now": now, "id": sid}
        extra = ""
        if action == "delete":
            extra = ", deleted_at = :now, purge_after = :purge"
            prm["purge"] = now + timedelta(days=row["retention_days"])
        ok = ", ".join(f":ok{i}" for i in range(len(allowed_from)))
        for i, s in enumerate(sorted(allowed_from)):
            prm[f"ok{i}"] = s
        r = await db.execute(  # compare-and-set: loses cleanly against a concurrent transition
            text(f"UPDATE collab_sessions SET state=:new, state_reason=:reason, updated_at=:now,"
                 f" version=version+1{extra} WHERE id=:id AND state IN ({ok})"), prm)
        if r.rowcount == 1 and action in ("complete", "delete"):
            # Checked AFTER the row is locked and flipped: a run linked concurrently either
            # committed before our UPDATE (seen here) or waits and is refused by its own
            # state='active' guard. Terminal means phase 'done'.
            live = [x["runId"] for x in await _runs(db, sid) if not x["terminal"]]
            if live:
                await db.rollback()
                raise _err(409, "SESSION_HAS_ACTIVE_RUNS",
                           f"{len(live)} linked run(s) are not finished: {live[:5]}")
        await db.commit()
        fresh = (await db.execute(text("SELECT * FROM collab_sessions WHERE id=:i"),
                                  {"i": sid})).mappings().first()
        if r.rowcount != 1:
            if fresh["state"] == target:
                return {**_out(fresh), "changed": False}
            raise _err(409, "ILLEGAL_TRANSITION", f"session is now {fresh['state']}")
        await _audit(db, _AUDIT_ACTION[action], p, sid, {
            "action": action, "before": {"state": row["state"]}, "after": {"state": target},
            "reason": reason, **({"purgeAfter": _iso(prm["purge"])} if action == "delete" else {})})
        return {**_out(fresh), "changed": True}


@router.post("/{sid}/pause")
async def pause_session(sid: str, request: Request, p: Auth,
                        body: ReasonBody | None = None) -> dict:
    return await _transition(request, p, sid, "pause", body.reason if body else None)


@router.post("/{sid}/complete")
async def complete_session(sid: str, request: Request, p: Auth,
                           body: ReasonBody | None = None) -> dict:
    return await _transition(request, p, sid, "complete", body.reason if body else None)


@router.post("/{sid}/archive")
async def archive_session(sid: str, request: Request, p: Auth,
                          body: ReasonBody | None = None) -> dict:
    return await _transition(request, p, sid, "archive", body.reason if body else None)


@router.post("/{sid}/restore")
async def restore_session(sid: str, request: Request, p: Auth,
                          body: ReasonBody | None = None) -> dict:
    return await _transition(request, p, sid, "restore", body.reason if body else None)


@router.post("/{sid}/delete")
async def delete_session(sid: str, body: DeleteBody, request: Request, p: Auth) -> dict:
    reason = body.reason.strip()
    if len(reason) < 3:
        raise _err(422, "VALIDATION_ERROR", "a reason (3+ chars) is required to delete")
    return await _transition(request, p, sid, "delete", reason)


@router.post("/{sid}/purge")
async def purge_session(sid: str, request: Request, p: Auth) -> dict:
    """Admin only, after retention. Erases member rows and descriptive text, keeps a tombstone.

    Runs are never touched; the session_runs links stay so the run gate keeps refusing starts.
    """
    if p.role != Role.ADMIN:
        raise _err(403, "FORBIDDEN", "admin only")
    async with request.app.state.session_factory() as db:
        row, _ = await _load(db, p, sid)
        if row["state"] != "deleted":
            raise _err(409, "SESSION_NOT_DELETED", "only a deleted session can be purged")
        if row["purged_at"] is not None:
            return {**_out(row), "changed": False}
        now = now_utc()
        elapsed = (await db.execute(
            text("SELECT COUNT(*) FROM collab_sessions WHERE id=:i AND purge_after <= :n"),
            {"i": sid, "n": now})).scalar()
        if not elapsed:
            raise _err(409, "RETENTION_NOT_ELAPSED",
                       f"retention runs until {_iso(row['purge_after'])}")
        removed = (await db.execute(text("DELETE FROM session_members WHERE session_id=:s"),
                                    {"s": sid})).rowcount
        await db.execute(
            text("UPDATE collab_sessions SET title='[purged]', description='', leader_user_id=NULL,"
                 " state_reason=NULL, purged_at=:n, updated_at=:n, version=version+1 WHERE id=:i"),
            {"n": now, "i": sid})
        retained = (await db.execute(text("SELECT COUNT(*) FROM session_runs WHERE session_id=:s"),
                                     {"s": sid})).scalar()
        await db.commit()
        await _audit(db, "session.purged", p, sid, {
            "membersErased": removed, "runsRetained": retained,
            "before": {"state": "deleted"}, "after": {"purged": True}})
        fresh = (await db.execute(text("SELECT * FROM collab_sessions WHERE id=:i"),
                                  {"i": sid})).mappings().first()
        return {**_out(fresh), "changed": True}
