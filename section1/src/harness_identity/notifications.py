"""In-app notifications, preferences and delivery (master prompt part 47).

See docs/notifications.md.

Rows are written by section4 ``notify.emit`` (ids and a closed ``summary_code`` only). This module
serves the inbox, the preferences and the mute/snooze/unsubscribe controls, re-checks authorization
when a link is opened, and delivers the opt-in email channel (``deliver_due``, ``run_digests``).
Raw SQLAlchemy Core over migration 0046. Posture: every row belongs to one user in one team and is
read only by that user, a row or scope the caller cannot see answers 404 (no existence oracle),
email is OFF until the user opts in, and every control change is audited (ids and states only,
never notification content).
"""

import base64
import html as _html
import json
import os
import re
import secrets
from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Any
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from harness_contracts import Principal, error_body, hash_token
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import sessions, workspaces
from .audit_emit import _emit as emit_audit
from .auth import get_principal
from .timeutil import as_utc, now_utc

router = APIRouter(prefix="/api/v1/notifications")

# Closed vocabularies (mirrored by section4 notify.py, pinned by tests on both sides).
KINDS = (
    "approval", "mention", "handoff", "failure", "blocked", "control_request", "suggestion",
    "budget",
)
SUMMARY_CODES: dict[str, tuple[str, ...]] = {
    "approval": ("approval_requested", "approval_decided"),
    "mention": ("mentioned",),
    "handoff": ("handoff_offered", "handoff_accepted", "handoff_rejected"),
    "failure": ("run_failed", "review_failed"),
    "blocked": ("run_blocked",),
    "control_request": ("control_requested", "control_granted", "control_declined"),
    "suggestion": ("suggestion_queued",),
    "budget": ("budget_warning", "budget_exhausted", "plan_limit_reached"),
}
# summary_code -> (one item, n items). Fixed text: names no person, session, plan or comment.
PREVIEWS: dict[str, tuple[str, str]] = {
    "approval_requested": ("An approval needs your decision", "{n} approvals need your decision"),
    "approval_decided": ("An approval was decided", "{n} approvals were decided"),
    "mentioned": ("You were mentioned", "You were mentioned {n} times"),
    "handoff_offered": ("A handoff was offered to you", "{n} handoffs were offered to you"),
    "handoff_accepted": ("Your handoff was accepted", "{n} handoffs were accepted"),
    "handoff_rejected": ("Your handoff was declined", "{n} handoffs were declined"),
    "run_failed": ("A run failed", "{n} run failures"),
    "review_failed": ("A review failed", "{n} reviews failed"),
    "run_blocked": ("A run is blocked and needs input", "{n} run blockers need input"),
    "control_requested": ("Someone asked for control of a run", "{n} control requests"),
    "control_granted": ("Your control request was granted", "{n} control grants"),
    "control_declined": ("Your control request was declined", "{n} control requests declined"),
    "suggestion_queued": ("A suggestion is waiting for you", "{n} suggestions are waiting"),
    "budget_warning": ("Team spend is nearing its monthly limit", "{n} spend warnings"),
    "budget_exhausted": ("Team spend reached its monthly limit", "{n} spend limit alerts"),
    "plan_limit_reached": ("Your team reached its daily run limit", "{n} daily run limit alerts"),
}
INAPP_MODES = ("on", "off")
EMAIL_MODES = ("off", "urgent", "immediate", "digest")
SCOPE_KINDS = ("workspace", "session")
MUTE_KINDS = ("workspace", "session", "run", "kind")
DIGEST_PERIODS = ("daily", "weekly")
MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 60
UNSUB_TTL_DAYS = 90
MAX_SNOOZE = timedelta(days=30)
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_ORDER = {"global": 0, "workspace": 1, "session": 2}


def _err(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, msg))


def _ts(v: Any) -> datetime | None:
    if v is None:
        return None
    return as_utc(datetime.fromisoformat(v) if isinstance(v, str) else v)


def _iso(v: Any) -> str | None:
    d = _ts(v)
    return None if d is None else d.isoformat()


Auth = Annotated[Principal, Depends(get_principal)]
_NOT_FOUND = "notification not found"


# ------------------------------------------------------------- pure rules (mirror of notify.py)


def resolve(rows: list[dict[str, Any]], kind: str) -> tuple[str, str]:
    """(inapp, email) for one event: rows run from least to most specific (global, workspace,
    session); a more specific scope wins and inside a scope the per-event entry beats the scope
    default. Built-in defaults: in-app on, email off."""
    inapp, email = "on", "off"
    for r in rows:
        inapp = r.get("inapp") or inapp
        email = r.get("email") or email
        ev = (r.get("events") or {}).get(kind) or {}
        inapp = ev.get("inapp") or inapp
        email = ev.get("email") or email
    return inapp, email


def _zone(name: str | None) -> Any:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 - unknown or unavailable zone: fall back to UTC
        return UTC


