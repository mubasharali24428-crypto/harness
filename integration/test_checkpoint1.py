"""Checkpoint 1 — headless integration of Sections 1, 3, 4, 5.

Proves: protocol conformance to frozen contracts, event semantics ⊆ contracts,
S1 bus persistence + replay of an S4 run, S3 powering S4's reviewer,
S5 executing S4's engine work — through the faithful flow that obeys the three
product laws (staged-approval gate, I-09 verification-first, reviewer integrity);
see docs/briefs/evidence/90day/checkpoint1-redesign-2026-11-21.md.
"""

import asyncio
import concurrent.futures
import json
import re
import shutil
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "section1" / "src"))
sys.path.insert(0, str(ROOT / "section3" / "src"))
sys.path.insert(0, str(ROOT / "section4" / "src"))
sys.path.insert(0, str(ROOT / "section5" / "src"))

import harness_contracts as contracts
from fastapi import FastAPI
from harness_contracts.events import EventType
from harness_contracts.roles import Principal, Role
from harness_contracts.signatures import ModelRouter, Sandbox
from harness_identity.eventbus import EventBus
from harness_identity.models import Base as S1Base
from harness_orchestrator import api as orch_api
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.reviewer import ReviewerSubAgent
from harness_orchestrator.service import OrchestratorService
from harness_router.providers import ModelProviderClient
from harness_router.service import ModelRouterService
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService


@pytest.fixture(scope="session")
def event_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _build_schema(conn):
    """create_all + SQL-side columns the ORM deliberately omits.

    M15-D1: ``agent_runs.pr_id`` has no ORM mirror (it would create a
    runs<->PRs flush-ordering cycle), so ``create_all`` cannot emit it while
    S4's run aggregate (``_RUN_ENRICH_SQL``) reads it. Mirror the migrated
    shape, like section4/tests/unit/test_run_get.py does.
    """
    await conn.run_sync(S1Base.metadata.create_all)
    await conn.execute(text("ALTER TABLE agent_runs ADD COLUMN pr_id VARCHAR(36)"))


@pytest_asyncio.fixture
async def bus():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await _build_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield EventBus(factory)
    await engine.dispose()


@pytest.fixture
def router():
    client = ModelProviderClient(api_keys={"deepseek": "test-key"})
    client.set_mock_response("", "Review looks clean; changes align with the brief.")
    return ModelRouterService(client=client)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """S5 local runner, unconfined through the explicit opt-in — the same convention
    section5/tests/conftest.py uses. Confinement itself is S5's own suite
    (test_landlock_read_isolation, test_phase4_confinement_gate); this checkpoint
    proves the cross-section flow, and must not depend on the host offering Landlock."""
    monkeypatch.setenv("HARNESS_SANDBOX_ALLOW_UNCONFINED", "1")
    return SandboxService(
        runner=LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="off")
    )


@pytest.mark.asyncio
async def test_protocols_conform_to_contracts(router, sandbox):
    assert isinstance(router, ModelRouter)
    assert isinstance(sandbox, Sandbox)


# ----------------------------------------------------------------- faithful flow
# Checkpoint-1 redesign (docs/briefs/evidence/90day/checkpoint1-redesign-2026-11-21.md).
# The flow obeys the three product laws instead of patching around them:
#   1. staged-approval completion gate: high-impact changes are withheld and the
#      leader approves + applies them through the collab routes before done;
#   2. I-09 verification-first (default on, never set here): the run's OWN test
#      run, executed for real in the S5 sandbox, reaches agent_tool_calls through
#      the live writer (record_engine_tool_calls) — nothing is inserted by hand;
#   3. reviewer integrity: the verdict only passes on that recorded green run.

MIGRATIONS = ROOT / "section1" / "migrations"
# The real migration files for the tables the flow touches beyond the S1 ORM.
FLOW_MIGRATIONS = (
    "0006_run_claims.sql",  # durable queue: start_execution enqueues, poll_once claims
    "0007_team_budget.sql",  # teams.budget_usd: the pre-spawn + reviewer budget gates read it
    "0035_run_collaboration.sql",  # participants/leader + high_impact_approvals
    "0043_control.sql",  # control lease: the leader IS the lease holder
    "0044_agents.sql",  # agent_tool_calls: where the engine's calls are recorded
    "0049_artifacts.sql",  # run_changes: the done-signal route records the diff's files
)

