"""M14-W4 F1a — cost metering live end-to-end (H-011 run-cost gap).

The chain under test: S4 engine completes → ONE team-attributed cost_ledger
row via harness_identity.cost_ledger.record_usage → GET /api/v1/models/spend
(W4-C1) reflects it NUMERIC-exactly. Also pins the honest empty states:

- completed engine run  ⇒ exactly one ledger row, exact scale-6 Decimal cost,
  correct model/role/tokens, attributed through the frozen team chain.
- failed/cancelled runs ⇒ a row only when the session transcript proves usage
  (W14: the transcript, not the event stream, is the live SDK's source of
  truth); a crash with no usage is never billed from an estimate.
- no DB wired           ⇒ hook is a silent no-op (orchestration unaffected).
- spend endpoint        ⇒ wired+empty ledger answers 200 unwired=False (real
  zeros, not unknown); no pool at all answers 503 LEDGER_UNAVAILABLE.

conftest.py is frozen: self-contained sqlite schema mirrors the frozen
0001 tables this flow touches (teams/projects/agent_runs/cost_ledger +
agent_plans/agent_events for rehydration), per test_budget.py's pattern.
"""

import asyncio

# Sibling section src trees (mirrors the runtime seams service.py/api.py use;
# pyproject pythonpath only carries this section + contracts).
import sys as _sys
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.events import EventType
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_ROOT = None


def _ensure_sibling_srcs() -> None:
    global _ROOT
    if _ROOT is None:
        from pathlib import Path

        _ROOT = Path(__file__).resolve().parents[3]
    for seam in ("section1", "section3"):
        src = str(_ROOT / seam / "src")
        if src not in _sys.path:
            _sys.path.append(src)


_ensure_sibling_srcs()

import harness_router.api as router_api  # noqa: E402

# M17-B3: the canonical money/pricing code lives in harness_contracts now
# (_to_money is a private of that module; S1/S3 keep re-export shims).
from harness_contracts.cost_ledger import _to_money  # noqa: E402  (exact money)
from harness_router.pricing import PricingEngine  # noqa: E402


def _wire_auth(app) -> None:
    """S1-R01a: Section 3 routes are bearer-gated; stub the principal."""
    from types import SimpleNamespace

    app.dependency_overrides[router_api.get_principal] = lambda: SimpleNamespace(
        userId="u-metering", teamId="team-a", role="admin"
    )

from harness_orchestrator.service import OrchestratorService  # noqa: E402

