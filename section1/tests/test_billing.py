"""Mission 10 node B1 — billing API tests (contract M10-C3 server side).

Self-contained by design (conftest.py is frozen): fixtures are local and the
router under test is mounted on a SCRATCH FastAPI app exactly like the wave9
invite suite and the A1 keys suite — app.py registration is ATLAS's merge step
and we must not touch that file. Sessions are seeded straight into the store
with contract-shaped token hashes so no login route is needed. Everything runs
offline on aiosqlite. The HARNESS_BILLING flag is flipped per test via
monkeypatch (the router reads os.environ per request by design).
"""

import sys
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.billing import BillingPlan, router
from harness_identity.models import (
    AgentRun,
    AuthSession,
    Base,
    CostLedger,
    Project,
    Team,
    TeamMember,
    User,
)

# --------------------------------------------------------------- fixtures


@pytest.fixture
async def billing_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def billing_app(billing_factory, monkeypatch):
    """Scratch app exposing ONLY this node's router; state mirrors what
    make_app() sets that our handlers read: session_factory (+ optional
    stripe adapter injected per-test). Flag starts OFF."""
    monkeypatch.delenv("HARNESS_BILLING", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = billing_factory
    return app


@pytest.fixture
async def billing_client(billing_app):
    transport = ASGITransport(app=billing_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_user(
    factory, email: str, *, role: str = "admin", team_id: str | None = None
) -> dict:
    """Create team/user/membership/session rows directly; return ids + bearer."""
    import secrets

    async with factory() as db:
        if team_id is None:
            team = Team(name=f"team-{email}")
            db.add(team)
            await db.flush()
            team_id = team.id
        user = User(email=email.lower(), name=email.split("@")[0])
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team_id, user_id=user.id, display_name=user.name, role=role))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team_id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {"teamId": team_id, "userId": user.id, "token": token}


async def _seed_spend(
    factory, team_id: str, entries: list[tuple[float, int]]
) -> None:
    """cost_ledger rows routed through projects -> agent_runs for the team.

    Each entry is (cost_usd, hours_from_month_start): timestamps are anchored to
    THIS month's UTC start, so positive offsets ALWAYS land inside the current
    month and negative ones ALWAYS land before it — deterministic whatever day
    the suite runs on (a raw ``now - timedelta(days=N)`` put the 1-3-day rows
    into last month whenever the suite ran on the 1st-3rd).
    """
    async with factory() as db:
        project = Project(team_id=team_id, name="spend-proj")
        db.add(project)
        await db.flush()
        run = AgentRun(project_id=project.id, state="done")
        db.add(run)
        await db.flush()
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        for i, (cost, hours_from_month_start) in enumerate(entries):
            db.add(
                CostLedger(
                    run_id=run.id,
                    agent_role="planner",
                    model=f"model-{i}",
                    input_tokens=10,
                    output_tokens=20,
                    cost_usd=cost,
                    latency_ms=5,
                    created_at=month_start + timedelta(hours=hours_from_month_start),
                )
            )
        await db.commit()


class _FakeStripe:
    """Injectable thin client satisfying the StripeClient protocol."""

    def __init__(self, payload: dict | None = None, boom: bool = False):
        self.payload = payload or {"plan": None, "monthlyBudgetUsd": 50.0, "usedUsd": 12.5}
        self.boom = boom
        self.calls: list[str] = []

    async def fetch_state(self, team_id: str) -> dict:
        self.calls.append(team_id)
        if self.boom:
            raise RuntimeError("stripe upstream 500")
        return dict(self.payload)


# ------------------------------------------------------------- flag off