TEAM, PROJECT, LEADER = "team-int", "proj-int", "lead-int"
P = "/api/v1/orchestrator/runs"
LIFECYCLE = {
    "plan_proposed", "plan_approved", "execution_started", "engine_completed",
    "engine_failed", "done_signal_received", "review_passed", "review_failed",
}
PYTEST = f"{sys.executable} -m pytest -q -p no:cacheprovider"

# Seed repo: 30 small functions (60 lines) and a test pinning one of them.
SEED_CALC = "".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(30))
SEED_TEST = "from calc import f7\n\n\ndef test_f7():\n    assert f7(1) == 8\n"
# The agent's refactor: same behaviour, every seed line rewritten -> a
# SUBSTANTIAL modify, which staging withholds for leader approval.
AGENT_CALC = (
    "def _offset(i):\n"
    "    def f(x):\n"
    "        return x + i\n"
    "    return f\n\n\n"
    "for _i in range(30):\n"
    "    globals()[f'f{_i}'] = _offset(_i)\n"
)
BROKEN_CALC = AGENT_CALC.replace("return x + i", "return x - i")


def _sqlite_statements(sql: str) -> list[str]:
    """A migration file's statements, with the two PG-only spellings swapped."""
    body = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    body = re.sub(r"\bBIGSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", body)
    body = re.sub(r"\bnow\(\)", "CURRENT_TIMESTAMP", body)
    return [s.strip() for s in body.split(";") if s.strip()]


@pytest_asyncio.fixture
async def flow_db(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'flow.db'}")
    async with eng.begin() as conn:
        await _build_schema(conn)
        for name in FLOW_MIGRATIONS:
            for stmt in _sqlite_statements((MIGRATIONS / name).read_text()):
                await conn.execute(text(stmt))
        await conn.execute(text("INSERT INTO teams (id, name) VALUES (:t, 'cp1')"), {"t": TEAM})
        await conn.execute(
            text("INSERT INTO projects (id, team_id, name) VALUES (:p, :t, 'cp1')"),
            {"p": PROJECT, "t": TEAM},
        )
        await conn.execute(
            text(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ('m-lead', :t, :u, 'Lead', 'human', 'engineer')"
            ),
            {"t": TEAM, "u": LEADER},
        )
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


def _sandbox_pytest(sandbox_svc, task_id: str, tree: Path) -> tuple[int, str]:
    """Run the scratch repo's tests for real inside an S5 sandbox task."""
    handle = sandbox_svc.run_task(task_id, "org/repo", "agent/demo", [])
    try:
        task_ws = sandbox_svc.runner.active_tasks[handle.taskId]["workspace"]
        shutil.copytree(tree, task_ws, dirs_exist_ok=True)

        async def go():
            return await sandbox_svc.runner.exec_structured(handle, PYTEST)

        # The engine calls this from its worker thread, and tests from the loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            res = pool.submit(lambda: asyncio.run(go())).result()
    finally:
        sandbox_svc.destroy(handle)
    return res.exit_code, f"{res.passed_count} passed, {res.failed_count} failed: {res.summary}"


class ScratchRuntime:
    """The injected engine runtime: edits the run workspace, runs the tests in S5,
    and reports both as the runtime's own session events (tool/call + tool/result)."""

    def __init__(self, sandbox_svc, *, new_calc: str, run_tests: bool = True) -> None:
        self.sandbox, self.new_calc, self.run_tests = sandbox_svc, new_calc, run_tests
        self.engine: DeepSeekHarnessWrapper | None = None
        self.run_id = ""
        self.test_runs: list[tuple[int, str]] = []

    def __call__(self, goal: str) -> dict:
        ws = Path(self.engine.run_workspace(self.run_id))
        (ws / "calc.py").write_text(self.new_calc)
        events: list[dict] = []
        if self.run_tests:
            code, output = _sandbox_pytest(self.sandbox, f"{self.run_id}-pytest", ws)
            self.test_runs.append((code, output))
            events = [
                {"type": "tool/call", "data": {
                    "callId": "call-1", "name": "bash",
                    "arguments": json.dumps({"command": PYTEST})}},
                {"type": "tool/result", "data": {"message": {
                    "toolCallId": "call-1", "isError": False,
                    "content": f"{output}\n[exit code: {code}]"}}},
            ]
        return {"finalResponse": f"{goal} => rewrote calc.py", "events": events}


