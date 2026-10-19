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
import re
import uuid
from datetime import timedelta
from typing import Any

from harness_contracts.roles import READ_ONLY, Principal, Role
from harness_contracts.timeutil import db_now
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import domain_metrics

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

    def __init__(self, status: int, code: str, message: str, audited: bool = False) -> None:
        self.status, self.code = status, code
        # True when the raising site already wrote its own audit row, so the API
        # layer does not add a second authz.denied row for the same refusal.
        self.audited = audited
        super().__init__(message)


def _approval_ttl() -> timedelta:
    try:
        return timedelta(seconds=max(1, int(os.environ.get(APPROVAL_TTL_ENV, "3600"))))
    except ValueError:
        return timedelta(seconds=3600)


PILOT_TEAMS_ENV = "HARNESS_PILOT_TEAMS"


def pilot_allowed(team_id: str) -> bool:
    """Per-team pilot allowlist: comma-separated team ids; unset or empty = every team."""
    allow = {x.strip() for x in os.environ.get(PILOT_TEAMS_ENV, "").split(",") if x.strip()}
    return not allow or team_id in allow


def _self_approval_allowed() -> bool:
    # Policy: a requester may not approve their own request (fail closed);
    # operators of single-person teams opt out explicitly.
    return os.environ.get("HARNESS_ALLOW_SELF_APPROVAL", "") == "1"


def proposal_digest(action: str, paths: list[str], diff: str, content: str = "") -> str:
    """``content`` (staged groups) fingerprints the full bytes: ``diff`` is only a display
    and is truncated or replaced by a placeholder for binary files."""
    parts = {"a": action, "p": sorted(paths), "d": diff}
    if content:
        parts["c"] = content
    body = json.dumps(parts, separators=(",", ":"))
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
            domain_metrics.inc("harness_isolation_violation_total", "path_traversal")
            raise CollabError(422, "BAD_PATHS", "paths must be relative, without '..'")
    return paths


_SECRET_KEY = re.compile(r"token|secret|password|passwd|key|otp|authorization|credential", re.I)


def _scrub(data: dict[str, Any]) -> dict[str, Any]:
    """Drop secret-shaped keys from anything that reaches the audit log or event stream
    (same rule as section1 audit_emit._scrub: a false positive costs a field, a miss
    leaks a secret into an append-only log)."""
    return {k: v for k, v in data.items() if not _SECRET_KEY.search(k)}


async def _audit(db: Any, p: Principal, action: str, run_id: str, detail: dict[str, Any]) -> None:
    """Append an audit row inside the caller's transaction (atomic with the change)."""
    detail = _scrub(detail)
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
    if db.bind.dialect.name == "postgresql":
        # Per-run commit order: serialise this run's event inserts until commit so `seq`
        # order == visibility order. The WebSocket cursor (afterSeq) is then gap-free:
        # a lower seq can never commit after a higher one was already pushed. Taken
        # after the state change and before commit; nothing else is locked afterwards.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"collab-events:{run_id}"},
        )
    payload = _scrub(payload)
    res = await db.execute(
        text(
            "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind,"
            " request_id, payload, ts) VALUES (:r, :t, :u, :k, :q, :p, :ts) RETURNING seq"
        ),
        {
            "r": run_id,
            "t": p.teamId,
            "u": p.userId,
            "k": kind,
            "q": request_id,
            "p": json.dumps(payload, default=str),
            "ts": db_now(),
        },
    )
    return res.scalar()


def _denied(reason: str) -> None:
    """Count a refused approval request/decision/execution (alerting: approval abuse)."""
    domain_metrics.inc("harness_approval_denied_total", reason)


