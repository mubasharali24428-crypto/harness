"""Export of session/run history and audit evidence, and deletion requests (part 50.005-008, 010).

Rules and limits: docs/search-and-export.md.

An export is a JOB: a row in ``export_jobs`` (who asked, which scope, format, size, digest, expiry,
downloads) plus a file in the export store (``HARNESS_EXPORT_DIR``). It is built synchronously, so
a job is ``completed`` or ``refused``/``failed`` when the POST returns. Everything an export holds
is read again from the live tables at request time and filtered for the requester: comment text
follows the discussion visibility rules, events are the durable history feed (no presence,
typing, cursors or markers), agent events are the allow-listed summaries (never terminal output
or diffs), and every JSON value passes ``search.clean_json`` (no secret-shaped keys or values, no
hidden-reasoning keys). Actor identity is the requester's choice (``includeActors``) and is
withheld for people erased by an executed deletion request. Size is capped
(``HARNESS_EXPORT_MAX_BYTES`` / ``HARNESS_EXPORT_MAX_RECORDS``): over the cap the job is refused
with ``EXPORT_TOO_LARGE``, never silently truncated. Every step writes an ``export.*`` audit row
with ids and counts only.

Deletion requests: a person asks for their own authored data to be deleted, an admin decides, and
approval executes the documented list (``DELETES``) and states what stays and why (``RETAINED``).

Raw SQL only (``text()``): sections may import only ``harness_contracts``.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from harness_contracts.roles import Principal, Role
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api as _api
from harness_orchestrator import collab, delivery, discussion, search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["exports"])

_service: Any = None

FORMAT = "harness.export/1"
KINDS = ("session_history", "run_history", "audit_evidence")
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_RECORDS = 20_000
DEFAULT_TTL_SECONDS = 86_400
_REF = re.compile(r"^[0-9a-f-]{36}\.(json|ndjson)$")
_MEDIA = {"json": "application/json", "ndjson": "application/x-ndjson"}

DELETES = [
    "comment text of everything you wrote (tombstone: state deleted, body emptied) and your display"
    " name on those comments",
    "the earlier revisions of those comments, except in audit-sensitive scopes (see retained)",
    "your reactions and the mentions of you on comments",
    "search documents derived from your comments, from events you caused and from approvals you"
    " requested; you are also removed from the participant lists of run and session documents",
    "export files you requested, and every unexpired history export whose scope holds data you"
    " authored (the file is deleted and the job marked deleted)",
]
RETAINED = [
    {
        "store": "audit_log",
        "why": "security evidence: append-only and hash chained, ids, states and"
        " counts only (no comment text); changing it would destroy tamper evidence",
    },
    {
        "store": "run_collab_events",
        "why": "run history, including guidance an agent was given, is"
        " evidence of why the agent acted; kept until the run's retention job removes it. "
        "Exports show"
        " you as a deleted user and search no longer finds those events",
    },
    {
        "store": "high_impact_approvals",
        "why": "a decision bound to a content digest is evidence of"
        " a consequential action; kept (search no longer finds your requests)",
    },
    {
        "store": "comments (tombstone row)",
        "why": "keeps thread structure, the opaque author user id"
        " and timestamps; the text and display name are gone",
    },
    {
        "store": "comment_revisions in audit-sensitive scopes",
        "why": "runs with a high-impact"
        " approval keep revisions for admins, as discussion deletion does (docs/discussion.md)",
    },
    {
        "store": "deletion_requests and export_jobs rows",
        "why": "proof that the request and the exports happened (ids, sizes, digests, no content)",
    },
    {
        "store": "artifacts",
        "why": "files belong to the run output and follow the artifact"
        " retention and delete controls, not this request",
    },
    {
        "store": "account and team membership",
        "why": "owned by the identity service: this request"
        " deletes authored content, not the account",
    },
]
REDACTION = [
    "secret-shaped keys and values removed; hidden-reasoning keys removed",
    "no presence, typing or cursor events; no engine cursor markers",
    "agent events limited to summaries: no terminal output, no diffs, no file contents",
    "hidden comments only for the author and admins, deleted/purged/past-retention comments"
    " without text",
]


def configure_exports(service: Any) -> None:
    global _service
    _service = service


def _factory() -> Any:
    svc = _service if _service is not None else _api._service
    return getattr(svc, "session_factory", None)


def _max_bytes() -> int:
    return search.env_int("HARNESS_EXPORT_MAX_BYTES", DEFAULT_MAX_BYTES) or DEFAULT_MAX_BYTES


def _max_records() -> int:
    return search.env_int("HARNESS_EXPORT_MAX_RECORDS", DEFAULT_MAX_RECORDS) or DEFAULT_MAX_RECORDS


def _ttl() -> int:
    return search.env_int("HARNESS_EXPORT_TTL_SECONDS", DEFAULT_TTL_SECONDS) or DEFAULT_TTL_SECONDS


def store_dir() -> Path:
    return Path(
        os.environ.get("HARNESS_EXPORT_DIR") or Path(tempfile.gettempdir()) / "harness-exports"
    )


def _file(ref: str) -> Path:
    if not _REF.match(ref or ""):
        raise ValueError("bad export reference")  # refs are built here, never from the client
    return store_dir() / ref


def _write_file(ref: str, data: bytes) -> None:
    d = store_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = d / f".{ref}.{uuid.uuid4().hex}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, _file(ref))


def _remove_file(ref: str | None) -> bool:
    if not ref:
        return False
    try:
        _file(ref).unlink()
        return True
    except (FileNotFoundError, ValueError):
        return False


class TooLarge(Exception):
    def __init__(self, what: str, limit: int) -> None:
        self.what, self.limit = what, limit
        super().__init__(f"export exceeds {what} limit {limit}")


class Budget:
    def __init__(self, records: int) -> None:
        self.records, self.left = records, records

    def take(self, n: int) -> None:
        self.left -= n
        if self.left < 0:
            raise TooLarge("records", self.records)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


# ------------------------------------------------------------------ actors, erasure


async def _erased(db: Any, team: str) -> dict[str, datetime]:
    got = await search.rows(
        db,
        "SELECT subject_user_id, MAX(executed_at) AS at FROM deletion_requests WHERE"
        " team_id = :t AND state = 'executed' GROUP BY subject_user_id",
        {"t": team},
    )
    return {r.subject_user_id: discussion._dt(r.at) for r in got if r.at is not None}


class Actors:
    """Turns user ids into the identity block of a record, once, after the final ordering."""

    def __init__(self, names: dict[str, str], erased: dict[str, datetime], include: bool) -> None:
        self.names, self.erased, self.include = names, erased, include
        self.pseudo: dict[str, str] = {}

    def of(self, uid: str | None, ts: datetime, hint: str | None = None) -> dict[str, Any] | None:
        if not uid:
            return None
        gone = self.erased.get(uid)
        if gone is not None and ts <= gone:
            return {"userId": None, "displayName": "deleted user", "erased": True}
        if uid.startswith("system:"):
            return {"userId": uid, "kind": "system"}
        if not self.include:
            return {"pseudonym": self.pseudo.setdefault(uid, f"actor-{len(self.pseudo) + 1}")}
        return {"userId": uid, "displayName": self.names.get(uid) or hint, "kind": "human"}


async def _names(db: Any, team: str, ids: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    listed = sorted(i for i in ids if i)
    for i in range(0, len(listed), 400):
        for r in await search.rows(
            db,
            "SELECT user_id, display_name FROM team_members WHERE team_id = :t AND user_id IN :ids",
            {"t": team, "ids": listed[i : i + 400]},
        ):
            out[r.user_id] = r.display_name
    return out


# ---------------------------------------------------------------- scope and builders


def _window(db_args: dict, col: str, since: datetime | None, until: datetime | None) -> str:
    sql = ""
    if since:
        sql += f" AND {col} >= :since"
        db_args["since"] = since
    if until:
        sql += f" AND {col} <= :until"
        db_args["until"] = until
    return sql


async def _run_scope(db: Any, p: Principal, run_id: str) -> Any:
    got = await search.rows(
        db,
        "SELECT r.id, r.project_id, r.branch, r.state, r.created_at FROM agent_runs r JOIN"
        " projects pr ON pr.id = r.project_id WHERE r.id = :r AND pr.team_id = :t",
        {"r": run_id, "t": p.teamId},
    )
    return got[0] if got and collab.pilot_allowed(p.teamId) else None


async def _session_scope(db: Any, p: Principal, sid: str) -> Any:
    got = await search.rows(
        db,
        "SELECT id, title, description, state, owner_user_id, created_at FROM collab_sessions"
        " WHERE id = :s AND team_id = :t",
        {"s": sid, "t": p.teamId},
    )
    if not got or (got[0].state == "deleted" and p.role != Role.ADMIN):
        return None
    if p.role != Role.ADMIN and not await search.rows(
        db,
        "SELECT 1 FROM session_members WHERE session_id = :s AND user_id = :u AND"
        " status = 'active'",
        {"s": sid, "u": p.userId},
    ):
        return None
    return got[0]


async def _history_records(
    db: Any,
    p: Principal,
    runs: list[str],
    sessions: list[str],
    since: datetime | None,
    until: datetime | None,
    budget: Budget,
) -> list[dict[str, Any]]:
    """Raw records (with ``_uid``/``_ts``) of the durable history of the given runs and the
    comments of the given runs and sessions. Every query is team-scoped."""
    out: list[dict[str, Any]] = []
    t = p.teamId
    if runs:
        a: dict[str, Any] = {
            "t": t,
            "runs": runs,
            "n": budget.left + 1,
            **delivery._visible_params(),
        }
        win = _window(a, "e.ts", since, until)
        visible = delivery._visible_sql().replace("kind", "e.kind")
        for r in await search.rows(
            db,
            "SELECT e.seq, e.run_id, e.actor_user_id, e.kind, e.request_id, e.payload,"
            " e.ts FROM run_collab_events e WHERE e.team_id = :t AND e.run_id IN :runs AND"
            f" {visible}{win} ORDER BY e.ts, e.seq LIMIT :n",
            a,
        ):
            out.append(
                {
                    "stream": "collab",
                    "kind": r.kind,
                    "seq": r.seq,
                    "id": None,
                    "runId": r.run_id,
                    "_ts": discussion._dt(r.ts),
                    "_uid": r.actor_user_id,
                    "data": {
                        "requestId": r.request_id,
                        "payload": search.clean_json(search.as_dict(r.payload)),
                    },
                }
            )
        budget.take(len(out))
        a = {"t": t, "runs": runs, "types": search.AGENT_EVENT_TYPES, "n": budget.left + 1}
        win = _window(a, "e.ts", since, until)
        got = await search.rows(
            db,
            "SELECT e.seq, e.run_id, e.type, e.payload, e.ts FROM agent_events e JOIN"
            " agent_runs r ON r.id = e.run_id JOIN projects pr ON pr.id = r.project_id WHERE"
            f" pr.team_id = :t AND e.run_id IN :runs AND e.type IN :types{win}"
            " ORDER BY e.ts, e.seq LIMIT :n",
            a,
        )
        budget.take(len(got))
        out += [
            {
                "stream": "agent",
                "kind": r.type,
                "seq": r.seq,
                "id": None,
                "runId": r.run_id,
                "_ts": discussion._dt(r.ts),
                "_uid": None,
                "data": {"summary": search.event_text(r.payload)},
            }
            for r in got
        ]
        a = {"t": t, "runs": runs, "n": budget.left + 1}
        win = _window(a, "h.created_at", since, until)
        got = await search.rows(
            db,
            "SELECT h.id, h.run_id, h.requester_user_id, h.decided_by, h.action, h.paths,"
            " h.summary, h.state, h.created_at, h.decided_at FROM high_impact_approvals h WHERE"
            f" h.team_id = :t AND h.run_id IN :runs{win} ORDER BY h.created_at, h.id LIMIT :n",
            a,
        )
        budget.take(len(got))
        for r in got:
            try:
                paths = [search.scrub_text(x, 512) for x in json.loads(r.paths)][:100]
            except (TypeError, ValueError):
                paths = []
            out.append(
                {
                    "stream": "approval",
                    "kind": f"approval.{r.state}",
                    "seq": None,
                    "id": r.id,
                    "runId": r.run_id,
                    "_ts": discussion._dt(r.created_at),
                    "_uid": r.requester_user_id,
                    "data": {
                        "action": r.action,
                        "paths": paths,
                        "summary": search.scrub_text(r.summary, 2000),
                        "state": r.state,
                        "decidedBy": r.decided_by,
                        "decidedAt": discussion._iso(r.decided_at),
                    },
                }
            )
    if runs or sessions:
        a = {"t": t, "runs": runs, "sess": sessions, "n": budget.left + 1}
        win = _window(a, "created_at", since, until)
        cfg = await discussion.settings(db, t)
        cutoff = discussion._cutoff(cfg, search.now())
        got = await search.rows(
            db,
            f"SELECT {discussion.COLS} FROM comments WHERE team_id = :t AND"
            " ((scope_kind = 'run' AND scope_id IN :runs) OR"
            f" (scope_kind = 'session' AND scope_id IN :sess)){win}"
            " ORDER BY created_at, id LIMIT :n",
            a,
        )
        budget.take(len(got))
        for r in got:
            gone = discussion._purged(r, cutoff)
            body = discussion._body_for(r, p, gone)
            why = (
                "purged"
                if gone
                else "deleted"
                if r.state == "deleted"
                else "hidden"
                if body is None
                else None
            )
            out.append(
                {
                    "stream": "comment",
                    "kind": "comment",
                    "seq": None,
                    "id": r.id,
                    "runId": r.scope_id if r.scope_kind == "run" else None,
                    "_ts": discussion._dt(r.created_at),
                    "_uid": r.author_user_id,
                    "_hint": r.author_name_snapshot,
                    "data": {
                        "scopeKind": r.scope_kind,
                        "scopeId": r.scope_id,
                        "parentId": r.parent_id,
                        "state": r.state,
                        "version": r.version,
                        "body": search.scrub_text(body, 4000) if body else None,
                        "bodyWithheld": why,
                        "editedAt": discussion._iso(r.edited_at),
                    },
                }
            )
    return out


async def _audit_records(
    db: Any,
    p: Principal,
    target: str | None,
    since: datetime | None,
    until: datetime | None,
    budget: Budget,
) -> list[dict[str, Any]]:
    a: dict[str, Any] = {"t": p.teamId, "needle": f'"teamId": "{p.teamId}"', "n": budget.left + 1}
    sql = (
        "SELECT seq, actor_member_id, actor_kind, action, target_type, target_id, detail, ts"
        + (", chain_pos, prev_hash, row_hash" if db.bind.dialect.name == "postgresql" else "")
        + " FROM audit_log WHERE (actor_member_id IN (SELECT id FROM team_members WHERE"
        " team_id = :t) OR (actor_member_id IS NULL AND CAST(detail AS TEXT) LIKE"
        " '%' || :needle || '%'))"
    )
    if target:
        sql += " AND target_id = :target"
        a["target"] = target
    sql += _window(a, "ts", since, until) + " ORDER BY seq LIMIT :n"
    got = await search.rows(db, sql, a)
    budget.take(len(got))
    people = (
        await search.rows(
            db,
            "SELECT id, user_id, display_name FROM team_members WHERE team_id = :t",
            {"t": p.teamId},
        )
        if got
        else []
    )
    members = {r.id: r for r in people}
    out = []
    for r in got:
        m = members.get(r.actor_member_id)
        detail = search.as_dict(r.detail)
        clean = search.clean_json(detail)
        out.append(
            {
                "stream": "audit",
                "kind": r.action,
                "seq": r.seq,
                "id": None,
                "runId": None,
                "_ts": discussion._dt(r.ts),
                "_uid": m.user_id if m else None,
                "_hint": m.display_name if m else None,
                "data": {
                    "actorMemberId": r.actor_member_id,
                    "actorKind": r.actor_kind,
                    "targetType": r.target_type,
                    "targetId": r.target_id,
                    "detail": clean,
                    "detailRedacted": clean != detail,
                    "chain": {
                        "pos": getattr(r, "chain_pos", None),
                        "prevHash": getattr(r, "prev_hash", None),
                        "rowHash": getattr(r, "row_hash", None),
                    },
                },
            }
        )
    return out


def _sort_key(r: dict[str, Any]) -> tuple:
    return (r["_ts"], r["stream"], r["seq"] if r["seq"] is not None else -1, r["id"] or "")


async def _build(
    db: Any,
    p: Principal,
    job_id: str,
    kind: str,
    scope: tuple[str, str],
    opts: dict[str, Any],
    since: datetime | None,
    until: datetime | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (header, records) for one export. Raises TooLarge over the record cap."""
    budget = Budget(_max_records())
    header: dict[str, Any] = {}
    if kind == "run_history":
        run = await _run_scope(db, p, scope[1])
        header["run"] = {
            "id": run.id,
            "projectId": run.project_id,
            "branch": run.branch,
            "state": run.state,
            "createdAt": discussion._iso(run.created_at),
        }
        raw = await _history_records(db, p, [run.id], [], since, until, budget)
    elif kind == "session_history":
        s = await _session_scope(db, p, scope[1])
        linked = (
            [
                r.run_id
                for r in await search.rows(
                    db,
                    "SELECT sr.run_id FROM session_runs sr JOIN agent_runs r ON r.id = sr.run_id"
                    " JOIN projects pr ON pr.id = r.project_id WHERE sr.session_id = :s AND"
                    " pr.team_id = :t ORDER BY sr.run_id",
                    {"s": s.id, "t": p.teamId},
                )
            ]
            if collab.pilot_allowed(p.teamId)
            else []
        )
        header["session"] = {
            "id": s.id,
            "title": search.scrub_text(s.title, 200),
            "state": s.state,
            "description": search.scrub_text(s.description, 1000),
            "createdAt": discussion._iso(s.created_at),
            "linkedRuns": linked,
        }
        if opts["includeActors"]:
            header["session"]["members"] = [
                {
                    "userId": m.user_id,
                    "agentId": m.agent_id,
                    "role": m.role,
                    "displayName": m.display_name,
                    "status": m.status,
                }
                for m in await search.rows(
                    db,
                    "SELECT user_id, agent_id, role, display_name, status FROM session_members"
                    " WHERE session_id = :s ORDER BY id",
                    {"s": s.id},
                )
            ]
        raw = await _history_records(db, p, linked, [s.id], since, until, budget)
    else:
        target = scope[1] if scope[0] != "team" else None
        raw = await _audit_records(db, p, target, since, until, budget)
    raw.sort(key=_sort_key)
    erased = await _erased(db, p.teamId)
    names = await _names(db, p.teamId, {r["_uid"] for r in raw if r["_uid"]})
    actors = Actors(names, erased, opts["includeActors"])
    records = []
    for i, r in enumerate(raw, 1):
        records.append(
            {
                "order": i,
                "stream": r["stream"],
                "kind": r["kind"],
                "seq": r["seq"],
                "id": r["id"],
                "ts": r["_ts"].isoformat(),
                "runId": r["runId"],
                "actor": actors.of(r["_uid"], r["_ts"], r.get("_hint")),
                "data": r["data"],
            }
        )
    header.update(
        {
            "format": FORMAT,
            "exportId": job_id,
            "kind": kind,
            "scope": {"kind": scope[0], "id": scope[1]},
            "teamId": p.teamId,
            "requestedBy": p.userId,
            "generatedAt": search.now().isoformat(),
            "ordering": "ts, then stream, then seq, then id; 'order' is the position in this list",
            "timestamps": "ISO 8601, UTC",
            "actorsIncluded": opts["includeActors"],
            "window": {
                "from": since.isoformat() if since else None,
                "to": until.isoformat() if until else None,
            },
            "redaction": REDACTION,
            "limits": {"maxBytes": _max_bytes(), "maxRecords": _max_records()},
            "recordCount": len(records),
        }
    )
    return header, records


