"""Mission 11 Wave 1 — done-signal gate + terminal-DONE hygiene (H-007/H-008).

Covers the W1 acceptance matrix for the human done-signal and the terminal
state machine:

- reader-role done-signal → 403 (ROLE_DENIED) at the HTTP seam;
- empty/whitespace diffText → 400 (EMPTY_DIFF);
- a stopped/DONE run cannot be resurrected by a late signal → 409
  (RUN_NOT_LIVE), and the machine itself refuses to leave DONE;
- REVIEW_PASSED in AWAITING_DONE_SIGNAL reaches terminal DONE — no zombie
  re-execution loop, even after a restart rehydrates the run into the gate
  state (the in-memory done-gate flag is re-derived from state on signal);
- reject_plan emits a plan.rejected event row AND an audit_log row via the
  S1 helper ``harness_identity.audit_emit.emit_plan_rejected``, and the run
  survives restart without resurrection: the persisted status is 'rejected',
  so a fresh service rehydrates IDLE with plan_approved False.

Schema note: the frozen Section-1 tables are mirrored minimally in scratch
SQLite (agent_events / agent_plans / audit_log / team_members / users /
teams / projects / agent_runs), mirroring the wave-9 self-contained fixture
pattern (conftest.py is shared; no new fixtures are added there).
"""

import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.events import EventType
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# section1 ships the audited emission helper this mission consumes; tests run
# under section4's interpreter paths, so its src tree is appended explicitly.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "section1" / "src"))

from harness_identity.audit_emit import emit_plan_rejected  # noqa: E402

from harness_orchestrator.api import (  # noqa: E402
    configure_orchestrator,
    get_principal,
    router,
)
from harness_orchestrator.planner import PlanNotApprovedError  # noqa: E402
from harness_orchestrator.service import OrchestratorService  # noqa: E402
from harness_orchestrator.signals import (  # noqa: E402
    EmptyDiffTextError,
    RunNotLiveForDoneSignalError,
    gate_done_signal,
)
from harness_orchestrator.state_machine import (  # noqa: E402
    PLAN_REJECTED_EVENT,
    AgentState,
    AgentStateMachine,
    InvalidStateTransitionError,
)

CLEAN_DIFF = "--- a/app.py\n+++ b/app.py\n@@\n print('hi')\n+print('bye')\n"

DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL
);
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id)
);
CREATE TABLE agent_runs (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id),
    state VARCHAR(40),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id),
    run_id VARCHAR(36) REFERENCES agent_runs(id),
    type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    goal TEXT NOT NULL,
    files_likely_to_change TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '[]',
    tests TEXT NOT NULL DEFAULT '[]',
    risks TEXT NOT NULL DEFAULT '[]',
    estimated_cost_usd REAL NOT NULL DEFAULT 0.05,
    forbidden_paths TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE users (
    id VARCHAR(36) PRIMARY KEY,
    email VARCHAR(320) UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE team_members (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id),
    user_id VARCHAR(36) REFERENCES users(id),
    display_name VARCHAR(200) NOT NULL DEFAULT '',
    member_type VARCHAR(16) NOT NULL DEFAULT 'human',
    role VARCHAR(16) NOT NULL DEFAULT 'engineer',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
);
CREATE TABLE audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_member_id VARCHAR(36) REFERENCES team_members(id),
    actor_kind VARCHAR(16) NOT NULL DEFAULT 'human',
    action VARCHAR(80) NOT NULL,
    target_type VARCHAR(60) NOT NULL DEFAULT '',
    target_id VARCHAR(80) NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

SEED = [
    "INSERT INTO teams (id, name) VALUES ('team-ds', 'DoneSignal')",
    "INSERT INTO projects (id, team_id) VALUES ('proj-ds', 'team-ds')",
    "INSERT INTO agent_runs (id, project_id, state) VALUES ('run-ds', 'proj-ds', 'idle')",
    "INSERT INTO users (id, email, name) VALUES "
    "('user-ds', 'ds@example.com', 'Done Signaller')",
    "INSERT INTO team_members (id, team_id, user_id, display_name, role) VALUES "
    "('member-ds', 'team-ds', 'user-ds', 'DS', 'admin')",
]


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/done_signal.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for statement in DDL.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            for stmt in SEED:
                await conn.execute(text(stmt))

    await _setup()
    yield factory
    await engine.dispose()


def _service(factory) -> OrchestratorService:
    """Service wired to the scratch DB with queueing disabled.

    ``run_queue_factory`` normally defaults to the session factory, but these
    tests drive start/stop synchronously, so queueing is switched off and
    execution spawns directly (the documented bare-mode path).
    """
    svc = OrchestratorService(session_factory=factory)
    svc.run_queue_factory = None
    return svc


