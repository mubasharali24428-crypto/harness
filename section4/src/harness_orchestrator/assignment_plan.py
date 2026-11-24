"""Phase 7 slice (C-01..C-04): structured brief -> versioned assignment proposal -> admin review.

Rules (FUTURE PLAN 2.0 §12, decisions D-03/D-05/D-07 defaults, CD-02):

* Proposal only. Nothing here starts, dispatches or delegates work; an approved version is a
  record an admin signed, not an instruction to an engine.
* Authorized context only: candidate assignees are the caller's own team members. A brief may
  name a preferred assignee; anyone else, or nobody, is proposed as UNASSIGNED with
  ``uncertainty="unknown"`` and the required role listed as missing expertise. No availability,
  skill or cost is inferred (cost is ``"unknown"`` until a real estimator exists).
* Versions are immutable rows (migration 0078). Every admin review inserts version N+1;
  reviewing anything but the latest is 409 STALE_VERSION (conflict -> renewed review).
* Members (any non-admin role) see only their own approved tasks with dependency KEYS, never
  another member's task body (C-04/C-05). Foreign or unknown project -> identical 404.
* Audit rows carry ids, versions and states only — never brief text.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException
from harness_contracts.roles import Principal, Role
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api as _api
from harness_orchestrator.plans import _now

router = APIRouter(prefix="/api/v1/orchestrator", tags=["assignment-plans"])

Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class BriefTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Key
    title: Line
    kind: Literal["human", "agent"] = "human"
    requiredRole: Line | None = None
    preferredAssignee: str | None = Field(default=None, max_length=64)
    dependsOn: list[Key] = Field(default_factory=list, max_length=20)


class Brief(BaseModel):
    """C-01: goals, constraints, roles, acceptance criteria, tasks."""

    model_config = ConfigDict(extra="forbid")
    goals: list[Line] = Field(min_length=1, max_length=20)
    constraints: list[Line] = Field(default_factory=list, max_length=20)
    acceptanceCriteria: list[Line] = Field(default_factory=list, max_length=20)
    tasks: list[BriefTask] = Field(min_length=1, max_length=50)


class Reassign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: Key
    assignee: str | None = Field(default=None, max_length=64)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject", "edit"]
    note: str | None = Field(default=None, max_length=500)
    reassign: list[Reassign] = Field(default_factory=list, max_length=50)


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"error": {"code": code, "message": message}})


def _factory() -> Any:
    factory = getattr(_api._service, "session_factory", None)
    if factory is None:
        raise _err(503, "PLANNING_UNAVAILABLE", "database not wired")
    return factory


async def _own_project(db: Any, p: Principal, project_id: str) -> None:
    row = (await db.execute(text("SELECT team_id FROM projects WHERE id = :p"),
                            {"p": project_id})).first()
    if row is None or row.team_id != p.teamId:  # no-oracle: foreign == unknown
        raise _err(404, "NOT_FOUND", "project not found")


def _require_admin(p: Principal) -> None:
    if p.role != Role.ADMIN:  # role gate before ownership: no existence oracle for low roles
        raise _err(403, "ROLE_DENIED", "only project admins may plan or review assignments")


async def _members(db: Any, team_id: str) -> set[str]:
    rows = await db.execute(
        text("SELECT user_id FROM team_members WHERE team_id = :t AND user_id IS NOT NULL"),
        {"t": team_id})
    return {r.user_id for r in rows}


def propose(brief: Brief, members: set[str]) -> list[dict[str, Any]]:
    """C-02, deterministic: name only what the authorized context supports."""
    keys = [t.key for t in brief.tasks]
    if len(set(keys)) != len(keys):
        raise _err(422, "BAD_BRIEF", "task keys must be unique")
    out = []
    for t in brief.tasks:
        if unknown := set(t.dependsOn) - set(keys):
            raise _err(422, "BAD_BRIEF", f"task {t.key} depends on unknown keys {sorted(unknown)}")
        known = t.preferredAssignee in members
        out.append({
            "task": t.key, "kind": t.kind, "dependsOn": t.dependsOn,
            "assignee": t.preferredAssignee if known else None,
            "rationale": "named in brief; current team member" if known
            else "no authorized team member named; availability unknown",
            "uncertainty": "low" if known else "unknown",
            "missingExpertise": [] if known else [t.requiredRole or "unspecified"],
            "costEstimate": "unknown",
        })
    return out


async def _latest(db: Any, project_id: str) -> Any:
    return (await db.execute(text(
        "SELECT * FROM assignment_plan_versions WHERE project_id = :p"
        " ORDER BY version DESC LIMIT 1"), {"p": project_id})).first()


async def _insert(db: Any, p: Principal, project_id: str, version: int, parent: int | None,
                  state: str, brief: str, assignments: list, note: str | None) -> dict:
    row = {"id": str(uuid.uuid4()), "t": p.teamId, "p": project_id, "v": version,
           "pv": parent, "s": state, "b": brief, "a": json.dumps(assignments),
           "u": p.userId, "n": note, "ts": _now()}
    try:
        await db.execute(text(
            "INSERT INTO assignment_plan_versions (id, team_id, project_id, version,"
            " parent_version, state, brief, assignments, created_by, note, created_at)"
            " VALUES (:id, :t, :p, :v, :pv, :s, :b, :a, :u, :n, :ts)"), row)
    except IntegrityError as e:
        await db.rollback()
        raise _err(409, "STALE_VERSION", "plan changed; re-review the latest version") from e
    member = (await db.execute(text(
        "SELECT id FROM team_members WHERE team_id = :t AND user_id = :u LIMIT 1"),
        {"t": p.teamId, "u": p.userId})).first()
    await db.execute(text(  # ids/versions/states only - never brief text
        "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, target_id,"
        " detail, ts) VALUES (:m, 'human', :act, 'project', :p, :d, :ts)"),
        {"m": member.id if member else None, "act": f"assignment_plan.{state}", "p": project_id,
         "d": json.dumps({"userId": p.userId, "teamId": p.teamId, "version": version,
                          "parent": parent}), "ts": row["ts"]})
    await db.commit()
    return {"version": version, "parentVersion": parent, "state": state,
            "brief": json.loads(brief), "assignments": assignments,
            "createdBy": p.userId, "note": note}


@router.post("/projects/{projectId}/assignment-plans", status_code=201)
async def create_proposal(projectId: str, brief: Brief, p: _api.RequirePrincipal) -> dict:
    _require_admin(p)
    async with _factory()() as db:
        await _own_project(db, p, projectId)
        latest = await _latest(db, projectId)
        version = 1 if latest is None else latest.version + 1
        return await _insert(db, p, projectId, version, latest.version if latest else None,
                             "proposed", brief.model_dump_json(),
                             propose(brief, await _members(db, p.teamId)), None)


@router.post("/projects/{projectId}/assignment-plans/{version}/review", status_code=201)
async def review(projectId: str, version: int, req: Review, p: _api.RequirePrincipal) -> dict:
    _require_admin(p)
    async with _factory()() as db:
        await _own_project(db, p, projectId)
        latest = await _latest(db, projectId)
        if latest is None or latest.version != version:
            raise _err(409, "STALE_VERSION", "only the latest version can be reviewed")
        if req.decision != "edit" and latest.state != "proposed":
            raise _err(409, "PLAN_DECIDED", f"version {version} is already {latest.state}")
        assignments = json.loads(latest.assignments)
        if req.decision == "edit":
            members, by_task = await _members(db, p.teamId), {a["task"]: a for a in assignments}
            for r in req.reassign:
                if r.task not in by_task or (r.assignee is not None and r.assignee not in members):
                    raise _err(422, "BAD_REASSIGN", f"unknown task or non-member for {r.task}")
                by_task[r.task].update(
                    assignee=r.assignee, rationale="set by admin review",
                    uncertainty="low" if r.assignee else "unknown")
        state = {"approve": "approved", "reject": "rejected", "edit": "proposed"}[req.decision]
        return await _insert(db, p, projectId, version + 1, version, state,
                             latest.brief, assignments, req.note)


@router.get("/projects/{projectId}/assignment-plans")
async def list_versions(projectId: str, p: _api.RequirePrincipal) -> dict:
    _require_admin(p)
    async with _factory()() as db:
        await _own_project(db, p, projectId)
        rows = await db.execute(text(
            "SELECT * FROM assignment_plan_versions WHERE project_id = :p ORDER BY version"),
            {"p": projectId})
        return {"versions": [_row(r) for r in rows]}


@router.get("/projects/{projectId}/assignment-plans/mine")
async def my_tasks(projectId: str, p: _api.RequirePrincipal) -> dict:
    """C-04: the caller's tasks from the latest APPROVED version; nothing about anyone else's."""
    async with _factory()() as db:
        await _own_project(db, p, projectId)
        row = (await db.execute(text(
            "SELECT version, brief, assignments FROM assignment_plan_versions"
            " WHERE project_id = :p AND state = 'approved' ORDER BY version DESC LIMIT 1"),
            {"p": projectId})).first()
    if row is None:
        return {"version": None, "tasks": []}
    brief = json.loads(row.brief)
    titles = {t["key"]: t["title"] for t in brief["tasks"]}
    mine = [a for a in json.loads(row.assignments) if a["assignee"] == p.userId]
    return {"version": row.version, "acceptanceCriteria": brief.get("acceptanceCriteria", []),
            "tasks": [{"task": a["task"], "title": titles[a["task"]], "kind": a["kind"],
                       "dependsOn": a["dependsOn"]} for a in mine]}


def _row(r: Any) -> dict[str, Any]:
    return {"version": r.version, "parentVersion": r.parent_version, "state": r.state,
            "brief": json.loads(r.brief), "assignments": json.loads(r.assignments),
            "createdBy": r.created_by, "note": r.note}
