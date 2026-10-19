"""Workspaces (master prompt part 39, Session 3 lane W). See docs/workspaces.md.

Raw SQLAlchemy Core (tables from migration 0040). Posture: deny by default,
team-scoped, a workspace the caller cannot see answers 404 (no existence
oracle), every consequential change is audited through the shared audit writer.
Project linking (projects.workspace_id) is repository-side bookkeeping only: it
never creates a workspace_members row.
"""

import json
import os
import re
from datetime import timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, Role, error_body
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .audit_emit import _emit as emit_audit
from .auth import get_principal
from .timeutil import now_utc

router = APIRouter(prefix="/api/v1/workspaces")

VISIBILITIES = ("private", "team", "org")
STATES = ("active", "paused", "archived", "deleted")
MEMBER_ROLES = ("owner", "member", "viewer")
BINDING_KINDS = ("repository", "environment")
DEFAULT_RESIDENCY = "us,eu,uk,ap,ca"
DEFAULT_MAX_PER_TEAM = 50
RETENTION_DAYS = 30
# action -> (states it may start from, resulting state)
TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "pause": (frozenset({"active"}), "paused"),
    "archive": (frozenset({"active", "paused"}), "archived"),
    "restore": (frozenset({"paused", "archived"}), "active"),
    "delete": (frozenset({"archived"}), "deleted"),
}
_AUDIT_ACTION = {"pause": "workspace.paused", "archive": "workspace.archived",
                 "restore": "workspace.restored", "delete": "workspace.deleted"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,79}$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,99}$")


def allowed_residency() -> list[str]:
    raw = os.environ.get("HARNESS_WORKSPACE_ALLOWED_RESIDENCY", DEFAULT_RESIDENCY)
    return [v.strip().lower() for v in raw.split(",") if v.strip()]


def max_per_team(request: Request) -> int:
    v = getattr(request.app.state, "workspace_max_per_team", None)
    return int(v if v is not None else os.environ.get("HARNESS_MAX_WORKSPACES_PER_TEAM",
                                                       DEFAULT_MAX_PER_TEAM))


def _err(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, msg))


def _iso(v: Any) -> str | None:
    return None if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))


# ------------------------------------------------------------- validation


def validate_name(name: str) -> str:
    n = name.strip()
    if not _NAME_RE.match(n):
        raise _err(422, "VALIDATION_ERROR",
                   "name must be 2-80 chars: letters, digits, space, . _ - (start alphanumeric)")
    return n


def validate_visibility(v: Any) -> str:
    if v not in VISIBILITIES:
        raise _err(422, "VALIDATION_ERROR", f"visibility must be one of {list(VISIBILITIES)}")
    return v


def validate_residency(v: Any) -> str:
    allowed = allowed_residency()
    if not isinstance(v, str) or v.lower() not in allowed:
        raise _err(422, "VALIDATION_ERROR", f"dataResidency must be one of {allowed}")
    return v.lower()


def validate_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list) or len(tags) > 20:
        raise _err(422, "VALIDATION_ERROR", "tags must be a list of at most 20 items")
    out = sorted({str(t).lower() for t in tags})
    if any(not _TAG_RE.match(t) for t in out):
        raise _err(422, "VALIDATION_ERROR", "tags: lowercase letters, digits, - and _ (max 32)")
    return out


def validate_settings(s: Any) -> dict[str, Any]:
    """Closed schema: unknown keys and wrong types are 422."""
    if not isinstance(s, dict):
        raise _err(422, "VALIDATION_ERROR", "settings must be an object")
    out: dict[str, Any] = {}
    for k, v in s.items():
        if k == "description" and isinstance(v, str) and len(v) <= 500:
            out[k] = v
        elif k == "default_branch" and isinstance(v, str) and _BRANCH_RE.match(v):
            out[k] = v
        elif (k == "max_concurrent_runs" and isinstance(v, int) and not isinstance(v, bool)
              and 1 <= v <= 50):
            out[k] = v
        elif k == "require_plan_approval" and isinstance(v, bool):
            out[k] = v
        else:
            raise _err(422, "VALIDATION_ERROR", f"invalid or unknown setting: {k!r}")
    return out


def validate_defaults(d: Any) -> dict[str, Any]:
    if not isinstance(d, dict):
        raise _err(422, "VALIDATION_ERROR", "defaults must be an object")
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k == "visibility":
            out[k] = validate_visibility(v)
        elif k == "dataResidency":
            out[k] = validate_residency(v)
        elif k == "tags":
            out[k] = validate_tags(v)
        elif k == "settings":
            out[k] = validate_settings(v)
        else:
            raise _err(422, "VALIDATION_ERROR", f"unknown template default: {k!r}")
    return out


# ------------------------------------------------------------- access


def _is_admin(p: Principal) -> bool:
    return p.role == Role.ADMIN


def _require_writer_role(p: Principal) -> None:
    if p.role not in (Role.ADMIN, Role.ENGINEER):
        raise _err(403, "FORBIDDEN", "admin or engineer role required")


