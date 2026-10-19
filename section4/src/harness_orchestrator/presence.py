"""Part 41 (lane P): approximate, privacy-controlled, expiring run presence.

Sections may import only ``harness_contracts``, so this speaks raw SQL to the tables of
migration 0042 (``presence_records``, ``team_presence_settings``, ``participant_prefs``)
and to the 0035 collaboration tables. Rules (docs/presence.md has the full contract):

* Visibility: every route sits behind the run ownership guard; a user whose team does not
  own the run gets the same 404 as an unknown run. Nothing here widens that.
* Heartbeats only from run participants; one row per (user, device). Reads merge devices
  per user (online beats idle beats disconnected) and never return exact timestamps,
  device ids or device counts: only status plus a coarse last-seen bucket.
* Expiry: a row is live while ``expires_at`` is in the future (heartbeat extends it by the
  team's ``stale_seconds``). Expired rows are kept 24 hours to derive the bucket, then purged.
* Team mode ``full`` (online/idle/disconnected + bucket), ``limited`` (online or unknown
  only, no last-seen) and ``off`` (nothing collected, heartbeats refused).
* Per-user ``share_presence``: ``nobody`` hides you from everyone but yourself, ``team``
  shows you to run participants, ``everyone`` to anyone who can see the run.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from harness_contracts.roles import Principal, Role
from harness_contracts.timeutil import db_now
from pydantic import BaseModel, Field
from sqlalchemy import text

from harness_orchestrator.api import RequirePrincipal, _authz_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["presence"])

DEFAULT_SETTINGS = {"mode": "full", "staleSeconds": 90, "maxParticipants": 20}
KEEP_EXPIRED = timedelta(hours=24)  # expired rows survive this long, for the last-seen bucket
MAX_DEVICES = 10  # presence rows per (user, run)
NOTICE = "Presence is approximate and does not guarantee that someone is available."

RunGuard = Annotated[Principal, Depends(_authz_dep("read", audit=True))]
_service: Any = None


def configure_presence(service: Any) -> None:
    """Deploy-time wiring: share the orchestrator's service (session factory)."""
    global _service
    _service = service


def now() -> datetime:
    """The clock for expiry decisions; tests replace this with a fake."""
    return db_now()


def err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})


def factory() -> Any:
    f = getattr(_service, "session_factory", None)
    if f is None:
        raise err(503, "PRESENCE_UNAVAILABLE", "no database wired")
    return f


def as_dt(v: Any) -> datetime:
    """sqlite hands back strings, PostgreSQL datetimes; always aware UTC."""
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


