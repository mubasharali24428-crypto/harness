"""Execution tracking for a run (part 44.004-010). Rules, limits, honest gaps: docs/execution.md.

Subtasks with dependencies, blockers and evidence references; bounded execution limits; the
checkpoint and safe-resume answer; agent-reported versus human-accepted completion; abandon or
fail with a reason; retry that cannot duplicate side effects.

What is ENFORCED and what is only RECORDED (repeated in ``/execution-status``):

* ``seconds``, ``modelCalls`` and ``costMicrousd`` are checked at run start (``start_execution``
  409), at queue claim (``_dispatch_claim``: the claim is finished and the run goes BLOCKED with
  the reason) and by :func:`sweep_running_limits`, which pauses a run that is already running (the
  lead wires it into the worker loop; nothing calls it by itself).
* ``modelCalls`` counts ``cost_ledger`` rows of the run and ``costMicrousd`` sums them: the engine
  path writes one row when an engine run COMPLETES, so spend inside a run that is still running is
  invisible until then. ``seconds`` is rebuilt from ``agent_events`` timestamps.
* ``toolCalls`` counts durable ``agent_tool_calls`` rows and is checked at run start, queue claim,
  and by the running-limit sweep. The engine must persist a row for every tool call for this cap
  to reflect actual activity.

State changes use only the state machine's own vocabulary: abandon is ``stop_agent`` (DONE), a
blocked claim is ``execution_failed`` (BLOCKED), a limit sweep is ``pause_agent`` (PAUSED), retry
is ``resume_agent``. No state is invented and no evidence is ever deleted.

Identity is always the authenticated Principal. A foreign or unknown run answers the same 404
envelope as an unknown one (the shared run guard). Raw SQL only (``text()``): sections may import
only ``harness_contracts``.
"""

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from harness_contracts.roles import CAN_APPROVE_PLANS, Principal
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import api as _api
from harness_orchestrator import collab
from harness_orchestrator.session_gate import session_block
from harness_orchestrator.state_machine import AgentState
from harness_orchestrator.workspace_gate import workspace_block

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orchestrator", tags=["execution"])

_service: Any = None

MAX_SUBTASKS = 200
MAX_DEPS = 20
MAX_EVIDENCE = 50
MAX_OPEN_BLOCKERS = 10
MAX_OUTPUTS = 50
TITLE_MAX, DESC_MAX, REF_MAX, NOTE_MAX = 200, 2000, 500, 500
SUMMARY_MAX = 2000
REASON_MAX = 500
# a retry stuck in 'requested' (the process died before it resolved) frees the run after this
STALE_REQUEST_SECONDS = 120
SUBTASK_STATES = ("todo", "in_progress", "blocked", "done", "cancelled")
CODE_LIMIT = "EXECUTION_LIMIT_EXCEEDED"
CODE_ABANDONED = "RUN_ABANDONED"
BOUNDS = {
    "maxSeconds": (1, 604_800),
    "maxModelCalls": (1, 100_000),
    "maxToolCalls": (1, 1_000_000),
    "maxCostMicrousd": (1, 1_000_000_000_000),
}
# (usage key, column, wire key)
_DIMS = (
    ("seconds", "max_seconds", "maxSeconds"),
    ("modelCalls", "max_model_calls", "maxModelCalls"),
    ("toolCalls", "max_tool_calls", "maxToolCalls"),
    ("costMicrousd", "max_cost_microusd", "maxCostMicrousd"),
)
ENFORCEMENT = {
    "seconds": {
        "enforced": True,
        "at": ["run start", "queue claim", "running sweep"],
        "source": "agent_events timestamps; an open interval counts only while a claim is running",
    },
    "modelCalls": {
        "enforced": True,
        "at": ["run start", "queue claim", "running sweep"],
        "source": "cost_ledger rows of the run; the engine writes one row per completed engine run",
    },
    "toolCalls": {
        "enforced": True,
        "at": ["run start", "queue claim", "running sweep"],
        "source": "durable agent_tool_calls rows for the run",
    },
    "costMicrousd": {
        "enforced": True,
        "at": ["run start", "queue claim", "running sweep"],
        "source": "SUM(cost_ledger.cost_usd); an in-flight run's spend is invisible until it ends",
    },
}
_START_EVENTS = ("execution_started", "resumed")
_STOP_EVENTS = (
    "paused",
    "engine_completed",
    "engine_failed",
    "blocked_awaiting_clarification",
    "done_signal_received",
)
_CHECKPOINT_EVENTS = _START_EVENTS + _STOP_EVENTS + ("review_passed", "review_failed", "pr_opened")
_MISSING_TABLE = ("no such table", "does not exist", "undefinedtable")


def configure_execution(service: Any) -> None:
    """Deploy-time wiring (like configure_discussion): the service owning the DB factory."""
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


def _q(sql: str, *expanding: str) -> Any:
    stmt = text(sql)
    return (
        stmt.bindparams(*[bindparam(n, expanding=True) for n in expanding]) if expanding else stmt
    )


class ExecError(collab.CollabError):
    """CollabError that also carries structured data for the HTTP body."""

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(status, code, message)
        self.extra = extra or {}


def _envelope(
    status: int, code: str, message: str, extra: dict[str, Any] | None = None
) -> HTTPException:
    return HTTPException(
        status, detail={"error": {"code": code, "message": message}, **(extra or {})}
    )


def _factory() -> Any:
    svc = _service if _service is not None else _api._service
    return getattr(svc, "session_factory", None)


def _svc() -> Any:
    return _service if _service is not None else _api._service


async def _call(call: Any) -> Any:
    factory = _factory()
    if factory is None:
        raise _envelope(503, "EXECUTION_UNAVAILABLE", "no database wired")
    try:
        return await call(factory)
    except collab.CollabError as e:
        raise _envelope(e.status, e.code, str(e), getattr(e, "extra", None)) from e


# ---------------------------------------------------------------------------- guards


async def _read_dep(runId: str, p: _api.CollabReader) -> Principal:
    _api._pilot_gate(p)
    return p


async def _write_dep(runId: str, p: _api.CollabWriter) -> Principal:
    _api._pilot_gate(p)
    await _api._persist_run(runId)
    return p


async def _approve_dep(runId: str, p: _api.PlanApprover) -> Principal:
    _api._pilot_gate(p)
    await _api._persist_run(runId)
    return p


Reader = Annotated[Principal, Depends(_read_dep)]
Writer = Annotated[Principal, Depends(_write_dep)]
Approver = Annotated[Principal, Depends(_approve_dep)]


# ---------------------------------------------------------------- shared run helpers


async def _run_state(service: Any, run_id: str) -> AgentState:
    return (await service._ensure_run(run_id))["state_machine"].state


async def _abandon_row(db: Any, run_id: str) -> Any:
    return (
        await db.execute(
            text(
                "SELECT id, attempt_no, state, reason, requested_by, requested_at, preserved, "
                "request_id"
                " FROM run_attempts WHERE run_id = :r AND kind = 'abandon'"
            ),
            {"r": run_id},
        )
    ).first()


async def _completion_row(db: Any, run_id: str) -> Any:
    return (
        await db.execute(text("SELECT * FROM run_completion WHERE run_id = :r"), {"r": run_id})
    ).first()


async def _assert_open(db: Any, run_id: str, *, allow_accepted: bool = False) -> None:
    """Records of an abandoned run, or of a run whose completion a human accepted, are read-only."""
    if await _abandon_row(db, run_id) is not None:
        raise ExecError(409, CODE_ABANDONED, "this run was abandoned; its records are read-only")
    if not allow_accepted:
        row = await _completion_row(db, run_id)
        if row is not None and row.human_state == "accepted":
            raise ExecError(
                409, "COMPLETION_ACCEPTED", "completion was accepted; its records are read-only"
            )


async def _emit(db: Any, p: Principal, run_id: str, kind: str, payload: dict[str, Any]) -> None:
    """A durable collaboration event (ids only) so peers refresh."""
    await collab._event(db, p, run_id, kind, payload, None)


def _rowcount(res: Any) -> int:
    return int(res.rowcount or 0)


# ---------------------------------------------------------------- usage and limits


def _micro(v: Any) -> int:
    return int((Decimal(str(v or 0)) * 1_000_000).to_integral_value(ROUND_HALF_UP))


def executing_seconds(
    events: list[tuple[str, datetime]],
    now: datetime,
    claim: tuple[str, datetime | None] | None,
    claim_known: bool,
) -> int:
    """Wall-clock time the run spent executing, rebuilt from lifecycle events.

    A start (``execution_started``/``resumed``) opens an interval, a settle (paused,
    engine_completed, engine_failed, blocked, done_signal) closes it. An interval still open counts
    up to ``now`` only while a ``run_claims`` row says ``running`` (from the later of the start and
    the claim time): a run waiting in the queue is not burning its time budget. Without a queue
    table the open interval counts.
    """
    total, start = 0.0, None
    for typ, ts in events:
        if typ in _START_EVENTS:
            start = start or ts
        elif typ in _STOP_EVENTS and start is not None:
            total += max(0.0, (ts - start).total_seconds())
            start = None
    if start is not None:
        if not claim_known:
            total += max(0.0, (now - start).total_seconds())
        elif claim is not None and claim[0] == "running":
            total += max(0.0, (now - max(start, claim[1] or start)).total_seconds())
    return int(total)


