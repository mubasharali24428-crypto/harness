"""M16-X5 — edge-case corpus, wave A: S4 decision seeds (raw-18).

Turns the raw-18 audit registry's Section-4 decision/lifecycle edge seeds
into executable regression tests. Test names mirror the registry line IDs
(``x18_<n>_`` prefix) so the corpus maps back to the audit doc:

- x18_21  [plan] approve double-invocation — sequential re-approve stays
          idempotent: SAME runId, no second CAS bump, no duplicate event.
- x18_22  [plan] approve-vs-reject concurrent race — two connections, one
          stale version: exactly one decision wins durably; the loser gets
          the honest 409 PLAN_ALREADY_DECIDED envelope. Pins the M12-F2 CAS
          (raw-18 #22 "conditional UPDATE") end-to-end at the HTTP seam.
- x18_2/3 [run] after-terminal — a late done-signal on a stopped/DONE run is
          rejected 409 RUN_NOT_LIVE and a late engine result RAISES instead
          of resurrecting; resume-after-DONE stays a silent no-op.
- x18_8   [done-signal] double-invocation — the gate consumes liveness once:
          after the first signal reaches review, a SECOND done-signal on the
          now-terminal run is refused 409 RUN_NOT_LIVE (exactly-once shape).
- x18_propose_422 [plan] propose with empty goal — FastAPI validation dies
          BEFORE any run/plan exists; with the H-019 envelope handlers wired
          the body is the unified {error:{code,message},detail:{...}} shape.

Covered-elsewhere note (skipped here, listed per mission protocol): the
deeper CAS primitives (#21/#24/#25/#56 neighbors), reverse-race, pre-0013
degradation and viewer gates live in tests/test_plan_concurrency.py; role/
evidence variants of the done-signal in tests/test_done_signal.py; publish
serialization in tests/unit/test_publish_ordering.py.
"""

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.events import EventType
from harness_contracts.roles import Principal, Role

from harness_orchestrator.plan_gate import (
    PLAN_ALREADY_DECIDED_CODE,
    _plan_gate_principal,
    configure_plan_gate,
)
from harness_orchestrator.plan_gate import (
    router as plan_router,
)
from harness_orchestrator.service import OrchestratorService

# ---------------------------------------------------------------------------
# Fixtures (self-contained per section convention; conftest does not exist)
# ---------------------------------------------------------------------------


class RecordingBus:
    """Duck-typed EventBus recording publishes (mirrors test_plan_gate)."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}")

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        return None


_AGENT_PLANS_DDL_V1 = (
    "CREATE TABLE agent_plans ("
    "id VARCHAR(36) PRIMARY KEY,"
    "run_id VARCHAR(36) NOT NULL,"
    "goal TEXT NOT NULL,"
    "files_likely_to_change JSON NOT NULL,"
    "steps JSON NOT NULL,"
    "tests JSON NOT NULL,"
    "risks JSON NOT NULL,"
    "estimated_cost_usd NUMERIC(12,6) NOT NULL,"
    "forbidden_paths JSON NOT NULL,"
    "expected_result TEXT NOT NULL,"
    "status VARCHAR(20) NOT NULL,"
    "version INTEGER NOT NULL DEFAULT 0,"
    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)

_EVENTS_DDL = (
    "CREATE TABLE agent_events ("
    "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    "project_id VARCHAR(36) NOT NULL,"
    "run_id VARCHAR(36),"
    "type VARCHAR(60) NOT NULL,"
    "payload TEXT NOT NULL,"
    "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)


@pytest.fixture()
def db_parts(tmp_path):
    """SQLite store carrying the post-0013 agent_plans (version column)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/x5.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.execute(text(_EVENTS_DDL))
            await conn.execute(text(_AGENT_PLANS_DDL_V1))
            # M18-C1: the plan-gate ownership guard probes projects.team_id
            # for any run carrying a projectId; x18 seeds bind runs to
            # 'proj-x5', so give that binding a same-team row.
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS projects ("
                    "id VARCHAR(36) PRIMARY KEY,"
                    " team_id VARCHAR(36) NOT NULL)"
                )
            )
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('proj-x5', 'team-x5')")
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=RecordingBus())
    yield {"service": service, "factory": factory}
    asyncio.run(engine.dispose())


def _principal(role: Role = Role.ADMIN, user_id: str = "u-x5") -> Principal:
    return Principal(userId=user_id, teamId="team-x5", role=role)


def _client(service: OrchestratorService, principal: Principal | None = None) -> TestClient:
    """Fresh app per call: plan-gate router + principal override (no bearer)."""
    configure_plan_gate(service)
    app = FastAPI()
    app.include_router(plan_router)
    if principal is not None:

        async def _override() -> Principal:
            return principal

        app.dependency_overrides[_plan_gate_principal] = _override
    return TestClient(app)


async def _plan_state(factory, plan_id: str) -> dict[str, Any]:
    from sqlalchemy import text

    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT status, version FROM agent_plans WHERE id = :p"),
                {"p": plan_id},
            )
        ).first()
    return {"status": row.status if row else None, "version": row.version if row else None}


