"""Collaborative discussion on runs and sessions (part 43). Rules and limits: docs/discussion.md.

Comments are a human conversation attached to a scope (``run`` or ``session``). They are
never agent input: the only way a comment reaches an agent is ``promote``, an explicit
action by a user who may steer, which goes through ``collab.redirect`` (the same path as
``POST runs/{id}/redirect``, so the control-lease queue applies) and records the link.

Identity is always the authenticated Principal. A run scope is guarded by the shared run
ownership dependency (``api._authz_dep``); a session scope by team + active membership (team
admins see every session of their team). A foreign or unknown scope answers the same 404
envelope. Raw SQL only (``text()``): sections may import only ``harness_contracts``.
"""

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from harness_contracts.roles import READ_ONLY, Principal, Role
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api as _api
from harness_orchestrator import collab

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["discussion"])

_service: Any = None

MAX_BODY = collab.MAX_TEXT
MAX_DEPTH = 2  # top-level = 0, so three levels in all
MAX_MENTIONS = 10
MAX_EXPORT = 10_000
REACTIONS = ("thumbs_up", "thumbs_down", "heart", "eyes", "celebrate", "question", "check")
REF_KINDS = ("plan", "file", "diff", "approval", "event")
SESSION_WRITABLE = ("active", "paused", "completed")
DEFAULTS = {"editWindowSeconds": 900, "retentionDays": 365, "auditSensitive": "auto"}
SYSTEM_ACTOR = "system:discussion-retention"
_TARGET = {"run": "agent_run", "session": "session", "team": "team"}

RefKind = Literal["plan", "file", "diff", "approval", "event"]
ReactionCode = Literal[
    "thumbs_up", "thumbs_down", "heart", "eyes", "celebrate", "question", "check"]

COLS = (
    "id, scope_kind, scope_id, team_id, author_user_id, author_name_snapshot, parent_id, depth,"
    " body, state, ref_kind, ref_id, ref_pos, created_at, edited_at, version, moderated_by,"
    " moderated_at, moderation_reason, deleted_at, deleted_by, purged_at"
)

# (principal, scope, scope id, "METHOD route") of the request in flight, for denial audit.
_ctx: ContextVar[tuple[Principal, str, str, str] | None] = ContextVar(
    "_discussion_ctx", default=None)


def configure_discussion(service: Any) -> None:
    """Deploy-time wiring (like configure_control): the service owning the DB factory."""
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


class DiscussionError(collab.CollabError):
    """CollabError that also carries structured data for the HTTP body."""

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(status, code, message)
        self.extra = extra or {}


def _q(sql: str, *expanding: str) -> Any:
    stmt = text(sql)
    if not expanding:
        return stmt
    return stmt.bindparams(*[bindparam(n, expanding=True) for n in expanding])


# ----------------------------------------------------------------------- guards

_RUN_GUARD = _api._authz_dep("read", audit=True)
_ROLE_OK = {
    "read": lambda r: True,
    "comment": lambda r: r != Role.GUEST,
    "steer": lambda r: r not in READ_ONLY,
    "export": lambda r: r in (Role.ADMIN, Role.ENGINEER),
    "admin": lambda r: r == Role.ADMIN,
}


def _factory() -> Any:
    svc = _service if _service is not None else _api._service
    return getattr(svc, "session_factory", None)


def _envelope(status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> Any:
    body = {"error": {"code": code, "message": message}, **(extra or {})}
    return HTTPException(status, detail=body)


async def _deny_audit(
    p: Principal, scope: str, sid: str, route: str, code: str, status: int
) -> None:
    """Every refused request by an authenticated principal leaves one authz.denied row."""
    factory = _factory()
    if factory is None:
        return
    try:
        async with factory() as db:
            await _audit(db, p, "authz.denied", scope, sid,
                         {"reason": code, "status": status, "attempt": route})
            await db.commit()
    except Exception:  # noqa: BLE001 - audit must never mask the denial
        logger.exception("discussion denial audit failed scope=%s/%s", scope, sid)


async def _session_access(p: Principal, sid: str) -> None:
    """Team + active membership (admins: every session of their team). Anything else,
    including unknown ids and deleted sessions for non-admins, is the same 404."""
    factory = _factory()
    if factory is None:
        raise _envelope(503, "DISCUSSION_UNAVAILABLE", "no database wired")
    admin = p.role == Role.ADMIN
    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT state FROM collab_sessions WHERE id = :s AND team_id = :t"),
                {"s": sid, "t": p.teamId},
            )
        ).first()
        ok = row is not None and (admin or row.state != "deleted")
        if ok and not admin:
            ok = (
                await db.execute(
                    text("SELECT 1 FROM session_members WHERE session_id = :s AND user_id = :u"
                         " AND status = 'active'"),
                    {"s": sid, "u": p.userId},
                )
            ).first() is not None
    if not ok:
        raise _envelope(404, "NOT_FOUND", "session not found")


def _guard(scope: str, perm: str) -> Any:
    async def dep(request: Request, scopeId: str, p: _api.RequirePrincipal) -> Principal:
        route = f"{request.method} {getattr(request.scope.get('route'), 'path', request.url.path)}"
        _ctx.set((p, scope, scopeId, route))
        if not _ROLE_OK[perm](p.role):  # role gate first: no new existence oracle for low roles
            _api._denial_ctx.set((p, scopeId, route))
            await _deny_audit(p, scope, scopeId, route, "ROLE_DENIED", 403)
            raise _envelope(403, "ROLE_DENIED", f"role may not {perm} here")
        if scope == "run":
            await _RUN_GUARD(request, scopeId, p)
            _api._pilot_gate(p)
            if perm != "read":
                await _api._persist_run(scopeId)
        else:
            await _session_access(p, scopeId)
        return p

    return dep


async def _call(call: Any) -> Any:
    factory = _factory()
    if factory is None:
        raise _envelope(503, "DISCUSSION_UNAVAILABLE", "no database wired")
    try:
        return await call(factory)
    except collab.CollabError as e:
        ctx = _ctx.get()
        if e.status == 403 and not e.audited and ctx is not None:
            await _deny_audit(ctx[0], ctx[1], ctx[2], ctx[3], e.code, e.status)
        raise _envelope(e.status, e.code, str(e), getattr(e, "extra", None)) from e


# ------------------------------------------------------------ audit, events, notify


