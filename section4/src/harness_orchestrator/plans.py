"""Task statement and versioned, reviewable plans (part 44, directives 001-003). Rules and limits:
docs/plans.md.

A run has one task spec (statement, success criteria, constraints, owner; optimistic version, one
revision row per save) and an ordered list of immutable plan versions. The legacy ``agent_plans``
row is mirrored lazily as a version (``source_plan_id``) on every read or write and its
approved/rejected status is followed from there, so the legacy routes and the engine keep working
unchanged. Approval is a legacy decision: once any version is ``approved`` the plan is locked and
every change (new version, comment, edit proposal, edit decision) is refused with 409
``PLAN_LOCKED``. Reviewers comment on a version; anyone with ``comment`` proposes a structured patch
(``edits``); an ``approve`` holder other than the proposer accepts it (which creates a new version)
or rejects it.

Identity is always the authenticated Principal. The run is guarded by the shared run ownership
dependency (``api._authz_dep``): a foreign or unknown run answers the same 404 envelope. Audit rows
and collaboration events carry ids and states only, never task, plan or comment text. Raw SQL only
(``text()``): sections may import only ``harness_contracts``.
"""

import difflib
import hashlib
import json
import logging
import os
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from harness_contracts.roles import CAN_APPROVE_PLANS, READ_ONLY, Principal, Role
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api as _api
from harness_orchestrator import collab

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["plans"])

_service: Any = None

MAX_STATEMENT = 4000
MAX_LIST_ITEMS = 20  # success criteria / constraints
MAX_ITEM = 500
MAX_CONTENT_BYTES = 64 * 1024  # canonical JSON of one plan version
MAX_VERSIONS = 100  # per run
MAX_COMMENTS = 500  # per plan version
MAX_PENDING_EDITS = 50  # per plan version
MAX_PATCH_OPS = 20
MAX_BODY = collab.MAX_TEXT
FIELDS = (
    "goal",
    "steps",
    "tests",
    "risks",
    "filesLikelyToChange",
    "forbiddenPaths",
    "expectedResult",
    "estimatedCostUsd",
)
LIST_FIELDS = ("steps", "tests", "risks", "filesLikelyToChange", "forbiddenPaths")
OPEN_STATES = ("draft", "proposed")
_TARGET = "agent_run"

# (principal, run id, "METHOD route") of the request in flight, for denial audit.
_ctx: ContextVar[tuple[Principal, str, str] | None] = ContextVar("_plans_ctx", default=None)


def configure_plans(service: Any) -> None:
    """Deploy-time wiring (like configure_discussion): the service owning the DB factory."""
    global _service
    _service = service


def _now() -> datetime:
    return collab.db_now()  # the one clock seam (tests patch it)


def _dt(v: Any) -> datetime | None:
    if v is None:
        return None
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _iso(v: Any) -> str | None:
    d = _dt(v)
    return d.isoformat() if d else None


def _jl(v: Any, default: Any) -> Any:
    """JSON column value: text on sqlite (and some drivers), already decoded on others."""
    if v is None:
        return default
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v


class PlansError(collab.CollabError):
    """CollabError that also carries structured data for the HTTP body."""

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(status, code, message)
        self.extra = extra or {}