def _require_admin(p: Principal) -> None:
    if not _is_admin(p):
        raise _err(403, "FORBIDDEN", "admin only")


async def _team_org(db, team_id: str) -> str | None:
    """teams.org_id (lane O adds the column); None when absent or unset."""
    try:
        row = (await db.execute(text("SELECT org_id FROM teams WHERE id = :t"),
                                {"t": team_id})).first()
    except Exception:  # noqa: BLE001 — column not migrated yet
        await db.rollback()
        return None
    return row[0] if row and row[0] else None


async def _ws_role(db, ws_id: str, user_id: str) -> str | None:
    row = (await db.execute(
        text("SELECT role FROM workspace_members WHERE workspace_id=:w AND user_id=:u"
             " AND removed_at IS NULL"), {"w": ws_id, "u": user_id})).first()
    return row[0] if row else None


async def _load(db, p: Principal, ws_id: str, *, write: bool = False):
    """Visible workspace row or 404; with write=True also owner/admin or 403."""
    row = (await db.execute(text("SELECT * FROM workspaces WHERE id = :i"),
                            {"i": ws_id})).mappings().first()
    nf = _err(404, "NOT_FOUND", "workspace not found")
    if row is None:
        raise nf
    role = None
    if row["team_id"] == p.teamId:
        if row["state"] == "deleted" and not _is_admin(p):
            raise nf
        role = await _ws_role(db, ws_id, p.userId)
        if row["visibility"] == "private" and role is None and not _is_admin(p):
            raise nf
    else:  # only org-visible workspaces of a team in the SAME org are readable
        if write or row["visibility"] != "org" or row["state"] == "deleted" or not row["org_id"]:
            raise nf
        if await _team_org(db, p.teamId) != row["org_id"]:
            raise nf
    if write and not (_is_admin(p) or role == "owner"):
        raise _err(403, "FORBIDDEN", "workspace owner or team admin required")
    if write:
        _require_writer_role(p)
    return row


def _require_mutable(row) -> None:
    if row["state"] not in ("active", "paused"):
        raise _err(409, "ILLEGAL_STATE", f"workspace is {row['state']}; restore it first")


def _out(row) -> dict[str, Any]:
    return {
        "id": row["id"], "teamId": row["team_id"], "orgId": row["org_id"],
        "projectId": row["project_id"], "name": row["name"], "state": row["state"],
        "stateReason": row["state_reason"], "visibility": row["visibility"],
        "dataResidency": row["data_residency"], "ownerUserId": row["owner_user_id"],
        "tags": json.loads(row["tags"]), "settings": json.loads(row["settings"]),
        "settingsVersion": row["settings_version"],
        "template": ({"name": row["template_name"], "version": row["template_version"]}
                     if row["template_name"] else None),
        "createdAt": _iso(row["created_at"]), "updatedAt": _iso(row["updated_at"]),
        "deletedAt": _iso(row["deleted_at"]), "purgeAfter": _iso(row["purge_after"]),
    }


async def _audit(db, action: str, p: Principal, ws_id: str, detail: dict) -> None:
    await emit_audit(db, action, user_id=p.userId, team_id=p.teamId,
                     target_type="workspace", target_id=ws_id, detail=detail)


# ------------------------------------------------------------- bodies


class CreateBody(BaseModel):
    name: str = Field(max_length=200)
    visibility: str | None = Field(default=None, max_length=16)
    dataResidency: str | None = Field(default=None, max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=20)
    templateName: str | None = Field(default=None, max_length=80)
    templateVersion: int | None = Field(default=None, ge=1)


class PatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    visibility: str | None = Field(default=None, max_length=16)
    dataResidency: str | None = Field(default=None, max_length=16)
    tags: list[str] | None = Field(default=None, max_length=20)


class SettingsBody(BaseModel):
    settings: dict[str, Any]  # merge-patch: a null value removes the key
    expectedVersion: int | None = Field(default=None, ge=1)


class RevertBody(BaseModel):
    toVersion: int = Field(ge=1)
    expectedVersion: int | None = Field(default=None, ge=1)


class MemberBody(BaseModel):
    role: str = Field(default="member", max_length=16)


class LinkBody(BaseModel):
    projectId: str = Field(max_length=36)


class BindingBody(BaseModel):
    kind: str = Field(max_length=16)
    value: str = Field(min_length=1, max_length=400)


class TemplateBody(BaseModel):
    name: str = Field(max_length=200)
    defaults: dict[str, Any]


class ApplyBody(BaseModel):
    name: str = Field(max_length=80)
    version: int | None = Field(default=None, ge=1)


class DeleteBody(BaseModel):
    reason: str = Field(default="", max_length=500)


class ReasonBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


Auth = Annotated[Principal, Depends(get_principal)]


# ------------------------------------------------------------- settings versions


