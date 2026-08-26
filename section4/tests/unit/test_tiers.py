"""M22-M2 — tier enforcement + upgrade CTA tests.

Covers the required matrix:

1. per-tier enforcement (free capped / pro+team unlimited) for the runs/day
   gate at run start (section4) and the context-index size cap (section11);
2. the cap-hit payload SHAPE: {error: envelope, upgrade: {tier, cap,
   capField, used, upgradeTo, cta}} — an upgrade action, never a dead end;
3. free-tier degradation honesty: absent/unreadable plan rows apply the FREE
   defaults (never silently pro), a broken store fails CLOSED with
   degraded=True, env knobs tune existing caps but never introduce caps on
   dimensions a tier buys unlimited.

Self-contained SQLite mirrors (conftest.py is FROZEN): DDL mirrors the frozen
tables the gates read (teams/projects/agent_runs/billing_plans with 0031
applied). The runs/day window uses date_trunc('day', now()) exactly like
production SQL; the mirror registers equivalent user functions.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from harness_contracts import (
    DEFAULT_AUDIT_RETENTION_DAYS,
    DEFAULT_CONTEXT_INDEX_MAX_CHUNKS,
    DEFAULT_RUNS_PER_DAY,
    DEFAULT_SEAT_LIMITS,
    ENV_TIER_CONTEXT_CHUNKS,
    ENV_TIER_RUNS_PER_DAY,
    PLAN_TIERS,
    TIER_CAP_FIELDS,
    TIER_INFO_FIELDS,
    UPGRADE_WIRE,
    TierCaps,
    cap_hit_wire,
    parse_tier,
    tier_caps,
)
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.budget import check_tier
from harness_orchestrator.service import OrchestratorService

_LOOP = None


def run(coro):
    global _LOOP
    if _LOOP is None:
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


# ----------------------------------------------------------------- contracts


def test_tier_defaults_matrix():
    """Free is capped on both enforced dimensions; pro/team are not."""
    assert DEFAULT_RUNS_PER_DAY["free"] > 0
    assert DEFAULT_RUNS_PER_DAY["pro"] is None
    assert DEFAULT_RUNS_PER_DAY["team"] is None
    assert DEFAULT_CONTEXT_INDEX_MAX_CHUNKS["free"] > 0
    assert DEFAULT_CONTEXT_INDEX_MAX_CHUNKS["pro"] is None
    assert DEFAULT_CONTEXT_INDEX_MAX_CHUNKS["team"] is None
    # Team = Pro + seats + longer audit retention.
    assert DEFAULT_SEAT_LIMITS["team"] > DEFAULT_SEAT_LIMITS["pro"]
    assert DEFAULT_SEAT_LIMITS["pro"] >= DEFAULT_SEAT_LIMITS["free"]
    assert (
        DEFAULT_AUDIT_RETENTION_DAYS["team"]
        > max(
            DEFAULT_AUDIT_RETENTION_DAYS["free"],
            DEFAULT_AUDIT_RETENTION_DAYS["pro"],
        )
    )
    # The ladder itself: cheapest first, team tops out the upgrade chain.
    assert PLAN_TIERS == ("free", "pro", "team")
    assert UPGRADE_WIRE == {"free": "pro", "pro": "team", "team": None}


def test_parse_tier_normalizes_and_falls_back_free():
    assert parse_tier("PRO") == "pro"
    assert parse_tier(" Team ") == "team"
    assert parse_tier(None) == "free"
    assert parse_tier("galactic") == "free"


def test_tier_caps_env_overrides_and_unlimited_stays_unlimited(monkeypatch):
    monkeypatch.delenv(ENV_TIER_RUNS_PER_DAY, raising=False)
    monkeypatch.delenv(ENV_TIER_CONTEXT_CHUNKS, raising=False)
    free = tier_caps("free")
    pro = tier_caps("pro")
    assert isinstance(free, TierCaps)
    assert free.upgrade_to == "pro" and pro.upgrade_to == "team"
    # A stray env knob must NOT downgrade pro from unlimited to a number.
    tuned_free = tier_caps("free", env={ENV_TIER_RUNS_PER_DAY: "5"})
    assert tuned_free.runs_per_day == 5
    assert tier_caps("pro", env={ENV_TIER_RUNS_PER_DAY: "5"}).runs_per_day is None
    assert tier_caps("pro", env={ENV_TIER_CONTEXT_CHUNKS: "9"}).context_index_max_chunks is None
    # Garbage env values are absent tiers, not errors.
    assert tier_caps("free", env={ENV_TIER_RUNS_PER_DAY: "garbage"}).runs_per_day == (
        DEFAULT_RUNS_PER_DAY["free"]
    )


def test_cap_hit_wire_shape():
    body = cap_hit_wire(
        tier="free", cap=20, cap_field="runsPerDay", used=20
    )
    # Frozen error envelope PLUS structured upgrade block — never bare.
    assert set(body) == {"error", "upgrade"}
    assert body["error"]["code"] == "TIER_CAP_REACHED"
    up = body["upgrade"]
    assert set(up) == {"tier", "cap", "capField", "used", "upgradeTo", "cta"}
    assert up["tier"] == "free"
    assert up["cap"] == 20 and up["used"] == 20
    assert up["capField"] in TIER_CAP_FIELDS
    assert up["upgradeTo"] == "pro"
    assert up["cta"]["action"] == "upgrade"
    # Top tier: nothing to sell — honest contact-the-operator degradation.
    top = cap_hit_wire(tier="team", cap=999, cap_field="seatLimit", used=1000)
    assert top["upgrade"]["upgradeTo"] is None
    assert "operator" in top["upgrade"]["cta"]["label"].lower()
    # Info fields stay descriptive; they are NOT the enforced set.
    assert set(TIER_INFO_FIELDS) == {"seatLimit", "auditRetentionDays"}
    assert TIER_CAP_FIELDS == ("runsPerDay", "contextIndexMaxChunks")


# ------------------------------------------------------------------- section4


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
    project_id VARCHAR(36) NOT NULL REFERENCES projects(id),
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
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
CREATE TABLE billing_plans (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) UNIQUE NOT NULL,
    plan VARCHAR(30) NOT NULL,
    monthly_budget_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
    -- migration 0031 columns:
    runs_per_day_override INTEGER,
    context_index_max_chunks_override INTEGER
);
"""

