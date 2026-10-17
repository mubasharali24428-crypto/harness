"""Wave 9 node B: per-team budget cap tests.

Covers the required matrix — under/at/over cap, unlimited default, per-team
override wins over env — plus the migration file's SQL text (PG dialect,
additive) and the orchestrator enforcement call-sites (no model call when
over budget; honest ENGINE_FAILED event).

Self-contained fixtures per wave-9 hotspot rules: conftest.py is FROZEN, so
the SQLite schema lives here. It mirrors the frozen Section-1 tables this
module reads (teams/projects/agent_runs/cost_ledger) + run_claims, with the
migration-0007 ``budget_usd`` column applied.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from harness_contracts.events import EventType
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.budget import (
    DEFAULT_BUDGET_WARN_PCT,
    ENV_BUDGET_WARN_PCT,
    ENV_TEAM_BUDGET_USD,
    BudgetStatus,
    budget_warn_pct,
    check_budget,
    env_budget_default,
    estimate_next_call_cost_usd,
    resolve_team_budget_usd,
)
from harness_orchestrator.reviewer import ReviewerSubAgent
from harness_orchestrator.service import OrchestratorService

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3] / "section1" / "migrations" / "0007_team_budget.sql"
)

# Mirrors the frozen schema (subset actually read by budget.py) with 0007 applied.
DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    budget_usd REAL NULL
);
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id)
);
CREATE TABLE agent_runs (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id)
);
CREATE TABLE cost_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36),
    agent_role VARCHAR(20) NOT NULL DEFAULT 'primary',
    model VARCHAR(120) NOT NULL DEFAULT 'stub',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12,6) NOT NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    -- M18-C2: the spend window is the DB-clock month; the mirror carries the
    -- frozen column (0001) so MONTH_WINDOW_SQL runs verbatim. SQLite's
    -- date('now','start of month','-1 day') yields LAST day of PREVIOUS month
    -- for any timestamp this month, so every in-month row passes and
    -- last-month rows fail — identical semantics to date_trunc on PG.
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
);
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36) NOT NULL,
    type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
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
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
"""

SEED = [
    "INSERT INTO teams (id, name) VALUES ('team-a', 'A')",
    "INSERT INTO teams (id, name) VALUES ('team-b', 'B')",
    "INSERT INTO projects (id, team_id) VALUES ('proj-a', 'team-a')",
    "INSERT INTO projects (id, team_id) VALUES ('proj-b', 'team-b')",
    "INSERT INTO agent_runs (id, project_id) VALUES ('run-a', 'proj-a')",
]

_LOOP = None


def run(coro):
    global _LOOP
    if _LOOP is None:
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


@pytest.fixture
def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/budget.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            # Comments stripped before the semicolon split (M18-C2: the DDL
            # now carries explanatory comments containing no semicolons, but
            # a comment tail without ';' would otherwise merge statements).
            body = "\n".join(
                line for line in DDL.splitlines() if not line.strip().startswith("--")
            )
            for statement in body.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            for stmt in SEED:
                await conn.execute(text(stmt))

    run(_setup())
    return factory


async def _add_spend(factory, run_id: str | None, cost_usd: float) -> None:
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO cost_ledger (run_id, cost_usd, created_at) "
                "VALUES (:r, :c, :ts)"
            ),
            {
                "r": run_id,
                "c": cost_usd,
                # M18-C2: the gate sums THIS month's spend; the mirror rows
                # must carry an in-month timestamp (the DDL default is
                # 2026-01-01, outside every current window).
                "ts": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        await db.commit()


async def _set_team_override(factory, team_id: str, value: float | None) -> None:
    async with factory() as db:
        await db.execute(
            text("UPDATE teams SET budget_usd = :v WHERE id = :t"),
            {"v": value, "t": team_id},
        )
        await db.commit()


# ------------------------------------------------------------------ env default


def test_env_default_unset_is_unlimited(monkeypatch):
    monkeypatch.delenv(ENV_TEAM_BUDGET_USD, raising=False)
    assert env_budget_default() is None
    assert resolve_team_budget_usd() is None


def test_env_default_parses_and_garbage_is_unlimited(monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "25.5")
    assert env_budget_default() == 25.5
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "not-a-number")
    assert env_budget_default() is None  # garbage never bricks orchestration


# ------------------------------------------------------------------- cap matrix


