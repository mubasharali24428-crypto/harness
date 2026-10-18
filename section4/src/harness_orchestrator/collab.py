"""Shared-run collaboration backend (multiplayer pilot S2): participants and a
single leader, redirects, explicit handoffs, and high-impact approvals bound
to a proposal digest.

Rules (documented, enforced here and by the DB in migration 0035):

* Identity comes ONLY from the authenticated Principal; callers never pass an
  actor. Run/team ownership is checked by the API guard before anything here.
* One leader per run (partial unique index). Leader-only: scope/permission
  redirects, handoff initiation, approval decisions.
* Ordinary guidance: any writer who is a participant; appended in ``seq``
  order. Concurrent instructions are NOT merged - last row wins by seq and
  every row is kept. Scope/permission changes need a reason, are leader-only,
  and invalidate the run's pending/approved high-impact approvals.
* Handoff: leader proposes to a participant who is an engineer/admin; leader
  keeps control until the recipient accepts (one open handoff per run).
* Approval: ``digest`` = sha256 over (action, sorted paths, diff). A decision
  must echo the digest the approver reviewed; any amend re-pends the request.
  Decisions and claims are compare-and-swap UPDATEs (race-safe).

Honest limit: this module gates and records. The DeepSeek SDK performs its own
file edits inside a run, so per-action enforcement inside the engine is not
wired yet - callers (sandbox/engine) must ``claim`` before executing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import timedelta
from typing import Any

from harness_contracts.roles import READ_ONLY, Principal, Role
from harness_contracts.timeutil import db_now
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

HIGH_IMPACT_ACTIONS = ("delete", "modify")  # modify = substantial file change
REDIRECT_KINDS = ("guidance", "scope_change", "permission_change")
MAX_PATHS = 100
MAX_PATH_LEN = 512
MAX_DIFF = 200_000
MAX_TEXT = 4000
MAX_REASON = 500
APPROVAL_TTL_ENV = "HARNESS_APPROVAL_TTL_SECONDS"


class CollabError(Exception):
    """Business-rule failure with a stable machine code and HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code = status, code
        super().__init__(message)


def _approval_ttl() -> timedelta:
    try:
        return timedelta(seconds=max(1, int(os.environ.get(APPROVAL_TTL_ENV, "3600"))))
    except ValueError:
        return timedelta(seconds=3600)


