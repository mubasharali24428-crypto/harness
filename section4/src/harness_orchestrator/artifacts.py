"""Run artifacts and version control (part 48). Behaviour and limits: docs/artifacts.md.

Everything is scoped to one run and so to one team: the shared run ownership dependency
(``api._authz_dep``) answers a foreign or unknown run with the same 404 envelope, and every
artifact / change query also filters on ``run_id`` and ``team_id``, so a foreign or mistyped
id is the same 404 (no existence oracle). Identity is always the authenticated Principal.

* Artifacts: raw-body upload, content-addressed store (``artifact_store``), server-derived
  media type, ``Content-Disposition: attachment`` + ``nosniff`` on content, JSON-only text preview,
  quarantine of executable / archive / script / active content (admin-only download).
* Changes and diff: the run's done-signal diff is parsed into per-file changes bound to the
  plan version and a review state. Review binds the patch digest, so changed content starts again.
* Git state: what the caller records about repo, branches and shas. Stale base is computed.
* Merge approval: a human decision recorded SEPARATELY from plan approval.

Raw SQL only (``text()``): sections may import only ``harness_contracts``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import unicodedata
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from harness_contracts import policy_enforce
from harness_contracts.policy_engine import ROLE_DEFAULTS
from harness_contracts.roles import Principal, Role
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api as _api
from harness_orchestrator import artifact_store, collab

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["artifacts"])

_service: Any = None

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_RUN_BYTES = 100 * 1024 * 1024
DEFAULT_RUN_COUNT = 200
DEFAULT_RETENTION_DAYS = 90
PREVIEW_SOURCE_MAX = 1024 * 1024  # bigger text is download-only
PREVIEW_CHARS = 64 * 1024
DIFF_FILE_LINES = 2000  # a file with more patch lines is "large": stats only
DIFF_FILE_CHARS = 100_000
DIFF_LINE_CHARS = 2000
DIFF_MINIFIED_LINE = 1000
DIFF_RESPONSE_CHARS = 200_000
MAX_FILES = 500
SYSTEM_ACTOR = "system:artifact-retention"
KINDS = ("upload", "output", "diff", "log")
SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
REQ_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_MISSING_SCHEMA = (
    "no such table",
    "no such column",
    "does not exist",
    "undefinedtable",
    "undefinedcolumn",
)
_GENERATED_PATH = re.compile(
    r"(^|/)(node_modules|dist|build|vendor|__pycache__|\.next|coverage)/|\.min\.|\.map$|"
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Cargo\.lock|"
    r"Gemfile\.lock|composer\.lock|go\.sum)$|\.pb\.go$|_pb2(_grpc)?\.py$|\.generated\.|"
    r"\.snap$|\.lock$"
)
_GENERATED_MARK = re.compile(r"@generated|do not edit|code generated|auto-?generated", re.I)
_BIDI = re.compile("[​-‏‪-‮⁦-⁩﻿]")
_CTRL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")

# (principal, run id, "METHOD route") of the request in flight, for denial audit.
_ctx: ContextVar[tuple[Principal, str, str] | None] = ContextVar("_artifacts_ctx", default=None)


def configure_artifacts(service: Any) -> None:
    """Deploy-time wiring (like configure_discussion): the service owning the DB factory."""
    global _service
    _service = service


def _svc() -> Any:
    return _service if _service is not None else _api._service


def _factory() -> Any:
    return getattr(_svc(), "session_factory", None)


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


def _json(v: Any, default: Any = None) -> Any:
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


class ArError(collab.CollabError):
    """CollabError that also carries structured data for the HTTP body."""

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(status, code, message)
        self.extra = extra or {}


def _not_found(what: str) -> ArError:
    return ArError(404, "NOT_FOUND", f"{what} not found")


async def _handled(coro: Any) -> Any:
    """Run a handler body; CollabError -> the stable error envelope (403s leave a denial row)."""
    try:
        return await coro
    except collab.CollabError as e:
        ctx = _ctx.get()
        if e.status == 403 and not e.audited and ctx is not None:
            await collab.audit_denial(_factory(), ctx[0], ctx[1], ctx[2], e.code, e.status)
        body = {"error": {"code": e.code, "message": str(e)}, **getattr(e, "extra", {})}
        raise HTTPException(e.status, detail=body) from e


def _need_factory() -> Any:
    factory = _factory()
    if factory is None:
        raise ArError(503, "ARTIFACTS_UNAVAILABLE", "no database wired")
    return factory


# ----------------------------------------------------------------------- guards


def _guard(dep: Any, write: bool) -> Any:
    async def g(request: Request, runId: str, p: _api.RequirePrincipal) -> Principal:
        route = f"{request.method} {getattr(request.scope.get('route'), 'path', request.url.path)}"
        _ctx.set((p, runId, route))
        await dep(request, runId, p)  # identity + role + team ownership (foreign run: 404)
        _api._pilot_gate(p)
        if write:
            await _api._persist_run(runId)
        return p

    return g


Reader = Annotated[Principal, Depends(_guard(_api._authz_dep("read", audit=True), False))]
Writer = Annotated[Principal, Depends(_guard(_api._authz_dep("write", audit=True), True))]
Approver = Annotated[Principal, Depends(_guard(_api._authz_dep("approve", audit=True), True))]


# ----------------------------------------------------------------- shared helpers


async def _optional(db: Any, sql: str, params: dict[str, Any]) -> list[Any] | None:
    """Read a table another lane owns. None when the table is not there yet (not an error)."""
    try:
        async with db.begin_nested():
            return list((await db.execute(text(sql), params)).all())
    except Exception as e:  # noqa: BLE001 - only a missing schema is tolerated
        if not any(k in str(e).lower() for k in _MISSING_SCHEMA):
            raise
        return None


async def _audit(
    db: Any, p: Principal, action: str, target_type: str, target_id: str, detail: dict[str, Any]
) -> None:
    """Append an audit row in the caller's transaction. Ids, states and digests only: never
    file content, names are not included either."""
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
            "tt": target_type,
            "tid": target_id,
            "d": json.dumps(
                {"userId": p.userId, "teamId": p.teamId, **collab._scrub(detail)}, default=str
            ),
            "ts": _now(),
        },
    )


async def _audit_alone(
    p: Principal, action: str, target_type: str, target_id: str, detail: dict[str, Any]
) -> None:
    """Own session + commit (used where the refusing transaction is abandoned)."""
    factory = _factory()
    if factory is None:
        return
    try:
        async with factory() as db:
            await _audit(db, p, action, target_type, target_id, detail)
            await db.commit()
    except Exception:  # noqa: BLE001 - audit must never mask the refusal
        logger.exception("artifact audit failed action=%s", action)


async def _holds(
    db: Any,
    p: Principal,
    perm: str,
    run_id: str,
    user_role: str | None = None,
    user_id: str | None = None,
) -> tuple[bool, str]:
    """Does ``p`` (or the roster member ``user_id`` with ``user_role``) hold ``perm`` on this run?

    Asks the org policy engine (the same ``decide_request`` the gate middleware uses, with the
    run in the chain) when the team belongs to an org; teams without policy tables or an org use
    the role defaults. An unexpected failure denies."""
    subject = p
    if user_id is not None:
        try:
            subject = Principal(userId=user_id, teamId=p.teamId, role=Role(user_role or ""))
        except ValueError:
            return False, "unknown role"
    try:
        async with db.begin_nested():
            org = await policy_enforce.team_org(db, p.teamId)
            if org is not None:
                chain = policy_enforce.chain_for(
                    org, p.teamId, f"/api/v1/orchestrator/runs/{run_id}"
                )
                d = await policy_enforce.decide_request(db, org, subject, perm, chain, _now())
                return bool(d.allowed), str(d.reason)[:200]
    except Exception as e:  # noqa: BLE001
        if not any(k in str(e).lower() for k in _MISSING_SCHEMA):
            logger.exception("policy evaluation failed; denying")
            return False, "policy could not be evaluated"
    return perm in ROLE_DEFAULTS.get(str(subject.role.value), frozenset()), "role default"


def _clean_text(s: str) -> str:
    return _BIDI.sub("�", _CTRL.sub("�", s))


def _no_store(headers: dict[str, str] | None = None) -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        **(headers or {}),
    }


# ------------------------------------------------------------------------ artifacts

_ACOLS = (
    "id, team_id, run_id, name, media_type, size_bytes, sha256, storage_ref, kind, state,"
    " scan, created_by, created_at, retention_until, deleted_at, deleted_by, request_id"
)


def _eff_state(r: Any, now: datetime) -> str:
    if r.state != "deleted" and r.retention_until is not None and _dt(r.retention_until) <= now:
        return "expired"
    return r.state


def _art(r: Any, now: datetime) -> dict[str, Any]:
    scan = _json(r.scan, {})
    state = _eff_state(r, now)
    small = int(r.size_bytes) <= PREVIEW_SOURCE_MAX
    return {
        "id": r.id,
        "runId": r.run_id,
        "name": r.name,
        "mediaType": r.media_type,
        "sizeBytes": int(r.size_bytes),
        "sha256": r.sha256,
        "kind": r.kind,
        "state": state,
        "scan": {
            "verdict": scan.get("verdict"),
            "reasons": scan.get("reasons", []),
            "sniffed": scan.get("sniffed"),
        },
        "previewable": state == "available" and bool(scan.get("textual")) and small,
        "download": {"available": "allowed", "quarantined": "administer_only"}.get(
            state, "unavailable"
        ),
        "createdBy": r.created_by,
        "createdAt": _iso(r.created_at),
        "retentionUntil": _iso(r.retention_until),
    }


async def _find(db: Any, p: Principal, run_id: str, artifact_id: str) -> Any:
    row = (
        await db.execute(
            text(f"SELECT {_ACOLS} FROM artifacts WHERE id = :i AND run_id = :r AND team_id = :t"),
            {"i": artifact_id[:64], "r": run_id, "t": p.teamId},
        )
    ).first()
    if row is None:
        raise _not_found("artifact")
    return row


def _disposition(name: str) -> str:
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100] or "artifact"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


async def _read_body(request: Request, cap: int) -> bytes:
    cl = request.headers.get("content-length", "")
    if cl.isdigit() and int(cl) > cap:
        raise ArError(413, "ARTIFACT_TOO_LARGE", f"artifacts are limited to {cap} bytes")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > cap:
            raise ArError(413, "ARTIFACT_TOO_LARGE", f"artifacts are limited to {cap} bytes")
    return bytes(buf)


async def create_artifact(
    factory: Any,
    p: Principal,
    run_id: str,
    *,
    name: str,
    kind: str,
    data: bytes,
    declared: str = "",
    request_id: str | None = None,
    retention_days: int | None = None,
    derived_from: str | None = None,
    change_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Store and record one artifact. Returns (artifact, replayed)."""
    sha = hashlib.sha256(data).hexdigest()
    now = _now()

    async def replay(db: Any) -> dict[str, Any] | None:
        if not request_id:
            return None
        row = (
            await db.execute(
                text(
                    f"SELECT {_ACOLS} FROM artifacts WHERE run_id = :r AND request_id = :q"
                    " AND team_id = :t"
                ),
                {"r": run_id, "q": request_id, "t": p.teamId},
            )
        ).first()
        if row is None:
            return None
        if row.sha256 != sha or row.created_by != p.userId:
            raise ArError(409, "REQUEST_ID_REUSED", "requestId was used for different content")
        return _art(row, now)

    async with factory() as db:
        done = await replay(db)
        if done is not None:
            return done, True
        if derived_from:
            ok = (
                await db.execute(
                    text(
                        "SELECT 1 FROM artifacts WHERE id = :i AND run_id = :r AND team_id = :t"
                        " AND state != 'deleted'"
                    ),
                    {"i": derived_from[:64], "r": run_id, "t": p.teamId},
                )
            ).first()
            if ok is None:
                raise ArError(422, "BAD_REFERENCE", "derivedFrom is not an artifact of this run")
        if change_id:
            ok = (
                await db.execute(
                    text(
                        "SELECT 1 FROM run_changes WHERE id = :i AND run_id = :r AND team_id = :t"
                    ),
                    {"i": change_id[:64], "r": run_id, "t": p.teamId},
                )
            ).first()
            if ok is None:
                raise ArError(422, "BAD_REFERENCE", "changeId is not a change of this run")
        used = (
            await db.execute(
                text(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b FROM artifacts"
                    " WHERE run_id = :r AND team_id = :t AND state != 'deleted'"
                ),
                {"r": run_id, "t": p.teamId},
            )
        ).first()
        if int(used.n) >= _env_int("HARNESS_ARTIFACT_RUN_MAX_COUNT", DEFAULT_RUN_COUNT) or int(
            used.b
        ) + len(data) > _env_int("HARNESS_ARTIFACT_RUN_MAX_BYTES", DEFAULT_RUN_BYTES):
            raise ArError(409, "ARTIFACT_QUOTA_EXCEEDED", "this run has reached its artifact quota")
        scan = artifact_store.classify(name, data, declared)
        quarantined = scan["verdict"] == "quarantined"
        ref = artifact_store.storage_ref(p.teamId, sha)
        await asyncio.to_thread(artifact_store.put, ref, data)
        aid = str(uuid.uuid4())
        days = retention_days or _env_int("HARNESS_ARTIFACT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)
        try:
            await db.execute(
                text(
                    "INSERT INTO artifacts (id, team_id, run_id, name, media_type, size_bytes,"
                    " sha256, storage_ref, kind, state, scan, request_id, created_by, created_at,"
                    " retention_until) VALUES (:id, :t, :r, :n, :mt, :sz, :sha, :ref, :k, :st,"
                    " :scan, :q, :u, :now, :ret)"
                ),
                {
                    "id": aid,
                    "t": p.teamId,
                    "r": run_id,
                    "n": name,
                    "mt": (
                        "application/octet-stream"
                        if quarantined
                        else artifact_store.serve_type(name, scan)
                    ),
                    "sz": len(data),
                    "sha": sha,
                    "ref": ref,
                    "k": kind,
                    "st": "quarantined" if quarantined else "available",
                    "scan": json.dumps(scan),
                    "q": request_id,
                    "u": p.userId,
                    "now": now,
                    "ret": now + timedelta(days=days),
                },
            )
            for rel, to_kind, to_id in (
                ("derived_from", "artifact", derived_from),
                ("evidenced_by", "change", change_id),
            ):
                if to_id:
                    await db.execute(
                        text(
                            "INSERT INTO artifact_lineage (id, run_id, from_kind, from_id,"
                            " to_kind, to_id, relation, created_at) VALUES (:i, :r, 'artifact',"
                            " :f, :tk, :ti, :rel, :now)"
                        ),
                        {
                            "i": str(uuid.uuid4()),
                            "r": run_id,
                            "f": aid,
                            "tk": to_kind,
                            "ti": to_id[:64],
                            "rel": rel,
                            "now": now,
                        },
                    )
            detail = {
                "runId": run_id,
                "artifactId": aid,
                "sizeBytes": len(data),
                "sha256": sha,
                "kind": kind,
                "verdict": scan["verdict"],
            }
            await _audit(db, p, "artifact.uploaded", "artifact", aid, detail)
            if quarantined:
                await _audit(
                    db,
                    p,
                    "artifact.quarantined",
                    "artifact",
                    aid,
                    {**detail, "reasons": scan["reasons"]},
                )
            await db.commit()
        except IntegrityError:
            await db.rollback()
            done = await replay(db)  # lost a requestId race: the winner's row is the answer
            if done is None:
                raise
            return done, True
        row = await _find(db, p, run_id, aid)
        return _art(row, now), False


@router.post("/runs/{runId}/artifacts", status_code=201)
async def upload_artifact(
    request: Request,
    runId: str,
    p: Writer,
    name: str = "",
    kind: Literal["upload", "output", "diff", "log"] = "upload",
    requestId: str | None = Query(None, max_length=64),
    retentionDays: int | None = Query(None, ge=1, le=3650),
    derivedFrom: str | None = Query(None, max_length=36),
    changeId: str | None = Query(None, max_length=36),
) -> Any:
    """Raw-body upload: the request body IS the file; ``?name=`` is its display name."""

    async def go() -> Any:
        clean = artifact_store.clean_name(name)
        if clean is None:
            raise ArError(
                422,
                "BAD_NAME",
                "name must be 1..120 chars without path separators,"
                " control characters or a leading or trailing dot",
            )
        if requestId is not None and not REQ_RE.match(requestId):
            raise ArError(422, "BAD_REQUEST_ID", "requestId must be 1..64 of A-Za-z0-9_.:-")
        factory = _need_factory()
        data = await _read_body(request, _env_int("HARNESS_ARTIFACT_MAX_BYTES", DEFAULT_MAX_BYTES))
        if not data:
            raise ArError(422, "EMPTY_ARTIFACT", "an artifact needs at least one byte")
        art, replayed = await create_artifact(
            factory,
            p,
            runId,
            name=clean,
            kind=kind,
            data=data,
            declared=request.headers.get("content-type", ""),
            request_id=requestId,
            retention_days=retentionDays,
            derived_from=derivedFrom,
            change_id=changeId,
        )
        return JSONResponse(
            {"artifact": art, "replayed": replayed},
            status_code=200 if replayed else 201,
            headers=_no_store(),
        )

    return await _handled(go())


@router.get("/runs/{runId}/artifacts")
async def list_artifacts(
    runId: str,
    p: Reader,
    state: Literal["available", "quarantined", "deleted", "expired"] | None = None,
    kind: Literal["upload", "output", "diff", "log"] | None = None,
    limit: int = Query(100, ge=1, le=200),
    cursor: str | None = Query(None, max_length=12),
) -> Any:
    async def go() -> Any:
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        now = _now()
        async with _need_factory()() as db:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_ACOLS} FROM artifacts WHERE run_id = :r AND team_id = :t"
                        " ORDER BY created_at DESC, id DESC"
                    ),
                    {"r": runId, "t": p.teamId},
                )
            ).all()
        items = [_art(r, now) for r in rows if (kind is None or r.kind == kind)]
        items = [a for a in items if (a["state"] == state if state else a["state"] != "deleted")]
        page = items[offset : offset + limit]
        nxt = str(offset + limit) if offset + limit < len(items) else None
        return JSONResponse({"items": page, "nextCursor": nxt}, headers=_no_store())

    return await _handled(go())