async def _audit(
    db: Any, p: Principal, action: str, scope: str, sid: str, detail: dict[str, Any]
) -> None:
    """Append an audit row in the caller's transaction. Detail is ids and states only:
    never comment text."""
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
            "a": member.id if member else None, "act": action, "tt": _TARGET[scope], "tid": sid,
            "d": json.dumps({"userId": p.userId, "teamId": p.teamId, **collab._scrub(detail)},
                            default=str),
            "ts": _now(),
        },
    )


async def _emit(db: Any, p: Principal, scope: str, sid: str, kind: str, cid: str) -> None:
    """Run scope: a durable collaboration event (ids only, never the text) so peers refresh.
    Sessions have no event stream; their clients poll the list."""
    if scope == "run":
        await collab._event(db, p, sid, kind, {"commentId": cid}, None)


async def _notify_mentions(
    db: Any, p: Principal, scope: str, sid: str, cid: str, users: list[str], workspace: Any = None
) -> None:
    """Lane N's inbox (``notify.emit``) when installed; otherwise nothing is sent. The inbox
    row carries ids only (no text, no session title). A failure never fails the comment."""
    if not users:
        return
    try:
        from harness_orchestrator import notify  # lane N; lazy, may not exist
    except ImportError:
        return
    for u in users:
        try:
            async with db.begin_nested():  # savepoint: a failed insert must not poison the txn
                await notify.emit(
                    db, team_id=p.teamId, user_id=u, kind="mention", source_kind="comment",
                    source_id=cid, run_id=sid if scope == "run" else None,
                    session_id=sid if scope == "session" else None,
                    workspace_id=workspace, dedupe_key=f"mention:{cid}:{u}",
                )
        except Exception:  # noqa: BLE001
            logger.warning("mention notification failed comment=%s", cid, exc_info=True)


# ----------------------------------------------------------------- settings, scope


async def settings(db: Any, team_id: str) -> dict[str, Any]:
    row = (
        await db.execute(
            text("SELECT edit_window_seconds, retention_days, audit_sensitive, updated_by,"
                 " updated_at FROM discussion_settings WHERE team_id = :t"),
            {"t": team_id},
        )
    ).first()
    if row is None:
        return {**DEFAULTS, "updatedBy": None, "updatedAt": None, "isDefault": True}
    return {
        "editWindowSeconds": int(row.edit_window_seconds), "retentionDays": int(row.retention_days),
        "auditSensitive": row.audit_sensitive, "updatedBy": row.updated_by,
        "updatedAt": _iso(row.updated_at), "isDefault": False,
    }


def _cutoff(cfg: dict[str, Any], now: datetime) -> datetime | None:
    days = cfg["retentionDays"]
    return now - timedelta(days=days) if days > 0 else None


def _purged(row: Any, cutoff: datetime | None) -> bool:
    """Purged for real, or past retention (enforced at read time even before a purge job runs)."""
    return row.purged_at is not None or (cutoff is not None and _dt(row.created_at) < cutoff)


async def _writable(db: Any, p: Principal, scope: str, sid: str) -> Any:
    """A session accepts new discussion while active, paused or completed."""
    if scope != "session":
        return None
    row = (
        await db.execute(
            text("SELECT state, workspace_id FROM collab_sessions WHERE id = :s AND team_id = :t"),
            {"s": sid, "t": p.teamId},
        )
    ).first()
    if row is None or row.state not in SESSION_WRITABLE:
        raise DiscussionError(409, "SCOPE_READ_ONLY", "this session no longer accepts discussion")
    return row.workspace_id


async def _sensitive(db: Any, p: Principal, cfg: dict[str, Any], scope: str, sid: str) -> bool:
    """Audit-sensitive scope: the team says so, or the run (or a run of the session) has had a
    high-impact approval. There a deleted comment keeps its revisions."""
    if cfg["auditSensitive"] == "always":
        return True
    sql = (
        "SELECT 1 FROM high_impact_approvals WHERE team_id = :t AND run_id = :s LIMIT 1"
        if scope == "run" else
        "SELECT 1 FROM high_impact_approvals h JOIN session_runs sr ON sr.run_id = h.run_id"
        " WHERE h.team_id = :t AND sr.session_id = :s LIMIT 1"
    )
    return (await db.execute(text(sql), {"t": p.teamId, "s": sid})).first() is not None


async def _scope_runs(db: Any, p: Principal, scope: str, sid: str) -> set[str]:
    if scope == "run":
        return {sid}
    rows = (
        await db.execute(
            text("SELECT sr.run_id FROM session_runs sr JOIN agent_runs r ON r.id = sr.run_id"
                 " JOIN projects pr ON pr.id = r.project_id"
                 " WHERE sr.session_id = :s AND pr.team_id = :t"),
            {"s": sid, "t": p.teamId},
        )
    ).all()
    return {r.run_id for r in rows}


async def _name(db: Any, team_id: str, user_id: str) -> str:
    row = (
        await db.execute(
            text("SELECT display_name FROM team_members WHERE team_id = :t AND user_id = :u"
                 " LIMIT 1"),
            {"t": team_id, "u": user_id},
        )
    ).first()
    return (row.display_name if row and row.display_name else None) or "A participant"


async def _get(db: Any, p: Principal, scope: str, sid: str, cid: str) -> Any:
    row = (
        await db.execute(
            text(f"SELECT {COLS} FROM comments WHERE id = :i AND scope_kind = :k"
                 " AND scope_id = :s AND team_id = :t"),
            {"i": cid, "k": scope, "s": sid, "t": p.teamId},
        )
    ).first()
    if row is None:
        raise DiscussionError(404, "COMMENT_NOT_FOUND", "no such comment")
    return row


# ------------------------------------------------------------ references, mentions


async def _ref_ok(
    db: Any, p: Principal, runs: set[str], kind: str, rid: str, pos: int | None
) -> bool:
    """Is the reference something this scope may point at? Re-evaluated on every read."""
    if kind == "file":
        return True  # a path label: authorised by the scope itself, no file store here
    if kind == "event":
        if rid not in runs or pos is None:
            return False
        sql = "SELECT 1 FROM run_collab_events WHERE run_id = :r AND seq = :p AND team_id = :t"
        got = await db.execute(text(sql), {"r": rid, "p": pos, "t": p.teamId})
        return got.first() is not None
    if kind == "plan":
        row = (await db.execute(text("SELECT run_id FROM agent_plans WHERE id = :i"),
                                {"i": rid})).first()
        return row is not None and row.run_id in runs
    row = (
        await db.execute(
            text("SELECT run_id, diff FROM high_impact_approvals WHERE id = :i AND team_id = :t"),
            {"i": rid, "t": p.teamId},
        )
    ).first()
    return row is not None and row.run_id in runs and (kind == "approval" or bool(row.diff))