async def usage_of(db: Any, run_id: str, now: datetime) -> dict[str, Any]:
    ev = [
        (r.type, _dt(r.ts))
        for r in (
            await db.execute(
                _q(
                    "SELECT type, ts FROM agent_events WHERE run_id = :r AND type IN :t ORDER BY "
                    "seq",
                    "t",
                ),
                {"r": run_id, "t": list(_START_EVENTS + _STOP_EVENTS)},
            )
        ).all()
    ]
    claim_known, claim = True, None
    try:
        row = (
            await db.execute(
                text("SELECT state, claimed_at FROM run_claims WHERE run_id = :r"), {"r": run_id}
            )
        ).first()
        claim = (row.state, _dt(row.claimed_at)) if row else None
    except Exception as exc:  # noqa: BLE001 - a store without the run queue has no claims
        if not any(m in str(exc).lower() for m in _MISSING_TABLE):
            raise
        claim_known = False
        await db.rollback()
    led = (
        await db.execute(
            text(
                "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS c FROM cost_ledger WHERE "
                "run_id = :r"
            ),
            {"r": run_id},
        )
    ).first()
    tool_calls: int | None
    try:
        async with db.begin_nested():  # a savepoint: a missing table must not roll back the caller
            tool_calls = int(
                (
                    await db.execute(
                        text("SELECT COUNT(*) FROM agent_tool_calls WHERE run_id=:r"), {"r": run_id}
                    )
                ).scalar_one()
            )
    except Exception as exc:  # noqa: BLE001 — pre-0044 scratch schema
        if not any(m in str(exc).lower() for m in _MISSING_TABLE):
            raise
        tool_calls = None
    return {
        "seconds": executing_seconds(ev, now, claim, claim_known),
        "modelCalls": int(led.n),
        "toolCalls": tool_calls,
        "costMicrousd": _micro(led.c),
    }


async def _limits_row(db: Any, run_id: str) -> Any:
    return (
        await db.execute(
            text(
                "SELECT max_seconds, max_model_calls, max_tool_calls, max_cost_microusd, version,"
                " updated_by, updated_at FROM run_execution_limits WHERE run_id = :r"
            ),
            {"r": run_id},
        )
    ).first()


def exceeded(limits: Any, usage: dict[str, Any]) -> list[dict[str, Any]]:
    """Enforced dimensions whose use is at or over the limit (no budget left to start more work)."""
    out = []
    for dim, col, _wire in _DIMS:
        lim, used = getattr(limits, col), usage.get(dim)
        if lim is not None and ENFORCEMENT[dim]["enforced"] and used is not None and used >= lim:
            out.append({"dimension": dim, "limit": int(lim), "used": int(used)})
    return out


def _limits_view(row: Any) -> dict[str, Any]:
    if row is None:
        return {
            **{wire: None for _d, _c, wire in _DIMS},
            "version": 0,
            "updatedBy": None,
            "updatedAt": None,
            "isDefault": True,
        }
    return {
        **{
            wire: (int(getattr(row, col)) if getattr(row, col) is not None else None)
            for _d, col, wire in _DIMS
        },
        "version": int(row.version),
        "updatedBy": row.updated_by,
        "updatedAt": _iso(row.updated_at),
        "isDefault": False,
    }


# ----------------------------------------------------------- start / claim / sweep gate


class ExecutionBlockedError(HTTPException):
    """409 envelope raised straight through any FastAPI route."""

    def __init__(self, block: dict[str, Any]) -> None:
        self.block = block
        msg = (
            "this run was abandoned"
            if block["code"] == CODE_ABANDONED
            else "execution limit reached: " + ", ".join(d["dimension"] for d in block["dims"])
        )
        super().__init__(
            409,
            detail={"error": {"code": block["code"], "message": msg}, "exceeded": block["dims"]},
        )


async def execution_block(
    factory: Any, run_id: str | None, now: datetime | None = None
) -> dict[str, Any] | None:
    """The reason a run may not (re)start: abandoned, or an enforced limit already reached.

    Pre-0048 schemas (no tables) allow; any other database error propagates (fail closed)."""
    if factory is None or not run_id:
        return None
    try:
        async with factory() as db:
            if await _abandon_row(db, run_id) is not None:
                return {"code": CODE_ABANDONED, "dims": [], "usage": None}
            limits = await _limits_row(db, run_id)
            if limits is None:
                return None
            usage = await usage_of(db, run_id, now or _now())
    except Exception as exc:  # noqa: BLE001 - only "schema not there" is tolerated
        if any(m in str(exc).lower() for m in _MISSING_TABLE):
            logger.debug("execution gate: execution schema unavailable; allowing")
            return None
        raise
    dims = exceeded(limits, usage)
    return {"code": CODE_LIMIT, "dims": dims, "usage": usage} if dims else None


async def _audit_system(factory: Any, run_id: str, action: str, detail: dict[str, Any]) -> None:
    """Audit row without a principal (the gate runs inside the worker): ids and numbers only."""
    try:
        async with factory() as db:
            team = (
                await db.execute(
                    text(
                        "SELECT p.team_id AS team_id FROM agent_runs r JOIN projects p ON p.id = "
                        "r.project_id"
                        " WHERE r.id = :r"
                    ),
                    {"r": run_id},
                )
            ).first()
            await db.execute(
                text(
                    "INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, "
                    "target_id,"
                    " detail, ts) VALUES (NULL, 'human', :a, 'agent_run', :i, :d, :ts)"
                ),
                {
                    "a": action,
                    "i": run_id,
                    "ts": _now(),
                    "d": json.dumps(
                        {"runId": run_id, "teamId": team.team_id if team else None, **detail},
                        default=str,
                    ),
                },
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - audit must never mask the refusal
        logger.exception("%s audit failed (run=%s)", action, run_id)


async def check_execution_start(
    service: Any, run_id: str, *, where: str = "start"
) -> dict[str, Any] | None:
    """Return the block (and audit it) when the run may not start.

    At queue claim a limit block also moves the run to BLOCKED with the reason
    (``execution_failed``), so it is resumable once the limit is raised."""
    factory = getattr(service, "session_factory", None)
    block = await execution_block(factory, run_id)
    if block is None:
        return None
    action = "execution.limit_exceeded" if block["code"] == CODE_LIMIT else "run.retry_refused"
    await _audit_system(
        factory, run_id, action, {"where": where, "code": block["code"], "exceeded": block["dims"]}
    )
    sm = ((getattr(service, "runs", {}) or {}).get(run_id) or {}).get("state_machine")
    if (
        where == "claim"
        and block["code"] == CODE_LIMIT
        and sm is not None
        and sm.state is AgentState.EXECUTING
    ):
        sm.execution_failed(
            "execution limit reached: " + ", ".join(d["dimension"] for d in block["dims"])
        )
    return block


async def enforce_execution_start(service: Any, run_id: str) -> None:
    """Raise :class:`ExecutionBlockedError` (409) when the run is abandoned or out of budget."""
    block = await check_execution_start(service, run_id, where="start")
    if block is not None:
        raise ExecutionBlockedError(block)


async def sweep_running_limits(service: Any) -> list[str]:
    """Pause runs that are running (live claim) and have reached an enforced limit.

    Returns the paused run ids.

    Not scheduled by this module: call it from a worker loop. Pause keeps the run resumable once a
    human raises the limit."""
    factory = getattr(service, "session_factory", None)
    if factory is None:
        return []
    try:
        async with factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT l.run_id AS run_id FROM run_execution_limits l JOIN run_claims c"
                        " ON c.run_id = l.run_id WHERE c.state = 'running'"
                    )
                )
            ).all()
    except Exception as exc:  # noqa: BLE001
        if any(m in str(exc).lower() for m in _MISSING_TABLE):
            return []
        raise
    paused: list[str] = []
    for r in rows:
        block = await execution_block(factory, r.run_id)
        if block is None or block["code"] != CODE_LIMIT:
            continue
        await _audit_system(
            factory,
            r.run_id,
            "execution.limit_exceeded",
            {"where": "running", "code": CODE_LIMIT, "exceeded": block["dims"], "action": "paused"},
        )
        try:
            await service.pause_agent(r.run_id)
        except Exception:  # noqa: BLE001 - one bad run must not stop the sweep
            logger.exception("limit sweep could not pause run %s", r.run_id)
            continue
        paused.append(r.run_id)
    return paused


# ------------------------------------------------------------------------ limits API


async def limits_get(factory: Any, run_id: str) -> dict[str, Any]:
    async with factory() as db:
        return {
            "runId": run_id,
            "limits": _limits_view(await _limits_row(db, run_id)),
            "bounds": {k: {"min": a, "max": b} for k, (a, b) in BOUNDS.items()},
            "enforcement": ENFORCEMENT,
        }


async def limits_put(factory: Any, p: Principal, run_id: str, req: "LimitsBody") -> dict[str, Any]:
    async with factory() as db:
        await _assert_open(db, run_id)
        before = await _limits_row(db, run_id)
        new = {col: getattr(req, wire) for _d, col, wire in _DIMS}
        cur_version = int(before.version) if before else 0
        if (req.expectedVersion or 0) != cur_version:
            raise ExecError(
                409,
                "STALE_VERSION",
                "limits changed since you read them",
                {"currentVersion": cur_version},
            )
        if before is not None and all(getattr(before, c) == v for c, v in new.items()):
            return await limits_get(factory, run_id)  # no-op: no version bump, no audit noise
        now = _now()
        args = {"r": run_id, "t": p.teamId, "u": p.userId, "now": now, **new}
        if before is None:
            try:
                await db.execute(
                    text(
                        "INSERT INTO run_execution_limits (run_id, team_id, max_seconds, "
                        "max_model_calls,"
                        " max_tool_calls, max_cost_microusd, version, updated_by, updated_at) "
                        "VALUES"
                        " (:r, :t, :max_seconds, :max_model_calls, :max_tool_calls, "
                        ":max_cost_microusd, 1,"
                        " :u, :now)"
                    ),
                    args,
                )
            except IntegrityError as e:  # two first writers: the loser is stale
                await db.rollback()
                raise ExecError(409, "STALE_VERSION", "limits changed since you read them") from e
        else:
            res = await db.execute(
                text(
                    "UPDATE run_execution_limits SET max_seconds = :max_seconds, max_model_calls "
                    "= :max_model_calls,"
                    " max_tool_calls = :max_tool_calls, max_cost_microusd = :max_cost_microusd,"
                    " version = version + 1, updated_by = :u, updated_at = :now"
                    " WHERE run_id = :r AND version = :v"
                ),
                {**args, "v": cur_version},
            )
            if _rowcount(res) == 0:
                await db.rollback()
                raise ExecError(409, "STALE_VERSION", "limits changed since you read them")
        await collab._audit(
            db,
            p,
            "execution.limits_updated",
            run_id,
            {
                "runId": run_id,
                "before": _limits_view(before),
                "after": {wire: new[col] for _d, col, wire in _DIMS},
                "version": cur_version + 1,
            },
        )
        await _emit(db, p, run_id, "execution.limits_updated", {"version": cur_version + 1})
        await db.commit()
    return await limits_get(factory, run_id)