def proposal_digest(action: str, paths: list[str], diff: str) -> str:
    body = json.dumps({"a": action, "p": sorted(paths), "d": diff}, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def validate_paths(paths: list[str]) -> list[str]:
    if not paths or len(paths) > MAX_PATHS:
        raise CollabError(422, "BAD_PATHS", f"1..{MAX_PATHS} paths required")
    for p in paths:
        parts = p.split("/")
        if (
            not p
            or len(p) > MAX_PATH_LEN
            or "\x00" in p
            or "\\" in p
            or p.startswith("/")
            or ".." in parts
        ):
            raise CollabError(422, "BAD_PATHS", "paths must be relative, without '..'")
    return paths


async def _audit(db: Any, p: Principal, action: str, run_id: str, detail: dict[str, Any]) -> None:
    """Append an audit row inside the caller's transaction (atomic with the change)."""
    member = (
        await db.execute(
            text("SELECT id FROM team_members WHERE team_id = :t AND user_id = :u LIMIT 1"),
            {"t": p.teamId, "u": p.userId},
        )
    ).first()
    await db.execute(
        text(
            "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type,"
            " target_id, detail, ts) VALUES (:a, 'human', :act, 'agent_run', :r, :d, :ts)"
        ),
        {
            "a": member.id if member else None,
            "act": action,
            "r": run_id,
            "d": json.dumps({"userId": p.userId, "teamId": p.teamId, **detail}, default=str),
            "ts": db_now(),
        },
    )


async def _event(
    db: Any, p: Principal, run_id: str, kind: str, payload: dict[str, Any], request_id: str | None
) -> int | None:
    res = await db.execute(
        text(
            "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind,"
            " request_id, payload, ts) VALUES (:r, :t, :u, :k, :q, :p, :ts) RETURNING seq"
        ),
        {
            "r": run_id, "t": p.teamId, "u": p.userId, "k": kind, "q": request_id,
            "p": json.dumps(payload, default=str), "ts": db_now(),
        },
    )
    return res.scalar()


async def _participant(db: Any, run_id: str, user_id: str) -> Any:
    return (
        await db.execute(
            text("SELECT user_id, role FROM run_participants WHERE run_id = :r AND user_id = :u"),
            {"r": run_id, "u": user_id},
        )
    ).first()


async def _leader_id(db: Any, run_id: str) -> str | None:
    row = (
        await db.execute(
            text("SELECT user_id FROM run_participants WHERE run_id = :r AND role = 'leader'"),
            {"r": run_id},
        )
    ).first()
    return row.user_id if row else None


async def deny_not_leader(db: Any, p: Principal, run_id: str, action: str) -> CollabError:
    """Audit an unauthorized attempt and return the error to raise. Callers
    invoke this before making any other change, so committing is safe."""
    try:
        await _audit(db, p, "authz.denied", run_id, {"reason": "not_leader", "attempt": action})
        await db.commit()
    except Exception:  # noqa: BLE001 - audit must never mask the denial
        logger.exception("not_leader audit failed run=%s", run_id)
    return CollabError(403, "NOT_LEADER", "only the session leader may do this")


async def _require_leader(db: Any, p: Principal, run_id: str, action: str) -> None:
    if await _leader_id(db, run_id) != p.userId:
        raise await deny_not_leader(db, p, run_id, action)


# ----------------------------------------------------------------- participants


async def join(factory: Any, p: Principal, run_id: str) -> dict[str, Any]:
    role = "observer" if p.role in READ_ONLY else "participant"
    async with factory() as db:
        if await _participant(db, run_id, p.userId) is None:
            await db.execute(
                text(
                    "INSERT INTO run_participants (run_id, team_id, user_id, role, joined_at)"
                    " VALUES (:r, :t, :u, :role, :ts)"
                ),
                {"r": run_id, "t": p.teamId, "u": p.userId, "role": role, "ts": db_now()},
            )
            await _event(db, p, run_id, "participant.joined", {"role": role}, None)
            await _audit(db, p, "participant.joined", run_id, {"role": role})
        await db.commit()
    return await roster(factory, run_id)


async def roster(factory: Any, run_id: str) -> dict[str, Any]:
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT user_id, role FROM run_participants"
                    " WHERE run_id = :r ORDER BY joined_at"
                ),
                {"r": run_id},
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
    return {
        "participants": [{"userId": r.user_id, "role": r.role} for r in rows],
        "leader": next((r.user_id for r in rows if r.role == "leader"), None),
        "pendingHandoff": (
            {"id": pend.id, "from": pend.from_user_id, "to": pend.to_user_id} if pend else None
        ),
    }


async def claim_leader(factory: Any, p: Principal, run_id: str) -> dict[str, Any]:
    """Claim leadership of a leaderless run. Takeover of an existing leader is
    deliberately NOT offered here - use handoff."""
    if p.role not in (Role.ADMIN, Role.ENGINEER):
        raise CollabError(403, "ROLE_DENIED", "only engineers/admins may lead a run")
    await join(factory, p, run_id)
    try:
        async with factory() as db:
            res = await db.execute(
                text(
                    "UPDATE run_participants SET role = 'leader'"
                    " WHERE run_id = :r AND user_id = :u"
                    " AND NOT EXISTS (SELECT 1 FROM run_participants x"
                    " WHERE x.run_id = :r AND x.role = 'leader')"
                ),
                {"r": run_id, "u": p.userId},
            )
            if res.rowcount != 1:
                raise CollabError(409, "LEADER_EXISTS", "run already has a leader; use handoff")
            await _event(db, p, run_id, "leader.assigned", {"leader": p.userId}, None)
            await _audit(db, p, "leader.assigned", run_id, {"leader": p.userId})
            await db.commit()
    except IntegrityError:
        raise CollabError(409, "LEADER_EXISTS", "run already has a leader; use handoff") from None
    return await roster(factory, run_id)