async def _check_ref(
    db: Any, p: Principal, scope: str, sid: str, kind: str | None, rid: str | None, pos: int | None
) -> None:
    if kind is None:
        if rid is not None or pos is not None:
            raise DiscussionError(422, "BAD_REF", "refId/refPos need a refKind")
        return
    if not rid:
        raise DiscussionError(422, "BAD_REF", "a reference needs refId")
    if kind == "file":
        collab.validate_paths([rid])
    if not await _ref_ok(db, p, await _scope_runs(db, p, scope, sid), kind, rid, pos):
        raise DiscussionError(422, "BAD_REF", "that reference is not available in this discussion")


async def _valid_mentions(db: Any, p: Principal, scope: str, sid: str, ids: list[str]) -> list[str]:
    """Only people who can already see the scope. Unknown, foreign and not-allowed users are
    indistinguishable (silently dropped, counted once), so a mention is no existence oracle."""
    ids = [u for u in dict.fromkeys(ids) if u != p.userId]
    if not ids:
        return []
    if scope == "run":
        sql = "SELECT user_id FROM team_members WHERE team_id = :t AND user_id IN :ids"
        args: dict[str, Any] = {"t": p.teamId, "ids": ids}
    else:
        sql = (
            "SELECT tm.user_id FROM team_members tm WHERE tm.team_id = :t AND tm.user_id IN :ids"
            " AND (lower(tm.role) = 'admin' OR EXISTS (SELECT 1 FROM session_members m"
            " WHERE m.session_id = :s AND m.user_id = tm.user_id AND m.status = 'active'))"
        )
        args = {"t": p.teamId, "ids": ids, "s": sid}
    ok = {r.user_id for r in (await db.execute(_q(sql, "ids"), args)).all()}
    return [u for u in ids if u in ok]


# --------------------------------------------------------------------- rendering


def _body_for(row: Any, p: Principal, gone: bool) -> str | None:
    if gone or row.state == "deleted":
        return None
    if row.state == "hidden" and not (p.role == Role.ADMIN or row.author_user_id == p.userId):
        return None
    return row.body