def _render(header: dict[str, Any], records: list[dict[str, Any]], fmt: str) -> bytes:
    if fmt == "json":
        return _dumps({**header, "records": records}).encode()
    lines = [_dumps({"_type": "header", **header})]
    lines += [_dumps({"_type": "record", **r}) for r in records]
    lines.append(_dumps({"_type": "footer", "recordCount": len(records), "complete": True}))
    return ("\n".join(lines) + "\n").encode()


# ------------------------------------------------------------------------ jobs


def _view(r: Any) -> dict[str, Any]:
    exp = discussion._dt(r.expires_at)
    state = "expired" if r.state == "completed" and exp <= search.now() else r.state
    return {
        "id": r.id,
        "kind": r.kind,
        "scope": {"kind": r.scope_kind, "id": r.scope_id},
        "format": r.format,
        "state": state,
        "errorCode": r.error_code,
        "recordCount": r.record_count,
        "sizeBytes": r.size_bytes,
        "sha256": r.sha256,
        "requestedBy": r.requested_by,
        "createdAt": discussion._iso(r.created_at),
        "completedAt": discussion._iso(r.completed_at),
        "expiresAt": discussion._iso(exp),
        "downloadCount": r.download_count,
        "options": search.as_dict(r.options),
        "download": f"/api/v1/orchestrator/exports/{r.id}/download"
        if state == "completed"
        else None,
    }