# -------------------------------------------------------------------- redirects


async def redirect(
    factory: Any, p: Principal, run_id: str, kind: str, content: str,
    reason: str | None, request_id: str | None,
) -> dict[str, Any]:
    if kind not in REDIRECT_KINDS:
        raise CollabError(422, "BAD_KIND", f"kind must be one of {REDIRECT_KINDS}")
    content = (content or "").strip()
    if not content or len(content) > MAX_TEXT:
        raise CollabError(422, "BAD_CONTENT", f"content must be 1..{MAX_TEXT} chars")
    changes_scope = kind != "guidance"
    reason = (reason or "").strip()
    if changes_scope and not reason:
        raise CollabError(422, "REASON_REQUIRED", "scope/permission changes need a reason")
    if len(reason) > MAX_REASON:
        raise CollabError(422, "BAD_REASON", f"reason max {MAX_REASON} chars")
    async with factory() as db:
        me = await _participant(db, run_id, p.userId)
        if me is None or me.role == "observer":
            raise CollabError(403, "NOT_PARTICIPANT", "join the run as a participant first")
        if request_id:
            dup = (
                await db.execute(
                    text("SELECT seq FROM run_collab_events WHERE run_id = :r AND request_id = :q"),
                    {"r": run_id, "q": request_id},
                )
            ).first()
            if dup:
                return {"seq": dup.seq, "duplicate": True, "invalidatedApprovals": 0}
        invalidated = 0
        if changes_scope:
            await _require_leader(db, p, run_id, f"redirect:{kind}")
            invalidated = await _invalidate(db, p, run_id, f"{kind}: {reason}")
        seq = await _event(
            db, p, run_id, f"redirect.{kind}",
            {"content": content, "reason": reason or None}, request_id,
        )
        await _audit(
            db, p, "redirect.scope_changed" if changes_scope else "redirect.recorded",
            run_id, {"kind": kind, "seq": seq, "reason": reason or None},
        )
        await db.commit()
    return {"seq": seq, "duplicate": False, "invalidatedApprovals": invalidated}


# ---------------------------------------------------------------------- handoff


async def request_handoff(
    factory: Any, p: Principal, run_id: str, to_user_id: str
) -> dict[str, Any]:
    async with factory() as db:
        await _require_leader(db, p, run_id, "handoff")
        if to_user_id == p.userId:
            raise CollabError(422, "SELF_HANDOFF", "cannot hand off to yourself")
        target = await _participant(db, run_id, to_user_id)
        member = (
            await db.execute(
                text("SELECT role FROM team_members WHERE team_id = :t AND user_id = :u"),
                {"t": p.teamId, "u": to_user_id},
            )
        ).first()
        if target is None or member is None or member.role not in ("admin", "engineer"):
            raise CollabError(422, "BAD_RECIPIENT", "recipient must be a joined engineer/admin")
        hid = str(uuid.uuid4())
        try:
            await db.execute(
                text(
                    "INSERT INTO run_handoffs (id, run_id, team_id, from_user_id, to_user_id,"
                    " state, created_at) VALUES (:i, :r, :t, :f, :to, 'pending', :ts)"
                ),
                {"i": hid, "r": run_id, "t": p.teamId, "f": p.userId, "to": to_user_id,
                 "ts": db_now()},
            )
            await _event(
                db, p, run_id, "handoff.requested", {"handoffId": hid, "to": to_user_id}, None
            )
            await _audit(db, p, "handoff.requested", run_id, {"handoffId": hid, "to": to_user_id})
            await db.commit()
        except IntegrityError:
            raise CollabError(409, "HANDOFF_PENDING", "a handoff is already pending") from None
    return {"handoffId": hid, "state": "pending", "from": p.userId, "to": to_user_id}