async def _write_settings(db, row, new: dict, p: Principal, expected: int | None, *,
                          reverted_from: int | None = None, tpl: tuple | None = None) -> int:
    """CAS the settings + append a version row (caller commits). Returns version."""
    cur = row["settings_version"]
    if expected is not None and expected != cur:
        raise _err(409, "VERSION_CONFLICT", f"settings are at version {cur}")
    old = json.loads(row["settings"])
    now = now_utc()
    r = await db.execute(
        text("UPDATE workspaces SET settings=:s, settings_version=:n, updated_at=:t"
             " WHERE id=:i AND settings_version=:c"),
        {"s": json.dumps(new, sort_keys=True), "n": cur + 1, "t": now, "i": row["id"], "c": cur})
    if r.rowcount != 1:
        raise _err(409, "VERSION_CONFLICT", "settings changed concurrently")
    await db.execute(
        text("INSERT INTO workspace_settings_versions (id, workspace_id, version, before_json,"
             " after_json, changed_by, reverted_from, template_name, template_version, created_at)"
             " VALUES (:id,:w,:v,:b,:a,:u,:rf,:tn,:tv,:t)"),
        {"id": str(uuid4()), "w": row["id"], "v": cur + 1, "b": json.dumps(old, sort_keys=True),
         "a": json.dumps(new, sort_keys=True), "u": p.userId, "rf": reverted_from,
         "tn": tpl[0] if tpl else None, "tv": tpl[1] if tpl else None, "t": now})
    return cur + 1


async def _settings_change(db, ws_id, p, new, expected, *, reverted_from=None, tpl=None):
    row = await _load(db, p, ws_id, write=True)
    _require_mutable(row)
    old = json.loads(row["settings"])
    if new == old:  # idempotent: no new version, no audit
        return {"settings": old, "settingsVersion": row["settings_version"], "changed": False}
    v = await _write_settings(db, row, new, p, expected, reverted_from=reverted_from, tpl=tpl)
    await db.commit()
    await _audit(db, "workspace.settings_changed", p, ws_id, {
        "version": v, "before": old, "after": new, "revertedFrom": reverted_from})
    return {"settings": new, "settingsVersion": v, "changed": True}


# ------------------------------------------------------------- templates / allowlist
# (static paths are registered BEFORE /{ws_id} routes)


async def _template(db, team_id: str, name: str, version: int | None):
    q = "SELECT * FROM workspace_templates WHERE team_id=:t AND name=:n"
    params: dict[str, Any] = {"t": team_id, "n": name}
    if version is not None:
        q += " AND version=:v"
        params["v"] = version
    row = (await db.execute(text(q + " ORDER BY version DESC LIMIT 1"),
                            params)).mappings().first()
    if row is None:
        raise _err(404, "NOT_FOUND", "template not found")
    return row


@router.post("/templates", status_code=201)
async def save_template(body: TemplateBody, request: Request, p: Auth) -> dict:
    _require_admin(p)
    name = validate_name(body.name)
    defaults = validate_defaults(body.defaults)
    async with request.app.state.session_factory() as db:
        cur = (await db.execute(
            text("SELECT COALESCE(MAX(version),0) FROM workspace_templates"
                 " WHERE team_id=:t AND name=:n"), {"t": p.teamId, "n": name})).scalar()
        tid = str(uuid4())
        try:
            await db.execute(
                text("INSERT INTO workspace_templates (id, team_id, name, version, defaults,"
                     " created_by, created_at) VALUES (:i,:t,:n,:v,:d,:u,:ts)"),
                {"i": tid, "t": p.teamId, "n": name, "v": cur + 1,
                 "d": json.dumps(defaults, sort_keys=True), "u": p.userId, "ts": now_utc()})
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            raise _err(409, "VERSION_CONFLICT", "template saved concurrently; retry") from e
        await _audit(db, "workspace.template_saved", p, tid,
                     {"name": name, "version": cur + 1, "after": defaults})
    return {"id": tid, "name": name, "version": cur + 1, "defaults": defaults}


@router.get("/templates")
async def list_templates(request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(
            text("SELECT * FROM workspace_templates WHERE team_id=:t ORDER BY name, version"),
            {"t": p.teamId})).mappings().all()
    return {"items": [{"id": r["id"], "name": r["name"], "version": r["version"],
                       "defaults": json.loads(r["defaults"]),
                       "createdAt": _iso(r["created_at"])} for r in rows]}


@router.post("/allowlist", status_code=201)
async def approve_binding(body: BindingBody, request: Request, p: Auth) -> dict:
    _require_admin(p)
    if body.kind not in BINDING_KINDS:
        raise _err(422, "VALIDATION_ERROR", f"kind must be one of {list(BINDING_KINDS)}")
    async with request.app.state.session_factory() as db:
        org = await _team_org(db, p.teamId)
        aid = str(uuid4())
        await db.execute(
            text("INSERT INTO workspace_binding_allowlist (id, team_id, org_id, kind, value,"
                 " created_by, created_at) VALUES (:i,:t,:o,:k,:v,:u,:ts)"),
            {"i": aid, "t": p.teamId, "o": org, "k": body.kind, "v": body.value.strip(),
             "u": p.userId, "ts": now_utc()})
        await db.commit()
        await _audit(db, "workspace.updated", p, aid, {
            "change": "binding_approved", "kind": body.kind, "value": body.value.strip()})
    return {"id": aid, "kind": body.kind, "value": body.value.strip()}