@router.get("/runs/{runId}/artifacts/{artifactId}")
async def get_artifact(runId: str, artifactId: str, p: Reader) -> Any:
    async def go() -> Any:
        async with _need_factory()() as db:
            row = await _find(db, p, runId, artifactId)
        return JSONResponse({"artifact": _art(row, _now())}, headers=_no_store())

    return await _handled(go())


@router.delete("/runs/{runId}/artifacts/{artifactId}")
async def delete_artifact(runId: str, artifactId: str, p: Writer) -> Any:
    """Uploader or an administer holder. Bytes are removed (when no other row of the team
    shares them), the row stays as a tombstone."""

    async def go() -> Any:
        now = _now()
        async with _need_factory()() as db:
            row = await _find(db, p, runId, artifactId)
            if row.state == "deleted":
                return JSONResponse({"artifact": _art(row, now)}, headers=_no_store())
            if row.created_by != p.userId and not (await _holds(db, p, "administer", runId))[0]:
                raise ArError(
                    403,
                    "NOT_ARTIFACT_OWNER",
                    "only the uploader or an administrator may delete an artifact",
                )
            await _tombstone(db, row, p.userId, now)
            await _audit(
                db,
                p,
                "artifact.deleted",
                "artifact",
                row.id,
                {"runId": runId, "artifactId": row.id, "reason": "requested"},
            )
            await db.commit()
            row = await _find(db, p, runId, artifactId)
        await _drop_blob_if_unused(row)
        return JSONResponse({"artifact": _art(row, now)}, headers=_no_store())

    return await _handled(go())