async def decide_handoff(
    factory: Any, p: Principal, run_id: str, handoff_id: str, accept: bool
) -> dict[str, Any]:
    new = "accepted" if accept else "rejected"
    async with factory() as db:
        h = (
            await db.execute(
                text(
                    "SELECT from_user_id, to_user_id FROM run_handoffs"
                    " WHERE id = :i AND run_id = :r AND state = 'pending'"
                ),
                {"i": handoff_id, "r": run_id},
            )
        ).first()
        if h is None:
            raise CollabError(404, "NOT_FOUND", "no such pending handoff")
        if h.to_user_id != p.userId:
            raise await deny_not_leader(db, p, run_id, "handoff.decide")
        res = await db.execute(
            text(
                "UPDATE run_handoffs SET state = :s, decided_at = :ts"
                " WHERE id = :i AND state = 'pending'"
            ),
            {"s": new, "ts": db_now(), "i": handoff_id},
        )
        if res.rowcount != 1:
            raise CollabError(409, "HANDOFF_DECIDED", "handoff already decided")
        if accept:
            # Demote only if the proposer is still leader; otherwise abort.
            dem = await db.execute(
                text(
                    "UPDATE run_participants SET role = 'participant'"
                    " WHERE run_id = :r AND user_id = :f AND role = 'leader'"
                ),
                {"r": run_id, "f": h.from_user_id},
            )
            if dem.rowcount != 1:
                await db.rollback()
                raise CollabError(409, "LEADER_CHANGED", "proposer is no longer leader")
            await db.execute(
                text(
                    "UPDATE run_participants SET role = 'leader'"
                    " WHERE run_id = :r AND user_id = :u"
                ),
                {"r": run_id, "u": p.userId},
            )
        await _event(db, p, run_id, f"handoff.{new}", {"handoffId": handoff_id}, None)
        await _audit(
            db, p, f"handoff.{new}", run_id,
            {"handoffId": handoff_id, "from": h.from_user_id},
        )
        await db.commit()
    return {"handoffId": handoff_id, "state": new, "leader": p.userId if accept else h.from_user_id}


# -------------------------------------------------------------------- approvals


async def _invalidate(db: Any, p: Principal, run_id: str, why: str) -> int:
    res = await db.execute(
        text(
            "UPDATE high_impact_approvals SET state = 'invalidated', decided_by = NULL,"
            " outcome = :w WHERE run_id = :r AND state IN ('pending', 'approved')"
        ),
        {"w": why, "r": run_id},
    )
    if res.rowcount:
        await _audit(db, p, "approval.invalidated", run_id, {"count": res.rowcount, "why": why})
    return res.rowcount or 0


def _view(r: Any) -> dict[str, Any]:
    paths = r.paths if isinstance(r.paths, list) else json.loads(r.paths)
    return {
        "id": r.id, "runId": r.run_id, "action": r.action, "paths": paths,
        "summary": r.summary, "diff": r.diff, "digest": r.digest, "state": r.state,
        "requester": r.requester_user_id, "decidedBy": r.decided_by, "outcome": r.outcome,
        "expiresAt": str(r.expires_at),
    }


async def _get_approval(db: Any, run_id: str, approval_id: str) -> Any:
    row = (
        await db.execute(
            text("SELECT * FROM high_impact_approvals WHERE id = :i AND run_id = :r"),
            {"i": approval_id, "r": run_id},
        )
    ).first()
    if row is None:
        raise CollabError(404, "NOT_FOUND", "no such approval")
    return row


async def request_approval(
    factory: Any, p: Principal, run_id: str, action: str, paths: list[str],
    summary: str, diff: str,
) -> dict[str, Any]:
    if action not in HIGH_IMPACT_ACTIONS:
        raise CollabError(422, "BAD_ACTION", f"action must be one of {HIGH_IMPACT_ACTIONS}")
    validate_paths(paths)
    if not summary.strip() or len(summary) > MAX_TEXT or len(diff) > MAX_DIFF:
        raise CollabError(422, "BAD_PROPOSAL", "summary required; summary/diff too large")
    aid, now = str(uuid.uuid4()), db_now()
    async with factory() as db:
        me = await _participant(db, run_id, p.userId)
        if me is None or me.role == "observer":
            raise CollabError(403, "NOT_PARTICIPANT", "join the run as a participant first")
        await db.execute(
            text(
                "INSERT INTO high_impact_approvals (id, run_id, team_id, requester_user_id,"
                " action, paths, summary, diff, digest, state, expires_at, created_at)"
                " VALUES (:i, :r, :t, :u, :a, :p, :s, :d, :g, 'pending', :x, :c)"
            ),
            {"i": aid, "r": run_id, "t": p.teamId, "u": p.userId, "a": action,
             "p": json.dumps(paths), "s": summary, "d": diff,
             "g": proposal_digest(action, paths, diff), "x": now + _approval_ttl(), "c": now},
        )
        leader = await _leader_id(db, run_id)
        await _audit(db, p, "approval.requested", run_id,
                     {"approvalId": aid, "action": action, "paths": paths, "leader": leader})
        await db.commit()
        row = await _get_approval(db, run_id, aid)
    return {**_view(row), "designatedApprover": leader}