async def _approval_change(
    db: Any,
    p: Principal,
    run_id: str,
    name: str,
    approval_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    """One approval transition = a product event (shown live to collaborators, no diff
    or free text) plus a security audit row with before/after values, same transaction."""
    detail = {"approvalId": approval_id, "before": before, "after": after, **(extra or {})}
    await _event(db, p, run_id, name, detail, None)
    await _audit(db, p, name, run_id, detail)


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


async def audit_denial(
    factory: Any, p: Principal, run_id: str, attempt: str, code: str, status: int
) -> None:
    """Denial-audit policy: every refused collaboration request by an
    authenticated principal (foreign run, role, participant/leader/state/recipient
    refusals) leaves exactly one ``authz.denied`` row carrying the stable error
    code. Own session + commit, because the refusing transaction is abandoned;
    failure to audit never changes the response."""
    if factory is None:
        return
    try:
        async with factory() as db:
            await _audit(
                db, p, "authz.denied", run_id,
                {"reason": code, "status": status, "attempt": attempt},
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - audit must never mask the denial
        logger.exception("denial audit failed run=%s code=%s", run_id, code)


async def deny_not_leader(db: Any, p: Principal, run_id: str, action: str) -> CollabError:
    """Audit an unauthorized attempt and return the error to raise. Callers
    invoke this before making any other change, so committing is safe."""
    audited = False
    if action.startswith("approval"):
        _denied("not_leader")
    try:
        await _audit(db, p, "authz.denied", run_id, {"reason": "not_leader", "attempt": action})
        await db.commit()
        audited = True
    except Exception:  # noqa: BLE001 - audit must never mask the denial
        logger.exception("not_leader audit failed run=%s", run_id)
    return CollabError(403, "NOT_LEADER", "only the session leader may do this", audited=audited)


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
    """Claim leadership of a leaderless run. A sitting leader is replaced only by a
    team ADMIN (absent-leader takeover: handoff needs the leader's cooperation, so
    it cannot recover a run whose leader is gone). Engineers still use handoff."""
    if p.role not in (Role.ADMIN, Role.ENGINEER):
        raise CollabError(403, "ROLE_DENIED", "only engineers/admins may lead a run")
    await join(factory, p, run_id)
    try:
        async with factory() as db:
            prev = await _leader_id(db, run_id)
            if prev == p.userId:
                raise CollabError(409, "LEADER_EXISTS", "you already lead this run")
            if prev is not None:
                if p.role != Role.ADMIN:
                    raise CollabError(409, "LEADER_EXISTS", "run already has a leader; use handoff")
                # One transaction: demote -> cancel pending handoff -> promote.
                dem = await db.execute(
                    text(
                        "UPDATE run_participants SET role = 'participant'"
                        " WHERE run_id = :r AND user_id = :f AND role = 'leader'"
                    ),
                    {"r": run_id, "f": prev},
                )
                if dem.rowcount != 1:
                    raise CollabError(409, "LEADER_CHANGED", "leader changed; retry")
                await db.execute(
                    text(
                        "UPDATE run_handoffs SET state = 'rejected', decided_at = :ts"
                        " WHERE run_id = :r AND state = 'pending'"
                    ),
                    {"r": run_id, "ts": db_now()},
                )
                await db.execute(
                    text(
                        "UPDATE run_participants SET role = 'leader'"
                        " WHERE run_id = :r AND user_id = :u"
                    ),
                    {"r": run_id, "u": p.userId},
                )
                detail = {
                    "leader": p.userId,
                    "from": prev,
                    "before": {"leader": prev},
                    "after": {"leader": p.userId},
                }
                await _event(db, p, run_id, "leader.takeover", detail, None)
                await _audit(db, p, "leader.takeover", run_id, detail)
                await db.commit()
                return await roster(factory, run_id)
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
            assigned = {
                "leader": p.userId,
                "before": {"leader": None},
                "after": {"leader": p.userId},
            }
            await _event(db, p, run_id, "leader.assigned", assigned, None)
            await _audit(db, p, "leader.assigned", run_id, assigned)
            await db.commit()
    except IntegrityError:
        raise CollabError(409, "LEADER_EXISTS", "run already has a leader; use handoff") from None
    return await roster(factory, run_id)


# -------------------------------------------------------------------- redirects


async def redirect(
    factory: Any,
    p: Principal,
    run_id: str,
    kind: str,
    content: str,
    reason: str | None,
    request_id: str | None,
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
                domain_metrics.inc("harness_event_delivery_total", "duplicate")
                return {"seq": dup.seq, "duplicate": True, "invalidatedApprovals": 0}
        invalidated = 0
        if changes_scope:
            await _require_leader(db, p, run_id, f"redirect:{kind}")
            invalidated = await _invalidate(db, p, run_id, f"{kind}: {reason}")
        try:
            seq = await _event(
                db,
                p,
                run_id,
                f"redirect.{kind}",
                {"content": content, "reason": reason or None},
                request_id,
            )
            await _audit(
                db,
                p,
                "redirect.scope_changed" if changes_scope else "redirect.recorded",
                run_id,
                {
                    "kind": kind,
                    "seq": seq,
                    "reason": reason or None,
                    "after": {"invalidatedApprovals": invalidated} if changes_scope else None,
                },
            )
            await db.commit()
        except IntegrityError:
            # Two simultaneous retries with the same requestId: the unique index lets one
            # row in, the other is a duplicate, not a 500.
            await db.rollback()
            dup = (
                await db.execute(
                    text("SELECT seq FROM run_collab_events WHERE run_id = :r AND request_id = :q"),
                    {"r": run_id, "q": request_id},
                )
            ).first()
            if request_id is None or dup is None:
                raise
            domain_metrics.inc("harness_event_delivery_total", "duplicate")
            return {"seq": dup.seq, "duplicate": True, "invalidatedApprovals": 0}
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
        if (
            target is None
            or member is None
            or str(member.role).lower() not in ("admin", "engineer")
        ):
            raise CollabError(422, "BAD_RECIPIENT", "recipient must be a joined engineer/admin")
        hid = str(uuid.uuid4())
        try:
            await db.execute(
                text(
                    "INSERT INTO run_handoffs (id, run_id, team_id, from_user_id, to_user_id,"
                    " state, created_at) VALUES (:i, :r, :t, :f, :to, 'pending', :ts)"
                ),
                {
                    "i": hid,
                    "r": run_id,
                    "t": p.teamId,
                    "f": p.userId,
                    "to": to_user_id,
                    "ts": db_now(),
                },
            )
            requested = {
                "handoffId": hid,
                "to": to_user_id,
                "before": {"leader": p.userId},
                "after": {"leader": p.userId, "pendingHandoffTo": to_user_id},
            }
            await _event(db, p, run_id, "handoff.requested", requested, None)
            await _audit(db, p, "handoff.requested", run_id, requested)
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
                    "UPDATE run_participants SET role = 'leader' WHERE run_id = :r AND user_id = :u"
                ),
                {"r": run_id, "u": p.userId},
            )
        decided = {
            "handoffId": handoff_id,
            "from": h.from_user_id,
            "before": {"leader": h.from_user_id, "pendingHandoffTo": h.to_user_id},
            "after": {"leader": p.userId if accept else h.from_user_id},
        }
        await _event(db, p, run_id, f"handoff.{new}", decided, None)
        await _audit(db, p, f"handoff.{new}", run_id, decided)
        await db.commit()
    return {"handoffId": handoff_id, "state": new, "leader": p.userId if accept else h.from_user_id}


# -------------------------------------------------------------------- approvals


async def _invalidate(db: Any, p: Principal, run_id: str, why: str) -> int:
    open_rows = (
        await db.execute(
            text(
                "SELECT id, state FROM high_impact_approvals"
                " WHERE run_id = :r AND state IN ('pending', 'approved')"
            ),
            {"r": run_id},
        )
    ).all()
    res = await db.execute(
        text(
            "UPDATE high_impact_approvals SET state = 'invalidated', decided_by = NULL,"
            " outcome = :w WHERE run_id = :r AND state IN ('pending', 'approved')"
        ),
        {"w": why, "r": run_id},
    )
    if res.rowcount:
        detail = {
            "count": res.rowcount,
            "why": why,
            "before": [{"id": r.id, "state": r.state} for r in open_rows],
            "after": {"state": "invalidated"},
        }
        await _event(db, p, run_id, "approval.invalidated", detail, None)
        await _audit(db, p, "approval.invalidated", run_id, detail)
    return res.rowcount or 0


def _view(r: Any) -> dict[str, Any]:
    paths = r.paths if isinstance(r.paths, list) else json.loads(r.paths)
    return {
        "id": r.id,
        "runId": r.run_id,
        "action": r.action,
        "paths": paths,
        "summary": r.summary,
        "diff": r.diff,
        "digest": r.digest,
        "state": r.state,
        "requester": r.requester_user_id,
        "decidedBy": r.decided_by,
        "outcome": r.outcome,
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
    factory: Any,
    p: Principal,
    run_id: str,
    action: str,
    paths: list[str],
    summary: str,
    diff: str,
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
            {
                "i": aid,
                "r": run_id,
                "t": p.teamId,
                "u": p.userId,
                "a": action,
                "p": json.dumps(paths),
                "s": summary,
                "d": diff,
                "g": proposal_digest(action, paths, diff),
                "x": now + _approval_ttl(),
                "c": now,
            },
        )
        leader = await _leader_id(db, run_id)
        await _approval_change(
            db, p, run_id, "approval.requested", aid, None, {"state": "pending"},
            {"action": action, "paths": paths, "leader": leader},
        )
        await db.commit()
        row = await _get_approval(db, run_id, aid)
    return {**_view(row), "designatedApprover": leader}


async def list_approvals(factory: Any, run_id: str) -> list[dict[str, Any]]:
    try:
        await expire_stale_approvals(factory, run_id)  # show expiry truthfully, with evidence
    except Exception:  # noqa: BLE001 - listing must work on any schema
        logger.warning("approval expiry sweep failed for run %s", run_id, exc_info=True)
    async with factory() as db:
        rows = (
            await db.execute(
                text("SELECT * FROM high_impact_approvals WHERE run_id = :r ORDER BY created_at"),
                {"r": run_id},
            )
        ).all()
    return [_view(r) for r in rows]


async def amend_approval(
    factory: Any,
    p: Principal,
    run_id: str,
    approval_id: str,
    paths: list[str],
    summary: str,
    diff: str,
) -> dict[str, Any]:
    """Change a proposal. Any prior approval is voided: it returns to pending
    with the NEW digest (requester only, and only before execution starts)."""
    validate_paths(paths)
    async with factory() as db:
        row = await _get_approval(db, run_id, approval_id)
        if row.requester_user_id != p.userId:
            _denied("not_requester")
            raise CollabError(403, "NOT_REQUESTER", "only the requester may amend")
        digest = proposal_digest(row.action, paths, diff)
        res = await db.execute(
            text(
                "UPDATE high_impact_approvals SET paths = :p, summary = :s, diff = :d,"
                " digest = :g, state = 'pending', decided_by = NULL, decided_at = NULL,"
                " expires_at = :x WHERE id = :i AND state IN ('pending', 'approved')"
            ),
            {
                "p": json.dumps(paths),
                "s": summary,
                "d": diff,
                "g": digest,
                "x": db_now() + _approval_ttl(),
                "i": approval_id,
            },
        )
        if res.rowcount != 1:
            _denied("not_amendable")
            raise CollabError(409, "NOT_AMENDABLE", f"approval is {row.state}")
        # The proposal changed: any prior approval is void and the request is pending again.
        await _approval_change(
            db, p, run_id, "approval.amended", approval_id,
            {"state": row.state, "digest": row.digest},
            {"state": "pending", "digest": digest},
            {"paths": paths, "voidedApproval": row.state == "approved"},
        )
        if row.state == "approved":
            await _approval_change(
                db, p, run_id, "approval.invalidated", approval_id,
                {"state": "approved", "digest": row.digest},
                {"state": "pending", "digest": digest},
                {"why": "proposal amended"},
            )
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
        if approve and row.requester_user_id == p.userId and not _self_approval_allowed():
            _denied("self_approval")
            await _audit(db, p, "approval.self_denied", run_id, {"approvalId": approval_id})
            await db.commit()
            raise CollabError(
                403, "SELF_APPROVAL", "another leader must approve your own request", audited=True
            )
        res = await db.execute(
            text(
                "UPDATE high_impact_approvals SET state = :s, decided_by = :u, decided_at = :ts"
                " WHERE id = :i AND state = 'pending' AND digest = :g AND expires_at > :ts"
                # leadership is part of the CAS: a leader demoted mid-request cannot decide
                " AND EXISTS (SELECT 1 FROM run_participants x WHERE x.run_id = :run"
                " AND x.user_id = :u AND x.role = 'leader')"
            ),
            {"s": new, "u": p.userId, "ts": db_now(), "i": approval_id, "g": digest, "run": run_id},
        )
        if res.rowcount != 1:
            if row.state == "pending" and await _leader_id(db, run_id) != p.userId:
                raise await deny_not_leader(db, p, run_id, "approval.decide")
            if row.state == "pending" and row.digest != digest:
                code, msg = "DIGEST_MISMATCH", "proposal changed since you reviewed it"
            elif row.state == "pending":
                await db.execute(
                    text(
                        "UPDATE high_impact_approvals SET state = 'expired'"
                        " WHERE id = :i AND state = 'pending'"
                    ),
                    {"i": approval_id},
                )
                await _approval_change(
                    db, p, run_id, "approval.expired", approval_id,
                    {"state": "pending"}, {"state": "expired"},
                )
                await db.commit()
                _denied("expired")
                code, msg = "EXPIRED", "approval request expired"
                raise CollabError(409, code, msg, audited=True)
            else:
                code, msg = "NOT_PENDING", f"approval is already {row.state}"
            _denied(code.lower())
            raise CollabError(409, code, msg)
        await _approval_change(
            db, p, run_id, f"approval.{new}", approval_id,
            {"state": "pending"}, {"state": new, "decidedBy": p.userId},
            {"requester": row.requester_user_id, "digest": digest, "paths": row.paths},
        )
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
            _denied("not_approved")
            raise CollabError(
                409,
                "NOT_APPROVED",
                f"cannot execute: approval is {row.state} or the proposal changed",
            )
        await _approval_change(
            db, p, run_id, "approval.claimed", approval_id,
            {"state": "approved"}, {"state": "executing", "claimedBy": p.userId},
        )
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
            _denied("not_executing")
            raise CollabError(409, "NOT_EXECUTING", "approval is not executing")
        await _approval_change(
            db, p, run_id, "approval.completed", approval_id,
            {"state": "executing"}, {"state": new},
            {"ok": ok, "outcome": outcome[:200]},
        )
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
                    " WHERE run_id = :r AND seq > :a AND kind <> 'guidance.delivered'"
                    " ORDER BY seq LIMIT :l"
                ),
                {"r": run_id, "a": after, "l": min(max(limit, 1), 500)},
            )
        ).all()
    return [
        {
            "seq": r.seq,
            "actor": r.actor_user_id,
            "kind": r.kind,
            "payload": r.payload if isinstance(r.payload, dict) else json.loads(r.payload),
            "ts": str(r.ts),
        }
        for r in rows
    ]