DDL = """
CREATE TABLE teams (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    -- M18-C2 tier 1: cap override (NULL = unset). Scratch mirrors without
    -- this column fail closed at the budget gate and every run blocks before
    -- the engine call (teams unreadable ⇒ degraded denial).
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
    -- H-023 money law: default mirrors PROD semantics (Postgres now()),
    -- a literal stamp rots the 7-day spend window as real time passes.
    -- (2026-08-26 close was green, red again from ~2026-08-30 onward.)
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id VARCHAR(36),
    run_id VARCHAR(36) NOT NULL,
    type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
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
    "INSERT INTO projects (id, team_id) VALUES ('proj-a', 'team-a')",
    # The metered run's durable anchor: cost_ledger attribution joins
    # run_id → agent_runs.project_id → projects.team_id, so the run row MUST
    # exist or the spend route would (correctly) drop the row as unattributed.
    "INSERT INTO agent_runs (id, project_id) VALUES ('run-a', 'proj-a')",
    "INSERT INTO agent_runs (id, project_id) VALUES ('m-run', 'proj-a')",
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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/metering.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for statement in DDL.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            for stmt in SEED:
                await conn.execute(text(stmt))

    run(_setup())
    yield factory
    run(engine.dispose())


class StubEngine:
    """Completed engine run with REAL usage (as the live path folds it)."""

    def __init__(self):
        self.calls = 0

    def execute_goal(self, goal=None, session_id=None):
        self.calls += 1
        return {
            "sessionId": session_id,
            "engine": "deepseek-harness-sdk:deepseek/deepseek-chat",
            "status": "completed",
            "finalResponse": "done" * 50,  # 100 chars → 25 output tokens est.
            # Real token accounting folded from dsh usage chunks (M14-W4 F1a).
            "usage": {"inputTokens": 10_000, "outputTokens": 500},
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


async def _ledger_rows(factory):
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT run_id, agent_role, model, input_tokens,"
                        " output_tokens, cost_usd, latency_ms FROM cost_ledger"
                        " ORDER BY seq"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# ------------------------------------------------------- engine completion → row


def test_engine_completion_writes_one_exact_decimal_row(db_factory):
    f = db_factory
    engine = StubEngine()
    svc, events = _wire_service(f, engine)
    _approve(svc, "m-run")
    run(svc.start_execution("m-run"))  # bare sqlite: no queue table → direct spawn
    task = svc.runs["m-run"]["task"]
    run(asyncio.wait_for(task, timeout=5))

    assert engine.calls == 1
    completed = [e for e in events if e[0] is EventType.ENGINE_COMPLETED]
    assert completed and completed[-1][1]["runId"] == "m-run"

    rows = run(_ledger_rows(f))
    assert len(rows) == 1, "exactly one ledger row per completed engine run"
    row = rows[0]
    assert row["run_id"] == "m-run"
    assert row["agent_role"] == "primary"
    assert row["model"] == "deepseek/deepseek-chat"  # the model that answered
    assert row["input_tokens"] == 10_000 and row["output_tokens"] == 500  # real usage

    # H-023 money law: deepseek-chat $0.2574/M in + $1.0287/M out
    expected = PricingEngine.calculate_cost("deepseek/deepseek-chat", 10_000, 500)
    assert expected == Decimal("0.003088")  # 0.002574 + 0.000514, quantized (12,6)
    stored = _to_money(row["cost_usd"])  # driver returns float on sqlite
    assert isinstance(stored, Decimal)
    assert stored == expected, "stored cost must be NUMERIC-exact vs PricingEngine"


def test_operator_rate_card_prices_the_ledger_row(db_factory):
    """W15: the ledger must book the operator's rate, not an invented fallback.

    The engine's model can be priced only through ``HARNESS_RATE_CARD_JSON``.
    Pricing that row from the frozen catalog alone silently used the
    0.50/1.50 fallback while the router quoted the operator's real rate for the
    same model — two answers for one call.
    """
    from harness_contracts.rate_card import RateCard, parse_overlay, set_active

    f = db_factory

    class CardOnlyEngine(StubEngine):
        def execute_goal(self, goal=None, session_id=None):
            result = super().execute_goal(goal=goal, session_id=session_id)
            result["engine"] = "deepseek-harness-sdk:lab/gpt-card"
            result["usage"] = {"inputTokens": 1_000, "outputTokens": 1_000}
            return result

    set_active(
        RateCard(parse_overlay({"models": {"lab/gpt-card": {"input": 1.25, "output": 10}}}))
    )
    try:
        svc, _events = _wire_service(f, CardOnlyEngine())
        _approve(svc, "m-card")
        run(svc.start_execution("m-card"))
        run(asyncio.wait_for(svc.runs["m-card"]["task"], timeout=5))
    finally:
        set_active(None)

    rows = run(_ledger_rows(f))
    assert [r["model"] for r in rows] == ["lab/gpt-card"]
    expected = Decimal("0.011250")  # (1.25 + 10) * 1000 / 1e6
    assert _to_money(rows[0]["cost_usd"]) == expected, (
        "an operator-priced model books the card rate, not the fallback (which would be 0.002000)"
    )


def test_estimated_tokens_when_engine_reports_no_usage(db_factory):
    f = db_factory

    class TextOnlyEngine(StubEngine):
        def execute_goal(self, goal=None, session_id=None):
            result = super().execute_goal(goal=goal, session_id=session_id)
            result.pop("usage")  # stub/dry engines carry no usage block
            return result

    svc, _ = _wire_service(f, TextOnlyEngine())
    _approve(svc, "m-run2")
    run(svc.start_execution("m-run2"))
    run(asyncio.wait_for(svc.runs["m-run2"]["task"], timeout=5))

    rows = run(_ledger_rows(f))
    assert len(rows) == 1
    row = rows[0]
    # ~4 chars/token estimate, same convention as the router's /call metering.
    assert row["output_tokens"] == max(1, len("done" * 50) // 4)
    assert row["input_tokens"] >= 1


def test_failed_run_records_no_ledger_row(db_factory):
    f = db_factory

    class ExplodingEngine(StubEngine):
        def execute_goal(self, goal=None, session_id=None):
            raise RuntimeError("boom")

    svc, events = _wire_service(f, ExplodingEngine())
    _approve(svc, "m-run3")
    run(svc.start_execution("m-run3"))
    run(asyncio.wait_for(svc.runs["m-run3"]["task"], timeout=5))
    assert [e for e in events if e[0] is EventType.ENGINE_FAILED]
    assert run(_ledger_rows(f)) == [], (
        "a failure with no transcript usage must not be billed from an estimate"
    )


def test_metering_hook_never_breaks_when_team_unresolvable(tmp_path):
    """No attributable team ⇒ skip honestly (no fabricated spend), run lives."""
    engine_arg = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/orphan.db")

    async def _bare():
        async with engine_arg.begin() as conn:
            for statement in DDL.split(";"):
                stmt = statement.strip()
                if stmt:
                    await conn.execute(text(stmt))
            # NOTE: no seed — agent_runs has no row for this run.

    run(_bare())
    factory = async_sessionmaker(engine_arg, expire_on_commit=False)

    class OrphanEngine(StubEngine):
        pass

    svc, _ = _wire_service(factory, OrphanEngine())
    _approve(svc, "orphan-run")  # in-memory plan; no agent_runs row exists
    run(svc.start_execution("orphan-run"))
    task = svc.runs["orphan-run"].get("task")
    if task is not None:
        run(asyncio.wait_for(task, timeout=5))
    assert run(_ledger_rows(factory)) == []


# ------------------------------------------------------------ spend reflects it


def test_spend_endpoint_reflects_engine_cost_exactly(db_factory, monkeypatch):
    f = db_factory
    engine = StubEngine()
    svc, _ = _wire_service(f, engine)
    _approve(svc, "m-run")
    run(svc.start_execution("m-run"))
    run(asyncio.wait_for(svc.runs["m-run"]["task"], timeout=5))

    # The spend route reads through ITS OWN duck-typed pool; adapt the same DB
    # so both halves of the pair contract hit identical rows.
    import re as _re

    class SQLAlchemyAsyncpgShim:
        """asyncpg-style fetch() over a SQLAlchemy session factory.

        SPEND_SQL is written for asyncpg ($n placeholders, ::casts,
        date_trunc); this shim translates exactly those forms to SQLite
        equivalents so the route's aggregation runs against the SAME rows the
        engine hook wrote. The day grouping degrades to raw timestamps here —
        the byDay fold still lands every row on one day key, which is all the
        exact-total assertion needs.
        """

        _TRANSLATIONS = (
            (r"date_trunc\('day', l\.created_at\)", "l.created_at"),
        )

        def __init__(self, factory):
            self._factory = factory

        async def fetch(self, sql, *params):
            pg_sql = _re.sub(r"::\w+", "", sql)  # strip PG casts first
            named: dict[str, object] = {}

            def _sub(match):
                i = int(match.group(1))
                named.setdefault(f"p_{i}", params[i - 1])
                return f":p_{i}"

            # $n → :p_n BEFORE stripping the casts the translation table
            # didn't cover (e.g. $1::text inside the IS NULL arm).
            pg_sql = _re.sub(r"\$(\d+)", _sub, pg_sql)
            for pattern, replacement in self._TRANSLATIONS:
                pg_sql = _re.sub(pattern, replacement, pg_sql)
            from sqlalchemy import text as _text

            async with self._factory() as db:
                result = await db.execute(_text(pg_sql), named)
                return [tuple(r) for r in result.fetchall()]

    shim = SQLAlchemyAsyncpgShim(f)
    router_api.set_ledger_pool(shim)
    try:
        app = FastAPI()
        app.include_router(router_api.router)
        _wire_auth(app)
        res = TestClient(app).get(
            "/api/v1/models/spend", params={"teamId": "team-a", "sinceDays": 7}
        )
        assert res.status_code == 200, res.text
        body = res.json()

        rows = run(_ledger_rows(f))
        expected_total = sum(_to_money(r["cost_usd"]) for r in rows)
        assert body["totals"]["costUsd"] == float(expected_total)
        assert body["totals"]["inputTokens"] == 10_000
        assert body["totals"]["outputTokens"] == 500
        assert body["byModel"][0]["model"] == "deepseek/deepseek-chat"
        # W4-C1: wired ledger ⇒ unwired False even alongside real numbers.
        assert body["unwired"] is False
    finally:
        router_api.set_ledger_pool(None)


def test_empty_ledger_answers_unwired_false_zeros_are_real(db_factory):
    """Wired but zero rows ⇒ 200 {zeroed totals, unwired:false} — metering IS
    live; nothing ran. The flag must not cry wolf."""
    f = db_factory
    assert run(_ledger_rows(f)) == []

    class Shim:
        async def fetch(self, sql, *params):
            return []

    router_api.set_ledger_pool(Shim())
    try:
        app = FastAPI()
        app.include_router(router_api.router)
        _wire_auth(app)
        res = TestClient(app).get("/api/v1/models/spend", params={"sinceDays": 30})
        assert res.status_code == 200
        body = res.json()
        assert body["totals"] == {"inputTokens": 0, "outputTokens": 0, "costUsd": 0}
        assert body["byDay"] == [] and body["byModel"] == []
        assert body["unwired"] is False
    finally:
        router_api.set_ledger_pool(None)


def test_no_metering_config_answers_503_unavailable(monkeypatch):
    """Zero rows AND no metering config ⇒ honest 503 LEDGER_UNAVAILABLE."""
    router_api.set_ledger_pool(None)
    app = FastAPI()
    app.include_router(router_api.router)
    _wire_auth(app)
    res = TestClient(app).get("/api/v1/models/spend")
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "LEDGER_UNAVAILABLE"


# ------------------------------------------------------- unit-level hook pieces


def test_fold_usage_prefers_real_usage_over_estimate():
    from harness_orchestrator.run_metering import fold_usage

    result = {
        "finalResponse": "x",
        "usage": {"inputTokens": 111, "outputTokens": 22},
    }
    assert fold_usage(result, "a much longer goal text") == (111, 22)
    # No usage block → ~4 chars/token estimate of goal + response.
    assert fold_usage({"finalResponse": "12345678"}, "abcd") == (1, 2)


def test_resolve_engine_model_names_the_answerer():
    from harness_orchestrator.engine import DeepSeekHarnessWrapper
    from harness_orchestrator.run_metering import resolve_engine_model

    wrapper = DeepSeekHarnessWrapper(model="deepseek/deepseek-reasoner")
    assert (
        resolve_engine_model(
            wrapper, {"engine": "deepseek-harness-sdk:deepseek/deepseek-chat"}
        )
        == "deepseek/deepseek-chat"
    )  # raw-02 L1: the model that actually answered wins
    assert resolve_engine_model(wrapper, {"engine": "injected"}) == (
        "deepseek/deepseek-reasoner"
    )  # falls back to configured primary


# ------------------------------------- transcript usage + retry-safe deltas (W14)


def _write_session_transcript(root, session_id: str, records: list[str]) -> None:
    from pathlib import Path

    path = Path(root) / session_id / "sessions" / "--ws--" / session_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "session.v4.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")


def test_session_usage_sums_transcript_and_dedupes_updates(tmp_path):
    """The deployed SDK emits no usage events; the transcript is the truth.

    Measured live: a completed run returns usage=None while session.v4.jsonl
    records one usage object per billed step. Consecutive duplicates (message
    update + its final form) collapse; cache reads are carried separately.
    """
    from harness_orchestrator.engine import DeepSeekHarnessWrapper

    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path), session_root=str(tmp_path / "sessions")
    )
    _write_session_transcript(
        tmp_path / "sessions",
        "run-u",
        [
            '{"type":"message","message":{"role":"assistant","usage":'
            '{"inputTokens":100,"outputTokens":10,"cacheReadTokens":900}}}',
            '{"type":"message","message":{"role":"assistant","usage":'
            '{"inputTokens":100,"outputTokens":10,"cacheReadTokens":900}}}',
            '{"type":"message","message":{"role":"assistant","usage":'
            '{"inputTokens":200,"outputTokens":20,"cacheReadTokens":0}}}',
            '{"type":"turn"}',
            "not json at all",
        ],
    )
    # 100+200 input, 10+20 output, 900 cache; the repeated first record counts once.
    assert wrapper.session_usage("run-u") == {
        "inputTokens": 300,
        "outputTokens": 30,
        "cacheReadTokens": 900,
    }
    assert wrapper.session_usage("no-such-run") is None


def test_fold_usage_reads_transcript_and_bills_cache_as_input():
    from harness_orchestrator.run_metering import fold_usage

    class TranscriptEngine:
        def session_usage(self, run_id):
            return {"inputTokens": 100, "outputTokens": 10, "cacheReadTokens": 900}

    # Transcript wins over the text estimate; cached prompt tokens bill as input
    # (the catalog has no cache tier, so this is a conservative upper bound).
    assert fold_usage({}, "g", engine=TranscriptEngine(), run_id="r") == (1000, 10)
    # allow_estimate=False is for failure paths: no evidence ⇒ no row.
    assert fold_usage({}, "g", engine=None, run_id=None, allow_estimate=False) is None


def test_blocked_run_meters_evidence_but_never_estimates(db_factory):
    """A budget-exhausted run really spent tokens; a crash with no transcript does not.

    The old hook metered the completion path only, so blocked/failed runs were
    invisible spend. Failures now record only provable usage (transcript), never
    a text estimate of an error message.
    """
    from harness_orchestrator.run_metering import record_run_cost

    class BlockedEngine:
        model = "deepseek/deepseek-chat"

        def session_usage(self, run_id):
            return {"inputTokens": 500, "outputTokens": 50, "cacheReadTokens": 0}

    class Svc:
        run_queue_factory = None

        def __init__(self, factory, engine):
            self.session_factory = factory
            self.engine = engine

        async def _team_for_run(self, run_data):
            return "team-a"

    run_data = {
        "runId": "m-run",
        "goal": "g",
        "engine_result": {
            "engine": "deepseek-harness-sdk:deepseek/deepseek-chat",
            "status": "error",
            "finalResponse": "DeepSeek Harness run failed: tool_budget_exhausted",
        },
    }
    run(record_run_cost(Svc(db_factory, BlockedEngine()), run_data, None,
                        allow_estimate=False))
    rows = run(_ledger_rows(db_factory))
    assert len(rows) == 1 and rows[0]["input_tokens"] == 500

    class CrashEngine:
        model = "deepseek/deepseek-chat"

        def session_usage(self, run_id):
            return None

    run_data["runId"] = "run-a"  # different run, same team
    run(record_run_cost(Svc(db_factory, CrashEngine()), run_data, None,
                        allow_estimate=False))
    assert len(run(_ledger_rows(db_factory))) == 1, "a crash with no usage is not billed"


def test_retried_run_meters_only_new_tokens(db_factory):
    """A resumed run reuses its session; the transcript is cumulative.

    Billing the whole session again on resume would double-count the first
    attempt, so the hook records the delta against rows already in the ledger.
    """
    from harness_orchestrator.run_metering import record_run_cost

    class GrowingEngine:
        model = "deepseek/deepseek-chat"
        totals = {"inputTokens": 1000, "outputTokens": 100, "cacheReadTokens": 0}

        def session_usage(self, run_id):
            return dict(self.totals)

    class Svc:
        run_queue_factory = None

        def __init__(self, factory, engine):
            self.session_factory = factory
            self.engine = engine

        async def _team_for_run(self, run_data):
            return "team-a"

    engine = GrowingEngine()
    svc = Svc(db_factory, engine)
    run_data = {
        "runId": "run-a",
        "goal": "g",
        "engine_result": {"engine": "deepseek-harness-sdk:deepseek/deepseek-chat"},
    }
    run(record_run_cost(svc, run_data, None))
    engine.totals = {"inputTokens": 2500, "outputTokens": 300, "cacheReadTokens": 0}
    run(record_run_cost(svc, run_data, None))
    # A third call with no new usage must add nothing.
    run(record_run_cost(svc, run_data, None))

    rows = run(_ledger_rows(db_factory))
    assert [(r["input_tokens"], r["output_tokens"]) for r in rows] == [
        (1000, 100),
        (1500, 200),
    ]