async def list_approvals(factory: Any, run_id: str) -> list[dict[str, Any]]:
    async with factory() as db:
        rows = (
            await db.execute(
                text("SELECT * FROM high_impact_approvals WHERE run_id = :r ORDER BY created_at"),
                {"r": run_id},
            )
        ).all()
    return [_view(r) for r in rows]


async def amend_approval(
    factory: Any, p: Principal, run_id: str, approval_id: str,
    paths: list[str], summary: str, diff: str,
) -> dict[str, Any]:
    """Change a proposal. Any prior approval is voided: it returns to pending
    with the NEW digest (requester only, and only before execution starts)."""
    validate_paths(paths)
    async with factory() as db:
        row = await _get_approval(db, run_id, approval_id)
        if row.requester_user_id != p.userId:
            raise CollabError(403, "NOT_REQUESTER", "only the requester may amend")
        digest = proposal_digest(row.action, paths, diff)
        res = await db.execute(
            text(
                "UPDATE high_impact_approvals SET paths = :p, summary = :s, diff = :d,"
                " digest = :g, state = 'pending', decided_by = NULL, decided_at = NULL,"
                " expires_at = :x WHERE id = :i AND state IN ('pending', 'approved')"
            ),
            {"p": json.dumps(paths), "s": summary, "d": diff, "g": digest,
             "x": db_now() + _approval_ttl(), "i": approval_id},
        )
        if res.rowcount != 1:
            raise CollabError(409, "NOT_AMENDABLE", f"approval is {row.state}")
        if row.state == "approved":
            await _audit(db, p, "approval.invalidated", run_id,
                         {"approvalId": approval_id, "why": "proposal amended"})
        await db.commit()
        row = await _get_approval(db, run_id, approval_id)
    return _view(row)


async def decide_approval(
    factory: Any, p: Principal, run_id: str, approval_id: str, approve: bool, digest: str
) -> dict[str, Any]:
    new = "approved" if approve else "rejected"
    async with factory() as db:
        await _require_leader(db, p, run_id, "approval.decide")
        row = await _get_approval(db, run_id, approval_id)
        res = await db.execute(
            text(
                "UPDATE high_impact_approvals SET state = :s, decided_by = :u, decided_at = :ts"
                " WHERE id = :i AND state = 'pending' AND digest = :g AND expires_at > :ts"
            ),
            {"s": new, "u": p.userId, "ts": db_now(), "i": approval_id, "g": digest},
        )
        if res.rowcount != 1:
            if row.state == "pending" and row.digest != digest:
                code, msg = "DIGEST_MISMATCH", "proposal changed since you reviewed it"
            elif row.state == "pending":
                await db.execute(
                    text("UPDATE high_impact_approvals SET state = 'expired'"
                         " WHERE id = :i AND state = 'pending'"),
                    {"i": approval_id},
                )
                await _audit(db, p, "approval.expired", run_id, {"approvalId": approval_id})
                await db.commit()
                code, msg = "EXPIRED", "approval request expired"
            else:
                code, msg = "NOT_PENDING", f"approval is already {row.state}"
            raise CollabError(409, code, msg)
        await _audit(db, p, f"approval.{new}", run_id,
                     {"approvalId": approval_id, "requester": row.requester_user_id,
                      "digest": digest, "paths": row.paths})
        await db.commit()
        row = await _get_approval(db, run_id, approval_id)
    return _view(row)