# --------------------------------------------------------------------------- subtasks


def _check_text(value: str, what: str, maximum: int, code: str) -> str:
    v = value.strip()
    if not v or len(v) > maximum:
        raise ExecError(422, code, f"{what} must be 1..{maximum} characters")
    return v


def _would_cycle(edges: dict[str, set[str]], node: str, new_deps: list[str]) -> bool:
    """True when ``node`` is reachable from one of its proposed dependencies (or is one of them)."""
    seen: set[str] = set()
    stack = list(new_deps)
    while stack:
        d = stack.pop()
        if d == node:
            return True
        if d not in seen:
            seen.add(d)
            stack.extend(edges.get(d, ()))
    return False


async def _edges(db: Any, run_id: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in (
        await db.execute(
            text("SELECT subtask_id, depends_on_id FROM run_subtask_deps WHERE run_id = :r"),
            {"r": run_id},
        )
    ).all():
        out.setdefault(r.subtask_id, set()).add(r.depends_on_id)
    return out


async def _check_deps(db: Any, run_id: str, deps: list[str]) -> list[str]:
    """Dependencies must be subtasks of this run. Unknown and foreign ids: the same 422."""
    deps = list(dict.fromkeys(deps))
    if len(deps) > MAX_DEPS:
        raise ExecError(422, "TOO_MANY_DEPENDENCIES", f"at most {MAX_DEPS} dependencies")
    if deps:
        found = {
            r.id
            for r in (
                await db.execute(
                    _q("SELECT id FROM run_subtasks WHERE run_id = :r AND id IN :ids", "ids"),
                    {"r": run_id, "ids": deps},
                )
            ).all()
        }
        if found != set(deps):
            raise ExecError(422, "BAD_DEPENDENCY", "a dependency is not a subtask of this run")
    return deps


_SUBTASK_COLS = (
    "id, title, description, state, state_before_block, position, version, created_by,"
    " created_at, updated_by, updated_at, completed_by, completed_at"
)


async def _subtasks(db: Any, run_id: str, only: str | None = None) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                f"SELECT {_SUBTASK_COLS} FROM run_subtasks WHERE run_id = :r ORDER BY position, id"
            ),
            {"r": run_id},
        )
    ).all()
    deps: dict[str, list[str]] = {}
    for r in (
        await db.execute(
            text(
                "SELECT subtask_id, depends_on_id FROM run_subtask_deps WHERE run_id = :r"
                " ORDER BY depends_on_id"
            ),
            {"r": run_id},
        )
    ).all():
        deps.setdefault(r.subtask_id, []).append(r.depends_on_id)
    blockers: dict[str, list[dict[str, Any]]] = {}
    for r in (
        await db.execute(
            text(
                "SELECT id, subtask_id, reason, created_by, created_at FROM run_subtask_blockers"
                " WHERE run_id = :r AND state = 'open' ORDER BY created_at, id"
            ),
            {"r": run_id},
        )
    ).all():
        blockers.setdefault(r.subtask_id, []).append(
            {
                "id": r.id,
                "reason": r.reason,
                "createdBy": r.created_by,
                "createdAt": _iso(r.created_at),
            }
        )
    evidence: dict[str, list[dict[str, Any]]] = {}
    for r in (
        await db.execute(
            text(
                "SELECT id, subtask_id, kind, ref, note, added_by, added_at FROM "
                "run_subtask_evidence"
                " WHERE run_id = :r ORDER BY added_at, id"
            ),
            {"r": run_id},
        )
    ).all():
        evidence.setdefault(r.subtask_id, []).append(
            {
                "id": r.id,
                "kind": r.kind,
                "ref": r.ref,
                "note": r.note,
                "addedBy": r.added_by,
                "addedAt": _iso(r.added_at),
            }
        )
    states = {r.id: r.state for r in rows}
    out = []
    for r in rows:
        if only is not None and r.id != only:
            continue
        mine = deps.get(r.id, [])
        unmet = [d for d in mine if states.get(d) != "done"]
        out.append(
            {
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "state": r.state,
                "position": r.position,
                "version": r.version,
                "dependsOn": mine,
                "unmetDependencies": unmet,
                "blockers": blockers.get(r.id, []),
                "evidenceCount": len(evidence.get(r.id, [])),
                "evidence": evidence.get(r.id, []),
                "ready": r.state in ("todo", "in_progress") and not unmet and r.id not in blockers,
                "createdBy": r.created_by,
                "createdAt": _iso(r.created_at),
                "updatedBy": r.updated_by,
                "updatedAt": _iso(r.updated_at),
                "completedBy": r.completed_by,
                "completedAt": _iso(r.completed_at),
            }
        )
    return out


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by = {s: 0 for s in SUBTASK_STATES}
    for i in items:
        by[i["state"]] += 1
    return {
        "total": len(items),
        "byState": by,
        "openBlockers": sum(len(i["blockers"]) for i in items),
        "incomplete": [i["id"] for i in items if i["state"] not in ("done", "cancelled")],
        "evidenceRefs": sum(i["evidenceCount"] for i in items),
    }


async def subtasks_list(factory: Any, run_id: str) -> dict[str, Any]:
    async with factory() as db:
        items = await _subtasks(db, run_id)
    return {
        "runId": run_id,
        "subtasks": items,
        "summary": _summary(items),
        "limits": {
            "maxSubtasks": MAX_SUBTASKS,
            "maxDependencies": MAX_DEPS,
            "maxEvidencePerSubtask": MAX_EVIDENCE,
            "maxOpenBlockers": MAX_OPEN_BLOCKERS,
        },
    }


async def _load(db: Any, run_id: str, sid: str) -> Any:
    row = (
        await db.execute(
            text(f"SELECT {_SUBTASK_COLS} FROM run_subtasks WHERE id = :i AND run_id = :r"),
            {"i": sid, "r": run_id},
        )
    ).first()
    if row is None:
        raise ExecError(404, "SUBTASK_NOT_FOUND", "no such subtask")
    return row


async def _one(db: Any, run_id: str, sid: str) -> dict[str, Any]:
    return (await _subtasks(db, run_id, only=sid))[0]


def _stale(row: Any) -> ExecError:
    return ExecError(
        409,
        "STALE_VERSION",
        "the subtask changed since you read it",
        {"currentVersion": int(row.version)},
    )


async def subtask_create(
    factory: Any, p: Principal, run_id: str, req: "SubtaskCreate"
) -> tuple[dict, bool]:
    title = _check_text(req.title, "title", TITLE_MAX, "BAD_TITLE")
    async with factory() as db:
        await _assert_open(db, run_id)

        async def existing() -> Any:
            return (
                await db.execute(
                    text(
                        "SELECT id FROM run_subtasks WHERE run_id = :r AND created_by = :u AND "
                        "request_id = :q"
                    ),
                    {"r": run_id, "u": p.userId, "q": req.requestId},
                )
            ).first()

        if req.requestId and (dup := await existing()) is not None:
            return await _one(db, run_id, dup.id), False
        count = (
            await db.execute(
                text(
                    "SELECT COUNT(*) AS n, COALESCE(MAX(position), 0) AS m"
                    " FROM run_subtasks WHERE run_id = :r"
                ),
                {"r": run_id},
            )
        ).first()
        if count.n >= MAX_SUBTASKS:
            raise ExecError(409, "SUBTASK_LIMIT", f"a run holds at most {MAX_SUBTASKS} subtasks")
        deps = await _check_deps(db, run_id, req.dependsOn)
        sid, now = str(uuid.uuid4()), _now()
        try:
            await db.execute(
                text(
                    "INSERT INTO run_subtasks (id, run_id, team_id, title, description, state, "
                    "position,"
                    " request_id, version, created_by, created_at, updated_by, updated_at) VALUES"
                    " (:i, :r, :t, :ti, :d, 'todo', :pos, :q, 1, :u, :now, :u, :now)"
                ),
                {
                    "i": sid,
                    "r": run_id,
                    "t": p.teamId,
                    "ti": title,
                    "d": req.description.strip(),
                    "pos": count.m + 1,
                    "q": req.requestId,
                    "u": p.userId,
                    "now": now,
                },
            )
        except IntegrityError:  # a simultaneous retry with the same requestId
            await db.rollback()
            if req.requestId and (dup := await existing()) is not None:
                return await _one(db, run_id, dup.id), False
            raise
        for d in deps:
            await db.execute(
                text(
                    "INSERT INTO run_subtask_deps (subtask_id, depends_on_id, run_id)"
                    " VALUES (:s, :d, :r)"
                ),
                {"s": sid, "d": d, "r": run_id},
            )
        await collab._audit(
            db,
            p,
            "subtask.created",
            run_id,
            {"runId": run_id, "subtaskId": sid, "dependsOn": deps, "after": {"state": "todo"}},
        )
        await _emit(db, p, run_id, "subtask.created", {"subtaskId": sid})
        await db.commit()
        return await _one(db, run_id, sid), True