async def _tombstone(db: Any, row: Any, by: str, now: datetime) -> None:
    await db.execute(
        text(
            "UPDATE artifacts SET state = 'deleted', deleted_at = :now, deleted_by = :u"
            " WHERE id = :i AND state != 'deleted'"
        ),
        {"now": now, "u": by, "i": row.id},
    )


async def _drop_blob_if_unused(row: Any) -> None:
    factory = _factory()
    async with factory() as db:
        left = (
            await db.execute(
                text(
                    "SELECT 1 FROM artifacts WHERE storage_ref = :ref AND state != 'deleted'"
                    " LIMIT 1"
                ),
                {"ref": row.storage_ref},
            )
        ).first()
    if left is None:
        await asyncio.to_thread(artifact_store.remove, row.storage_ref)


async def purge_expired(factory: Any, now: datetime | None = None) -> int:
    """Retention sweep for the lead's scheduler: expired rows become tombstones and their bytes
    are removed. Reads already treat an expired artifact as gone before this runs."""
    now = now or _now()
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    f"SELECT {_ACOLS} FROM artifacts WHERE state != 'deleted'"
                    " AND retention_until IS NOT NULL AND retention_until <= :now"
                ),
                {"now": now},
            )
        ).all()
        for r in rows:
            await _tombstone(db, r, SYSTEM_ACTOR, now)
            who = Principal(userId=SYSTEM_ACTOR, teamId=r.team_id, role=Role.ENGINEER)
            await _audit(
                db,
                who,
                "artifact.deleted",
                "artifact",
                r.id,
                {"runId": r.run_id, "artifactId": r.id, "reason": "retention"},
            )
        await db.commit()
    for r in rows:
        await _drop_blob_if_unused(r)
    return len(rows)


async def _content_row(db: Any, p: Principal, run_id: str, artifact_id: str, preview: bool) -> Any:
    row = await _find(db, p, run_id, artifact_id)
    state = _eff_state(row, _now())
    if state == "deleted":
        raise ArError(410, "ARTIFACT_DELETED", "this artifact was deleted")
    if state == "expired":
        raise ArError(410, "ARTIFACT_EXPIRED", "this artifact passed its retention period")
    if state == "quarantined":
        if not (await _holds(db, p, "administer", run_id))[0]:
            raise ArError(
                403,
                "ARTIFACT_QUARANTINED",
                "this artifact is quarantined; only an administrator may download it",
            )
        if preview:
            raise ArError(409, "PREVIEW_UNAVAILABLE", "quarantined content has no preview")
    return row


async def _bytes(row: Any) -> bytes:
    data = await asyncio.to_thread(artifact_store.get, row.storage_ref)
    if data is None:
        raise ArError(
            410,
            "ARTIFACT_CONTENT_MISSING",
            "the stored bytes are gone or failed their integrity check",
        )
    return data


@router.get("/runs/{runId}/artifacts/{artifactId}/content")
async def artifact_content(runId: str, artifactId: str, p: Reader) -> Any:
    """Always an attachment with nosniff: the browser saves it, never renders it."""

    async def go() -> Any:
        async with _need_factory()() as db:
            row = await _content_row(db, p, runId, artifactId, preview=False)
        data = await _bytes(row)
        async with _need_factory()() as db:
            await _audit(
                db,
                p,
                "artifact.downloaded",
                "artifact",
                row.id,
                {"runId": runId, "artifactId": row.id, "state": row.state, "sizeBytes": len(data)},
            )
            await db.commit()
        return Response(
            content=data,
            media_type=(
                "application/octet-stream" if row.state == "quarantined" else row.media_type
            ),
            headers=_no_store(
                {
                    "Content-Disposition": _disposition(row.name),
                    "Content-Security-Policy": "sandbox; default-src 'none'",
                    "X-Artifact-Sha256": row.sha256,
                    "X-Artifact-State": row.state,
                }
            ),
        )

    return await _handled(go())