async def _require_executor(db: Any, p: Principal, run_id: str, row: Any) -> None:
    """Only the requester or the current leader may start/finish execution."""
    if p.userId != row.requester_user_id and p.userId != await _leader_id(db, run_id):
        raise await deny_not_leader(db, p, run_id, "approval.execute")


async def claim_approval(
    factory: Any, p: Principal, run_id: str, approval_id: str, digest: str
) -> dict[str, Any]:
    """Re-check proposal identity immediately before execution: approved ->
    executing only if the digest still matches and the approval is unexpired."""
    async with factory() as db:
        row = await _get_approval(db, run_id, approval_id)
        await _require_executor(db, p, run_id, row)
        res = await db.execute(
            text(
                "UPDATE high_impact_approvals SET state = 'executing'"
                " WHERE id = :i AND state = 'approved' AND digest = :g AND expires_at > :ts"
            ),
            {"i": approval_id, "g": digest, "ts": db_now()},
        )
        if res.rowcount != 1:
            raise CollabError(409, "NOT_APPROVED", f"cannot execute: approval is {row.state}"
                              " or the proposal changed")
        await _audit(db, p, "approval.claimed", run_id, {"approvalId": approval_id})
        await db.commit()
        row = await _get_approval(db, run_id, approval_id)
    return _view(row)


async def complete_approval(
    factory: Any, p: Principal, run_id: str, approval_id: str, ok: bool, outcome: str
) -> dict[str, Any]:
    new = "completed" if ok else "failed"
    async with factory() as db:
        row = await _get_approval(db, run_id, approval_id)
        await _require_executor(db, p, run_id, row)
        res = await db.execute(
            text(
                "UPDATE high_impact_approvals SET state = :s, outcome = :o"
                " WHERE id = :i AND state = 'executing'"
            ),
            {"s": new, "o": outcome[:MAX_TEXT], "i": approval_id},
        )
        if res.rowcount != 1:
            raise CollabError(409, "NOT_EXECUTING", "approval is not executing")
        await _audit(db, p, "approval.completed", run_id,
                     {"approvalId": approval_id, "ok": ok, "outcome": outcome[:200]})
        await db.commit()
        row = await _get_approval(db, run_id, approval_id)
    return _view(row)


async def history(
    factory: Any, run_id: str, after: int = 0, limit: int = 200
) -> list[dict[str, Any]]:
    """Ordered collaboration history; clients resume with ``after`` = last seq."""
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT seq, actor_user_id, kind, payload, ts FROM run_collab_events"
                    " WHERE run_id = :r AND seq > :a ORDER BY seq LIMIT :l"
                ),
                {"r": run_id, "a": after, "l": min(max(limit, 1), 500)},
            )
        ).all()
    return [
        {"seq": r.seq, "actor": r.actor_user_id, "kind": r.kind,
         "payload": r.payload if isinstance(r.payload, dict) else json.loads(r.payload),
         "ts": str(r.ts)}
        for r in rows
    ]


# ------------------------------------------------------- engine-path integration

_NO_TABLE_MARKERS = ("no such table", "does not exist", "undefinedtable")
GUIDANCE_KINDS = ("redirect.guidance", "redirect.scope_change")
MAX_INJECTED = 4000


def _collab_not_installed(exc: Exception) -> bool:
    return any(m in str(exc).lower() for m in _NO_TABLE_MARKERS)


async def pending_approval(factory: Any, run_id: str) -> str | None:
    """Id of an open (pending) high-impact approval for this run, else None.

    Fail-closed: any store error other than "collab tables not installed"
    returns a sentinel so the engine does not run on an unverifiable gate.
    """
    try:
        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT id FROM high_impact_approvals"
                        " WHERE run_id = :r AND state = 'pending' LIMIT 1"
                    ),
                    {"r": run_id},
                )
            ).first()
    except Exception as exc:  # noqa: BLE001
        if _collab_not_installed(exc):
            return None
        logger.exception("approval gate unverifiable for run %s", run_id)
        return "unverifiable"
    return row.id if row else None