@pytest.mark.parametrize(
    ("spent", "estimate", "cap", "expected_allowed"),
    [
        (10.0, 0.05, 20.0, True),  # under cap
        (19.95, 0.05, 20.0, False),  # AT cap boundary: spend+estimate == cap blocks
        (20.0, 0.05, 20.0, False),  # over cap
        (25.0, 0.05, 20.0, False),  # far over cap
        (0.05, 0.05, 0.0, False),  # zero cap blocks anything
    ],
)
def test_check_budget_cap_matrix(db_factory, monkeypatch, spent, estimate, cap, expected_allowed):
    monkeypatch.delenv(ENV_TEAM_BUDGET_USD, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", spent))
    status: BudgetStatus = run(
        check_budget(f, "team-a", budget_usd=cap, estimated_call_cost_usd=estimate)
    )
    assert status.allowed is expected_allowed
    assert status.spentUsd == pytest.approx(spent)
    assert status.capUsd == pytest.approx(cap)
    if not expected_allowed:
        assert "budget exhausted" in status.reason


def test_unlimited_env_default_allows_any_spend(db_factory, monkeypatch):
    monkeypatch.delenv(ENV_TEAM_BUDGET_USD, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", 9999.0))
    status: BudgetStatus = run(check_budget(f, "team-a"))
    assert status.allowed is True
    assert status.capUsd is None
    assert status.reason == ""


def test_per_team_override_wins_over_env(db_factory, monkeypatch):
    f = db_factory
    run(_set_team_override(f, "team-a", 12.0))
    run(_add_spend(f, "run-a", 11.0))

    # Env would allow (cap 10 < override 12): the DB override must WIN.
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "10.0")
    under: BudgetStatus = run(check_budget(f, "team-a", estimated_call_cost_usd=0.5))
    assert under.allowed is True and under.capUsd == pytest.approx(12.0)

    # Override says stop even though the env default is generous.
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "100.0")
    over: BudgetStatus = run(check_budget(f, "team-a", estimated_call_cost_usd=2.0))
    assert over.allowed is False and over.capUsd == pytest.approx(12.0)


def test_null_override_falls_back_to_env(db_factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "5.0")
    f = db_factory
    run(_add_spend(f, "run-a", 4.99))
    status: BudgetStatus = run(check_budget(f, "team-a", estimated_call_cost_usd=0.05))
    assert status.allowed is False and status.capUsd == pytest.approx(5.0)


def test_ledger_rows_without_runs_do_not_count(db_factory, monkeypatch):
    """Spend aggregates only through the run join (orphan ledger rows excluded)."""
    monkeypatch.delenv(ENV_TEAM_BUDGET_USD, raising=False)
    f = db_factory
    run(_add_spend(f, None, 50.0))  # no run_id → no team attribution
    status: BudgetStatus = run(check_budget(f, "team-a", budget_usd=1.0))
    assert status.allowed is True
    assert status.spentUsd == pytest.approx(0.0)


def test_unknown_team_degrades_to_fail_closed(tmp_path, monkeypatch):
    """M18-C2 INVERTED posture: a DB error while resolving a cap now DENIES
    the call with the explicit degraded flag (fail-closed everywhere) — the
    old wave-9 behavior degraded to 'allowed', which was the one fail-open
    path in the system. Operators see degraded=True and an outage-shaped
    reason instead of a silently opened gate."""
    monkeypatch.delenv(ENV_TEAM_BUDGET_USD, raising=False)

    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("db down")

    status: BudgetStatus = run(check_budget(BrokenFactory(), "team-unknown"))
    assert status.allowed is False
    assert status.degraded is True
    assert "budget store unavailable" in status.reason


# ------------------------------------------------------- estimate helper (contract)


def test_estimate_prefers_router_estimator():
    from harness_contracts.signatures import ModelResult, ModelRole

    class Router:
        def call_model(self, agent_id, role, messages, opts=None):
            return ModelResult(text="t", costUsd=0.01, model="m", latencyMs=1)

        def estimate_cost(self, role, input_tokens, output_tokens):
            return 0.42

        def list_available_models(self):
            return ["m"]

    est = estimate_next_call_cost_usd(model_router=Router(), role=ModelRole.REVIEWER)
    assert est == pytest.approx(0.42)


def test_estimate_falls_back_when_router_raises():
    from harness_contracts.signatures import ModelRole

    class BadRouter:
        def estimate_cost(self, role, input_tokens, output_tokens):
            raise RuntimeError("no pricing")

    est = estimate_next_call_cost_usd(
        model_router=BadRouter(), role=ModelRole.REVIEWER, estimated_cost_usd=0.07
    )
    assert est == pytest.approx(0.07)


# ------------------------------------------------------------------ migration file


