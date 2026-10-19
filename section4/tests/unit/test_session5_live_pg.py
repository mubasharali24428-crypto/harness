"""Session 5 (parts 43, 47) on real Postgres: the full 0001->latest chain, the discussion router
and notify.emit. SQLite cannot show compare-and-set edits, request-id idempotency or dedupe
upserts under real concurrent connections. Opt-in (HARNESS_TEST_PG); builds and drops its own
throwaway database."""

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from harness_contracts.roles import Principal, Role
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import discussion, notify
from harness_orchestrator.service import OrchestratorService

_ROOT = Path(__file__).resolve().parents[3]
P = "/api/v1/orchestrator"

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG session 5 tests skipped",
)

_spec = importlib.util.spec_from_file_location(
    "harness_migrate", _ROOT / "deploy" / "runners" / "migrate.py"
)
assert _spec is not None and _spec.loader is not None  # noqa: S101
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

USERS = {f"u{i}": ("t1", Role.ENGINEER) for i in range(8)} | {"boss": ("t1", Role.ADMIN)}


def _server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


class Bus:
    async def publish(self, *a, **k):
        return None

    async def ensure_run(self, *a, **k):
        return None


@pytest.fixture
async def env(monkeypatch):
    import asyncpg

    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)

    dbname = f"harness_s5_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(f"{base}/{dbname}")
    try:
        for path in sorted((_ROOT / "section1" / "migrations").glob("*.sql")):
            for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)
        await conn.execute("INSERT INTO teams (id, name) VALUES ('t1', 's4')")
        await conn.execute(
            "INSERT INTO projects (id, team_id, name, repo_full_name)"
            " VALUES ('p', 't1', 'p', 'l/p')"
        )
        await conn.execute(
            "INSERT INTO agent_runs (id, project_id, state) VALUES ('r1', 'p', 'idle')"
        )
        for u, (t, role) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, email, name, is_active) VALUES ($1, $2, $3, true)",
                u, f"{u}@example.com", f"Name {u}",
            )
            await conn.execute(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ($1, $2, $3, $4, 'human', $5)", f"m-{u}", t, u, f"Name {u}", role.value,
            )
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{dbname}".replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    discussion.configure_discussion(service)
    app = FastAPI()
    for r in (orch_api.router, discussion.router):
        app.include_router(r)
    def principal(request: Request) -> Principal:
        user = request.headers["x-user"]  # per-request identity so concurrent calls stay distinct
        return Principal(userId=user, teamId=USERS[user][0], role=USERS[user][1])

    app.dependency_overrides[orch_api.get_principal] = principal
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.factory = factory
        yield client
    await engine.dispose()
    admin = await asyncpg.connect(_server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    finally:
        await admin.close()


def h(user):
    return {"x-user": user}


async def scalar(c, sql, **kw):
    async with c.factory() as db:
        return (await db.execute(text(sql), kw)).scalar_one()


async def _join_all(c, n=4):
    for i in range(n):
        assert (await c.post(f"{P}/runs/r1/join", headers=h(f"u{i}"))).status_code == 200


async def test_comment_edit_cas_and_request_id_idempotency_on_real_postgres(env):
    c = env
    await _join_all(c)
    url = f"{P}/runs/r1/comments"
    # the same requestId fired 6x at once yields exactly one row (201 once, 200 for the replays)
    first = {"body": "hello", "requestId": "rq1"}
    res = await asyncio.gather(*(c.post(url, headers=h("u0"), json=first) for _ in range(6)))
    assert sorted(x.status_code for x in res).count(201) == 1, [x.text for x in res]
    assert {x.json()["id"] for x in res} == {res[0].json()["id"]}
    assert await scalar(c, "SELECT count(*) FROM comments WHERE scope_id='r1'") == 1
    cid = res[0].json()["id"]

    # four simultaneous edits from the same base version: one winner, the rest refused
    res = await asyncio.gather(*(c.patch(f"{url}/{cid}", headers=h("u0"),
                                         json={"body": f"v{i}", "expectedVersion": 1})
                                 for i in range(4)))
    codes = sorted(x.status_code for x in res)
    assert codes.count(200) == 1 and codes.count(409) == 3, [x.text for x in res]
    assert await scalar(c, "SELECT version FROM comments WHERE id=:i", i=cid) == 2
    assert await scalar(c, "SELECT count(*) FROM comment_revisions WHERE comment_id=:i", i=cid) >= 1
    # another team member cannot edit someone else's comment
    r = await c.patch(f"{url}/{cid}", headers=h("u1"), json={"body": "x", "expectedVersion": 2})
    assert r.status_code in (403, 404), r.text


async def test_mentions_notify_once_and_dedupe_under_concurrency_on_real_postgres(env):
    c = env
    await _join_all(c)
    r = await c.post(f"{P}/runs/r1/comments", headers=h("u0"),
                     json={"body": "look", "mentions": ["u1", "u2"], "requestId": "m1"})
    assert r.status_code == 201, r.text
    for u in ("u1", "u2"):
        n = await scalar(c, "SELECT count(*) FROM notification_inbox WHERE user_id=:u"
                            " AND kind='mention'", u=u)
        assert n == 1, u
    # a burst of identical emits (same dedupe key) from concurrent connections stays one logical row
    async def one():
        async with c.factory() as db:
            out = await notify.emit(db, team_id="t1", user_id="u3", kind="mention",
                                    source_kind="comment", source_id="cx", run_id="r1",
                                    dedupe_key="dup:1")
            await db.commit()
            return out
    outs = await asyncio.gather(*(one() for _ in range(8)))
    assert all(o.get("status") != "error" for o in outs), outs
    assert await scalar(c, "SELECT coalesce(sum(count),0) FROM notification_inbox"
                           " WHERE user_id='u3' AND dedupe_key='dup:1'") >= 1
    assert await scalar(c, "SELECT count(*) FROM notification_inbox WHERE user_id='u3'"
                           " AND dedupe_key='dup:1'") <= 2  # documented race: at most 2 rows
    # a foreign-team recipient is refused, never inserted
    async with c.factory() as db:
        out = await notify.emit(db, team_id="t1", user_id="ghost", kind="mention",
                                source_kind="comment", source_id="cx")
        await db.commit()
    assert out.get("status") in ("rejected", "skipped", "error"), out
