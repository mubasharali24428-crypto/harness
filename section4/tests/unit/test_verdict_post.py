"""P-15 wiring: collect_facts reads recorded facts; run() builds and posts. Offline: scratch
SQLite + httpx.MockTransport, no network."""
import json

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import verdict_post as vp

# Assembled at runtime so no contiguous token-shaped literal sits in the source (secret scanners).
GHP = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"

DDL = [
    "CREATE TABLE agent_runs (id TEXT PRIMARY KEY, project_id TEXT, pr_id TEXT)",
    "CREATE TABLE pull_requests (id TEXT PRIMARY KEY, github_pr_number INTEGER)",
    "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, type TEXT,"
    " payload TEXT, ts TEXT)",
    "CREATE TABLE agent_tool_calls (id TEXT PRIMARY KEY, run_id TEXT, tool TEXT, target TEXT,"
    " outcome TEXT, summary TEXT, reported_by TEXT, created_at TEXT)",
    "CREATE TABLE cost_ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, cost_usd REAL)",
    "CREATE TABLE agent_plans (id TEXT PRIMARY KEY, run_id TEXT, goal TEXT,"
    " estimated_cost_usd REAL, created_at TEXT)",
]
TOKEN = "tok-live-secret-123"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory, *, pr=True, approval=True, tests=True, cost=True):
    async with factory() as s:
        await s.execute(text("INSERT INTO agent_runs VALUES ('run-1','p1', :p)"),
                        {"p": "pr-1" if pr else None})
        await s.execute(text("INSERT INTO pull_requests VALUES ('pr-1', 7)"))
        await s.execute(text(
            "INSERT INTO agent_plans VALUES ('pl1','run-1',"
            f" 'fix it {GHP}', 0.05, '2026-01-01')"))
        if approval:
            payload = json.dumps({"approvedBy": "user-9", "approvedAt": "2026-11-13T00:00:01Z"})
            await s.execute(
                text("INSERT INTO agent_events (run_id, type, payload, ts) VALUES"
                     " ('run-1','plan_approved', :pl, '2026-11-13T00:00:00Z')"),
                {"pl": payload})
        if tests:
            await s.execute(text(
                "INSERT INTO agent_tool_calls VALUES ('c1','run-1','bash','pytest -q',"
                " 'ok','pytest -q (exit 0)','engine','2026-01-01')"))
        if cost:
            await s.execute(
                text("INSERT INTO cost_ledger (run_id, cost_usd) VALUES ('run-1', 0.0123)"))
        await s.commit()


async def test_collect_reads_recorded_facts(factory):
    await _seed(factory)
    async with factory() as db:
        got = await vp.collect_facts(db, "run-1")
    f = got["facts"]
    assert got["pr_number"] == 7 and got["project_id"] == "p1"
    assert f["plan"]["approved_by"] == "user-9"
    assert f["cost"]["actual_usd"] == pytest.approx(0.0123)
    assert f["tests"]["known"] is True and f["tests"]["runs"][0]["result"] == "passed"


async def test_nothing_recorded_stays_unknown(factory):
    await _seed(factory, approval=False, tests=False, cost=False)
    async with factory() as db:
        f = (await vp.collect_facts(db, "run-1"))["facts"]
    assert f["plan"] == {} and f["cost"]["actual_usd"] is None
    assert f["tests"]["known"] is False


async def test_unknown_run_or_unlinked_pr_raises(factory):
    await _seed(factory, pr=False)
    async with factory() as db:
        with pytest.raises(LookupError):
            await vp.collect_facts(db, "ghost")
        with pytest.raises(LookupError):
            await vp.collect_facts(db, "run-1")  # no PR linked


def _transport(calls, risks=None, github_status=200):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.headers.get("authorization"),
                      json.loads(req.content) if req.content else None))
        if req.url.path.endswith("/risks"):
            return httpx.Response(200 if risks is not None else 503, json=risks or [])
        return httpx.Response(github_status, json={"commentId": 1, "action": "created"})
    return httpx.MockTransport(handler)


async def test_run_builds_and_posts_without_leaking(factory):
    await _seed(factory)
    calls: list = []
    risks = [{"path": "auth.py", "severity": "high", "reason": "auth path"}]
    out = await vp.run(
        factory, "run-1", github_url="http://gh", review_url="http://rv", token=TOKEN,
        transport=_transport(calls, risks=risks))
    assert out["action"] == "created"
    post = [c for c in calls if c[0] == "POST"][0]
    assert post[1] == "/api/v1/projects/p1/pulls/7/verdict-comment"
    assert post[3]["marker"] == "<!-- harness-verdict:v1 run=run-1 -->"
    body = post[3]["bodyMd"]
    assert post[3]["marker"] in body
    assert "ghp_abcdef" not in body and TOKEN not in body  # goal token + caller token scrubbed
    assert "auth.py" in body and "user-9" in body


async def test_risk_service_down_is_not_recorded_not_none_flagged(factory):
    await _seed(factory)
    out = await vp.run(
        factory, "run-1", github_url="http://gh", review_url="http://rv", token=TOKEN,
        dry_run=True, transport=_transport([], risks=None))
    assert out["dryRun"] is True
    assert "risk analysis unavailable" in out["body"] and "none flagged" not in out["body"]


async def test_dry_run_posts_nothing(factory):
    await _seed(factory)
    calls: list = []
    await vp.run(factory, "run-1", github_url="http://gh", review_url="http://rv", token=TOKEN,
                 dry_run=True, transport=_transport(calls, risks=[]))
    assert not any(c[0] == "POST" for c in calls)