@router.get("/runs/{runId}/artifacts/{artifactId}/preview")
async def artifact_preview(runId: str, artifactId: str, p: Reader) -> Any:
    """JSON only. Text is returned as a JSON string (clients show it as text, never as HTML);
    everything else answers ``previewable: false`` and is download-only."""

    async def go() -> Any:
        async with _need_factory()() as db:
            row = await _content_row(db, p, runId, artifactId, preview=True)
        scan = _json(row.scan, {})
        base = {
            "artifactId": row.id,
            "name": row.name,
            "sizeBytes": int(row.size_bytes),
            "sha256": row.sha256,
            "mediaType": row.media_type,
        }
        if not scan.get("textual"):
            out = {**base, "previewable": False, "reason": "NOT_TEXT"}
        elif int(row.size_bytes) > PREVIEW_SOURCE_MAX:
            out = {**base, "previewable": False, "reason": "TOO_LARGE"}
        else:
            raw = (await _bytes(row)).decode("utf-8", errors="replace")
            shown = _clean_text(raw[:PREVIEW_CHARS])
            out = {
                **base,
                "previewable": True,
                "kind": "text",
                "text": shown,
                "truncated": len(raw) > PREVIEW_CHARS,
                "render": "text-only",
            }
        return JSONResponse(
            out, headers=_no_store({"Content-Security-Policy": "sandbox; default-src 'none'"})
        )

    return await _handled(go())


# ----------------------------------------------------------------- diff parsing


def _safe_path(path: str | None) -> str | None:
    if not path or len(path) > 512 or "\x00" in path or "\\" in path or path.startswith("/"):
        return None
    if ".." in path.split("/") or any(unicodedata.category(c) in ("Cc", "Cf") for c in path):
        return None
    return path


def _strip_prefix(raw: str) -> str:
    raw = raw.split("\t", 1)[0].strip()
    if raw.startswith(('"a/', '"b/')):
        raw = raw.strip('"')
    return raw[2:] if raw[:2] in ("a/", "b/") else raw


def parse_diff(diff: str) -> list[dict[str, Any]]:
    """Unified diff (git or difflib flavour) -> one dict per file. Hunk line counts are followed,
    so a removed line that happens to start with ``-- `` is not mistaken for a file header."""
    files: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    old_left = new_left = 0

    def start(path: str | None = None) -> dict[str, Any]:
        f = {
            "path": path,
            "kind": "modified",
            "lines": [],
            "add": 0,
            "del": 0,
            "maxAdd": 0,
            "binary": False,
            "symlink": False,
            "hdr": False,
            "hunks": 0,
        }
        files.append(f)
        return f

    for ln in diff.split("\n"):
        ln = ln.rstrip("\r") if old_left == 0 and new_left == 0 else ln
        if old_left > 0 or new_left > 0:  # inside a hunk body
            tag = ln[:1]
            if tag == "+":
                new_left -= 1
                cur["add"] += 1
                cur["maxAdd"] = max(cur["maxAdd"], len(ln) - 1)
            elif tag == "-":
                old_left -= 1
                cur["del"] += 1
            elif tag == "\\":
                pass
            else:
                old_left -= 1
                new_left -= 1
            cur["lines"].append(ln)
            continue
        m = re.match(r"^diff --git a/(.*) b/(.*)$", ln)
        if m:
            cur = start(m.group(2))
            cur["lines"].append(ln)
            continue
        h = _HUNK.match(ln)
        if h and cur is not None:
            old_left = int(h.group(1) if h.group(1) is not None else 1)
            new_left = int(h.group(2) if h.group(2) is not None else 1)
            cur["hunks"] += 1
            cur["lines"].append(ln)
            continue
        if ln.startswith("--- ") and cur is not None and not cur["hdr"] and cur["hunks"] == 0:
            cur["hdr"] = True  # git already opened this file with a diff --git line
            if ln[4:].startswith("/dev/null"):
                cur["kind"] = "added"
            cur["lines"].append(ln)
            continue
        if ln.startswith("--- "):
            cur = start()
            cur["hdr"] = True
            cur["old"] = _strip_prefix(ln[4:])
            if ln[4:].startswith("/dev/null"):
                cur["kind"] = "added"
            cur["lines"].append(ln)
            continue
        if cur is not None and ln.startswith("+++ ") and cur["hunks"] == 0:
            target = ln[4:]
            if target.startswith("/dev/null"):
                cur["kind"] = "deleted"
                cur["path"] = cur["path"] or cur.get("old")
            else:
                cur["path"] = _strip_prefix(target)
            cur["lines"].append(ln)
            continue
        bm = re.match(r"^Binary files? (?:a/)?(.*?)(?: and (?:b/)?(.*?))? differ(?:s)?$", ln)
        if bm or ln.startswith("GIT binary patch"):
            if cur is None or cur["hunks"] or cur["binary"]:
                cur = start()
            if cur["path"] is None and bm:
                cur["path"] = bm.group(2) or bm.group(1)
            cur["binary"] = True
            cur["lines"].append(ln)
            continue
        if cur is not None:
            if ln.startswith("new file mode"):
                cur["kind"] = "added"
            elif ln.startswith("deleted file mode"):
                cur["kind"] = "deleted"
            elif ln.startswith("rename to "):
                cur["kind"], cur["path"] = "renamed", ln[10:].strip()
            if ln.endswith(" 120000"):
                cur["symlink"] = True
            cur["lines"].append(ln)
    out = []
    for f in files:
        while f["lines"] and f["lines"][-1] == "":
            f["lines"].pop()
        if not f["lines"]:
            continue
        patch = "\n".join(f["lines"])
        flags = {
            "binary": f["binary"],
            "symlink": f["symlink"],
            "generated": bool(f["path"] and _GENERATED_PATH.search(f["path"]))
            or bool(_GENERATED_MARK.search(patch[:4000])),
            "minified": bool(f["path"] and ".min." in f["path"])
            or f["maxAdd"] > DIFF_MINIFIED_LINE,
            "large": len(patch) > DIFF_FILE_CHARS or len(f["lines"]) > DIFF_FILE_LINES,
        }
        path = _safe_path(f["path"])
        out.append(
            {
                "path": path,
                "kind": f["kind"],
                "additions": f["add"],
                "deletions": f["del"],
                "flags": flags,
                "patch": patch,
                "diffSha": hashlib.sha256(patch.encode()).hexdigest(),
            }
        )
    return out


def _omit_reason(flags: dict[str, Any]) -> str | None:
    for key in ("binary", "generated", "minified", "large"):
        if flags.get(key):
            return key
    return None


async def _done_diff(run_id: str) -> str | None:
    """The run's recorded done-signal diff, or None when there is none (or it is unreadable)."""
    getter = getattr(_svc(), "get_done_diff", None)
    if getter is None:
        return None
    try:
        return await getter(run_id)
    except (KeyError, ValueError):
        return None