_JOB_COLS = (
    "id, team_id, requested_by, requester_role, kind, scope_kind, scope_id, format,"
    " options, state, error_code, record_count, size_bytes, sha256, storage_ref,"
    " request_id, download_count, created_at, completed_at, expires_at"
)


async def _job(db: Any, p: Principal, job_id: str) -> Any:
    """A job of the caller's team that the caller may see (own, or any for admins), else None."""
    got = await search.rows(
        db,
        f"SELECT {_JOB_COLS} FROM export_jobs WHERE id = :i AND team_id = :t",
        {"i": job_id, "t": p.teamId},
    )
    if not got or (p.role != Role.ADMIN and got[0].requested_by != p.userId):
        return None
    return got[0]


async def _refuse(
    db: Any,
    p: Principal,
    code: str,
    status: int,
    message: str,
    detail: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> Any:
    await search.audit(
        db,
        p,
        "export.refused",
        "export",
        detail.get("exportId", p.teamId),
        {"reason": code, **detail},
    )
    await db.commit()
    return search.envelope(status, code, message, extra)


class ExportBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    kind: Literal["session_history", "run_history", "audit_evidence"]
    scopeId: str | None = Field(default=None, max_length=80)
    format: Literal["json", "ndjson"] = "json"
    includeActors: bool = True
    since: str | None = Field(default=None, alias="from", max_length=40)
    until: str | None = Field(default=None, alias="to", max_length=40)
    requestId: str | None = Field(default=None, max_length=64)


async def create_export(f: Any, p: Principal, req: ExportBody) -> tuple[dict[str, Any], bool]:
    since, until = search.parse_ts(req.since, "from"), search.parse_ts(req.until, "to")
    if since and until and since > until:
        raise search.envelope(422, "BAD_RANGE", "from is after to")
    ok = discussion._ROLE_OK["export"](p.role) and (
        req.kind != "audit_evidence" or p.role == Role.ADMIN
    )
    async with f() as db:
        if not ok:
            raise await _refuse(
                db, p, "ROLE_DENIED", 403, f"role may not export {req.kind}", {"kind": req.kind}
            )
        if req.requestId:
            dup = await search.rows(
                db,
                f"SELECT {_JOB_COLS} FROM export_jobs WHERE team_id = :t AND requested_by ="
                " :u AND request_id = :q",
                {"t": p.teamId, "u": p.userId, "q": req.requestId},
            )
            if dup:
                return _view(dup[0]), False
        if req.kind == "audit_evidence":
            scope = ("team", p.teamId)
            if req.scopeId:
                scope = (
                    ("run", req.scopeId)
                    if await _run_scope(db, p, req.scopeId)
                    else (
                        ("session", req.scopeId)
                        if await _session_scope(db, p, req.scopeId)
                        else None
                    )
                )
        elif not req.scopeId:
            raise search.envelope(422, "BAD_SCOPE", "scopeId is required for this kind")
        elif req.kind == "run_history":
            scope = ("run", req.scopeId) if await _run_scope(db, p, req.scopeId) else None
        else:
            scope = ("session", req.scopeId) if await _session_scope(db, p, req.scopeId) else None
        if scope is None:  # foreign, deleted, not a member, unknown: one answer
            raise await _refuse(
                db,
                p,
                "NOT_FOUND",
                404,
                "scope not found",
                {"kind": req.kind, "scopeId": req.scopeId},
            )
        job_id = str(uuid.uuid4())
        opts = {"includeActors": req.includeActors}
        t = search.now()
        try:
            await db.execute(
                text(
                    "INSERT INTO export_jobs (id, team_id, requested_by, requester_role, kind,"
                    " scope_kind, scope_id, format, options, state, request_id, created_at,"
                    " expires_at) VALUES (:i, :t, :u, :ro, :k, :sk, :si, :f, :o, 'running', :q,"
                    " :c, :e)"
                ),
                {
                    "i": job_id,
                    "t": p.teamId,
                    "u": p.userId,
                    "ro": p.role.value,
                    "k": req.kind,
                    "sk": scope[0],
                    "si": scope[1],
                    "f": req.format,
                    "o": _dumps({**opts, "from": req.since, "to": req.until}),
                    "q": req.requestId,
                    "c": t,
                    "e": t + timedelta(seconds=_ttl()),
                },
            )
            await search.audit(
                db,
                p,
                "export.requested",
                "export",
                job_id,
                {
                    "exportId": job_id,
                    "kind": req.kind,
                    "scopeKind": scope[0],
                    "scopeId": scope[1],
                    "format": req.format,
                    "includeActors": req.includeActors,
                },
            )
            await db.commit()
        except IntegrityError:  # a simultaneous retry of the same requestId
            await db.rollback()
            dup = await search.rows(
                db,
                f"SELECT {_JOB_COLS} FROM export_jobs WHERE team_id = :t AND requested_by ="
                " :u AND request_id = :q",
                {"t": p.teamId, "u": p.userId, "q": req.requestId},
            )
            return _view(dup[0]), False
        try:
            header, records = await _build(db, p, job_id, req.kind, scope, opts, since, until)
            data = _render(header, records, req.format)
            if len(data) > _max_bytes():
                raise TooLarge("bytes", _max_bytes())
        except TooLarge as e:
            await db.rollback()
            await db.execute(
                text(
                    "UPDATE export_jobs SET state = 'refused', error_code = 'EXPORT_TOO_LARGE',"
                    " completed_at = :c WHERE id = :i"
                ),
                {"c": search.now(), "i": job_id},
            )
            raise await _refuse(
                db,
                p,
                "EXPORT_TOO_LARGE",
                413,
                f"the export exceeds the {e.what} limit ({e.limit}); narrow the scope or window",
                {"exportId": job_id, "kind": req.kind, "limit": e.what},
                {"limit": e.what, "max": e.limit, "exportId": job_id},
            ) from e
        except Exception:
            await db.rollback()
            logger.exception("export build failed job=%s", job_id)
            await db.execute(
                text(
                    "UPDATE export_jobs SET state = 'failed', error_code = 'EXPORT_FAILED',"
                    " completed_at = :c WHERE id = :i"
                ),
                {"c": search.now(), "i": job_id},
            )
            raise await _refuse(
                db,
                p,
                "EXPORT_FAILED",
                500,
                "the export could not be built",
                {"exportId": job_id, "kind": req.kind},
            ) from None
        ref = f"{job_id}.{req.format}"
        digest = hashlib.sha256(data).hexdigest()
        await asyncio.to_thread(_write_file, ref, data)
        await db.execute(
            text(
                "UPDATE export_jobs SET state = 'completed', record_count = :n, size_bytes = :b,"
                " sha256 = :h, storage_ref = :r, completed_at = :c WHERE id = :i"
            ),
            {
                "n": len(records),
                "b": len(data),
                "h": digest,
                "r": ref,
                "c": search.now(),
                "i": job_id,
            },
        )
        await search.audit(
            db,
            p,
            "export.completed",
            "export",
            job_id,
            {
                "exportId": job_id,
                "kind": req.kind,
                "scopeKind": scope[0],
                "scopeId": scope[1],
                "format": req.format,
                "recordCount": len(records),
                "sizeBytes": len(data),
                "sha256": digest,
            },
        )
        await db.commit()
        return _view(
            (
                await search.rows(
                    db, f"SELECT {_JOB_COLS} FROM export_jobs WHERE id = :i", {"i": job_id}
                )
            )[0]
        ), True


async def _still_allowed(db: Any, p: Principal, job: Any) -> bool:
    """Re-authorise the scope at download time: losing access to the session or run ends the
    right to take the file, whoever asked for it."""
    if job.scope_kind == "run":
        return await _run_scope(db, p, job.scope_id) is not None
    if job.scope_kind == "session":
        return await _session_scope(db, p, job.scope_id) is not None
    return p.role == Role.ADMIN


async def list_exports(f: Any, p: Principal, limit: int) -> dict[str, Any]:
    if not discussion._ROLE_OK["export"](p.role):
        raise search.envelope(403, "ROLE_DENIED", "role may not export")
    async with f() as db:
        mine = "" if p.role == Role.ADMIN else " AND requested_by = :u"
        got = await search.rows(
            db,
            f"SELECT {_JOB_COLS} FROM export_jobs WHERE team_id = :t{mine}"
            " ORDER BY created_at DESC, id DESC LIMIT :n",
            {"t": p.teamId, "u": p.userId, "n": limit},
        )
    return {"exports": [_view(r) for r in got]}


async def get_export(f: Any, p: Principal, job_id: str) -> dict[str, Any]:
    if not discussion._ROLE_OK["export"](p.role):
        raise search.envelope(403, "ROLE_DENIED", "role may not export")
    async with f() as db:
        job = await _job(db, p, job_id)
    if job is None:
        raise search.envelope(404, "EXPORT_NOT_FOUND", "export not found")
    return _view(job)


async def download(f: Any, p: Principal, job_id: str) -> tuple[bytes, Any]:
    if not discussion._ROLE_OK["export"](p.role):
        raise search.envelope(403, "ROLE_DENIED", "role may not export")
    async with f() as db:
        job = await _job(db, p, job_id)
        if job is None or (job.state == "completed" and not await _still_allowed(db, p, job)):
            raise search.envelope(404, "EXPORT_NOT_FOUND", "export not found")
        if job.state in ("expired", "deleted") or (
            job.state == "completed" and discussion._dt(job.expires_at) <= search.now()
        ):
            raise search.envelope(410, "EXPORT_EXPIRED", "this export has expired or was deleted")
        if job.state != "completed" or not job.storage_ref:
            raise search.envelope(409, "EXPORT_UNAVAILABLE", f"export is {job.state}")
        try:
            data = await asyncio.to_thread(lambda: _file(job.storage_ref).read_bytes())
        except (FileNotFoundError, ValueError):
            raise search.envelope(410, "EXPORT_EXPIRED", "the export file is gone") from None
        if hashlib.sha256(data).hexdigest() != job.sha256:
            raise search.envelope(500, "EXPORT_CORRUPT", "the export file failed its checksum")
        await db.execute(
            text("UPDATE export_jobs SET download_count = download_count + 1 WHERE id = :i"),
            {"i": job_id},
        )
        await search.audit(
            db,
            p,
            "export.downloaded",
            "export",
            job_id,
            {
                "exportId": job_id,
                "kind": job.kind,
                "sizeBytes": job.size_bytes,
                "sha256": job.sha256,
                "owner": job.requested_by == p.userId,
            },
        )
        await db.commit()
    return data, job


async def purge_expired_exports(f: Any, *, team_id: str | None = None) -> int:
    """Delete the files of expired and deleted jobs and mark expired jobs ``expired``. A function:
    ``schedule.maintenance_jobs`` runs it on an interval. Also finishes file removal that a
    crash left behind after a deletion request. Returns the number of files removed."""
    removed = 0
    async with f() as db:
        extra = " AND team_id = :t" if team_id else ""
        got = await search.rows(
            db,
            "SELECT id, state, storage_ref FROM export_jobs WHERE storage_ref IS NOT NULL AND"
            f" (state IN ('expired', 'deleted') OR expires_at <= :now){extra}",
            {"now": search.now(), "t": team_id},
        )
        for r in got:
            removed += int(await asyncio.to_thread(_remove_file, r.storage_ref))
            await db.execute(
                text(
                    "UPDATE export_jobs SET storage_ref = NULL, state = CASE WHEN state ="
                    " 'completed' THEN 'expired' ELSE state END WHERE id = :i"
                ),
                {"i": r.id},
            )
        await db.commit()
    return removed


# ------------------------------------------------------------- deletion requests

_REQ_COLS = (
    "id, team_id, subject_user_id, requested_by, reason, state, request_id, created_at,"
    " decided_by, decided_at, decision_note, executed_at, result"
)


def _req_view(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "subjectUserId": r.subject_user_id,
        "requestedBy": r.requested_by,
        "reason": r.reason,
        "state": r.state,
        "createdAt": discussion._iso(r.created_at),
        "decidedBy": r.decided_by,
        "decidedAt": discussion._iso(r.decided_at),
        "decisionNote": r.decision_note,
        "executedAt": discussion._iso(r.executed_at),
        "result": search.as_dict(r.result) or None,
    }


def _policy() -> dict[str, Any]:
    return {
        "deletes": DELETES,
        "retained": RETAINED,
        "decision": "an admin approves or rejects; approval executes immediately",
    }


class DeletionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=collab.MAX_REASON)
    requestId: str | None = Field(default=None, max_length=64)