SEED = [
    "INSERT INTO teams (id, name) VALUES ('team-a', 'A')",
    "INSERT INTO teams (id, name) VALUES ('team-b', 'B')",
    "INSERT INTO projects (id, team_id) VALUES ('proj-a', 'team-a')",
]


@pytest.fixture
def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/tier.db")

    def _register(dbapi_conn, _rec=None):
        dbapi_conn.create_function("now", 0, lambda: datetime.now(UTC).isoformat())
        dbapi_conn.create_function("date_trunc", 2, lambda unit, value: value[:10])

    event.listen(engine.sync_engine, "connect", _register)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
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
    yield factory
    run(engine.dispose())


async def _set_plan(factory, plan: str, *, runs=None, chunks=None) -> None:
    """Upsert team-a's billing_plans row (mirror of PUT /billing/plan)."""
    async with factory() as db:
        await db.execute(text("DELETE FROM billing_plans WHERE team_id = 'team-a'"))
        await db.execute(
            text(
                "INSERT INTO billing_plans (id, team_id, plan,"
                " runs_per_day_override, context_index_max_chunks_override)"
                " VALUES ('bp-a', 'team-a', :plan, :r, :c)"
            ),
            {"plan": plan, "r": runs, "c": chunks},
        )
        await db.commit()


async def _add_run(factory, run_id: str, today: bool = True) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO agent_runs (id, project_id, created_at)"
                " VALUES (:rid, 'proj-a', :ts)"
            ),
            {"rid": run_id, "ts": stamp},
        )
        await db.commit()