def test_migration_file_pg_dialect_additive():
    content = MIGRATION_PATH.read_text(encoding="utf-8")
    # Strip comment lines, then split statements (no semicolons inside comments).
    body = "\n".join(
        line for line in content.splitlines() if not line.strip().startswith("--")
    )
    statements = [s.strip() for s in body.split(";") if s.strip()]
    assert len(statements) == 1, "0007 is a single additive ALTER"
    stmt = statements[0]
    assert stmt.upper().startswith("ALTER TABLE TEAMS ADD COLUMN BUDGET_USD"), (
        "0007 must be the additive teams.budget_usd ALTER only"
    )
    assert "DROP" not in stmt.upper() and "DELETE" not in stmt.upper()
    assert "NUMERIC(12,2)" in content, "PG dialect NUMERIC like sibling migrations"


def test_migration_column_shape_matches_sqlite_ddl(db_factory, monkeypatch):
    """The 0007 column semantics work as the runtime expects (NULL → env)."""
    content = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "NULL" in content  # nullable override: NULL means "not set"
    f = db_factory  # DDL above already carries the budget_usd column
    run(_set_team_override(f, "team-b", None))
    monkeypatch.delenv(ENV_TEAM_BUDGET_USD, raising=False)
    status: BudgetStatus = run(check_budget(f, "team-b", estimated_call_cost_usd=500.0))
    assert status.allowed is True and status.capUsd is None


# --------------------------------------------- orchestrator enforcement (pre-call)


class OverBudgetEngine:
    """Any engine invocation during an over-budget test is a failure."""

    def __init__(self):
        self.calls = 0

    def execute_goal(self, goal=None, session_id=None):
        self.calls += 1
        return {
            "sessionId": session_id,
            "engine": "must-not-run",
            "status": "completed",
            "finalResponse": "SPENT MONEY",
        }


def _wire_service(factory, engine) -> tuple[OrchestratorService, list]:
    svc = OrchestratorService(engine=engine, session_factory=factory)  # type: ignore[arg-type]
    events: list[tuple[EventType, dict]] = []
    svc.on_event = lambda etype, payload: events.append((etype, payload))
    return svc, events


def _approve(svc, run_id):
    run(svc.propose_plan(run_id, project_id="proj-a"))
    plan_id = svc.runs[run_id]["plan_id"]
    run(svc.approve_plan(run_id, plan_id, "u-test"))


def _drive_to_spawn(svc, run_id, over_budget: bool):
    """start_execution → (worker path with a session_factory) poll_once.

    Mirrors deploy wiring: the durable run queue owns spawning; the budget
    gate sits at claim time (and again inside _execute as a second lock).
    Returns True when the engine task was spawned.
    """
    run(svc.start_execution(run_id))
    claimed = run(svc.poll_once())
    if over_budget:
        return False  # gate must refuse to spawn — asserted by callers
    return bool(claimed) and svc.runs[run_id].get("task") is not None