@router.get("/runs/{runId}/diff")
async def run_diff(
    runId: str, p: Reader, limit: int = Query(100, ge=1, le=MAX_FILES), offset: int = Query(0, ge=0)
) -> Any:
    """The done-signal diff as structured JSON. Binary, generated, minified and large files show
    stats only. Patch text is control-character-stripped and is never HTML; clients render it
    with textContent."""

    async def go() -> Any:
        diff = await _done_diff(runId)
        if diff is None:
            raise _not_found("diff")
        files = parse_diff(diff)
        page, budget = [], DIFF_RESPONSE_CHARS
        for f in files[offset : offset + limit]:
            reason = _omit_reason(f["flags"])
            patch = f["patch"]
            if reason is None and len(patch) > budget:
                reason = "response_budget"
            item = {
                "path": f["path"] or "[unsafe path]",
                "pathRejected": f["path"] is None,
                "changeKind": f["kind"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "flags": f["flags"],
                "diffSha": f["diffSha"],
                "patch": None,
                "patchOmitted": reason,
            }
            if reason is None:
                item["patch"] = "\n".join(
                    _clean_text(
                        ln
                        if len(ln) <= DIFF_LINE_CHARS
                        else ln[:DIFF_LINE_CHARS] + " [line truncated]"
                    )
                    for ln in patch.split("\n")
                )
                budget -= len(item["patch"])
            page.append(item)
        nxt = offset + limit if offset + limit < len(files) else None
        return JSONResponse(
            {
                "runId": runId,
                "format": "unified-structured",
                "render": "text-only",
                "totalFiles": len(files),
                "files": page,
                "nextOffset": nxt,
            },
            headers=_no_store(),
        )

    return await _handled(go())


# ---------------------------------------------------------------------- changes

_CCOLS = (
    "id, run_id, team_id, path, change_kind, before_sha, after_sha, diff_sha, flags,"
    " plan_version, review_state, reviewed_by, reviewed_at, superseded, created_at"
)


def changes_digest(rows: list[Any]) -> str:
    pairs = sorted((r.path, r.diff_sha) for r in rows)
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode()).hexdigest()


def _chg(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "path": r.path,
        "changeKind": r.change_kind,
        "planVersion": r.plan_version,
        "reviewState": r.review_state,
        "reviewedBy": r.reviewed_by,
        "reviewedAt": _iso(r.reviewed_at),
        "diffSha": r.diff_sha,
        "flags": _json(r.flags, {}),
        "recordedAt": _iso(r.created_at),
    }


async def _plan_version(db: Any, p: Principal, run_id: str) -> int | None:
    """The plan version a change is bound to: the approved one, else the newest."""
    rows = await _optional(
        db,
        "SELECT version FROM plan_versions WHERE run_id = :r AND team_id = :t"
        " ORDER BY CASE WHEN state = 'approved' THEN 1 ELSE 0 END DESC, version DESC LIMIT 1",
        {"r": run_id, "t": p.teamId},
    )
    return int(rows[0].version) if rows else None


async def _current_changes(db: Any, p: Principal, run_id: str) -> list[Any]:
    return list(
        (
            await db.execute(
                text(
                    f"SELECT {_CCOLS} FROM run_changes WHERE run_id = :r AND team_id = :t"
                    " AND superseded = 0 ORDER BY path, created_at"
                ),
                {"r": run_id, "t": p.teamId},
            )
        ).all()
    )


async def sync_changes(db: Any, p: Principal, run_id: str) -> tuple[bool, int]:
    """Record the files of the run's done diff as changes (idempotent). Returns
    (diff available, number of paths refused as unsafe). A patch that differs from the one
    recorded for its path supersedes it and starts pending again."""
    diff = await _done_diff(run_id)
    if diff is None:
        return False, 0
    files = parse_diff(diff)
    safe = [f for f in files if f["path"]][:MAX_FILES]
    keep = {(f["path"], f["diffSha"]) for f in safe}
    now = _now()
    plan_version = await _plan_version(db, p, run_id)
    fresh = 0
    existing = {
        (r.path, r.diff_sha): r
        for r in (
            await db.execute(
                text(f"SELECT {_CCOLS} FROM run_changes WHERE run_id = :r AND team_id = :t"),
                {"r": run_id, "t": p.teamId},
            )
        ).all()
    }
    seen: set[tuple[str, str]] = set()
    for f in safe:
        if (f["path"], f["diffSha"]) in seen:
            continue
        seen.add((f["path"], f["diffSha"]))
        row = existing.get((f["path"], f["diffSha"]))
        if row is None:
            fresh += 1
            await db.execute(
                text(
                    "INSERT INTO run_changes (id, run_id, team_id, path, change_kind, diff_sha,"
                    " flags, plan_version, review_state, superseded, created_at) VALUES (:i, :r,"
                    " :t, :pa, :k, :d, :fl, :pv, 'pending', 0, :now)"
                ),
                {
                    "i": str(uuid.uuid4()),
                    "r": run_id,
                    "t": p.teamId,
                    "pa": f["path"],
                    "k": f["kind"],
                    "d": f["diffSha"],
                    "fl": json.dumps(f["flags"]),
                    "pv": plan_version,
                    "now": now,
                },
            )
        elif row.superseded:
            await db.execute(
                text("UPDATE run_changes SET superseded = 0 WHERE id = :i"), {"i": row.id}
            )
    for (path, sha), row in existing.items():
        if (path, sha) not in keep and not row.superseded:
            await db.execute(
                text("UPDATE run_changes SET superseded = 1 WHERE id = :i"), {"i": row.id}
            )
    if fresh:
        await _audit(
            db,
            p,
            "change.recorded",
            "agent_run",
            run_id,
            {"runId": run_id, "count": fresh, "planVersion": plan_version},
        )
    await db.commit()
    return True, len([f for f in files if not f["path"]])


@router.get("/runs/{runId}/changes")
async def list_changes(runId: str, p: Reader) -> Any:
    """Changed files of the run, each bound to the plan version and a review state. Reading
    records any not-yet-recorded files from the done diff (derived, deterministic, audited once)."""

    async def go() -> Any:
        async with _need_factory()() as db:
            available, unsafe = await sync_changes(db, p, runId)
            rows = await _current_changes(db, p, runId)
        counts = {
            s: sum(1 for r in rows if r.review_state == s)
            for s in ("pending", "approved", "rejected")
        }
        return JSONResponse(
            {
                "runId": runId,
                "items": [_chg(r) for r in rows],
                "counts": counts,
                "changesDigest": changes_digest(rows),
                "diffAvailable": available,
                "unsafePaths": unsafe,
            },
            headers=_no_store(),
        )

    return await _handled(go())


class ReviewBody(BaseModel):
    decision: Literal["approved", "rejected"]
    diffSha: str
    note: str | None = None


@router.post("/runs/{runId}/changes/{changeId}/review")
async def review_change(runId: str, changeId: str, body: ReviewBody, p: Approver) -> Any:
    """An approve holder decides one change. The decision must echo the patch digest reviewed;
    a change is decided once (a different patch is a different row)."""

    async def go() -> Any:
        if not SHA256_RE.match(body.diffSha) or len(body.note or "") > 500:
            raise ArError(422, "BAD_REVIEW", "diffSha must be a sha256 and note at most 500 chars")
        async with _need_factory()() as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_CCOLS} FROM run_changes WHERE id = :i AND run_id = :r"
                        " AND team_id = :t"
                    ),
                    {"i": changeId[:64], "r": runId, "t": p.teamId},
                )
            ).first()
            if row is None:
                raise _not_found("change")
            if row.superseded:
                raise ArError(409, "CHANGE_SUPERSEDED", "a newer patch replaced this change")
            if row.diff_sha != body.diffSha:
                raise ArError(409, "CHANGE_STALE", "the change is not the patch you reviewed")
            if row.review_state != "pending":
                if row.review_state == body.decision:
                    return JSONResponse(
                        {"change": _chg(row), "replayed": True}, headers=_no_store()
                    )
                raise ArError(
                    409, "CHANGE_ALREADY_REVIEWED", f"this change was already {row.review_state}"
                )
            res = await db.execute(  # compare-and-swap: two reviewers cannot both decide
                text(
                    "UPDATE run_changes SET review_state = :s, reviewed_by = :u, reviewed_at ="
                    " :now WHERE id = :i AND review_state = 'pending' AND superseded = 0"
                ),
                {"s": body.decision, "u": p.userId, "now": _now(), "i": row.id},
            )
            if res.rowcount != 1:
                raise ArError(409, "CHANGE_ALREADY_REVIEWED", "this change was just decided")
            await _audit(
                db,
                p,
                "change.reviewed",
                "run_change",
                row.id,
                {
                    "runId": runId,
                    "changeId": row.id,
                    "decision": body.decision,
                    "diffSha": row.diff_sha,
                    "planVersion": row.plan_version,
                },
            )
            await db.commit()
            row = (
                await db.execute(
                    text(f"SELECT {_CCOLS} FROM run_changes WHERE id = :i"), {"i": row.id}
                )
            ).first()
        return JSONResponse({"change": _chg(row), "replayed": False}, headers=_no_store())

    return await _handled(go())