@pytest.mark.parametrize("plan", ["free", "pro", "team"])
def test_per_tier_run_start_enforcement(db_factory, monkeypatch, plan):
    """THE matrix: at the free cap the run-start gate refuses; above it, not."""
    monkeypatch.delenv("HARNESS_TEAM_BUDGET_USD", raising=False)
    cap = DEFAULT_RUNS_PER_DAY[plan]
    f = db_factory
    run(_set_plan(f, plan))

    status = run(check_tier(f, "team-a"))
    assert status.allowed is True and status.tier == plan

    if cap is None:
        # Unlimited tier: even a pile of same-day runs never trips the gate.
        for i in range(5):
            run(_add_run(f, f"r-{plan}-{i}"))
        status = run(check_tier(f, "team-a"))
        assert status.allowed is True
        assert status.capHit is None
        return
    for i in range(cap - 1):
        run(_add_run(f, f"r-{plan}-{i}"))
    status = run(check_tier(f, "team-a"))
    assert status.allowed is True  # one slot left
    run(_add_run(f, f"r-{plan}-last"))
    status = run(check_tier(f, "team-a"))
    assert status.allowed is False  # cap reached
    assert status.degraded is False
    hit = status.capHit
    assert hit["upgrade"]["capField"] == "runsPerDay"
    assert hit["upgrade"]["used"] == cap and hit["upgrade"]["cap"] == cap


def test_missing_plan_row_degrades_honestly_to_free_defaults(db_factory, monkeypatch):
    """No billing row ⇒ free-tier enforcement applies (never silent pro)."""
    monkeypatch.delenv("HARNESS_TEAM_BUDGET_USD", raising=False)
    f = db_factory
    cap = DEFAULT_RUNS_PER_DAY["free"]
    for i in range(cap):
        run(_add_run(f, f"m-{i}"))
    status = run(check_tier(f, "team-a"))
    assert status.allowed is False
    assert status.capHit["upgrade"]["tier"] == "free"
    assert status.capHit["upgrade"]["upgradeTo"] == "pro"