async def team_settings(db: Any, team_id: str) -> dict[str, Any]:
    """Effective settings (defaults when no row). A missing table (a schema older than
    0042) also yields the defaults: a store that predates presence cannot have limits."""
    try:
        async with db.begin_nested():  # savepoint: a failed read must not poison the txn
            row = (
                await db.execute(
                    text(
                        "SELECT mode, stale_seconds, max_participants, updated_by, updated_at"
                        " FROM team_presence_settings WHERE team_id = :t"
                    ),
                    {"t": team_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 - deliberate fail-open to defaults, see docstring
        logger.warning("presence settings unavailable; using defaults", exc_info=True)
        return {**DEFAULT_SETTINGS, "updatedBy": None, "updatedAt": None, "isDefault": True}
    if row is None:
        return {**DEFAULT_SETTINGS, "updatedBy": None, "updatedAt": None, "isDefault": True}
    return {
        "mode": row.mode,
        "staleSeconds": int(row.stale_seconds),
        "maxParticipants": int(row.max_participants),
        "updatedBy": row.updated_by,
        "updatedAt": as_dt(row.updated_at).isoformat(),
        "isDefault": False,
    }


async def audit(
    db: Any, p: Principal, action: str, target_type: str, target_id: str, detail: dict[str, Any]
) -> None:
    """Append an audit row inside the caller's transaction. Callers pass only fields
    they control (ids, modes, counts): no free text, no secrets."""
    member = (
        await db.execute(
            text("SELECT id FROM team_members WHERE team_id = :t AND user_id = :u LIMIT 1"),
            {"t": p.teamId, "u": p.userId},
        )
    ).first()
    await db.execute(
        text(
            "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type,"
            " target_id, detail, ts) VALUES (:a, 'human', :act, :tt, :tid, :d, :ts)"
        ),
        {
            "a": member.id if member else None,
            "act": action,
            "tt": target_type,
            "tid": target_id,
            "d": json.dumps({"userId": p.userId, "teamId": p.teamId, **detail}, default=str),
            "ts": db_now(),
        },
    )


async def event(db: Any, p: Principal, run_id: str, kind: str, payload: dict[str, Any]) -> None:
    """Product event for the run's live stream (same table and ordering as collab)."""
    if db.bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"collab-events:{run_id}"},
        )
    await db.execute(
        text(
            "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind,"
            " payload, ts) VALUES (:r, :t, :u, :k, :p, :ts)"
        ),
        {
            "r": run_id,
            "t": p.teamId,
            "u": p.userId,
            "k": kind,
            "p": json.dumps(payload, default=str),
            "ts": db_now(),
        },
    )


async def display_name(db: Any, team_id: str, user_id: str) -> str:
    row = (
        await db.execute(
            text(
                "SELECT display_name FROM team_members WHERE team_id = :t AND user_id = :u"
                " LIMIT 1"
            ),
            {"t": team_id, "u": user_id},
        )
    ).first()
    return (row.display_name if row and row.display_name else None) or "A participant"


# ------------------------------------------------------------------ status model


def bucket(age: timedelta) -> str | None:
    """Coarse last-seen bucket; None when older than the 24 hour retention."""
    s = age.total_seconds()
    if s < 300:
        return "under_5_min"
    if s < 3600:
        return "under_1_hour"
    if s < KEEP_EXPIRED.total_seconds():
        return "under_1_day"
    return None


def merge_status(rows: list[Any], mode: str, at: datetime) -> dict[str, Any]:
    """Merge one user's device rows into a single approximate status.

    online > idle > disconnected(+bucket) > unknown. ``limited`` collapses everything
    except live presence into ``unknown`` and never reports idle or a bucket.
    """
    live = [r for r in rows if as_dt(r.expires_at) > at]
    if live:
        online = any(r.state == "online" for r in live)
        return {"status": "online" if online or mode == "limited" else "idle"}
    if mode == "limited" or not rows:
        return {"status": "unknown"}
    b = bucket(at - max(as_dt(r.last_seen) for r in rows))
    return {"status": "disconnected", "lastSeen": b} if b else {"status": "unknown"}


def announcement(name: str, state: dict[str, Any]) -> str:
    """Plain sentence for an aria-live region: one per status."""
    s = state["status"]
    if s == "online":
        return f"{name} is online"
    if s == "idle":
        return f"{name} is idle"
    if s == "disconnected":
        return f"{name} disconnected"
    if s == "hidden":
        return f"{name} has hidden their presence"
    return f"{name}'s presence is unknown"


# ------------------------------------------------------------------------ routes


class HeartbeatRequest(BaseModel):
    deviceId: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    state: Literal["online", "idle"] = "online"


class LeaveRequest(BaseModel):
    deviceId: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$"
    )


class SettingsRequest(BaseModel):
    mode: Literal["full", "limited", "off"] | None = None
    staleSeconds: int | None = Field(default=None, ge=15, le=3600)
    maxParticipants: int | None = Field(default=None, ge=1, le=200)


async def _roster(db: Any, run_id: str) -> list[Any]:
    return (
        await db.execute(
            text("SELECT user_id, role FROM run_participants WHERE run_id = :r ORDER BY joined_at"),
            {"r": run_id},
        )
    ).all()