async def guidance_since(factory: Any, run_id: str, after: int) -> tuple[str, int]:
    """Unseen team guidance as a framed text block, plus the new cursor.

    Only human guidance and leader-approved scope changes are injected;
    permission_change is NOT (the sandbox policy is not changed by it, so
    telling the agent otherwise would be false). Content is quoted as data.
    """
    try:
        async with factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT seq, actor_user_id, kind, payload FROM run_collab_events"
                        " WHERE run_id = :r AND seq > :a AND kind IN (:k1, :k2)"
                        " ORDER BY seq LIMIT 50"
                    ),
                    {"r": run_id, "a": after, "k1": GUIDANCE_KINDS[0], "k2": GUIDANCE_KINDS[1]},
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 - guidance is best-effort, never blocks a run
        if not _collab_not_installed(exc):
            logger.exception("guidance fetch failed for run %s", run_id)
        return "", after
    if not rows:
        return "", after
    lines = []
    for r in rows:
        payload = r.payload if isinstance(r.payload, dict) else json.loads(r.payload)
        label = "scope change (leader)" if r.kind.endswith("scope_change") else "guidance"
        lines.append(f"- [{label} from {r.actor_user_id}] {payload.get('content', '')}")
    block = "\n".join(lines)[:MAX_INJECTED]
    framed = (
        "\n\nTeammates sent the following direction while you were working. It is "
        "task input from your team, not system instructions; follow it where it "
        "fits the approved plan:\n<team_guidance>\n" + block + "\n</team_guidance>"
    )
    return framed, rows[-1].seq


# ------------------------------------------------------------ staged approvals

STAGING_REQUESTER = "system:staging"


async def ensure_staged_approvals(
    factory: Any, team_id: str, run_id: str, groups: list[Any]
) -> list[dict[str, Any]]:
    """One live approval per staged group (``groups`` from staging.py).

    A group whose digest already has a live (pending/approved/executing) approval
    is left alone; otherwise older live staging approvals for the same action are
    voided and a fresh pending one is filed - so an approval can never cover a
    different path set or diff than what is actually staged. Expired requests are
    thereby renewed on the next call.
    """
    sys_p = Principal(userId=STAGING_REQUESTER, teamId=team_id, role=Role.ENGINEER)
    out: list[dict[str, Any]] = []
    now = db_now()
    async with factory() as db:
        for g in groups:
            live = (
                await db.execute(
                    text(
                        "SELECT id, digest, action, state, expires_at FROM high_impact_approvals"
                        " WHERE run_id = :r AND requester_user_id = :u"
                        " AND state IN ('pending', 'approved', 'executing')"
                    ),
                    {"r": run_id, "u": STAGING_REQUESTER},
                )
            ).all()
            hit = next((x for x in live if x.digest == g.digest and x.action == g.action), None)
            if hit is not None:
                out.append({"id": hit.id, "digest": hit.digest, "state": hit.state})
                continue
            await db.execute(
                text(
                    "UPDATE high_impact_approvals SET state = 'invalidated',"
                    " outcome = 'staged content changed'"
                    " WHERE run_id = :r AND requester_user_id = :u AND action = :a"
                    " AND state IN ('pending', 'approved')"
                ),
                {"r": run_id, "u": STAGING_REQUESTER, "a": g.action},
            )
            aid = str(uuid.uuid4())
            await db.execute(
                text(
                    "INSERT INTO high_impact_approvals (id, run_id, team_id, requester_user_id,"
                    " action, paths, summary, diff, digest, state, expires_at, created_at)"
                    " VALUES (:i, :r, :t, :u, :a, :p, :s, :d, :g, 'pending', :x, :c)"
                ),
                {"i": aid, "r": run_id, "t": team_id, "u": STAGING_REQUESTER, "a": g.action,
                 "p": json.dumps(g.paths), "s": g.summary, "d": g.diff, "g": g.digest,
                 "x": now + _approval_ttl(), "c": now},
            )
            await _audit(db, sys_p, "approval.requested", run_id,
                         {"approvalId": aid, "action": g.action, "paths": g.paths,
                          "source": "staging"})
            out.append({"id": aid, "digest": g.digest, "state": "pending"})
        await db.commit()
    return out