class DecisionBody(BaseModel):
    note: str | None = Field(default=None, max_length=collab.MAX_REASON)


async def create_deletion_request(
    f: Any, p: Principal, req: DeletionBody
) -> tuple[dict[str, Any], bool]:
    """Any signed-in person may ask for THEIR OWN authored data to be deleted (the subject is the
    principal, never a client-supplied id). One open request per person."""
    async with f() as db:
        if req.requestId:
            dup = await search.rows(
                db,
                f"SELECT {_REQ_COLS} FROM deletion_requests WHERE team_id = :t AND"
                " requested_by = :u AND request_id = :q",
                {"t": p.teamId, "u": p.userId, "q": req.requestId},
            )
            if dup:
                return {**_req_view(dup[0]), "policy": _policy()}, False
        rid = str(uuid.uuid4())
        try:
            await db.execute(
                text(
                    "INSERT INTO deletion_requests (id, team_id, subject_user_id, requested_by,"
                    " reason, state, request_id, created_at) VALUES (:i, :t, :u, :u, :r,"
                    " 'pending', :q, :c)"
                ),
                {
                    "i": rid,
                    "t": p.teamId,
                    "u": p.userId,
                    "r": (req.reason or "").strip() or None,
                    "q": req.requestId,
                    "c": search.now(),
                },
            )
            await search.audit(
                db,
                p,
                "deletion.requested",
                "deletion_request",
                rid,
                {"requestId": rid, "subjectUserId": p.userId},
            )
            await db.commit()
        except IntegrityError:
            await db.rollback()
            dup = await search.rows(
                db,
                f"SELECT {_REQ_COLS} FROM deletion_requests WHERE team_id = :t AND"
                " requested_by = :u AND request_id = :q AND :q IS NOT NULL",
                {"t": p.teamId, "u": p.userId, "q": req.requestId},
            )
            if dup:  # a simultaneous retry of the same requestId
                return {**_req_view(dup[0]), "policy": _policy()}, False
            raise search.envelope(
                409, "DELETION_PENDING", "you already have an open deletion request"
            ) from None
        got = await search.rows(
            db, f"SELECT {_REQ_COLS} FROM deletion_requests WHERE id = :i", {"i": rid}
        )
    return {**_req_view(got[0]), "policy": _policy()}, True