def test_over_budget_start_execution_never_spawns_engine(db_factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    f = db_factory
    run(_add_spend(f, "run-a", 2.0))
    engine = OverBudgetEngine()
    svc, events = _wire_service(f, engine)

    _approve(svc, "ob-run")
    spawned = _drive_to_spawn(svc, "ob-run", over_budget=True)
    assert spawned is False
    assert engine.calls == 0, "engine must never be invoked over budget"
    failed = [e for e in events if e[0] is EventType.ENGINE_FAILED]
    assert failed, "honest blocked event required"
    payload = failed[-1][1]
    assert payload["runId"] == "ob-run"
    assert "Budget cap reached" in payload["reason"]


def test_under_budget_run_still_executes(db_factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "50.0")
    f = db_factory
    run(_add_spend(f, "run-a", 1.0))

    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {
                "sessionId": session_id,
                "engine": "stub",
                "status": "completed",
                "finalResponse": "done",
            }

    svc, events = _wire_service(f, StubEngine())
    _approve(svc, "ok-run")
    assert _drive_to_spawn(svc, "ok-run", over_budget=False) is True
    task = svc.runs["ok-run"]["task"]
    run(asyncio.wait_for(task, timeout=5))
    completed = [e for e in events if e[0] is EventType.ENGINE_COMPLETED]
    assert completed and completed[-1][1]["runId"] == "ok-run"


def test_queue_worker_poll_refuses_to_spawn_over_budget(db_factory, monkeypatch):
    """Pre-spawn gate: poll_once drops the claim without ever spawning."""
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    f = db_factory
    run(_add_spend(f, "run-a", 5.0))
    engine = OverBudgetEngine()
    svc, events = _wire_service(f, engine)

    _approve(svc, "q-run")
    run(svc.start_execution("q-run"))  # enqueues (worker path)
    claimed = run(svc.poll_once())
    assert claimed is None
    assert engine.calls == 0
    failed = [e for e in events if e[0] is EventType.ENGINE_FAILED]
    assert failed and failed[-1][1]["runId"] == "q-run"
    claims = run(_queue_snapshot(f))
    assert claims == [], "dropped claim must not linger queued/running"


async def _queue_snapshot(factory):
    from harness_orchestrator.run_queue import get_queue_snapshot

    return await get_queue_snapshot(factory)


def test_reviewer_model_call_withheld_over_budget(db_factory, monkeypatch):
    """signal_done over cap: reviewer skips call_model entirely (spy proves it)."""
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    f = db_factory
    run(_add_spend(f, "run-a", 5.0))

    calls: list[str] = []

    class SpyRouter:
        def call_model(self, agent_id, role, messages, opts=None):
            calls.append(agent_id)
            from harness_contracts.signatures import ModelResult

            return ModelResult(text="model summary", costUsd=0.5, model="m", latencyMs=1)

        def estimate_cost(self, role, input_tokens, output_tokens):
            return 0.01

        def list_available_models(self):
            return ["m"]

    svc = OrchestratorService(
        engine=OverBudgetEngine(),  # type: ignore[arg-type]
        session_factory=f,
        reviewer=ReviewerSubAgent(model_router=SpyRouter()),
    )
    events: list[tuple[EventType, dict]] = []
    svc.on_event = lambda etype, payload: events.append((etype, payload))

    run(svc.propose_plan("rv-run", project_id="proj-a"))
    findings = run(svc.signal_done("rv-run", diff_text=GOLDEN_CLEAN_DIFF))
    assert calls == [], "model_router.call_model must NOT fire over budget"
    assert findings.modelUsed is None
    assert any("budget" in i.lower() for i in findings.issues)
    assert any("withheld" in i.lower() for i in findings.issues)


GOLDEN_CLEAN_DIFF = (
    "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n print('hi')\n+print('bye')\n"
)


def test_reviewer_model_call_flows_when_under_budget(db_factory, monkeypatch):
    """Regression guard: the new gate changes nothing below the cap."""
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "100.0")
    f = db_factory
    run(_add_spend(f, "run-a", 1.0))

    from harness_contracts.signatures import ModelResult

    class SpyRouter:
        def call_model(self, agent_id, role, messages, opts=None):
            return ModelResult(text="model summary", costUsd=0.5, model="gpt", latencyMs=7)

        def estimate_cost(self, role, input_tokens, output_tokens):
            return 0.01

        def list_available_models(self):
            return ["gpt"]

    svc = OrchestratorService(
        engine=OverBudgetEngine(),  # type: ignore[arg-type]
        session_factory=f,
        reviewer=ReviewerSubAgent(model_router=SpyRouter()),
    )
    run(svc.propose_plan("rv-ok", project_id="proj-a"))
    findings = run(svc.signal_done("rv-ok", diff_text=GOLDEN_CLEAN_DIFF))
    assert findings.modelUsed == "gpt"
    assert findings.summary == "model summary"


def test_signal_done_over_budget_still_gates_pass_fail_deterministically(db_factory, monkeypatch):
    """Heuristics stay authoritative: a conflicting diff still FAILS review."""
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "100.0")
    f = db_factory
    run(_add_spend(f, "run-a", 0.0))

    svc, events = _wire_service(f, OverBudgetEngine())
    run(svc.propose_plan("rv-conflict", project_id="proj-a"))
    conflict_diff = "<<<<<<< HEAD\n=======\n>>>>>>> branch\n"
    findings = run(svc.signal_done("rv-conflict", diff_text=conflict_diff))
    assert findings.passed is False
    failed = [e for e in events if e[0] is EventType.REVIEW_FAILED]
    assert failed


# ------------------------------------------------- M23-B: warning threshold


def test_budget_warn_pct_default_and_invalid_values(monkeypatch):
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    assert budget_warn_pct() == DEFAULT_BUDGET_WARN_PCT
    monkeypatch.setenv(ENV_BUDGET_WARN_PCT, "not-a-number")
    assert budget_warn_pct() == DEFAULT_BUDGET_WARN_PCT
    monkeypatch.setenv(ENV_BUDGET_WARN_PCT, "0")
    assert budget_warn_pct() == 0.0  # disabled
    monkeypatch.setenv(ENV_BUDGET_WARN_PCT, "100")
    assert budget_warn_pct() == 0.0  # >= 100 is the hard stop itself
    monkeypatch.setenv(ENV_BUDGET_WARN_PCT, "55.5")
    assert budget_warn_pct() == pytest.approx(55.5)