def _minutes(hhmm: str | None) -> int | None:
    if not hhmm or not _HHMM.match(hhmm):
        return None
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def quiet_until(now: datetime, tz_name: str | None, start: str | None,
                end: str | None) -> datetime | None:
    """None outside quiet hours, else the UTC instant they end (wall-clock window in the user's
    own zone, start inclusive, end exclusive, may wrap midnight)."""
    s, e = _minutes(start), _minutes(end)
    if s is None or e is None or s == e:
        return None
    zone = _zone(tz_name)
    local = as_utc(now).astimezone(zone)
    cur = local.hour * 60 + local.minute
    inside = (s <= cur < e) if s < e else (cur >= s or cur < e)
    if not inside:
        return None
    day = local.date() + timedelta(days=1 if (s > e and cur >= s) else 0)
    return datetime.combine(day, time(e // 60, e % 60), tzinfo=zone).astimezone(UTC)


# ------------------------------------------------------------- preference rows


def _load_prefs(r: Any) -> dict[str, Any]:
    try:
        events = json.loads(r["events"] or "{}")
    except ValueError:
        events = {}
    return {
        "scope_kind": r["scope_kind"], "scope_id": r["scope_id"], "inapp": r["inapp"],
        "email": r["email"], "events": events if isinstance(events, dict) else {},
        "timezone": r["timezone"], "quiet_enabled": bool(r["quiet_enabled"]),
        "quiet_start": r["quiet_start"], "quiet_end": r["quiet_end"],
        "digest_period": r["digest_period"], "digest_hour": r["digest_hour"],
        "last_digest_at": _ts(r["last_digest_at"]), "version": r["version"],
    }


async def _pref_rows(db, team_id: str, user_id: str, workspace_id: str | None,
                     session_id: str | None) -> list[dict[str, Any]]:
    """Global row plus the matching workspace and session rows, least specific first."""
    clauses, prm = ["scope_kind = 'global'"], {"t": team_id, "u": user_id}
    if workspace_id:
        clauses.append("(scope_kind = 'workspace' AND scope_id = :w)")
        prm["w"] = workspace_id
    if session_id:
        clauses.append("(scope_kind = 'session' AND scope_id = :s)")
        prm["s"] = session_id
    rows = (await db.execute(
        text("SELECT * FROM notification_preferences WHERE team_id=:t AND user_id=:u"
             f" AND ({' OR '.join(clauses)})"), prm)).mappings().all()
    return sorted((_load_prefs(r) for r in rows), key=lambda r: _ORDER[r["scope_kind"]])


def _pref_out(row: dict[str, Any] | None, scope_kind: str, scope_id: str | None) -> dict[str, Any]:
    r = row or {}
    out: dict[str, Any] = {
        "scope": {"kind": scope_kind, "id": scope_id},
        "inapp": r.get("inapp"), "email": r.get("email"), "events": r.get("events") or {},
        "version": r.get("version", 0),
    }
    if scope_kind == "global":
        out.update({
            "timezone": r.get("timezone") or "UTC",
            "quietHours": {"enabled": bool(r.get("quiet_enabled")),
                           "start": r.get("quiet_start") or "22:00",
                           "end": r.get("quiet_end") or "07:00"},
            "digest": {"period": r.get("digest_period") or "daily",
                       "hour": r.get("digest_hour", 8),
                       "lastSentAt": _iso(r.get("last_digest_at"))},
        })
    return out


def _effective(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    return {k: dict(zip(("inapp", "email"), resolve(rows, k), strict=True)) for k in KINDS}


# ------------------------------------------------------------- scope access (link and scopes)


async def _run_scope(db, p: Principal, run_id: str):
    """(project_id, session_id, workspace_id) of a run the caller may reach, else None."""
    row = (await db.execute(
        text("SELECT r.project_id AS project_id, pr.team_id AS team_id,"
             " pr.workspace_id AS workspace_id FROM agent_runs r JOIN projects pr"
             " ON pr.id = r.project_id WHERE r.id = :r"), {"r": run_id})).mappings().first()
    if row is None or row["team_id"] != p.teamId:
        return None
    link = (await db.execute(text("SELECT session_id FROM session_runs WHERE run_id = :r"),
                             {"r": run_id})).first()
    return row["project_id"], (link[0] if link else None), row["workspace_id"]


async def _can_see(db, p: Principal, *, run_id: str | None = None, session_id: str | None = None,
                   workspace_id: str | None = None) -> tuple[bool, str | None]:
    """Every scope in the list must be visible to the caller RIGHT NOW (membership, team, deleted
    state, private workspace). Returns (ok, project_id of the run when there is one)."""
    project_id = None
    if run_id:
        scope = await _run_scope(db, p, run_id)
        if scope is None:
            return False, None
        project_id = scope[0]
        session_id = session_id or scope[1]
        workspace_id = workspace_id or scope[2]
    try:
        if session_id:
            await sessions._load(db, p, session_id)
        if workspace_id:
            await workspaces._load(db, p, workspace_id)
    except HTTPException:
        return False, None
    return True, project_id


async def _require_scope(db, p: Principal, kind: str, scope_id: str) -> dict[str, Any]:
    """Visible workspace / session / run or 404. Returns {workspace_id, session_id} for rows."""
    ws = sess = None
    if kind == "workspace":
        ok, _ = await _can_see(db, p, workspace_id=scope_id)
        ws = scope_id
    elif kind == "session":
        ok, _ = await _can_see(db, p, session_id=scope_id)
        sess = scope_id
    else:
        ok, _ = await _can_see(db, p, run_id=scope_id)
    if not ok:
        raise _err(404, "NOT_FOUND", f"{kind} not found")
    if kind == "session":
        row = (await db.execute(text("SELECT workspace_id FROM collab_sessions WHERE id=:s"),
                                {"s": scope_id})).first()
        ws = row[0] if row else None
    return {"workspace_id": ws, "session_id": sess}


# ------------------------------------------------------------- item output


def _preview(code: str, count: int) -> str:
    one, many = PREVIEWS.get(code, ("You have a notification", "{n} notifications"))
    return one if count <= 1 else many.format(n=count)


def _item(r: Any, now: datetime) -> dict[str, Any]:
    state, until = r["state"], _ts(r["snoozed_until"])
    if state == "snoozed" and until is not None and until <= now:
        state = "unread"  # a snooze that ran out brings the item back
    try:
        delivery = json.loads(r["delivery"] or "{}")
    except ValueError:
        delivery = {}
    return {
        "id": r["id"], "kind": r["kind"], "state": state, "urgent": bool(r["urgent"]),
        "count": r["count"], "summaryCode": r["summary_code"],
        "preview": _preview(r["summary_code"], r["count"]),
        "sourceKind": r["source_kind"], "sourceId": r["source_id"], "runId": r["run_id"],
        "sessionId": r["session_id"], "workspaceId": r["workspace_id"],
        "createdAt": _iso(r["created_at"]), "updatedAt": _iso(r["updated_at"]),
        "snoozedUntil": _iso(r["snoozed_until"]) if state == "snoozed" else None,
        "delivery": delivery, "link": f"/api/v1/notifications/link/{r['id']}",
    }


async def _mine(db, p: Principal, nid: str):
    row = (await db.execute(
        text("SELECT * FROM notification_inbox WHERE id=:i AND team_id=:t AND user_id=:u"),
        {"i": nid, "t": p.teamId, "u": p.userId})).mappings().first()
    if row is None:
        raise _err(404, "NOT_FOUND", _NOT_FOUND)
    return row


_LIVE = ("(state = 'unread' OR (state = 'snoozed' AND snoozed_until <= :now))")


async def _audit(db, action: str, p: Principal, target_id: str, detail: dict) -> None:
    await emit_audit(db, action, user_id=p.userId, team_id=p.teamId,
                     target_type="notification", target_id=target_id, detail=detail)


# ------------------------------------------------------------- inbox


def _encode_cursor(ts: datetime, nid: str) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{nid}".encode()).decode()


def _decode_cursor(c: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(c.encode()).decode()
        ts, nid = raw.split("|", 1)
        return as_utc(datetime.fromisoformat(ts)), nid
    except Exception as e:  # noqa: BLE001
        raise _err(422, "VALIDATION_ERROR", "invalid cursor") from e


@router.get("")
async def list_inbox(request: Request, p: Auth, unread: bool = False, kind: str | None = None,
                     state: str | None = None, limit: int = 30, cursor: str | None = None) -> dict:
    if not 1 <= limit <= 100:
        raise _err(422, "VALIDATION_ERROR", "limit must be 1-100")
    if kind is not None and kind not in KINDS:
        raise _err(422, "VALIDATION_ERROR", "unknown kind")
    if state not in (None, "unread", "read", "snoozed", "dismissed"):
        raise _err(422, "VALIDATION_ERROR", "unknown state")
    now = now_utc()
    where, prm = ["team_id=:t", "user_id=:u"], {"t": p.teamId, "u": p.userId, "now": now}
    if unread or state == "unread":
        where.append(_LIVE)
    elif state == "snoozed":
        where.append("(state = 'snoozed' AND snoozed_until > :now)")
    elif state in ("read", "dismissed"):
        where.append("state = :st")
        prm["st"] = state
    else:
        where.append(
            "(state IN ('unread','read') OR (state = 'snoozed' AND snoozed_until <= :now))")
    if kind:
        where.append("kind = :k")
        prm["k"] = kind
    if cursor:
        cts, cid = _decode_cursor(cursor)
        where.append("(updated_at < :cts OR (updated_at = :cts AND id < :cid))")
        prm.update({"cts": cts, "cid": cid})
    async with request.app.state.session_factory() as db:
        rows = (await db.execute(
            text(f"SELECT * FROM notification_inbox WHERE {' AND '.join(where)}"
                 " ORDER BY updated_at DESC, id DESC LIMIT :n"),
            {**prm, "n": limit + 1})).mappings().all()
    page = rows[:limit]
    nxt = _encode_cursor(_ts(page[-1]["updated_at"]), page[-1]["id"]) if len(rows) > limit else None
    return {"items": [_item(r, now) for r in page], "nextCursor": nxt}


@router.get("/unread-count")
async def unread_count(request: Request, p: Auth) -> dict:
    now = now_utc()
    async with request.app.state.session_factory() as db:
        row = (await db.execute(
            text("SELECT COUNT(*), COALESCE(SUM(CASE WHEN urgent THEN 1 ELSE 0 END), 0)"
                 f" FROM notification_inbox WHERE team_id=:t AND user_id=:u AND {_LIVE}"),
            {"t": p.teamId, "u": p.userId, "now": now})).first()
    return {"unread": int(row[0]), "urgentUnread": int(row[1])}


class SnoozeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)
    until: datetime | None = None


class ReadAllBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str | None = Field(default=None, max_length=20)


@router.post("/read-all")
async def read_all(request: Request, p: Auth, body: ReadAllBody | None = None) -> dict:
    kind = body.kind if body else None
    if kind is not None and kind not in KINDS:
        raise _err(422, "VALIDATION_ERROR", "unknown kind")
    now = now_utc()
    sql = ("UPDATE notification_inbox SET state='read', read_at=:now, snoozed_until=NULL"
           f" WHERE team_id=:t AND user_id=:u AND {_LIVE}")
    prm: dict[str, Any] = {"t": p.teamId, "u": p.userId, "now": now}
    if kind:
        sql += " AND kind=:k"
        prm["k"] = kind
    async with request.app.state.session_factory() as db:
        res = await db.execute(text(sql), prm)
        await db.commit()
    return {"updated": res.rowcount}


async def _transition(request: Request, p: Principal, nid: str, new: str, *,
                      until: datetime | None = None) -> dict:
    now = now_utc()
    async with request.app.state.session_factory() as db:
        row = await _mine(db, p, nid)
        if new == "snoozed" and row["state"] == "dismissed":
            raise _err(409, "ILLEGAL_STATE", "a dismissed notification cannot be snoozed")
        if new == "read" and row["state"] == "dismissed":
            return _item(row, now)  # dismissed stays dismissed
        if row["state"] != new or new == "snoozed":
            await db.execute(
                text("UPDATE notification_inbox SET state=:s, snoozed_until=:su, read_at=:ra"
                     " WHERE id=:i"),
                {"s": new, "su": until, "ra": now if new == "read" else row["read_at"], "i": nid})
            await db.commit()
            row = await _mine(db, p, nid)
            if new == "snoozed":
                await _audit(db, "notification.snoozed", p, nid, {
                    "kind": row["kind"], "until": until.isoformat() if until else None})
        return _item(row, now)


@router.post("/{nid}/read")
async def mark_read(nid: str, request: Request, p: Auth) -> dict:
    return await _transition(request, p, nid, "read")


@router.post("/{nid}/dismiss")
async def dismiss(nid: str, request: Request, p: Auth) -> dict:
    return await _transition(request, p, nid, "dismissed")


@router.post("/{nid}/snooze")
async def snooze(nid: str, body: SnoozeBody, request: Request, p: Auth) -> dict:
    if (body.minutes is None) == (body.until is None):
        raise _err(422, "VALIDATION_ERROR", "give exactly one of minutes or until")
    now = now_utc()
    until = now + timedelta(minutes=body.minutes) if body.minutes else as_utc(body.until)
    if until <= now or until - now > MAX_SNOOZE:
        raise _err(422, "VALIDATION_ERROR", "snooze must end within the next 30 days")
    return await _transition(request, p, nid, "snoozed", until=until)


# ------------------------------------------------------------- link re-check (47.007)


@router.get("/link/{nid}")
async def open_link(nid: str, request: Request, p: Auth) -> dict:
    """The target path of one notification, or 404 when it is not the caller's OR the caller can
    no longer see the run, session or workspace it points at. Checked on every open."""
    async with request.app.state.session_factory() as db:
        row = await _mine(db, p, nid)
        ok, project_id = await _can_see(db, p, run_id=row["run_id"], session_id=row["session_id"],
                                        workspace_id=row["workspace_id"])
    if not ok:
        raise _err(404, "NOT_FOUND", _NOT_FOUND)
    if row["run_id"] and project_id:
        path = f"/projects/{quote(project_id)}/runs/{quote(row['run_id'])}"
    elif row["session_id"]:
        path = f"/sessions/{quote(row['session_id'])}"
    elif row["workspace_id"]:
        path = f"/workspaces/{quote(row['workspace_id'])}"
    else:  # a team-level row: a spend alert is acted on in billing, anything else in the inbox
        path = "/settings/billing" if row["kind"] == "budget" else "/notifications"
    if row["source_kind"] in ("approval", "comment", "handoff", "suggestion"):
        path += f"?focus={quote(row['source_kind'])}:{quote(row['source_id'])}"
    return {"notificationId": nid, "path": path}


# ------------------------------------------------------------- preferences


class _QuietBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    start: str | None = Field(default=None, max_length=5)
    end: str | None = Field(default=None, max_length=5)


class _DigestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str | None = Field(default=None, max_length=8)
    hour: int | None = Field(default=None, ge=0, le=23)


class ScopePrefsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inapp: str | None = Field(default=None, max_length=16)
    email: str | None = Field(default=None, max_length=16)
    events: dict[str, dict[str, str | None] | None] | None = None
    expectedVersion: int | None = Field(default=None, ge=1)


class GlobalPrefsBody(ScopePrefsBody):
    timezone: str | None = Field(default=None, max_length=64)
    quietHours: _QuietBody | None = None
    digest: _DigestBody | None = None


def _check_mode(field: str, v: str | None, allowed: tuple[str, ...]) -> None:
    if v is not None and v not in allowed:
        raise _err(422, "VALIDATION_ERROR", f"{field} must be one of {', '.join(allowed)}")


def _merge_events(old: dict, patch: dict) -> dict:
    """Merge-patch: an entry set to null removes it, a null field inside an entry clears it."""
    if len(patch) > len(KINDS):
        raise _err(422, "VALIDATION_ERROR", "too many event entries")
    out = {k: dict(v) for k, v in old.items()}
    for k, ev in patch.items():
        if k not in KINDS:
            raise _err(422, "VALIDATION_ERROR", f"unknown event kind: {k!r}")
        if ev is None:
            out.pop(k, None)
            continue
        if set(ev) - {"inapp", "email"}:
            raise _err(422, "VALIDATION_ERROR", "event entries take only inapp and email")
        _check_mode("events.inapp", ev.get("inapp"), INAPP_MODES)
        _check_mode("events.email", ev.get("email"), EMAIL_MODES)
        cur = {**out.get(k, {}), **ev}
        cur = {a: b for a, b in cur.items() if b is not None}
        if cur:
            out[k] = cur
        else:
            out.pop(k, None)
    return out


async def _live_mutes(db, p: Principal, now: datetime) -> list[dict[str, Any]]:
    rows = (await db.execute(
        text("SELECT * FROM notification_mutes WHERE team_id=:t AND user_id=:u AND ended_at IS NULL"
             " AND (muted_until IS NULL OR muted_until > :now) ORDER BY created_at, id"),
        {"t": p.teamId, "u": p.userId, "now": now})).mappings().all()
    return [_mute_out(r) for r in rows]


def _mute_out(r: Any) -> dict[str, Any]:
    return {"id": r["id"], "scopeKind": r["scope_kind"], "scopeId": r["scope_id"],
            "until": _iso(r["muted_until"]), "createdAt": _iso(r["created_at"]),
            "endedAt": _iso(r["ended_at"])}


async def _get_scope_row(db, p: Principal, kind: str, sid: str):
    return (await db.execute(
        text("SELECT * FROM notification_preferences WHERE team_id=:t AND user_id=:u"
             " AND scope_kind=:k AND scope_id=:s"),
        {"t": p.teamId, "u": p.userId, "k": kind, "s": sid})).mappings().first()


async def _save_prefs(db, p: Principal, kind: str, sid: str, changes: dict[str, Any],
                      expected: int | None) -> tuple[dict | None, dict, dict]:
    """Upsert the changed columns (CAS on version). Returns (row, before, after) of what changed;
    both are empty when nothing changed (idempotent PUT: no write, no audit)."""
    row = await _get_scope_row(db, p, kind, sid)
    cur = _load_prefs(row) if row is not None else {}
    cur_version = cur.get("version", 0)
    if expected is not None and expected != cur_version:
        raise _err(409, "VERSION_CONFLICT", f"preferences are at version {cur_version}")
    default = {"inapp": None, "email": None, "events": {}, "timezone": None, "quiet_enabled": False,
               "quiet_start": None, "quiet_end": None, "digest_period": "daily", "digest_hour": 8}
    before = {k: cur.get(k, default[k]) for k in changes}
    changed = {k: v for k, v in changes.items() if before[k] != v}
    if not changed:
        return row, {}, {}
    now = now_utc()
    cols = {k: (json.dumps(v, sort_keys=True) if k == "events" else v) for k, v in changed.items()}
    if row is None:
        full = {**default, **changed}
        try:
            await db.execute(
                text("INSERT INTO notification_preferences (id, team_id, user_id, scope_kind,"
                     " scope_id, inapp, email, events, timezone, quiet_enabled, quiet_start,"
                     " quiet_end, digest_period, digest_hour, version, created_at, updated_at)"
                     " VALUES (:id,:t,:u,:k,:s,:inapp,:email,:events,:tz,:qe,:qs,:qn,:dp,:dh,1,"
                     ":now,:now)"),
                {"id": str(uuid4()), "t": p.teamId, "u": p.userId, "k": kind, "s": sid,
                 "inapp": full["inapp"], "email": full["email"],
                 "events": json.dumps(full["events"], sort_keys=True), "tz": full["timezone"],
                 "qe": full["quiet_enabled"], "qs": full["quiet_start"], "qn": full["quiet_end"],
                 "dp": full["digest_period"], "dh": full["digest_hour"], "now": now})
        except IntegrityError as e:
            await db.rollback()
            raise _err(409, "VERSION_CONFLICT", "preferences changed concurrently") from e
    else:
        sets = ", ".join(f"{k}=:{k}" for k in cols)
        res = await db.execute(
            text(f"UPDATE notification_preferences SET {sets}, version=version+1, updated_at=:now"
                 " WHERE id=:id AND version=:v"),
            {**cols, "now": now, "id": row["id"], "v": cur_version})
        if res.rowcount != 1:
            await db.rollback()
            raise _err(409, "VERSION_CONFLICT", "preferences changed concurrently")
    await db.commit()
    return await _get_scope_row(db, p, kind, sid), before, changed


async def _pref_response(db, p: Principal, kind: str, sid: str, row: Any) -> dict[str, Any]:
    out = _pref_out(_load_prefs(row) if row is not None else None, kind,
                    None if kind == "global" else sid)
    if kind == "global":
        out["mutes"] = await _live_mutes(db, p, now_utc())
        out["kinds"] = list(KINDS)
        out["effective"] = _effective([_load_prefs(row)] if row is not None else [])
    return out


@router.get("/preferences")
async def get_preferences(request: Request, p: Auth) -> dict:
    async with request.app.state.session_factory() as db:
        row = await _get_scope_row(db, p, "global", "*")
        out = await _pref_response(db, p, "global", "*", row)
    return out


@router.put("/preferences")
async def put_preferences(body: GlobalPrefsBody, request: Request, p: Auth) -> dict:
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    async with request.app.state.session_factory() as db:
        row = await _get_scope_row(db, p, "global", "*")
        cur = _load_prefs(row) if row is not None else {}
        for f in ("inapp", "email"):
            if f in sent:
                _check_mode(f, getattr(body, f), INAPP_MODES if f == "inapp" else EMAIL_MODES)
                changes[f] = getattr(body, f)
        if "events" in sent and body.events is not None:
            changes["events"] = _merge_events(cur.get("events", {}), body.events)
        if "timezone" in sent:
            tz = body.timezone
            if tz is not None:
                try:
                    ZoneInfo(tz)
                except Exception as e:  # noqa: BLE001
                    raise _err(422, "INVALID_TIMEZONE", "unknown time zone") from e
            changes["timezone"] = tz
        if body.quietHours is not None:
            q = body.quietHours
            start = q.start if q.start is not None else (cur.get("quiet_start") or "22:00")
            end = q.end if q.end is not None else (cur.get("quiet_end") or "07:00")
            enabled = q.enabled if q.enabled is not None else bool(cur.get("quiet_enabled"))
            if _minutes(start) is None or _minutes(end) is None or start == end:
                raise _err(422, "INVALID_QUIET_HOURS",
                           "start and end must be different HH:MM times")
            changes.update(quiet_enabled=enabled, quiet_start=start, quiet_end=end)
        if body.digest is not None:
            _check_mode("digest.period", body.digest.period, DIGEST_PERIODS)
            if body.digest.period is not None:
                changes["digest_period"] = body.digest.period
            if body.digest.hour is not None:
                changes["digest_hour"] = body.digest.hour
        row, before, after = await _save_prefs(db, p, "global", "*", changes, body.expectedVersion)
        if after:
            await _audit(db, "notification.preferences_changed", p, "global", {
                "scopeKind": "global", "scopeId": None, "before": before, "after": after})
        return await _pref_response(db, p, "global", "*", row)


@router.get("/preferences/scopes/{scope_kind}/{scope_id}")
async def get_scope_preferences(scope_kind: str, scope_id: str, request: Request, p: Auth) -> dict:
    if scope_kind not in SCOPE_KINDS:
        raise _err(422, "VALIDATION_ERROR", "scopeKind must be workspace or session")
    async with request.app.state.session_factory() as db:
        ids = await _require_scope(db, p, scope_kind, scope_id)
        row = await _get_scope_row(db, p, scope_kind, scope_id)
        out = await _pref_response(db, p, scope_kind, scope_id, row)
        out["effective"] = _effective(
            await _pref_rows(db, p.teamId, p.userId, ids["workspace_id"], ids["session_id"]))
    return out


@router.put("/preferences/scopes/{scope_kind}/{scope_id}")
async def put_scope_preferences(scope_kind: str, scope_id: str, body: ScopePrefsBody,
                                request: Request, p: Auth) -> dict:
    if scope_kind not in SCOPE_KINDS:
        raise _err(422, "VALIDATION_ERROR", "scopeKind must be workspace or session")
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    async with request.app.state.session_factory() as db:
        ids = await _require_scope(db, p, scope_kind, scope_id)
        row = await _get_scope_row(db, p, scope_kind, scope_id)
        cur = _load_prefs(row) if row is not None else {}
        for f in ("inapp", "email"):
            if f in sent:
                _check_mode(f, getattr(body, f), INAPP_MODES if f == "inapp" else EMAIL_MODES)
                changes[f] = getattr(body, f)
        if "events" in sent and body.events is not None:
            changes["events"] = _merge_events(cur.get("events", {}), body.events)
        row, before, after = await _save_prefs(db, p, scope_kind, scope_id, changes,
                                               body.expectedVersion)
        if after:
            await _audit(db, "notification.preferences_changed", p, scope_id, {
                "scopeKind": scope_kind, "scopeId": scope_id, "before": before, "after": after})
        out = await _pref_response(db, p, scope_kind, scope_id, row)
        out["effective"] = _effective(
            await _pref_rows(db, p.teamId, p.userId, ids["workspace_id"], ids["session_id"]))
    return out


# ------------------------------------------------------------- mute


class MuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopeKind: str = Field(max_length=12)
    scopeId: str = Field(min_length=1, max_length=80)
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 365)
    until: datetime | None = None  # neither: muted until unmuted


@router.post("/mute", status_code=201)
async def mute(body: MuteBody, request: Request, p: Auth, response: Response) -> dict:
    if body.scopeKind not in MUTE_KINDS:
        raise _err(422, "VALIDATION_ERROR", "scopeKind must be workspace, session, run or kind")
    if body.minutes is not None and body.until is not None:
        raise _err(422, "VALIDATION_ERROR", "give minutes or until, not both")
    now = now_utc()
    until = (now + timedelta(minutes=body.minutes) if body.minutes
             else as_utc(body.until) if body.until else None)
    if until is not None and until <= now:
        raise _err(422, "VALIDATION_ERROR", "until must be in the future")
    async with request.app.state.session_factory() as db:
        if body.scopeKind == "kind":
            if body.scopeId not in KINDS:
                raise _err(422, "VALIDATION_ERROR", "unknown kind")
        elif len(body.scopeId) > 36:
            raise _err(404, "NOT_FOUND", f"{body.scopeKind} not found")
        else:
            await _require_scope(db, p, body.scopeKind, body.scopeId)
        # A mute whose time ran out is closed (kept as history) so a new one can start.
        await db.execute(
            text("UPDATE notification_mutes SET ended_at=muted_until WHERE team_id=:t"
                 " AND user_id=:u AND ended_at IS NULL AND muted_until IS NOT NULL"
                 " AND muted_until <= :now"),
            {"t": p.teamId, "u": p.userId, "now": now})
        live = (await db.execute(
            text("SELECT * FROM notification_mutes WHERE team_id=:t AND user_id=:u"
                 " AND scope_kind=:k AND scope_id=:s AND ended_at IS NULL"),
            {"t": p.teamId, "u": p.userId, "k": body.scopeKind,
             "s": body.scopeId})).mappings().first()
        if live is not None:
            response.status_code = 200  # idempotent retry
            if _ts(live["muted_until"]) != until:
                await db.execute(text("UPDATE notification_mutes SET muted_until=:m WHERE id=:i"),
                                 {"m": until, "i": live["id"]})
                await db.commit()
                await _audit(db, "notification.muted", p, live["id"], {
                    "scopeKind": body.scopeKind, "scopeId": body.scopeId,
                    "until": until.isoformat() if until else None})
            else:
                await db.commit()
            row = (await db.execute(text("SELECT * FROM notification_mutes WHERE id=:i"),
                                    {"i": live["id"]})).mappings().first()
            return _mute_out(row)
        mid = str(uuid4())
        try:
            await db.execute(
                text("INSERT INTO notification_mutes (id, team_id, user_id, scope_kind, scope_id,"
                     " muted_until, created_at) VALUES (:i,:t,:u,:k,:s,:m,:now)"),
                {"i": mid, "t": p.teamId, "u": p.userId, "k": body.scopeKind, "s": body.scopeId,
                 "m": until, "now": now})
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise _err(409, "MUTE_CONFLICT", "a mute for this scope already exists") from None
        await _audit(db, "notification.muted", p, mid, {
            "scopeKind": body.scopeKind, "scopeId": body.scopeId,
            "until": until.isoformat() if until else None})
        row = (await db.execute(text("SELECT * FROM notification_mutes WHERE id=:i"),
                                {"i": mid})).mappings().first()
    return _mute_out(row)


@router.delete("/mute/{mute_id}", status_code=204)
async def unmute(mute_id: str, request: Request, p: Auth) -> None:
    """Ends the mute (kept as history). Repeating the call is a no-op."""
    now = now_utc()
    async with request.app.state.session_factory() as db:
        row = (await db.execute(
            text("SELECT * FROM notification_mutes WHERE id=:i AND team_id=:t AND user_id=:u"),
            {"i": mute_id, "t": p.teamId, "u": p.userId})).mappings().first()
        if row is None:
            raise _err(404, "NOT_FOUND", "mute not found")
        if row["ended_at"] is None:
            res = await db.execute(
                text("UPDATE notification_mutes SET ended_at=:now WHERE id=:i"
                     " AND ended_at IS NULL"),
                {"now": now, "i": mute_id})
            await db.commit()
            if res.rowcount == 1:
                await _audit(db, "notification.unmuted", p, mute_id, {
                    "scopeKind": row["scope_kind"], "scopeId": row["scope_id"]})


# ------------------------------------------------------------- unsubscribe (token, no login)


class UnsubscribeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=200)


async def _clear_email_overrides(db, team_id: str, user_id: str, now: datetime) -> int:
    """Unsubscribe means no more email: drop the user's own per-scope and per-event email
    overrides too (they would otherwise win over the global setting by precedence)."""
    n = 0
    rows = (await db.execute(
        text("SELECT * FROM notification_preferences WHERE team_id=:t AND user_id=:u"),
        {"t": team_id, "u": user_id})).mappings().all()
    for r in rows:
        prefs = _load_prefs(r)
        events = {k: {a: b for a, b in ev.items() if a != "email"}
                  for k, ev in prefs["events"].items() if isinstance(ev, dict)}
        events = {k: ev for k, ev in events.items() if ev}
        email = prefs["email"] if r["scope_kind"] == "global" else None
        if events == prefs["events"] and email == prefs["email"]:
            continue
        await db.execute(
            text("UPDATE notification_preferences SET email=:e, events=:ev, version=version+1,"
                 " updated_at=:now WHERE id=:i"),
            {"e": email, "ev": json.dumps(events, sort_keys=True), "now": now, "i": r["id"]})
        n += 1
    return n


@router.post("/unsubscribe")
async def unsubscribe(body: UnsubscribeBody, request: Request) -> dict:
    """Email unsubscribe from the link in a mail: the token IS the credential (no session).

    Idempotent and single-use: the first call switches the email channel off for that person and
    audits it, a repeat answers 200 ``alreadyApplied`` and changes nothing (so an old mail can never
    switch off an email channel the person has re-enabled since). Unknown or expired: 404."""
    now = now_utc()
    h = hash_token(body.token)
    async with request.app.state.session_factory() as db:
        tok = (await db.execute(
            text("SELECT * FROM notification_unsub_tokens WHERE token_hash=:h"),
            {"h": h})).mappings().first()
        if tok is None or (tok["used_at"] is None and _ts(tok["expires_at"]) <= now):
            raise _err(404, "NOT_FOUND", "unsubscribe link not found or expired")
        if tok["used_at"] is not None:
            return {"unsubscribed": True, "alreadyApplied": True}
        res = await db.execute(
            text("UPDATE notification_unsub_tokens SET used_at=:now WHERE token_hash=:h"
                 " AND used_at IS NULL"), {"now": now, "h": h})
        if res.rowcount != 1:
            await db.rollback()
            return {"unsubscribed": True, "alreadyApplied": True}
        actor = Principal(userId=tok["user_id"], teamId=tok["team_id"], role="viewer")
        _, before, after = await _save_prefs(db, actor, "global", "*", {"email": "off"}, None)
        cleared = await _clear_email_overrides(db, tok["team_id"], tok["user_id"], now)
        pending = (await db.execute(
            text("SELECT id FROM notification_inbox WHERE team_id=:t AND user_id=:u"
                 " AND email_state IN ('pending','digest')"),
            {"t": tok["team_id"], "u": tok["user_id"]})).scalars().all()
        for nid in pending:
            await _set_email(db, nid, "skipped", "unsubscribed")
        await db.commit()
        await emit_audit(db, "notification.unsubscribed", user_id=tok["user_id"],
                         team_id=tok["team_id"], target_type="notification",
                         target_id=tok["user_id"], detail={"channel": "email", "before": before,
                                                          "after": after,
                                                          "clearedOverrides": cleared})
    return {"unsubscribed": True, "alreadyApplied": False}


# ------------------------------------------------------------- email delivery


def _app_url() -> str:
    return os.environ.get("HARNESS_APP_URL", "").rstrip("/")


async def _set_email(db, nid: str, state: str, reason: str | None, *, attempts: int | None = None,
                     due: datetime | None = None, at: datetime | None = None) -> None:
    """One place that moves the email channel of a row: machine columns plus the visible JSON."""
    row = (await db.execute(text("SELECT delivery, email_attempts FROM notification_inbox"
                                 " WHERE id=:i"), {"i": nid})).first()
    try:
        d = json.loads(row[0] or "{}")
    except ValueError:
        d = {}
    n = row[1] if attempts is None else attempts
    d["email"] = {"state": state, "reason": reason, "attempts": n,
                  "dueAt": due.isoformat() if due else None,
                  "lastAttemptAt": (at.isoformat() if at
                                    else (d.get("email") or {}).get("lastAttemptAt"))}
    await db.execute(
        text("UPDATE notification_inbox SET email_state=:s, email_attempts=:n, email_due_at=:due,"
             " delivery=:d WHERE id=:i"),
        {"s": state, "n": n, "due": due, "d": json.dumps(d), "i": nid})


async def _new_unsub_url(db, team_id: str, user_id: str, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    await db.execute(
        text("INSERT INTO notification_unsub_tokens (token_hash, team_id, user_id, created_at,"
             " expires_at) VALUES (:h,:t,:u,:now,:exp)"),
        {"h": hash_token(token), "t": team_id, "u": user_id, "now": now,
         "exp": now + timedelta(days=UNSUB_TTL_DAYS)})
    return f"{_app_url()}/notifications/unsubscribe?token={token}"


def _compose(subject: str, lines: list[str], open_url: str, unsub_url: str) -> tuple[str, str, str]:
    plain = "\n".join([*lines, "", f"Open your notifications: {open_url}",
                       f"Stop these emails: {unsub_url}"])
    body = "".join(f"<p>{_html.escape(ln)}</p>" for ln in lines)
    html_body = (
        '<!DOCTYPE html><html><body style="font-family:sans-serif">'
        f"<h2>{_html.escape(subject)}</h2>{body}"
        f'<p><a href="{_html.escape(open_url)}">Open your notifications</a></p>'
        f'<p style="color:#666;font-size:12px"><a href="{_html.escape(unsub_url)}">'
        "Stop these emails</a>. Sent by Harness; this message holds no run or comment content."
        "</p></body></html>")
    return subject, html_body, plain


async def _recipient(db, team_id: str, user_id: str) -> str | None:
    row = (await db.execute(
        text("SELECT u.email FROM users u JOIN team_members m ON m.user_id = u.id"
             " WHERE u.id=:u AND m.team_id=:t AND u.is_active"),
        {"u": user_id, "t": team_id})).first()
    return row[0] if row else None


def _delivery(delivery: Any, settings: Any):
    if delivery is not None:
        return delivery
    from .notifier import NotifierDelivery, _settings

    return NotifierDelivery(settings if settings is not None else _settings())


async def _failed(db, team_id: str, user_id: str, nids: list[str], error: str, now: datetime,
                  audits: list[tuple]) -> None:
    audits.append(("notification.delivery_failed", team_id, user_id, nids[0],
                   {"channel": "email", "notificationIds": nids[:20], "error": error[:200]}))


async def deliver_due(factory, now: datetime, *, delivery=None, settings=None,
                      limit: int = 100) -> dict[str, int]:
    """Send the immediate emails that are due at ``now`` (injectable clock).

    Re-checks at send time: handled in-app or dismissed rows are skipped, a snoozed row waits,
    an email setting switched off since is honoured, quiet hours hold non-urgent mail until they
    end, retries back off, and after ``MAX_ATTEMPTS`` the row is marked failed and audited."""
    now = as_utc(now)
    out = {"scanned": 0, "sent": 0, "failed": 0, "retried": 0, "skipped": 0, "held": 0}
    audits: list[tuple] = []
    sender = _delivery(delivery, settings)
    async with factory() as db:
        rows = (await db.execute(
            text("SELECT * FROM notification_inbox WHERE email_state='pending'"
                 " AND email_due_at <= :now ORDER BY email_due_at, id LIMIT :n"),
            {"now": now, "n": limit})).mappings().all()
        for r in rows:
            out["scanned"] += 1
            nid, team, user = r["id"], r["team_id"], r["user_id"]
            until_ts = _ts(r["snoozed_until"])
            snoozed = r["state"] == "snoozed" and until_ts is not None and until_ts > now
            if r["state"] in ("read", "dismissed"):
                await _set_email(db, nid, "skipped", "handled_in_app")
                out["skipped"] += 1
                continue
            if snoozed:
                out["held"] += 1
                continue
            prefs = await _pref_rows(db, team, user, r["workspace_id"], r["session_id"])
            _, mode = resolve(prefs, r["kind"])
            urgent = bool(r["urgent"])
            if mode == "off" or (mode == "urgent" and not urgent):
                await _set_email(db, nid, "skipped", "opted_out")
                out["skipped"] += 1
                continue
            if mode == "digest" and not urgent:
                await _set_email(db, nid, "digest", "digest")
                continue
            glob = next((x for x in prefs if x["scope_kind"] == "global"), None)
            if glob and glob["quiet_enabled"] and not urgent:
                until = quiet_until(now, glob["timezone"], glob["quiet_start"], glob["quiet_end"])
                if until is not None:
                    await _set_email(db, nid, "pending", "quiet_hours", due=until)
                    out["held"] += 1
                    continue
            to = await _recipient(db, team, user)
            if to is None:
                await _set_email(db, nid, "skipped", "no_recipient")
                out["skipped"] += 1
                continue
            unsub = await _new_unsub_url(db, team, user, now)
            subject, html_body, plain = _compose(
                "Harness: " + _preview(r["summary_code"], r["count"]),
                [_preview(r["summary_code"], r["count"])],
                f"{_app_url()}/notifications?open={nid}", unsub)
            try:
                ok, error = await sender.deliver(to, subject, html_body, plain)
            except Exception as exc:  # noqa: BLE001 - a bad send must not stop the batch
                ok, error = False, f"{type(exc).__name__}"
            attempts = r["email_attempts"] + 1
            if ok:
                await _set_email(db, nid, "sent", "delivered", attempts=attempts, at=now)
                out["sent"] += 1
            elif attempts >= MAX_ATTEMPTS:
                await _set_email(db, nid, "failed", "send_failed", attempts=attempts, at=now)
                await _failed(db, team, user, [nid], error or "send failed", now, audits)
                out["failed"] += 1
            else:
                back = now + timedelta(seconds=RETRY_BASE_SECONDS * 2 ** (attempts - 1))
                await _set_email(db, nid, "pending", "retrying", attempts=attempts, due=back,
                                 at=now)
                out["retried"] += 1
        await db.commit()
        for action, team, user, target, detail in audits:
            await emit_audit(db, action, user_id=user, team_id=team, actor_kind="system",
                             target_type="notification", target_id=target, detail=detail)
    return out


async def run_digests(factory, now: datetime, *, delivery=None, settings=None) -> dict[str, int]:
    """Send one digest email per person whose digest is due at ``now`` (injectable clock).

    Due = the person's local time is past their digest hour, the last digest is older than a
    day (daily) or a week (weekly), and it is not inside their quiet hours. Only still-unread
    items are listed, handled ones are skipped. Retries back off and fail after MAX_ATTEMPTS."""
    now = as_utc(now)
    out = {"users": 0, "sent": 0, "failed": 0, "retried": 0, "skipped": 0}
    audits: list[tuple] = []
    sender = _delivery(delivery, settings)
    async with factory() as db:
        people = (await db.execute(
            text("SELECT DISTINCT team_id, user_id FROM notification_inbox"
                 " WHERE email_state='digest'"))).all()
        for team, user in people:
            g = (await db.execute(
                text("SELECT * FROM notification_preferences WHERE team_id=:t AND user_id=:u"
                     " AND scope_kind='global'"), {"t": team, "u": user})).mappings().first()
            gp = _load_prefs(g) if g is not None else {}
            zone = _zone(gp.get("timezone"))
            local = now.astimezone(zone)
            last = gp.get("last_digest_at")
            if local.hour < gp.get("digest_hour", 8):
                continue
            if last is not None:
                if gp.get("digest_period", "daily") == "weekly":
                    if now - last < timedelta(days=7):
                        continue
                elif last.astimezone(zone).date() >= local.date():
                    continue
            if gp.get("quiet_enabled") and quiet_until(
                    now, gp.get("timezone"), gp.get("quiet_start"), gp.get("quiet_end")):
                continue
            out["users"] += 1
            rows = (await db.execute(
                text("SELECT * FROM notification_inbox WHERE team_id=:t AND user_id=:u"
                     " AND email_state='digest' AND (email_due_at IS NULL OR email_due_at <= :now)"
                     " ORDER BY created_at, id"),
                {"t": team, "u": user, "now": now})).mappings().all()
            live = []
            for r in rows:
                if r["state"] in ("read", "dismissed"):
                    await _set_email(db, r["id"], "skipped", "handled_in_app")
                    out["skipped"] += 1
                elif r["state"] == "snoozed" and (_ts(r["snoozed_until"]) or now) > now:
                    continue
                else:
                    live.append(r)
            if not live:
                continue
            to = await _recipient(db, team, user)
            if to is None:
                for r in live:
                    await _set_email(db, r["id"], "skipped", "no_recipient")
                out["skipped"] += len(live)
                continue
            unsub = await _new_unsub_url(db, team, user, now)
            lines = [_preview(r["summary_code"], r["count"]) for r in live[:20]]
            if len(live) > 20:
                lines.append(f"and {len(live) - 20} more")
            subject, html_body, plain = _compose(f"Harness digest: {len(live)} items", lines,
                                                 f"{_app_url()}/notifications", unsub)
            try:
                ok, error = await sender.deliver(to, subject, html_body, plain)
            except Exception as exc:  # noqa: BLE001
                ok, error = False, f"{type(exc).__name__}"
            ids = [r["id"] for r in live]
            if ok:
                for r in live:
                    await _set_email(db, r["id"], "sent", "digest_delivered",
                                     attempts=r["email_attempts"] + 1, at=now)
                if g is None:
                    await _save_prefs(db, Principal(userId=user, teamId=team, role="viewer"),
                                      "global", "*", {"digest_period": "daily"}, None)
                await db.execute(
                    text("UPDATE notification_preferences SET last_digest_at=:now WHERE team_id=:t"
                         " AND user_id=:u AND scope_kind='global'"),
                    {"now": now, "t": team, "u": user})
                audits.append(("notification.digest_sent", team, user, ids[0], {
                    "channel": "email", "count": len(live),
                    "kinds": sorted({r["kind"] for r in live})}))
                out["sent"] += 1
            else:
                dead = False
                for r in live:
                    attempts = r["email_attempts"] + 1
                    if attempts >= MAX_ATTEMPTS:
                        dead = True
                        await _set_email(db, r["id"], "failed", "send_failed",
                                         attempts=attempts, at=now)
                    else:
                        back = now + timedelta(seconds=RETRY_BASE_SECONDS * 5 * 2 ** (attempts - 1))
                        await _set_email(db, r["id"], "digest", "retrying", attempts=attempts,
                                         due=back, at=now)
                if dead:
                    await _failed(db, team, user, ids, error or "send failed", now, audits)
                    out["failed"] += 1
                else:
                    out["retried"] += 1
        await db.commit()
        for action, team, user, target, detail in audits:
            await emit_audit(db, action, user_id=user, team_id=team, actor_kind="system",
                             target_type="notification", target_id=target, detail=detail)
    return out
