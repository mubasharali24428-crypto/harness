"""Simultaneous control and conflicts (part 42). Rules and limits: docs/control.md.

The run leader IS the control-lease holder (``run_participants.role = 'leader'``
plus a ``control_leases`` row from migration 0043 that adds expiry). This module
adds acquire/renew/release/transfer/revoke/decide on top of collab.py's single
leader, a FIFO request queue, shared steering (non-holders' guidance is queued
for the holder) and a versioned run intent with stale-edit rejection, conflict
records, and forced re-review when a resolution changes approved state.

Identity is always the authenticated Principal. Foreign runs answer 404 through
the shared run-authorization dependency. Raw SQL only (``text()``).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from harness_contracts.roles import Principal, Role
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api as _api
from harness_orchestrator import collab

router = APIRouter(prefix="/api/v1/orchestrator", tags=["control"])

_service: Any = None

REQUEST_TTL = timedelta(minutes=10)
INSTRUCTION_TTL = timedelta(hours=1)
MAX_QUEUED_INSTRUCTIONS = 50
MAX_PERMISSIONS = 50
DISCONNECT_ENV = "HARNESS_CONTROL_DISCONNECT_SECONDS"
FIELDS = ("goal", "scopePaths", "permissions")
_PERM = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_EMPTY = {"version": 0, "goal": "", "scopePaths": [], "permissions": []}


def configure_control(service: Any) -> None:
    """Deploy-time wiring (like configure_orchestrator): the service owning the DB factory."""
    global _service
    _service = service


def _now() -> datetime:
    return collab.db_now()  # one clock seam for leases, requests and intent (tests patch it)


def _dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    d = datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _iso(v: Any) -> str | None:
    d = _dt(v)
    return d.isoformat() if d else None


class ControlError(collab.CollabError):
    """CollabError that also carries structured data for the HTTP body."""

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None,
                 audited: bool = False) -> None:
        super().__init__(status, code, message, audited=audited)
        self.extra = extra or {}


def _eligible(p: Principal) -> None:
    if p.role not in (Role.ADMIN, Role.ENGINEER):
        raise collab.CollabError(403, "ROLE_DENIED", "only engineers/admins may hold control")


async def _record(db: Any, p: Principal, run_id: str, name: str, detail: dict[str, Any]) -> None:
    """One transition = a product event for collaborators plus an audit row, one transaction."""
    await collab._event(db, p, run_id, name, detail, None)
    await collab._audit(db, p, name, run_id, detail)


async def _has_table(db: Any, name: str) -> bool:
    if db.bind.dialect.name == "sqlite":
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :n"
    else:
        sql = "SELECT 1 WHERE to_regclass(:n) IS NOT NULL"
    return (await db.execute(text(sql), {"n": name})).first() is not None


async def _connected(db: Any, p_team: str, run_id: str, user_id: str) -> bool | None:
    """Presence (lane P table). None = unknown (presence not installed or switched off for
    the team), which is never treated as disconnected."""
    if not await _has_table(db, "presence_records"):
        return None
    if await _has_table(db, "team_presence_settings"):
        mode = (
            await db.execute(
                text("SELECT mode FROM team_presence_settings WHERE team_id = :t"), {"t": p_team}
            )
        ).first()
        if mode is not None and mode.mode == "off":
            return None
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM presence_records WHERE scope_kind = 'run' AND scope_id = :r"
                " AND user_id = :u AND expires_at > :now LIMIT 1"
            ),
            {"r": run_id, "u": user_id, "now": _now()},
        )
    ).first()
    return row is not None


def _grace() -> timedelta:
    return timedelta(seconds=max(0, collab._int_env(DISCONNECT_ENV, 60)))


async def _takeable(db: Any, team_id: str, run_id: str, lease: Any) -> bool:
    """A live lease is takeable when its holder has no unexpired presence for the grace period."""
    if lease is None:
        return False
    renewed = _dt(lease.renewed_at)
    if renewed is None or _now() - renewed < _grace():
        return False
    return await _connected(db, team_id, run_id, lease.holder_user_id) is False


async def _expire_requests(db: Any, run_id: str) -> None:
    await db.execute(
        text(
            "UPDATE control_requests SET state = 'expired'"
            " WHERE run_id = :r AND state = 'pending' AND expires_at <= :now"
        ),
        {"r": run_id, "now": _now()},
    )


async def _queue(db: Any, run_id: str) -> list[Any]:
    return (
        await db.execute(
            text(
                "SELECT id, user_id, created_at, expires_at FROM control_requests"
                " WHERE run_id = :r AND state = 'pending' AND expires_at > :now"
                " ORDER BY created_at, id"
            ),
            {"r": run_id, "now": _now()},
        )
    ).all()


async def _joined_engineer(db: Any, p: Principal, run_id: str, user_id: str) -> None:
    target = await collab._participant(db, run_id, user_id)
    member = (
        await db.execute(
            text("SELECT role FROM team_members WHERE team_id = :t AND user_id = :u"),
            {"t": p.teamId, "u": user_id},
        )
    ).first()
    if target is None or member is None or str(member.role).lower() not in ("admin", "engineer"):
        raise collab.CollabError(422, "BAD_RECIPIENT", "recipient must be a joined engineer/admin")


async def _swap_holder(
    db: Any, p: Principal, run_id: str, old: str | None, new: str, ttl: timedelta | None = None
) -> Any:
    """Atomically move the leader role (and a fresh lease) from ``old`` to ``new``."""
    if old is not None:
        dem = await db.execute(
            text(
                "UPDATE run_participants SET role = 'participant'"
                " WHERE run_id = :r AND user_id = :u AND role = 'leader'"
            ),
            {"r": run_id, "u": old},
        )
        if dem.rowcount != 1:
            raise collab.CollabError(409, "LEADER_CHANGED", "control changed; retry")
    pro = await db.execute(
        text(
            "UPDATE run_participants SET role = 'leader' WHERE run_id = :r AND user_id = :u"
            " AND NOT EXISTS (SELECT 1 FROM run_participants x"
            " WHERE x.run_id = :r AND x.role = 'leader')"
        ),
        {"r": run_id, "u": new},
    )
    if pro.rowcount != 1:
        raise collab.CollabError(409, "LEASE_HELD", "control is held by someone else; retry")
    await collab.cancel_handoffs(db, run_id)
    return await collab.set_lease(db, p.teamId, run_id, new, ttl)


async def _vacate(db: Any, run_id: str, holder: str) -> None:
    """The holder loses control. Compare-and-swap on the holder: if control moved since
    the caller checked (a racing transfer), nothing changes and the caller retries."""
    dem = await db.execute(
        text(
            "UPDATE run_participants SET role = 'participant'"
            " WHERE run_id = :r AND user_id = :u AND role = 'leader'"
        ),
        {"r": run_id, "u": holder},
    )
    if dem.rowcount != 1:
        await db.rollback()
        raise collab.CollabError(409, "LEADER_CHANGED", "control changed; retry")
    await collab.lease_sql(
        db, "DELETE FROM control_leases WHERE run_id = :r AND holder_user_id = :u",
        r=run_id, u=holder)
    await collab.cancel_handoffs(db, run_id)


async def _close_request(db: Any, run_id: str, user_id: str, state: str, by: str) -> bool:
    res = await db.execute(
        text(
            "UPDATE control_requests SET state = :s, decided_by = :b, decided_at = :ts"
            " WHERE run_id = :r AND user_id = :u AND state = 'pending'"
        ),
        {"s": state, "b": by, "ts": _now(), "r": run_id, "u": user_id},
    )
    return bool(res.rowcount)


# ---------------------------------------------------------------- control view


async def view(factory: Any, p: Principal, run_id: str) -> dict[str, Any]:
    async with factory() as db:
        await collab.expire_lease_if_due(db, run_id)  # show expiry truthfully, with evidence
        await _expire_requests(db, run_id)
        await db.commit()
        leader = await collab._leader_id(db, run_id)
        lease = await collab.get_lease(db, run_id)
        queue = await _queue(db, run_id)
        insts = (
            await db.execute(
                text(
                    "SELECT id, author_user_id, content, created_at, expires_at"
                    " FROM control_instructions WHERE run_id = :r AND state = 'queued'"
                    " AND expires_at > :now ORDER BY created_at, id"
                ),
                {"r": run_id, "now": _now()},
            )
        ).all()
        pend = (
            await db.execute(
                text(
                    "SELECT id, from_user_id, to_user_id FROM run_handoffs"
                    " WHERE run_id = :r AND state = 'pending'"
                ),
                {"r": run_id},
            )
        ).first()
        me = await collab._participant(db, run_id, p.userId)
        takeable = False
        holder: dict[str, Any] | None = None
        if leader is not None:
            conn = await _connected(db, p.teamId, run_id, leader)
            takeable = await _takeable(db, p.teamId, run_id, lease)
            exp = _dt(lease.expires_at) if lease else None
            holder = {
                "userId": leader,
                "untimed": lease is None,
                "acquiredAt": _iso(lease.acquired_at) if lease else None,
                "renewedAt": _iso(lease.renewed_at) if lease else None,
                "expiresAt": _iso(exp),
                "expiresInSeconds": max(0, int((exp - _now()).total_seconds())) if exp else None,
                "connected": conn,
                "takeable": takeable,
            }
    ids = [r.user_id for r in queue]
    eligible = p.role in (Role.ADMIN, Role.ENGINEER)
    is_holder = leader == p.userId
    head = ids[0] if ids else None
    free_ok = leader is None and eligible and head in (None, p.userId)
    base = f"/api/v1/orchestrator/runs/{run_id}/control"
    return {
        "runId": run_id,
        "model": {
            "controller": "one exclusive lease holder, who is the run leader",
            "steering": "non-holders' guidance is queued as suggestions for the holder",
            "leaseSeconds": int(collab.lease_ttl().total_seconds()),
            "disconnectGraceSeconds": int(_grace().total_seconds()),
            "requestTtlSeconds": int(REQUEST_TTL.total_seconds()),
            "instructionTtlSeconds": int(INSTRUCTION_TTL.total_seconds()),
        },
        "holder": holder,
        "queue": [
            {"requestId": r.id, "userId": r.user_id, "requestedAt": _iso(r.created_at),
             "position": i + 1}
            for i, r in enumerate(queue)
        ],
        "queuedInstructions": [
            {"id": r.id, "author": r.author_user_id, "content": r.content,
             "queuedAt": _iso(r.created_at), "expiresAt": _iso(r.expires_at)}
            for r in insts
        ],
        "pendingHandoff": (
            {"id": pend.id, "from": pend.from_user_id, "to": pend.to_user_id} if pend else None
        ),
        "viewer": {
            "userId": p.userId,
            "isHolder": is_holder,
            "canAcquire": bool(free_ok or (eligible and takeable and not is_holder)),
            "canRequest": bool(
                eligible and leader is not None and not is_holder and p.userId not in ids
            ),
            "canSuggest": bool(me is not None and me.role != "observer" and not is_holder),
            "queuePosition": ids.index(p.userId) + 1 if p.userId in ids else None,
        },
        "how": {
            "request": f"POST {base}/request",
            "acquire": f"POST {base}/acquire",
            "suggest": f"POST /api/v1/orchestrator/runs/{run_id}/redirect (kind guidance)",
        },
        "announcement": (
            f"{leader} holds control"
            + (f" until {_iso(lease.expires_at)}" if lease else "")
            if leader
            else "Nobody holds control"
        ),
    }


# --------------------------------------------------------------------- lease ops


async def request_control(factory: Any, p: Principal, run_id: str) -> dict[str, Any]:
    _eligible(p)
    await collab.join(factory, p, run_id)
    async with factory() as db:
        leader = await collab._leader_id(db, run_id)
        if leader == p.userId:
            raise collab.CollabError(409, "ALREADY_HOLDER", "you already hold control")
        if leader is None:
            raise collab.CollabError(409, "CONTROL_FREE", "nobody holds control; acquire it")
        await _expire_requests(db, run_id)
        rid, now = str(uuid.uuid4()), _now()
        try:
            await db.execute(
                text(
                    "INSERT INTO control_requests (id, run_id, team_id, user_id, state,"
                    " created_at, expires_at) VALUES (:i, :r, :t, :u, 'pending', :c, :x)"
                ),
                {"i": rid, "r": run_id, "t": p.teamId, "u": p.userId, "c": now,
                 "x": now + REQUEST_TTL},
            )
        except IntegrityError:
            await db.rollback()
            ids = [r.user_id for r in await _queue(db, run_id)]
            mine = (
                await db.execute(
                    text(
                        "SELECT id FROM control_requests WHERE run_id = :r AND user_id = :u"
                        " AND state = 'pending'"
                    ),
                    {"r": run_id, "u": p.userId},
                )
            ).first()
            return {"requestId": mine.id if mine else None, "duplicate": True,
                    "position": ids.index(p.userId) + 1 if p.userId in ids else None}
        await _record(db, p, run_id, "control.requested", {
            "requestId": rid, "holder": leader,
            "before": None, "after": {"state": "pending"}})
        await db.commit()
        ids = [r.user_id for r in await _queue(db, run_id)]
    return {"requestId": rid, "duplicate": False, "holder": leader,
            "position": ids.index(p.userId) + 1}


async def acquire(
    factory: Any, p: Principal, run_id: str, ttl_seconds: int | None
) -> dict[str, Any]:
    """Take a free lease (FIFO queue head first) or a disconnected holder's lease;
    the current holder acquiring again just renews (idempotent retry)."""
    _eligible(p)
    await collab.join(factory, p, run_id)
    ttl = collab.lease_ttl(ttl_seconds)
    try:
        async with factory() as db:
            leader = await collab._leader_id(db, run_id)
            if leader == p.userId:
                return await _renew_tx(db, p, run_id, ttl)
            await _expire_requests(db, run_id)
            lease = await collab.get_lease(db, run_id)
            takeover = False
            if leader is not None:
                if not await _takeable(db, p.teamId, run_id, lease):
                    raise ControlError(
                        409, "LEASE_HELD", "control is held; request it instead",
                        {"holder": leader, "expiresAt": _iso(lease.expires_at) if lease else None},
                    )
                takeover = True
            else:
                queue = await _queue(db, run_id)
                if queue and queue[0].user_id != p.userId:
                    raise ControlError(409, "QUEUE_ORDER", "someone is ahead of you in the queue",
                                       {"nextInQueue": queue[0].user_id})
            expires = await _swap_holder(db, p, run_id, leader, p.userId, ttl)
            await _close_request(db, run_id, p.userId, "granted", p.userId)
            detail = {"holder": p.userId, "expiresAt": _iso(expires),
                      "before": {"holder": leader}, "after": {"holder": p.userId}}
            if takeover:
                detail["takeover"] = "holder_disconnected"
            await _record(db, p, run_id, "control.acquired", detail)
            await db.commit()
    except IntegrityError:
        raise collab.CollabError(409, "LEASE_HELD", "control is held; request it instead") from None
    return {"holder": p.userId, "expiresAt": _iso(expires), "takeover": takeover}


async def _renew_tx(db: Any, p: Principal, run_id: str, ttl: timedelta) -> dict[str, Any]:
    now = _now()
    exp = now + ttl
    res = await collab.lease_sql(
        db,
        "UPDATE control_leases SET expires_at = :x, renewed_at = :n, version = version + 1"
        " WHERE run_id = :r AND holder_user_id = :u AND expires_at > :n",
        x=exp, n=now, r=run_id, u=p.userId,
    )
    if res is not None and res.rowcount != 1:
        # a pre-0043 leader has no row yet: acquiring/renewing creates its lease
        if await collab.get_lease(db, run_id) is not None:
            raise collab.CollabError(409, "LEASE_EXPIRED", "your lease lapsed; acquire again")
        await collab.set_lease(db, p.teamId, run_id, p.userId, ttl)
    await _record(db, p, run_id, "control.renewed", {
        "holder": p.userId, "expiresAt": _iso(exp), "after": {"expiresAt": _iso(exp)}})
    await db.commit()
    return {"holder": p.userId, "expiresAt": _iso(exp), "takeover": False}


async def renew(factory: Any, p: Principal, run_id: str, ttl_seconds: int | None) -> dict[str, Any]:
    async with factory() as db:
        await collab._require_leader(db, p, run_id, "control.renew")
        return await _renew_tx(db, p, run_id, collab.lease_ttl(ttl_seconds))


async def release(factory: Any, p: Principal, run_id: str) -> dict[str, Any]:
    async with factory() as db:
        await collab._require_leader(db, p, run_id, "control.release")
        await _vacate(db, run_id, p.userId)
        await _record(db, p, run_id, "control.released", {
            "holder": p.userId, "before": {"holder": p.userId}, "after": {"holder": None}})
        await db.commit()
        queue = await _queue(db, run_id)
    return {"holder": None, "nextInQueue": queue[0].user_id if queue else None}


async def _transfer_tx(
    db: Any, p: Principal, run_id: str, to_user: str, via: str
) -> dict[str, Any]:
    await _joined_engineer(db, p, run_id, to_user)
    expires = await _swap_holder(db, p, run_id, p.userId, to_user)
    await _close_request(db, run_id, to_user, "granted", p.userId)
    await _record(db, p, run_id, "control.transferred", {
        "from": p.userId, "to": to_user, "via": via, "expiresAt": _iso(expires),
        "before": {"holder": p.userId}, "after": {"holder": to_user}})
    await db.commit()
    return {"holder": to_user, "expiresAt": _iso(expires)}


async def transfer(factory: Any, p: Principal, run_id: str, to_user: str) -> dict[str, Any]:
    async with factory() as db:
        await collab._require_leader(db, p, run_id, "control.transfer")
        if to_user == p.userId:
            raise collab.CollabError(422, "SELF_TRANSFER", "you already hold control")
        return await _transfer_tx(db, p, run_id, to_user, "transfer")


async def revoke(factory: Any, p: Principal, run_id: str, reason: str) -> dict[str, Any]:
    if p.role != Role.ADMIN:
        raise collab.CollabError(403, "ROLE_DENIED", "only a team admin may revoke control")
    reason = reason.strip()
    if not reason or len(reason) > collab.MAX_REASON:
        raise collab.CollabError(422, "REASON_REQUIRED", "a reason (1..500 chars) is required")
    async with factory() as db:
        holder = await collab._leader_id(db, run_id)
        if holder is None:
            raise collab.CollabError(409, "NO_HOLDER", "nobody holds control")
        await _vacate(db, run_id, holder)
        await _record(db, p, run_id, "control.revoked", {
            "from": holder, "reason": reason,
            "before": {"holder": holder}, "after": {"holder": None}})
        await db.commit()
    return {"holder": None, "revokedFrom": holder}


# ----------------------------------------------------------- shared steering queue


async def queue_if_held(
    db: Any, p: Principal, run_id: str, content: str, request_id: str | None
) -> dict[str, Any] | None:
    """Called by collab.redirect for guidance. When a lease exists and someone else holds
    it, the guidance becomes a queued suggestion; otherwise None (record it directly)."""
    leader = await collab._leader_id(db, run_id)
    if leader is None or leader == p.userId or await collab.get_lease(db, run_id) is None:
        return None
    if request_id:
        dup = (
            await db.execute(
                text("SELECT id FROM control_instructions WHERE run_id = :r AND request_id = :q"),
                {"r": run_id, "q": request_id},
            )
        ).first()
        if dup:
            return _queued(dup.id, True)
    open_n = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM control_instructions WHERE run_id = :r"
                " AND state = 'queued' AND expires_at > :now"
            ),
            {"r": run_id, "now": _now()},
        )
    ).scalar()
    if open_n >= MAX_QUEUED_INSTRUCTIONS:
        raise collab.CollabError(409, "QUEUE_FULL", "the holder has too many suggestions waiting")
    iid, now = str(uuid.uuid4()), _now()
    try:
        await db.execute(
            text(
                "INSERT INTO control_instructions (id, run_id, team_id, author_user_id, content,"
                " request_id, state, created_at, expires_at) VALUES"
                " (:i, :r, :t, :u, :c, :q, 'queued', :n, :x)"
            ),
            {"i": iid, "r": run_id, "t": p.teamId, "u": p.userId, "c": content,
             "q": request_id, "n": now, "x": now + INSTRUCTION_TTL},
        )
    except IntegrityError:  # simultaneous retry with the same requestId
        await db.rollback()
        dup = (
            await db.execute(
                text("SELECT id FROM control_instructions WHERE run_id = :r AND request_id = :q"),
                {"r": run_id, "q": request_id},
            )
        ).first()
        if dup is None:
            raise
        return _queued(dup.id, True)
    await _record(db, p, run_id, "control.instruction_queued", {
        "instructionId": iid, "holder": leader, "after": {"state": "queued"}})
    return _queued(iid, False)


def _queued(iid: str, duplicate: bool) -> dict[str, Any]:
    return {"seq": None, "duplicate": duplicate, "invalidatedApprovals": 0, "queued": True,
            "instructionId": iid}


async def decide(
    factory: Any, p: Principal, run_id: str, request_id: str | None,
    instruction_id: str | None, decision: str, reason: str | None,
) -> dict[str, Any]:
    reason = (reason or "").strip()[: collab.MAX_REASON] or None
    if (request_id is None) == (instruction_id is None):
        raise collab.CollabError(
            422, "BAD_DECISION", "give exactly one of requestId, instructionId")
    ok = {"requestId": ("grant", "decline", "cancel"), "instructionId": ("accept", "reject")}
    if decision not in ok["requestId" if request_id else "instructionId"]:
        raise collab.CollabError(422, "BAD_DECISION", "decision does not fit that target")
    async with factory() as db:
        if request_id:
            return await _decide_request(db, p, run_id, request_id, decision)
        return await _decide_instruction(db, p, run_id, instruction_id or "", decision, reason)


async def _decide_request(
    db: Any, p: Principal, run_id: str, request_id: str, decision: str
) -> dict[str, Any]:
    await _expire_requests(db, run_id)
    row = (
        await db.execute(
            text("SELECT user_id, state FROM control_requests WHERE id = :i AND run_id = :r"),
            {"i": request_id, "r": run_id},
        )
    ).first()
    if row is None:
        raise collab.CollabError(404, "NOT_FOUND", "no such request")
    if decision == "cancel":
        if row.user_id != p.userId:
            raise collab.CollabError(403, "NOT_REQUESTER", "only the requester may cancel")
    else:
        await collab._require_leader(db, p, run_id, f"control.{decision}")
    if row.state != "pending":
        raise collab.CollabError(409, "NOT_PENDING", f"request is already {row.state}")
    if decision == "grant":
        out = await _transfer_tx(db, p, run_id, row.user_id, "request")
        return {"requestId": request_id, "state": "granted", **out}
    state = "cancelled" if decision == "cancel" else "declined"
    if not await _close_request(db, run_id, row.user_id, state, p.userId):
        raise collab.CollabError(409, "NOT_PENDING", "request is no longer pending")
    await _record(db, p, run_id, "control.requested", {
        "requestId": request_id, "requester": row.user_id,
        "before": {"state": "pending"}, "after": {"state": state}})
    await db.commit()
    return {"requestId": request_id, "state": state}


async def _decide_instruction(
    db: Any, p: Principal, run_id: str, iid: str, decision: str, reason: str | None
) -> dict[str, Any]:
    await collab._require_leader(db, p, run_id, f"control.instruction.{decision}")
    row = (
        await db.execute(
            text(
                "SELECT author_user_id, content, state, expires_at FROM control_instructions"
                " WHERE id = :i AND run_id = :r"
            ),
            {"i": iid, "r": run_id},
        )
    ).first()
    if row is None:
        raise collab.CollabError(404, "NOT_FOUND", "no such suggestion")
    new = "accepted" if decision == "accept" else "rejected"
    res = await db.execute(
        text(
            "UPDATE control_instructions SET state = :s, decided_by = :u, decided_at = :n,"
            " reason = :w WHERE id = :i AND state = 'queued' AND expires_at > :n"
        ),
        {"s": new, "u": p.userId, "n": _now(), "w": reason, "i": iid},
    )
    if res.rowcount != 1:
        raise collab.CollabError(409, "NOT_PENDING", "suggestion already decided or expired")
    if decision == "reject":
        await _record(db, p, run_id, "control.instruction_rejected", {
            "instructionId": iid, "author": row.author_user_id, "reason": reason,
            "before": {"state": "queued"}, "after": {"state": "rejected"}})
        await db.commit()
        return {"instructionId": iid, "state": "rejected"}
    # accepted: it becomes ordinary guidance, delivered to the agent in seq order
    seq = await collab._event(db, p, run_id, "redirect.guidance", {
        "content": row.content, "reason": None, "suggestedBy": row.author_user_id,
        "instructionId": iid}, None)
    await db.execute(text("UPDATE control_instructions SET event_seq = :s WHERE id = :i"),
                     {"s": seq, "i": iid})
    await collab._audit(db, p, "redirect.recorded", run_id, {
        "kind": "guidance", "seq": seq, "instructionId": iid, "suggestedBy": row.author_user_id})
    await db.commit()
    return {"instructionId": iid, "state": "accepted", "seq": seq}


# ------------------------------------------------------------------------ intent


def _norm_patch(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "goal" in patch:
        goal = (patch["goal"] or "").strip()
        if len(goal) > collab.MAX_TEXT:
            raise collab.CollabError(422, "BAD_GOAL", f"goal max {collab.MAX_TEXT} chars")
        out["goal"] = goal
    if "scopePaths" in patch:
        paths = patch["scopePaths"] or []
        if paths:
            collab.validate_paths(paths)
        out["scopePaths"] = sorted(set(paths))
    if "permissions" in patch:
        perms = patch["permissions"] or []
        if len(perms) > MAX_PERMISSIONS or any(not _PERM.match(x) for x in perms):
            raise collab.CollabError(
                422, "BAD_PERMISSIONS", "permissions: short lowercase tokens like fs.delete")
        out["permissions"] = sorted(set(perms))
    if not out:
        raise collab.CollabError(
            422, "EMPTY_EDIT", "give at least one of goal, scopePaths, permissions")
    return out


def _snap(r: Any) -> dict[str, Any]:
    return {
        "version": r.version,
        "goal": r.goal,
        "scopePaths": json.loads(r.scope_paths),
        "permissions": json.loads(r.permissions),
    }


_VCOLS = "version, goal, scope_paths, permissions, updated_by, updated_at, changed_fields"


async def _version(db: Any, run_id: str, version: int) -> dict[str, Any]:
    if version == 0:
        return dict(_EMPTY)
    row = (
        await db.execute(
            text(f"SELECT {_VCOLS} FROM run_intent_versions WHERE run_id = :r AND version = :v"),
            {"r": run_id, "v": version},
        )
    ).first()
    if row is None:
        raise collab.CollabError(409, "BAD_VERSION", "unknown intent version")
    return _snap(row)


async def _current(db: Any, run_id: str) -> dict[str, Any]:
    row = (
        await db.execute(
            text(f"SELECT {_VCOLS} FROM run_intent_versions WHERE run_id = :r"
                 " ORDER BY version DESC LIMIT 1"),
            {"r": run_id},
        )
    ).first()
    if row is None:
        return {**_EMPTY, "updatedBy": None, "updatedAt": None}
    return {**_snap(row), "updatedBy": row.updated_by, "updatedAt": _iso(row.updated_at)}


def _public(snap: dict[str, Any]) -> dict[str, Any]:
    return {k: snap.get(k) for k in ("version", "goal", "scopePaths", "permissions",
                                     "updatedBy", "updatedAt")}


async def get_intent(factory: Any, run_id: str) -> dict[str, Any]:
    async with factory() as db:
        cur = await _current(db, run_id)
        hist = (
            await db.execute(
                text("SELECT version, updated_by, updated_at, changed_fields"
                     " FROM run_intent_versions WHERE run_id = :r ORDER BY version DESC LIMIT 20"),
                {"r": run_id},
            )
        ).all()
        n = (
            await db.execute(
                text("SELECT COUNT(*) FROM control_conflicts WHERE run_id = :r AND state = 'open'"),
                {"r": run_id},
            )
        ).scalar()
    return {
        "intent": _public(cur),
        "openConflicts": n,
        "history": [
            {"version": h.version, "by": h.updated_by, "at": _iso(h.updated_at),
             "changed": json.loads(h.changed_fields)}
            for h in hist
        ],
    }


async def _open_approvals(db: Any, run_id: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text("SELECT id, action, state, paths, digest FROM high_impact_approvals"
                 " WHERE run_id = :r AND state IN ('pending', 'approved') ORDER BY created_at"),
            {"r": run_id},
        )
    ).all()
    return [
        {"id": r.id, "action": r.action, "state": r.state, "digest": r.digest,
         "paths": r.paths if isinstance(r.paths, list) else json.loads(r.paths)}
        for r in rows
    ]


async def _apply(
    db: Any, p: Principal, run_id: str, cur: dict[str, Any], patch: dict[str, Any],
    request_id: str | None, audit: str, strict: bool, extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write intent version cur+1 (the primary key is the stale-edit compare-and-swap)
    and void open approvals when scope/permissions changed (``strict``: when anything
    changed, used for conflict resolutions). Caller commits."""
    changed = [f for f in FIELDS if f in patch and patch[f] != cur[f]]
    new = {**{f: cur[f] for f in FIELDS}, **{f: patch[f] for f in changed}}
    version = cur["version"] + 1
    try:
        await db.execute(
            text(
                "INSERT INTO run_intent_versions (run_id, version, team_id, goal, scope_paths,"
                " permissions, changed_fields, updated_by, updated_at, request_id)"
                " VALUES (:r, :v, :t, :g, :s, :p, :c, :u, :n, :q)"
            ),
            {"r": run_id, "v": version, "t": p.teamId, "g": new["goal"],
             "s": json.dumps(new["scopePaths"]), "p": json.dumps(new["permissions"]),
             "c": json.dumps(changed), "u": p.userId, "n": _now(), "q": request_id},
        )
    except IntegrityError:
        await db.rollback()  # the loser of a version race: nothing of this edit survives
        raise ControlError(409, "STALE_INTENT_VERSION", "intent changed while you were editing",
                           {"currentVersion": (await _current(db, run_id))["version"]}) from None
    voids = strict or "scopePaths" in changed or "permissions" in changed
    invalidated = 0
    if voids:
        invalidated = await collab._invalidate(
            db, p, run_id, f"intent v{version}: {', '.join(changed)} changed")
    detail = {
        "version": version, "changed": changed, "invalidatedApprovals": invalidated,
        "before": {f: (cur[f][:500] if f == "goal" else cur[f]) for f in changed},
        "after": {f: (new[f][:500] if f == "goal" else new[f]) for f in changed},
        **(extra or {}),
    }
    await collab._event(db, p, run_id, audit, detail, None)
    await collab._audit(db, p, audit, run_id, detail)
    holder = await collab._leader_id(db, run_id)
    if holder == p.userId and ("goal" in changed or "scopePaths" in changed):
        parts = []
        if "goal" in changed:
            parts.append(f"goal: {new['goal']}")
        if "scopePaths" in changed:
            parts.append("scope paths: " + (", ".join(new["scopePaths"]) or "(none recorded)"))
        await collab._event(db, p, run_id, "redirect.scope_change", {
            "content": f"Run intent v{version} - " + "; ".join(parts),
            "reason": f"intent v{version}"}, None)
    return {"version": version, "intent": {**new, "version": version}, "changed": changed,
            "invalidatedApprovals": invalidated, "reviewRequired": invalidated > 0,
            "duplicate": False}


