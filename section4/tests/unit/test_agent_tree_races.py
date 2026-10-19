"""Part 45 (lane A): spawn limits and cancel hold under concurrent requests.

One event loop, real HTTP routes, sqlite file store. Every spawned request task is awaited
by ``gather`` BEFORE ``engine.dispose()`` (sqlite teardown trap, PROJECT-STATE section 6).
"""

import asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.unit.agent_tree_support import P, add_def_async, build, spawn_body


async def _env(tmp_path, monkeypatch, **def_over):
    engine, factory, app, who, _ = await build(tmp_path, monkeypatch)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    d = await add_def_async(factory, "coder", **def_over)
    return engine, factory, client, d


async def _rows(factory, sql, **kw):
    async with factory() as db:
        return (await db.execute(text(sql), kw)).mappings().all()


async def test_concurrent_child_spawns_cannot_exceed_children_limit(tmp_path, monkeypatch):
    engine, factory, client, d = await _env(tmp_path, monkeypatch, max_children=3)
    try:
        root = (await client.post(f"{P}/r1/agents/spawn", json=spawn_body(d, budget="1.0"))).json()
        results = await asyncio.gather(*[
            client.post(f"{P}/r1/agents/spawn", json=spawn_body(d, root["id"], budget="0.05"))
            for _ in range(8)])
        codes = sorted(r.status_code for r in results)
        assert codes == [201] * 3 + [409] * 5, [r.text for r in results]
        assert {r.json()["detail"]["error"]["code"] for r in results if r.status_code == 409} == {
            "SPAWN_CHILDREN_EXCEEDED"}
        parent = (await _rows(factory, "SELECT children_count, reserved_microusd"
                              " FROM agent_assignments WHERE id=:i", i=root["id"]))[0]
        assert (parent["children_count"], parent["reserved_microusd"]) == (3, 150_000)
        assert (await _rows(factory, "SELECT COUNT(*) AS n FROM agent_assignments"))[0]["n"] == 4
    finally:
        await client.aclose()
        await engine.dispose()


async def test_concurrent_child_spawns_cannot_exceed_parent_budget(tmp_path, monkeypatch):
    engine, factory, client, d = await _env(tmp_path, monkeypatch, max_children=5)
    try:
        root = (await client.post(f"{P}/r1/agents/spawn", json=spawn_body(d, budget="1.0"))).json()
        results = await asyncio.gather(*[
            client.post(f"{P}/r1/agents/spawn", json=spawn_body(d, root["id"], budget="0.4"))
            for _ in range(5)])
        assert sorted(r.status_code for r in results) == [201, 201, 409, 409, 409]
        parent = (await _rows(factory, "SELECT reserved_microusd, budget_microusd"
                              " FROM agent_assignments WHERE id=:i", i=root["id"]))[0]
        assert parent["reserved_microusd"] == 800_000 <= parent["budget_microusd"]
    finally:
        await client.aclose()
        await engine.dispose()


async def test_concurrent_root_spawns_cannot_exceed_run_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_AGENT_RUN_BUDGET_USD", "1.0")
    monkeypatch.setenv("HARNESS_AGENT_RUN_MAX_AGENTS", "20")
    engine, factory, client, d = await _env(tmp_path, monkeypatch)
    try:
        results = await asyncio.gather(*[
            client.post(f"{P}/r1/agents/spawn", json=spawn_body(d, budget="0.3"))
            for _ in range(6)])
        assert sorted(r.status_code for r in results) == [201] * 3 + [409] * 3
        pool = (await _rows(factory, "SELECT reserved_microusd, total_spawned"
                            " FROM agent_run_pools"))[0]
        assert (pool["reserved_microusd"], pool["total_spawned"]) == (900_000, 3)
    finally:
        await client.aclose()
        await engine.dispose()


async def test_cancel_racing_spawns_never_leaves_a_live_orphan(tmp_path, monkeypatch):
    engine, factory, client, d = await _env(tmp_path, monkeypatch, max_children=3, max_depth=3)
    try:
        for _ in range(4):
            root = (await client.post(f"{P}/r1/agents/spawn",
                                      json=spawn_body(d, budget="1.0"))).json()
            kid = (await client.post(f"{P}/r1/agents/spawn",
                                     json=spawn_body(d, root["id"], budget="0.3"))).json()
            await asyncio.gather(
                client.post(f"{P}/r1/agents/{root['id']}/cancel"),
                *[client.post(f"{P}/r1/agents/spawn", json=spawn_body(d, p, budget="0.05"))
                  for p in (root["id"], kid["id"], root["id"])])
            await client.post(f"{P}/r1/agents/{root['id']}/cancel")  # settle if one 409'd
        orphans = await _rows(
            factory,
            "SELECT c.id FROM agent_assignments c JOIN agent_assignments p ON p.id = c.parent_id"
            " WHERE c.state IN ('active','paused') AND p.state NOT IN ('active','paused')")
        assert orphans == []
        pool = (await _rows(factory, "SELECT reserved_microusd FROM agent_run_pools"))[0]
        assert pool["reserved_microusd"] == 0  # every reservation was handed back
    finally:
        await client.aclose()
        await engine.dispose()