# ------------------------------------------------------------------- git state


class GitStateBody(BaseModel):
    repoFullName: str | None = None
    baseBranch: str | None = None
    baseSha: str | None = None
    headBranch: str | None = None
    observedHeadSha: str | None = None
    runHeadSha: str | None = None


def _bad(field: str, rule: str) -> ArError:
    return ArError(422, "BAD_GIT_STATE", f"{field}: {rule}")


def _check_branch(field: str, v: str | None) -> None:
    if v is None:
        return
    if (
        not BRANCH_RE.match(v)
        or ".." in v
        or "//" in v
        or v.endswith(("/", ".lock", "."))
        or "@{" in v
    ):
        raise _bad(field, "not a valid branch name")


def _check_sha(field: str, v: str | None) -> None:
    if v is not None and not SHA_RE.match(v):
        raise _bad(field, "must be a lowercase 40 or 64 hex commit id")


async def _run_info(db: Any, run_id: str) -> Any:
    return (
        await db.execute(
            text(
                "SELECT r.project_id AS pid, r.branch AS branch, pr.repo_full_name AS repo"
                " FROM agent_runs r LEFT JOIN projects pr ON pr.id = r.project_id"
                " WHERE r.id = :r"
            ),
            {"r": run_id},
        )
    ).first()


async def _git_row(db: Any, p: Principal, run_id: str) -> Any:
    return (
        await db.execute(
            text(
                "SELECT run_id, team_id, repo_full_name, base_branch, base_sha, head_branch,"
                " observed_head_sha, run_head_sha, stale, checked_at, updated_by FROM run_git_state"
                " WHERE run_id = :r AND team_id = :t"
            ),
            {"r": run_id, "t": p.teamId},
        )
    ).first()


def _base_state(g: Any) -> str:
    if g is None or not g.base_sha or not g.observed_head_sha:
        return "unknown"
    return "stale" if g.base_sha != g.observed_head_sha else "current"


async def _git_view(db: Any, p: Principal, run_id: str) -> dict[str, Any]:
    run = await _run_info(db, run_id)
    g = await _git_row(db, p, run_id)
    prs = await _optional(
        db,
        "SELECT id, github_pr_number, pr_url, status FROM pull_requests WHERE run_id = :r"
        " ORDER BY created_at DESC LIMIT 1",
        {"r": run_id},
    )
    state = _base_state(g)
    recorded_repo = g.repo_full_name if g and g.repo_full_name else None
    system_repo = run.repo if run and run.repo else None
    return {
        "runId": run_id,
        "recorded": g is not None,
        "repo": {
            "fullName": system_repo or recorded_repo,
            "source": "project" if system_repo else ("recorded" if recorded_repo else None),
        },
        "branch": {
            "name": (g.head_branch if g and g.head_branch else None)
            or (run.branch if run else None),
            "source": "recorded"
            if g and g.head_branch
            else ("run" if run and run.branch else None),
            "convention": f"agent/{run_id}",
        },
        "base": {
            "branch": g.base_branch if g else None,
            "sha": g.base_sha if g else None,
            "observedHeadSha": g.observed_head_sha if g else None,
            "state": state,
            "stale": state == "stale",
            "checkedAt": _iso(g.checked_at) if g else None,
            "source": "recorded",
        },
        "runHeadSha": g.run_head_sha if g else None,
        "workspace": {"id": run_id, "isolatedPerRun": True},
        "pullRequest": (
            {
                "id": prs[0].id,
                "number": prs[0].github_pr_number,
                "url": prs[0].pr_url,
                "status": prs[0].status,
            }
            if prs
            else None
        ),
        "mergeRequirement": "rebase_or_merge_required" if state == "stale" else None,
        "updatedBy": g.updated_by if g else None,
    }


@router.get("/runs/{runId}/git-state")
async def get_git_state(runId: str, p: Reader) -> Any:
    async def go() -> Any:
        async with _need_factory()() as db:
            return JSONResponse(await _git_view(db, p, runId), headers=_no_store())

    return await _handled(go())


@router.put("/runs/{runId}/git-state")
async def put_git_state(runId: str, body: GitStateBody, p: Writer) -> Any:
    """Record what the caller observed. Partial: fields left out keep their value. Nothing here
    talks to GitHub; staleness is base sha != observed head of the base branch."""

    async def go() -> Any:
        fields = body.model_dump(exclude_none=True)
        if not fields:
            raise ArError(422, "EMPTY_UPDATE", "give at least one field")
        if body.repoFullName is not None and not REPO_RE.match(body.repoFullName):
            raise _bad("repoFullName", "must be owner/name")
        _check_branch("baseBranch", body.baseBranch)
        _check_branch("headBranch", body.headBranch)
        for k in ("baseSha", "observedHeadSha", "runHeadSha"):
            _check_sha(k, fields.get(k))
        async with _need_factory()() as db:
            run = await _run_info(db, runId)
            if run is not None and run.repo and body.repoFullName not in (None, run.repo):
                raise ArError(
                    422, "REPO_MISMATCH", "the run's project is connected to another repo"
                )
            cur = await _git_row(db, p, runId)
            repo = (
                (run.repo if run and run.repo else None)
                or body.repoFullName
                or (cur.repo_full_name if cur else "")
            )

            def pick(name: str, col: str) -> Any:
                return fields[name] if name in fields else (getattr(cur, col) if cur else None)

            vals = {
                "base_branch": pick("baseBranch", "base_branch"),
                "base_sha": pick("baseSha", "base_sha"),
                "head_branch": pick("headBranch", "head_branch"),
                "observed": pick("observedHeadSha", "observed_head_sha"),
                "run_head": pick("runHeadSha", "run_head_sha"),
            }
            stale = int(
                bool(vals["base_sha"] and vals["observed"] and vals["base_sha"] != vals["observed"])
            )
            params = {
                "r": runId,
                "t": p.teamId,
                "repo": repo or "",
                "now": _now(),
                "u": p.userId,
                "stale": stale,
                **vals,
            }
            try:
                if cur is None:
                    await db.execute(
                        text(
                            "INSERT INTO run_git_state (run_id, team_id, repo_full_name,"
                            " base_branch, base_sha, head_branch, observed_head_sha,"
                            " run_head_sha, stale, checked_at, updated_by) VALUES (:r, :t, :repo,"
                            " :base_branch, :base_sha, :head_branch, :observed, :run_head,"
                            " :stale, :now, :u)"
                        ),
                        params,
                    )
                else:
                    await db.execute(
                        text(
                            "UPDATE run_git_state SET repo_full_name = :repo, base_branch ="
                            " :base_branch, base_sha = :base_sha, head_branch = :head_branch,"
                            " observed_head_sha = :observed, run_head_sha = :run_head,"
                            " stale = :stale, checked_at = :now, updated_by = :u"
                            " WHERE run_id = :r AND team_id = :t"
                        ),
                        params,
                    )
                if stale and not (cur and cur.stale):
                    await _audit(
                        db,
                        p,
                        "base.stale_detected",
                        "agent_run",
                        runId,
                        {
                            "runId": runId,
                            "baseSha": vals["base_sha"],
                            "observedHeadSha": vals["observed"],
                        },
                    )
                await db.commit()
            except IntegrityError as e:
                await db.rollback()
                raise ArError(
                    409,
                    "BRANCH_IN_USE",
                    "another run of this team already uses that head branch in this repo",
                ) from e
            return JSONResponse(await _git_view(db, p, runId), headers=_no_store())

    return await _handled(go())


# ------------------------------------------------------------- merge approval