@router.get("/allowlist")
async def list_allowlist(request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(
            text("SELECT id, kind, value FROM workspace_binding_allowlist"
                 " WHERE team_id=:t AND revoked_at IS NULL ORDER BY kind, value"),
            {"t": p.teamId})).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.delete("/allowlist/{entry_id}", status_code=204)
async def revoke_allowlist(entry_id: str, request: Request, p: Auth) -> None:
    _require_admin(p)
    async with request.app.state.session_factory() as db:
        r = await db.execute(
            text("UPDATE workspace_binding_allowlist SET revoked_at=:t WHERE id=:i"
                 " AND team_id=:team"
                 " AND revoked_at IS NULL"), {"t": now_utc(), "i": entry_id, "team": p.teamId})
        await db.commit()
        if r.rowcount != 1:
            raise _err(404, "NOT_FOUND", "allowlist entry not found")
        await _audit(db, "workspace.updated", p, entry_id, {"change": "binding_unapproved"})


# ------------------------------------------------------------- create / search / read


@router.post("", status_code=201)
async def create_workspace(body: CreateBody, request: Request, p: Auth) -> dict:
    _require_writer_role(p)
    name = validate_name(body.name)
    explicit_tags = validate_tags(body.tags)
    async with request.app.state.session_factory() as db:
        defaults: dict[str, Any] = {}
        tpl = None
        if body.templateName:
            tpl = await _template(db, p.teamId, body.templateName, body.templateVersion)
            defaults = json.loads(tpl["defaults"])
        vis = validate_visibility(body.visibility or defaults.get("visibility") or "team")
        res_raw = body.dataResidency or defaults.get("dataResidency")
        if res_raw is None:
            raise _err(422, "VALIDATION_ERROR", "dataResidency is required (explicit policy)")
        res = validate_residency(res_raw)
        tags = sorted(set(explicit_tags) | set(defaults.get("tags", [])))
        settings = validate_settings(defaults.get("settings", {}))
        org = await _team_org(db, p.teamId)
        if vis == "org" and org is None:
            raise _err(422, "VALIDATION_ERROR", "visibility 'org' needs a team in an organization")
        used = (await db.execute(
            text("SELECT COUNT(*) FROM workspaces WHERE team_id=:t AND state <> 'deleted'"),
            {"t": p.teamId})).scalar()
        if used >= max_per_team(request):
            raise _err(409, "QUOTA_EXCEEDED", f"team workspace quota reached ({used})")
        ws_id, now = str(uuid4()), now_utc()
        try:
            await db.execute(
                text("INSERT INTO workspaces (id, team_id, org_id, name, name_key, state,"
                     " visibility, data_residency, owner_user_id, tags, settings, settings_version,"
                     " template_id, template_name, template_version, created_at, updated_at)"
                     " VALUES (:id,:t,:o,:n,:nk,'active',:v,:r,:u,:tags,:s,1,:ti,:tn,:tv,"
                     ":now,:now)"),
                {"id": ws_id, "t": p.teamId, "o": org, "n": name, "nk": name.lower(), "v": vis,
                 "r": res, "u": p.userId, "tags": json.dumps(tags),
                 "s": json.dumps(settings, sort_keys=True), "ti": tpl["id"] if tpl else None,
                 "tn": tpl["name"] if tpl else None, "tv": tpl["version"] if tpl else None,
                 "now": now})
            await db.execute(
                text("INSERT INTO workspace_members (workspace_id, user_id, role, added_by,"
                     " created_at) VALUES (:w,:u,'owner',:u,:now)"),
                {"w": ws_id, "u": p.userId, "now": now})
            await db.execute(
                text("INSERT INTO workspace_settings_versions (id, workspace_id, version,"
                     " before_json, after_json, changed_by, template_name, template_version,"
                     " created_at) VALUES (:i,:w,1,'{}',:a,:u,:tn,:tv,:now)"),
                {"i": str(uuid4()), "w": ws_id, "a": json.dumps(settings, sort_keys=True),
                 "u": p.userId, "tn": tpl["name"] if tpl else None,
                 "tv": tpl["version"] if tpl else None, "now": now})
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            raise _err(409, "NAME_TAKEN", "a workspace with this name exists in the team") from e
        await _audit(db, "workspace.created", p, ws_id, {
            "name": name, "visibility": vis, "dataResidency": res,
            "after": {"state": "active", "tags": tags}})
        if tpl:
            await _audit(db, "workspace.template_applied", p, ws_id, {
                "template": tpl["name"], "templateVersion": tpl["version"], "at": "create"})
        return _out(await _load(db, p, ws_id))


def _esc(s: str) -> str:
    return s.replace("!", "!!").replace("%", "!%").replace("_", "!_")


