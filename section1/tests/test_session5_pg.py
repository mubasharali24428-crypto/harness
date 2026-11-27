"""Session 5 (part 47) on real Postgres: migrations through 0046 plus the notifications router
through the real app, then the audit hash chain over what it wrote. SQLite cannot catch dialect
faults in its raw SQL (jsonb, ON CONFLICT, TIMESTAMPTZ binds). Opt-in (HARNESS_TEST_PG=1);
builds and drops its own throwaway database."""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.audit_chain import verify_chain
from harness_identity.config import Settings

ROOT = Path(__file__).resolve().parents[2]
N = "/api/v1/notifications"

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG session 5 tests skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", ROOT / "deploy" / "runners" / "migrate.py"
)
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else (os.environ.get("HARNESS_TEST_PG_DSN") or "postgresql://harness:harness@127.0.0.1:5499/harness")


@pytest.fixture
async def env():
    import asyncpg

    name = f"harness_s5id_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        for path in sorted((ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                await conn.execute(stmt)
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
    )
    app = make_app(factory, settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.engine = engine
        yield c
    await engine.dispose()
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def login(c, email):
    r = await c.post("/api/v1/auth/login", json={"email": email})
    assert r.status_code == 200, r.text
    j = r.json()
    return {"Authorization": f"Bearer {j['token']}"}, j["principal"]


async def seed(c, principal, n):
    ids = []
    async with c.engine.begin() as conn:
        for i in range(n):
            nid = str(uuid.uuid4())
            ids.append(nid)
            await conn.execute(text(
                "INSERT INTO notification_inbox (id, team_id, user_id, kind, source_kind,"
                " source_id, urgent, group_key, count, summary_code, state, created_at,"
                " updated_at, delivery, email_state, email_attempts) VALUES (:i,:t,:u,'approval',"
                "'approval',:s,false,:g,1,'approval_requested','unread',now(),now(),"
                "CAST(:d AS jsonb),'none',0)"),
                {"i": nid, "t": principal["teamId"], "u": principal["userId"], "s": f"s{i}",
                 "g": f"approval:{nid}",
                 "d": '{"inapp": "delivered", "email": {"state": "none", "reason": null}}'})
    return ids


async def test_notifications_on_real_postgres(env):
    c = env
    ha, alice = await login(c, "alice@example.com")
    hb, _ = await login(c, "bob@example.com")  # another team
    ids = await seed(c, alice, 5)

    r = await c.get(f"{N}/unread-count", headers=ha)
    assert r.status_code == 200 and r.json()["unread"] == 5, r.text
    assert (await c.get(f"{N}/unread-count", headers=hb)).json()["unread"] == 0
    page = await c.get(f"{N}?limit=2", headers=ha)
    assert page.status_code == 200 and len(page.json()["items"]) == 2 and page.json()["nextCursor"]
    nxt = await c.get(f"{N}?limit=2&cursor={page.json()['nextCursor']}", headers=ha)
    seen = {i["id"] for i in page.json()["items"]} | {i["id"] for i in nxt.json()["items"]}
    assert len(seen) == 4  # cursor paging neither repeats nor skips

    # simultaneous read of the same item is idempotent; a foreign user gets 404
    res = await asyncio.gather(*(c.post(f"{N}/{ids[0]}/read", headers=ha) for _ in range(5)))
    assert all(x.status_code == 200 for x in res), [x.text for x in res]
    assert (await c.post(f"{N}/{ids[1]}/read", headers=hb)).status_code == 404
    snooze = await c.post(f"{N}/{ids[1]}/snooze", json={"minutes": 30}, headers=ha)
    assert snooze.status_code == 200
    assert (await c.post(f"{N}/{ids[2]}/dismiss", headers=ha)).status_code == 200
    assert (await c.get(f"{N}/unread-count", headers=ha)).json()["unread"] == 2
    assert (await c.post(f"{N}/read-all", headers=ha)).status_code == 200
    assert (await c.get(f"{N}/unread-count", headers=ha)).json()["unread"] == 0

    # preferences: version compare-and-set under concurrency, one winner
    r = await c.put(f"{N}/preferences", json={"email": "immediate", "timezone": "Europe/Paris",
                                              "quietHours": {"enabled": True}}, headers=ha)
    assert r.status_code == 200, r.text
    v = r.json().get("version", 1)
    body = {"inapp": "on", "expectedVersion": v}
    res = await asyncio.gather(*(c.put(f"{N}/preferences", json=body, headers=ha)
                                 for _ in range(4)))
    codes = sorted(x.status_code for x in res)
    assert codes.count(200) == 1 and codes.count(409) == 3, [x.text for x in res]

    async with c.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO projects (id, team_id, name, repo_full_name)"
            " VALUES ('p', :t, 'p', 'l/p')"), {"t": alice["teamId"]})
        await conn.execute(text(
            "INSERT INTO agent_runs (id, project_id, state) VALUES ('r1', 'p', 'idle')"))
    # mute then unmute keeps the row (ended, not deleted)
    r = await c.post(f"{N}/mute", json={"scopeKind": "run", "scopeId": "r1", "minutes": 60},
                     headers=ha)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert (await c.delete(f"{N}/mute/{mid}", headers=ha)).status_code == 204
    async with c.engine.connect() as conn:
        kept = (await conn.execute(text("SELECT count(*) FROM notification_mutes"))).scalar_one()
        assert kept == 1
        rep = await verify_chain(conn)
        actions = {a for (a,) in (await conn.execute(text("SELECT action FROM audit_log"))).all()}
    assert rep.ok, rep.problems
    assert {"notification.preferences_changed", "notification.muted",
            "notification.unmuted", "notification.snoozed"} <= actions