async def subtask_patch(
    factory: Any, p: Principal, run_id: str, sid: str, req: "SubtaskPatch"
) -> dict:
    async with factory() as db:
        await _assert_open(db, run_id)
        row = await _load(db, run_id, sid)
        if row.version != req.expectedVersion:
            raise _stale(row)
        if row.state == "done":
            raise ExecError(409, "SUBTASK_DONE", "a completed subtask cannot be edited")
        sets: dict[str, Any] = {}
        if req.title is not None:
            title = _check_text(req.title, "title", TITLE_MAX, "BAD_TITLE")
            if title != row.title:
                sets["title"] = title
        if req.description is not None and req.description.strip() != row.description:
            sets["description"] = req.description.strip()
        deps = None
        if req.dependsOn is not None:
            deps = await _check_deps(db, run_id, req.dependsOn)
            edges = await _edges(db, run_id)
            edges.pop(sid, None)
            if _would_cycle(edges, sid, deps):
                raise ExecError(409, "DEPENDENCY_CYCLE", "that dependency would make a cycle")
        if req.state is not None and req.state != row.state:
            if row.state == "blocked":
                raise ExecError(409, "SUBTASK_BLOCKED", "resolve the blockers first")
            if req.state == "in_progress":
                unmet = await _unmet(db, run_id, sid, deps)
                if unmet:
                    raise ExecError(
                        409,
                        "DEPENDENCIES_INCOMPLETE",
                        "dependencies are not done",
                        {"unmetDependencies": unmet},
                    )
            sets["state"] = req.state
        if not sets and deps is None:
            return await _one(db, run_id, sid)  # no-op: no version bump, no audit noise
        before = {"state": row.state, "version": row.version}
        sets.update(updated_by=p.userId, updated_at=_now())
        assign = ", ".join(f"{k} = :{k}" for k in sets)
        res = await db.execute(
            text(
                f"UPDATE run_subtasks SET {assign}, version = version + 1"
                " WHERE id = :i AND version = :v"
            ),
            {**sets, "i": sid, "v": row.version},
        )
        if _rowcount(res) == 0:
            await db.rollback()
            raise _stale(row)
        if deps is not None:
            await db.execute(text("DELETE FROM run_subtask_deps WHERE subtask_id = :s"), {"s": sid})
            for d in deps:
                await db.execute(
                    text(
                        "INSERT INTO run_subtask_deps (subtask_id, depends_on_id, run_id)"
                        " VALUES (:s, :d, :r)"
                    ),
                    {"s": sid, "d": d, "r": run_id},
                )
        await collab._audit(
            db,
            p,
            "subtask.updated",
            run_id,
            {
                "runId": run_id,
                "subtaskId": sid,
                "before": before,
                "after": {"state": sets.get("state", row.state), "version": row.version + 1},
                "fields": sorted(k for k in sets if k not in ("updated_by", "updated_at"))
                + (["dependsOn"] if deps is not None else []),
            },
        )
        await _emit(db, p, run_id, "subtask.updated", {"subtaskId": sid})
        await db.commit()
        return await _one(db, run_id, sid)


async def _unmet(db: Any, run_id: str, sid: str, deps: list[str] | None = None) -> list[str]:
    if deps is None:
        deps = sorted((await _edges(db, run_id)).get(sid, set()))
    if not deps:
        return []
    done = {
        r.id
        for r in (
            await db.execute(
                _q(
                    "SELECT id FROM run_subtasks WHERE run_id = :r AND state = 'done' AND id IN "
                    ":ids",
                    "ids",
                ),
                {"r": run_id, "ids": deps},
            )
        ).all()
    }
    return [d for d in deps if d not in done]


async def _open_blockers(db: Any, sid: str) -> list[Any]:
    return (
        await db.execute(
            text(
                "SELECT id, reason FROM run_subtask_blockers WHERE subtask_id = :s"
                " AND state = 'open' ORDER BY created_at, id"
            ),
            {"s": sid},
        )
    ).all()


async def subtask_block(
    factory: Any, p: Principal, run_id: str, sid: str, req: "BlockBody"
) -> tuple[dict, bool]:
    reason = _check_text(req.reason, "reason", REASON_MAX, "BAD_REASON")
    async with factory() as db:
        await _assert_open(db, run_id)
        row = await _load(db, run_id, sid)
        if row.state in ("done", "cancelled"):
            raise ExecError(409, "SUBTASK_CLOSED", f"a {row.state} subtask cannot be blocked")
        open_ = await _open_blockers(db, sid)
        if any(b.reason == reason for b in open_):
            return await _one(db, run_id, sid), False  # the same open blocker: a retried POST
        if len(open_) >= MAX_OPEN_BLOCKERS:
            raise ExecError(409, "BLOCKER_LIMIT", f"at most {MAX_OPEN_BLOCKERS} open blockers")
        bid, now = str(uuid.uuid4()), _now()
        try:
            await db.execute(
                text(
                    "INSERT INTO run_subtask_blockers (id, subtask_id, run_id, team_id, reason, "
                    "state, created_by, created_at) VALUES (:i, :s, :r, :t, :re, 'open', :u, :now)"
                ),
                {
                    "i": bid,
                    "s": sid,
                    "r": run_id,
                    "t": p.teamId,
                    "re": reason,
                    "u": p.userId,
                    "now": now,
                },
            )
        except IntegrityError:  # a simultaneous identical POST: the first blocker stands
            await db.rollback()
            return await _one(db, run_id, sid), False
        before = {"state": row.state}
        await db.execute(
            text(
                "UPDATE run_subtasks SET state = 'blocked', state_before_block = CASE WHEN state "
                "= 'blocked'"
                " THEN state_before_block ELSE state END, version = version + 1, updated_by = :u,"
                " updated_at = :now WHERE id = :i"
            ),
            {"u": p.userId, "now": now, "i": sid},
        )
        await collab._audit(
            db,
            p,
            "subtask.blocked",
            run_id,
            {
                "runId": run_id,
                "subtaskId": sid,
                "blockerId": bid,
                "before": before,
                "after": {"state": "blocked"},
            },
        )
        await _emit(db, p, run_id, "subtask.blocked", {"subtaskId": sid, "blockerId": bid})
        await db.commit()
        return await _one(db, run_id, sid), True


async def subtask_unblock(
    factory: Any, p: Principal, run_id: str, sid: str, req: "UnblockBody"
) -> tuple[dict, bool]:
    async with factory() as db:
        await _assert_open(db, run_id)
        row = await _load(db, run_id, sid)
        open_ = await _open_blockers(db, sid)
        if req.blockerId is not None:
            known = (
                await db.execute(
                    text(
                        "SELECT state FROM run_subtask_blockers WHERE id = :b AND subtask_id = :s"
                    ),
                    {"b": req.blockerId, "s": sid},
                )
            ).first()
            if known is None:
                raise ExecError(404, "BLOCKER_NOT_FOUND", "no such blocker")
            targets = [b.id for b in open_ if b.id == req.blockerId]
        else:
            targets = [b.id for b in open_]
        if not targets:
            return await _one(db, run_id, sid), False  # already resolved: a retried POST
        now = _now()
        for b in targets:
            await db.execute(
                text(
                    "UPDATE run_subtask_blockers SET state = 'resolved', resolved_by = :u, "
                    "resolved_at = :now,"
                    " resolution = :re WHERE id = :b AND state = 'open'"
                ),
                {
                    "u": p.userId,
                    "now": now,
                    "re": (req.resolution or "").strip()[:REASON_MAX] or None,
                    "b": b,
                },
            )
        still = len(open_) - len(targets)
        after = "blocked" if still else (row.state_before_block or "todo")
        if not still:
            await db.execute(
                text(
                    "UPDATE run_subtasks SET state = :s, state_before_block = NULL,"
                    " version = version + 1, updated_by = :u, updated_at = :now WHERE id = :i"
                ),
                {"s": after, "u": p.userId, "now": now, "i": sid},
            )
        await collab._audit(
            db,
            p,
            "subtask.unblocked",
            run_id,
            {
                "runId": run_id,
                "subtaskId": sid,
                "blockerIds": targets,
                "before": {"state": row.state},
                "after": {"state": after},
            },
        )
        await _emit(db, p, run_id, "subtask.unblocked", {"subtaskId": sid})
        await db.commit()
        return await _one(db, run_id, sid), True


async def subtask_complete(
    factory: Any, p: Principal, run_id: str, sid: str, req: "CompleteBody"
) -> tuple[dict, bool]:
    async with factory() as db:
        await _assert_open(db, run_id)
        row = await _load(db, run_id, sid)
        if row.state == "done":
            return await _one(db, run_id, sid), False  # idempotent
        if row.version != req.expectedVersion:
            raise _stale(row)
        if row.state == "cancelled":
            raise ExecError(409, "SUBTASK_CLOSED", "a cancelled subtask cannot be completed")
        if await _open_blockers(db, sid):
            raise ExecError(409, "SUBTASK_BLOCKED", "resolve the blockers first")
        unmet = await _unmet(db, run_id, sid)
        if unmet:
            raise ExecError(
                409,
                "DEPENDENCIES_INCOMPLETE",
                "dependencies are not done",
                {"unmetDependencies": unmet},
            )
        n = (
            (
                await db.execute(
                    text("SELECT COUNT(*) AS n FROM run_subtask_evidence WHERE subtask_id = :s"),
                    {"s": sid},
                )
            )
            .first()
            .n
        )
        if n == 0:
            raise ExecError(409, "EVIDENCE_REQUIRED", "add completion evidence before completing")
        now = _now()
        res = await db.execute(
            text(
                "UPDATE run_subtasks SET state = 'done', state_before_block = NULL, completed_by "
                "= :u,"
                " completed_at = :now, version = version + 1, updated_by = :u, updated_at = :now"
                " WHERE id = :i AND version = :v"
            ),
            {"u": p.userId, "now": now, "i": sid, "v": row.version},
        )
        if _rowcount(res) == 0:
            await db.rollback()
            raise _stale(row)
        await collab._audit(
            db,
            p,
            "subtask.completed",
            run_id,
            {
                "runId": run_id,
                "subtaskId": sid,
                "evidenceCount": int(n),
                "before": {"state": row.state},
                "after": {"state": "done"},
            },
        )
        await _emit(db, p, run_id, "subtask.completed", {"subtaskId": sid})
        await db.commit()
        return await _one(db, run_id, sid), True


