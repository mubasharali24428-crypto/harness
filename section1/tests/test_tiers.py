"""M22-M2 — tier storage on billing_plans (0031) + tierCaps on the wire.

Covers the section1 slice of lane M2:

* ``PLAN_TIERS`` re-export equals the frozen contracts ladder (the bare
  Mission-10 tuple became behavior-bearing without breaking imports);
* GET /api/v1/billing/state carries a ``tierCaps`` block resolved through
  harness_contracts.tier_caps (free defaults with no plan row; pro facts
  after an admin PUT);
* migration 0031 is a unique-slot, additive-only pair of ALTERs and the ORM
  mirror (BillingPlan) carries exactly those nullable INTEGER columns.

Scratch-app pattern identical to test_billing.py (conftest.py is FROZEN).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from harness_contracts import (
    DEFAULT_AUDIT_RETENTION_DAYS,
    DEFAULT_CONTEXT_INDEX_MAX_CHUNKS,
    DEFAULT_RUNS_PER_DAY,
    DEFAULT_SEAT_LIMITS,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.billing import PLAN_TIERS, BillingPlan, router


def test_plan_tiers_reexport_matches_contracts():
    import harness_contracts

    assert PLAN_TIERS is harness_contracts.PLAN_TIERS
    assert tuple(PLAN_TIERS) == ("free", "pro", "team")


# --------------------------------------------------------------- fixtures


@pytest.fixture
async def tiers_factory():
    engine = create_async_engine("sqlite+aiosqlite://")

    def _register(dbapi_conn, _rec=None):
        dbapi_conn.create_function(
            "now", 0, lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        )
        dbapi_conn.create_function(
            "date_trunc", 2, lambda unit, value: f"{str(value)[:7]}-01 00:00:00"
        )

    event.listen(engine.sync_engine, "connect", _register)
    from harness_identity.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def tiers_client(tiers_factory, monkeypatch):
    monkeypatch.delenv("HARNESS_BILLING", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = tiers_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_admin(factory) -> dict:
    """Team/user/membership/session rows; returns ids + bearer (admin)."""
    import secrets

    from harness_contracts import hash_token

    from harness_identity.models import AuthSession, Team, TeamMember, User

    async with factory() as db:
        team = Team(name="tier-team")
        db.add(team)
        await db.flush()
        user = User(email="tier@x.com", name="tier")
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=user.id, display_name="tier", role="admin"))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
    return {"teamId": team.id, "userId": user.id, "token": token}


# ------------------------------------------------------------------ wire


async def test_state_carries_free_tier_caps_without_plan_row(
    tiers_client, tiers_factory, monkeypatch
):
    monkeypatch.setenv("HARNESS_BILLING", "local")
    actor = await _seed_admin(tiers_factory)

    r = await tiers_client.get("/api/v1/billing/state", headers=_bearer(actor["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] == "free"  # honest fallback, unchanged
    caps = body["tierCaps"]
    assert caps["tier"] == "free"
    assert caps["runsPerDay"] == DEFAULT_RUNS_PER_DAY["free"]
    assert caps["contextIndexMaxChunks"] == DEFAULT_CONTEXT_INDEX_MAX_CHUNKS["free"]
    assert caps["seatLimit"] == DEFAULT_SEAT_LIMITS["free"]
    assert caps["auditRetentionDays"] == DEFAULT_AUDIT_RETENTION_DAYS["free"]
    assert caps["upgradeTo"] == "pro"


async def test_put_plan_pro_flips_tier_caps(tiers_client, tiers_factory, monkeypatch):
    monkeypatch.setenv("HARNESS_BILLING", "local")
    actor = await _seed_admin(tiers_factory)

    r = await tiers_client.put(
        "/api/v1/billing/plan",
        json={"plan": "pro"},
        headers=_bearer(actor["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] == "pro"
    caps = body["tierCaps"]
    # Pro buys FULL runs + FULL index: unlimited on both enforced dimensions.
    assert caps["tier"] == "pro"
    assert caps["runsPerDay"] is None
    assert caps["contextIndexMaxChunks"] is None
    assert caps["seatLimit"] == DEFAULT_SEAT_LIMITS["pro"]
    assert caps["upgradeTo"] == "team"


# ------------------------------------------------------------ migration 0031


def _migration_text() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "0031_tier_cap_overrides.sql"
    ).read_text(encoding="utf-8")


def test_migration_slot_0031_is_unique():
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    prefixes: dict[str, int] = {}
    for f in migrations_dir.glob("*.sql"):
        prefixes.setdefault(f.name.split("_", 1)[0], 0)
        prefixes[f.name.split("_", 1)[0]] += 1
    dupes = [p for p, n in prefixes.items() if n > 1]
    assert dupes == [], f"duplicate migration prefixes would crash migrate(): {dupes}"


def test_migration_0031_is_additive_pair_of_alters_matching_orm():
    content = _migration_text()
    body = "\n".join(
        line for line in content.splitlines() if not line.strip().startswith("--")
    )
    statements = [s.strip() for s in body.split(";") if s.strip()]
    assert len(statements) == 2
    expected = {
        ("billing_plans", "runs_per_day_override"),
        ("billing_plans", "context_index_max_chunks_override"),
    }
    got = set()
    for stmt in statements:
        upper = stmt.upper()
        assert upper.startswith("ALTER TABLE BILLING_PLANS ADD COLUMN "), stmt
        assert "DROP" not in upper and "DELETE" not in upper and "UPDATE " not in upper
        parts = stmt.split()
        got.add((parts[2], parts[5]))
        assert parts[6].upper() == "INTEGER", "override columns are nullable INTEGER"
    assert got == expected
    # The ORM mirror carries exactly these columns (nullable int).
    for name in ("runs_per_day_override", "context_index_max_chunks_override"):
        col = BillingPlan.__table__.columns[name]
        assert col.nullable is True
        assert col.type.python_type is int