def _envelope(status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> Any:
    return HTTPException(
        status, detail={"error": {"code": code, "message": message}, **(extra or {})}
    )


# ----------------------------------------------------------------------- guards

_RUN_GUARD = _api._authz_dep("read", audit=True)
_ROLE_OK = {
    "read": lambda r: True,
    "comment": lambda r: r != Role.GUEST,
    "steer": lambda r: r not in READ_ONLY,
    "approve": lambda r: r in CAN_APPROVE_PLANS,
}


def _factory() -> Any:
    svc = _service if _service is not None else _api._service
    return getattr(svc, "session_factory", None)


async def _audit(db: Any, p: Principal, action: str, run_id: str, detail: dict[str, Any]) -> None:
    """Append an audit row in the caller's transaction. Detail is ids and states only."""
    member = (
        await db.execute(
            text("SELECT id FROM team_members WHERE team_id = :t AND user_id = :u LIMIT 1"),
            {"t": p.teamId, "u": p.userId},
        )
    ).first()
    await db.execute(
        text(
            "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, target_id,"
            " detail, ts) VALUES (:a, 'human', :act, :tt, :tid, :d, :ts)"
        ),
        {
            "a": member.id if member else None,
            "act": action,
            "tt": _TARGET,
            "tid": run_id,
            "d": json.dumps(
                {"userId": p.userId, "teamId": p.teamId, "runId": run_id, **collab._scrub(detail)},
                default=str,
            ),
            "ts": _now(),
        },
    )


async def _deny_audit(p: Principal, run_id: str, route: str, code: str, status: int) -> None:
    """Every refused request by an authenticated principal leaves one authz.denied row."""
    factory = _factory()
    if factory is None:
        return
    try:
        async with factory() as db:
            await _audit(
                db, p, "authz.denied", run_id, {"reason": code, "status": status, "attempt": route}
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - audit must never mask the denial
        logger.exception("plans denial audit failed run=%s", run_id)


def _guard(perm: str) -> Any:
    async def dep(request: Request, runId: str, p: _api.RequirePrincipal) -> Principal:
        route = f"{request.method} {getattr(request.scope.get('route'), 'path', request.url.path)}"
        _ctx.set((p, runId, route))
        if not _ROLE_OK[perm](p.role):  # role gate first: no new existence oracle for low roles
            _api._denial_ctx.set((p, runId, route))
            await _deny_audit(p, runId, route, "ROLE_DENIED", 403)
            raise _envelope(403, "ROLE_DENIED", f"role may not {perm} here")
        await _RUN_GUARD(request, runId, p)
        _api._pilot_gate(p)
        if perm != "read":
            await _api._persist_run(runId)
        return p

    return dep


async def _call(call: Any) -> Any:
    factory = _factory()
    if factory is None:
        raise _envelope(503, "PLANS_UNAVAILABLE", "no database wired")
    try:
        async with factory() as db:
            return await call(db)
    except collab.CollabError as e:
        ctx = _ctx.get()
        if e.status == 403 and not e.audited and ctx is not None:
            await _deny_audit(ctx[0], ctx[1], ctx[2], e.code, e.status)
        raise _envelope(e.status, e.code, str(e), getattr(e, "extra", None)) from e


async def _emit(db: Any, p: Principal, run_id: str, kind: str, payload: dict[str, Any]) -> None:
    """A durable collaboration event (ids only, never text) so peers refresh."""
    await collab._event(db, p, run_id, kind, payload, None)


# ------------------------------------------------------------------ plan content


def _check_path(v: str) -> str:
    parts = v.split("/")
    if not v or len(v) > 512 or "\x00" in v or "\\" in v or v.startswith("/") or ".." in parts:
        raise ValueError("not a safe relative path")
    return v


Line = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
ShortLine = Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class PlanContent(BaseModel):
    """The structured plan: the fields of the legacy AgentPlan, bounded."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=MAX_BODY)
    steps: list[Line] = Field(default_factory=list, max_length=100)
    tests: list[ShortLine] = Field(default_factory=list, max_length=50)
    risks: list[ShortLine] = Field(default_factory=list, max_length=50)
    filesLikelyToChange: list[str] = Field(default_factory=list, max_length=200)
    forbiddenPaths: list[str] = Field(default_factory=list, max_length=200)
    expectedResult: str = Field(default="", max_length=MAX_BODY)
    estimatedCostUsd: float = Field(default=0.0, ge=0, le=1_000_000)

    @field_validator("filesLikelyToChange", "forbiddenPaths")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return [_check_path(x) for x in v]


def _norm(raw: dict[str, Any]) -> dict[str, Any]:
    """Fixed key order and types, so the same plan always has the same digest."""
    return {
        "goal": str(raw.get("goal") or ""),
        "steps": [str(x) for x in raw.get("steps") or []],
        "tests": [str(x) for x in raw.get("tests") or []],
        "risks": [str(x) for x in raw.get("risks") or []],
        "filesLikelyToChange": [str(x) for x in raw.get("filesLikelyToChange") or []],
        "forbiddenPaths": [str(x) for x in raw.get("forbiddenPaths") or []],
        "expectedResult": str(raw.get("expectedResult") or ""),
        "estimatedCostUsd": round(float(raw.get("estimatedCostUsd") or 0), 6),
    }


def _canon(content: dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(canon: str) -> str:
    return hashlib.sha256(canon.encode()).hexdigest()


def _finalize(model: PlanContent) -> tuple[dict[str, Any], str, str]:
    content = _norm(model.model_dump())
    canon = _canon(content)
    if len(canon.encode()) > MAX_CONTENT_BYTES:
        raise PlansError(
            422, "PLAN_TOO_LARGE", f"a plan version is at most {MAX_CONTENT_BYTES} bytes"
        )
    return content, canon, _digest(canon)


# ---------------------------------------------------------------- patches and diff


class PatchOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["replace", "add", "remove"]
    path: str = Field(pattern=r"^/[A-Za-z]{1,32}(/(\d{1,4}|-))?$")
    value: Any = None


def _apply_patch(content: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply ops in order to a copy (list indexes see earlier ops). Whole-field ``replace`` or
    ``replace``/``add``/``remove`` on one list item (``-`` appends). Anything else is BAD_PATCH."""
    out = json.loads(json.dumps(content))
    for i, o in enumerate(ops):
        parts = o["path"].split("/")[1:]
        field = parts[0]
        where = f"op {i}"
        if field not in FIELDS:
            raise PlansError(422, "BAD_PATCH", f"{where}: unknown field")
        needs_value = o["op"] != "remove"
        if needs_value and o.get("value") is None:
            raise PlansError(422, "BAD_PATCH", f"{where}: value required")
        if len(parts) == 1:
            if o["op"] != "replace":
                raise PlansError(422, "BAD_PATCH", f"{where}: a whole field can only be replaced")
            out[field] = o["value"]
            continue
        if field not in LIST_FIELDS or not isinstance(out[field], list):
            raise PlansError(422, "BAD_PATCH", f"{where}: {field} is not a list")
        lst, idx = out[field], parts[1]
        if idx == "-":
            if o["op"] != "add":
                raise PlansError(422, "BAD_PATCH", f"{where}: '-' only works with add")
            lst.append(o["value"])
            continue
        k = int(idx)
        if o["op"] == "add" and k <= len(lst):
            lst.insert(k, o["value"])
        elif o["op"] == "replace" and k < len(lst):
            lst[k] = o["value"]
        elif o["op"] == "remove" and k < len(lst):
            del lst[k]
        else:
            raise PlansError(422, "BAD_PATCH", f"{where}: index out of range")
    return out


def _validated(raw: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    try:
        return _finalize(PlanContent.model_validate(raw))
    except ValueError as e:  # pydantic ValidationError: say where, never echo the value
        errs = getattr(e, "errors", lambda: [])()
        where = ".".join(str(x) for x in errs[0]["loc"]) if errs else "content"
        raise PlansError(422, "BAD_PLAN", f"invalid plan content at {where}") from e


def _diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Structured diff of two plan contents: per field, scalar before/after or list opcodes."""
    fields = []
    for f in FIELDS:
        if f in LIST_FIELDS:
            sm = difflib.SequenceMatcher(None, a[f], b[f], autojunk=False)
            ops = [
                {
                    "op": tag,
                    "before": [{"index": i, "value": a[f][i]} for i in range(i1, i2)],
                    "after": [{"index": j, "value": b[f][j]} for j in range(j1, j2)],
                }
                for tag, i1, i2, j1, j2 in sm.get_opcodes()
                if tag != "equal"
            ]
            fields.append({"field": f, "kind": "list", "changed": bool(ops), "ops": ops})
        else:
            fields.append(
                {
                    "field": f,
                    "kind": "scalar",
                    "changed": a[f] != b[f],
                    "before": a[f],
                    "after": b[f],
                }
            )
    return {"fields": fields, "changedFields": [x["field"] for x in fields if x["changed"]]}


# ------------------------------------------------------------- legacy mirror, lock

VCOLS = (
    "id, run_id, version, content, content_sha256, author_kind, author_id, state,"
    " parent_version, source_plan_id, from_edit_id, approved_sha256, approved_at, created_at"
)


async def _sync_legacy(db: Any, p: Principal, run_id: str) -> None:
    """Mirror unmirrored ``agent_plans`` rows of the run as versions (oldest first) and follow
    the legacy status of mirrored ones. Idempotent; safe under concurrent callers (the unique
    index on ``source_plan_id`` decides). Does not commit; callers do."""
    legacy = (
        await db.execute(
            text(
                "SELECT id, goal, files_likely_to_change, steps, tests, risks,"
                " estimated_cost_usd, forbidden_paths, expected_result, status, created_at"
                " FROM agent_plans WHERE run_id = :r ORDER BY created_at, id"
            ),
            {"r": run_id},
        )
    ).all()
    if not legacy:
        return
    mirrored = {
        r.source_plan_id: r
        for r in (
            await db.execute(
                text(
                    "SELECT source_plan_id, version, state FROM plan_versions"
                    " WHERE run_id = :r AND source_plan_id IS NOT NULL"
                ),
                {"r": run_id},
            )
        ).all()
    }
    now = _now()
    for row in legacy:
        want = row.status if row.status in ("approved", "rejected") else "proposed"
        have = mirrored.get(row.id)
        if have is None:
            canon_content = _norm(
                {
                    "goal": row.goal,
                    "steps": _jl(row.steps, []),
                    "tests": _jl(row.tests, []),
                    "risks": _jl(row.risks, []),
                    "filesLikelyToChange": _jl(row.files_likely_to_change, []),
                    "forbiddenPaths": _jl(row.forbidden_paths, []),
                    "expectedResult": row.expected_result,
                    "estimatedCostUsd": row.estimated_cost_usd,
                }
            )
            canon = _canon(canon_content)
            digest = _digest(canon)
            nxt = (
                await db.execute(
                    text(
                        "SELECT COALESCE(MAX(version), 0) + 1 AS n FROM plan_versions"
                        " WHERE run_id = :r"
                    ),
                    {"r": run_id},
                )
            ).scalar()
            try:
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "INSERT INTO plan_versions (id, run_id, team_id, version, content,"
                            " content_sha256, author_kind, author_id, state, source_plan_id,"
                            " approved_sha256, approved_at, created_at) VALUES (:i, :r, :t, :v,"
                            " :c, :h, 'agent', 'planner', :s, :sp, :ah, :aa, :ts)"
                        ),
                        {
                            "i": str(uuid.uuid4()),
                            "r": run_id,
                            "t": p.teamId,
                            "v": nxt,
                            "c": canon,
                            "h": digest,
                            "s": want,
                            "sp": row.id,
                            "ah": digest if want == "approved" else None,
                            "aa": now if want == "approved" else None,
                            "ts": _dt(row.created_at) or now,
                        },
                    )
            except IntegrityError:  # a concurrent reader mirrored it first
                continue
            if want != "rejected":
                await db.execute(
                    text(
                        "UPDATE plan_versions SET state = 'superseded' WHERE run_id = :r"
                        " AND version < :v AND state IN ('draft', 'proposed')"
                    ),
                    {"r": run_id, "v": nxt},
                )
            continue
        if want == "approved" and have.state != "approved":
            await db.execute(
                text(
                    "UPDATE plan_versions SET state = 'approved',"
                    " approved_sha256 = content_sha256, approved_at = :now"
                    " WHERE run_id = :r AND source_plan_id = :sp"
                ),
                {"now": now, "r": run_id, "sp": row.id},
            )
        elif want == "rejected" and have.state in ("draft", "proposed", "approved"):
            await db.execute(
                text(
                    "UPDATE plan_versions SET state = 'rejected', approved_sha256 = NULL,"
                    " approved_at = NULL WHERE run_id = :r AND source_plan_id = :sp"
                ),
                {"r": run_id, "sp": row.id},
            )
    # An approved plan closes every other open version: only the approved content may run.
    await db.execute(
        text(
            "UPDATE plan_versions SET state = 'superseded' WHERE run_id = :r"
            " AND state IN ('draft', 'proposed') AND EXISTS (SELECT 1 FROM plan_versions a"
            " WHERE a.run_id = :r AND a.state = 'approved')"
        ),
        {"r": run_id},
    )


async def _locked(db: Any, run_id: str) -> int | None:
    """Version number of the approved version, or None while the plan is still open."""
    row = (
        await db.execute(
            text(
                "SELECT version FROM plan_versions WHERE run_id = :r AND state = 'approved'"
                " ORDER BY version DESC LIMIT 1"
            ),
            {"r": run_id},
        )
    ).first()
    return int(row.version) if row else None


async def _require_open(db: Any, run_id: str) -> None:
    v = await _locked(db, run_id)
    if v is not None:
        raise PlansError(
            409,
            "PLAN_LOCKED",
            "the plan is approved and can no longer change",
            {"approvedVersion": v},
        )


async def _latest(db: Any, run_id: str) -> int:
    return int(
        (
            await db.execute(
                text("SELECT COALESCE(MAX(version), 0) FROM plan_versions WHERE run_id = :r"),
                {"r": run_id},
            )
        ).scalar()
    )


async def _version(db: Any, p: Principal, run_id: str, n: int) -> Any:
    row = (
        await db.execute(
            text(
                f"SELECT {VCOLS} FROM plan_versions WHERE run_id = :r AND version = :v"
                " AND team_id = :t"
            ),
            {"r": run_id, "v": n, "t": p.teamId},
        )
    ).first()
    if row is None:
        raise PlansError(404, "VERSION_NOT_FOUND", "no such plan version")
    return row


def _version_view(
    row: Any,
    locked: int | None = None,
    p: Principal | None = None,
    latest: int | None = None,
    counts: Any = None,
    full: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "runId": row.run_id,
        "version": int(row.version),
        "state": row.state,
        "contentSha256": row.content_sha256,
        "author": {"kind": row.author_kind, "id": row.author_id},
        "parentVersion": row.parent_version,
        "fromEditId": row.from_edit_id,
        "source": "legacy" if row.source_plan_id else "native",
        "legacyPlanId": row.source_plan_id,
        "approvedSha256": row.approved_sha256,
        "approvedAt": _iso(row.approved_at),
        "createdAt": _iso(row.created_at),
    }
    if full:
        out["content"] = _jl(row.content, {})
    if counts is not None:
        out["commentCount"] = int(counts.comments)
        out["pendingEditCount"] = int(counts.pending)
    if p is not None:
        open_ = locked is None and row.state in OPEN_STATES and latest == int(row.version)
        out["can"] = {
            "comment": locked is None and p.role != Role.GUEST,
            "proposeEdit": open_ and p.role != Role.GUEST,
            "decideEdit": open_ and p.role in CAN_APPROVE_PLANS,
            "newVersion": locked is None and p.role not in READ_ONLY,
        }
    return out


# ------------------------------------------------------------------ task statement


class TaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=MAX_STATEMENT)
    successCriteria: list[Annotated[str, StringConstraints(min_length=1, max_length=MAX_ITEM)]] = (
        Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    )
    constraints: list[Annotated[str, StringConstraints(min_length=1, max_length=MAX_ITEM)]] = Field(
        default_factory=list, max_length=MAX_LIST_ITEMS
    )
    ownerUserId: str | None = Field(default=None, min_length=1, max_length=36)
    expectedVersion: int = Field(ge=0)


async def _name(db: Any, team_id: str, user_id: str) -> str:
    row = (
        await db.execute(
            text(
                "SELECT display_name FROM team_members WHERE team_id = :t AND user_id = :u LIMIT 1"
            ),
            {"t": team_id, "u": user_id},
        )
    ).first()
    return (row.display_name if row and row.display_name else None) or "A participant"


async def _task_view(db: Any, p: Principal, run_id: str, row: Any) -> dict[str, Any]:
    can = {"edit": p.role not in READ_ONLY}
    if row is None:
        return {
            "runId": run_id,
            "exists": False,
            "statement": "",
            "successCriteria": [],
            "constraints": [],
            "owner": None,
            "version": 0,
            "updatedBy": None,
            "updatedAt": None,
            "can": can,
        }
    owner = None
    if row.owner_user_id:
        owner = {"userId": row.owner_user_id, "name": await _name(db, p.teamId, row.owner_user_id)}
    return {
        "runId": run_id,
        "exists": True,
        "statement": row.statement,
        "successCriteria": _jl(row.success_criteria, []),
        "constraints": _jl(row.constraints, []),
        "owner": owner,
        "version": int(row.version),
        "updatedBy": row.updated_by,
        "updatedAt": _iso(row.updated_at),
        "can": can,
    }


class _Lost(Exception):
    """The optimistic update matched no row: someone else saved first."""


async def get_task(db: Any, p: Principal, run_id: str, version: int | None) -> dict[str, Any]:
    if version is None:
        row = (
            await db.execute(
                text("SELECT * FROM task_specs WHERE run_id = :r AND team_id = :t"),
                {"r": run_id, "t": p.teamId},
            )
        ).first()
    else:
        row = (
            await db.execute(
                text(
                    "SELECT * FROM task_spec_revisions WHERE run_id = :r AND team_id = :t"
                    " AND version = :v"
                ),
                {"r": run_id, "t": p.teamId, "v": version},
            )
        ).first()
        if row is None:
            raise PlansError(404, "VERSION_NOT_FOUND", "no such task version")
    return await _task_view(db, p, run_id, row)


async def put_task(db: Any, p: Principal, run_id: str, req: TaskBody) -> tuple[dict, bool]:
    if req.ownerUserId is not None:
        ok = (
            await db.execute(
                text(
                    "SELECT 1 FROM team_members WHERE team_id = :t AND user_id = :u"
                    " AND member_type = 'human'"
                ),
                {"t": p.teamId, "u": req.ownerUserId},
            )
        ).first()
        if ok is None:
            raise PlansError(422, "OWNER_NOT_IN_TEAM", "the owner must be a member of this team")
    cur = (
        await db.execute(
            text("SELECT * FROM task_specs WHERE run_id = :r AND team_id = :t"),
            {"r": run_id, "t": p.teamId},
        )
    ).first()
    seen = int(cur.version) if cur else 0
    if req.expectedVersion != seen:
        raise PlansError(
            409, "VERSION_CONFLICT", "the task changed since you read it", {"currentVersion": seen}
        )
    crit, cons = json.dumps(req.successCriteria), json.dumps(req.constraints)
    if cur is not None and (
        cur.statement,
        _jl(cur.success_criteria, []),
        _jl(cur.constraints, []),
        cur.owner_user_id,
    ) == (req.statement, req.successCriteria, req.constraints, req.ownerUserId):
        return await _task_view(db, p, run_id, cur), False  # nothing changed: no new version
    now, new = _now(), seen + 1
    changed = ["statement", "successCriteria", "constraints", "owner"]
    if cur is not None:
        changed = [
            k
            for k, a, b in (
                ("statement", cur.statement, req.statement),
                ("successCriteria", _jl(cur.success_criteria, []), req.successCriteria),
                ("constraints", _jl(cur.constraints, []), req.constraints),
                ("owner", cur.owner_user_id, req.ownerUserId),
            )
            if a != b
        ]
    try:
        if cur is None:
            await db.execute(
                text(
                    "INSERT INTO task_specs (run_id, team_id, statement, success_criteria,"
                    " constraints, owner_user_id, version, updated_by, updated_at) VALUES"
                    " (:r, :t, :s, :c, :k, :o, 1, :u, :now)"
                ),
                {
                    "r": run_id,
                    "t": p.teamId,
                    "s": req.statement,
                    "c": crit,
                    "k": cons,
                    "o": req.ownerUserId,
                    "u": p.userId,
                    "now": now,
                },
            )
        else:
            res = await db.execute(
                text(
                    "UPDATE task_specs SET statement = :s, success_criteria = :c,"
                    " constraints = :k, owner_user_id = :o, version = :new, updated_by = :u,"
                    " updated_at = :now WHERE run_id = :r AND team_id = :t AND version = :seen"
                ),
                {
                    "s": req.statement,
                    "c": crit,
                    "k": cons,
                    "o": req.ownerUserId,
                    "new": new,
                    "u": p.userId,
                    "now": now,
                    "r": run_id,
                    "t": p.teamId,
                    "seen": seen,
                },
            )
            if res.rowcount != 1:
                raise _Lost
        await db.execute(
            text(
                "INSERT INTO task_spec_revisions (run_id, version, team_id, statement,"
                " success_criteria, constraints, owner_user_id, updated_by, updated_at) VALUES"
                " (:r, :v, :t, :s, :c, :k, :o, :u, :now)"
            ),
            {
                "r": run_id,
                "v": new,
                "t": p.teamId,
                "s": req.statement,
                "c": crit,
                "k": cons,
                "o": req.ownerUserId,
                "u": p.userId,
                "now": now,
            },
        )
    except (IntegrityError, _Lost) as e:  # a simultaneous editor won
        await db.rollback()
        cur_v = (
            await db.execute(
                text("SELECT version FROM task_specs WHERE run_id = :r"), {"r": run_id}
            )
        ).scalar()
        raise PlansError(
            409,
            "VERSION_CONFLICT",
            "the task changed since you read it",
            {"currentVersion": int(cur_v or 0)},
        ) from e
    await _emit(db, p, run_id, "task.updated", {"version": new})
    await _audit(
        db,
        p,
        "task.updated",
        run_id,
        {
            "version": new,
            "changed": changed,
            "ownerUserId": req.ownerUserId,
            "before": {"version": seen},
            "after": {"version": new},
        },
    )
    await db.commit()
    row = (
        await db.execute(text("SELECT * FROM task_specs WHERE run_id = :r"), {"r": run_id})
    ).first()
    return await _task_view(db, p, run_id, row), True


# ---------------------------------------------------------------- plan versions


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: PlanContent
    expectedVersion: int = Field(ge=0)
    requestId: str | None = Field(default=None, min_length=1, max_length=64)


async def _create_version(
    db: Any,
    p: Principal,
    run_id: str,
    content: dict[str, Any],
    canon: str,
    digest: str,
    parent: int | None,
    request_id: str | None,
    from_edit: str | None = None,
) -> int:
    nxt = await _latest(db, run_id) + 1
    if nxt > MAX_VERSIONS:
        raise PlansError(409, "TOO_MANY_VERSIONS", f"a run keeps at most {MAX_VERSIONS} versions")
    await db.execute(
        text(
            "INSERT INTO plan_versions (id, run_id, team_id, version, content, content_sha256,"
            " author_kind, author_id, state, parent_version, from_edit_id, request_id,"
            " created_at) VALUES (:i, :r, :t, :v, :c, :h, 'user', :u, 'proposed', :pa, :fe,"
            " :q, :now)"
        ),
        {
            "i": str(uuid.uuid4()),
            "r": run_id,
            "t": p.teamId,
            "v": nxt,
            "c": canon,
            "h": digest,
            "u": p.userId,
            "pa": parent,
            "fe": from_edit,
            "q": request_id,
            "now": _now(),
        },
    )
    await db.execute(
        text(
            "UPDATE plan_versions SET state = 'superseded' WHERE run_id = :r AND version < :v"
            " AND state IN ('draft', 'proposed')"
        ),
        {"r": run_id, "v": nxt},
    )
    await _emit(db, p, run_id, "plan.version_created", {"version": nxt})
    await _audit(
        db,
        p,
        "plan.version_created",
        run_id,
        {
            "version": nxt,
            "parentVersion": parent,
            "contentSha256": digest,
            "fromEditId": from_edit,
            "after": {"state": "proposed"},
        },
    )
    return nxt


async def create_version(db: Any, p: Principal, run_id: str, req: VersionBody) -> tuple[dict, bool]:
    await _sync_legacy(db, p, run_id)

    async def existing() -> Any:
        if not req.requestId:
            return None
        return (
            await db.execute(
                text(
                    f"SELECT {VCOLS} FROM plan_versions WHERE run_id = :r AND author_id = :u"
                    " AND request_id = :q"
                ),
                {"r": run_id, "u": p.userId, "q": req.requestId},
            )
        ).first()

    if (dup := await existing()) is not None:
        await db.commit()
        return _version_view(dup), False
    await _require_open(db, run_id)
    latest = await _latest(db, run_id)
    if req.expectedVersion != latest:
        raise PlansError(
            409, "VERSION_CONFLICT", "a newer plan version exists", {"currentVersion": latest}
        )
    content, canon, digest = _finalize(req.content)
    try:
        n = await _create_version(
            db, p, run_id, content, canon, digest, latest or None, req.requestId
        )
        await db.commit()
    except IntegrityError as e:  # a simultaneous writer took this number or this request id
        await db.rollback()
        if (dup := await existing()) is not None:
            return _version_view(dup), False
        raise PlansError(
            409,
            "VERSION_CONFLICT",
            "a newer plan version exists",
            {"currentVersion": await _latest(db, run_id)},
        ) from e
    return _version_view(await _version(db, p, run_id, n)), True


async def list_versions(db: Any, p: Principal, run_id: str) -> dict[str, Any]:
    await _sync_legacy(db, p, run_id)
    await db.commit()
    rows = (
        await db.execute(
            text(
                "SELECT v.id, v.run_id, v.version, v.content, v.content_sha256, v.author_kind,"
                " v.author_id, v.state, v.parent_version, v.source_plan_id, v.from_edit_id,"
                " v.approved_sha256, v.approved_at, v.created_at,"
                " (SELECT COUNT(*) FROM plan_comments c WHERE c.run_id = v.run_id"
                "  AND c.plan_version = v.version) AS comments,"
                " (SELECT COUNT(*) FROM plan_edits e WHERE e.run_id = v.run_id"
                "  AND e.base_version = v.version AND e.state = 'pending') AS pending"
                " FROM plan_versions v WHERE v.run_id = :r AND v.team_id = :t ORDER BY v.version"
            ),
            {"r": run_id, "t": p.teamId},
        )
    ).all()
    locked = await _locked(db, run_id)
    latest = max((int(r.version) for r in rows), default=0)
    return {
        "runId": run_id,
        "latestVersion": latest,
        "locked": locked is not None,
        "approvedVersion": locked,
        "versions": [_version_view(r, locked, p, latest, r, full=False) for r in rows],
    }


async def get_version(db: Any, p: Principal, run_id: str, n: int) -> dict[str, Any]:
    await _sync_legacy(db, p, run_id)
    await db.commit()
    row = await _version(db, p, run_id, n)
    counts = (
        await db.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM plan_comments WHERE run_id = :r "
                "AND plan_version = :v)"
                " AS comments, (SELECT COUNT(*) FROM plan_edits WHERE run_id = :r"
                " AND base_version = :v AND state = 'pending') AS pending"
            ),
            {"r": run_id, "v": n},
        )
    ).first()
    return _version_view(row, await _locked(db, run_id), p, await _latest(db, run_id), counts)


async def version_diff(
    db: Any, p: Principal, run_id: str, n: int, against: int | None
) -> dict[str, Any]:
    await _sync_legacy(db, p, run_id)
    await db.commit()
    b = await _version(db, p, run_id, n)
    if against is None:
        if n == 1:
            raise PlansError(422, "NO_BASE", "version 1 has no previous version; pass against")
        against = n - 1
    a = await _version(db, p, run_id, against)
    out = _diff(_norm(_jl(a.content, {})), _norm(_jl(b.content, {})))
    return {
        "runId": run_id,
        "from": {"version": int(a.version), "sha256": a.content_sha256},
        "to": {"version": int(b.version), "sha256": b.content_sha256},
        "identical": a.content_sha256 == b.content_sha256,
        **out,
    }


# ------------------------------------------------------------------- comments


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=MAX_BODY)
    fieldPath: str | None = Field(
        default=None, pattern=r"^[A-Za-z]{1,32}(/\d{1,4})?$", max_length=64
    )
    requestId: str | None = Field(default=None, min_length=1, max_length=64)


def _comment_view(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "runId": r.run_id,
        "planVersion": int(r.plan_version),
        "author": {"userId": r.author_user_id, "name": r.author_name_snapshot},
        "body": r.body,
        "fieldPath": r.field_path,
        "createdAt": _iso(r.created_at),
    }


_CCOLS = (
    "id, run_id, plan_version, author_user_id, author_name_snapshot, field_path, body, created_at"
)


def _check_field_path(path: str | None) -> None:
    if path is not None and path.split("/")[0] not in FIELDS:
        raise PlansError(422, "BAD_FIELD_PATH", "fieldPath must name a plan field")


async def add_comment(
    db: Any, p: Principal, run_id: str, n: int, req: CommentBody
) -> tuple[dict, bool]:
    await _sync_legacy(db, p, run_id)
    await _version(db, p, run_id, n)

    async def existing() -> Any:
        if not req.requestId:
            return None
        return (
            await db.execute(
                text(
                    f"SELECT {_CCOLS} FROM plan_comments WHERE run_id = :r AND author_user_id = :u"
                    " AND request_id = :q"
                ),
                {"r": run_id, "u": p.userId, "q": req.requestId},
            )
        ).first()

    if (dup := await existing()) is not None:
        await db.commit()
        return _comment_view(dup), False
    await _require_open(db, run_id)
    _check_field_path(req.fieldPath)
    total = (
        await db.execute(
            text("SELECT COUNT(*) FROM plan_comments WHERE run_id = :r AND plan_version = :v"),
            {"r": run_id, "v": n},
        )
    ).scalar()
    if int(total) >= MAX_COMMENTS:
        raise PlansError(409, "TOO_MANY_COMMENTS", f"at most {MAX_COMMENTS} comments per version")
    cid = str(uuid.uuid4())
    try:
        await db.execute(
            text(
                "INSERT INTO plan_comments (id, run_id, team_id, plan_version, author_user_id,"
                " author_name_snapshot, field_path, body, request_id, created_at) VALUES"
                " (:i, :r, :t, :v, :u, :n, :f, :b, :q, :now)"
            ),
            {
                "i": cid,
                "r": run_id,
                "t": p.teamId,
                "v": n,
                "u": p.userId,
                "n": await _name(db, p.teamId, p.userId),
                "f": req.fieldPath,
                "b": req.body,
                "q": req.requestId,
                "now": _now(),
            },
        )
    except IntegrityError:  # a simultaneous retry with the same requestId
        await db.rollback()
        if (dup := await existing()) is not None:
            return _comment_view(dup), False
        raise
    await _emit(db, p, run_id, "plan.comment_added", {"commentId": cid, "planVersion": n})
    await _audit(
        db,
        p,
        "plan.comment_added",
        run_id,
        {"commentId": cid, "planVersion": n, "fieldPath": req.fieldPath},
    )
    await db.commit()
    row = (
        await db.execute(text(f"SELECT {_CCOLS} FROM plan_comments WHERE id = :i"), {"i": cid})
    ).first()
    return _comment_view(row), True


async def list_comments(
    db: Any, p: Principal, run_id: str, n: int, limit: int, after: str | None
) -> dict[str, Any]:
    await _sync_legacy(db, p, run_id)
    await db.commit()
    await _version(db, p, run_id, n)
    sql = (
        f"SELECT {_CCOLS} FROM plan_comments WHERE run_id = :r AND team_id = :t"
        " AND plan_version = :v"
    )
    args: dict[str, Any] = {"r": run_id, "t": p.teamId, "v": n, "lim": limit + 1}
    if after:
        cur = (
            await db.execute(
                text(
                    "SELECT created_at, id FROM plan_comments WHERE id = :i AND run_id = :r"
                    " AND plan_version = :v AND team_id = :t"
                ),
                {"i": after, "r": run_id, "v": n, "t": p.teamId},
            )
        ).first()
        if cur is None:
            raise PlansError(422, "BAD_CURSOR", "unknown cursor")
        sql += " AND (created_at > :ca OR (created_at = :ca AND id > :ci))"
        args.update(ca=cur.created_at, ci=cur.id)
    rows = (await db.execute(text(sql + " ORDER BY created_at, id LIMIT :lim"), args)).all()
    page = rows[:limit]
    return {
        "runId": run_id,
        "planVersion": n,
        "comments": [_comment_view(r) for r in page],
        "nextCursor": page[-1].id if len(rows) > limit else None,
    }


# ---------------------------------------------------------------------- edits


class ProposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: list[PatchOp] = Field(min_length=1, max_length=MAX_PATCH_OPS)
    rationale: str | None = Field(default=None, max_length=1000)
    requestId: str | None = Field(default=None, min_length=1, max_length=64)


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject"]
    note: str | None = Field(default=None, max_length=500)


_ECOLS = (
    "id, run_id, base_version, proposed_by, patch, rationale, state, decided_by, decided_at,"
    " decision_note, resulting_version, created_at"
)


def _edit_view(r: Any, latest: int, locked: int | None) -> dict[str, Any]:
    return {
        "id": r.id,
        "runId": r.run_id,
        "baseVersion": int(r.base_version),
        "proposedBy": r.proposed_by,
        "patch": _jl(r.patch, []),
        "rationale": r.rationale,
        "state": r.state,
        "createdAt": _iso(r.created_at),
        "decision": (
            {
                "by": r.decided_by,
                "at": _iso(r.decided_at),
                "note": r.decision_note,
                "resultingVersion": r.resulting_version,
            }
            if r.decided_by
            else None
        ),
        "stale": r.state == "pending" and (locked is not None or int(r.base_version) != latest),
    }


async def _get_edit(db: Any, p: Principal, run_id: str, n: int, edit_id: str) -> Any:
    row = (
        await db.execute(
            text(
                f"SELECT {_ECOLS} FROM plan_edits WHERE id = :i AND run_id = :r AND team_id = :t"
                " AND base_version = :v"
            ),
            {"i": edit_id, "r": run_id, "t": p.teamId, "v": n},
        )
    ).first()
    if row is None:
        raise PlansError(404, "EDIT_NOT_FOUND", "no such proposed edit")
    return row


async def propose_edit(
    db: Any, p: Principal, run_id: str, n: int, req: ProposeBody
) -> tuple[dict, bool]:
    await _sync_legacy(db, p, run_id)
    base = await _version(db, p, run_id, n)
    latest = await _latest(db, run_id)

    async def existing() -> Any:
        if not req.requestId:
            return None
        return (
            await db.execute(
                text(
                    f"SELECT {_ECOLS} FROM plan_edits WHERE run_id = :r AND proposed_by = :u"
                    " AND request_id = :q"
                ),
                {"r": run_id, "u": p.userId, "q": req.requestId},
            )
        ).first()

    if (dup := await existing()) is not None:
        await db.commit()
        return _edit_view(dup, latest, await _locked(db, run_id)), False
    await _require_open(db, run_id)
    if base.state not in OPEN_STATES or n != latest:
        raise PlansError(
            409,
            "VERSION_NOT_OPEN",
            "only the latest open version takes edits",
            {"currentVersion": latest},
        )
    pending = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM plan_edits WHERE run_id = :r AND base_version = :v"
                " AND state = 'pending'"
            ),
            {"r": run_id, "v": n},
        )
    ).scalar()
    if int(pending) >= MAX_PENDING_EDITS:
        raise PlansError(409, "TOO_MANY_EDITS", f"at most {MAX_PENDING_EDITS} pending edits")
    ops = [{**o.model_dump(), "value": None if o.op == "remove" else o.value} for o in req.patch]
    if len(json.dumps(ops)) > MAX_CONTENT_BYTES:
        raise PlansError(422, "PATCH_TOO_LARGE", f"a patch is at most {MAX_CONTENT_BYTES} bytes")
    base_content = _norm(_jl(base.content, {}))
    _, _, new_digest = _validated(_apply_patch(base_content, ops))
    if new_digest == base.content_sha256:
        raise PlansError(422, "NO_CHANGE", "the patch does not change the plan")
    eid = str(uuid.uuid4())
    try:
        await db.execute(
            text(
                "INSERT INTO plan_edits (id, run_id, team_id, base_version, proposed_by, patch,"
                " rationale, state, request_id, created_at) VALUES (:i, :r, :t, :v, :u, :pa,"
                " :ra, 'pending', :q, :now)"
            ),
            {
                "i": eid,
                "r": run_id,
                "t": p.teamId,
                "v": n,
                "u": p.userId,
                "pa": json.dumps(ops),
                "ra": req.rationale,
                "q": req.requestId,
                "now": _now(),
            },
        )
    except IntegrityError:
        await db.rollback()
        if (dup := await existing()) is not None:
            return _edit_view(dup, latest, None), False
        raise
    await _emit(db, p, run_id, "plan.edit_proposed", {"editId": eid, "baseVersion": n})
    await _audit(
        db,
        p,
        "plan.edit_proposed",
        run_id,
        {"editId": eid, "baseVersion": n, "opCount": len(ops), "after": {"state": "pending"}},
    )
    await db.commit()
    row = await _get_edit(db, p, run_id, n, eid)
    return _edit_view(row, latest, None), True


async def list_edits(db: Any, p: Principal, run_id: str, n: int) -> dict[str, Any]:
    await _sync_legacy(db, p, run_id)
    await db.commit()
    await _version(db, p, run_id, n)
    rows = (
        await db.execute(
            text(
                f"SELECT {_ECOLS} FROM plan_edits WHERE run_id = :r AND team_id = :t"
                " AND base_version = :v ORDER BY created_at, id"
            ),
            {"r": run_id, "t": p.teamId, "v": n},
        )
    ).all()
    latest, locked = await _latest(db, run_id), await _locked(db, run_id)
    return {
        "runId": run_id,
        "planVersion": n,
        "edits": [_edit_view(r, latest, locked) for r in rows],
    }


def _self_approval_allowed() -> bool:
    return os.environ.get("HARNESS_ALLOW_SELF_APPROVAL", "") == "1"


async def decide_edit(
    db: Any, p: Principal, run_id: str, n: int, edit_id: str, req: DecideBody
) -> tuple[dict, bool]:
    await _sync_legacy(db, p, run_id)
    edit = await _get_edit(db, p, run_id, n, edit_id)
    new_state = "accepted" if req.decision == "accept" else "rejected"
    if edit.state != "pending":
        await db.commit()
        if edit.state == new_state and edit.decided_by == p.userId:
            view = _edit_view(edit, await _latest(db, run_id), await _locked(db, run_id))
            return view, False  # an idempotent retry of the same decision
        raise PlansError(409, "EDIT_ALREADY_DECIDED", f"the edit was already {edit.state}")
    await _require_open(db, run_id)
    if (
        new_state == "accepted" and edit.proposed_by == p.userId and not _self_approval_allowed()
    ):  # withdrawing your own proposal stays allowed
        raise PlansError(403, "SELF_APPROVAL", "you cannot accept your own proposed edit")
    latest = await _latest(db, run_id)
    resulting: int | None = None
    prepared = None
    if new_state == "accepted":
        base = await _version(db, p, run_id, n)
        if base.state not in OPEN_STATES or n != latest:
            raise PlansError(
                409,
                "STALE_EDIT",
                "the plan moved on since this edit was proposed",
                {"currentVersion": latest},
            )
        prepared = _validated(_apply_patch(_norm(_jl(base.content, {})), _jl(edit.patch, [])))
    now = _now()
    res = await db.execute(  # the compare-and-set that makes two simultaneous deciders one
        text(
            "UPDATE plan_edits SET state = :s, decided_by = :u, decided_at = :now,"
            " decision_note = :note WHERE id = :i AND run_id = :r AND state = 'pending'"
        ),
        {"s": new_state, "u": p.userId, "now": now, "note": req.note, "i": edit_id, "r": run_id},
    )
    if res.rowcount != 1:
        await db.rollback()
        raise PlansError(409, "EDIT_ALREADY_DECIDED", "the edit was just decided")
    if prepared is not None:
        content, canon, digest = prepared
        try:
            resulting = await _create_version(
                db, p, run_id, content, canon, digest, n, None, from_edit=edit_id
            )
        except IntegrityError as e:
            await db.rollback()
            raise PlansError(
                409,
                "STALE_EDIT",
                "the plan moved on since this edit was proposed",
                {"currentVersion": await _latest(db, run_id)},
            ) from e
        await db.execute(
            text("UPDATE plan_edits SET resulting_version = :v WHERE id = :i"),
            {"v": resulting, "i": edit_id},
        )
    await _emit(db, p, run_id, "plan.edit_decided", {"editId": edit_id, "decision": new_state})
    await _audit(
        db,
        p,
        "plan.edit_decided",
        run_id,
        {
            "editId": edit_id,
            "baseVersion": n,
            "resultingVersion": resulting,
            "proposedBy": edit.proposed_by,
            "before": {"state": "pending"},
            "after": {"state": new_state},
        },
    )
    await db.commit()
    row = await _get_edit(db, p, run_id, n, edit_id)
    return _edit_view(row, await _latest(db, run_id), await _locked(db, run_id)), True


# ---------------------------------------------------------------------- routes

Read = Annotated[Principal, Depends(_guard("read"))]
Comment = Annotated[Principal, Depends(_guard("comment"))]
Steer = Annotated[Principal, Depends(_guard("steer"))]
Approve = Annotated[Principal, Depends(_guard("approve"))]
_V = "/runs/{runId}/plan/versions"


def _reply(response: Response, created: bool) -> None:
    if not created:
        response.status_code = 200  # an idempotent replay or a no-op


@router.get("/runs/{runId}/task", name="plans_task_get")
async def task_get(
    runId: str, actor: Read, version: Annotated[int | None, Query(ge=1)] = None
) -> dict:
    return await _call(lambda db: get_task(db, actor, runId, version))


@router.put("/runs/{runId}/task", name="plans_task_put")
async def task_put(runId: str, req: TaskBody, actor: Steer) -> dict:
    out, _ = await _call(lambda db: put_task(db, actor, runId, req))
    return out


@router.get(_V, name="plans_versions_list")
async def versions_list(runId: str, actor: Read) -> dict:
    return await _call(lambda db: list_versions(db, actor, runId))


@router.post(_V, status_code=201, name="plans_versions_create")
async def versions_create(runId: str, req: VersionBody, actor: Steer, response: Response) -> dict:
    out, created = await _call(lambda db: create_version(db, actor, runId, req))
    _reply(response, created)
    return out


@router.get(_V + "/{n}", name="plans_version_get")
async def version_get(runId: str, n: int, actor: Read) -> dict:
    return await _call(lambda db: get_version(db, actor, runId, n))


@router.get(_V + "/{n}/diff", name="plans_version_diff")
async def version_diff_(
    runId: str, n: int, actor: Read, against: Annotated[int | None, Query(ge=1)] = None
) -> dict:
    return await _call(lambda db: version_diff(db, actor, runId, n, against))


@router.get(_V + "/{n}/comments", name="plans_comments_list")
async def comments_list(
    runId: str,
    n: int,
    actor: Read,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after: Annotated[str | None, Query(max_length=64)] = None,
) -> dict:
    return await _call(lambda db: list_comments(db, actor, runId, n, limit, after))


@router.post(_V + "/{n}/comments", status_code=201, name="plans_comments_create")
async def comments_create(
    runId: str, n: int, req: CommentBody, actor: Comment, response: Response
) -> dict:
    out, created = await _call(lambda db: add_comment(db, actor, runId, n, req))
    _reply(response, created)
    return out


@router.get(_V + "/{n}/edits", name="plans_edits_list")
async def edits_list(runId: str, n: int, actor: Read) -> dict:
    return await _call(lambda db: list_edits(db, actor, runId, n))


@router.post(_V + "/{n}/edits", status_code=201, name="plans_edits_propose")
async def edits_propose(
    runId: str, n: int, req: ProposeBody, actor: Comment, response: Response
) -> dict:
    out, created = await _call(lambda db: propose_edit(db, actor, runId, n, req))
    _reply(response, created)
    return out


@router.post(_V + "/{n}/edits/{editId}/decide", name="plans_edits_decide")
async def edits_decide(
    runId: str, n: int, editId: str, req: DecideBody, actor: Approve, response: Response
) -> dict:
    out, created = await _call(lambda db: decide_edit(db, actor, runId, n, editId, req))
    _reply(response, created)
    return out