async def list_deletion_requests(
    f: Any, p: Principal, state: str | None, limit: int
) -> dict[str, Any]:
    async with f() as db:
        sql = f"SELECT {_REQ_COLS} FROM deletion_requests WHERE team_id = :t"
        a: dict[str, Any] = {"t": p.teamId, "n": limit}
        if p.role != Role.ADMIN:
            sql += " AND subject_user_id = :u"
            a["u"] = p.userId
        if state:
            sql += " AND state = :s"
            a["s"] = state
        got = await search.rows(db, sql + " ORDER BY created_at DESC, id DESC LIMIT :n", a)
    return {"requests": [_req_view(r) for r in got], "policy": _policy()}


def _chunks(items: list[str], n: int = 400) -> list[list[str]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


async def _execute(db: Any, team: str, subject: str, decider: str) -> tuple[dict, list[str]]:
    """The documented deletion, in the caller's transaction. Returns (counts, export files to
    remove after the commit)."""
    t = search.now()
    u = {"t": team, "u": subject}
    mine = await search.rows(
        db,
        "SELECT id, scope_kind, scope_id, state FROM comments WHERE team_id = :t AND"
        " author_user_id = :u",
        u,
    )
    ids = [r.id for r in mine]
    live = [r.id for r in mine if r.state != "deleted"]
    for chunk in _chunks(live):
        await db.execute(
            search._stmt(
                "UPDATE comments SET state = 'deleted', body = '', deleted_at = :now,"
                " deleted_by = :u WHERE team_id = :t AND id IN :ids",
                {"ids": chunk},
            ),
            {"now": t, "u": subject, "t": team, "ids": chunk},
        )
    await db.execute(
        text(
            "UPDATE comments SET author_name_snapshot = 'deleted user'"
            " WHERE team_id = :t AND author_user_id = :u"
        ),
        u,
    )
    cfg = await discussion.settings(db, team)
    sys_p = search.system_principal(team)
    scopes: dict[tuple[str, str], list[str]] = {}
    for r in mine:
        scopes.setdefault((r.scope_kind, r.scope_id), []).append(r.id)
    blanked = kept = 0
    for (kind, sid), cids in scopes.items():
        if await discussion._sensitive(db, sys_p, cfg, kind, sid):
            kept += len(cids)
            continue
        for chunk in _chunks(cids):
            res = await db.execute(
                search._stmt(
                    "UPDATE comment_revisions SET body = '' WHERE comment_id IN :ids",
                    {"ids": chunk},
                ),
                {"ids": chunk},
            )
            blanked += res.rowcount or 0
    reactions = (
        await db.execute(
            text(
                "DELETE FROM comment_reactions WHERE user_id = :u AND comment_id IN"
                " (SELECT id FROM comments WHERE team_id = :t)"
            ),
            u,
        )
    ).rowcount or 0
    mentions = (
        await db.execute(
            text(
                "DELETE FROM comment_mentions WHERE mentioned_user_id = :u AND comment_id IN"
                " (SELECT id FROM comments WHERE team_id = :t)"
            ),
            u,
        )
    ).rowcount or 0
    docs = 0
    for chunk in _chunks(ids):
        docs += (
            await db.execute(
                search._stmt(
                    "DELETE FROM search_documents WHERE team_id = :t AND kind = 'comment'"
                    " AND source_id IN :ids",
                    {"ids": chunk},
                ),
                {"t": team, "ids": chunk},
            )
        ).rowcount or 0
    appr = [
        r.id
        for r in await search.rows(
            db,
            "SELECT id FROM high_impact_approvals WHERE team_id = :t AND requester_user_id = :u",
            u,
        )
    ]
    for chunk in _chunks(appr):
        docs += (
            await db.execute(
                search._stmt(
                    "DELETE FROM search_documents WHERE team_id = :t AND kind = 'approval'"
                    " AND source_id IN :ids",
                    {"ids": chunk},
                ),
                {"t": team, "ids": chunk},
            )
        ).rowcount or 0
    like = f"%{search._esc_like(json.dumps(subject))}%"
    docs += (
        await db.execute(
            text(
                "DELETE FROM search_documents WHERE team_id = :t AND kind = 'event' AND"
                " participant_ids LIKE :p ESCAPE '!'"
            ),
            {"t": team, "p": like},
        )
    ).rowcount or 0
    for d in await search.rows(
        db,
        "SELECT id, participant_ids FROM search_documents WHERE team_id = :t AND kind IN"
        " ('run', 'session') AND participant_ids LIKE :p ESCAPE '!'",
        {"t": team, "p": like},
    ):
        left = [x for x in json.loads(d.participant_ids) if x != subject]
        await db.execute(
            text("UPDATE search_documents SET participant_ids = :l WHERE id = :i"),
            {"l": json.dumps(left), "i": d.id},
        )
    files: list[str] = []
    for j in await search.rows(
        db,
        "SELECT id, kind, scope_kind, scope_id, requested_by, storage_ref FROM export_jobs"
        " WHERE team_id = :t AND state = 'completed' AND kind <> 'audit_evidence'",
        {"t": team},
    ):
        if j.requested_by == subject or await _touches(db, team, subject, j):
            await db.execute(
                text("UPDATE export_jobs SET state = 'deleted' WHERE id = :i"), {"i": j.id}
            )
            if j.storage_ref:
                files.append(j.storage_ref)
    counts = {
        "commentsTombstoned": len(live),
        "commentRevisionsBlanked": blanked,
        "commentRevisionsRetained": kept,
        "reactions": reactions,
        "mentions": mentions,
        "searchDocuments": docs,
        "exportFiles": len(files),
    }
    return counts, files


async def _touches(db: Any, team: str, subject: str, job: Any) -> bool:
    runs = (
        "= :s"
        if job.scope_kind == "run"
        else ("IN (SELECT run_id FROM session_runs WHERE session_id = :s)")
    )
    sess = " OR (scope_kind = 'session' AND scope_id = :s)" if job.scope_kind == "session" else ""
    got = await search.rows(
        db,
        "SELECT 1 WHERE EXISTS (SELECT 1 FROM comments WHERE team_id = :t AND"
        f" author_user_id = :u AND ((scope_kind = 'run' AND scope_id {runs}){sess}))"
        f" OR EXISTS (SELECT 1 FROM run_collab_events WHERE team_id = :t AND actor_user_id = :u"
        f" AND run_id {runs}) OR EXISTS (SELECT 1 FROM high_impact_approvals WHERE team_id = :t"
        f" AND requester_user_id = :u AND run_id {runs})",
        {"t": team, "u": subject, "s": job.scope_id},
    )
    return bool(got)


async def decide_deletion(
    f: Any, p: Principal, rid: str, decision: Literal["approve", "reject"], note: str | None
) -> dict[str, Any]:
    async with f() as db:
        if p.role != Role.ADMIN:
            await search.audit(
                db,
                p,
                "authz.denied",
                "deletion_request",
                rid,
                {"reason": "ROLE_DENIED", "attempt": f"deletion-requests/{decision}"},
            )
            await db.commit()
            raise search.envelope(403, "ROLE_DENIED", "only team admins decide deletion requests")
        got = await search.rows(
            db,
            f"SELECT {_REQ_COLS} FROM deletion_requests WHERE id = :i AND team_id = :t",
            {"i": rid, "t": p.teamId},
        )
        if not got:
            raise search.envelope(404, "DELETION_NOT_FOUND", "deletion request not found")
        r = got[0]
        if r.subject_user_id == p.userId and not collab._self_approval_allowed():
            raise search.envelope(
                403, "SELF_DECISION", "another admin must decide your own deletion request"
            )
        if decision == "reject":
            note = (note or "").strip()
            if len(note) < 3:
                raise search.envelope(422, "REASON_REQUIRED", "a rejection needs a reason")
            res = await db.execute(
                text(
                    "UPDATE deletion_requests SET state = 'rejected', decided_by = :u,"
                    " decided_at = :c, decision_note = :n WHERE id = :i AND state = 'pending'"
                ),
                {"u": p.userId, "c": search.now(), "n": note, "i": rid},
            )
            if res.rowcount != 1:
                await db.rollback()
                raise search.envelope(409, "NOT_PENDING", f"request is {r.state}")
            await search.audit(
                db,
                p,
                "deletion.rejected",
                "deletion_request",
                rid,
                {
                    "requestId": rid,
                    "subjectUserId": r.subject_user_id,
                    "before": {"state": "pending"},
                    "after": {"state": "rejected"},
                },
            )
            await db.commit()
        else:
            if r.state == "executed":
                return {**_req_view(r), "duplicate": True}
            if r.state == "rejected":
                raise search.envelope(409, "NOT_PENDING", "request was rejected")
            if r.state == "pending":
                res = await db.execute(
                    text(
                        "UPDATE deletion_requests SET state = 'approved', decided_by = :u,"
                        " decided_at = :c, decision_note = :n WHERE id = :i AND"
                        " state = 'pending'"
                    ),
                    {"u": p.userId, "c": search.now(), "n": (note or "").strip() or None, "i": rid},
                )
                if res.rowcount != 1:
                    await db.rollback()
                    raise search.envelope(409, "NOT_PENDING", "request was decided meanwhile")
                await search.audit(
                    db,
                    p,
                    "deletion.approved",
                    "deletion_request",
                    rid,
                    {
                        "requestId": rid,
                        "subjectUserId": r.subject_user_id,
                        "before": {"state": "pending"},
                        "after": {"state": "approved"},
                    },
                )
                await db.commit()
            # state is now 'approved' (also: a retry after a crash between approval and execution)
            counts, files = await _execute(db, p.teamId, r.subject_user_id, p.userId)
            result = {"deleted": counts, "retained": RETAINED}
            await db.execute(
                text(
                    "UPDATE deletion_requests SET state = 'executed', executed_at = :c,"
                    " result = :r WHERE id = :i AND state = 'approved'"
                ),
                {"c": search.now(), "r": _dumps(result), "i": rid},
            )
            await search.audit(
                db,
                p,
                "deletion.executed",
                "deletion_request",
                rid,
                {
                    "requestId": rid,
                    "subjectUserId": r.subject_user_id,
                    "counts": counts,
                    "before": {"state": "approved"},
                    "after": {"state": "executed"},
                },
            )
            await db.commit()
            for ref in files:
                await asyncio.to_thread(_remove_file, ref)
            if files:
                await db.execute(
                    search._stmt(
                        "UPDATE export_jobs SET storage_ref = NULL WHERE team_id = :t"
                        " AND storage_ref IN :refs",
                        {"refs": files},
                    ),
                    {"t": p.teamId, "refs": files},
                )
                await db.commit()
        return _req_view(
            (
                await search.rows(
                    db, f"SELECT {_REQ_COLS} FROM deletion_requests WHERE id = :i", {"i": rid}
                )
            )[0]
        )


# ------------------------------------------------------------------------ routes


def _f() -> Any:
    f = _factory()
    if f is None:
        raise search.envelope(503, "EXPORT_UNAVAILABLE_DB", "no database wired")
    return f


@router.post("/exports", status_code=201)
async def exports_create(req: ExportBody, actor: _api.RequirePrincipal, response: Response) -> dict:
    out, created = await create_export(_f(), actor, req)
    if not created:
        response.status_code = 200
        out = {**out, "duplicate": True}
    return out


@router.get("/exports")
async def exports_list(
    actor: _api.RequirePrincipal, limit: Annotated[int, Query(ge=1, le=100)] = 25
) -> dict:
    return await list_exports(_f(), actor, limit)


@router.get("/exports/{exportId}")
async def exports_get(exportId: str, actor: _api.RequirePrincipal) -> dict:
    return await get_export(_f(), actor, exportId)


@router.get("/exports/{exportId}/download")
async def exports_download(exportId: str, actor: _api.RequirePrincipal) -> Response:
    data, job = await download(_f(), actor, exportId)
    return Response(
        content=data,
        media_type=_MEDIA[job.format],
        headers={
            "Content-Disposition": (
                f'attachment; filename="harness-{job.kind}-{job.id}.{job.format}"'
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "X-Export-Sha256": job.sha256,
        },
    )


@router.post("/deletion-requests", status_code=201)
async def deletion_create(
    req: DeletionBody, actor: _api.RequirePrincipal, response: Response
) -> dict:
    out, created = await create_deletion_request(_f(), actor, req)
    if not created:
        response.status_code = 200
        out = {**out, "duplicate": True}
    return out


@router.get("/deletion-requests")
async def deletion_list(
    actor: _api.RequirePrincipal,
    state: Literal["pending", "approved", "rejected", "executed"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    return await list_deletion_requests(_f(), actor, state, limit)


@router.post("/deletion-requests/{requestId}/approve")
async def deletion_approve(
    requestId: str, actor: _api.RequirePrincipal, req: DecisionBody | None = None
) -> dict:
    return await decide_deletion(_f(), actor, requestId, "approve", req.note if req else None)


@router.post("/deletion-requests/{requestId}/reject")
async def deletion_reject(
    requestId: str, actor: _api.RequirePrincipal, req: DecisionBody | None = None
) -> dict:
    return await decide_deletion(_f(), actor, requestId, "reject", req.note if req else None)
