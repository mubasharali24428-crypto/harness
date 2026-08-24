"""Wave 9 node A — cost metering tests.

Self-contained fixtures (conftest.py is frozen this wave): a capturing fake
pool mimics asyncpg's execute/fetch surface so insert *shape*, bind types,
failure swallowing, and the /spend route are verified against the real
FastAPI app without needing a live PostgreSQL driver in this venv.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.signatures import ModelResult, ModelRole

from harness_router import api, ledger
from harness_router.pricing import PricingEngine


class FakeLedgerPool:
    """Captures (verb, sql, params) like asyncpg's pool.execute/fetch would."""

    def __init__(
        self,
        *,
        fail: bool = False,
        rows: list[tuple] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, tuple]] = []
        self.fail = fail
        self.rows = rows or []
        self._seq = 0

    async def execute(self, sql: str, *params: object):
        self.calls.append(("execute", sql, params))
        if self.fail:
            raise RuntimeError("simulated ledger outage")
        self._seq += 1
        return (self._seq, datetime.now(UTC))

    async def fetch(self, sql: str, *params: object):
        self.calls.append(("fetch", sql, params))
        if self.fail:
            raise RuntimeError("simulated ledger outage")
        return list(self.rows)


@pytest.fixture(autouse=True)
def _reset_ledger_pool():
    """Every test starts and ends with metering unwired (global hygiene)."""
    api.set_ledger_pool(None)
    yield
    api.set_ledger_pool(None)


# --- pricing math ------------------------------------------------------------


def test_cost_math_matches_pricing_engine():
    params = ledger.build_usage_params(
        role=ModelRole.PRIMARY,
        model="deepseek/deepseek-chat",
        input_tokens=1_000_000,
        output_tokens=500_000,
        latency_ms=120,
    )
    # deepseek-chat: 0.14/M in + 0.28/M out -> 0.14 + 0.14 = 0.28
    # H-023: cost binds as TEXT at NUMERIC(12,6); normalize via to_money.
    assert ledger.to_money(params[5]) == Decimal("0.280000")
    assert ledger.to_money(params[5]) == PricingEngine.calculate_cost(
        "deepseek/deepseek-chat", 1_000_000, 500_000
    )


def test_zero_token_local_model_costs_zero():
    params = ledger.build_usage_params(
        role=ModelRole.PRIMARY,
        model="local/qwen3:14b-ablated",
        input_tokens=4096,
        output_tokens=512,
        latency_ms=5,
    )
    # H-023: money binds as TEXT ("0.000000"), read back exactly via to_money.
    assert ledger.to_money(params[5]) == Decimal("0")


def test_role_enum_persists_as_bare_value():
    params = ledger.build_usage_params(
        role=ModelRole.REVIEWER,
        model="openai/gpt-4o-mini",
        input_tokens=10,
        output_tokens=10,
        latency_ms=1,
    )
    assert params[1] == "reviewer"


def test_varchar_limits_are_enforced_at_bind_time():
    params = ledger.build_usage_params(
        role="r" * 40,  # agent_role VARCHAR(20)
        model="m" * 300,  # model VARCHAR(120)
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    )
    assert len(params[1]) <= 20
    assert len(params[2]) <= 120


# --- insert shape -------------------------------------------------------------


def test_insert_sql_targets_frozen_schema():
    sql = ledger.INSERT_USAGE_SQL
    assert "INSERT INTO cost_ledger" in sql
    for col in [
        "run_id",
        "agent_role",
        "model",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
    ]:
        assert col in sql, f"missing column {col}"
    # positional asyncpg placeholders $1..$7, RETURNING for the row back
    for ph in ["$1", "$2", "$3", "$4", "$5", "$6", "$7"]:
        assert ph in sql
    assert "RETURNING" in sql
    # timestamps come from the column default — never SQL now()
    assert "now(" not in sql.lower()