async def _flow(tmp_path, monkeypatch, flow_db, router, sandbox, runtime, run_id):
    """Wire S1 (db) + S3 (router) + S4 (service + real routes) + S5 (sandbox) and run the
    engine through the durable queue to the done-gate. Returns (orchestrator, client, emitted)."""
    monkeypatch.delenv("HARNESS_VERIFICATION_FIRST", raising=False)  # the default IS the law
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "calc.py").write_text(SEED_CALC)
    (seed / "test_calc.py").write_text(SEED_TEST)
    monkeypatch.setenv("HARNESS_WORKSPACE_SEED", str(seed))

    engine = DeepSeekHarnessWrapper(
        backend_runner=runtime,
        workspace_dir=str(tmp_path / "workspaces"),
        session_root=str(tmp_path / "sessions"),
        isolated=True,  # staging (B3) applies to isolated engines: the production default
    )
    runtime.engine, runtime.run_id = engine, run_id
    emitted: list[tuple[str, dict]] = []
    orchestrator = OrchestratorService(
        reviewer=ReviewerSubAgent(model_router=router),
        engine=engine,
        on_event=lambda etype, payload: emitted.append((etype, payload)),
        session_factory=flow_db,
        bus=EventBus(flow_db),  # S1 owns delivery: agent_events persistence + replay
    )
    monkeypatch.setattr(orch_api, "_service", orchestrator)
    app = FastAPI()
    app.include_router(orch_router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=LEADER, teamId=TEAM, role=Role.ENGINEER
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cp1")

    plan_id = await orchestrator.propose_plan(
        run_id, goal="refactor calc into a table", project_id=PROJECT
    )
    await orchestrator.approve_plan(run_id, plan_id, principal_user_id=LEADER)
    await orchestrator.start_execution(run_id)  # enqueues: run_claims is migrated
    assert await orchestrator.poll_once() == run_id  # the worker path claims + spawns
    await orchestrator.runs[run_id]["task"]
    return orchestrator, client, emitted


async def _drain(orchestrator) -> None:
    """Wait for the service's ordered bus publishes to land."""
    while orchestrator._bg_tasks:
        await asyncio.gather(*list(orchestrator._bg_tasks), return_exceptions=True)


def _done(client, run_id):
    return client.post(f"{P}/{run_id}/done-signal", json={"diffText": "+refactor calc"})


async def _staged_approval(client, run_id) -> dict:
    res = await client.get(f"{P}/{run_id}/approvals")
    assert res.status_code == 200, res.text
    staged = [a for a in res.json()["approvals"] if a["requester"] == "system:staging"]
    assert len(staged) == 1, staged
    return staged[0]


@pytest.mark.asyncio
async def test_checkpoint1_full_flow(flow_db, router, sandbox, tmp_path, monkeypatch):
    run_id = "run-int-001"
    runtime = ScratchRuntime(sandbox, new_calc=AGENT_CALC)
    orchestrator, client, emitted = await _flow(
        tmp_path, monkeypatch, flow_db, router, sandbox, runtime, run_id
    )
    ws = Path(orchestrator.engine.run_workspace(run_id))

    # The engine ran the scratch repo's tests for real, in S5, green.
    assert [code for code, _ in runtime.test_runs] == [0]
    assert "1 passed" in runtime.test_runs[0][1]
    # ...and the live writer recorded that call as the run's own evidence.
    async with flow_db() as db:
        calls = (await db.execute(text(
            "SELECT tool, target, summary, outcome, reported_by FROM agent_tool_calls"
            " WHERE run_id = :r"), {"r": run_id})).all()
    assert [(c.tool, c.target, c.summary, c.outcome, c.reported_by) for c in calls] == [
        ("bash", PYTEST, "engine bash call (exit 0)", "ok", "engine")
    ]

    # Law 1: the substantial rewrite is WITHHELD — the workspace still has the seed.
    staged = orchestrator.runs[run_id]["staged"]
    assert [g["paths"] for g in staged["withheld"]] == [["calc.py"]]
    assert (ws / "calc.py").read_text() == SEED_CALC

    async with client:
        res = await client.post(f"{P}/{run_id}/leader")
        assert res.status_code == 200, res.text
        # Refusal 1: done before approval -> the staged gate's specific 409.
        res = await _done(client, run_id)
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["error"]["code"] == "STAGED_APPROVAL_PENDING"

        approval = await _staged_approval(client, run_id)
        assert approval["state"] == "pending" and approval["paths"] == ["calc.py"]
        res = await client.post(
            f"{P}/{run_id}/approvals/{approval['id']}/decision",
            json={"approve": True, "digest": approval["digest"], "viewed_detail": True},
        )
        assert res.status_code == 200, res.text
        assert res.json()["state"] == "approved" and res.json()["decidedBy"] == LEADER
        async with flow_db() as db:
            audit = (await db.execute(text(
                "SELECT detail FROM audit_log WHERE action = 'approval.approved'"))).all()
        assert len(audit) == 1 and json.loads(audit[0].detail)["viewedDetail"] is True

        # Refusal 2: approved but not applied is still not done.
        res = await _done(client, run_id)
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["error"]["code"] == "STAGED_APPROVAL_PENDING"

        res = await client.post(
            f"{P}/{run_id}/approvals/{approval['id']}/apply", json={"digest": approval["digest"]}
        )
        assert res.status_code == 200, res.text
        assert res.json()["state"] == "completed"
        assert (ws / "calc.py").read_text() == AGENT_CALC  # the approved bytes landed

        # The applied tree is still green (same S5 path, not recorded: evidence is the run's own).
        assert _sandbox_pytest(sandbox, "post-apply-check", ws)[0] == 0

        # All three laws satisfied -> the gate opens and the reviewer passes.
        res = await _done(client, run_id)
        assert res.status_code == 200, res.text
        findings = res.json()["findings"]
        assert findings["passed"] is True, findings

    # S1 delivery: every lifecycle event the service emitted is in the durable log,
    # in causal order, with the frozen envelope — the run was driven through the bus.
    await _drain(orchestrator)
    names = [e.value if hasattr(e, "value") else e for e, _ in emitted]
    assert [n for n in names if n in LIFECYCLE] == [
        "plan_proposed",
        "plan_approved",
        "execution_started",
        "engine_completed",
        "done_signal_received",
        "review_passed",
    ], names
    assert set(names) <= {t.value for t in contracts.EVENT_TYPES}
    replayed = await EventBus(flow_db).replay(PROJECT, after_seq=0)
    assert [e.type.value for e in replayed if e.runId == run_id] == names
    for env in replayed:
        assert {"type", "payload", "ts", "seq", "projectId", "runId", "schemaVersion"} == set(
            env.model_dump()
        )

    review_calls = [c for c in router.call_history if c["role"] == "reviewer"]
    assert len(review_calls) >= 1, "reviewer must call models through Section 3"


@pytest.mark.asyncio
async def test_checkpoint1_refuses_done_without_recorded_tests(
    flow_db, router, sandbox, tmp_path, monkeypatch
):
    """Law 2: approve + apply alone never opens the gate — no recorded green run, no done."""
    run_id = "run-int-002"
    runtime = ScratchRuntime(sandbox, new_calc=AGENT_CALC, run_tests=False)
    orchestrator, client, _ = await _flow(
        tmp_path, monkeypatch, flow_db, router, sandbox, runtime, run_id
    )
    async with client:
        res = await client.post(f"{P}/{run_id}/leader")
        assert res.status_code == 200, res.text
        approval = await _staged_approval(client, run_id)
        await client.post(
            f"{P}/{run_id}/approvals/{approval['id']}/decision",
            json={"approve": True, "digest": approval["digest"]},
        )
        res = await client.post(
            f"{P}/{run_id}/approvals/{approval['id']}/apply", json={"digest": approval["digest"]}
        )
        assert res.json()["state"] == "completed"
        res = await _done(client, run_id)
        assert res.status_code == 400, res.text
        assert res.json()["detail"]["error"]["code"] == "VERIFICATION_REQUIRED"

    # Law 3: even a caller that skips the route's gate gets no pass from the reviewer.
    findings = await orchestrator.signal_done(run_id, diff_text="+refactor calc")
    assert findings.passed is False
    assert "No green test evidence" in findings.summary


@pytest.mark.asyncio
async def test_checkpoint1_red_tests_never_pass(flow_db, router, sandbox, tmp_path, monkeypatch):
    """Laws 2+3: a real RED run is recorded as red — the gate and the reviewer both refuse."""
    run_id = "run-int-003"
    runtime = ScratchRuntime(sandbox, new_calc=BROKEN_CALC)
    orchestrator, client, _ = await _flow(
        tmp_path, monkeypatch, flow_db, router, sandbox, runtime, run_id
    )
    assert runtime.test_runs[0][0] != 0  # pytest really failed in the sandbox
    async with client:
        res = await client.post(f"{P}/{run_id}/leader")
        assert res.status_code == 200, res.text
        approval = await _staged_approval(client, run_id)
        await client.post(
            f"{P}/{run_id}/approvals/{approval['id']}/decision",
            json={"approve": True, "digest": approval["digest"]},
        )
        await client.post(
            f"{P}/{run_id}/approvals/{approval['id']}/apply", json={"digest": approval["digest"]}
        )
        res = await _done(client, run_id)
        assert res.status_code == 400, res.text
        assert res.json()["detail"]["error"]["code"] == "VERIFICATION_REQUIRED"
    findings = await orchestrator.signal_done(run_id, diff_text="+refactor calc")
    assert findings.passed is False
    assert "Automated test suite failed." in findings.summary


@pytest.mark.asyncio
async def test_orchestrator_rehydrates_state_from_event_log():
    """dsh-inspired core: agent_events is the source of truth. A fresh S4 process
    with only a DB handle rebuilds run state by replaying persisted events."""
    from harness_identity.eventbus import EventBus
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
    from sqlalchemy.ext.asyncio import create_async_engine as _cae

    eng = _cae("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await _build_schema(conn)
    factory = _asm(eng, expire_on_commit=False)

    producer_bus = EventBus(factory)
    rid = "run-rehydrate-001"
    await producer_bus.ensure_run("proj-re", rid)
    await producer_bus.publish(
        EventType.PLAN_PROPOSED, "proj-re", {"planId": "plan-rx", "runId": rid}, rid
    )
    await producer_bus.publish(
        EventType.PLAN_APPROVED, "proj-re", {"planId": "plan-rx", "runId": rid}, rid
    )
    await producer_bus.publish(EventType.EXECUTION_STARTED, "proj-re", {"runId": rid}, rid)
    await producer_bus.publish(EventType.PAUSED, "proj-re", {"runId": rid}, rid)

    fresh_service = OrchestratorService(session_factory=factory)  # empty memory, DB only
    status = await fresh_service.get_status(rid)
    assert status["state"] == "paused"
    assert status["planApproved"] is True
    assert status["planId"] == "plan-rx"

    # FK safety: publishing with an unknown run_id must not explode (ensure_run seeds).
    await producer_bus.publish(EventType.RESUMED, "proj-re", {"runId": "brand-new-run"}, "brand-new-run")

    await eng.dispose()



@pytest.mark.asyncio
async def test_cross_instance_live_fanout_via_pg_notify():
    """G-LIVE: two EventBus instances over ONE Postgres DB must still deliver
    live events to each other's subscribers (production runs one bus instance
    per service process). Skips on sqlite drivers (no NOTIFY support)."""
    import os

    if os.environ.get("HARNESS_TEST_PG") != "1":
        pytest.skip("requires Postgres (set HARNESS_TEST_PG=1)")

    from harness_identity.eventbus import EventBus
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm2
    from sqlalchemy.ext.asyncio import create_async_engine as _cae2

    url = os.environ["HARNESS_DATABASE_URL"]
    eng_a = _cae2(url)
    eng_b = _cae2(url)
    factory_a = _asm2(eng_a, expire_on_commit=False)
    factory_b = _asm2(eng_b, expire_on_commit=False)

    # Seed real parent rows — against PG the frozen FKs are enforced.
    async with eng_a.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO teams (id, name) VALUES ('team-x', 'fanout') "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO projects (id, team_id, name) "
                "VALUES ('proj-x', 'team-x', 'fanout-test') ON CONFLICT (id) DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO agent_runs (id, project_id, state) "
                "VALUES ('run-x', 'proj-x', 'idle') ON CONFLICT (id) DO NOTHING"
            )
        )

    bus_publisher = EventBus(factory_a)   # lives in "S5's process"
    bus_subscriber = EventBus(factory_b)  # lives in "S1's process"

    q = await bus_subscriber.subscribe("proj-x")

    await bus_publisher.publish(EventType.EXECUTION_STARTED, "proj-x", {"runId": "run-x"}, "run-x")

    env = await asyncio.wait_for(q.get(), timeout=5)
    assert env.type == EventType.EXECUTION_STARTED
    assert env.seq >= 1

    await eng_a.dispose()
    await eng_b.dispose()