@router.get("/runs/{runId}/presence")
async def get_presence(runId: str, actor: RunGuard) -> dict[str, Any]:
    at = now()
    async with factory()() as db:
        cfg = await team_settings(db, actor.teamId)
        roster = await _roster(db, runId)
        body: dict[str, Any] = {
            "runId": runId,
            "mode": cfg["mode"],
            "approximate": True,
            "notice": NOTICE,
            "capacity": {"max": cfg["maxParticipants"], "current": len(roster)},
            "participants": [],
        }
        in_roster = any(r.user_id == actor.userId for r in roster)
        rows = []
        if cfg["mode"] != "off" and roster:
            rows = (
                await db.execute(
                    text(
                        "SELECT user_id, state, last_seen, expires_at FROM presence_records"
                        " WHERE scope_kind = 'run' AND scope_id = :r"
                    ),
                    {"r": runId},
                )
            ).all()
        prefs = {
            r.user_id: r
            for r in (
                await db.execute(
                    text("SELECT user_id, muted, notify, share_presence FROM participant_prefs"
                         " WHERE run_id = :r"),
                    {"r": runId},
                )
            ).all()
        }
        names = {
            r.user_id: r.display_name
            for r in (
                await db.execute(
                    text("SELECT user_id, display_name FROM team_members WHERE team_id = :t"),
                    {"t": actor.teamId},
                )
            ).all()
        }
    for r in roster:
        name = names.get(r.user_id) or "A participant"
        share = prefs[r.user_id].share_presence if r.user_id in prefs else "team"
        me = r.user_id == actor.userId
        if cfg["mode"] == "off":
            state: dict[str, Any] | None = None
        elif me or share == "everyone" or (share == "team" and in_roster):
            state = merge_status([x for x in rows if x.user_id == r.user_id], cfg["mode"], at)
        else:
            state = {"status": "hidden"}
        entry: dict[str, Any] = {
            "userId": r.user_id,
            "role": r.role,
            "isLeader": r.role == "leader",
            "self": me,
            "presence": state,
            "announcement": announcement(name, state) if state else f"{name} is in the session",
        }
        if me:
            pr = prefs.get(r.user_id)
            entry["preferences"] = {
                "muted": bool(pr.muted) if pr else False,
                "notify": bool(pr.notify) if pr else True,
                "sharePresence": share,
            }
        body["participants"].append(entry)
    return body