@router.get("")
async def search_workspaces(
    request: Request, p: Auth, q: str | None = None, state: str | None = None,
    template: str | None = None, tag: str | None = None, limit: int = 25,
    cursor: str | None = None,
) -> dict:
    if state is not None and state not in STATES:
        raise _err(422, "VALIDATION_ERROR", f"state must be one of {list(STATES)}")
    if tag is not None and not _TAG_RE.match(tag.lower()):
        raise _err(422, "VALIDATION_ERROR", "invalid tag")
    limit = max(1, min(int(limit), 100))
    where = ["w.team_id = :team"]
    prm: dict[str, Any] = {"team": p.teamId, "uid": p.userId, "lim": limit + 1}
    if not _is_admin(p):
        where.append("w.state <> 'deleted'")
        where.append("(w.visibility <> 'private' OR EXISTS (SELECT 1 FROM workspace_members m"
                     " WHERE m.workspace_id = w.id AND m.user_id = :uid AND m.removed_at IS NULL))")
    if state:
        where.append("w.state = :state")
        prm["state"] = state
    elif _is_admin(p):
        where.append("w.state <> 'deleted'")
    if q:
        where.append("w.name_key LIKE :q ESCAPE '!'")
        prm["q"] = f"%{_esc(q.strip().lower())}%"
    if template:
        where.append("w.template_name = :tpl")
        prm["tpl"] = template
    if tag:
        where.append("w.tags LIKE :tag")
        prm["tag"] = f'%"{tag.lower()}"%'
    if cursor:
        where.append("(w.name_key, w.id) > (SELECT c.name_key, c.id FROM workspaces c"
                     " WHERE c.id = :cur AND c.team_id = :team)")
        prm["cur"] = cursor
    sql = ("SELECT w.* FROM workspaces w WHERE " + " AND ".join(where)
           + " ORDER BY w.name_key, w.id LIMIT :lim")
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(text(sql), prm)).mappings().all()
    items = [_out(r) for r in rows[:limit]]
    return {"items": items, "nextCursor": items[-1]["id"] if len(rows) > limit else None}


@router.get("/{ws_id}")
async def get_workspace(ws_id: str, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        return _out(await _load(db, p, ws_id))


@router.patch("/{ws_id}")
async def update_workspace(ws_id: str, body: PatchBody, request: Request, p: Auth) -> dict:
    sets: dict[str, Any] = {}
    if body.name is not None:
        sets["name"] = validate_name(body.name)
    if body.visibility is not None:
        sets["visibility"] = validate_visibility(body.visibility)
    if body.dataResidency is not None:
        sets["data_residency"] = validate_residency(body.dataResidency)
    if body.tags is not None:
        sets["tags"] = validate_tags(body.tags)
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id, write=True)
        _require_mutable(row)
        if sets.get("visibility") == "org" and await _team_org(db, p.teamId) is None:
            raise _err(422, "VALIDATION_ERROR", "visibility 'org' needs a team in an organization")
        before = {k: (json.loads(row[k]) if k == "tags" else row[k]) for k in sets}
        changed = {k: v for k, v in sets.items() if before[k] != v}
        if not changed:
            return _out(row)
        cols = dict(changed)
        if "name" in cols:
            cols["name_key"] = cols["name"].lower()
        if "tags" in cols:
            cols["tags"] = json.dumps(cols["tags"])
        assign = ", ".join(f"{c} = :{c}" for c in cols)
        try:  # column names come from the fixed set above, never from input
            await db.execute(text(f"UPDATE workspaces SET {assign}, updated_at = :now"
                                  " WHERE id = :id"), {**cols, "now": now_utc(), "id": ws_id})
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            raise _err(409, "NAME_TAKEN", "a workspace with this name exists in the team") from e
        await _audit(db, "workspace.updated", p, ws_id, {
            "before": {k: before[k] for k in changed}, "after": changed})
        return _out(await _load(db, p, ws_id))


# ------------------------------------------------------------- settings


@router.get("/{ws_id}/settings")
async def get_settings(ws_id: str, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id)
    return {"settings": json.loads(row["settings"]), "settingsVersion": row["settings_version"]}


@router.put("/{ws_id}/settings")
async def put_settings(ws_id: str, body: SettingsBody, request: Request, p: Auth) -> dict:
    patch = body.settings
    removals = {k for k, v in patch.items() if v is None}
    upd = validate_settings({k: v for k, v in patch.items() if v is not None})
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id, write=True)
        new = {k: v for k, v in json.loads(row["settings"]).items() if k not in removals}
        new.update(upd)
        return await _settings_change(db, ws_id, p, new, body.expectedVersion)


@router.get("/{ws_id}/settings/history")
async def settings_history(ws_id: str, request: Request, p: Auth, limit: int = 50) -> dict:
    async with request.app.state.session_factory() as db:
        await _load(db, p, ws_id)
        rows = (await db.execute(
            text("SELECT * FROM workspace_settings_versions WHERE workspace_id=:w"
                 " ORDER BY version DESC LIMIT :l"),
            {"w": ws_id, "l": max(1, min(int(limit), 200))})).mappings().all()
    return {"items": [{
        "version": r["version"], "before": json.loads(r["before_json"]),
        "after": json.loads(r["after_json"]), "changedBy": r["changed_by"],
        "revertedFrom": r["reverted_from"],
        "template": ({"name": r["template_name"], "version": r["template_version"]}
                     if r["template_name"] else None),
        "createdAt": _iso(r["created_at"])} for r in rows]}