def test_record_usage_binds_expected_params_in_order():
    pool = FakeLedgerPool()
    row = _run(ledger.record_usage(
        pool,
        agent_id="agent-42",
        role=ModelRole.PRIMARY,
        model="deepseek/deepseek-coder",
        input_tokens=800,
        output_tokens=200,
        latency_ms=321,
        run_id=None,
    ))
    verb, sql, params = pool.calls[0]
    assert verb == "execute"
    assert sql == ledger.INSERT_USAGE_SQL
    assert params[0] is None  # run_id nullable
    assert params[1] == "primary"
    assert params[2] == "deepseek/deepseek-coder"
    assert params[3] == 800 and params[4] == 200
    assert isinstance(params[6], int) and params[6] == 321

    assert row is not None
    assert row.seq == 1
    assert row.agent_role == "primary"
    assert row.model == "deepseek/deepseek-coder"
    assert row.cost_usd == ledger.to_money(params[5])
    assert isinstance(row.cost_usd, Decimal)
    assert isinstance(row.created_at, datetime)


def test_record_usage_passes_run_id_when_given():
    pool = FakeLedgerPool()
    _run(ledger.record_usage(
        pool,
        agent_id="a",
        role="reviewer",
        model="openai/gpt-4o-mini",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        run_id="0b9e6c6e-0000-4000-8000-000000000001",
    ))
    assert pool.calls[0][2][0] == "0b9e6c6e-0000-4000-8000-000000000001"


# --- failure swallowing -------------------------------------------------------


def test_record_usage_swallows_execute_failure(caplog):
    pool = FakeLedgerPool(fail=True)
    with caplog.at_level("WARNING", logger="harness_router.ledger"):
        row = _run(ledger.record_usage(
            pool,
            agent_id="agent-7",
            role=ModelRole.PRIMARY,
            model="deepseek/deepseek-chat",
            input_tokens=10,
            output_tokens=10,
            latency_ms=9,
        ))
    assert row is None  # never raises into the caller
    assert any("cost_ledger" in r.message.lower() or "ledger" in r.message.lower()
               for r in caplog.records)


def test_record_usage_swallows_any_internal_error():
    class ExplodingPool(FakeLedgerPool):
        async def execute(self, sql, *params):  # raises before capture even
            raise ValueError("bad bind")

    row = _run(ledger.record_usage(
        ExplodingPool(),
        agent_id="a",
        role=ModelRole.PRIMARY,
        model="deepseek/deepseek-chat",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    ))
    assert row is None


# --- call-path wiring ---------------------------------------------------------


async def test_call_model_success_records_usage():
    pool = FakeLedgerPool()
    api.set_ledger_pool(pool)
    result = await api.call_model(
        api.CallModelRequest(
            agentId="agent-e2e",
            role=ModelRole.REVIEWER,
            messages=[{"role": "user", "content": "hello ledger"}],
        )
    )
    assert isinstance(result, ModelResult)

    for task in api.pending_usage_tasks():  # drain fire-and-forget writes
        await task

    executes = [c for c in pool.calls if c[0] == "execute"]
    assert len(executes) == 1, "every successful model call must record usage"
    _, sql, params = executes[0]
    assert sql == ledger.INSERT_USAGE_SQL
    assert params[2] == result.model  # default reviewer model
    assert params[3] >= 1 and params[4] >= 1
    assert params[6] == result.latencyMs


async def test_call_model_succeeds_even_when_ledger_is_down():
    pool = FakeLedgerPool(fail=True)
    api.set_ledger_pool(pool)
    result = await api.call_model(
        api.CallModelRequest(
            agentId="agent-outage",
            role=ModelRole.PRIMARY,
            messages=[{"role": "user", "content": "still works"}],
        )
    )
    assert isinstance(result, ModelResult)  # call path unaffected
    for task in api.pending_usage_tasks():
        await task  # must not raise