@router.post("/runs/{runId}/presence/heartbeat")
async def heartbeat(runId: str, req: HeartbeatRequest, actor: RunGuard) -> dict[str, Any]:
    at = now()
    async with factory()() as db:
        cfg = await team_settings(db, actor.teamId)
        if cfg["mode"] == "off":
            raise err(409, "PRESENCE_DISABLED", "your organization has turned presence off")
        mine = (
            await db.execute(
                text("SELECT 1 FROM run_participants WHERE run_id = :r AND user_id = :u"),
                {"r": runId, "u": actor.userId},
            )
        ).first()
        if mine is None:
            raise err(409, "NOT_PARTICIPANT", "join the run before sending presence")
        key = {"r": runId, "u": actor.userId}
        # Retention: drop this run's rows that expired more than 24 hours ago.
        await db.execute(
            text("DELETE FROM presence_records WHERE scope_kind = 'run' AND scope_id = :r"
                 " AND expires_at < :cut"),
            {"r": runId, "cut": at - KEEP_EXPIRED},
        )
        known = (
            await db.execute(
                text("SELECT device_id FROM presence_records WHERE scope_kind = 'run'"
                     " AND scope_id = :r AND user_id = :u"),
                key,
            )
        ).all()
        if req.deviceId not in {k.device_id for k in known} and len(known) >= MAX_DEVICES:
            # Reclaim this user's own expired rows first; only live devices count.
            await db.execute(
                text("DELETE FROM presence_records WHERE scope_kind = 'run' AND scope_id = :r"
                     " AND user_id = :u AND expires_at <= :now"),
                {**key, "now": at},
            )
            left = (
                await db.execute(
                    text("SELECT count(*) FROM presence_records WHERE scope_kind = 'run'"
                         " AND scope_id = :r AND user_id = :u"),
                    key,
                )
            ).scalar()
            if left >= MAX_DEVICES:
                raise err(429, "TOO_MANY_DEVICES", f"at most {MAX_DEVICES} active devices per run")
        state = "online" if cfg["mode"] == "limited" else req.state
        expires = at + timedelta(seconds=cfg["staleSeconds"])
        await db.execute(
            text(
                "INSERT INTO presence_records (scope_kind, scope_id, team_id, user_id,"
                " device_id, state, last_seen, expires_at)"
                " VALUES ('run', :r, :t, :u, :d, :s, :now, :exp)"
                " ON CONFLICT (scope_kind, scope_id, user_id, device_id) DO UPDATE SET"
                " state = excluded.state, last_seen = excluded.last_seen,"
                " expires_at = excluded.expires_at, team_id = excluded.team_id"
            ),
            {**key, "t": actor.teamId, "d": req.deviceId, "s": state, "now": at, "exp": expires},
        )
        await db.commit()
    return {
        "recorded": True,
        "state": state,
        "mode": cfg["mode"],
        "expiresInSeconds": cfg["staleSeconds"],
        "heartbeatEverySeconds": max(5, cfg["staleSeconds"] // 3),
    }


@router.post("/runs/{runId}/presence/leave")
async def presence_leave(
    runId: str, actor: RunGuard, req: LeaveRequest | None = None
) -> dict[str, Any]:
    """Go offline now: expire this device (or all of yours). Idempotent. Roster membership
    is untouched, see ``participation/leave`` for that."""
    at = now()
    sql = (
        "UPDATE presence_records SET expires_at = :now WHERE scope_kind = 'run' AND scope_id = :r"
        " AND user_id = :u AND expires_at > :now"
    )
    params: dict[str, Any] = {"r": runId, "u": actor.userId, "now": at}
    if req and req.deviceId:
        sql += " AND device_id = :d"
        params["d"] = req.deviceId
    async with factory()() as db:
        res = await db.execute(text(sql), params)
        await db.commit()
    return {"expiredDevices": res.rowcount}


def _settings_body(cfg: dict[str, Any], team_id: str) -> dict[str, Any]:
    return {"teamId": team_id, **cfg}


@router.get("/presence-settings")
async def get_settings(actor: RequirePrincipal) -> dict[str, Any]:
    async with factory()() as db:
        return _settings_body(await team_settings(db, actor.teamId), actor.teamId)


@router.put("/presence-settings")
async def put_settings(
    req: SettingsRequest, actor: RequirePrincipal
) -> dict[str, Any]:
    """Team admins only. Fields not sent keep their current value. Turning presence ``off``
    deletes the team's stored presence rows. Lowering the cap never evicts anyone."""
    if actor.role != Role.ADMIN:
        raise err(403, "ROLE_DENIED", "only team admins may change presence settings")
    async with factory()() as db:
        before = await team_settings(db, actor.teamId)
        after = {
            "mode": req.mode or before["mode"],
            "staleSeconds": req.staleSeconds or before["staleSeconds"],
            "maxParticipants": req.maxParticipants or before["maxParticipants"],
        }
        keys = ("mode", "staleSeconds", "maxParticipants")
        if all(after[k] == before[k] for k in keys) and not before["isDefault"]:
            return _settings_body(before, actor.teamId)  # no-op: no audit noise
        at = now()
        await db.execute(
            text(
                "INSERT INTO team_presence_settings (team_id, mode, stale_seconds,"
                " max_participants, updated_by, updated_at)"
                " VALUES (:t, :m, :s, :x, :u, :now)"
                " ON CONFLICT (team_id) DO UPDATE SET mode = excluded.mode,"
                " stale_seconds = excluded.stale_seconds,"
                " max_participants = excluded.max_participants,"
                " updated_by = excluded.updated_by, updated_at = excluded.updated_at"
            ),
            {"t": actor.teamId, "m": after["mode"], "s": after["staleSeconds"],
             "x": after["maxParticipants"], "u": actor.userId, "now": at},
        )
        if after["mode"] == "off":
            await db.execute(
                text("DELETE FROM presence_records WHERE team_id = :t"), {"t": actor.teamId}
            )
        await audit(
            db, actor, "presence.settings_changed", "team", actor.teamId,
            {"scope": "team", "before": {k: before[k] for k in keys},
             "after": {k: after[k] for k in keys}},
        )
        await db.commit()
        return _settings_body(await team_settings(db, actor.teamId), actor.teamId)