_LINK = re.compile(r"https?://\S+")


async def subtask_evidence(
    factory: Any, p: Principal, run_id: str, sid: str, req: "EvidenceBody"
) -> tuple[dict, bool]:
    ref = _check_text(req.ref, "ref", REF_MAX, "BAD_REF")
    if req.kind == "file":
        collab.validate_paths([ref])
    elif req.kind == "link" and not _LINK.fullmatch(ref):
        raise ExecError(422, "BAD_REF", "a link must be an http(s) URL")
    async with factory() as db:
        await _assert_open(db, run_id)
        await _load(db, run_id, sid)
        dup = (
            await db.execute(
                text(
                    "SELECT 1 FROM run_subtask_evidence WHERE subtask_id = :s AND kind = :k"
                    " AND ref = :ref"
                ),
                {"s": sid, "k": req.kind, "ref": ref},
            )
        ).first()
        if dup is not None:
            return await _one(db, run_id, sid), False
        n = (
            (
                await db.execute(
                    text("SELECT COUNT(*) AS n FROM run_subtask_evidence WHERE subtask_id = :s"),
                    {"s": sid},
                )
            )
            .first()
            .n
        )
        if n >= MAX_EVIDENCE:
            raise ExecError(409, "EVIDENCE_LIMIT", f"at most {MAX_EVIDENCE} evidence references")
        eid = str(uuid.uuid4())
        try:
            await db.execute(
                text(
                    "INSERT INTO run_subtask_evidence (id, subtask_id, run_id, team_id, kind, "
                    "ref, note, added_by,"
                    " added_at) VALUES (:i, :s, :r, :t, :k, :ref, :n, :u, :now)"
                ),
                {
                    "i": eid,
                    "s": sid,
                    "r": run_id,
                    "t": p.teamId,
                    "k": req.kind,
                    "ref": ref,
                    "n": (req.note or "").strip(),
                    "u": p.userId,
                    "now": _now(),
                },
            )
        except IntegrityError:  # a simultaneous identical POST
            await db.rollback()
            return await _one(db, run_id, sid), False
        await collab._audit(
            db,
            p,
            "subtask.evidence_added",
            run_id,
            {"runId": run_id, "subtaskId": sid, "evidenceId": eid, "kind": req.kind},
        )
        await _emit(db, p, run_id, "subtask.evidence_added", {"subtaskId": sid, "evidenceId": eid})
        await db.commit()
        return await _one(db, run_id, sid), True


# ------------------------------------------------------------------------- completion


def _digest(summary: str, outputs: list[dict[str, Any]]) -> str:
    body = json.dumps(
        {"s": summary, "o": sorted(outputs, key=lambda o: (o["kind"], o["ref"]))},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


async def _history(db: Any, run_id: str) -> list[dict[str, Any]]:
    return [
        {
            "reportNo": r.report_no,
            "event": r.event,
            "by": r.actor_user_id,
            "digest": r.digest,
            "reason": r.reason,
            "at": _iso(r.at),
        }
        for r in (
            await db.execute(
                text(
                    "SELECT report_no, event, actor_user_id, digest, reason, at FROM "
                    "run_completion_history"
                    " WHERE run_id = :r ORDER BY report_no,"
                    " CASE event WHEN 'reported' THEN 0 ELSE 1 END, at"
                ),
                {"r": run_id},
            )
        ).all()
    ]


async def _accept_blockers(db: Any, service: Any, run_id: str) -> list[dict[str, Any]]:
    """Why a human cannot accept right now (independent of who asks)."""
    out = []
    if await _run_state(service, run_id) is AgentState.EXECUTING:
        out.append({"code": "RUN_EXECUTING", "message": "the engine can still change the outputs"})
    inc = _summary(await _subtasks(db, run_id))["incomplete"]
    if inc:
        out.append(
            {
                "code": "SUBTASKS_INCOMPLETE",
                "message": "subtasks are neither done nor cancelled",
                "subtaskIds": inc,
            }
        )
    return out


async def completion_get(factory: Any, service: Any, p: Principal, run_id: str) -> dict[str, Any]:
    async with factory() as db:
        row = await _completion_row(db, run_id)
        items = await _subtasks(db, run_id)
        blocking = await _accept_blockers(db, service, run_id)
        state = await _run_state(service, run_id)
        abandoned = await _abandon_row(db, run_id)
        history = await _history(db, run_id)
    reported = row is not None and row.agent_state == "reported"
    human = row.human_state if row else "none"
    if not reported:
        blocking = [
            {"code": "NO_REPORT", "message": "the agent has not reported completion"},
            *blocking,
        ]
    if human != "none":
        blocking = [{"code": "ALREADY_DECIDED", "message": f"already {human}"}, *blocking]
    if abandoned is not None:
        blocking = [{"code": CODE_ABANDONED, "message": "the run was abandoned"}, *blocking]
    approver = p.role in CAN_APPROVE_PLANS
    can = reported and approver and not blocking
    return {
        "runId": run_id,
        "agentReported": {
            "state": row.agent_state if row else "none",
            "reportNo": int(row.report_no) if row else 0,
            "summary": row.summary if reported else None,
            "outputs": json.loads(row.outputs) if reported else [],
            "digest": row.report_digest if reported else None,
            "reportedBy": row.reported_by if reported else None,
            "reportedAt": _iso(row.reported_at) if reported else None,
        },
        "humanDecision": {
            "state": human,
            "decidedBy": row.decided_by if row else None,
            "decidedAt": _iso(row.decided_at) if row else None,
            "reason": row.decision_reason if row else None,
            "digest": row.decided_digest if row else None,
        },
        "accepted": human == "accepted",
        "review": {
            "runState": state.value,
            "subtasks": _summary(items),
            "blocking": blocking,
            "canAccept": bool(can and p.userId != (row.reported_by if row else None)),
            "canReject": bool(reported and approver and human == "none" and abandoned is None),
            "note": "agent-reported completion is not accepted completion: only a human with the"
            " approve permission who is not the reporter can accept",
        },
        "history": history,
    }


async def completion_report(
    factory: Any, service: Any, p: Principal, run_id: str, req: "ReportBody"
) -> tuple[dict, bool]:
    summary = _check_text(req.summary, "summary", SUMMARY_MAX, "BAD_SUMMARY")
    outputs = [
        {
            "kind": o.kind,
            "ref": _check_text(o.ref, "ref", REF_MAX, "BAD_REF"),
            "note": (o.note or "").strip(),
        }
        for o in req.outputs
    ]
    for o in outputs:
        if o["kind"] == "file":
            collab.validate_paths([o["ref"]])
    digest = _digest(summary, outputs)
    async with factory() as db:
        await _assert_open(db, run_id)

        async def existing() -> Any:
            return (
                await db.execute(
                    text(
                        "SELECT 1 FROM run_completion_history WHERE run_id = :r AND actor_user_id "
                        "= :u"
                        " AND request_id = :q"
                    ),
                    {"r": run_id, "u": p.userId, "q": req.requestId},
                )
            ).first()

        if req.requestId and await existing() is not None:
            return await completion_get(factory, service, p, run_id), False
        row = await _completion_row(db, run_id)
        old = int(row.report_no) if row else 0
        now = _now()
        args = {
            "r": run_id,
            "t": p.teamId,
            "s": summary,
            "o": json.dumps(outputs),
            "g": digest,
            "u": p.userId,
            "now": now,
            "n": old + 1,
        }
        conflict = ExecError(409, "REPORT_CONFLICT", "another report was written at the same time")
        try:
            if row is None:
                await db.execute(
                    text(
                        "INSERT INTO run_completion (run_id, team_id, agent_state, report_no, "
                        "summary, outputs,"
                        " report_digest, reported_by, reported_at, human_state, version) VALUES "
                        "(:r, :t,"
                        " 'reported', :n, :s, :o, :g, :u, :now, 'none', 1)"
                    ),
                    args,
                )
            else:
                res = await db.execute(
                    text(
                        "UPDATE run_completion SET agent_state = 'reported', report_no = :n, "
                        "summary = :s,"
                        " outputs = :o, report_digest = :g, reported_by = :u, reported_at = :now,"
                        " human_state = 'none', decided_by = NULL, decided_at = NULL, "
                        "decision_reason = NULL,"
                        " decided_digest = NULL, version = version + 1 WHERE run_id = :r AND "
                        "report_no = :old"
                    ),
                    {**args, "old": old},
                )
                if _rowcount(res) == 0:
                    await db.rollback()
                    raise conflict
            await db.execute(
                text(
                    "INSERT INTO run_completion_history (id, run_id, team_id, report_no, event, "
                    "actor_user_id,"
                    " digest, request_id, at) VALUES (:i, :r, :t, :n, 'reported', :u, :g, :q, :now)"
                ),
                {**args, "i": str(uuid.uuid4()), "q": req.requestId},
            )
        except IntegrityError as e:  # a simultaneous retry, or a simultaneous first report
            await db.rollback()
            if req.requestId and await existing() is not None:
                return await completion_get(factory, service, p, run_id), False
            raise conflict from e
        await collab._audit(
            db,
            p,
            "completion.reported",
            run_id,
            {
                "runId": run_id,
                "reportNo": old + 1,
                "digest": digest,
                "outputCount": len(outputs),
                "after": {"agentState": "reported", "humanState": "none"},
            },
        )
        await _emit(db, p, run_id, "completion.reported", {"reportNo": old + 1})
        await db.commit()
    return await completion_get(factory, service, p, run_id), True


async def completion_decide(
    factory: Any, service: Any, p: Principal, run_id: str, accept: bool, req: "DecideBody"
) -> tuple[dict, bool]:
    word = "accepted" if accept else "rejected"
    async with factory() as db:
        await _assert_open(db, run_id, allow_accepted=True)
        row = await _completion_row(db, run_id)
        if row is None or row.agent_state != "reported":
            raise ExecError(409, "NO_REPORT", "the agent has not reported completion")
        if row.human_state == word and row.decided_digest == req.reportDigest:
            return await completion_get(factory, service, p, run_id), False  # idempotent repeat
        if row.human_state != "none":
            raise ExecError(409, "ALREADY_DECIDED", f"this report was already {row.human_state}")
        if row.report_digest != req.reportDigest:
            raise ExecError(
                409,
                "STALE_REPORT",
                "the report changed since you reviewed it",
                {"currentDigest": row.report_digest, "reportNo": int(row.report_no)},
            )
        if accept and row.reported_by == p.userId:
            await collab.audit_denial(
                factory, p, run_id, "POST completion/accept", "SELF_ACCEPT_FORBIDDEN", 403
            )
            raise ExecError(
                403, "SELF_ACCEPT_FORBIDDEN", "the reporter cannot accept their own report"
            )
        if accept:
            blocking = await _accept_blockers(db, service, run_id)
            if blocking:
                raise ExecError(
                    409, blocking[0]["code"], blocking[0]["message"], {"blocking": blocking}
                )
        res = await db.execute(
            text(
                "UPDATE run_completion SET human_state = :w, decided_by = :u, decided_at = :now,"
                " decision_reason = :re, decided_digest = :g, version = version + 1"
                " WHERE run_id = :r AND human_state = 'none' AND report_digest = :g"
            ),
            {
                "w": word,
                "u": p.userId,
                "now": _now(),
                "re": (req.reason or None),
                "g": req.reportDigest,
                "r": run_id,
            },
        )
        if _rowcount(res) == 0:
            await db.rollback()
            raise ExecError(409, "ALREADY_DECIDED", "this report was decided at the same time")
        await db.execute(
            text(
                "INSERT INTO run_completion_history (id, run_id, team_id, report_no, event, "
                "actor_user_id,"
                " digest, reason, at) VALUES (:i, :r, :t, :n, :w, :u, :g, :re, :now)"
            ),
            {
                "i": str(uuid.uuid4()),
                "r": run_id,
                "t": p.teamId,
                "n": row.report_no,
                "w": word,
                "u": p.userId,
                "g": req.reportDigest,
                "re": req.reason,
                "now": _now(),
            },
        )
        await collab._audit(
            db,
            p,
            f"completion.{word}",
            run_id,
            {
                "runId": run_id,
                "reportNo": int(row.report_no),
                "digest": req.reportDigest,
                "reporter": row.reported_by,
                "after": {"humanState": word},
            },
        )
        await _emit(db, p, run_id, f"completion.{word}", {"reportNo": int(row.report_no)})
        await db.commit()
    return await completion_get(factory, service, p, run_id), True


# ------------------------------------------------- checkpoints, safe resume, status


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


async def _events(db: Any, run_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            _q(
                "SELECT seq, type, ts, payload FROM agent_events WHERE run_id = :r AND type IN :t"
                " ORDER BY seq DESC LIMIT :n",
                "t",
            ),
            {"r": run_id, "t": list(_CHECKPOINT_EVENTS), "n": limit},
        )
    ).all()
    out = []
    for r in reversed(rows):
        pl = _payload(r.payload)
        hint = pl.get("checkpoint")
        out.append(
            {
                "seq": int(r.seq),
                "type": r.type,
                "at": _iso(r.ts),
                "reason": str(pl["reason"])[:200] if pl.get("reason") else None,
                "checkpoint": (
                    {"kind": str(hint.get("kind"))[:40], "source": str(hint.get("source"))[:40]}
                    if isinstance(hint, dict)
                    else None
                ),
            }
        )
    return out