async def _render(
    db: Any, p: Principal, scope: str, sid: str, rows: list[Any], cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    if not rows:
        return []
    now, admin = _now(), p.role == Role.ADMIN
    cutoff = _cutoff(cfg, now)
    ids = [r.id for r in rows]
    react: dict[str, dict[str, Any]] = {}
    for r in (
        await db.execute(
            _q("SELECT comment_id, reaction, COUNT(*) AS n,"
               " SUM(CASE WHEN user_id = :u THEN 1 ELSE 0 END) AS mine FROM comment_reactions"
               " WHERE comment_id IN :ids GROUP BY comment_id, reaction", "ids"),
            {"u": p.userId, "ids": ids},
        )
    ).all():
        react.setdefault(r.comment_id, {})[r.reaction] = {"count": int(r.n), "mine": bool(r.mine)}
    ment: dict[str, list[dict[str, Any]]] = {}
    for r in (
        await db.execute(
            _q("SELECT m.comment_id, m.mentioned_user_id AS uid, tm.display_name AS name"
               " FROM comment_mentions m JOIN team_members tm"
               " ON tm.user_id = m.mentioned_user_id AND tm.team_id = :t"
               " WHERE m.comment_id IN :ids ORDER BY m.created_at, m.mentioned_user_id", "ids"),
            {"t": p.teamId, "ids": ids},
        )
    ).all():
        ment.setdefault(r.comment_id, []).append(
            {"userId": r.uid, "name": r.name or "A participant"})
    promo = {
        r.comment_id: r
        for r in (
            await db.execute(
                _q("SELECT comment_id, run_id, promoted_by, promoted_at, comment_version,"
                   " event_seq, instruction_id FROM comment_promotions WHERE comment_id IN :ids",
                   "ids"),
                {"ids": ids},
            )
        ).all()
    }
    runs: set[str] | None = None
    out = []
    for r in rows:
        gone = _purged(r, cutoff)
        own = r.author_user_id == p.userId
        visible = r.state == "visible" and not gone
        ref = None
        if r.ref_kind:
            if runs is None:
                runs = await _scope_runs(db, p, scope, sid)
            ok = await _ref_ok(db, p, runs, r.ref_kind, r.ref_id, r.ref_pos)
            ref = ({"kind": r.ref_kind, "id": r.ref_id, "pos": r.ref_pos, "available": True}
                   if ok else {"kind": r.ref_kind, "available": False})
        window = cfg["editWindowSeconds"]
        until = _dt(r.created_at) + timedelta(seconds=window) if window else None
        pr = promo.get(r.id)
        out.append({
            "id": r.id, "scopeKind": r.scope_kind, "scopeId": r.scope_id, "parentId": r.parent_id,
            "depth": r.depth,
            "author": {"userId": r.author_user_id, "name": r.author_name_snapshot},
            "body": _body_for(r, p, gone), "state": r.state, "purged": gone,
            "createdAt": _iso(r.created_at), "editedAt": _iso(r.edited_at),
            "version": r.version, "edited": r.version > 1, "ref": ref,
            "reactions": react.get(r.id, {}), "mentions": ment.get(r.id, []),
            "moderation": (
                {"state": r.state, "by": r.moderated_by, "at": _iso(r.moderated_at),
                 "reason": r.moderation_reason} if r.moderated_by else None),
            "deleted": ({"at": _iso(r.deleted_at), "by": r.deleted_by}
                        if r.state == "deleted" else None),
            "promotion": ({"runId": pr.run_id, "by": pr.promoted_by, "at": _iso(pr.promoted_at),
                           "commentVersion": pr.comment_version, "seq": pr.event_seq,
                           "instructionId": pr.instruction_id} if pr else None),
            "editableUntil": _iso(until) if visible and until else None,
            "can": {
                "edit": bool(own and visible and until and now <= until and p.role != Role.GUEST),
                "delete": own and r.state != "deleted" and not gone,
                "react": visible and p.role != Role.GUEST,
                "reply": visible and r.depth < MAX_DEPTH and p.role != Role.GUEST,
                "promote": visible and p.role not in READ_ONLY and pr is None,
                "moderate": admin and r.state != "deleted" and not gone,
            },
        })
    return out


def _policy(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"editWindowSeconds": cfg["editWindowSeconds"], "retentionDays": cfg["retentionDays"],
            "auditSensitive": cfg["auditSensitive"], "maxReplyDepth": MAX_DEPTH,
            "maxBodyChars": MAX_BODY, "reactions": list(REACTIONS), "refKinds": list(REF_KINDS)}


# ------------------------------------------------------------------------ list


async def list_comments(
    factory: Any, p: Principal, scope: str, sid: str, limit: int, after: str | None
) -> dict[str, Any]:
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        sql = (f"SELECT {COLS} FROM comments WHERE scope_kind = :k AND scope_id = :s"
               " AND team_id = :t")
        args: dict[str, Any] = {"k": scope, "s": sid, "t": p.teamId, "n": limit + 1}
        if after:
            if (await db.execute(
                text("SELECT 1 FROM comments WHERE id = :c AND scope_kind = :k AND scope_id = :s"
                     " AND team_id = :t"),
                {"c": after, "k": scope, "s": sid, "t": p.teamId})).first() is None:
                raise DiscussionError(422, "BAD_CURSOR", "unknown cursor")
            sql += (" AND (created_at > (SELECT created_at FROM comments WHERE id = :c) OR"
                    " (created_at = (SELECT created_at FROM comments WHERE id = :c) AND id > :c))")
            args["c"] = after
        rows = (await db.execute(text(sql + " ORDER BY created_at, id LIMIT :n"), args)).all()
        page = rows[:limit]
        return {
            "scope": {"kind": scope, "id": sid},
            "comments": await _render(db, p, scope, sid, page, cfg),
            "nextCursor": page[-1].id if len(rows) > limit else None,
            "policy": _policy(cfg),
        }


# ----------------------------------------------------------------------- create


async def create(
    factory: Any, p: Principal, scope: str, sid: str, req: "CreateBody"
) -> tuple[dict[str, Any], bool]:
    body = req.body.strip()
    if not body:
        raise DiscussionError(422, "BAD_BODY", f"body must be 1..{MAX_BODY} characters")
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        workspace = await _writable(db, p, scope, sid)

        async def existing() -> Any:
            return (
                await db.execute(
                    text(f"SELECT {COLS} FROM comments WHERE scope_kind = :k AND scope_id = :s"
                         " AND author_user_id = :u AND request_id = :q"),
                    {"k": scope, "s": sid, "u": p.userId, "q": req.requestId},
                )
            ).first()

        async def done(row: Any, dup: bool, ignored: int = 0) -> tuple[dict[str, Any], bool]:
            view = (await _render(db, p, scope, sid, [row], cfg))[0]
            return {**view, "duplicate": dup, "ignoredMentions": ignored}, not dup

        if req.requestId and (dup := await existing()) is not None:
            return await done(dup, True)
        depth = 0
        if req.parentId:
            parent = (
                await db.execute(
                    text("SELECT depth, state, purged_at, created_at FROM comments WHERE id = :i"
                         " AND scope_kind = :k AND scope_id = :s AND team_id = :t"),
                    {"i": req.parentId, "k": scope, "s": sid, "t": p.teamId},
                )
            ).first()
            if parent is None:
                raise DiscussionError(422, "BAD_PARENT", "no such comment to reply to")
            if parent.state != "visible" or _purged(parent, _cutoff(cfg, _now())):
                raise DiscussionError(
                    409, "PARENT_UNAVAILABLE", "that comment cannot be replied to")
            depth = parent.depth + 1
            if depth > MAX_DEPTH:
                raise DiscussionError(
                    422, "REPLY_TOO_DEEP", f"replies nest at most {MAX_DEPTH} deep")
        await _check_ref(db, p, scope, sid, req.refKind, req.refId, req.refPos)
        wanted = [u for u in dict.fromkeys(req.mentions) if u != p.userId]
        users = await _valid_mentions(db, p, scope, sid, req.mentions)
        cid, now = str(uuid.uuid4()), _now()
        try:
            await db.execute(
                text(
                    "INSERT INTO comments (id, scope_kind, scope_id, team_id, author_user_id,"
                    " author_name_snapshot, parent_id, depth, body, state, ref_kind, ref_id,"
                    " ref_pos, request_id, created_at, version) VALUES (:i, :k, :s, :t, :u, :n,"
                    " :pa, :d, :b, 'visible', :rk, :ri, :rp, :q, :now, 1)"
                ),
                {"i": cid, "k": scope, "s": sid, "t": p.teamId, "u": p.userId,
                 "n": await _name(db, p.teamId, p.userId), "pa": req.parentId, "d": depth,
                 "b": body, "rk": req.refKind, "ri": req.refId, "rp": req.refPos,
                 "q": req.requestId, "now": now},
            )
        except IntegrityError:  # a simultaneous retry with the same requestId
            await db.rollback()
            if req.requestId and (dup := await existing()) is not None:
                return await done(dup, True)
            raise
        await db.execute(
            text("INSERT INTO comment_revisions (comment_id, version, body, edited_by, edited_at)"
                 " VALUES (:c, 1, :b, :u, :now)"),
            {"c": cid, "b": body, "u": p.userId, "now": now},
        )
        for u in users:
            await db.execute(
                text("INSERT INTO comment_mentions (comment_id, mentioned_user_id, created_at)"
                     " VALUES (:c, :u, :now)"),
                {"c": cid, "u": u, "now": now},
            )
        await _emit(db, p, scope, sid, "comment.created", cid)
        await _audit(db, p, "comment.created", scope, sid, {
            "commentId": cid, "scopeKind": scope, "scopeId": sid, "parentId": req.parentId,
            "refKind": req.refKind, "mentionCount": len(users), "after": {"state": "visible"}})
        await _notify_mentions(db, p, scope, sid, cid, users, workspace)
        await db.commit()
        row = await _get(db, p, scope, sid, cid)
        return await done(row, False, len(wanted) - len(users))


# ------------------------------------------------------------------------- edit


async def edit(
    factory: Any, p: Principal, scope: str, sid: str, cid: str, req: "EditBody"
) -> dict[str, Any]:
    body = req.body.strip()
    if not body:
        raise DiscussionError(422, "BAD_BODY", f"body must be 1..{MAX_BODY} characters")
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        workspace = await _writable(db, p, scope, sid)
        row = await _get(db, p, scope, sid, cid)
        if row.author_user_id != p.userId:
            raise collab.CollabError(403, "NOT_AUTHOR", "only the author may edit a comment")
        if row.state != "visible" or _purged(row, _cutoff(cfg, _now())):
            raise DiscussionError(
                409, "COMMENT_UNAVAILABLE", "this comment can no longer be edited")
        if row.version != req.expectedVersion:
            raise DiscussionError(409, "STALE_COMMENT_VERSION", "the comment changed; reload it",
                                  {"currentVersion": row.version})
        until = _dt(row.created_at) + timedelta(seconds=cfg["editWindowSeconds"])
        now = _now()
        if now > until:
            raise DiscussionError(409, "EDIT_WINDOW_CLOSED", "the edit window has closed",
                                  {"editWindowSeconds": cfg["editWindowSeconds"],
                                   "closedAt": _iso(until)})
        new_users: list[str] = []
        if req.mentions is not None:
            valid = await _valid_mentions(db, p, scope, sid, req.mentions)
            old = {r.mentioned_user_id for r in (await db.execute(
                text("SELECT mentioned_user_id FROM comment_mentions WHERE comment_id = :c"),
                {"c": cid})).all()}
            new_users = [u for u in valid if u not in old]
            for u in old - set(valid):
                await db.execute(
                    text("DELETE FROM comment_mentions WHERE comment_id = :c"
                         " AND mentioned_user_id = :u"), {"c": cid, "u": u})
            for u in new_users:
                await db.execute(
                    text("INSERT INTO comment_mentions (comment_id, mentioned_user_id, created_at)"
                         " VALUES (:c, :u, :now)"), {"c": cid, "u": u, "now": now})
            changed_mentions = bool(new_users or (old - set(valid)))
        else:
            changed_mentions = False
        if body == row.body and not changed_mentions:
            view = (await _render(db, p, scope, sid, [row], cfg))[0]
            return {**view, "changed": False}
        if body != row.body:
            res = await db.execute(
                text("UPDATE comments SET body = :b, version = version + 1, edited_at = :now"
                     " WHERE id = :i AND version = :v AND state = 'visible'"),
                {"b": body, "now": now, "i": cid, "v": row.version},
            )
            if res.rowcount != 1:
                await db.rollback()
                raise DiscussionError(
                    409, "STALE_COMMENT_VERSION", "the comment changed; reload it")
            await db.execute(
                text("INSERT INTO comment_revisions (comment_id, version, body, edited_by,"
                     " edited_at) VALUES (:c, :v, :b, :u, :now)"),
                {"c": cid, "v": row.version + 1, "b": body, "u": p.userId, "now": now},
            )
        await _emit(db, p, scope, sid, "comment.edited", cid)
        await _audit(db, p, "comment.edited", scope, sid, {
            "commentId": cid, "scopeKind": scope, "scopeId": sid,
            "before": {"version": row.version},
            "after": {"version": row.version + (1 if body != row.body else 0)}})
        await _notify_mentions(db, p, scope, sid, cid, new_users, workspace)
        await db.commit()
        return {**(await _render(db, p, scope, sid, [await _get(db, p, scope, sid, cid)], cfg))[0],
                "changed": True}


# ----------------------------------------------- delete (tombstone) and moderation


async def _tombstone(
    db: Any, p: Principal, cfg: dict[str, Any], scope: str, sid: str, row: Any, via: str,
    reason: str | None,
) -> bool:
    """state -> deleted, body gone from the comment. In an audit-sensitive scope the revisions
    keep their text (admins can read them); elsewhere the revisions are blanked too. Always
    kept: the row, author, timestamps, moderation fields, reactions, mentions, audit."""
    now = _now()
    mod = (", moderated_by = :u, moderated_at = :now, moderation_reason = :w"
           if via == "moderator" else "")
    args = {"now": now, "u": p.userId, "i": row.id, "prev": row.state}
    if mod:
        args["w"] = reason
    res = await db.execute(
        text("UPDATE comments SET state = 'deleted', body = '', deleted_at = :now,"
             " deleted_by = :u" + mod + " WHERE id = :i AND state = :prev"), args)
    if res.rowcount != 1:
        return False
    keep = await _sensitive(db, p, cfg, scope, sid)
    if not keep:
        await db.execute(text("UPDATE comment_revisions SET body = '' WHERE comment_id = :c"),
                         {"c": row.id})
    await _emit(db, p, scope, sid, "comment.deleted", row.id)
    await _audit(db, p, "comment.deleted", scope, sid, {
        "commentId": row.id, "scopeKind": scope, "scopeId": sid, "via": via,
        "revisionsKept": keep, "before": {"state": row.state}, "after": {"state": "deleted"}})
    return True


async def delete(factory: Any, p: Principal, scope: str, sid: str, cid: str) -> dict[str, Any]:
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        row = await _get(db, p, scope, sid, cid)
        if row.author_user_id != p.userId:
            raise collab.CollabError(
                403, "NOT_AUTHOR", "only the author may delete a comment (admins moderate)")
        if row.state == "deleted":
            return {"id": cid, "state": "deleted", "changed": False}
        if not await _tombstone(db, p, cfg, scope, sid, row, "author", None):
            await db.rollback()
            raise DiscussionError(409, "STALE_COMMENT_VERSION", "the comment changed; retry")
        await db.commit()
        return {"id": cid, "state": "deleted", "changed": True}


async def moderate(
    factory: Any, p: Principal, scope: str, sid: str, cid: str, action: str, reason: str
) -> dict[str, Any]:
    reason = reason.strip()
    if not 3 <= len(reason) <= collab.MAX_REASON:
        raise DiscussionError(422, "REASON_REQUIRED", "a reason of 3..500 characters is required")
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        row = await _get(db, p, scope, sid, cid)
        target = {"hide": "hidden", "restore": "visible", "delete": "deleted"}[action]
        if row.state == target:
            return {"id": cid, "state": target, "changed": False}
        if row.state == "deleted":
            raise DiscussionError(409, "COMMENT_DELETED", "a deleted comment cannot be changed")
        if action == "delete":
            if not await _tombstone(db, p, cfg, scope, sid, row, "moderator", reason):
                await db.rollback()
                raise DiscussionError(409, "STALE_COMMENT_VERSION", "the comment changed; retry")
        else:
            res = await db.execute(
                text("UPDATE comments SET state = :new, moderated_by = :u, moderated_at = :now,"
                     " moderation_reason = :w WHERE id = :i AND state = :prev"),
                {"new": target, "u": p.userId, "now": _now(), "w": reason, "i": cid,
                 "prev": row.state},
            )
            if res.rowcount != 1:
                await db.rollback()
                raise DiscussionError(409, "STALE_COMMENT_VERSION", "the comment changed; retry")
            name = "comment.hidden" if action == "hide" else "comment.restored"
            await _emit(db, p, scope, sid, name, cid)
            await _audit(db, p, name, scope, sid, {
                "commentId": cid, "scopeKind": scope, "scopeId": sid,
                "before": {"state": row.state}, "after": {"state": target}})
        await db.commit()
        return {"id": cid, "state": target, "changed": True}


# --------------------------------------------------------------------- reactions


async def react(
    factory: Any, p: Principal, scope: str, sid: str, cid: str, reaction: str, present: bool
) -> dict[str, Any]:
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        await _writable(db, p, scope, sid)
        row = await _get(db, p, scope, sid, cid)
        if row.state != "visible" or _purged(row, _cutoff(cfg, _now())):
            raise DiscussionError(409, "COMMENT_UNAVAILABLE", "this comment cannot be reacted to")
        key = {"c": cid, "u": p.userId, "r": reaction}
        if present:
            await db.execute(
                text("INSERT INTO comment_reactions (comment_id, user_id, reaction, created_at)"
                     " VALUES (:c, :u, :r, :now) ON CONFLICT (comment_id, user_id, reaction)"
                     " DO NOTHING"), {**key, "now": _now()})
        else:
            await db.execute(
                text("DELETE FROM comment_reactions WHERE comment_id = :c AND user_id = :u"
                     " AND reaction = :r"), key)
        await db.commit()
        view = (await _render(db, p, scope, sid, [row], cfg))[0]
        return {"id": cid, "reactions": view["reactions"]}


# ----------------------------------------------------------------------- promote


async def promote(
    factory: Any, p: Principal, scope: str, sid: str, cid: str, req: "PromoteBody"
) -> dict[str, Any]:
    """The explicit step that turns discussion into guidance. Idempotent: one promotion per
    comment (``comment_promotions`` key), and the guidance instruction carries the
    deterministic request id ``promote:<commentId>``, so a retry after a crash between the
    two writes finds the first instruction instead of sending a second."""
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        await _writable(db, p, scope, sid)
        row = await _get(db, p, scope, sid, cid)
        link = (await db.execute(
            text("SELECT run_id, event_seq, instruction_id, comment_version FROM comment_promotions"
                 " WHERE comment_id = :c"), {"c": cid})).first()
        if link is not None:
            return {"commentId": cid, "runId": link.run_id, "seq": link.event_seq,
                    "instructionId": link.instruction_id, "queued": link.event_seq is None,
                    "commentVersion": link.comment_version, "duplicate": True}
        if row.state != "visible" or _purged(row, _cutoff(cfg, _now())):
            raise DiscussionError(409, "COMMENT_UNAVAILABLE", "this comment cannot be promoted")
        if row.version != req.expectedVersion:
            raise DiscussionError(409, "STALE_COMMENT_VERSION",
                                  "the comment changed since you read it; review it again",
                                  {"currentVersion": row.version})
        run_id = sid if scope == "run" else req.runId
        if (scope == "run" and req.runId not in (None, sid)) or run_id is None or (
            run_id not in await _scope_runs(db, p, scope, sid)
        ):
            raise DiscussionError(
                422, "BAD_RUN", "name a run of this session to receive the guidance")
        content, version = row.body, row.version
    sent = await collab.redirect(
        factory, p, run_id, "guidance", content, req.reason or f"comment:{cid}", f"promote:{cid}")
    async with factory() as db:
        res = await db.execute(
            text("INSERT INTO comment_promotions (comment_id, team_id, run_id, promoted_by,"
                 " promoted_at, comment_version, event_seq, instruction_id) VALUES"
                 " (:c, :t, :r, :u, :now, :v, :s, :ins) ON CONFLICT (comment_id) DO NOTHING"),
            {"c": cid, "t": p.teamId, "r": run_id, "u": p.userId, "now": _now(), "v": version,
             "s": sent.get("seq"), "ins": sent.get("instructionId")},
        )
        if res.rowcount == 1:
            await _emit(db, p, scope, sid, "comment.promoted", cid)
            await _audit(db, p, "comment.promoted", scope, sid, {
                "commentId": cid, "scopeKind": scope, "scopeId": sid, "runId": run_id,
                "seq": sent.get("seq"), "instructionId": sent.get("instructionId"),
                "commentVersion": version})
        await db.commit()
    return {"commentId": cid, "runId": run_id, "seq": sent.get("seq"),
            "instructionId": sent.get("instructionId"), "queued": bool(sent.get("queued")),
            "commentVersion": version,
            "duplicate": res.rowcount != 1 or bool(sent.get("duplicate"))}


# ---------------------------------------------------------------- history, export


def _revision_body(row: Any, rev_body: str, p: Principal, gone: bool) -> str | None:
    if gone or not rev_body:
        return None
    if row.state == "visible":
        return rev_body
    admin = p.role == Role.ADMIN
    if row.state == "hidden":
        return rev_body if admin or row.author_user_id == p.userId else None
    return rev_body if admin else None  # deleted: kept revisions are for admins only


async def _revisions(db: Any, p: Principal, row: Any, gone: bool) -> list[dict[str, Any]]:
    revs = (
        await db.execute(
            text("SELECT version, body, edited_by, edited_at FROM comment_revisions"
                 " WHERE comment_id = :c ORDER BY version"), {"c": row.id})
    ).all()
    return [{"version": r.version, "editedBy": r.edited_by, "editedAt": _iso(r.edited_at),
             "body": _revision_body(row, r.body, p, gone)} for r in revs]


async def history(
    factory: Any, p: Principal, scope: str, sid: str, cid: str
) -> dict[str, Any]:
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        row = await _get(db, p, scope, sid, cid)
        gone = _purged(row, _cutoff(cfg, _now()))
        return {"commentId": cid, "state": row.state, "purged": gone,
                "author": {"userId": row.author_user_id, "name": row.author_name_snapshot},
                "createdAt": _iso(row.created_at), "currentVersion": row.version,
                "revisions": await _revisions(db, p, row, gone)}


async def export(factory: Any, p: Principal, scope: str, sid: str) -> dict[str, Any]:
    """Own scope, own team only (every query filters team_id; mentioned names come from the
    caller's team). Admins also get hidden text and retained revisions of deleted comments;
    engineers get what the scope's readers see. Past-retention text is never exported."""
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        rows = (await db.execute(
            text(f"SELECT {COLS} FROM comments WHERE scope_kind = :k AND scope_id = :s"
                 " AND team_id = :t ORDER BY created_at, id LIMIT :n"),
            {"k": scope, "s": sid, "t": p.teamId, "n": MAX_EXPORT + 1})).all()
        truncated, rows = len(rows) > MAX_EXPORT, rows[:MAX_EXPORT]
        cutoff = _cutoff(cfg, _now())
        views = await _render(db, p, scope, sid, rows, cfg)
        for row, view in zip(rows, views, strict=True):
            view.pop("can", None)
            view["revisions"] = await _revisions(db, p, row, _purged(row, cutoff))
        await _audit(db, p, "comment.exported", scope, sid, {
            "scopeKind": scope, "scopeId": sid, "count": len(views), "truncated": truncated})
        await db.commit()
        return {
            "format": "harness.discussion.export/1", "scope": {"kind": scope, "id": sid},
            "teamId": p.teamId, "exportedAt": _iso(_now()), "exportedBy": p.userId,
            "retentionDays": cfg["retentionDays"], "count": len(views), "truncated": truncated,
            "attachments": "not supported: comments are text only",
            "comments": views,
        }


# ------------------------------------------------------------------------ search


def _like(q: str) -> str:
    return "%" + q.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def _snippet(body: str, q: str) -> str:
    i = max(body.lower().find(q.lower()), 0)
    start = max(0, i - 60)
    head, tail = "..." if start else "", "..." if start + 200 < len(body) else ""
    return head + body[start:start + 200] + tail


async def search(
    factory: Any, p: Principal, q: str, kind: str | None, limit: int, before: str | None
) -> dict[str, Any]:
    q = q.strip()
    if len(q) < 2:
        raise DiscussionError(422, "BAD_QUERY", "search needs at least 2 characters")
    scopes = []
    if kind in (None, "run") and collab.pilot_allowed(p.teamId):
        scopes.append(
            "(c.scope_kind = 'run' AND EXISTS (SELECT 1 FROM agent_runs r JOIN projects pr"
            " ON pr.id = r.project_id WHERE r.id = c.scope_id AND pr.team_id = :t))")
    if kind in (None, "session"):
        scopes.append(
            "(c.scope_kind = 'session' AND EXISTS (SELECT 1 FROM collab_sessions s"
            " WHERE s.id = c.scope_id AND s.team_id = :t AND (:adm = 1 OR (s.state <> 'deleted'"
            " AND EXISTS (SELECT 1 FROM session_members m WHERE m.session_id = s.id"
            " AND m.user_id = :u AND m.status = 'active')))))")
    if not scopes:
        return {"query": q, "results": [], "nextCursor": None}
    async with factory() as db:
        cfg = await settings(db, p.teamId)
        cut = _cutoff(cfg, _now())
        args: dict[str, Any] = {"t": p.teamId, "u": p.userId, "adm": int(p.role == Role.ADMIN),
                                "q": _like(q), "n": limit + 1}
        sql = ("SELECT c.id, c.scope_kind, c.scope_id, c.parent_id, c.author_user_id,"
               " c.author_name_snapshot, c.body, c.created_at, c.edited_at FROM comments c"
               " WHERE c.team_id = :t AND c.state = 'visible' AND c.purged_at IS NULL"
               " AND lower(c.body) LIKE :q ESCAPE '!' AND (" + " OR ".join(scopes) + ")")
        if cut is not None:
            sql += " AND c.created_at >= :cut"
            args["cut"] = cut
        if before:
            sql += (" AND (c.created_at < (SELECT created_at FROM comments WHERE id = :b) OR"
                    " (c.created_at = (SELECT created_at FROM comments WHERE id = :b)"
                    " AND c.id < :b))")
            args["b"] = before
        rows = (await db.execute(
            text(sql + " ORDER BY c.created_at DESC, c.id DESC LIMIT :n"), args)).all()
    page = rows[:limit]
    return {
        "query": q,
        "results": [{
            "commentId": r.id, "scopeKind": r.scope_kind, "scopeId": r.scope_id,
            "parentId": r.parent_id, "author": {"userId": r.author_user_id,
                                                "name": r.author_name_snapshot},
            "snippet": _snippet(r.body, q), "createdAt": _iso(r.created_at),
            "editedAt": _iso(r.edited_at),
        } for r in page],
        "nextCursor": page[-1].id if len(rows) > limit else None,
    }


# --------------------------------------------------------- settings and retention


async def put_settings(factory: Any, p: Principal, req: "SettingsBody") -> dict[str, Any]:
    async with factory() as db:
        before = await settings(db, p.teamId)
        after = {
            "editWindowSeconds": (before["editWindowSeconds"] if req.editWindowSeconds is None
                                  else req.editWindowSeconds),
            "retentionDays": (before["retentionDays"] if req.retentionDays is None
                              else req.retentionDays),
            "auditSensitive": req.auditSensitive or before["auditSensitive"],
        }
        if not before["isDefault"] and all(after[k] == before[k] for k in after):
            return before  # no-op: no audit noise
        await db.execute(
            text("INSERT INTO discussion_settings (team_id, edit_window_seconds, retention_days,"
                 " audit_sensitive, updated_by, updated_at) VALUES (:t, :w, :r, :s, :u, :now)"
                 " ON CONFLICT (team_id) DO UPDATE SET"
                 " edit_window_seconds = excluded.edit_window_seconds,"
                 " retention_days = excluded.retention_days,"
                 " audit_sensitive = excluded.audit_sensitive,"
                 " updated_by = excluded.updated_by, updated_at = excluded.updated_at"),
            {"t": p.teamId, "w": after["editWindowSeconds"], "r": after["retentionDays"],
             "s": after["auditSensitive"], "u": p.userId, "now": _now()},
        )
        keys = tuple(after)
        await _audit(db, p, "discussion.settings_changed", "team", p.teamId, {
            "before": {k: before[k] for k in keys}, "after": after})
        await db.commit()
        return await settings(db, p.teamId)


async def purge_expired(factory: Any, *, team_id: str | None = None, batch: int = 500) -> int:
    """Retention: blank the text of comments (and their revisions) older than the team's
    ``retentionDays`` and stamp ``purged_at``. The row, author, timestamps, moderation fields
    and audit rows stay. Reads already treat past-retention comments as purged, so a late run
    of this job changes nothing visible. Call it from a worker loop or a cron; it is not
    scheduled by this module. Returns the number of comments purged."""
    total = 0
    async with factory() as db:
        teams = (await db.execute(
            text("SELECT DISTINCT team_id FROM comments WHERE purged_at IS NULL"
                 + (" AND team_id = :t" if team_id else "")),
            {"t": team_id} if team_id else {})).all()
        for t in teams:
            cfg = await settings(db, t.team_id)
            cut = _cutoff(cfg, _now())
            if cut is None:
                continue
            rows = (await db.execute(
                text("SELECT id, scope_kind, scope_id FROM comments WHERE team_id = :t"
                     " AND purged_at IS NULL AND created_at < :cut LIMIT :n"),
                {"t": t.team_id, "cut": cut, "n": batch})).all()
            if not rows:
                continue
            ids = [r.id for r in rows]
            await db.execute(_q("UPDATE comments SET body = '', purged_at = :now WHERE id IN :ids",
                                "ids"), {"now": _now(), "ids": ids})
            await db.execute(_q("UPDATE comment_revisions SET body = '' WHERE comment_id IN :ids",
                                "ids"), {"ids": ids})
            who = Principal(userId=SYSTEM_ACTOR, teamId=t.team_id, role=Role.ENGINEER)
            per_scope: dict[tuple[str, str], int] = {}
            for r in rows:
                k = (r.scope_kind, r.scope_id)
                per_scope[k] = per_scope.get(k, 0) + 1
            for (kind, sid), n in per_scope.items():
                await _audit(db, who, "comment.purged", kind, sid, {
                    "scopeKind": kind, "scopeId": sid, "count": n,
                    "retentionDays": cfg["retentionDays"]})
            total += len(rows)
        await db.commit()
    return total


# ------------------------------------------------------------------------ routes


class CreateBody(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_BODY)
    parentId: str | None = Field(default=None, max_length=64)
    mentions: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=MAX_MENTIONS)
    refKind: RefKind | None = None
    refId: str | None = Field(default=None, max_length=512)
    refPos: int | None = Field(default=None, ge=0, le=100_000_000)
    requestId: str | None = Field(default=None, max_length=64)