def test_call_route_through_real_app_records_usage():
    """End-to-end: HTTP POST /call fires the background usage write."""
    pool = FakeLedgerPool()
    api.set_ledger_pool(pool)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/models/call",
            json={
                "agentId": "agent-http",
                "role": "primary",
                "messages": [{"role": "user", "content": "meter me"}],
            },
        )
        assert res.status_code == 200
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if any(c[0] == "execute" for c in pool.calls):
                break
            time.sleep(0.05)
    executes = [c for c in pool.calls if c[0] == "execute"]
    assert executes, "usage row must be written after an HTTP /call"


# --- spend aggregation --------------------------------------------------------


def test_summarize_spend_folds_rows_into_wire_shape():
    day1 = datetime(2026, 8, 20, tzinfo=UTC)
    day2 = datetime(2026, 8, 21, tzinfo=UTC)
    rows = [
        ("openai/gpt-4o-mini", 1000, 500, 0.45, day1),
        ("deepseek/deepseek-chat", 2000, 100, 0.01, day1),
        ("openai/gpt-4o-mini", 500, 250, 0.225, day2),
    ]
    out = ledger.summarize_spend(rows)
    assert out["totals"] == {"inputTokens": 3500, "outputTokens": 850, "costUsd": 0.685}
    assert [m["model"] for m in out["byModel"]] == [
        "openai/gpt-4o-mini",
        "deepseek/deepseek-chat",
    ]  # sorted by cost desc
    assert out["byModel"][0]["tokens"] == 2250  # 1000+500+500+250 across both days
    assert [d["day"] for d in out["byDay"]] == ["2026-08-20", "2026-08-21"]


def test_summarize_spend_empty_is_zeroed():
    out = ledger.summarize_spend([])
    assert out["totals"] == {"inputTokens": 0, "outputTokens": 0, "costUsd": 0}
    assert out["byModel"] == [] and out["byDay"] == []


async def test_spend_summary_binds_filters_as_scalars_and_datetime():
    pool = FakeLedgerPool()
    await ledger.spend_summary(pool, team_id="team-1", project_id="proj-2", since_days=7)
    _, sql, params = pool.calls[0]
    assert params[0] == "team-1"
    assert params[1] == "proj-2"
    assert isinstance(params[2], datetime) and params[2].tzinfo is not None
    # parameterized GROUP BY SQL only — no literal ids interpolated
    assert "GROUP BY" in sql and "team-1" not in sql and "$1" in sql and "$3" in sql


async def test_spend_summary_no_filters_binds_nulls():
    pool = FakeLedgerPool()
    await ledger.spend_summary(pool)
    assert pool.calls[0][2] == (None, None, None)


def test_spend_route_through_real_app():
    day = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    pool = FakeLedgerPool(rows=[("openai/gpt-4o-mini", 1500, 700, 0.31, day)])
    api.set_ledger_pool(pool)
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    res = client.get("/api/v1/models/spend", params={"teamId": "team-9", "sinceDays": 30})
    assert res.status_code == 200
    body = res.json()
    assert body["totals"] == {"inputTokens": 1500, "outputTokens": 700, "costUsd": 0.31}
    assert body["byModel"][0]["model"] == "openai/gpt-4o-mini"
    assert body["byDay"][0]["day"] == "2026-08-21"

    _, sql, params = pool.calls[-1]
    assert params[0] == "team-9" and params[1] is None
    assert isinstance(params[2], datetime)


def test_spend_route_returns_503_without_pool():
    app = FastAPI()
    app.include_router(api.router)
    res = TestClient(app).get("/api/v1/models/spend")
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "LEDGER_UNAVAILABLE"


def test_spend_route_maps_query_failure_to_500():
    api.set_ledger_pool(FakeLedgerPool(fail=True))
    app = FastAPI()
    app.include_router(api.router)
    res = TestClient(app).get("/api/v1/models/spend")
    assert res.status_code == 500
    assert res.json()["detail"]["error"]["code"] == "LEDGER_QUERY_FAILED"


def _run(awaitable):
    """Run a single coroutine to completion inside sync tests."""
    import asyncio

    return asyncio.new_event_loop().run_until_complete(awaitable)