async def assess_resume(
    service: Any, db: Any, run_id: str, now: datetime, *, own_attempt: str | None = None
) -> dict[str, Any]:
    """Is resuming this run safe right now? One answer for ``/execution-status`` and ``/retry``.

    Reads only durable evidence (event log, run_claims, approvals, attempts) plus this process's
    engine liveness. It cannot see inside a foreign worker that is still winding down: the claim
    row and the heartbeat reaper cover that case."""
    state = await _run_state(service, run_id)
    reasons: list[dict[str, Any]] = []

    def add(code: str, **detail: Any) -> None:
        if all(r["code"] != code for r in reasons):
            reasons.append({"code": code, **detail})

    if await _abandon_row(db, run_id) is not None:
        add(CODE_ABANDONED)
    comp = await _completion_row(db, run_id)
    if comp is not None and comp.human_state == "accepted":
        add("COMPLETION_ACCEPTED")
    if state not in (AgentState.PAUSED, AgentState.BLOCKED):
        add("RUN_NOT_RESUMABLE", state=state.value)
    claim = (
        await db.execute(text("SELECT state FROM run_claims WHERE run_id = :r"), {"r": run_id})
    ).first()
    live = (getattr(service, "runs", {}).get(run_id) or {}).get("task")
    if (
        claim is not None
        or (live is not None and not live.done())
        or not service.engine_quiesced(run_id)
    ):
        add("ATTEMPT_IN_PROGRESS", claim=claim.state if claim else None)
    cut = now - timedelta(seconds=STALE_REQUEST_SECONDS)
    flight = (
        await db.execute(
            text(
                "SELECT id, attempt_no, requested_at FROM run_attempts WHERE run_id = :r AND "
                "kind = 'retry' AND state = 'requested' AND id <> :own"
            ),
            {"r": run_id, "own": own_attempt or ""},
        )
    ).first()
    if flight is not None and _dt(flight.requested_at) > cut:
        add("ATTEMPT_IN_PROGRESS", attemptNo=int(flight.attempt_no))
    side = (
        await db.execute(
            text(
                "SELECT id, state FROM high_impact_approvals WHERE run_id = :r AND state IN "
                "('approved', 'executing')"
                " ORDER BY created_at, id"
            ),
            {"r": run_id},
        )
    ).all()
    if side:
        add("UNRECONCILED_SIDE_EFFECTS", approvals=[{"id": s.id, "state": s.state} for s in side])
    limits = await _limits_row(db, run_id)
    if limits is not None:
        dims = exceeded(limits, await usage_of(db, run_id, now))
        if dims:
            add(CODE_LIMIT, exceeded=dims)
    factory = service.session_factory
    sess = await session_block(factory, run_id)
    if sess:
        add("SESSION_NOT_ACTIVE", sessionId=sess[0], state=sess[1])
    proj = (
        await db.execute(text("SELECT project_id FROM agent_runs WHERE id = :r"), {"r": run_id})
    ).first()
    ws = await workspace_block(factory, proj.project_id if proj else None)
    if ws:
        add("WORKSPACE_NOT_ACTIVE", workspaceId=ws[0], state=ws[1])
    head = (
        await db.execute(
            text(
                "SELECT seq, type, ts FROM agent_events WHERE run_id = :r AND type != "
                "'plan.rejected'"
                " ORDER BY seq DESC LIMIT 1"
            ),
            {"r": run_id},
        )
    ).first()
    nxt = (
        (
            await db.execute(
                text(
                    "SELECT COALESCE(MAX(attempt_no), 1) + 1 AS n FROM run_attempts"
                    " WHERE run_id = :r AND kind = 'retry'"
                ),
                {"r": run_id},
            )
        )
        .first()
        .n
    )
    return {
        "safe": not reasons,
        "reasons": reasons,
        "state": state.value,
        "nextAttemptNo": int(nxt),
        "resumeFrom": (
            {
                "kind": "engine_session_history",
                "sessionId": run_id,
                "lastEventSeq": int(head.seq),
                "lastEventType": head.type,
                "lastEventAt": _iso(head.ts),
            }
            if head
            else None
        ),
        "how": "POST /runs/{runId}/retry (or /resume): the engine is re-invoked with the same"
        " session id and the dsh SDK continues its own session history",
        "caveat": "side effects of the interrupted step are not rolled back; review the"
        " workspace checkpoints before resuming",
    }


async def _attempts(db: Any, run_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "attemptNo": int(r.attempt_no),
            "kind": r.kind,
            "state": r.state,
            "reason": r.reason,
            "requestedBy": r.requested_by,
            "requestedAt": _iso(r.requested_at),
            "fromState": r.from_state,
            "fromEventSeq": r.from_event_seq,
            "errorCode": r.error_code,
        }
        for r in (
            await db.execute(
                text(
                    "SELECT id, attempt_no, kind, state, reason, requested_by, requested_at, "
                    "from_state,"
                    " from_event_seq, error_code FROM run_attempts WHERE run_id = :r"
                    " ORDER BY requested_at, attempt_no, id"
                ),
                {"r": run_id},
            )
        ).all()
    ]