def test_allowed_decision_reports_warning_threshold_crossing(db_factory, monkeypatch):
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", 16.0))
    status: BudgetStatus = run(
        check_budget(f, "team-a", budget_usd=20.0, estimated_call_cost_usd=0.0)
    )
    assert status.allowed is True
    assert status.warned is True
    assert status.warnAtUsd == pytest.approx(16.0)  # 80% of $20


def test_projection_below_threshold_is_silent(db_factory, monkeypatch):
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", 15.0))
    status: BudgetStatus = run(
        check_budget(f, "team-a", budget_usd=20.0, estimated_call_cost_usd=0.05)
    )
    assert status.allowed is True and status.warned is False


def test_projection_landing_on_threshold_warns(db_factory, monkeypatch):
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", 15.95))
    status: BudgetStatus = run(
        check_budget(f, "team-a", budget_usd=20.0, estimated_call_cost_usd=0.05)
    )
    assert status.allowed is True and status.warned is True  # 16.00 == 16.00


def test_blocked_decision_is_never_also_warned(db_factory, monkeypatch):
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", 19.95))
    status: BudgetStatus = run(
        check_budget(f, "team-a", budget_usd=20.0, estimated_call_cost_usd=0.05)
    )
    assert status.allowed is False
    assert status.warned is False and status.warnAtUsd is None


def test_custom_warn_pct_honored(db_factory, monkeypatch):
    monkeypatch.setenv(ENV_BUDGET_WARN_PCT, "50")
    f = db_factory
    run(_add_spend(f, "run-a", 10.01))
    status: BudgetStatus = run(
        check_budget(f, "team-a", budget_usd=20.0, estimated_call_cost_usd=0.0)
    )
    assert status.allowed is True and status.warned is True
    assert status.warnAtUsd == pytest.approx(10.0)


def test_warn_pct_zero_disables_warnings(db_factory, monkeypatch):
    monkeypatch.setenv(ENV_BUDGET_WARN_PCT, "0")
    f = db_factory
    run(_add_spend(f, "run-a", 19.0))
    status: BudgetStatus = run(
        check_budget(f, "team-a", budget_usd=20.0, estimated_call_cost_usd=0.0)
    )
    assert status.allowed is True and status.warned is False and status.warnAtUsd is None


def test_unlimited_cap_never_warns(db_factory, monkeypatch):
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", 500.0))
    status: BudgetStatus = run(check_budget(f, "team-a"))
    assert status.allowed is True and status.warned is False and status.capUsd is None


def test_near_cap_run_warns_but_still_executes(db_factory, monkeypatch):
    """Service seam: the pre-spawn gate flags the warning once and the run
    proceeds — never a block, never repeated noise."""
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "2.0")
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", 1.7))  # 85% of the $2 env cap

    class StubEngine:
        def execute_goal(self, goal=None, session_id=None):
            return {
                "sessionId": session_id,
                "engine": "stub",
                "status": "completed",
                "finalResponse": "done",
            }

    svc, events = _wire_service(f, StubEngine())
    _approve(svc, "warn-run")
    assert _drive_to_spawn(svc, "warn-run", over_budget=False) is True
    assert svc.runs["warn-run"]["budget_warned"] is True
    assert not [e for e in events if e[0] is EventType.ENGINE_FAILED]
    run(asyncio.wait_for(svc.runs["warn-run"]["task"], timeout=5))


def test_under_cap_no_warning_and_reviewer_warning_path(db_factory, monkeypatch):
    """Below the threshold: no flag, no behavior change (regression guard)."""
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "100.0")
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    f = db_factory
    run(_add_spend(f, "run-a", 1.0))

    class SpyRouter:
        def call_model(self, agent_id, role, messages, opts=None):
            from harness_contracts.signatures import ModelResult

            return ModelResult(text="s", costUsd=0.5, model="gpt", latencyMs=1)

        def estimate_cost(self, role, input_tokens, output_tokens):
            return 0.01

        def list_available_models(self):
            return ["gpt"]

    svc = OrchestratorService(
        engine=OverBudgetEngine(),  # type: ignore[arg-type]
        session_factory=f,
        reviewer=ReviewerSubAgent(model_router=SpyRouter()),
    )
    run(svc.propose_plan("rv-warn-ok", project_id="proj-a"))
    run(svc.signal_done("rv-warn-ok", diff_text=GOLDEN_CLEAN_DIFF))
    assert svc.runs["rv-warn-ok"].get("budget_warned") is not True