async def _latest_merge(db: Any, p: Principal, run_id: str) -> Any:
    return (
        await db.execute(
            text(
                "SELECT id, decision, head_sha, base_sha, changes_digest, plan_version, decided_by,"
                " note, created_at FROM merge_approvals WHERE run_id = :r AND team_id = :t"
                " ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"r": run_id, "t": p.teamId},
        )
    ).first()


def _merge_state(m: Any, g: Any, rows: list[Any]) -> str:
    """none | approved | rejected | outdated (head, base or changes moved since the record)."""
    if m is None:
        return "none"
    if m.decision == "rejected":
        return "rejected"
    if (
        m.changes_digest != changes_digest(rows)
        or (g is not None and g.run_head_sha and m.head_sha != g.run_head_sha)
        or _base_state(g) == "stale"
    ):
        return "outdated"
    return "approved"


def _merge_view(m: Any, state: str) -> dict[str, Any] | None:
    if m is None:
        return None
    return {
        "id": m.id,
        "decision": m.decision,
        "state": state,
        "headSha": m.head_sha,
        "baseSha": m.base_sha,
        "changesDigest": m.changes_digest,
        "planVersion": m.plan_version,
        "decidedBy": m.decided_by,
        "decidedAt": _iso(m.created_at),
    }


async def _blockers(db: Any, p: Principal, run_id: str) -> tuple[list[str], Any, list[Any], Any]:
    g = await _git_row(db, p, run_id)
    rows = await _current_changes(db, p, run_id)
    m = await _latest_merge(db, p, run_id)
    out: list[str] = []
    if g is None or _base_state(g) == "unknown":
        out.append("GIT_STATE_MISSING")
    elif _base_state(g) == "stale":
        out.append("BASE_STALE")
    if not rows:
        out.append("CHANGES_NOT_RECORDED")
    elif any(r.review_state == "rejected" for r in rows):
        out.append("CHANGES_REJECTED")
    elif any(r.review_state == "pending" for r in rows):
        out.append("CHANGES_NOT_REVIEWED")
    state = _merge_state(m, g, rows)
    if state != "approved":
        out.append(
            {
                "none": "MERGE_APPROVAL_REQUIRED",
                "rejected": "MERGE_REJECTED",
                "outdated": "MERGE_APPROVAL_OUTDATED",
            }[state]
        )
    return out, g, rows, m


class MergeBody(BaseModel):
    decision: Literal["approved", "rejected"] = "approved"
    headSha: str | None = None
    changesDigest: str
    requestId: str | None = None
    note: str | None = None


@router.post("/runs/{runId}/merge-approval", status_code=201)
async def merge_approval(runId: str, body: MergeBody, p: Approver) -> Any:
    """The human merge decision. It is its own record: plan approval does not create, imply or
    satisfy it, and holding it does not approve plans. Approving needs every current change
    approved, a fresh base, and echoes the changes digest and head sha being approved."""

    async def go() -> Any:
        if body.requestId is not None and not REQ_RE.match(body.requestId):
            raise ArError(422, "BAD_REQUEST_ID", "requestId must be 1..64 of A-Za-z0-9_.:-")
        if len(body.note or "") > 500 or not SHA256_RE.match(body.changesDigest):
            raise ArError(
                422, "BAD_MERGE_APPROVAL", "changesDigest must be a sha256, note at most 500 chars"
            )
        _check_sha("headSha", body.headSha)
        async with _need_factory()() as db:
            if body.requestId:
                prev = (
                    await db.execute(
                        text(
                            "SELECT id, decision, head_sha, base_sha, changes_digest, plan_version,"
                            " decided_by, note, created_at FROM merge_approvals WHERE run_id = :r"
                            " AND request_id = :q AND team_id = :t"
                        ),
                        {"r": runId, "q": body.requestId, "t": p.teamId},
                    )
                ).first()
                if prev is not None:
                    if (prev.decision, prev.head_sha, prev.changes_digest, prev.decided_by) != (
                        body.decision,
                        body.headSha,
                        body.changesDigest,
                        p.userId,
                    ):
                        raise ArError(
                            409, "REQUEST_ID_REUSED", "requestId was used for another decision"
                        )
                    return JSONResponse(
                        {"mergeApproval": _merge_view(prev, prev.decision), "replayed": True},
                        headers=_no_store(),
                    )
            kind = (
                await db.execute(
                    text(
                        "SELECT member_type FROM team_members WHERE team_id = :t AND user_id = :u"
                        " LIMIT 1"
                    ),
                    {"t": p.teamId, "u": p.userId},
                )
            ).first()
            if kind is not None and kind.member_type == "agent":
                raise await _merge_refused(
                    p, runId, 403, "AGENT_CANNOT_APPROVE_MERGE", "an agent cannot approve a merge"
                )
            await sync_changes(db, p, runId)
            blockers, g, rows, _ = await _blockers(db, p, runId)
            if body.changesDigest != changes_digest(rows):
                raise await _merge_refused(
                    p, runId, 409, "CHANGES_CHANGED", "the changes are not the ones you reviewed"
                )
            if body.decision == "approved":
                hard = [
                    b
                    for b in blockers
                    if b
                    in (
                        "GIT_STATE_MISSING",
                        "BASE_STALE",
                        "CHANGES_NOT_RECORDED",
                        "CHANGES_REJECTED",
                        "CHANGES_NOT_REVIEWED",
                    )
                ]
                if hard:
                    raise await _merge_refused(
                        p,
                        runId,
                        409,
                        hard[0],
                        "merge approval is blocked: " + ", ".join(hard),
                        {"blockers": hard},
                    )
                if not body.headSha or (g.run_head_sha and g.run_head_sha != body.headSha):
                    raise await _merge_refused(
                        p,
                        runId,
                        409,
                        "HEAD_MISMATCH",
                        "headSha must be the run branch head recorded in git-state",
                    )
            mid = str(uuid.uuid4())
            last = await _latest_merge(db, p, runId)
            stamp = _now()
            if last is not None and _dt(last.created_at) >= stamp:
                stamp = _dt(last.created_at) + timedelta(microseconds=1)  # decisions stay ordered
            try:
                await db.execute(
                    text(
                        "INSERT INTO merge_approvals (id, run_id, team_id, decision, head_sha,"
                        " base_sha, changes_digest, plan_version, decided_by, note, request_id,"
                        " created_at) VALUES (:i, :r, :t, :d, :h, :b, :c, :pv, :u, :n, :q, :now)"
                    ),
                    {
                        "i": mid,
                        "r": runId,
                        "t": p.teamId,
                        "d": body.decision,
                        "h": body.headSha,
                        "b": g.base_sha if g else None,
                        "c": body.changesDigest,
                        "pv": rows[0].plan_version if rows else None,
                        "u": p.userId,
                        "n": body.note,
                        "q": body.requestId,
                        "now": stamp,
                    },
                )
                await _audit(
                    db,
                    p,
                    "merge.approved" if body.decision == "approved" else "merge.refused",
                    "agent_run",
                    runId,
                    {
                        "runId": runId,
                        "mergeApprovalId": mid,
                        "decision": body.decision,
                        "headSha": body.headSha,
                        "changesDigest": body.changesDigest,
                    },
                )
                await db.commit()
            except IntegrityError:
                await db.rollback()  # lost a requestId race: the winner's record is the answer
                prev = (
                    await db.execute(
                        text(
                            "SELECT id, decision, head_sha, base_sha, changes_digest, plan_version,"
                            " decided_by, note, created_at FROM merge_approvals WHERE run_id = :r"
                            " AND request_id = :q AND team_id = :t"
                        ),
                        {"r": runId, "q": body.requestId, "t": p.teamId},
                    )
                ).first()
                if prev is None:
                    raise
                return JSONResponse(
                    {"mergeApproval": _merge_view(prev, prev.decision), "replayed": True},
                    headers=_no_store(),
                )
            m = await _latest_merge(db, p, runId)
            return JSONResponse(
                {"mergeApproval": _merge_view(m, m.decision), "replayed": False},
                status_code=201,
                headers=_no_store(),
            )

    return await _handled(go())


async def _merge_refused(
    p: Principal, run_id: str, status: int, code: str, msg: str, extra: dict[str, Any] | None = None
) -> ArError:
    await _audit_alone(p, "merge.refused", "agent_run", run_id, {"runId": run_id, "reason": code})
    err = ArError(status, code, msg, extra)
    err.audited = True  # the refusal row above is its audit
    return err


@router.get("/runs/{runId}/git-permissions")
async def git_permissions(runId: str, p: Reader) -> Any:
    """Who may commit, push, open a PR and merge on this run, answered by the policy engine for
    the caller and for the run's participants. Merge needs ``approve`` AND a current merge
    approval; the plan approval is not consulted."""

    async def go() -> Any:
        async with _need_factory()() as db:
            blockers, g, rows, m = await _blockers(db, p, runId)
            people = (
                await db.execute(
                    text(
                        "SELECT rp.user_id AS uid, tm.role AS role, tm.member_type AS mtype"
                        " FROM run_participants rp LEFT JOIN team_members tm ON tm.user_id ="
                        " rp.user_id AND tm.team_id = rp.team_id WHERE rp.run_id = :r AND"
                        " rp.team_id = :t ORDER BY rp.user_id LIMIT 50"
                    ),
                    {"r": runId, "t": p.teamId},
                )
            ).all()

            async def answer(uid: str, role: str, mtype: str) -> dict[str, Any]:
                steer = await _holds(db, p, "steer", runId, role, uid)
                approve = await _holds(db, p, "approve", runId, role, uid)
                human = mtype != "agent"
                can_merge = approve[0] and human and not blockers
                return {
                    "userId": uid,
                    "role": role,
                    "kind": mtype,
                    "commit": {"allowed": steer[0], "needs": "steer"},
                    "push": {"allowed": steer[0], "needs": "steer"},
                    "openPullRequest": {"allowed": steer[0], "needs": "steer"},
                    "approveMerge": {"allowed": approve[0] and human, "needs": "approve"},
                    "merge": {
                        "allowed": can_merge,
                        "needs": "approve + current merge approval",
                        "blockedBy": (
                            []
                            if can_merge
                            else (
                                (["NOT_AN_APPROVER"] if not (approve[0] and human) else [])
                                + blockers
                            )
                        ),
                    },
                }

            roster = [await answer(r.uid, r.role or "", r.mtype or "human") for r in people]
            me = await answer(p.userId, p.role.value, "human")
            state = _merge_state(m, g, rows)
        return JSONResponse(
            {
                "runId": runId,
                "caller": me,
                "participants": roster,
                "mergeApproval": _merge_view(m, state),
                "blockers": blockers,
                "separation": "plan approval never satisfies merge approval; "
                "they are separate records",
            },
            headers=_no_store(),
        )

    return await _handled(go())


# --------------------------------------------------------------------- lineage


@router.get("/runs/{runId}/lineage")
async def lineage(runId: str, p: Reader) -> Any:
    """Task request -> plan versions -> changes -> review -> accepted completion -> merge
    approval, plus the artifacts. Built from the tables of those stages; a stage whose table is
    not deployed or has no row is listed under ``missing``, never invented."""

    async def go() -> Any:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        def node(kind: str, id_: str, **kw: Any) -> None:
            nodes.append({"kind": kind, "id": id_, **kw})

        def edge(fk: str, fi: str, tk: str, ti: str, rel: str) -> None:
            edges.append(
                {"from": {"kind": fk, "id": fi}, "to": {"kind": tk, "id": ti}, "relation": rel}
            )

        async with _need_factory()() as db:
            task = await _optional(
                db,
                "SELECT version, updated_at FROM task_specs WHERE run_id = :r AND team_id = :t",
                {"r": runId, "t": p.teamId},
            )
            plans = await _optional(
                db,
                "SELECT id, version, state, content_sha256 FROM plan_versions WHERE run_id ="
                " :r AND team_id = :t ORDER BY version",
                {"r": runId, "t": p.teamId},
            )
            comp = await _optional(
                db,
                "SELECT agent_state, human_state FROM run_completion WHERE run_id = :r",
                {"r": runId},
            )
            changes = await _current_changes(db, p, runId)
            m = await _latest_merge(db, p, runId)
            g = await _git_row(db, p, runId)
            arts = (
                await db.execute(
                    text(
                        f"SELECT {_ACOLS} FROM artifacts WHERE run_id = :r AND team_id = :t"
                        " AND state != 'deleted' ORDER BY created_at, id"
                    ),
                    {"r": runId, "t": p.teamId},
                )
            ).all()
            links = (
                await db.execute(
                    text(
                        "SELECT from_kind, from_id, to_kind, to_id, relation FROM artifact_lineage"
                        " WHERE run_id = :r"
                    ),
                    {"r": runId},
                )
            ).all()
        stages = {
            "task": bool(task),
            "plan": bool(plans),
            "approvedPlan": False,
            "changes": bool(changes),
            "changesReviewed": False,
            "completionAccepted": False,
            "mergeApproved": False,
        }
        if task:
            node("task", runId, version=int(task[0].version))
        for pl in plans or []:
            node(
                "plan_version",
                pl.id,
                version=int(pl.version),
                state=pl.state,
                contentSha256=pl.content_sha256,
            )
            if task:
                edge("task", runId, "plan_version", pl.id, "planned_as")
            if pl.state == "approved":
                stages["approvedPlan"] = True
        plan_ids = {int(pl.version): pl.id for pl in plans or []}
        for c in changes:
            node(
                "change",
                c.id,
                path=c.path,
                reviewState=c.review_state,
                planVersion=c.plan_version,
                diffSha=c.diff_sha,
            )
            src = plan_ids.get(c.plan_version) if c.plan_version else None
            if src:
                edge("plan_version", src, "change", c.id, "implemented_by")
            elif task:
                edge("task", runId, "change", c.id, "changed_by")
        stages["changesReviewed"] = bool(changes) and all(
            c.review_state == "approved" for c in changes
        )
        if comp:
            accepted = comp[0].human_state == "accepted"
            node(
                "completion", runId, agentState=comp[0].agent_state, humanState=comp[0].human_state
            )
            stages["completionAccepted"] = accepted
            if accepted:
                for c in changes:
                    edge("change", c.id, "completion", runId, "accepted_in")
        if m is not None:
            state = _merge_state(m, g, changes)
            node(
                "merge_approval",
                m.id,
                decision=m.decision,
                state=state,
                decidedBy=m.decided_by,
                headSha=m.head_sha,
            )
            stages["mergeApproved"] = state == "approved"
            src_kind, src_id = ("completion", runId) if comp else ("task", runId)
            edge(src_kind, src_id, "merge_approval", m.id, "merge_decided_in")
            for c in changes:
                edge("change", c.id, "merge_approval", m.id, "covered_by")
        now = _now()
        for a in arts:
            node(
                "artifact",
                a.id,
                name=a.name,
                state=_eff_state(a, now),
                artifactKind=a.kind,
                sha256=a.sha256,
            )
            edge("task" if task else "run", runId, "artifact", a.id, "produced")
        for lk in links:
            edge(lk.from_kind, lk.from_id, lk.to_kind, lk.to_id, lk.relation)
        if not task:
            node("run", runId)
        required = (
            "task",
            "plan",
            "approvedPlan",
            "changes",
            "changesReviewed",
            "completionAccepted",
            "mergeApproved",
        )
        missing = [s for s in required if not stages[s]]
        return JSONResponse(
            {
                "runId": runId,
                "nodes": nodes,
                "edges": edges,
                "stages": stages,
                "missing": missing,
                "complete": not missing,
                "sources": {
                    "task": "task_specs",
                    "plan": "plan_versions",
                    "changes": "run_changes",
                    "completion": "run_completion",
                    "merge": "merge_approvals",
                    "artifacts": "artifacts + artifact_lineage",
                },
            },
            headers=_no_store(),
        )

    return await _handled(go())