async def put_intent(
    factory: Any, p: Principal, run_id: str, expected: int, patch: dict[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    patch = _norm_patch(patch)
    async with factory() as db:
        me = await collab._participant(db, run_id, p.userId)
        if me is None or me.role == "observer":
            raise collab.CollabError(403, "NOT_PARTICIPANT", "join the run as a participant first")
        if request_id:
            dup = (
                await db.execute(
                    text("SELECT version FROM run_intent_versions"
                         " WHERE run_id = :r AND request_id = :q"),
                    {"r": run_id, "q": request_id},
                )
            ).first()
            if dup:
                snap = await _version(db, run_id, dup.version)
                return {"version": dup.version, "intent": snap, "changed": [],
                        "invalidatedApprovals": 0, "reviewRequired": False, "duplicate": True}
        holder = await collab._leader_id(db, run_id)
        cur = await _current(db, run_id)
        if expected > cur["version"]:
            raise collab.CollabError(422, "BAD_VERSION", "expectedVersion is ahead of the intent")
        changed = {f: v for f, v in patch.items() if v != cur[f]}
        if not changed:
            return {"version": cur["version"], "intent": _public(cur), "changed": [],
                    "invalidatedApprovals": 0, "reviewRequired": False, "duplicate": False,
                    "unchanged": True}
        if expected < cur["version"]:
            base = await _version(db, run_id, expected)
            clash = [f for f in changed if cur[f] != base[f]]  # moved since the editor looked
            extra = {"currentVersion": cur["version"], "current": _public(cur),
                     "conflictFields": clash}
            if not clash:
                raise ControlError(409, "STALE_INTENT_VERSION",
                                   "intent moved on; re-read it and resubmit", extra)
            cid = await _open_conflict(db, p, run_id, "conflict", expected, changed, clash)
            await _record(db, p, run_id, "control.conflict_detected", {
                "conflictId": cid, "baseVersion": expected, "currentVersion": cur["version"],
                "fields": clash, "before": {"version": cur["version"]}, "after": {"state": "open"}})
            await db.commit()
            raise ControlError(409, "INTENT_CONFLICT", "your edit conflicts with a newer version",
                               {**extra, "conflictId": cid}, audited=True)
        if holder is not None and holder != p.userId:
            cid = await _open_conflict(db, p, run_id, "proposal", cur["version"], changed, [])
            await _record(db, p, run_id, "control.instruction_queued", {
                "conflictId": cid, "kind": "intent", "fields": list(changed), "holder": holder,
                "after": {"state": "open"}})
            await db.commit()
            return {"queued": True, "proposalId": cid, "holder": holder,
                    "version": cur["version"]}
        out = await _apply(db, p, run_id, cur, changed, request_id, "intent.updated", False)
        await db.commit()
    return out


async def _open_conflict(
    db: Any, p: Principal, run_id: str, kind: str, base: int, proposed: dict[str, Any],
    fields: list[str],
) -> str:
    cid = str(uuid.uuid4())
    await db.execute(
        text(
            "INSERT INTO control_conflicts (id, run_id, team_id, kind, author_user_id,"
            " base_version, proposed, conflict_fields, state, created_at)"
            " VALUES (:i, :r, :t, :k, :u, :b, :p, :f, 'open', :n)"
        ),
        {"i": cid, "r": run_id, "t": p.teamId, "k": kind, "u": p.userId, "b": base,
         "p": json.dumps(proposed), "f": json.dumps(fields), "n": _now()},
    )
    return cid


async def list_conflicts(factory: Any, run_id: str, state: str = "open") -> dict[str, Any]:
    if state not in ("open", "resolved", "all"):
        raise collab.CollabError(422, "BAD_STATE", "state must be open, resolved or all")
    async with factory() as db:
        cur = await _current(db, run_id)
        rows = (
            await db.execute(
                text(
                    "SELECT id, kind, author_user_id, base_version, proposed, conflict_fields,"
                    " state, resolution, resolved_by, resolved_version, created_at"
                    " FROM control_conflicts WHERE run_id = :r"
                    + ("" if state == "all" else " AND state = :s")
                    + " ORDER BY created_at, id"
                ),
                {"r": run_id, "s": state},
            )
        ).all()
        out = []
        for r in rows:
            base = await _version(db, run_id, r.base_version)
            proposed = json.loads(r.proposed)
            clash = json.loads(r.conflict_fields)
            out.append({
                "id": r.id, "kind": r.kind, "author": r.author_user_id, "state": r.state,
                "baseVersion": r.base_version, "stale": r.base_version < cur["version"],
                "createdAt": _iso(r.created_at), "resolution": r.resolution,
                "resolvedBy": r.resolved_by, "resolvedVersion": r.resolved_version,
                "proposed": proposed,
                "fields": [
                    {"field": f, "base": base[f], "current": cur[f], "proposed": v,
                     "conflicting": f in clash}
                    for f, v in proposed.items()
                ],
            })
        approvals = await _open_approvals(db, run_id)
    return {
        "currentApproved": _public(cur),
        "conflicts": out,
        "openApprovals": approvals,
        "resolutionOptions": ["keep_current", "take_proposed", "merge"],
        "note": "resolving a conflict that changes the intent voids every pending/approved "
                "approval above, so they must be reviewed again",
    }


async def resolve_conflict(
    factory: Any, p: Principal, run_id: str, conflict_id: str, resolution: str,
    expected: int, merged: dict[str, Any] | None,
) -> dict[str, Any]:
    async with factory() as db:
        holder = await collab._leader_id(db, run_id)
        if p.userId != holder and p.role != Role.ADMIN:
            raise await collab.deny_not_leader(db, p, run_id, "intent.resolve")
        row = (
            await db.execute(
                text("SELECT kind, author_user_id, base_version, proposed, state"
                     " FROM control_conflicts WHERE id = :i AND run_id = :r"),
                {"i": conflict_id, "r": run_id},
            )
        ).first()
        if row is None:
            raise collab.CollabError(404, "NOT_FOUND", "no such conflict")
        if row.state != "open":
            raise collab.CollabError(409, "ALREADY_RESOLVED", "conflict already resolved")
        cur = await _current(db, run_id)
        if expected != cur["version"]:
            raise ControlError(409, "STALE_INTENT_VERSION",
                               "intent moved on; review the current state first",
                               {"currentVersion": cur["version"], "current": _public(cur)})
        patch: dict[str, Any] = {}
        if resolution == "take_proposed":
            patch = json.loads(row.proposed)
        elif resolution == "merge":
            patch = _norm_patch(merged or {})
        elif resolution != "keep_current":
            raise collab.CollabError(422, "BAD_RESOLUTION", "unknown resolution")
        claim = await db.execute(
            text("UPDATE control_conflicts SET state = 'resolved', resolution = :s,"
                 " resolved_by = :u, resolved_at = :n, resolved_version = :v"
                 " WHERE id = :i AND state = 'open'"),
            {"s": resolution, "u": p.userId, "n": _now(), "v": cur["version"], "i": conflict_id},
        )
        if claim.rowcount != 1:
            raise collab.CollabError(409, "ALREADY_RESOLVED", "conflict already resolved")
        is_conflict = row.kind == "conflict"
        extra = {"conflictId": conflict_id, "resolution": resolution,
                 "author": row.author_user_id}
        changed = [f for f in FIELDS if f in patch and patch[f] != cur[f]]
        if changed:
            out = await _apply(
                db, p, run_id, cur, patch, None,
                "control.conflict_resolved" if is_conflict else "intent.updated",
                strict=is_conflict, extra=extra)
        else:
            name = "control.conflict_resolved" if is_conflict else "control.instruction_rejected"
            await _record(db, p, run_id, name, {
                **extra, "version": cur["version"], "invalidatedApprovals": 0,
                "before": {"state": "open"}, "after": {"state": "resolved"}})
            out = {"version": cur["version"], "intent": _public(cur), "changed": [],
                   "invalidatedApprovals": 0, "reviewRequired": False, "duplicate": False}
        await db.execute(
            text("UPDATE control_conflicts SET resolved_version = :v WHERE id = :i"),
            {"v": out["version"], "i": conflict_id})
        await db.commit()
    return {"conflictId": conflict_id, "state": "resolved", "resolution": resolution, **out}


# ------------------------------------------------------------------------ routes

_Reader = Annotated[Principal, Depends(_api._authz_dep("read", audit=True))]
_Writer = Annotated[Principal, Depends(_api._authz_dep("write", audit=True))]
RUN = "/runs/{runId}"


async def _call(call: Any) -> Any:
    svc = _service if _service is not None else _api._service
    factory = getattr(svc, "session_factory", None)
    if factory is None:
        raise HTTPException(503, detail={"error": {"code": "CONTROL_UNAVAILABLE",
                                                   "message": "no database wired"}})
    try:
        return await call(factory)
    except collab.CollabError as e:
        ctx = _api._denial_ctx.get()
        if not e.audited and ctx is not None:
            await collab.audit_denial(factory, ctx[0], ctx[1], ctx[2], e.code, e.status)
        body: dict[str, Any] = {"error": {"code": e.code, "message": str(e)}}
        body.update(getattr(e, "extra", {}))
        raise HTTPException(e.status, detail=body) from e


class LeaseBody(BaseModel):
    ttlSeconds: int | None = Field(default=None, ge=30, le=3600)


class TransferBody(BaseModel):
    toUserId: str = Field(min_length=1, max_length=64)


class RevokeBody(BaseModel):
    reason: str = Field(min_length=1, max_length=collab.MAX_REASON)


class DecideBody(BaseModel):
    requestId: str | None = Field(default=None, max_length=64)
    instructionId: str | None = Field(default=None, max_length=64)
    decision: Literal["grant", "decline", "cancel", "accept", "reject"]
    reason: str | None = Field(default=None, max_length=collab.MAX_REASON)


class IntentBody(BaseModel):
    expectedVersion: int = Field(ge=0)
    goal: str | None = Field(default=None, max_length=collab.MAX_TEXT)
    scopePaths: list[str] | None = Field(default=None, max_length=collab.MAX_PATHS)
    permissions: list[str] | None = Field(default=None, max_length=MAX_PERMISSIONS)
    requestId: str | None = Field(default=None, max_length=64)


class MergedIntent(BaseModel):
    goal: str | None = Field(default=None, max_length=collab.MAX_TEXT)
    scopePaths: list[str] | None = Field(default=None, max_length=collab.MAX_PATHS)
    permissions: list[str] | None = Field(default=None, max_length=MAX_PERMISSIONS)


class ResolveBody(BaseModel):
    resolution: Literal["keep_current", "take_proposed", "merge"]
    expectedVersion: int = Field(ge=0)
    merged: MergedIntent | None = None


async def _mutating(actor: Principal, run_id: str) -> None:
    _api._pilot_gate(actor)
    await _api._persist_run(run_id)


@router.get(RUN + "/control")
async def control_view(runId: str, actor: _Reader) -> dict:
    _api._pilot_gate(actor)
    return await _call(lambda f: view(f, actor, runId))


@router.post(RUN + "/control/request")
async def control_request(runId: str, actor: _Writer) -> dict:
    await _mutating(actor, runId)
    return await _call(lambda f: request_control(f, actor, runId))


@router.post(RUN + "/control/acquire")
async def control_acquire(runId: str, actor: _Writer, body: LeaseBody | None = None) -> dict:
    await _mutating(actor, runId)
    ttl = body.ttlSeconds if body else None
    return await _call(lambda f: acquire(f, actor, runId, ttl))


@router.post(RUN + "/control/renew")
async def control_renew(runId: str, actor: _Writer, body: LeaseBody | None = None) -> dict:
    await _mutating(actor, runId)
    ttl = body.ttlSeconds if body else None
    return await _call(lambda f: renew(f, actor, runId, ttl))


@router.post(RUN + "/control/release")
async def control_release(runId: str, actor: _Writer) -> dict:
    await _mutating(actor, runId)
    return await _call(lambda f: release(f, actor, runId))


@router.post(RUN + "/control/transfer")
async def control_transfer(runId: str, req: TransferBody, actor: _Writer) -> dict:
    await _mutating(actor, runId)
    return await _call(lambda f: transfer(f, actor, runId, req.toUserId))


@router.post(RUN + "/control/revoke")
async def control_revoke(runId: str, req: RevokeBody, actor: _Writer) -> dict:
    await _mutating(actor, runId)
    return await _call(lambda f: revoke(f, actor, runId, req.reason))


@router.post(RUN + "/control/decide")
async def control_decide(runId: str, req: DecideBody, actor: _Writer) -> dict:
    await _mutating(actor, runId)
    result = await _call(lambda f: decide(
        f, actor, runId, req.requestId, req.instructionId, req.decision, req.reason))
    if isinstance(result, dict) and result.get("state") == "accepted":  # now ordinary guidance
        svc = _service if _service is not None else _api._service
        result["takesEffect"] = await collab.takes_effect_now(svc, runId, False)
    return result


@router.get(RUN + "/intent")
async def intent_get(runId: str, actor: _Reader) -> dict:
    _api._pilot_gate(actor)
    return await _call(lambda f: get_intent(f, runId))


@router.put(RUN + "/intent")
async def intent_put(runId: str, req: IntentBody, actor: _Writer, response: Response) -> dict:
    await _mutating(actor, runId)
    patch = {k: getattr(req, k) for k in ("goal", "scopePaths", "permissions")
             if k in req.model_fields_set}
    out = await _call(lambda f: put_intent(f, actor, runId, req.expectedVersion, patch,
                                           req.requestId))
    if out.get("queued"):
        response.status_code = 202
    return out


@router.get(RUN + "/intent/conflicts")
async def intent_conflicts(runId: str, actor: _Reader, state: str = "open") -> dict:
    _api._pilot_gate(actor)
    return await _call(lambda f: list_conflicts(f, runId, state))


@router.post(RUN + "/intent/conflicts/{conflictId}/resolve")
async def intent_resolve(runId: str, conflictId: str, req: ResolveBody, actor: _Writer) -> dict:
    await _mutating(actor, runId)
    merged = ({k: getattr(req.merged, k) for k in req.merged.model_fields_set}
              if req.merged else None)
    return await _call(lambda f: resolve_conflict(
        f, actor, runId, conflictId, req.resolution, req.expectedVersion, merged))