# ------------------------------------------------------- engine-path integration

_NO_TABLE_MARKERS = ("no such table", "does not exist", "undefinedtable")
GUIDANCE_KINDS = ("redirect.guidance", "redirect.scope_change")
GUIDANCE_DELIVERED = "guidance.delivered"
MAX_INJECTED = 4000


def _collab_not_installed(exc: Exception) -> bool:
    return any(m in str(exc).lower() for m in _NO_TABLE_MARKERS)


async def unapplied_staged_approval(factory: Any, run_id: str) -> str | None:
    """Fail closed until every open staged decision has actually been applied."""
    try:
        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT id FROM high_impact_approvals WHERE run_id = :r"
                        " AND requester_user_id = 'system:staging'"
                        " AND state IN ('pending', 'approved', 'executing') LIMIT 1"
                    ),
                    {"r": run_id},
                )
            ).first()
        return row.id if row else None
    except Exception:  # noqa: BLE001 - an unreadable gate is never permission
        return "unverifiable"


EXPIRY_ACTOR = "system:approval-expiry"


async def expire_stale_approvals(factory: Any, run_id: str) -> list[str]:
    """Move pending approvals past their TTL to ``expired`` (event + audit row each).

    An expired request can no longer be decided or claimed (the decision CAS checks
    ``expires_at``); this makes the stored state say so, so a request nobody answered
    stops holding the engine gate shut forever. Returns the expired approval ids.
    """
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "UPDATE high_impact_approvals SET state = 'expired'"
                    " WHERE run_id = :r AND state = 'pending' AND expires_at <= :now"
                    " RETURNING id, team_id"
                ),
                {"r": run_id, "now": db_now()},
            )
        ).all()
        for r in rows:
            who = Principal(userId=EXPIRY_ACTOR, teamId=r.team_id, role=Role.ENGINEER)
            await _approval_change(
                db, who, run_id, "approval.expired", r.id,
                {"state": "pending"}, {"state": "expired"}, {"reason": "ttl"},
            )
            domain_metrics.inc("harness_approval_denied_total", "expired")
        await db.commit()
    return [r.id for r in rows]