@router.post("/{ws_id}/settings/revert")
async def revert_settings(ws_id: str, body: RevertBody, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        await _load(db, p, ws_id, write=True)
        target = (await db.execute(
            text("SELECT after_json FROM workspace_settings_versions"
                 " WHERE workspace_id=:w AND version=:v"),
            {"w": ws_id, "v": body.toVersion})).first()
        if target is None:
            raise _err(404, "NOT_FOUND", "settings version not found")
        return await _settings_change(db, ws_id, p, json.loads(target[0]), body.expectedVersion,
                                      reverted_from=body.toVersion)


# ------------------------------------------------------------- templates (apply)


@router.post("/{ws_id}/apply-template")
async def apply_template(ws_id: str, body: ApplyBody, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id, write=True)
        _require_mutable(row)
        tpl = await _template(db, p.teamId, body.name, body.version)
        d = json.loads(tpl["defaults"])
        vis = d.get("visibility", row["visibility"])
        if vis == "org" and await _team_org(db, p.teamId) is None:
            raise _err(422, "VALIDATION_ERROR", "visibility 'org' needs a team in an organization")
        tags = sorted(set(json.loads(row["tags"])) | set(d.get("tags", [])))
        settings = {**json.loads(row["settings"]), **d.get("settings", {})}
        await db.execute(
            text("UPDATE workspaces SET visibility=:v, data_residency=:r, tags=:g, template_id=:ti,"
                 " template_name=:tn, template_version=:tv, updated_at=:now WHERE id=:id"),
            {"v": vis, "r": d.get("dataResidency", row["data_residency"]),
             "g": json.dumps(tags), "ti": tpl["id"], "tn": tpl["name"], "tv": tpl["version"],
             "now": now_utc(), "id": ws_id})
        row = (await db.execute(text("SELECT * FROM workspaces WHERE id=:i"),
                                {"i": ws_id})).mappings().first()
        if settings != json.loads(row["settings"]):
            await _write_settings(db, row, settings, p, None, tpl=(tpl["name"], tpl["version"]))
        await db.commit()
        await _audit(db, "workspace.template_applied", p, ws_id, {
            "template": tpl["name"], "templateVersion": tpl["version"], "at": "apply",
            "before": {"visibility": row["visibility"]}, "after": {"visibility": vis}})
        return _out(await _load(db, p, ws_id))


# ------------------------------------------------------------- members


@router.get("/{ws_id}/members")
async def list_members(ws_id: str, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        await _load(db, p, ws_id)
        rows = (await db.execute(
            text("SELECT user_id, role FROM workspace_members WHERE workspace_id=:w"
                 " AND removed_at IS NULL ORDER BY user_id"), {"w": ws_id})).mappings().all()
    return {"items": [{"userId": r["user_id"], "role": r["role"]} for r in rows]}


@router.put("/{ws_id}/members/{user_id}")
async def put_member(ws_id: str, user_id: str, body: MemberBody, request: Request,
                     p: Auth) -> dict:
    if body.role not in MEMBER_ROLES:
        raise _err(422, "VALIDATION_ERROR", f"role must be one of {list(MEMBER_ROLES)}")
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id, write=True)
        _require_mutable(row)
        in_team = (await db.execute(
            text("SELECT 1 FROM team_members WHERE team_id=:t AND user_id=:u"),
            {"t": p.teamId, "u": user_id})).first()
        if in_team is None:
            raise _err(422, "NOT_A_TEAM_MEMBER", "only members of the owning team can be added")
        cur = await _ws_role(db, ws_id, user_id)
        if cur == body.role:
            return {"userId": user_id, "role": cur}
        if cur == "owner":
            await _require_other_owner(db, ws_id, user_id)
        now = now_utc()
        await db.execute(text("DELETE FROM workspace_members WHERE workspace_id=:w AND user_id=:u"),
                         {"w": ws_id, "u": user_id})
        await db.execute(
            text("INSERT INTO workspace_members (workspace_id, user_id, role, added_by, created_at)"
                 " VALUES (:w,:u,:r,:by,:now)"),
            {"w": ws_id, "u": user_id, "r": body.role, "by": p.userId, "now": now})
        await db.commit()
        await _audit(db, "workspace.updated", p, ws_id, {
            "change": "member_set", "userId": user_id,
            "before": {"role": cur}, "after": {"role": body.role}})
    return {"userId": user_id, "role": body.role}


async def _require_other_owner(db, ws_id: str, user_id: str) -> None:
    n = (await db.execute(
        text("SELECT COUNT(*) FROM workspace_members WHERE workspace_id=:w AND role='owner'"
             " AND removed_at IS NULL AND user_id <> :u"), {"w": ws_id, "u": user_id})).scalar()
    if n == 0:
        raise _err(409, "LAST_OWNER", "a workspace must keep at least one owner")


@router.delete("/{ws_id}/members/{user_id}", status_code=204)
async def remove_member(ws_id: str, user_id: str, request: Request, p: Auth) -> None:
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id, write=True)
        _require_mutable(row)
        cur = await _ws_role(db, ws_id, user_id)
        if cur is None:
            raise _err(404, "NOT_FOUND", "member not found")
        if cur == "owner":
            await _require_other_owner(db, ws_id, user_id)
        await db.execute(text("DELETE FROM workspace_members WHERE workspace_id=:w AND user_id=:u"),
                         {"w": ws_id, "u": user_id})
        await db.commit()
        await _audit(db, "workspace.updated", p, ws_id, {
            "change": "member_removed", "userId": user_id, "before": {"role": cur}})