class EditBody(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_BODY)
    expectedVersion: int = Field(ge=1)
    mentions: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(
        default=None, max_length=MAX_MENTIONS)


class ReactBody(BaseModel):
    reaction: ReactionCode
    present: bool = True


class PromoteBody(BaseModel):
    action: Literal["send_as_guidance"]
    expectedVersion: int = Field(ge=1)
    runId: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=collab.MAX_REASON)


class ModerateBody(BaseModel):
    action: Literal["hide", "restore", "delete"]
    reason: str = Field(min_length=3, max_length=collab.MAX_REASON)


class SettingsBody(BaseModel):
    editWindowSeconds: int | None = Field(default=None, ge=0, le=86400)
    retentionDays: int | None = Field(default=None, ge=0, le=3650)
    auditSensitive: Literal["auto", "always"] | None = None


def _register(scope: str, prefix: str) -> None:
    base = f"/{prefix}/{{scopeId}}/comments"
    Read = Annotated[Principal, Depends(_guard(scope, "read"))]
    Comment = Annotated[Principal, Depends(_guard(scope, "comment"))]
    Steer = Annotated[Principal, Depends(_guard(scope, "steer"))]
    Export = Annotated[Principal, Depends(_guard(scope, "export"))]
    Admin = Annotated[Principal, Depends(_guard(scope, "admin"))]

    @router.get(base, name=f"{scope}_comments_list")
    async def list_(
        scopeId: str, actor: Read, limit: Annotated[int, Query(ge=1, le=100)] = 50,
        after: Annotated[str | None, Query(max_length=64)] = None,
    ) -> dict:
        return await _call(lambda f: list_comments(f, actor, scope, scopeId, limit, after))

    @router.post(base, status_code=201, name=f"{scope}_comments_create")
    async def create_(scopeId: str, req: CreateBody, actor: Comment, response: Response) -> dict:
        out, created = await _call(lambda f: create(f, actor, scope, scopeId, req))
        if not created:
            response.status_code = 200
        return out

    @router.get(base + "/export", name=f"{scope}_comments_export")
    async def export_(scopeId: str, actor: Export) -> dict:
        return await _call(lambda f: export(f, actor, scope, scopeId))

    @router.patch(base + "/{commentId}", name=f"{scope}_comments_edit")
    async def edit_(scopeId: str, commentId: str, req: EditBody, actor: Comment) -> dict:
        return await _call(lambda f: edit(f, actor, scope, scopeId, commentId, req))

    @router.delete(base + "/{commentId}", name=f"{scope}_comments_delete")
    async def delete_(scopeId: str, commentId: str, actor: Comment) -> dict:
        return await _call(lambda f: delete(f, actor, scope, scopeId, commentId))

    @router.get(base + "/{commentId}/history", name=f"{scope}_comments_history")
    async def history_(scopeId: str, commentId: str, actor: Read) -> dict:
        return await _call(lambda f: history(f, actor, scope, scopeId, commentId))

    @router.post(base + "/{commentId}/reactions", name=f"{scope}_comments_react")
    async def react_(scopeId: str, commentId: str, req: ReactBody, actor: Comment) -> dict:
        return await _call(
            lambda f: react(f, actor, scope, scopeId, commentId, req.reaction, req.present))

    @router.post(base + "/{commentId}/promote", name=f"{scope}_comments_promote")
    async def promote_(scopeId: str, commentId: str, req: PromoteBody, actor: Steer) -> dict:
        return await _call(lambda f: promote(f, actor, scope, scopeId, commentId, req))

    @router.post(base + "/{commentId}/moderate", name=f"{scope}_comments_moderate")
    async def moderate_(scopeId: str, commentId: str, req: ModerateBody, actor: Admin) -> dict:
        return await _call(
            lambda f: moderate(f, actor, scope, scopeId, commentId, req.action, req.reason))


_register("run", "runs")
_register("session", "sessions")


@router.get("/discussions/search")
async def discussions_search(
    actor: _api.RequirePrincipal,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    scopeKind: Literal["run", "session"] | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
    before: Annotated[str | None, Query(max_length=64)] = None,
) -> dict:
    return await _call(lambda f: search(f, actor, q, scopeKind, limit, before))


@router.get("/discussion-settings")
async def discussion_settings_get(actor: _api.RequirePrincipal) -> dict:
    async def go(f: Any) -> dict[str, Any]:
        async with f() as db:
            return {"teamId": actor.teamId, **await settings(db, actor.teamId)}

    return await _call(go)


@router.put("/discussion-settings")
async def discussion_settings_put(req: SettingsBody, actor: _api.RequirePrincipal) -> dict:
    if actor.role != Role.ADMIN:
        await _deny_audit(
            actor, "team", actor.teamId, "PUT discussion-settings", "ROLE_DENIED", 403)
        raise _envelope(403, "ROLE_DENIED", "only team admins may change discussion settings")
    return {"teamId": actor.teamId, **await _call(lambda f: put_settings(f, actor, req))}