async def pending_approval(factory: Any, run_id: str) -> str | None:
    """Id of an open (pending, unexpired) high-impact approval for this run, else None.

    Hot path = one query, no pending row, return. Only when a request is pending is the
    TTL sweep run: a request past its expiry is moved to ``expired`` (event + audit) and
    stops holding the engine gate shut. Fail-closed: any store error other than
    "collab tables not installed" returns a sentinel so the engine does not run on an
    unverifiable gate.
    """
    sql = (
        "SELECT id FROM high_impact_approvals"
        " WHERE run_id = :r AND state = 'pending' LIMIT 1"
    )
    try:
        for attempt in (1, 2):
            async with factory() as db:
                row = (await db.execute(text(sql), {"r": run_id})).first()
            if row is None:
                return None
            if attempt == 2:
                return row.id
            try:
                expired = await expire_stale_approvals(factory, run_id)
            except Exception:  # noqa: BLE001 - housekeeping only: the gate answer is unaffected
                logger.warning("approval expiry sweep failed for run %s", run_id, exc_info=True)
                return row.id
            if row.id not in expired:
                return row.id
            # that request just expired: look again for any other still-pending one
    except Exception as exc:  # noqa: BLE001
        if _collab_not_installed(exc):
            return None
        logger.exception("approval gate unverifiable for run %s", run_id)
        return "unverifiable"
    return None