def test_absent_billing_table_uses_free_defaults_not_outage(tmp_path, monkeypatch):
    """Pre-0010 scratch mirror: tier is ABSENT (defaults), not degraded."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/no-billing.db")

    def _register(dbapi_conn, _rec=None):
        dbapi_conn.create_function("now", 0, lambda: datetime.now(UTC).isoformat())
        dbapi_conn.create_function("date_trunc", 2, lambda unit, value: value[:10])

    event.listen(engine.sync_engine, "connect", _register)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            for ddl in (
                "CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200))",
                "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY,"
                " team_id VARCHAR(36) NOT NULL)",
                "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY,"
                " project_id VARCHAR(36) NOT NULL,"
                " created_at TEXT NOT NULL DEFAULT '2026-01-01')",
            ):
                await conn.execute(text(ddl))
            await conn.execute(text("INSERT INTO teams (id, name) VALUES ('t', 'T')"))
            await conn.execute(text("INSERT INTO projects (id, team_id) VALUES ('p', 't')"))

    run(setup())
    status = run(check_tier(factory, "t"))
    assert status.allowed is True
    assert status.degraded is False
    assert status.tier == "free"
    run(engine.dispose())


class ExplodingFactory:
    """The money store is DOWN: every session open dies (M18-C2 posture)."""

    def __call__(self):
        raise RuntimeError("db gone")


def test_broken_store_fails_closed_with_degraded_flag():
    status = run(check_tier(ExplodingFactory(), "team-a"))
    assert status.allowed is False
    assert status.degraded is True
    assert status.capHit is None  # no fabricated CTA during an outage


def test_override_columns_beat_tier_defaults(db_factory, monkeypatch):
    """0031 override columns win over the published default when non-NULL."""
    monkeypatch.delenv("HARNESS_TEAM_BUDGET_USD", raising=False)
    f = db_factory
    run(_set_plan(f, "free", runs=1, chunks=99))
    run(_add_run(f, "o-1"))
    status = run(check_tier(f, "team-a"))
    assert status.allowed is False
    assert status.capHit["upgrade"]["cap"] == 1


async def _drive_to_gate(factory, run_key: str):
    """Full production path: propose → approve → start_execution → poll_once.

    Mirrors deploy wiring exactly like unit/test_budget.py's enforcement
    tests: the durable queue owns spawning; the gate chain answers at claim
    time. Returns (service, events, spawned).
    """
    from harness_contracts.events import EventType

    svc = OrchestratorService(engine=_NoEngine(), session_factory=factory)
    events: list[tuple[EventType, dict]] = []
    svc.on_event = lambda etype, payload, project_id=None, run_id=None: events.append(
        (etype, payload)
    )
    await svc.propose_plan(run_key, project_id="proj-a")
    plan_id = svc.runs[run_key]["plan_id"]
    await svc.approve_plan(run_key, plan_id, "u-test")
    await svc.start_execution(run_key)
    claimed = await svc.poll_once()
    return svc, events, bool(claimed) and svc.runs[run_key].get("task") is not None


class _NoEngine:
    """Never invoked: the gate chain must refuse before any engine spawn."""

    calls = 0

    def execute_goal(self, *a, **k):  # pragma: no cover - must not run
        _NoEngine.calls += 1
        return {}


def test_run_start_gate_emits_cta_payload(db_factory, monkeypatch):
    """Full chain proof: tier-blocked run carries the upgrade block on
    run_data (mirrored into the aggregate API by M22-M2) and emits an honest
    ENGINE_FAILED — the CTA rides along instead of a dead end."""
    from harness_contracts.events import EventType

    monkeypatch.setenv("HARNESS_TEAM_BUDGET_USD", "1000000.0")  # budget can't be the blocker
    f = db_factory
    run(_set_plan(f, "free"))
    cap = DEFAULT_RUNS_PER_DAY["free"]
    for i in range(cap):
        run(_add_run(f, f"c-{i}"))

    svc, events, spawned = run(_drive_to_gate(f, "tier-run"))
    assert spawned is False, "engine must never be invoked at the tier cap"
    failed = [e for e in events if e[0] is EventType.ENGINE_FAILED]
    assert failed, "honest blocked event required"
    reason = failed[-1][1].get("reason", "")
    assert "limit reached" in reason.lower()
    upgrade = svc.runs["tier-run"].get("upgrade")
    assert isinstance(upgrade, dict)
    assert set(upgrade) == {"tier", "cap", "capField", "used", "upgradeTo", "cta"}
    assert upgrade["capField"] == "runsPerDay" and upgrade["upgradeTo"] == "pro"
    # Budget gate untouched below the tier decision: still healthy.
    assert svc.runs["tier-run"].get("budget_degraded", False) is False


def test_budget_gate_still_blocks_after_tier_allows(db_factory, monkeypatch):
    """Tier pass ≠ bypass: the M18-C2 spend gate still answers second."""
    from harness_contracts.events import EventType

    monkeypatch.setenv("HARNESS_TEAM_BUDGET_USD", "0.05")
    f = db_factory
    run(_set_plan(f, "pro"))  # unlimited tier...
    # ...but the month's ledger already ate the whole budget.

    async def _seed_spend():
        async with f() as db:
            await db.execute(
                text(
                    "INSERT INTO cost_ledger (run_id, cost_usd, created_at)"
                    " VALUES ('run-a', 5.0,"
                    " strftime('%Y-%m-%d %H:%M:%S', 'now'))"
                ),
            )
            await db.commit()

    run(_seed_spend())
    svc, events, spawned = run(_drive_to_gate(f, "b-run"))
    assert spawned is False
    assert any(e[0] is EventType.ENGINE_FAILED for e in events)
    # A BUDGET denial carries no upgrade block — upgrades don't sell spend.
    assert svc.runs["b-run"].get("upgrade") is None
