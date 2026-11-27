"""Part 11.006/7 on real Postgres: migration 0037's NOTIFY really wakes the bus listener,
and the signal carries identity only (never the payload). Opt-in like the other live-PG
suites (HARNESS_TEST_PG=1); uses a throwaway database."""

import asyncio
import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.eventbus import EventBus

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; live-PG collab push tests skipped",
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
async def pg():
    import asyncpg

    name = f"harness_push_{uuid.uuid4().hex[:10]}"
    base = _server_dsn().rsplit("/", 1)[0]
    admin = await asyncpg.connect(_server_dsn())
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    conn = await asyncpg.connect(f"{base}/{name}")
    for path in sorted((ROOT / "section1" / "migrations").glob("*.sql")):
        for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
            await conn.execute(stmt)
    await conn.execute("INSERT INTO teams (id, name) VALUES ('t1', 'a')")
    await conn.execute(
        "INSERT INTO projects (id, team_id, name, repo_full_name) VALUES ('p', 't1', 'p', 'l/p')"
    )
    await conn.execute("INSERT INTO agent_runs (id, project_id, state) VALUES ('r1', 'p', 'idle')")
    yield {"conn": conn, "dsn": f"{base}/{name}"}
    await conn.close()
    admin = await asyncpg.connect(_server_dsn())
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.close()


INSERT = (
    "INSERT INTO run_collab_events (run_id, team_id, actor_user_id, kind, payload)"
    " VALUES ('r1', 't1', 'u1', 'participant.joined', $1::jsonb) RETURNING seq"
)


async def test_trigger_notifies_with_identity_only(pg):
    got: list[str] = []
    await pg["conn"].add_listener("collab_events", lambda *a: got.append(a[3]))
    seq = await pg["conn"].fetchval(INSERT, json.dumps({"content": "SENSITIVE-BODY"}))
    for _ in range(50):
        if got:
            break
        await asyncio.sleep(0.05)
    assert got, "no NOTIFY received"
    body = json.loads(got[0])
    assert body == {"seq": seq, "runId": "r1", "kind": "participant.joined"}
    assert "SENSITIVE-BODY" not in got[0]


async def test_bus_listener_wakes_a_run_subscriber_end_to_end(pg):
    engine = create_async_engine(pg["dsn"].replace("postgresql://", "postgresql+asyncpg://"))
    bus = EventBus(async_sessionmaker(engine, expire_on_commit=False))
    wake = bus.collab_waker("r1")  # also starts the LISTEN task
    try:
        await asyncio.sleep(0.5)  # let the listener connect and LISTEN
        seq = await pg["conn"].fetchval(INSERT, json.dumps({}))
        await asyncio.wait_for(wake.wait(), timeout=5)
        frames = await bus.collab_since("r1", "t1", 0)
        assert [f["seq"] for f in frames] == [seq]
        assert (await bus.collab_since("r1", "other-team", 0)) == []  # team-scoped in SQL
    finally:
        bus.release_collab_waker("r1", wake)
        if bus._listener_task:
            bus._listener_task.cancel()
        await engine.dispose()