async def status_get(factory: Any, service: Any, run_id: str) -> dict[str, Any]:
    now = _now()
    async with factory() as db:
        limits = await _limits_row(db, run_id)
        usage = await usage_of(db, run_id, now)
        ex = exceeded(limits, usage) if limits is not None else []
        resume = await assess_resume(service, db, run_id, now)
        events = await _events(db, run_id)
        comp = await _completion_row(db, run_id)
        subs = _summary(await _subtasks(db, run_id))
        attempts = await _attempts(db, run_id)
        proj = (
            await db.execute(text("SELECT project_id FROM agent_runs WHERE id = :r"), {"r": run_id})
        ).first()
    pid = proj.project_id if proj else None
    failure = None
    if resume["state"] == AgentState.BLOCKED.value:
        failed = next((e for e in reversed(events) if e["type"] == "engine_failed"), None)
        if failed is not None:
            failure = {"seq": failed["seq"], "at": failed["at"], "reason": failed["reason"]}
    enforcement = {
        dim: {
            **ENFORCEMENT[dim],
            "limit": _limits_view(limits)[wire],
            "used": usage[dim],
            "exceeded": any(d["dimension"] == dim for d in ex),
        }
        for dim, _c, wire in _DIMS
    }
    return {
        "runId": run_id,
        "state": resume["state"],
        "limits": _limits_view(limits),
        "usage": usage,
        "enforcement": enforcement,
        "exceeded": ex,
        "checkpoints": {
            "events": events,
            "workspace": {
                "projectId": pid,
                "via": f"GET /api/v1/sandbox/{pid}/checkpoints",
                "note": "git snapshots live in the section 5 sandbox workspace, not read here",
            },
        },
        "resume": resume,
        "failure": failure,
        "attempts": attempts,
        "subtasks": subs,
        "completion": {
            "agentState": comp.agent_state if comp else "none",
            "humanState": comp.human_state if comp else "none",
            "reportNo": int(comp.report_no) if comp else 0,
        },
    }


# --------------------------------------------------------------------- abandon, retry


async def _preserved(db: Any, run_id: str) -> dict[str, Any]:
    items = _summary(await _subtasks(db, run_id))
    ledger = (
        (
            await db.execute(
                text("SELECT COUNT(*) AS n FROM cost_ledger WHERE run_id = :r"), {"r": run_id}
            )
        )
        .first()
        .n
    )
    last = (
        (
            await db.execute(
                text("SELECT MAX(seq) AS s FROM agent_events WHERE run_id = :r"), {"r": run_id}
            )
        )
        .first()
        .s
    )
    comp = await _completion_row(db, run_id)
    return {
        "subtasks": items["total"],
        "evidenceRefs": items["evidenceRefs"],
        "openBlockers": items["openBlockers"],
        "ledgerRows": int(ledger),
        "lastEventSeq": last,
        "checkpointEvents": len(await _events(db, run_id)),
        "completion": {
            "agentState": comp.agent_state if comp else "none",
            "humanState": comp.human_state if comp else "none",
        },
        "deleted": "nothing",
    }


def _attempt_view(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "attemptNo": int(row.attempt_no),
        "state": row.state,
        "reason": row.reason,
        "requestedBy": row.requested_by,
        "requestedAt": _iso(row.requested_at),
    }


async def abandon(
    factory: Any, service: Any, p: Principal, run_id: str, req: "AbandonBody"
) -> tuple[dict, bool]:
    async with _run_lock(service, run_id):
        return await _abandon(factory, service, p, run_id, req)


async def _abandon(
    factory: Any, service: Any, p: Principal, run_id: str, req: "AbandonBody"
) -> tuple[dict, bool]:
    reason = _check_text(req.reason, "reason", REASON_MAX, "BAD_REASON")
    async with factory() as db:
        done = await _abandon_row(db, run_id)
        if done is not None:  # idempotent: the first record stands
            return {
                "abandoned": True,
                "attempt": _attempt_view(done),
                "preserved": json.loads(done.preserved or "{}"),
                "duplicate": True,
            }, False
        await _assert_open(db, run_id)
        state = await _run_state(service, run_id)
        if state is AgentState.DONE:
            raise ExecError(409, "RUN_ALREADY_FINISHED", "the run is already finished")
    await service.stop_agent(
        run_id
    )  # state machine vocabulary: DONE; kills the engine, releases the claim
    async with factory() as db:
        preserved = await _preserved(db, run_id)
        last_retry = (
            (
                await db.execute(
                    text(
                        "SELECT COALESCE(MAX(attempt_no), 1) AS n FROM run_attempts"
                        " WHERE run_id = :r AND kind = 'retry'"
                    ),
                    {"r": run_id},
                )
            )
            .first()
            .n
        )
        aid, now = str(uuid.uuid4()), _now()
        try:
            await db.execute(
                text(
                    "INSERT INTO run_attempts (id, run_id, team_id, attempt_no, kind, state, "
                    "reason, request_id,"
                    " requested_by, requested_at, from_state, preserved, resolved_at) VALUES (:i, "
                    ":r, :t, :n,"
                    " 'abandon', :st, :re, :q, :u, :now, :fs, :pr, :now)"
                ),
                {
                    "i": aid,
                    "r": run_id,
                    "t": p.teamId,
                    "n": last_retry,
                    "st": req.outcome,
                    "re": reason,
                    "q": req.requestId,
                    "u": p.userId,
                    "now": now,
                    "fs": state.value,
                    "pr": json.dumps(preserved),
                },
            )
        except IntegrityError:  # a simultaneous abandon: the first record stands
            await db.rollback()
            done = await _abandon_row(db, run_id)
            return {
                "abandoned": True,
                "attempt": _attempt_view(done),
                "preserved": json.loads(done.preserved or "{}"),
                "duplicate": True,
            }, False
        await collab._audit(
            db,
            p,
            "run.abandoned",
            run_id,
            {
                "runId": run_id,
                "attemptNo": int(last_retry),
                "outcome": req.outcome,
                "before": {"state": state.value},
                "after": {"state": AgentState.DONE.value},
                "preserved": preserved,
            },
        )
        await _emit(db, p, run_id, "run.abandoned", {"outcome": req.outcome})
        await db.commit()
        done = await _abandon_row(db, run_id)
    return {
        "abandoned": True,
        "attempt": _attempt_view(done),
        "preserved": preserved,
        "duplicate": False,
    }, True


async def _refuse_retry(
    factory: Any, p: Principal, run_id: str, req: "RetryBody", reasons: list[dict]
) -> None:
    """The refusal itself is evidence: own transaction, because the refusing one is abandoned."""
    try:
        async with factory() as db:
            await collab._audit(
                db,
                p,
                "run.retry_refused",
                run_id,
                {
                    "runId": run_id,
                    "requestId": req.requestId,
                    "codes": [r["code"] for r in reasons],
                },
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - audit must never mask the refusal
        logger.exception("run.retry_refused audit failed (run=%s)", run_id)


def _run_lock(service: Any, run_id: str) -> asyncio.Lock:
    """One lock per run on the service, shared by retry and abandon.

    ponytail: serialises those two inside one process and lives as long as the service. Across
    processes the unique indexes of run_attempts and the re-check after the insert hold."""
    locks = service.__dict__.setdefault("_execution_run_locks", {})
    return locks.setdefault(run_id, asyncio.Lock())


async def retry(
    factory: Any, service: Any, p: Principal, run_id: str, req: "RetryBody"
) -> tuple[dict, bool]:
    async with _run_lock(service, run_id):
        return await _retry(factory, service, p, run_id, req)


async def _retry(
    factory: Any, service: Any, p: Principal, run_id: str, req: "RetryBody"
) -> tuple[dict, bool]:
    now = _now()

    async def existing(db: Any) -> Any:
        return (
            await db.execute(
                text(
                    "SELECT id, attempt_no, state, reason, requested_by, requested_at, error_code "
                    "FROM run_attempts"
                    " WHERE run_id = :r AND requested_by = :u AND request_id = :q AND kind = "
                    "'retry'"
                ),
                {"r": run_id, "u": p.userId, "q": req.requestId},
            )
        ).first()

    def dup_view(row: Any) -> dict[str, Any]:
        return {"attempt": _attempt_view(row), "errorCode": row.error_code, "duplicate": True}

    async with factory() as db:
        if (dup := await existing(db)) is not None:
            return dup_view(dup), False
        await db.execute(
            text(  # a retry stuck in 'requested' (process died before it resolved) frees the run
                "UPDATE run_attempts SET state = 'start_failed', error_code = 'STALE_REQUEST', "
                "resolved_at = :now"
                " WHERE run_id = :r AND kind = 'retry' AND state = 'requested' AND requested_at <= "
                ":cut"
            ),
            {"r": run_id, "now": now, "cut": now - timedelta(seconds=STALE_REQUEST_SECONDS)},
        )
        await db.commit()
        assessment = await assess_resume(service, db, run_id, now)
        if not assessment["safe"]:
            await db.rollback()
            await _refuse_retry(factory, p, run_id, req, assessment["reasons"])
            first = assessment["reasons"][0]
            raise ExecError(
                409,
                first["code"],
                "this run cannot be retried safely right now",
                {"reasons": assessment["reasons"], "state": assessment["state"]},
            )
        head = assessment["resumeFrom"] or {}
        aid, no = str(uuid.uuid4()), assessment["nextAttemptNo"]
        try:
            await db.execute(
                text(
                    "INSERT INTO run_attempts (id, run_id, team_id, attempt_no, kind, state, "
                    "reason, request_id,"
                    " requested_by, requested_at, from_state, from_event_seq, from_event_type) "
                    "VALUES (:i, :r, :t,"
                    " :n, 'retry', 'requested', :re, :q, :u, :now, :fs, :fseq, :ftype)"
                ),
                {
                    "i": aid,
                    "r": run_id,
                    "t": p.teamId,
                    "n": no,
                    "re": req.reason,
                    "q": req.requestId,
                    "u": p.userId,
                    "now": now,
                    "fs": assessment["state"],
                    "fseq": head.get("lastEventSeq"),
                    "ftype": head.get("lastEventType"),
                },
            )
        except IntegrityError as e:  # same requestId raced us, or another retry is resuming the run
            await db.rollback()
            if (dup := await existing(db)) is not None:
                return dup_view(dup), False
            reasons = [{"code": "ATTEMPT_IN_PROGRESS"}]
            await _refuse_retry(factory, p, run_id, req, reasons)
            raise ExecError(
                409,
                "ATTEMPT_IN_PROGRESS",
                "another retry is already resuming this run",
                {"reasons": reasons},
            ) from e
        # The slot is ours (unique in-flight index). Look again now that nothing else can claim it:
        # a retry in another process may have resumed the run between our first look and the insert.
        recheck = await assess_resume(service, db, run_id, now, own_attempt=aid)
        if not recheck["safe"]:
            await db.rollback()
            await _refuse_retry(factory, p, run_id, req, recheck["reasons"])
            raise ExecError(
                409,
                recheck["reasons"][0]["code"],
                "this run cannot be retried safely right now",
                {"reasons": recheck["reasons"], "state": recheck["state"]},
            )
        await collab._audit(
            db,
            p,
            "run.retry_requested",
            run_id,
            {
                "runId": run_id,
                "attemptNo": no,
                "requestId": req.requestId,
                "before": {"state": assessment["state"]},
                "fromEventSeq": head.get("lastEventSeq"),
            },
        )
        await db.commit()
    outcome, error = "resumed", None
    try:
        await service.resume_agent(run_id)
        if await _run_state(service, run_id) is not AgentState.EXECUTING:
            outcome, error = "start_failed", "RESUME_NOT_APPLIED"
    except Exception as exc:  # noqa: BLE001 - recorded on the attempt, then surfaced
        outcome, error = (
            "start_failed",
            (
                "PLAN_NOT_APPROVED"
                if type(exc).__name__ == "PlanNotApprovedError"
                else "RESUME_FAILED"
            ),
        )
        logger.exception("retry could not resume run %s", run_id)
    async with factory() as db:
        await db.execute(
            text(
                "UPDATE run_attempts SET state = :s, error_code = :e, resolved_at = :now"
                " WHERE id = :i"
            ),
            {"s": outcome, "e": error, "now": _now(), "i": aid},
        )
        if outcome == "resumed":
            await collab._audit(
                db,
                p,
                "execution.checkpoint_resumed",
                run_id,
                {
                    "runId": run_id,
                    "attemptNo": no,
                    "fromEventSeq": head.get("lastEventSeq"),
                    "fromEventType": head.get("lastEventType"),
                    "after": {"state": AgentState.EXECUTING.value},
                },
            )
            await _emit(db, p, run_id, "run.retry_requested", {"attemptNo": no})
        await db.commit()
        row = await existing(db)
    if outcome != "resumed":
        raise ExecError(
            409,
            error or "RESUME_FAILED",
            "the retry was recorded but the run did not resume",
            {"attempt": _attempt_view(row)},
        )
    return {"attempt": _attempt_view(row), "errorCode": None, "duplicate": False}, True


# ------------------------------------------------------------------------------ models


class LimitsBody(BaseModel):
    maxSeconds: int | None = Field(default=None, ge=1, le=BOUNDS["maxSeconds"][1])
    maxModelCalls: int | None = Field(default=None, ge=1, le=BOUNDS["maxModelCalls"][1])
    maxToolCalls: int | None = Field(default=None, ge=1, le=BOUNDS["maxToolCalls"][1])
    maxCostMicrousd: int | None = Field(default=None, ge=1, le=BOUNDS["maxCostMicrousd"][1])
    expectedVersion: int | None = Field(default=None, ge=0)


class SubtaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str = Field(default="", max_length=DESC_MAX)
    dependsOn: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=MAX_DEPS
    )
    requestId: str | None = Field(default=None, max_length=64)


