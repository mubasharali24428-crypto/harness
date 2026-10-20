"""Search over what the caller may see (part 50.001-004, 009).

Rules and limits: docs/search-and-export.md. Two halves with one rule between them.

* The INDEX (``search_documents``) is derived, team-scoped and rebuildable. ``sync_index`` feeds it
  incrementally from per-source watermarks (``search_index_state``); ``reindex`` rebuilds a team.
  It only ever holds text a team member could read at indexing time: no hidden, deleted, purged
  or past-retention comments, no presence/typing events, no cursor markers, no raw terminal
  output or diffs, no secret-shaped keys or values, no hidden reasoning keys.
* SERVING never trusts the index. A query scans only ``team_id = principal.teamId`` rows, then
  rebuilds every candidate from its LIVE source (same builder the indexer uses) and re-applies
  state, retention, run/session access, the pilot allowlist, deletion-request erasure and the
  caller's filters. Snippets come from that live text (scrubbed, bounded, HTML-escaped), never
  from the index, and a hit whose live text no longer matches the query is dropped. No total
  count is returned, and a cursor is only handed out when a further AUTHORISED hit exists (or
  the scan budget ran out), so unauthorised rows are not countable.

Raw SQL only (``text()``): sections may import only ``harness_contracts``. PostgreSQL uses full
text search (``to_tsvector('simple', ...)``) when the dialect allows it and falls back to LIKE on
any error; SQLite always uses LIKE. The PostgreSQL branch is untested on a live database here.
"""

import base64
import binascii
import html
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from harness_contracts.roles import Principal, Role
from sqlalchemy import String, bindparam, text
from sqlalchemy.exc import DBAPIError

from harness_orchestrator import api as _api
from harness_orchestrator import collab, delivery, discussion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["search"])

_service: Any = None

KINDS = ("run", "session", "comment", "event", "artifact", "approval")
SOURCES = (
    "comments",
    "collab_events",
    "agent_events",
    "runs",
    "sessions",
    "approvals",
    "artifacts",
)
SYSTEM_ACTOR = "system:search-index"
#: agent_events types that carry a human-readable summary. terminal_output and diff_progress are
#: raw output (secrets, size) and are never indexed or exported.
AGENT_EVENT_TYPES = (
    "plan_proposed",
    "plan_approved",
    "execution_started",
    "paused",
    "resumed",
    "blocked_awaiting_clarification",
    "done_signal_received",
    "review_passed",
    "review_failed",
    "pr_opened",
    "engine_completed",
    "engine_failed",
)
TEXT_KEYS = ("content", "reason", "summary", "message", "note")
MAX_DOC_BODY = 8000
MAX_TITLE = 300
MAX_Q = 100
BATCH = 500
CAND_BATCH = 100  # index rows examined per round trip while serving
SEQ_OVERLAP = 200  # event seq values re-read on every sync (late commits with a lower seq)
ARTIFACT_CAP = 5000
SNIPPET_CHARS = 200
MAX_TERMS = 8

_HIDDEN_KEY = re.compile(
    r"reasoning|thought|thinking|scratchpad|chain.?of.?thought|\bcot\b|inner.?monologue", re.I
)
_SECRET_VALUE = re.compile(
    "|".join(
        [
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
            r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}",
            r"\bgh[pousr]_[A-Za-z0-9]{20,}",
            r"\bgithub_pat_[A-Za-z0-9_]{20,}",
            r"\bAKIA[0-9A-Z]{16}\b",
            r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
            r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
        ]
    ),
    re.I,
)
_SECRET_ASSIGN = re.compile(
    r"\b([\w.-]*(?:password|passwd|secret|token|api[_-]?key|authorization|credential"
    r"|private[_-]?key)[\w.-]*)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)",
    re.I,
)


def configure_search(service: Any) -> None:
    """Deploy-time wiring (like configure_discussion): the service owning the DB factory."""
    global _service
    _service = service


# ------------------------------------------------------------------ shared helpers


def now() -> datetime:
    return collab.db_now()  # the one clock seam (tests patch it)


def factory() -> Any:
    svc = _service if _service is not None else _api._service
    return getattr(svc, "session_factory", None)


