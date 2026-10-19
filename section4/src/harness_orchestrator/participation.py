"""Part 41 (lane P): join/leave/observe/mute/rejoin, participant cap, notification prefs.

Works on the collaboration roster (``run_participants``, migration 0035) without editing
collab.py. Rules (docs/presence.md):

* Leaving DELETEs your ``run_participants`` row (history stays in ``run_collab_events`` and
  audit_log). A leader cannot leave or observe: 409 ``LEADER_MUST_HAND_OFF``.
* Cap: ``team_presence_settings.max_participants`` (default 20) counts every roster row
  (leader, participants, observers). A full run refuses NEW members with 409
  ``SESSION_FULL`` and audits ``participant.capacity_refused``. Existing members, role
  switches and idempotent retries are never refused. :func:`capacity_ok` is the one check
  the collab join path calls.
* Every state change returns, audits and (for roster changes) broadcasts an
  ``announcement`` sentence for screen readers. Mute stays private to the user.
* All actions are idempotent: repeating one changes nothing and writes no second row.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter
from harness_contracts.roles import READ_ONLY, Principal
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import presence
from harness_orchestrator.presence import RunGuard, audit, err, event, factory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["participation"])

SESSION_FULL = "SESSION_FULL"
LEADER_MUST_HAND_OFF = "LEADER_MUST_HAND_OFF"


def configure_participation(service: Any) -> None:
    """Deploy-time wiring: shares presence's service (one session factory)."""
    presence.configure_presence(service)


async def capacity_ok(db: Any, run_id: str, team_id: str, user_id: str) -> bool:
    """May ``user_id`` be on ``run_id``'s roster? True when already a member or when the
    roster is below the team's cap. On PostgreSQL a per-run advisory lock held to the
    caller's commit serialises concurrent joins, so the cap cannot be overshot. Call it
    inside the transaction that then INSERTs the participant row."""
    if db.bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"run-capacity:{run_id}"},
        )
    members = (
        await db.execute(
            text("SELECT user_id FROM run_participants WHERE run_id = :r"), {"r": run_id}
        )
    ).all()
    if any(m.user_id == user_id for m in members):
        return True
    cfg = await presence.team_settings(db, team_id)
    return len(members) < cfg["maxParticipants"]