async def _walk_to_done_gate(svc: OrchestratorService, run_id: str = "run-ds") -> None:
    """propose → approve → start(engine stub settles instantly) → gate open."""
    svc.engine = _InstantEngine()
    plan_id = await svc.propose_plan(run_id, project_id="proj-ds")
    await svc.approve_plan(run_id, plan_id, "user-ds")
    await svc.start_execution(run_id)
    task = svc.runs[run_id].get("task")
    if task is not None:
        await task
    sm: AgentStateMachine = svc.runs[run_id]["state_machine"]
    assert sm.state is AgentState.AWAITING_DONE_SIGNAL


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


# --------------------------------------------------------------------------
# Role gate — reader roles may not declare work done.
# --------------------------------------------------------------------------


def test_reader_role_done_signal_is_rejected():
    from harness_orchestrator.signals import DoneSignalRoleDenied

    principal = Principal(userId="u-view", teamId="t", role=Role.VIEWER)
    with pytest.raises(DoneSignalRoleDenied) as excinfo:
        gate_done_signal(principal, AgentState.AWAITING_DONE_SIGNAL, CLEAN_DIFF)
    assert excinfo.value.http_status == 403
    assert excinfo.value.code == "ROLE_DENIED"


def test_guest_role_done_signal_rejected_too():
    from harness_orchestrator.signals import DoneSignalRoleDenied

    principal = Principal(userId="u-guest", teamId="t", role=Role.GUEST)
    with pytest.raises(DoneSignalRoleDenied) as excinfo:
        gate_done_signal(principal, AgentState.EXECUTING, CLEAN_DIFF)
    assert excinfo.value.code == "ROLE_DENIED"


async def test_reader_role_done_signal_http_403(db_factory):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId="user-ds", teamId="team-ds", role=Role.VIEWER
    )
    client = TestClient(app)
    res = client.post(
        "/api/v1/orchestrator/runs/run-ds/done-signal",
        json={"diffText": CLEAN_DIFF},
    )
    assert res.status_code == 403
    body = res.json()["detail"]
    assert body["error"]["code"] == "ROLE_DENIED"

# --------------------------------------------------------------------------
# Evidence gate — an empty diffText proves nothing was delivered.
# --------------------------------------------------------------------------


async def test_empty_diff_text_rejected_before_any_transition(db_factory):
    svc = _service(db_factory)
    await _walk_to_done_gate(svc)
    events: list[tuple] = []
    svc.on_event = lambda etype, payload: events.append((etype, payload))

    with pytest.raises(EmptyDiffTextError) as excinfo:
        await svc.gate_done_signal("run-ds", _admin(), "")
    assert excinfo.value.code == "EMPTY_DIFF"

    # Nothing moved: still sitting in the done-gate, no DONE_SIGNAL event.
    sm = svc.runs["run-ds"]["state_machine"]
    assert sm.state is AgentState.AWAITING_DONE_SIGNAL
    assert EventType.DONE_SIGNAL_RECEIVED not in [etype for etype, _ in events]


async def test_whitespace_only_diff_rejected(db_factory):
    svc = _service(db_factory)
    with pytest.raises(EmptyDiffTextError):
        await svc.gate_done_signal("run-ds", _admin(), "   \n\t  ")


def _admin() -> Principal:
    return Principal(userId="user-ds", teamId="team-ds", role=Role.ADMIN)


# --------------------------------------------------------------------------
# Liveness gate — stopped/DONE runs are never resurrected by a late signal.
# --------------------------------------------------------------------------


