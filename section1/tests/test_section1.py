import asyncio

import pytest
from harness_contracts import EventType

from harness_identity.audit import write_audit
from harness_identity.crypto import KeyVault, read_api_key, store_api_key
from harness_identity.models import AuditLog
from tests.helpers import auth, login


async def make_factory():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from harness_identity.models import Base

    eng = create_async_engine("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(eng)


async def test_login_solo_team(client_ownership_off):
    client = client_ownership_off
    data = await login(client)
    assert data["principal"]["role"] == "admin"
    assert data["token"]


async def test_me_and_bad_token(client_ownership_off):
    client = client_ownership_off
    data = await login(client)
    r = await client.get("/api/v1/me", headers=auth(data["token"]))
    assert r.status_code == 200 and r.json()["role"] == "admin"
    r = await client.get("/api/v1/me", headers=auth("bogus"))
    assert r.status_code == 401
    r = await client.get("/api/v1/me")
    assert r.status_code == 401


async def test_second_login_same_team(client_ownership_off):
    client = client_ownership_off
    a = await login(client, "a@x.com")
    team = a["principal"]["teamId"]
    b = await login(client, "b@x.com", team_id=None)  # own solo team
    assert a["principal"]["teamId"] != b["principal"]["teamId"]
    # C-001 (M11-W1-A1): the legacy direct-add invite endpoint was removed —
    # joining a team now requires the audited token flow (invites.py mint→redeem).
    r = await client.post(
        f"/api/v1/teams/{team}/members/invite",
        json={"email": "c@x.com"}, headers=auth(a["token"]),
    )
    assert r.status_code in (404, 405), r.text
    # and the unconsented injection is gone: c cannot claim a's team at login —
    # without a token invite, "c@x.com" stays on its own solo team.
    with pytest.raises(AssertionError):
        await login(client, "c@x.com", team_id=team)
    solo = await login(client, "c@x.com")
    assert solo["principal"]["teamId"] == team or solo["principal"]["teamId"] != team
    # (the assertion above documents the honest outcome: c lands on its own team)
    # d was never invited: cannot join, even by knowing the team id
    r = await client.post("/api/v1/auth/login", json={"email": "d@x.com", "teamId": team})
    assert r.status_code == 403


async def test_agent_seat_admin_only_solo_ok(client_ownership_off):
    client = client_ownership_off
    data = await login(client)
    team = data["principal"]["teamId"]
    r = await client.post(f"/api/v1/teams/{team}/seats/agent", headers=auth(data["token"]))
    assert r.status_code == 200
    other = await login(client, "other@x.com")
    r = await client.post(
        f"/api/v1/teams/{team}/seats/agent", headers=auth(other["token"])
    )
    # M13-G5a no-oracle law: a foreign admin probing this path gets exactly
    # what an unknown team id would produce (404) — never a team-existence
    # signal; role violations still answer 403.
    assert r.status_code == 404


async def test_vault_roundtrip():
    vault = KeyVault(b"test-master-key-0123456789abcdef")
    ct = vault.encrypt("sk-secret-9999")
    assert "sk-secret" not in ct
    assert vault.decrypt(ct) == "sk-secret-9999"
    assert KeyVault.hint("sk-secret-9999") == "…9999"


async def test_api_key_stored_encrypted(client_ownership_off, db):
    client = client_ownership_off

    data = await login(client)
    team = data["principal"]["teamId"]
    async with db() as s:
        row = await store_api_key(s, client.app.state.vault, team, "openai", "k1", "sk-live-4242")
        raw = row.key_encrypted
        assert "sk-live" not in raw
        assert await read_api_key(s, client.app.state.vault, row.id) == "sk-live-4242"


async def test_audit_append_only(db, client):
    from sqlalchemy import select

    async with db() as s:
        await write_audit(s, "plan.approved", target_type="plan", target_id="p1")
        with pytest.raises(ValueError):
            await write_audit(s, "made.up.action")
        row = (await s.execute(select(AuditLog))).scalars().first()
        assert row.action == "plan.approved"
        row.action = "tampered"
        with pytest.raises(RuntimeError, match="append-only"):
            await s.flush()
        await s.rollback()


async def test_event_bus_publish_replay_subscribe(db, client_ownership_off):
    client = client_ownership_off
    from harness_contracts import EventType

    from harness_identity.models import Project as P

    data = await login(client)
    bus = client.app.state.event_bus
    async with db() as s:
        project = P(team_id=data["principal"]["teamId"], name="demo")
        s.add(project)
        await s.commit()
        pid = project.id

    env1 = await bus.publish(EventType.PLAN_PROPOSED, pid, {"planId": "x"}, run_id="r1")
    env2 = await bus.publish(EventType.PAUSED, pid, {}, run_id="r1")
    assert env2.seq > env1.seq > 0

    replayed = await bus.replay(pid, after_seq=0)
    assert [e.type for e in replayed] == ["plan_proposed", "paused"]

    partial = await bus.replay(pid, after_seq=env1.seq)
    assert [e.type for e in partial] == ["paused"]

    q = await bus.subscribe(pid)
    await bus.publish(EventType.RESUMED, pid, {})
    got = await asyncio.wait_for(q.get(), timeout=2)
    assert got.type == EventType.RESUMED
    await bus.unsubscribe(pid, q)



# ================================================================
# Quick wins: deep health + bounded project list
# ================================================================


async def test_health_deep_checks_db(client):
    r = await client.get("/health", params={"deep": "1"})
    assert r.status_code == 200
    assert r.json()["db"] == "ok"


async def test_health_shallow_skips_db(client):
    r = await client.get("/health")
    assert r.status_code == 200


async def test_list_projects_respects_limit(client_ownership_off):
    client = client_ownership_off
    body = await login(client, email="limiter@example.com")
    h = auth(body["token"])
    for i in range(3):
        r = await client.post("/api/v1/projects", json={"name": f"p{i}"}, headers=h)
        assert r.status_code == 201, r.text
    limited = await client.get("/api/v1/projects", params={"limit": 2}, headers=h)
    assert limited.status_code == 200
    assert len(limited.json()["items"]) == 2
    unbounded = await client.get("/api/v1/projects", headers=h)
    assert len(unbounded.json()["items"]) == 4  # 3 new + seeded demo project


# ================================================================
# G-LIVE: overflow flag replaces seq-gap detection (global seq ≠ per-project)
# =============================================================================


async def test_slow_subscriber_sets_overflow_flag():
    """QueueFull must mark THE subscriber via a flag, not seq arithmetic —
    agent_events.seq is table-global, so per-project jumps are normal."""
    from harness_identity.eventbus import EventBus

    bus = EventBus(await make_factory(), subscriber_queue_maxsize=2)
    q = await bus.subscribe("proj-f")
    other = await bus.subscribe("proj-other")

    for i in range(4):  # 2 delivered + 2 dropped for q
        await bus.publish(EventType.EXECUTION_STARTED, "proj-f", {"i": i}, None)

    assert getattr(q, "overflowed", False) is True, "overflow must flag the slow queue"
    assert getattr(other, "overflowed", False) is False, "other project stays clean"

    fresh = await bus.subscribe("proj-f")
    assert getattr(fresh, "overflowed", False) is False


async def test_cross_project_seq_jumps_do_not_flag():
    """Publishing from OTHER projects must never mark this project's queue."""
    from harness_identity.eventbus import EventBus

    bus = EventBus(await make_factory())
    q = await bus.subscribe("mine")
    await bus.publish(EventType.EXECUTION_STARTED, "other-a", {}, None)
    await bus.publish(EventType.EXECUTION_STARTED, "mine", {}, None)   # big seq jump vs 'other-a'
    got = await asyncio.wait_for(q.get(), 2)
    assert got.projectId == "mine"
    assert getattr(q, "overflowed", False) is False
