"""A1-S2 — personal keys, consent, bounded share spend, spend intelligence (offline)."""

import asyncio
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from harness_contracts import hash_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.api_keys import router as keys_router
from harness_identity.crypto import KeyVault
from harness_identity.models import (
    AgentRun,
    AuditLog,
    AuthSession,
    Base,
    CostLedger,
    KeyShare,
    Project,
    Team,
    TeamMember,
    User,
)
from harness_identity.personal_keys_api import resolve_shared_key, router
from harness_identity.spend import (
    Sample,
    budget_alert,
    detect_spend_anomaly,
    predict_tokens,
    reconcile_usage,
)

RAW = "sk-personal-ABCDEF1234567890SECRET"  # gitleaks:allow
P = "/api/v1/identity"


@pytest.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")

    @event.listens_for(engine.sync_engine, "connect")
    def _fns(dbapi: sqlite3.Connection, _r):
        dbapi.create_function("now", 0, lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"))
        dbapi.create_function("date_trunc", 2, lambda _u, v: f"{str(v)[:7]}-01 00:00:00")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.include_router(keys_router)
    app.state.session_factory = factory
    app.state.vault = KeyVault(b"test-master-key-0123456789abcdef")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield client, factory, app.state.vault
    await engine.dispose()


async def _team(factory, name="team"):
    async with factory() as db:
        team = Team(name=name)
        db.add(team)
        await db.commit()
        return team.id


async def _user(factory, team_id, email, role="admin"):
    async with factory() as db:
        user = User(email=email, name=email)
        db.add(user)
        await db.flush()
        db.add(TeamMember(team_id=team_id, user_id=user.id, display_name=email, role=role))
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
        return {"id": user.id, "team": team_id, "h": {"Authorization": f"Bearer {token}"}}


async def _setup(factory):
    t = await _team(factory)
    n = secrets.token_hex(3)
    owner = await _user(factory, t, f"owner{n}@x.io")
    rec = await _user(factory, t, f"rec{n}@x.io", role="engineer")
    return owner, rec


async def _share(client, owner, rec, cap="5", accept=True):
    k = await client.post(
        f"{P}/personal-keys",
        headers=owner["h"],
        json={"label": "mine", "provider": "openai", "key": RAW},
    )
    assert k.status_code == 201, k.text
    s = await client.post(
        f"{P}/personal-keys/{k.json()['id']}/shares",
        headers=owner["h"],
        json={"recipientUserId": rec["id"], "spendCapUsd": cap},
    )
    assert s.status_code == 201, s.text
    sid = s.json()["id"]
    if accept:
        assert (await client.post(f"{P}/shares/{sid}/accept", headers=rec["h"])).status_code == 200
    return k.json()["id"], sid


def _reserve(client, rec, sid, amt, key):
    return client.post(
        f"{P}/shares/{sid}/reservations",
        headers=rec["h"],
        json={"amountUsd": amt, "idempotencyKey": key},
    )


async def test_personal_key_never_leaks_to_team_surfaces(env):
    client, factory, _ = env
    owner, rec = await _setup(factory)
    kid, _sid = await _share(client, owner, rec)
    listing = await client.get("/api/v1/keys", headers=rec["h"])
    assert listing.json()["items"] == []
    assert (await client.delete(f"/api/v1/keys/{kid}", headers=rec["h"])).status_code == 404
    assert (await client.delete(f"{P}/personal-keys/{kid}", headers=rec["h"])).status_code == 404
    mine = await client.get(f"{P}/personal-keys", headers=rec["h"])
    assert mine.json()["items"] == []
    assert RAW not in (await client.get(f"{P}/personal-keys", headers=owner["h"])).text


async def test_unaccepted_declined_and_stranger_cannot_use(env):
    client, factory, _ = env
    owner, rec = await _setup(factory)
    _k, sid = await _share(client, owner, rec, accept=False)
    assert (await _reserve(client, rec, sid, "1", "a")).status_code == 403  # pending
    stranger = await _user(factory, await _team(factory, "other"), "s@y.io")
    assert (await _reserve(client, stranger, sid, "1", "a")).status_code == 404
    assert (await client.post(f"{P}/shares/{sid}/accept", headers=owner["h"])).status_code == 404
    assert (await client.post(f"{P}/shares/{sid}/decline", headers=rec["h"])).status_code == 200
    assert (await client.post(f"{P}/shares/{sid}/accept", headers=rec["h"])).status_code == 409


async def test_cannot_share_to_non_member_or_self(env):
    client, factory, _ = env
    owner, _rec = await _setup(factory)
    outsider = await _user(factory, await _team(factory, "other"), "o@y.io")
    k = await client.post(
        f"{P}/personal-keys",
        headers=owner["h"],
        json={"label": "m", "provider": "openai", "key": RAW},
    )
    url = f"{P}/personal-keys/{k.json()['id']}/shares"
    r = await client.post(url, headers=owner["h"], json={"recipientUserId": outsider["id"]})
    assert r.status_code == 404
    r = await client.post(url, headers=owner["h"], json={"recipientUserId": owner["id"]})
    assert r.status_code == 422


async def test_concurrent_reservations_never_exceed_cap(env):
    client, factory, _ = env
    owner, rec = await _setup(factory)
    _k, sid = await _share(client, owner, rec, cap="5")
    rs = await asyncio.gather(*[_reserve(client, rec, sid, "1", f"k{i}") for i in range(10)])
    codes = sorted(r.status_code for r in rs)
    assert codes.count(201) == 5 and codes.count(403) == 5
    async with factory() as db:
        share = (await db.execute(select(KeyShare))).scalar_one()
    assert share.committed_usd == Decimal("5")


async def test_retry_is_idempotent_and_key_reuse_conflicts(env):
    client, factory, _ = env
    owner, rec = await _setup(factory)
    _k, sid = await _share(client, owner, rec, cap="5")
    a = await _reserve(client, rec, sid, "2", "same")
    b = await _reserve(client, rec, sid, "2", "same")
    assert a.json()["id"] == b.json()["id"] and b.json()["shareCommittedUsd"] == "2.000000"
    assert (await _reserve(client, rec, sid, "3", "same")).status_code == 409


async def test_revocation_blocks_new_work_but_in_flight_usage_still_recorded(env):
    client, factory, _ = env
    owner, rec = await _setup(factory)
    _k, sid = await _share(client, owner, rec, cap="5")
    inflight = (await _reserve(client, rec, sid, "2", "run1")).json()
    assert (await client.delete(f"{P}/shares/{sid}", headers=owner["h"])).json()[
        "status"
    ] == "revoked"
    assert (await _reserve(client, rec, sid, "1", "run2")).status_code == 403  # queued work denied
    done = await client.post(
        f"{P}/shares/{sid}/reservations/{inflight['id']}/settle",
        headers=rec["h"],
        json={"actualUsd": "2.5"},
    )
    assert done.status_code == 200 and done.json()["shareCommittedUsd"] == "2.500000"
    assert (await client.post(f"{P}/shares/{sid}/accept", headers=rec["h"])).status_code == 409


async def test_unknown_usage_stays_unsettled_then_late_usage_after_release(env):
    client, factory, _ = env
    owner, rec = await _setup(factory)
    _k, sid = await _share(client, owner, rec, cap="5")
    r = (await _reserve(client, rec, sid, "2", "u")).json()
    base = f"{P}/shares/{sid}/reservations/{r['id']}"
    u = await client.post(f"{base}/settle", headers=rec["h"], json={})
    assert u.json()["status"] == "unsettled" and u.json()["shareCommittedUsd"] == "2.000000"
    assert (
        await client.post(f"{base}/release", headers=rec["h"])
    ).status_code == 409  # unknown != unused
    s = await client.post(f"{base}/settle", headers=rec["h"], json={"actualUsd": "1.25"})
    assert s.json()["status"] == "settled" and s.json()["shareCommittedUsd"] == "1.250000"
    assert (
        await client.post(f"{base}/settle", headers=rec["h"], json={"actualUsd": "9"})
    ).status_code == 409
    # release then late usage reopens the charge, flagged late and over-cap aware
    r2 = (await _reserve(client, rec, sid, "3", "v")).json()
    b2 = f"{P}/shares/{sid}/reservations/{r2['id']}"
    assert (await client.post(f"{b2}/release", headers=rec["h"])).json()[
        "shareCommittedUsd"
    ] == "1.250000"
    late = await client.post(f"{b2}/settle", headers=rec["h"], json={"actualUsd": "6"})
    assert late.json()["late"] is True and late.json()["overCap"] is True
    assert late.json()["shareCommittedUsd"] == "7.250000"


async def test_expiry_and_revoked_key_deny_use_and_resolver(env):
    client, factory, vault = env
    owner, rec = await _setup(factory)
    kid, sid = await _share(client, owner, rec, cap="5")
    async with factory() as db:
        key, payer = await resolve_shared_key(db, vault, share_id=sid, user_id=rec["id"])
    assert key == RAW and payer == owner["id"]
    async with factory() as db:
        with pytest.raises(PermissionError):
            await resolve_shared_key(db, vault, share_id=sid, user_id=owner["id"])  # not recipient
        await db.execute(update(KeyShare).values(expires_at=datetime.now(UTC) - timedelta(hours=1)))
        await db.commit()
        with pytest.raises(PermissionError):
            await resolve_shared_key(db, vault, share_id=sid, user_id=rec["id"])
    assert (await _reserve(client, rec, sid, "1", "x")).status_code == 403
    # revoking the key kills its shares
    owner2, rec2 = await _setup(factory)
    kid2, sid2 = await _share(client, owner2, rec2)
    assert (
        await client.delete(f"{P}/personal-keys/{kid2}", headers=owner2["h"])
    ).status_code == 204
    assert (await _reserve(client, rec2, sid2, "1", "x")).status_code == 403


async def test_audit_trail_has_no_secret(env):
    client, factory, _ = env
    owner, rec = await _setup(factory)
    await _share(client, owner, rec)
    async with factory() as db:
        rows = (await db.execute(select(AuditLog))).scalars().all()
    actions = {r.action for r in rows}
    assert {"personal_key.added", "key_share.offered", "key_share.accepted"} <= actions
    assert RAW not in str([r.detail for r in rows])


async def test_alerts_statement_forecast_anomalies_endpoints(env):
    client, factory, _ = env
    owner, _rec = await _setup(factory)
    async with factory() as db:
        project = Project(team_id=owner["team"], name="p")
        db.add(project)
        await db.flush()
        run = AgentRun(project_id=project.id)
        db.add(run)
        await db.flush()
        db.add(
            CostLedger(
                run_id=run.id,
                agent_role="primary",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("9"),
                latency_ms=1,
            )
        )
        await db.execute(text("ALTER TABLE teams ADD COLUMN budget_usd NUMERIC"))
        await db.execute(
            text("UPDATE teams SET budget_usd = 10 WHERE id = :t"), {"t": owner["team"]}
        )
        await db.commit()
    a = (await client.get(f"{P}/spend/alerts", headers=owner["h"])).json()
    assert a["level"] == "warning" and a["remainingUsd"] == "1.000000"
    st = (await client.get(f"{P}/usage-statement", headers=owner["h"])).json()
    assert st["totalUsd"] == "9.000000" and st["reconciliation"]["status"] == "unsettled"
    st = (await client.get(f"{P}/usage-statement?providerUsd=9.5", headers=owner["h"])).json()
    assert st["reconciliation"]["status"] == "discrepancy"
    f = (await client.get(f"{P}/spend/forecast?promptTokens=100", headers=owner["h"])).json()
    assert f["available"] and f["calibrated"] is False
    an = (await client.get(f"{P}/spend/anomalies", headers=owner["h"])).json()
    assert an["status"] == "insufficient_history"
    other = await _user(factory, await _team(factory, "o"), "z@z.io")
    other_st = await client.get(f"{P}/usage-statement", headers=other["h"])
    assert other_st.json()["totalUsd"] == "0.000000"


# ------------------------------------------------------------- pure spend math


def _s(out, days_ago=0, cost="0.01"):
    return Sample(10, out, Decimal(cost), datetime.now(UTC) - timedelta(days=days_ago))


def test_forecast_uncertainty_and_staleness():
    assert predict_tokens([], 10)["available"] is False
    few = predict_tokens([_s(100)], 10)
    assert few["calibrated"] is False and few["outputTokens"]["high"] >= 400
    many = predict_tokens([_s(100 + i) for i in range(20)], 10)
    assert many["calibrated"] and many["outputTokens"]["p50"] <= many["outputTokens"]["p90"]
    assert predict_tokens([_s(100, days_ago=30)], 10)["stale"] is True


def test_anomaly_detection_robust_and_honest():
    flat = {f"2026-10-{d:02d}": Decimal("5") for d in range(1, 11)}
    assert (
        detect_spend_anomaly({**flat, "2026-10-11": Decimal("5.2")}, "2026-10-11")["status"]
        == "normal"
    )
    assert (
        detect_spend_anomaly({**flat, "2026-10-11": Decimal("80")}, "2026-10-11")["status"]
        == "anomaly"
    )
    assert (
        detect_spend_anomaly(
            {"2026-10-01": Decimal("1"), "2026-10-02": Decimal("99")}, "2026-10-02"
        )["status"]
        == "insufficient_history"
    )


def test_alert_levels_count_holds_and_zero_cap():
    d = Decimal
    assert budget_alert(None, d(5), d(0))["level"] == "unlimited"
    assert budget_alert(d(10), d(5), d(0))["level"] == "ok"
    assert budget_alert(d(10), d(5), d(3))["level"] == "warning"  # in-flight counts
    assert budget_alert(d(10), d(7), d(3))["level"] == "ceiling"
    assert budget_alert(d(0), d(0), d(0))["level"] == "ceiling"
    assert reconcile_usage(d(5), None)["status"] == "unsettled"
    assert reconcile_usage(d(5), d("5.005"))["status"] == "matched"