def envelope(status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> Any:
    return HTTPException(
        status, detail={"error": {"code": code, "message": message}, **(extra or {})}
    )


def env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def scrub_text(value: Any, limit: int = MAX_DOC_BODY) -> str:
    """Redact secret-shaped values in free text (private keys, provider tokens, JWTs, bearer
    tokens, ``password=...`` assignments) and bound the length. Heuristic by design: a miss is
    possible for secrets with no recognisable shape, which is why secrets never enter the
    system through the keys this module reads (see ``clean_json``)."""
    s = "" if value is None else str(value)
    s = _SECRET_VALUE.sub("[redacted]", s)
    s = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", s)
    return s[:limit]


def clean_json(value: Any, depth: int = 0) -> Any:
    """Copy of a JSON value without secret-shaped keys (collab's rule) and without hidden-reasoning
    keys, strings scrubbed and bounded, depth and fan-out bounded."""
    if depth > 4:
        return None
    if isinstance(value, dict):
        return {
            k: clean_json(v, depth + 1)
            for k, v in list(value.items())[:60]
            if not collab._SECRET_KEY.search(str(k)) and not _HIDDEN_KEY.search(str(k))
        }
    if isinstance(value, list | tuple):
        return [clean_json(v, depth + 1) for v in value[:50]]
    if isinstance(value, str):
        return scrub_text(value, 2000)
    return value


def as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def missing_table(exc: BaseException) -> bool:
    msg = str(getattr(exc, "orig", exc)).lower()
    return "no such table" in msg or "does not exist" in msg or "undefined table" in msg


def _esc_like(s: str) -> str:
    return s.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _stmt(sql: str, args: dict[str, Any]) -> Any:
    st = text(sql)
    # An empty list renders as `SELECT CAST(NULL AS <type>) WHERE 1!=1`; untyped that is INTEGER,
    # which PostgreSQL will not compare with a varchar id. Id lists are strings: say so (only when
    # empty, so every non-empty statement stays exactly as it was).
    exp = [
        bindparam(k, expanding=True, type_=None if v else String)
        for k, v in args.items()
        if isinstance(v, list | tuple | set | frozenset)
    ]
    return st.bindparams(*exp) if exp else st


async def rows(db: Any, sql: str, args: dict[str, Any] | None = None) -> list[Any]:
    """Execute raw SQL; list-valued arguments become expanding IN parameters."""
    a = {
        k: (list(v) if isinstance(v, set | frozenset | tuple) else v)
        for k, v in (args or {}).items()
    }
    return (await db.execute(_stmt(sql, a), a)).all()


async def audit(
    db: Any, p: Principal, action: str, target_type: str, target_id: str, detail: dict[str, Any]
) -> None:
    """Append an audit row in the caller's transaction. Detail is ids, counts and states: never
    text, secrets or hidden reasoning."""
    member = await rows(
        db,
        "SELECT id FROM team_members WHERE team_id = :t AND user_id = :u LIMIT 1",
        {"t": p.teamId, "u": p.userId},
    )
    await db.execute(
        text(
            "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, target_id,"
            " detail, ts) VALUES (:a, 'human', :act, :tt, :tid, :d, :ts)"
        ),
        {
            "a": member[0].id if member else None,
            "act": action,
            "tt": target_type,
            "tid": target_id,
            "d": json.dumps(
                {"userId": p.userId, "teamId": p.teamId, **collab._scrub(detail)}, default=str
            ),
            "ts": now(),
        },
    )


def system_principal(team_id: str) -> Principal:
    return Principal(userId=SYSTEM_ACTOR, teamId=team_id, role=Role.ENGINEER)


# --------------------------------------------------------------------------- documents


@dataclass
class Doc:
    kind: str
    source_id: str
    created_at: datetime
    key: Any = ""  # paging key of the source (id or seq)
    title: str = ""
    body: str = ""
    run_id: str | None = None
    session_id: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    status: str | None = None
    approval_state: str | None = None
    participants: tuple[str, ...] = ()
    actor: str | None = None
    ok: bool = True  # eligible for the index at all
    access: tuple[str, str | None] = ("team", None)  # ("session", id): members and admins only
    stamp: datetime | None = None


def _stamp(created: Any, updated: Any) -> datetime:
    c = discussion._dt(created)
    return max(c, discussion._dt(updated)) if updated else c


def _title(s: Any) -> str:
    return scrub_text(s, MAX_TITLE)


def event_text(payload: Any) -> str:
    p = as_dict(payload)
    parts = [str(p[k]) for k in TEXT_KEYS if isinstance(p.get(k), str) and p[k].strip()]
    return scrub_text(" ".join(parts))


_ERASED = (
    " AND NOT EXISTS (SELECT 1 FROM deletion_requests d WHERE d.team_id = :t"
    " AND d.state = 'executed' AND d.subject_user_id = {col} AND {ts} <= d.executed_at)"
)


async def _runs(db: Any, team: str, ids: set[str]) -> dict[str, Any]:
    if not ids:
        return {}
    got = await rows(
        db,
        "SELECT r.id AS id, r.project_id AS project_id, r.state AS state, r.branch AS branch,"
        " r.created_at AS created_at, p.workspace_id AS workspace_id FROM agent_runs r"
        " JOIN projects p ON p.id = r.project_id WHERE p.team_id = :t AND r.id IN :ids",
        {"t": team, "ids": ids},
    )
    return {r.id: r for r in got}


async def _sessions(db: Any, team: str, ids: set[str]) -> dict[str, Any]:
    if not ids:
        return {}
    got = await rows(
        db,
        "SELECT id, state, workspace_id FROM collab_sessions WHERE team_id = :t AND id IN :ids",
        {"t": team, "ids": ids},
    )
    return {r.id: r for r in got}


async def _fetch_comments(db: Any, team: str, where: str, args: dict, tail: str) -> list[Doc]:
    cutoff = discussion._cutoff(await discussion.settings(db, team), now())
    got = await rows(
        db,
        "SELECT c.id, c.scope_kind, c.scope_id, c.author_user_id, c.body, c.state,"
        " c.created_at, c.edited_at, c.moderated_at, c.deleted_at, c.purged_at FROM comments c"
        f" WHERE c.team_id = :t AND {where} {tail}",
        {"t": team, **args},
    )
    runs = await _runs(db, team, {r.scope_id for r in got if r.scope_kind == "run"})
    sess = await _sessions(db, team, {r.scope_id for r in got if r.scope_kind == "session"})
    out = []
    for r in got:
        created = discussion._dt(r.created_at)
        stamps = [
            discussion._dt(x)
            for x in (r.created_at, r.edited_at, r.moderated_at, r.deleted_at, r.purged_at)
            if x is not None
        ]
        d = Doc(
            "comment",
            r.id,
            created,
            key=r.id,
            status="visible",
            actor=r.author_user_id,
            participants=(r.author_user_id,),
            stamp=max(stamps),
        )
        ok = (
            r.state == "visible"
            and r.purged_at is None
            and not (cutoff is not None and created < cutoff)
        )
        if r.scope_kind == "run":
            run = runs.get(r.scope_id)
            ok = ok and run is not None
            d.run_id, d.access = r.scope_id, ("run", r.scope_id)
            if run is not None:
                d.project_id, d.workspace_id = run.project_id, run.workspace_id
        else:
            s = sess.get(r.scope_id)
            ok = ok and s is not None and s.state != "deleted"
            d.session_id, d.access = r.scope_id, ("session", r.scope_id)
            if s is not None:
                d.workspace_id = s.workspace_id
        d.ok = ok
        d.body = scrub_text(r.body) if ok else ""
        out.append(d)
    return out


async def _fetch_collab_events(db: Any, team: str, where: str, args: dict, tail: str) -> list[Doc]:
    visible = delivery._visible_sql().replace("kind", "e.kind")  # same filter as the history feed
    sql = (
        "SELECT e.seq, e.run_id, e.actor_user_id, e.kind, e.payload, e.ts FROM run_collab_events"
        f" e WHERE e.team_id = :t AND {where} AND {visible}"
        + _ERASED.format(col="e.actor_user_id", ts="e.ts")
        + f" {tail}"
    )
    got = await rows(db, sql, {"t": team, **delivery._visible_params(), **args})
    runs = await _runs(db, team, {r.run_id for r in got})
    out = []
    for r in got:
        run = runs.get(r.run_id)
        d = Doc(
            "event",
            f"c{r.seq}",
            discussion._dt(r.ts),
            key=r.seq,
            title=_title(r.kind),
            body=event_text(r.payload),
            run_id=r.run_id,
            actor=r.actor_user_id,
            participants=(r.actor_user_id,),
            access=("run", r.run_id),
            ok=run is not None,
        )
        if run is not None:
            d.project_id, d.workspace_id = run.project_id, run.workspace_id
        out.append(d)
    return out


async def _fetch_agent_events(db: Any, team: str, where: str, args: dict, tail: str) -> list[Doc]:
    got = await rows(
        db,
        "SELECT e.seq, e.run_id, e.type, e.payload, e.ts FROM agent_events e"
        " JOIN agent_runs r ON r.id = e.run_id JOIN projects p ON p.id = r.project_id"
        f" WHERE p.team_id = :t AND e.type IN :types AND {where} {tail}",
        {"t": team, "types": AGENT_EVENT_TYPES, **args},
    )
    runs = await _runs(db, team, {r.run_id for r in got})
    out = []
    for r in got:
        run = runs.get(r.run_id)
        d = Doc(
            "event",
            f"a{r.seq}",
            discussion._dt(r.ts),
            key=r.seq,
            title=_title(r.type),
            body=event_text(r.payload),
            run_id=r.run_id,
            access=("run", r.run_id),
            ok=run is not None,
        )
        if run is not None:
            d.project_id, d.workspace_id = run.project_id, run.workspace_id
        out.append(d)
    return out


async def _fetch_runs(db: Any, team: str, where: str, args: dict, tail: str) -> list[Doc]:
    got = await rows(
        db,
        "SELECT r.id, r.project_id, r.state, r.branch, r.created_at, r.updated_at,"
        " p.workspace_id AS workspace_id,"
        " (SELECT a.goal FROM agent_plans a WHERE a.run_id = r.id ORDER BY a.created_at DESC"
        " LIMIT 1) AS goal,"
        " (SELECT h.state FROM high_impact_approvals h WHERE h.run_id = r.id AND h.team_id = :t"
        " ORDER BY h.created_at DESC, h.id DESC LIMIT 1) AS astate"
        " FROM agent_runs r JOIN projects p ON p.id = r.project_id"
        f" WHERE p.team_id = :t AND {where} {tail}",
        {"t": team, **args},
    )
    ids = {r.id for r in got}
    people: dict[str, list[str]] = {}
    for r in await rows(
        db,
        "SELECT run_id, user_id FROM run_participants WHERE team_id = :t AND run_id IN :ids",
        {"t": team, "ids": ids},
    ):
        people.setdefault(r.run_id, []).append(r.user_id)
    link = {
        r.run_id: r.session_id
        for r in await rows(
            db, "SELECT run_id, session_id FROM session_runs WHERE run_id IN :ids", {"ids": ids}
        )
    }
    return [
        Doc(
            "run",
            r.id,
            discussion._dt(r.created_at),
            key=r.id,
            title=_title(r.branch or r.id),
            body=scrub_text(f"{r.goal or ''} {r.state}"),
            run_id=r.id,
            session_id=link.get(r.id),
            project_id=r.project_id,
            workspace_id=r.workspace_id,
            status=r.state,
            approval_state=r.astate,
            participants=tuple(people.get(r.id, ())),
            access=("run", r.id),
            stamp=_stamp(r.created_at, r.updated_at),
        )
        for r in got
    ]


async def _fetch_sessions(db: Any, team: str, where: str, args: dict, tail: str) -> list[Doc]:
    got = await rows(
        db,
        "SELECT s.id, s.workspace_id, s.title, s.description, s.state, s.created_at,"
        f" s.updated_at FROM collab_sessions s WHERE s.team_id = :t AND {where} {tail}",
        {"t": team, **args},
    )
    people: dict[str, list[str]] = {}
    for m in await rows(
        db,
        "SELECT session_id, user_id FROM session_members WHERE kind = 'user'"
        " AND status = 'active' AND session_id IN :ids",
        {"ids": {r.id for r in got}},
    ):
        people.setdefault(m.session_id, []).append(m.user_id)
    out = []
    for r in got:
        ok = r.state != "deleted"  # deleted sessions are searchable by nobody
        out.append(
            Doc(
                "session",
                r.id,
                discussion._dt(r.created_at),
                key=r.id,
                title=_title(r.title),
                body=scrub_text(r.description) if ok else "",
                session_id=r.id,
                workspace_id=r.workspace_id,
                status=r.state,
                participants=tuple(people.get(r.id, ())),
                ok=ok,
                access=("session", r.id),
                stamp=_stamp(r.created_at, r.updated_at),
            )
        )
    return out


async def _fetch_approvals(db: Any, team: str, where: str, args: dict, tail: str) -> list[Doc]:
    got = await rows(
        db,
        "SELECT h.id, h.run_id, h.requester_user_id, h.decided_by, h.action, h.paths,"
        " h.summary, h.state, h.created_at, h.decided_at FROM high_impact_approvals h"
        f" WHERE h.team_id = :t AND {where}"
        + _ERASED.format(col="h.requester_user_id", ts="h.created_at")
        + f" {tail}",
        {"t": team, **args},
    )
    runs = await _runs(db, team, {r.run_id for r in got})
    out = []
    for r in got:
        run = runs.get(r.run_id)
        try:
            paths = " ".join(str(x) for x in json.loads(r.paths))
        except (TypeError, ValueError):
            paths = ""
        stamps = [discussion._dt(x) for x in (r.created_at, r.decided_at) if x is not None]
        d = Doc(
            "approval",
            r.id,
            discussion._dt(r.created_at),
            key=r.id,
            title=_title(f"{r.action} approval"),
            body=scrub_text(f"{paths} {r.summary}"),
            run_id=r.run_id,
            status=r.state,
            approval_state=r.state,
            actor=r.requester_user_id,
            participants=tuple(x for x in (r.requester_user_id, r.decided_by) if x),
            access=("run", r.run_id),
            ok=run is not None,
            stamp=max(stamps),
        )
        if run is not None:
            d.project_id, d.workspace_id = run.project_id, run.workspace_id
        out.append(d)
    return out


async def _fetch_artifacts(db: Any, team: str, where: str, args: dict, tail: str) -> list[Doc]:
    got = await rows(
        db,
        "SELECT a.id, a.run_id, a.name, a.media_type, a.kind, a.state, a.created_by,"
        f" a.created_at FROM artifacts a WHERE a.team_id = :t AND {where} {tail}",
        {"t": team, **args},
    )
    runs = await _runs(db, team, {r.run_id for r in got})
    out = []
    for r in got:
        run = runs.get(r.run_id)
        ok = r.state == "available" and run is not None  # quarantined or deleted: never indexed
        d = Doc(
            "artifact",
            r.id,
            discussion._dt(r.created_at),
            key=r.id,
            title=_title(r.name),
            body=scrub_text(f"{r.media_type} {r.kind}"),
            run_id=r.run_id,
            status=r.state,
            actor=r.created_by,
            participants=(r.created_by,) if r.created_by else (),
            access=("run", r.run_id),
            ok=ok,
        )
        if run is not None:
            d.project_id, d.workspace_id = run.project_id, run.workspace_id
        out.append(d)
    return out


# source -> (builder, mode, key column, change columns)
_SPEC: dict[str, tuple[Any, str, str, tuple[str, ...]]] = {
    "comments": (
        _fetch_comments,
        "ts",
        "c.id",
        ("c.created_at", "c.edited_at", "c.moderated_at", "c.deleted_at", "c.purged_at"),
    ),
    "collab_events": (_fetch_collab_events, "seq", "e.seq", ()),
    "agent_events": (_fetch_agent_events, "seq", "e.seq", ()),
    "runs": (_fetch_runs, "ts", "r.id", ("r.created_at", "r.updated_at")),
    "sessions": (_fetch_sessions, "ts", "s.id", ("s.created_at", "s.updated_at")),
    "approvals": (_fetch_approvals, "ts", "h.id", ("h.created_at", "h.decided_at")),
    "artifacts": (_fetch_artifacts, "all", "a.id", ()),
}
_MAX_SEQ = {
    "collab_events": "SELECT MAX(seq) AS m FROM run_collab_events WHERE team_id = :t",
    "agent_events": "SELECT MAX(e.seq) AS m FROM agent_events e JOIN agent_runs r ON"
    " r.id = e.run_id JOIN projects p ON p.id = r.project_id WHERE p.team_id = :t",
}
_BY_KIND_LIVE = {
    "comment": ("comments",),
    "event": ("collab_events", "agent_events"),
    "run": ("runs",),
    "session": ("sessions",),
    "approval": ("approvals",),
    "artifact": ("artifacts",),
}


# ------------------------------------------------------------------------ the indexer


async def _upsert(db: Any, team: str, d: Doc, at: datetime) -> None:
    await db.execute(
        text(
            "INSERT INTO search_documents (id, team_id, kind, source_id, run_id, session_id,"
            " project_id, workspace_id, status, approval_state, title, body, participant_ids,"
            " created_at, indexed_at) VALUES (:id, :t, :k, :s, :r, :se, :p, :w, :st, :a, :ti,"
            " :b, :pi, :c, :at) ON CONFLICT (team_id, kind, source_id) DO UPDATE SET"
            " run_id = excluded.run_id, session_id = excluded.session_id,"
            " project_id = excluded.project_id, workspace_id = excluded.workspace_id,"
            " status = excluded.status, approval_state = excluded.approval_state,"
            " title = excluded.title, body = excluded.body,"
            " participant_ids = excluded.participant_ids, created_at = excluded.created_at,"
            " indexed_at = excluded.indexed_at"
        ),
        {
            "id": str(uuid.uuid4()),
            "t": team,
            "k": d.kind,
            "s": d.source_id,
            "r": d.run_id,
            "se": d.session_id,
            "p": d.project_id,
            "w": d.workspace_id,
            "st": d.status,
            "a": d.approval_state,
            "ti": d.title,
            "b": d.body[:MAX_DOC_BODY],
            "pi": json.dumps(sorted({x for x in d.participants if x})),
            "c": d.created_at,
            "at": at,
        },
    )


async def _drop(db: Any, team: str, kind: str, source_id: str) -> int:
    res = await db.execute(
        text("DELETE FROM search_documents WHERE team_id = :t AND kind = :k AND source_id = :s"),
        {"t": team, "k": kind, "s": source_id},
    )
    return res.rowcount or 0


async def _get_wm(db: Any, team: str, source: str) -> str:
    r = await rows(
        db,
        "SELECT watermark FROM search_index_state WHERE team_id = :t AND source = :s",
        {"t": team, "s": source},
    )
    return r[0].watermark if r else ""


async def _set_state(
    db: Any, team: str, source: str, wm: str, at: datetime, added: int, err: str | None
) -> None:
    await db.execute(
        text(
            "INSERT INTO search_index_state (team_id, source, watermark, last_synced_at,"
            " docs_indexed, last_error) VALUES (:t, :s, :w, :at, :n, :e)"
            " ON CONFLICT (team_id, source) DO UPDATE SET watermark = excluded.watermark,"
            " last_synced_at = excluded.last_synced_at, last_error = excluded.last_error,"
            " docs_indexed = search_index_state.docs_indexed + excluded.docs_indexed"
        ),
        {"t": team, "s": source, "w": wm, "at": at, "n": added, "e": err},
    )


async def _sync_source(
    db: Any, team: str, source: str, at: datetime, max_batches: int
) -> dict[str, Any]:
    build, mode, key, change_cols = _SPEC[source]
    wm = await _get_wm(db, team, source)
    args: dict[str, Any] = {}
    base = "1 = 1"
    if mode == "seq":
        after: Any = max(0, int(wm or 0) - SEQ_OVERLAP)
    else:
        after = ""
        if mode == "ts" and wm:
            args["w"] = discussion._dt(wm) - timedelta(
                seconds=env_int("HARNESS_SEARCH_OVERLAP_SECONDS", 300)
            )
            base = "(" + " OR ".join(f"{c} >= :w" for c in change_cols) + ")"
    added = removed = 0
    new_wm = wm
    top: Any = None
    more = False
    ceiling = 0
    if mode == "seq":  # read before paging: rows committed meanwhile are caught next time
        ceiling = (await rows(db, _MAX_SEQ[source], {"t": team}))[0].m or 0
    for batch_no in range(max_batches if mode == "seq" else 1_000_000):
        where = f"{base} AND {key} > :after"
        docs = await build(
            db,
            team,
            where,
            {**args, "after": after, "n": BATCH},
            f"ORDER BY {key} LIMIT :n" if mode != "all" else f"ORDER BY {key} LIMIT {ARTIFACT_CAP}",
        )
        for d in docs:
            if d.ok:
                await _upsert(db, team, d, at)
                added += 1
            else:
                removed += await _drop(db, team, d.kind, d.source_id)
            if d.stamp is not None and (top is None or d.stamp > top):
                top = d.stamp
        if mode == "all":
            seen = {d.source_id for d in docs}
            for g in await rows(
                db,
                "SELECT source_id FROM search_documents WHERE team_id = :t AND kind = 'artifact'",
                {"t": team},
            ):
                if g.source_id not in seen:  # row vanished at the source
                    removed += await _drop(db, team, "artifact", g.source_id)
            break
        if not docs:
            break
        after = docs[-1].key
        await db.commit()
        if mode == "seq":
            new_wm = str(max(int(new_wm or 0), int(after)))
        if len(docs) < BATCH:
            break
        more = mode == "seq" and batch_no == max_batches - 1
    if mode == "seq" and not more:  # everything after the last match is noise: skip it next time
        new_wm = str(max(int(new_wm or 0), int(ceiling)))
    if mode == "ts" and top is not None:
        new_wm = max(discussion._dt(wm), top).isoformat() if wm else top.isoformat()
    elif mode == "all":
        new_wm = at.isoformat()
    await _set_state(db, team, source, new_wm, at, added, None)
    await db.commit()
    return {"indexed": added, "removed": removed, "watermark": new_wm, "more": more}


async def sync_index(
    factory_: Any, team_id: str | None = None, *, max_batches: int = 20
) -> dict[str, Any]:
    """Incremental sync of the search index from every source, for one team or all teams.

    Per (team, source) a watermark in ``search_index_state`` marks progress: event streams by
    ``seq`` (with a small re-read overlap), mutable sources by their change timestamps (with an
    overlap window, ``HARNESS_SEARCH_OVERLAP_SECONDS``, default 300). Every write is an upsert, so
    re-reading is harmless. A missing source table is skipped, not an error. A failing source is
    reported in ``errors`` and the others still run. Returns
    ``{teamId: {source: {indexed, removed, watermark, more} | {skipped: true}}, "errors": [...]}``.
    ``schedule.maintenance_jobs`` runs it on an interval from the deployed orchestrator; the
    on-query refresh in ``GET search`` also calls it."""
    out: dict[str, Any] = {"errors": []}
    async with factory_() as db:
        teams = (
            [team_id]
            if team_id
            else [
                r.team_id
                for r in await rows(
                    db, "SELECT team_id FROM projects UNION SELECT team_id FROM collab_sessions"
                )
            ]
        )
    for team in sorted(set(teams)):
        res: dict[str, Any] = {}
        for source in SOURCES:
            at = now()
            async with factory_() as db:
                try:
                    res[source] = await _sync_source(db, team, source, at, max_batches)
                except DBAPIError as e:
                    await db.rollback()
                    if missing_table(e):
                        res[source] = {"skipped": True}
                        continue
                    logger.exception("search sync failed team=%s source=%s", team, source)
                    out["errors"].append(
                        {
                            "teamId": team,
                            "source": source,
                            "error": type(e.orig).__name__ if e.orig else "DBError",
                        }
                    )
        out[team] = res
    return out


async def reindex(
    factory_: Any, team_id: str, *, actor_user_id: str = SYSTEM_ACTOR
) -> dict[str, Any]:
    """Drop the team's documents and watermarks and rebuild from the sources. Use after raising
    retention, restoring data, or if the index is suspected stale (hard-deleted sources are only
    cleaned by this). Writes one ``search.reindexed`` audit row (team id and counts only)."""
    async with factory_() as db:
        await db.execute(text("DELETE FROM search_documents WHERE team_id = :t"), {"t": team_id})
        await db.execute(text("DELETE FROM search_index_state WHERE team_id = :t"), {"t": team_id})
        await db.commit()
    result = await sync_index(factory_, team_id, max_batches=1_000_000)
    async with factory_() as db:
        counts = {
            r.kind: r.n
            for r in await rows(
                db,
                "SELECT kind, COUNT(*) AS n FROM search_documents WHERE team_id = :t GROUP BY kind",
                {"t": team_id},
            )
        }
        who = Principal(userId=actor_user_id, teamId=team_id, role=Role.ENGINEER)
        await audit(
            db,
            who,
            "search.reindexed",
            "team",
            team_id,
            {"documents": counts, "errors": len(result["errors"])},
        )
        await db.commit()
    return {"teamId": team_id, "documents": counts, "errors": result["errors"]}


# -------------------------------------------------------------------------- serving


def _engine(db: Any) -> str:
    want = os.environ.get("HARNESS_SEARCH_ENGINE", "auto").lower()
    return "fts" if want != "like" and db.bind.dialect.name == "postgresql" else "like"


def match_sql(engine: str, terms: list[str]) -> tuple[str, dict[str, Any]]:
    """The text-match predicate over the index row. LIKE: every term is a case-insensitive
    substring (SQLite and the PG fallback). FTS (PostgreSQL only): word match on the
    ``simple`` configuration, no stemming."""
    if not terms:
        return "1 = 1", {}
    if engine == "fts":
        return (
            "to_tsvector('simple', title || ' ' || body) @@ plainto_tsquery('simple', :fts)",
            {"fts": " ".join(terms)},
        )
    clauses = [f"lower(title || ' ' || body) LIKE :m{i} ESCAPE '!'" for i in range(len(terms))]
    return " AND ".join(clauses), {f"m{i}": f"%{_esc_like(t)}%" for i, t in enumerate(terms)}


def _snippet(text_: str, terms: list[str]) -> str:
    low = text_.lower()
    hits = [i for i in (low.find(t) for t in terms) if i >= 0]
    i = min(hits) if hits else 0
    start = max(0, i - 60)
    end = start + SNIPPET_CHARS
    return ("..." if start else "") + text_[start:end] + ("..." if end < len(text_) else "")


def _enc_cursor(created: datetime, doc_id: str) -> str:
    raw = json.dumps({"c": created.isoformat(), "i": doc_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _dec_cursor(token: str) -> tuple[datetime, str]:
    try:
        o = json.loads(base64.urlsafe_b64decode(token.encode()))
        return datetime.fromisoformat(o["c"]).astimezone(UTC), str(o["i"])
    except (ValueError, KeyError, TypeError, binascii.Error) as e:
        raise envelope(422, "BAD_CURSOR", "unrecognised cursor") from e


def parse_ts(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError as e:
        raise envelope(422, "BAD_FILTER", f"{name} must be an ISO 8601 date or time") from e
    return d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)


async def _live(db: Any, p: Principal, cands: list[Any]) -> dict[str, Doc]:
    """Rebuild each candidate from its live source and keep the ones the caller may see now.
    Returns ``{index row id: live Doc}``."""
    wanted: dict[tuple[str, str], str] = {(c.kind, c.source_id): c.id for c in cands}
    live: dict[str, Doc] = {}
    pilot = collab.pilot_allowed(p.teamId)
    for kind in {c.kind for c in cands}:
        ids = [c.source_id for c in cands if c.kind == kind]
        for source in _BY_KIND_LIVE[kind]:
            build, mode, key, _cols = _SPEC[source]
            sub = ids
            if kind == "event":
                pre = "c" if source == "collab_events" else "a"
                sub = [i[1:] for i in ids if i.startswith(pre) and i[1:].isdigit()]
                sub = [int(i) for i in sub]
            if not sub:
                continue
            for d in await build(db, p.teamId, f"{key} IN :live_ids", {"live_ids": sub}, ""):
                if d.ok and (pilot or d.access[0] != "run"):
                    live[wanted.get((d.kind, d.source_id), "")] = d
    sessions = {d.access[1] for d in live.values() if d.access[0] == "session"}
    mine: set[str] = set()
    if sessions and p.role != Role.ADMIN:
        mine = {
            r.session_id
            for r in await rows(
                db,
                "SELECT session_id FROM session_members WHERE user_id = :u AND status = 'active'"
                " AND session_id IN :ids",
                {"u": p.userId, "ids": sessions},
            )
        }
    return {
        k: d
        for k, d in live.items()
        if k and (d.access[0] != "session" or p.role == Role.ADMIN or d.access[1] in mine)
    }


def _hit(d: Doc, terms: list[str]) -> dict[str, Any]:
    return {
        "kind": d.kind,
        "sourceId": d.source_id,
        "runId": d.run_id,
        "sessionId": d.session_id,
        "projectId": d.project_id,
        "title": html.escape(d.title),
        "snippet": html.escape(_snippet(f"{d.title} {d.body}".strip(), terms)),
        "status": d.status,
        "approvalState": d.approval_state,
        "actorUserId": d.actor,
        "createdAt": d.created_at.isoformat(),
    }


async def _refresh_if_stale(f: Any, team: str) -> None:
    ttl = env_int("HARNESS_SEARCH_AUTO_SYNC_SECONDS", 30)
    if ttl <= 0:
        return
    try:
        async with f() as db:
            last = (
                await rows(
                    db,
                    "SELECT MAX(last_synced_at) AS m FROM search_index_state WHERE team_id = :t",
                    {"t": team},
                )
            )[0].m
        if last is None or (now() - discussion._dt(last)).total_seconds() >= ttl:
            await sync_index(f, team, max_batches=4)
    except Exception:  # noqa: BLE001 - a refresh failure must not fail the query
        logger.warning("search refresh failed team=%s", team, exc_info=True)


async def search(
    f: Any,
    p: Principal,
    *,
    q: str | None = None,
    kind: str | None = None,
    project_id: str | None = None,
    participant_id: str | None = None,
    status: str | None = None,
    approval_state: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    q = (q or "").strip()
    if q and len(q) < 2:
        raise envelope(422, "BAD_QUERY", "search needs at least 2 characters")
    if not q and not any(
        (kind, project_id, participant_id, status, approval_state, date_from, date_to)
    ):
        raise envelope(422, "BAD_QUERY", "give a query or at least one filter")
    if date_from and date_to and date_from > date_to:
        raise envelope(422, "BAD_FILTER", "from is after to")
    terms = [t for t in re.split(r"\s+", q.lower()) if t][:MAX_TERMS]
    pos = _dec_cursor(cursor) if cursor else None
    await _refresh_if_stale(f, p.teamId)
    budget = env_int("HARNESS_SEARCH_SCAN_BUDGET", 600)
    hits: list[tuple[dict[str, Any], tuple[datetime, str]]] = []
    scanned = 0
    exhausted = False
    last_pos = pos
    async with f() as db:
        engine = _engine(db)
        while len(hits) <= limit and scanned < budget:
            cands = None
            for eng in (engine, "like"):
                m_sql, m_args = match_sql(eng, terms)
                sql = (
                    "SELECT id, kind, source_id, created_at FROM search_documents WHERE"
                    f" team_id = :t AND {m_sql}"
                )
                args: dict[str, Any] = {"t": p.teamId, "n": CAND_BATCH, **m_args}
                for col, name, val in (
                    ("kind", "k", kind),
                    ("project_id", "pr", project_id),
                    ("status", "st", status),
                    ("approval_state", "ap", approval_state),
                ):
                    if val:
                        sql += f" AND {col} = :{name}"
                        args[name] = val
                if participant_id:
                    sql += " AND participant_ids LIKE :pp ESCAPE '!'"
                    args["pp"] = f"%{_esc_like(json.dumps(participant_id))}%"
                if date_from:
                    sql += " AND created_at >= :df"
                    args["df"] = date_from
                if date_to:
                    sql += " AND created_at <= :dt"
                    args["dt"] = date_to
                if last_pos:
                    sql += " AND (created_at < :cc OR (created_at = :cc AND id < :ci))"
                    args["cc"], args["ci"] = last_pos
                try:
                    cands = await rows(
                        db, sql + " ORDER BY created_at DESC, id DESC LIMIT :n", args
                    )
                    break
                except DBAPIError:
                    if eng == "like":
                        raise
                    await db.rollback()
                    logger.warning("search: full text failed, falling back to LIKE")
            if not cands:
                exhausted = True
                break
            scanned += len(cands)
            last_pos = (discussion._dt(cands[-1].created_at), cands[-1].id)
            live = await _live(db, p, cands)
            for c in cands:
                d = live.get(c.id)
                if d is None:
                    continue
                hay = f"{d.title} {d.body}".lower()
                if terms and not all(t in hay for t in terms):
                    continue  # the source changed since it was indexed
                if (status and d.status != status) or (
                    approval_state and d.approval_state != approval_state
                ):
                    continue
                if (project_id and d.project_id != project_id) or (
                    participant_id and participant_id not in d.participants
                ):
                    continue
                hits.append((_hit(d, terms), (discussion._dt(c.created_at), c.id)))
                if len(hits) > limit:
                    break
            if len(cands) < CAND_BATCH:
                exhausted = True
                break
    page = hits[:limit]
    more = len(hits) > limit
    scan_limited = not more and not exhausted
    next_pos = page[-1][1] if more and page else (last_pos if scan_limited else None)
    return {
        "query": q,
        "results": [h for h, _ in page],
        "nextCursor": _enc_cursor(*next_pos) if next_pos else None,
        "scanLimited": scan_limited,
        "snippetEncoding": "html",
        "consistency": CONSISTENCY["summary"],
    }


CONSISTENCY = {
    "summary": (
        "eventual for discovery (new or changed items may take up to the indexing delay "
        "to appear or disappear from candidates), immediate for access (every hit and "
        "snippet is re-authorised against the live source when served)"
    ),
}


async def index_status(f: Any, p: Principal) -> dict[str, Any]:
    async with f() as db:
        states = await rows(
            db,
            "SELECT source, watermark, last_synced_at, docs_indexed, last_error FROM"
            " search_index_state WHERE team_id = :t ORDER BY source",
            {"t": p.teamId},
        )
        counts = {}
        if p.role == Role.ADMIN:  # counts of unauthorised rows are an oracle: admins only
            counts = {
                r.kind: r.n
                for r in await rows(
                    db,
                    "SELECT kind, COUNT(*) AS n FROM search_documents WHERE team_id = :t"
                    " GROUP BY kind",
                    {"t": p.teamId},
                )
            }
        engine = _engine(db)
    t = now()
    seen = {s.source: s for s in states}
    sources = []
    for name in SOURCES:
        s = seen.get(name)
        synced = discussion._dt(s.last_synced_at) if s and s.last_synced_at else None
        sources.append(
            {
                "source": name,
                "watermark": s.watermark if s else None,
                "lastSyncedAt": synced.isoformat() if synced else None,
                "lagSeconds": int((t - synced).total_seconds()) if synced else None,
                "lastError": s.last_error if s else None,
            }
        )
    ttl = env_int("HARNESS_SEARCH_AUTO_SYNC_SECONDS", 30)
    return {
        "teamId": p.teamId,
        "engine": engine,
        "sources": sources,
        "documentCounts": counts,
        "indexing": {
            "refreshAfterSeconds": ttl or None,
            "overlapSeconds": env_int("HARNESS_SEARCH_OVERLAP_SECONDS", 300),
            "note": (
                "a query refreshes the index first when it is older than refreshAfterSeconds "
                "(a refresh covers at most a bounded batch of events). With 0, or if nothing "
                "calls sync_index, new items stay invisible to search until a sync runs: "
                "there is no upper bound without a scheduler"
            ),
        },
        "consistency": CONSISTENCY["summary"],
        "indexedKinds": list(KINDS),
        "notIndexed": [
            "hidden, deleted, purged or past-retention comments",
            "deleted sessions",
            "presence, typing and cursor events",
            "terminal output and diffs",
            "secret-shaped keys and values",
            "hidden reasoning",
            "quarantined or deleted artifacts",
            "artifact file contents",
        ],
    }


# ------------------------------------------------------------------------- routes


@router.get("/search")
async def search_route(
    actor: _api.RequirePrincipal,
    q: Annotated[str | None, Query(max_length=MAX_Q)] = None,
    kind: Literal["run", "session", "comment", "event", "artifact", "approval"] | None = None,
    projectId: Annotated[str | None, Query(max_length=64)] = None,
    participantId: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[str | None, Query(max_length=40)] = None,
    approvalState: Annotated[str | None, Query(max_length=16)] = None,
    date_from: Annotated[str | None, Query(alias="from", max_length=40)] = None,
    date_to: Annotated[str | None, Query(alias="to", max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict:
    f = factory()
    if f is None:
        raise envelope(503, "SEARCH_UNAVAILABLE", "no database wired")
    return await search(
        f,
        actor,
        q=q,
        kind=kind,
        project_id=projectId,
        participant_id=participantId,
        status=status,
        approval_state=approvalState,
        date_from=parse_ts(date_from, "from"),
        date_to=parse_ts(date_to, "to"),
        limit=limit,
        cursor=cursor,
    )


@router.get("/search/status")
async def search_status_route(actor: _api.RequirePrincipal) -> dict:
    f = factory()
    if f is None:
        raise envelope(503, "SEARCH_UNAVAILABLE", "no database wired")
    return await index_status(f, actor)