async def _refuse_full(factory_: Any, p: Principal, run_id: str) -> None:
    """Audit the refusal in its own transaction (the refusing one is abandoned)."""
    try:
        async with factory_() as db:
            cfg = await presence.team_settings(db, p.teamId)
            await audit(
                db, p, "participant.capacity_refused", "agent_run", run_id,
                {"runId": run_id, "max": cfg["maxParticipants"]},
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - audit must never mask the refusal
        logger.exception("capacity_refused audit failed run=%s", run_id)
    raise err(409, SESSION_FULL, "this run is at its participant limit")


async def _me(db: Any, run_id: str, user_id: str) -> Any:
    return (
        await db.execute(
            text("SELECT role FROM run_participants WHERE run_id = :r AND user_id = :u"),
            {"r": run_id, "u": user_id},
        )
    ).first()


async def _changed(
    db: Any, p: Principal, run_id: str, kind: str, audit_action: str,
    before: str | None, after: str | None, said: str,
) -> None:
    detail = {"runId": run_id, "before": {"role": before}, "after": {"role": after},
              "announcement": said}
    await event(db, p, run_id, kind, detail)
    await audit(db, p, audit_action, "agent_run", run_id, detail)


def _result(**kw: Any) -> dict[str, Any]:
    return {"changed": True, **kw}


@router.post("/runs/{runId}/participation/observe")
async def observe(runId: str, actor: RunGuard) -> dict[str, Any]:
    """Watch without taking part: participant -> observer."""
    async with factory()() as db:
        me = await _me(db, runId, actor.userId)
        if me is None:
            raise err(409, "NOT_PARTICIPANT", "join the run first")
        if me.role == "leader":
            raise err(409, LEADER_MUST_HAND_OFF, "the leader must hand off before observing")
        if me.role == "observer":
            return {"changed": False, "role": "observer"}
        name = await presence.display_name(db, actor.teamId, actor.userId)
        said = f"{name} is now observing"
        res = await db.execute(
            text("UPDATE run_participants SET role = 'observer' WHERE run_id = :r"
                 " AND user_id = :u AND role = 'participant'"),
            {"r": runId, "u": actor.userId},
        )
        if res.rowcount != 1:  # lost a race with a leader change
            raise err(409, "ROLE_CHANGED", "your role changed; retry")
        await _changed(db, actor, runId, "participant.observing", "participant.observing",
                       "participant", "observer", said)
        await db.commit()
    return _result(role="observer", announcement=said)


@router.post("/runs/{runId}/participation/participate")
async def participate(runId: str, actor: RunGuard) -> dict[str, Any]:
    """Take part again: observer -> participant. Viewers and guests stay observers."""
    async with factory()() as db:
        me = await _me(db, runId, actor.userId)
        if me is None:
            raise err(409, "NOT_PARTICIPANT", "join the run first")
        if me.role != "observer":
            return {"changed": False, "role": me.role}
        if actor.role in READ_ONLY:
            raise err(403, "ROLE_DENIED", "your role can only observe")
        name = await presence.display_name(db, actor.teamId, actor.userId)
        said = f"{name} is now participating"
        res = await db.execute(
            text("UPDATE run_participants SET role = 'participant' WHERE run_id = :r"
                 " AND user_id = :u AND role = 'observer'"),
            {"r": runId, "u": actor.userId},
        )
        if res.rowcount != 1:
            raise err(409, "ROLE_CHANGED", "your role changed; retry")
        # No reserved audit name for observer -> participant, so it is a join with before/after.
        await _changed(db, actor, runId, "participant.participating", "participant.joined",
                       "observer", "participant", said)
        await db.commit()
    return _result(role="participant", announcement=said)


@router.post("/runs/{runId}/participation/leave")
async def leave(runId: str, actor: RunGuard) -> dict[str, Any]:
    async with factory()() as db:
        me = await _me(db, runId, actor.userId)
        if me is None:
            return {"changed": False, "left": True}
        if me.role == "leader":
            raise err(409, LEADER_MUST_HAND_OFF, "the leader must hand off before leaving")
        name = await presence.display_name(db, actor.teamId, actor.userId)
        said = f"{name} left the run"
        res = await db.execute(
            text("DELETE FROM run_participants WHERE run_id = :r AND user_id = :u"
                 " AND role <> 'leader'"),
            {"r": runId, "u": actor.userId},
        )
        if res.rowcount != 1:  # became leader between the read and the delete
            raise err(409, LEADER_MUST_HAND_OFF, "the leader must hand off before leaving")
        await db.execute(
            text("DELETE FROM presence_records WHERE scope_kind = 'run' AND scope_id = :r"
                 " AND user_id = :u"),
            {"r": runId, "u": actor.userId},
        )
        # A pending handoff to someone who is gone would block every other handoff.
        await db.execute(
            text("UPDATE run_handoffs SET state = 'cancelled', decided_at = :ts WHERE run_id = :r"
                 " AND to_user_id = :u AND state = 'pending'"),
            {"r": runId, "u": actor.userId, "ts": presence.now()},
        )
        await _changed(db, actor, runId, "participant.left", "participant.left",
                       me.role, None, said)
        await db.commit()
    return _result(left=True, announcement=said)


@router.post("/runs/{runId}/participation/rejoin")
async def rejoin(runId: str, actor: RunGuard) -> dict[str, Any]:
    """(Re)join the roster as observer (viewer/guest) or participant. Subject to the cap."""
    role = "observer" if actor.role in READ_ONLY else "participant"
    f = factory()
    async with f() as db:
        mine = await _me(db, runId, actor.userId)
        if mine is not None:
            return {"changed": False, "role": mine.role}
        if not await capacity_ok(db, runId, actor.teamId, actor.userId):
            await db.rollback()
            await _refuse_full(f, actor, runId)
        name = await presence.display_name(db, actor.teamId, actor.userId)
        said = f"{name} rejoined the run"
        try:
            await db.execute(
                text("INSERT INTO run_participants (run_id, team_id, user_id, role, joined_at)"
                     " VALUES (:r, :t, :u, :role, :ts)"),
                {"r": runId, "t": actor.teamId, "u": actor.userId, "role": role,
                 "ts": presence.now()},
            )
            await _changed(db, actor, runId, "participant.rejoined", "participant.rejoined",
                           None, role, said)
            await db.commit()
        except IntegrityError:  # concurrent retry already inserted us: idempotent
            await db.rollback()
            return {"changed": False, "role": role}
    return _result(role=role, announcement=said)


# -------------------------------------------------------------- notification prefs


class PrefsRequest(BaseModel):
    muted: bool | None = None
    notify: bool | None = None
    sharePresence: Literal["everyone", "team", "nobody"] | None = None


def _prefs_body(row: Any) -> dict[str, Any]:
    muted = bool(row.muted) if row else False
    notify = bool(row.notify) if row else True
    return {
        "muted": muted,
        "notify": notify,
        "sharePresence": row.share_presence if row else "team",
        "notificationsEnabled": notify and not muted,
    }


async def _get_prefs(db: Any, run_id: str, user_id: str) -> Any:
    return (
        await db.execute(
            text("SELECT muted, notify, share_presence FROM participant_prefs"
                 " WHERE run_id = :r AND user_id = :u"),
            {"r": run_id, "u": user_id},
        )
    ).first()


async def _set_prefs(
    db: Any, p: Principal, run_id: str, change: dict[str, Any], action: str
) -> dict[str, Any]:
    if await _me(db, run_id, p.userId) is None:
        raise err(409, "NOT_PARTICIPANT", "join the run first")
    before = _prefs_body(await _get_prefs(db, run_id, p.userId))
    after = {**before, **{k: v for k, v in change.items() if v is not None}}
    keys = ("muted", "notify", "sharePresence")
    if all(after[k] == before[k] for k in keys):
        return {"changed": False, **before}
    await db.execute(
        text(
            "INSERT INTO participant_prefs (run_id, user_id, muted, notify, share_presence,"
            " updated_at) VALUES (:r, :u, :m, :n, :s, :ts)"
            " ON CONFLICT (run_id, user_id) DO UPDATE SET muted = excluded.muted,"
            " notify = excluded.notify, share_presence = excluded.share_presence,"
            " updated_at = excluded.updated_at"
        ),
        {"r": run_id, "u": p.userId, "m": after["muted"], "n": after["notify"],
         "s": after["sharePresence"], "ts": presence.now()},
    )
    said = {
        "participant.muted": "Notifications for this run are muted",
        "participant.unmuted": "Notifications for this run are on",
    }.get(action, "Your notification and presence preferences were saved")
    await audit(
        db, p, action, "agent_run", run_id,
        {"runId": run_id, "scope": "user",
         "before": {k: before[k] for k in keys}, "after": {k: after[k] for k in keys}},
    )
    await db.commit()
    fresh = _prefs_body(await _get_prefs(db, run_id, p.userId))
    return {"changed": True, "announcement": said, **fresh}


@router.get("/runs/{runId}/participation/prefs")
async def get_prefs(runId: str, actor: RunGuard) -> dict[str, Any]:
    async with factory()() as db:
        return _prefs_body(await _get_prefs(db, runId, actor.userId))


@router.put("/runs/{runId}/participation/prefs")
async def put_prefs(runId: str, req: PrefsRequest, actor: RunGuard) -> dict[str, Any]:
    async with factory()() as db:
        action = "presence.settings_changed"
        if req.muted is not None and req.notify is None and req.sharePresence is None:
            action = "participant.muted" if req.muted else "participant.unmuted"
        return await _set_prefs(db, actor, runId, req.model_dump(), action)


@router.post("/runs/{runId}/participation/mute")
async def mute(runId: str, actor: RunGuard) -> dict[str, Any]:
    async with factory()() as db:
        return await _set_prefs(db, actor, runId, {"muted": True}, "participant.muted")


@router.post("/runs/{runId}/participation/unmute")
async def unmute(runId: str, actor: RunGuard) -> dict[str, Any]:
    async with factory()() as db:
        return await _set_prefs(db, actor, runId, {"muted": False}, "participant.unmuted")