def _propose(client: TestClient, goal: str = "x5 edge corpus") -> str:
    res = client.post("/api/v1/orchestrator/plans", json={"goal": goal})
    assert res.status_code == 201, res.text
    return res.json()["planId"]


# ---------------------------------------------------------------------------
# x18_21 — [plan] approve | double-invocation → idempotent same-runId
# ---------------------------------------------------------------------------


def test_x18_21_approve_double_invocation_is_idempotent_same_runid(db_parts):
    service, factory = db_parts["service"], db_parts["factory"]
    client = _client(service, _principal())

    plan_id = _propose(client)
    located = [rid for rid, rd in service.runs.items() if rd.get("plan_id") == plan_id]
    assert located, "proposal must register its run"
    run_id = located[0]
    # The plan-gate route mints bare runs (no projectId); give the run one so
    # the service's _emit bridge fans the approve event out to the recording
    # bus exactly like a deploy-wired run.
    service.runs[run_id]["projectId"] = "proj-x5"

    r1 = client.post(f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert r1.status_code == 200, r1.text
    first_run = r1.json()["runId"]

    # The double-invocation itself (double-click / retried POST):
    r2 = client.post(f"/api/v1/orchestrator/plans/{plan_id}/approve")
    assert r2.status_code == 200, r2.text
    assert r2.json()["runId"] == first_run, "re-approve must short-circuit"

    # Exactly ONE durable decision: version sits at 1 (single CAS bump)...
    state = asyncio.run(_plan_state(factory, plan_id))
    assert state["version"] == 1, "second approve must not re-bump the CAS"
    assert state["status"] == "approved"

    # ...and exactly ONE plan_approved event reached the bus. The route's
    # emission is fire-and-forget; drain the chain before counting.
    async def _drain():
        if svc_bg := getattr(service, "_bg_tasks", None):
            await asyncio.gather(*list(svc_bg))

    asyncio.run(_drain())
    approved_events = [
        p for p in service.bus.published if p.endswith(f":{EventType.PLAN_APPROVED.value}")
    ]
    assert len(approved_events) == 1


# ---------------------------------------------------------------------------
# x18_22 — [plan] approve vs reject | concurrent actors, one stale version
# ---------------------------------------------------------------------------


def test_x18_22_approve_vs_reject_race_exactly_one_winner_loser_gets_409(db_parts):
    service, factory = db_parts["service"], db_parts["factory"]
    client = _client(service, _principal())
    plan_id = _propose(client)
    located = [
        rid for rid, rd in service.runs.items() if rd.get("plan_id") == plan_id
    ]
    assert located, "proposal must register its run"
    run_id = located[0]

    barrier = asyncio.Barrier(2)

    async def _approve():
        await barrier.wait()
        try:
            await service._persist_plan_status(
                run_id, plan_id, "approved", expected_version=0
            )
        except Exception as exc:  # PlanAlreadyDecidedError expected for the loser
            return type(exc).__name__
        return "approved"

    async def _reject():
        await barrier.wait()
        try:
            await service._persist_plan_status(
                run_id, plan_id, "rejected", expected_version=0
            )
        except Exception as exc:  # PlanAlreadyDecidedError expected for the loser
            return type(exc).__name__
        return "rejected"

    async def _race():
        return await asyncio.gather(_approve(), _reject())

    outcomes = asyncio.run(_race())
    # Exactly one winner; WHICH decision wins is whatever the interleaving
    # allows — the CAS guarantees a single durable answer, not a favorite.
    assert sorted(outcomes) == ["PlanAlreadyDecidedError", "rejected"] or sorted(
        outcomes
    ) == ["PlanAlreadyDecidedError", "approved"], (
        f"exactly one winner allowed: {outcomes}"
    )
    winner = "rejected" if "rejected" in outcomes else "approved"

    state = asyncio.run(_plan_state(factory, plan_id))
    assert state["version"] == 1, "one conditional UPDATE moved the row"
    assert state["status"] == winner, "the persisted mirror names the same winner"

    # HTTP seam: the loser's retry gets the honest 409 envelope (CAS pinned).
    loser = _client(service, _principal(role=Role.ENGINEER, user_id="u-loser"))
    res = loser.post(f"/api/v1/orchestrator/plans/{plan_id}/reject")
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["error"]["code"] == PLAN_ALREADY_DECIDED_CODE


# ---------------------------------------------------------------------------
# x18_2 + x18_3 — [run] after-terminal: stop→DONE is absorbing
# ---------------------------------------------------------------------------


class _InstantEngine:
    """Deterministic stand-in for DeepSeekHarnessWrapper: settles instantly."""

    mode = "stub"

    def execute_goal(self, goal=None, session_id=None):
        return {
            "sessionId": session_id,
            "engine": "stub",
            "status": "completed",
            "finalResponse": "settled",
        }


async def _walk_to_done(svc: OrchestratorService, run_id: str = "run-x5") -> None:
    svc.engine = _InstantEngine()
    svc.run_queue_factory = None  # bare-mode path: spawn directly
    plan_id = await svc.propose_plan(run_id, goal="g", project_id=None)
    await svc.approve_plan(run_id, plan_id, "user-x5")
    await svc.start_execution(run_id)
    task = svc.runs[run_id].get("task")
    if task is not None:
        await task
    await svc.stop_agent(run_id)  # → terminal DONE


async def test_x18_2_after_terminal_transitions_are_hard_rejected(db_parts):
    from harness_orchestrator.state_machine import AgentState, InvalidStateTransitionError

    svc = db_parts["service"]
    await _walk_to_done(svc, "run-term")
    sm = svc.runs["run-term"]["state_machine"]
    assert sm.state is AgentState.DONE

    with pytest.raises(InvalidStateTransitionError):
        sm.execution_completed("late zombie engine result")
    with pytest.raises(InvalidStateTransitionError):
        sm.signal_done()
    assert sm.state is AgentState.DONE, "terminal absorbs everything"


async def test_x18_3_stop_double_invocation_is_idempotent_done(db_parts):
    """x18_3: stop→DONE double-invocation semantics, pinned AS SHIPPED.

    RESOLVED (multiplayer S4): service-level stop is an idempotent no-op;
    machine-level finish() still refuses. Original note: the registry expects a second stop on
    a DONE run to be an "idempotent no-op returning DONE", but today
    ``OrchestratorService.stop_agent`` calls ``sm.finish()`` unguarded, so the
    repeat stop RAISES InvalidStateTransitionError (HTTP 400 via api.py) —
    the machine-level refusal below. The route handler has no try/except for
    it either, so a client double-clicking Stop gets a 400, not 200-DONE.
    This test pins the loud-refusal contract so the corpus detects any drift;
    if ATLAS implements the #3 fix (stop reads state, returns current if
    already DONE), flip this pin to assert the silent no-op.
    """
    from harness_orchestrator.state_machine import AgentState, InvalidStateTransitionError

    svc = db_parts["service"]
    await _walk_to_done(svc, "run-stop")
    sm = svc.runs["run-stop"]["state_machine"]
    assert sm.state is AgentState.DONE

    # Machine level: the repeat stop is refused loudly (H-008 hygiene)...
    with pytest.raises(InvalidStateTransitionError):
        sm.finish()
    assert sm.state is AgentState.DONE

    # ...but the service seam is idempotent (S4 fix for the #3 gap): a repeat
    # stop on a DONE run is a silent no-op, not a 400/500.
    await svc.stop_agent("run-stop")
    assert svc.runs["run-stop"]["state_machine"].state is AgentState.DONE


async def test_x18_8_done_signal_after_terminal_refused_409_not_resurrection(db_parts):
    """x18_8/x18_9 neighbor: a done-signal arriving AFTER the run went
    terminal (double-fire of the signal under any reader-state ordering) is
    refused with RUN_NOT_LIVE — never processed, never resurrecting."""
    from harness_orchestrator.signals import RunNotLiveForDoneSignalError
    from harness_orchestrator.state_machine import AgentState

    svc = db_parts["service"]
    await _walk_to_done(svc, "run-sig")
    assert svc.runs["run-sig"]["state_machine"].state is AgentState.DONE

    admin = Principal(userId="user-x5", teamId="team-x5", role=Role.ADMIN)
    for _ in (1, 2):  # both the late signal AND its double-fire refuse
        with pytest.raises(RunNotLiveForDoneSignalError) as excinfo:
            await svc.gate_done_signal("run-sig", admin, "--- a/f\n+b/c\n")
        assert excinfo.value.http_status == 409
        assert excinfo.value.code == "RUN_NOT_LIVE"


# ---------------------------------------------------------------------------
# x18_propose_422 — [plan] propose | empty goal → unified 422 envelope
# ---------------------------------------------------------------------------


def test_x18_propose_empty_goal_422_unified_envelope_and_nothing_created():
    service = OrchestratorService(bus=RecordingBus())  # bare unit mode
    app = FastAPI()
    app.include_router(plan_router)
    import pathlib as _pl
    import sys as _sys
    _s1src = _pl.Path(__file__).resolve().parents[3] / 'section1' / 'src'
    if str(_s1src) not in _sys.path:
        _sys.path.insert(0, str(_s1src))  # test-only: canonical middleware lives in S1
    from harness_identity.envelope_middleware import install_error_envelope

    install_error_envelope(app)  # H-019 handlers; canonical middleware (B3 dedupe)

    async def _override() -> Principal:
        return _principal()

    app.dependency_overrides[_plan_gate_principal] = _override
    client = TestClient(app, raise_server_exceptions=True)

    res = client.post("/api/v1/orchestrator/plans", json={"goal": ""})
    assert res.status_code == 422, res.text
    body = res.json()
    # Unified envelope contract (harness_contracts.error_body shape):
    assert set(body) == {"error", "detail"}, body
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["detail"]["error"] == body["error"], "legacy mirror identical"

    # Nothing was created: no run registered, no plan persisted, no events.
    assert service.runs == {}
    assert service.bus.published == []