async def test_flag_off_state_is_honest_200(billing_factory, billing_client):
    actor = await _seed_user(billing_factory, "off@x.com")
    r = await billing_client.get("/api/v1/billing/state", headers=_bearer(actor["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"enabled": False, "plan": None, "quota": None, "source": "flag-off"}
    # explicit falsy spellings stay off too
    for value in ("off", "", "none"):
        import os

        os.environ["HARNESS_BILLING"] = value
        try:
            r = await billing_client.get(
                "/api/v1/billing/state", headers=_bearer(actor["token"])
            )
            assert r.status_code == 200
            assert r.json()["enabled"] is False
            assert r.json()["source"] == "flag-off"
        finally:
            del os.environ["HARNESS_BILLING"]


# --------------------------------------------------------- enabled: local


async def test_enabled_local_state_quota_month_math(billing_factory, billing_client, monkeypatch):
    monkeypatch.setenv("HARNESS_BILLING", "local")
    actor = await _seed_user(billing_factory, "local@x.com")
    # 1.25 + 0.75 + 2.00 this month = 4.00; the 99.00 row is LAST month and
    # must NOT leak into the current-month quota. Offsets are hours from this
    # month's UTC start, so the in/out split holds on every calendar day.
    await _seed_spend(
        billing_factory, actor["teamId"], [(1.25, 5), (0.75, 96), (2.0, 24), (99.0, -72)]
    )

    r = await billing_client.get("/api/v1/billing/state", headers=_bearer(actor["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["source"] == "local"
    assert body["plan"] == "free"  # no billing_plans row yet -> honest fallback
    assert body["quota"]["usedUsd"] == pytest.approx(4.0)
    assert body["quota"]["monthlyBudgetUsd"] == 0.0


async def test_enabled_local_zero_ledger_rows_quota_is_zero(
    billing_factory, billing_client, monkeypatch
):
    monkeypatch.setenv("HARNESS_BILLING", "local")
    actor = await _seed_user(billing_factory, "zero@x.com")

    r = await billing_client.get("/api/v1/billing/state", headers=_bearer(actor["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["source"] == "local"
    assert body["plan"] == "free"
    assert body["quota"] == {"monthlyBudgetUsd": 0.0, "usedUsd": 0.0}


async def test_teams_budget_usd_0007_feeds_quota_max(billing_factory, billing_client, monkeypatch):
    """teams.budget_usd (migration 0007) participates when the column exists:
    effective budget = max(plan row, team override)."""
    monkeypatch.setenv("HARNESS_BILLING", "local")
    actor = await _seed_user(billing_factory, "cap@x.com")
    async with billing_factory() as db:
        # mirror migration 0007 onto the scratch schema (Team model is frozen)
        await db.execute(text("ALTER TABLE teams ADD COLUMN budget_usd NUMERIC(12,2)"))
        await db.execute(
            text("UPDATE teams SET budget_usd = :b WHERE id = :t"),
            {"b": 25.5, "t": actor["teamId"]},
        )
        db.add(BillingPlan(team_id=actor["teamId"], plan="pro", monthly_budget_usd=10))
        await db.commit()

    r = await billing_client.get("/api/v1/billing/state", headers=_bearer(actor["token"]))
    body = r.json()
    assert body["plan"] == "pro"
    assert body["quota"]["monthlyBudgetUsd"] == pytest.approx(25.5)  # max(10, 25.5)


# ------------------------------------------------------------ PUT /plan


async def test_admin_changes_plan_engineer_forbidden(billing_factory, billing_client, monkeypatch):
    monkeypatch.setenv("HARNESS_BILLING", "local")
    admin = await _seed_user(billing_factory, "adm@x.com", role="admin")
    engineer = await _seed_user(
        billing_factory, "eng@x.com", role="engineer", team_id=admin["teamId"]
    )
    viewer = await _seed_user(
        billing_factory, "vw@x.com", role="viewer", team_id=admin["teamId"]
    )

    # engineer CANNOT change the plan (admin-only gate, contract envelope)
    r = await billing_client.put(
        "/api/v1/billing/plan", json={"plan": "pro"}, headers=_bearer(engineer["token"])
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"]["code"] == "FORBIDDEN"

    # viewer/guest likewise cannot
    r = await billing_client.put(
        "/api/v1/billing/plan", json={"plan": "pro"}, headers=_bearer(viewer["token"])
    )
    assert r.status_code == 403, r.text

    # nothing was written by the denied callers
    async with billing_factory() as db:
        assert (await db.execute(select(BillingPlan))).scalars().all() == []

    # admin CAN change the plan -> updated state shape comes back
    r = await billing_client.put(
        "/api/v1/billing/plan", json={"plan": "pro"}, headers=_bearer(admin["token"])
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"enabled", "plan", "quota", "source"}
    assert body == {
        "enabled": True,
        "plan": "pro",
        "quota": {"monthlyBudgetUsd": 0.0, "usedUsd": 0.0},
        "source": "local",
    }

    # second PUT UPSERTS (never a duplicate row per team)
    r = await billing_client.put(
        "/api/v1/billing/plan", json={"plan": "team"}, headers=_bearer(admin["token"])
    )
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "team"
    async with billing_factory() as db:
        rows = (await db.execute(select(BillingPlan))).scalars().all()
        assert len(rows) == 1
        assert rows[0].plan == "team"

    # state endpoint now reflects the persisted tier
    r = await billing_client.get("/api/v1/billing/state", headers=_bearer(admin["token"]))
    assert r.json()["plan"] == "team"


async def test_put_plan_validation_and_auth(billing_factory, billing_client, monkeypatch):
    monkeypatch.setenv("HARNESS_BILLING", "local")
    admin = await _seed_user(billing_factory, "val@x.com", role="admin")

    # unknown tier dies at validation (Literal contract M10-C3)
    r = await billing_client.put(
        "/api/v1/billing/plan", json={"plan": "enterprise"}, headers=_bearer(admin["token"])
    )
    assert r.status_code == 422, r.text

    # bearer contract per api-conventions.md
    r = await billing_client.get("/api/v1/billing/state")
    assert r.status_code == 401
    r = await billing_client.put("/api/v1/billing/plan", json={"plan": "free"})
    assert r.status_code == 401


# ------------------------------------------------------ stripe behind flag


async def test_stripe_source_only_with_key_and_injected_adapter(
    billing_factory, billing_client, billing_app, monkeypatch
):
    monkeypatch.setenv("HARNESS_BILLING", "stripe")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake_not_real")
    fake = _FakeStripe()
    billing_app.state.stripe_client = fake
    actor = await _seed_user(billing_factory, "st@x.com")

    r = await billing_client.get("/api/v1/billing/state", headers=_bearer(actor["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["source"] == "stripe"
    assert body["plan"] == "free"  # adapter returned None plan -> free fallback
    assert body["quota"] == {"monthlyBudgetUsd": 50.0, "usedUsd": 12.5}
    assert fake.calls == [actor["teamId"]]


async def test_stripe_flag_without_key_or_adapter_stays_local(
    billing_factory, billing_client, billing_app, monkeypatch
):
    actor = await _seed_user(billing_factory, "st2@x.com")
    hdrs = _bearer(actor["token"])

    # flag=stripe but NO key -> local numbers, never an import attempt
    monkeypatch.setenv("HARNESS_BILLING", "stripe")
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    r = await billing_client.get("/api/v1/billing/state", headers=hdrs)
    assert r.json()["source"] == "local"

    # key present but NO injected adapter -> degrade to local, not 500
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake_not_real")
    r = await billing_client.get("/api/v1/billing/state", headers=hdrs)
    assert r.json()["source"] == "local"

    # key + adapter that RAISES -> degrade to local with real DB numbers
    billing_app.state.stripe_client = _FakeStripe(boom=True)
    r = await billing_client.get("/api/v1/billing/state", headers=hdrs)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local"
    assert body["quota"]["usedUsd"] == 0.0


async def test_stripe_package_never_imported():
    """Hard mission rule: zero new installs; the adapter is an interface."""
    assert "stripe" not in sys.modules


# ------------------------------------------------- schema/migration parity


def test_billing_model_matches_frozen_migration_shape():
    cols = BillingPlan.__table__.columns
    assert set(cols.keys()) == {"id", "team_id", "plan", "monthly_budget_usd", "updated_at"}
    assert cols["id"].primary_key
    assert cols["id"].type.length == 36  # type: ignore[attr-defined]
    assert cols["team_id"].type.length == 36  # type: ignore[attr-defined]
    assert cols["team_id"].unique is True  # UNIQUE per-team (uq_billing_plans_team_id)
    assert cols["plan"].type.length == 30  # type: ignore[attr-defined]
    assert cols["monthly_budget_usd"].type.precision == 12  # type: ignore[attr-defined]
    assert cols["monthly_budget_usd"].type.scale == 2  # type: ignore[attr-defined]
    fks = list(cols["team_id"].foreign_keys)
    assert len(fks) == 1 and fks[0].column.table.name == "teams"


def test_migration_sql_style_matches_siblings():
    from pathlib import Path

    sql_path = Path(__file__).resolve().parents[1] / "migrations" / "0010_billing_plans.sql"
    sql = sql_path.read_text()
    # sibling style: leading -- comment header, additive CREATE TABLE block
    assert sql.lstrip().startswith("--")
    assert "CREATE TABLE billing_plans (" in sql
    assert "\tid VARCHAR(36) NOT NULL," in sql
    assert "\tteam_id VARCHAR(36) NOT NULL," in sql
    assert "\tplan VARCHAR(30) NOT NULL," in sql
    assert "\tmonthly_budget_usd NUMERIC(12,2) DEFAULT 0 NOT NULL," in sql
    assert "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL," in sql
    assert "\tPRIMARY KEY (id)," in sql
    assert "\tCHECK (plan IN ('free', 'pro', 'team'))," in sql
    assert "\tFOREIGN KEY(team_id) REFERENCES teams (id)" in sql
    assert "CREATE UNIQUE INDEX uq_billing_plans_team_id ON billing_plans (team_id);" in sql