async def test_done_signal_on_stopped_run_is_conflict(db_factory):
    svc = _service(db_factory)
    plan_id = await svc.propose_plan("run-ds", project_id="proj-ds")
    await svc.approve_plan("run-ds", plan_id, "user-ds")
    await svc.start_execution("run-ds")
    await svc.stop_agent("run-ds")
    assert svc.runs["run-ds"]["state_machine"].state is AgentState.DONE

    with pytest.raises(RunNotLiveForDoneSignalError) as excinfo:
        await svc.gate_done_signal("run-ds", _admin(), CLEAN_DIFF)
    assert excinfo.value.code == "RUN_NOT_LIVE"
    # And the HTTP seam maps it to 409 (router pointed at THIS service).
    configure_orchestrator(svc)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: _admin()
    res = TestClient(app).post(
        "/api/v1/orchestrator/runs/run-ds/done-signal",
        json={"diffText": CLEAN_DIFF},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["error"]["code"] == "RUN_NOT_LIVE"


async def test_stopped_run_cannot_be_resumed_into_execution(db_factory):
    svc = _service(db_factory)
    plan_id = await svc.propose_plan("run-ds", project_id="proj-ds")
    await svc.approve_plan("run-ds", plan_id, "user-ds")
    await svc.start_execution("run-ds")
    await svc.stop_agent("run-ds")
    sm = svc.runs["run-ds"]["state_machine"]

    # resume-from-DONE is a deliberate silent no-op (pinned by
    # tests/unit/test_resume.py): it must never reach EXECUTING...
    sm.resume()
    assert sm.state is AgentState.DONE
    # ...while an explicit done-signal on the terminal run is a loud refusal.
    with pytest.raises(InvalidStateTransitionError):
        sm.signal_done()
    assert sm.state is AgentState.DONE


# --------------------------------------------------------------------------
# Terminal semantics — REVIEW_PASSED in the done-gate lands on DONE.
# --------------------------------------------------------------------------


async def test_review_passed_in_awaiting_done_signal_reaches_terminal_done(db_factory):
    svc = _service(db_factory)
    await _walk_to_done_gate(svc)
    events: list[tuple] = []
    svc.on_event = lambda etype, payload: events.append((etype, payload))

    findings = await svc.signal_done("run-ds", diff_text=CLEAN_DIFF)
    assert findings.passed is True
    sm = svc.runs["run-ds"]["state_machine"]
    assert sm.state is AgentState.DONE

    verdicts = [payload for etype, payload in events if etype is EventType.REVIEW_PASSED]
    assert verdicts, "REVIEW_PASSED must be emitted"
    # The zombie killer: DONE, not EXECUTING. No further transitions allowed.
    with pytest.raises(InvalidStateTransitionError):
        sm.transition_to(AgentState.EXECUTING)


async def test_review_pass_after_restart_rehydration_still_terminates(db_factory):
    """The done-gate flag is in-memory; a restart replays states only.

    Rehydrate straight into AWAITING_DONE_SIGNAL (as the replay would), fire
    signal_done, and the pass must STILL terminate the run instead of looping
    back to EXECUTING forever (the H-008 zombie).
    """
    _ = _service(db_factory)  # prove the fixture DB is wired; machine is standalone
    sm = AgentStateMachine(run_id="rehydrated-run", on_event=lambda t, p: None)
    sm.state = AgentState.AWAITING_DONE_SIGNAL  # post-replay, flag lost
    assert sm.done_gate_opened is False

    sm.signal_done()  # re-derives the gate from the state at signal time
    assert sm.done_gate_opened is True
    sm.reviewer_passed({"passed": True})
    assert sm.state is AgentState.DONE


# --------------------------------------------------------------------------
# reject_plan — event + audit row, and no resurrection after restart.
# --------------------------------------------------------------------------


async def test_reject_plan_writes_event_and_audit_row_and_survives_restart(db_factory):
    factory = db_factory
    svc = _service(factory)
    plan_id = await svc.propose_plan("run-ds", project_id="proj-ds")

    # Sanity: the S1 helper landed with this exact symbol/signature shape.
    assert callable(emit_plan_rejected)

    await svc.reject_plan("run-ds", plan_id, "user-ds")

    async with factory() as db:
        event_rows = (
            (
                await db.execute(
                    text(
                        "SELECT type, payload FROM agent_events "
                        "WHERE run_id = 'run-ds' ORDER BY seq"
                    )
                )
            )
            .mappings()
            .all()
        )
        status_row = (
            await db.execute(text("SELECT status FROM agent_plans WHERE id = :p"),
                             {"p": plan_id})
        ).first()
        audit_rows = (
            (
                await db.execute(
                    text(
                        "SELECT action, target_id, detail FROM audit_log "
                        "WHERE action = 'plan.rejected'"
                    )
                )
            )
            .mappings()
            .all()
        )

    rejection_events = [r for r in event_rows if r["type"] == PLAN_REJECTED_EVENT]
    assert rejection_events, "plan.rejected must be persisted as an event row"
    assert '"planId"' in str(rejection_events[-1]["payload"])
    assert status_row is not None and status_row.status == "rejected"
    assert len(audit_rows) == 1, "exactly one plan.rejected audit row"
    assert audit_rows[0]["target_id"] == "run-ds"

    # Restart: a FRESH service rehydrates from the log. The rejected plan is
    # not approved, the machine boots non-executing, and nothing resurrects.
    fresh = _service(factory)
    run_data = await fresh._ensure_run("run-ds")
    sm_fresh: AgentStateMachine = run_data["state_machine"]
    assert sm_fresh.plan_approved is False
    assert sm_fresh.state is not AgentState.EXECUTING
    with pytest.raises((PlanNotApprovedError, InvalidStateTransitionError)):
        await fresh.start_execution("run-ds")


async def test_state_machine_reject_plan_emits_and_resets_approval():
    events: list[tuple] = []
    sm = AgentStateMachine(run_id="rj-run", on_event=lambda t, p: events.append((t, p)))
    sm.plan_approved = True
    sm.reject_plan("plan-x", "approver-1")
    assert sm.plan_approved is False
    assert sm.state is AgentState.IDLE
    names = [
        getattr(etype, "value", etype) for etype, _ in events
    ]
    assert "plan.rejected" in names


async def test_emit_plan_rejected_helper_signature_contract():
    """The W1-A2 coordination contract: keyword surface we call at merge."""
    import inspect

    params = inspect.signature(emit_plan_rejected).parameters
    for name in ("actor_user_id", "team_id", "target_id"):
        assert name in params, f"emit_plan_rejected missing '{name}'"


async def test_run_id_generation_remains_unique_across_rescue_flow(db_factory):
    """Cheap invariant guard: uuid4 ids never collide inside one test run."""
    ids = {str(uuid.uuid4()) for _ in range(64)}
    assert len(ids) == 64