# ------------------------------------------------------------- project links


@router.get("/{ws_id}/projects")
async def list_projects(ws_id: str, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id)
        if row["team_id"] != p.teamId:  # cross-team org readers get workspace metadata only
            return {"items": []}
        rows = (await db.execute(
            text("SELECT id, name, repo_full_name FROM projects WHERE workspace_id=:w"
                 " ORDER BY name"), {"w": ws_id})).mappings().all()
    return {"items": [{"id": r["id"], "name": r["name"], "repoFullName": r["repo_full_name"]}
                      for r in rows]}


@router.post("/{ws_id}/projects", status_code=201)
async def link_project(ws_id: str, body: LinkBody, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id, write=True)
        _require_mutable(row)
        proj = (await db.execute(
            text("SELECT id, workspace_id FROM projects WHERE id=:p AND team_id=:t"),
            {"p": body.projectId, "t": p.teamId})).mappings().first()
        if proj is None:
            raise _err(404, "NOT_FOUND", "project not found")
        if proj["workspace_id"] == ws_id:
            return {"workspaceId": ws_id, "projectId": body.projectId}
        r = await db.execute(
            text("UPDATE projects SET workspace_id=:w WHERE id=:p AND workspace_id IS NULL"),
            {"w": ws_id, "p": body.projectId})
        if r.rowcount != 1:
            raise _err(409, "PROJECT_ALREADY_LINKED", "project belongs to another workspace")
        await db.execute(
            text("UPDATE workspaces SET project_id=:p WHERE id=:w AND project_id IS NULL"),
            {"p": body.projectId, "w": ws_id})
        await db.commit()
        # Deliberately NO workspace_members insert: repository link != collaboration access.
        await _audit(db, "workspace.updated", p, ws_id, {
            "change": "project_linked", "projectId": body.projectId})
    return {"workspaceId": ws_id, "projectId": body.projectId}


@router.delete("/{ws_id}/projects/{project_id}", status_code=204)
async def unlink_project(ws_id: str, project_id: str, request: Request, p: Auth) -> None:
    async with request.app.state.session_factory() as db:
        await _load(db, p, ws_id, write=True)  # allowed in any state: lifts restrictions
        r = await db.execute(
            text("UPDATE projects SET workspace_id=NULL WHERE id=:p AND workspace_id=:w"
                 " AND team_id=:t"), {"p": project_id, "w": ws_id, "t": p.teamId})
        if r.rowcount != 1:
            raise _err(404, "NOT_FOUND", "project is not linked to this workspace")
        await db.execute(
            text("UPDATE workspaces SET project_id=NULL WHERE id=:w AND project_id=:p"),
            {"w": ws_id, "p": project_id})
        await db.commit()
        await _audit(db, "workspace.updated", p, ws_id, {
            "change": "project_unlinked", "projectId": project_id})


# ------------------------------------------------------------- bindings


async def _approved(db, p: Principal, kind: str, value: str) -> bool:
    org = await _team_org(db, p.teamId)
    row = (await db.execute(
        text("SELECT 1 FROM workspace_binding_allowlist WHERE kind=:k AND value=:v"
             " AND revoked_at IS NULL AND (team_id=:t OR (:o IS NOT NULL AND org_id=:o)) LIMIT 1"),
        {"k": kind, "v": value, "t": p.teamId, "o": org})).first()
    return row is not None


@router.get("/{ws_id}/bindings")
async def list_bindings(ws_id: str, request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id)
        if row["team_id"] != p.teamId:
            return {"items": []}
        rows = (await db.execute(
            text("SELECT id, kind, value FROM workspace_bindings WHERE workspace_id=:w"
                 " AND removed_at IS NULL ORDER BY kind, value"), {"w": ws_id})).mappings().all()
        return {"items": [{**dict(r), "approved": await _approved(db, p, r["kind"], r["value"])}
                          for r in rows]}