class SubtaskPatch(BaseModel):
    expectedVersion: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESC_MAX)
    state: Literal["todo", "in_progress", "cancelled"] | None = None
    dependsOn: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(
        default=None, max_length=MAX_DEPS
    )


class BlockBody(BaseModel):
    reason: str = Field(min_length=3, max_length=REASON_MAX)


class UnblockBody(BaseModel):
    blockerId: str | None = Field(default=None, max_length=64)
    resolution: str | None = Field(default=None, max_length=REASON_MAX)


class CompleteBody(BaseModel):
    expectedVersion: int = Field(ge=1)


class EvidenceBody(BaseModel):
    kind: Literal["file", "diff", "test", "output", "link", "note", "artifact"]
    ref: str = Field(min_length=1, max_length=REF_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)


class OutputRef(BaseModel):
    kind: Literal["file", "diff", "artifact", "test", "log", "link"]
    ref: str = Field(min_length=1, max_length=REF_MAX)
    note: str | None = Field(default=None, max_length=200)


class ReportBody(BaseModel):
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX)
    outputs: list[OutputRef] = Field(default_factory=list, max_length=MAX_OUTPUTS)
    requestId: str | None = Field(default=None, max_length=64)


class DecideBody(BaseModel):
    reportDigest: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=REASON_MAX)


class RejectBody(DecideBody):
    reason: str = Field(min_length=3, max_length=REASON_MAX)


class AbandonBody(BaseModel):
    reason: str = Field(min_length=3, max_length=REASON_MAX)
    outcome: Literal["abandoned", "failed"] = "abandoned"
    requestId: str | None = Field(default=None, max_length=64)


class RetryBody(BaseModel):
    requestId: str = Field(min_length=8, max_length=64)
    reason: str | None = Field(default=None, max_length=REASON_MAX)


# ------------------------------------------------------------------------------ routes

_B = "/runs/{runId}"


def _status(response: Response, created: bool, ok: int = 201) -> None:
    response.status_code = ok if created else 200


@router.get(_B + "/execution-limits")
async def execution_limits_get(runId: str, actor: Reader) -> dict:
    return await _call(lambda f: limits_get(f, runId))


@router.put(_B + "/execution-limits")
async def execution_limits_put(runId: str, req: LimitsBody, actor: Writer) -> dict:
    return await _call(lambda f: limits_put(f, actor, runId, req))


@router.get(_B + "/execution-status")
async def execution_status(runId: str, actor: Reader) -> dict:
    return await _call(lambda f: status_get(f, _svc(), runId))


@router.get(_B + "/subtasks")
async def subtasks_get(runId: str, actor: Reader) -> dict:
    return await _call(lambda f: subtasks_list(f, runId))


@router.post(_B + "/subtasks", status_code=201)
async def subtasks_post(runId: str, req: SubtaskCreate, actor: Writer, response: Response) -> dict:
    out, created = await _call(lambda f: subtask_create(f, actor, runId, req))
    _status(response, created)
    return {"subtask": out, "duplicate": not created}


@router.patch(_B + "/subtasks/{subtaskId}")
async def subtasks_patch(runId: str, subtaskId: str, req: SubtaskPatch, actor: Writer) -> dict:
    return {"subtask": await _call(lambda f: subtask_patch(f, actor, runId, subtaskId, req))}


@router.post(_B + "/subtasks/{subtaskId}/block")
async def subtasks_block(runId: str, subtaskId: str, req: BlockBody, actor: Writer) -> dict:
    out, changed = await _call(lambda f: subtask_block(f, actor, runId, subtaskId, req))
    return {"subtask": out, "duplicate": not changed}


@router.post(_B + "/subtasks/{subtaskId}/unblock")
async def subtasks_unblock(runId: str, subtaskId: str, req: UnblockBody, actor: Writer) -> dict:
    out, changed = await _call(lambda f: subtask_unblock(f, actor, runId, subtaskId, req))
    return {"subtask": out, "duplicate": not changed}


@router.post(_B + "/subtasks/{subtaskId}/complete")
async def subtasks_complete(runId: str, subtaskId: str, req: CompleteBody, actor: Writer) -> dict:
    out, changed = await _call(lambda f: subtask_complete(f, actor, runId, subtaskId, req))
    return {"subtask": out, "duplicate": not changed}


@router.post(_B + "/subtasks/{subtaskId}/evidence")
async def subtasks_evidence(runId: str, subtaskId: str, req: EvidenceBody, actor: Writer) -> dict:
    out, changed = await _call(lambda f: subtask_evidence(f, actor, runId, subtaskId, req))
    return {"subtask": out, "duplicate": not changed}


@router.get(_B + "/completion")
async def completion_read(runId: str, actor: Reader) -> dict:
    return await _call(lambda f: completion_get(f, _svc(), actor, runId))


@router.post(_B + "/completion/report", status_code=201)
async def completion_report_route(
    runId: str, req: ReportBody, actor: Writer, response: Response
) -> dict:
    out, created = await _call(lambda f: completion_report(f, _svc(), actor, runId, req))
    _status(response, created)
    return {**out, "duplicate": not created}


@router.post(_B + "/completion/accept")
async def completion_accept(runId: str, req: DecideBody, actor: Approver) -> dict:
    out, changed = await _call(lambda f: completion_decide(f, _svc(), actor, runId, True, req))
    return {**out, "duplicate": not changed}


@router.post(_B + "/completion/reject")
async def completion_reject(runId: str, req: RejectBody, actor: Approver) -> dict:
    out, changed = await _call(lambda f: completion_decide(f, _svc(), actor, runId, False, req))
    return {**out, "duplicate": not changed}


@router.post(_B + "/abandon")
async def abandon_route(runId: str, req: AbandonBody, actor: Writer, response: Response) -> dict:
    out, created = await _call(lambda f: abandon(f, _svc(), actor, runId, req))
    _status(response, created, 200)
    return out


@router.post(_B + "/retry", status_code=202)
async def retry_route(runId: str, req: RetryBody, actor: Writer, response: Response) -> dict:
    out, created = await _call(lambda f: retry(f, _svc(), actor, runId, req))
    response.status_code = 202 if created else 200
    return out


__all__ = [
    "router",
    "configure_execution",
    "check_execution_start",
    "enforce_execution_start",
    "sweep_running_limits",
    "ExecutionBlockedError",
]