async def mark_guidance_delivered(factory: Any, run_id: str, up_to: int) -> None:
    """Persist the delivery cursor as an append-only marker (survives restarts).

    Written after the engine call returns, so a crash re-delivers guidance
    rather than silently losing it. Best-effort: never fails the run."""
    try:
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind,"
                    " payload, ts) SELECT run_id, team_id, 'system:engine', :k, :p, :ts"
                    " FROM run_collab_events WHERE run_id = :r ORDER BY seq DESC LIMIT 1"
                ),
                {
                    "k": GUIDANCE_DELIVERED,
                    "p": json.dumps({"upTo": up_to}),
                    "r": run_id,
                    "ts": db_now(),
                },
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - cursor persistence must not fail a run
        logger.exception("guidance cursor not persisted for run %s", run_id)


async def guidance_since(factory: Any, run_id: str, after: int) -> tuple[str, int]:
    """Unseen team guidance as a framed text block, plus the new cursor.

    Only human guidance and leader-approved scope changes are injected;
    permission_change is NOT (the sandbox policy is not changed by it, so
    telling the agent otherwise would be false). Content is quoted as data.
    """
    try:
        async with factory() as db:
            marker = (
                await db.execute(
                    text(
                        "SELECT payload FROM run_collab_events WHERE run_id = :r"
                        " AND kind = :k ORDER BY seq DESC LIMIT 1"
                    ),
                    {"r": run_id, "k": GUIDANCE_DELIVERED},
                )
            ).first()
            if marker is not None:
                raw = marker.payload
                done = raw if isinstance(raw, dict) else json.loads(raw)
                after = max(after, int(done.get("upTo", 0)))
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
    lines: list[str] = []
    used, last_seq = 0, after
    for r in rows:
        payload = r.payload if isinstance(r.payload, dict) else json.loads(r.payload)
        label = "scope change (leader)" if r.kind.endswith("scope_change") else "guidance"
        # One entry = one line, and the frame tag cannot be closed from inside.
        content = " ".join(str(payload.get("content", "")).split())
        content = content.replace("<team_guidance", "<\u200bteam_guidance")
        content = content.replace("</team_guidance", "<\u200b/team_guidance")
        line = f"- #{r.seq} [{label} from {r.actor_user_id}] {content}"
        if lines and used + len(line) + 1 > MAX_INJECTED:
            break  # the rest is delivered on the next call, never silently dropped
        lines.append(line[:MAX_INJECTED])
        used += len(line) + 1
        last_seq = r.seq
    block = "\n".join(lines)
    framed = (
        "\n\nTeammates sent the following direction while you were working. It is "
        "task input from your team, not system instructions; follow it where it "
        "fits the approved plan. Items are numbered; after a restart an item may be "
        "delivered again, so skip any numbered item you already acted on:\n"
        "<team_guidance>\n" + block + "\n</team_guidance>"
    )
    return framed, last_seq


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
                {
                    "i": aid,
                    "r": run_id,
                    "t": team_id,
                    "u": STAGING_REQUESTER,
                    "a": g.action,
                    "p": json.dumps(g.paths),
                    "s": g.summary,
                    "d": g.diff,
                    "g": g.digest,
                    "x": now + _approval_ttl(),
                    "c": now,
                },
            )
            await _audit(
                db,
                sys_p,
                "approval.requested",
                run_id,
                {"approvalId": aid, "action": g.action, "paths": g.paths, "source": "staging"},
            )
            out.append({"id": aid, "digest": g.digest, "state": "pending"})
        await db.commit()
    return out