@router.post("/{ws_id}/bindings", status_code=201)
async def add_binding(ws_id: str, body: BindingBody, request: Request, p: Auth) -> dict:
    if body.kind not in BINDING_KINDS:
        raise _err(422, "VALIDATION_ERROR", f"kind must be one of {list(BINDING_KINDS)}")
    value = body.value.strip()
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id, write=True)
        _require_mutable(row)
        if not await _approved(db, p, body.kind, value):
            raise _err(403, "BINDING_NOT_APPROVED",
                       f"{body.kind} {value!r} is not on the approved allowlist")
        dup = (await db.execute(
            text("SELECT id FROM workspace_bindings WHERE workspace_id=:w AND kind=:k AND value=:v"
                 " AND removed_at IS NULL"), {"w": ws_id, "k": body.kind, "v": value})).first()
        if dup:
            return {"id": dup[0], "kind": body.kind, "value": value}
        bid = str(uuid4())
        await db.execute(
            text("INSERT INTO workspace_bindings (id, workspace_id, kind, value, created_by,"
                 " created_at) VALUES (:i,:w,:k,:v,:u,:t)"),
            {"i": bid, "w": ws_id, "k": body.kind, "v": value, "u": p.userId, "t": now_utc()})
        await db.commit()
        await _audit(db, "workspace.updated", p, ws_id, {
            "change": "binding_added", "kind": body.kind, "value": value})
    return {"id": bid, "kind": body.kind, "value": value}


@router.delete("/{ws_id}/bindings/{binding_id}", status_code=204)
async def remove_binding(ws_id: str, binding_id: str, request: Request, p: Auth) -> None:
    async with request.app.state.session_factory() as db:
        await _load(db, p, ws_id, write=True)
        r = await db.execute(
            text("UPDATE workspace_bindings SET removed_at=:t WHERE id=:i AND workspace_id=:w"
                 " AND removed_at IS NULL"), {"t": now_utc(), "i": binding_id, "w": ws_id})
        if r.rowcount != 1:
            raise _err(404, "NOT_FOUND", "binding not found")
        await db.commit()
        await _audit(db, "workspace.updated", p, ws_id, {
            "change": "binding_removed", "bindingId": binding_id})


# ------------------------------------------------------------- lifecycle


async def _transition(request: Request, p: Auth, ws_id: str, action: str,
                      reason: str | None) -> dict:
    allowed_from, target = TRANSITIONS[action]
    async with request.app.state.session_factory() as db:
        row = await _load(db, p, ws_id, write=True)
        if row["state"] == target:  # idempotent: no change, no audit
            return {**_out(row), "changed": False}
        if row["state"] not in allowed_from:
            if action == "delete" and row["state"] in ("active", "paused"):
                raise _err(409, "WORKSPACE_NOT_ARCHIVED", "archive the workspace before deleting")
            raise _err(409, "ILLEGAL_TRANSITION", f"cannot {action} a {row['state']} workspace")
        now = now_utc()
        extra = ""
        prm: dict[str, Any] = {"new": target, "reason": reason, "now": now, "id": ws_id,
                               "ok": list(allowed_from)}
        if action == "delete":
            extra = ", deleted_at = :now, purge_after = :purge"
            prm["purge"] = now + timedelta(days=RETENTION_DAYS)
        ok = ", ".join(f":ok{i}" for i in range(len(allowed_from)))
        for i, s in enumerate(sorted(allowed_from)):
            prm[f"ok{i}"] = s
        prm.pop("ok")
        r = await db.execute(  # compare-and-set: loses cleanly against a concurrent transition
            text(f"UPDATE workspaces SET state=:new, state_reason=:reason, updated_at=:now{extra}"
                 f" WHERE id=:id AND state IN ({ok})"), prm)
        await db.commit()
        fresh = await _load(db, p, ws_id)
        if r.rowcount != 1:
            if fresh["state"] == target:
                return {**_out(fresh), "changed": False}
            raise _err(409, "ILLEGAL_TRANSITION", f"workspace is now {fresh['state']}")
        await _audit(db, _AUDIT_ACTION[action], p, ws_id, {
            "before": {"state": row["state"]}, "after": {"state": target}, "reason": reason})
        return {**_out(fresh), "changed": True}


@router.post("/{ws_id}/pause")
async def pause_workspace(ws_id: str, request: Request, p: Auth,
                          body: ReasonBody | None = None) -> dict:
    return await _transition(request, p, ws_id, "pause", body.reason if body else None)


@router.post("/{ws_id}/archive")
async def archive_workspace(ws_id: str, request: Request, p: Auth,
                            body: ReasonBody | None = None) -> dict:
    return await _transition(request, p, ws_id, "archive", body.reason if body else None)


@router.post("/{ws_id}/restore")
async def restore_workspace(ws_id: str, request: Request, p: Auth,
                            body: ReasonBody | None = None) -> dict:
    return await _transition(request, p, ws_id, "restore", body.reason if body else None)


@router.post("/{ws_id}/delete")
async def delete_workspace(ws_id: str, body: DeleteBody, request: Request, p: Auth) -> dict:
    _require_admin(p)
    reason = body.reason.strip()
    if len(reason) < 3:
        raise _err(422, "VALIDATION_ERROR", "a reason (3+ chars) is required to delete")
    return await _transition(request, p, ws_id, "delete", reason)
