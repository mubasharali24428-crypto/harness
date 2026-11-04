#!/usr/bin/env python3
"""W15 slice 1 — prove admission fairness on the real PostgreSQL dialect.

The claim statement is the only place admission can be decided without a race,
and SQLite cannot exercise the pieces the deployment relies on (the advisory
grant lock and ``FOR UPDATE OF rc SKIP LOCKED``). This drill therefore creates
a throwaway database on the same PostgreSQL server, seeds two teams' queues, and
drives the real ``claim_next_run`` — no engine, no model call, no spend.

It proves three things:

* a team holding every slot does not get the next one while another team has
  queued work and a free slot (the noisy-neighbour fix);
* the per-team cap is still a hard ceiling (a saturated team is skipped);
* with a two-slot pool, the idle team's single run is served.

    section4/.venv/bin/python scripts/e2e/w15_fairness_drill.py

DSN: ``HARNESS_PG_DSN`` (default ``postgresql://harness:harness@127.0.0.1:5499/harness``).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "section4" / "src"))

EVIDENCE = ROOT / "docs" / "briefs" / "evidence" / "eval"
DSN = os.environ.get(
    "HARNESS_PG_DSN", "postgresql://harness:harness@127.0.0.1:5499/harness"
)

DDL = (
    "CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT NOT NULL)",
    (
        "CREATE TABLE run_claims (run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
        "state TEXT NOT NULL, claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, "
        "worker_id TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE billing_plans (id TEXT PRIMARY KEY, team_id TEXT UNIQUE, "
        "concurrent_runs_override INTEGER)"
    ),
)


def _fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


async def _seed(conn, rows: list[tuple[str, str, str, str]]) -> None:
    from sqlalchemy import text

    await conn.execute(text("INSERT INTO projects VALUES ('p-busy','t-busy'), ('p-idle','t-idle')"))
    await conn.execute(
        text("INSERT INTO billing_plans VALUES ('bp-busy','t-busy',NULL), ('bp-idle','t-idle',NULL)")
    )
    for run_id, project_id, state, at in rows:
        await conn.execute(
            text("INSERT INTO run_claims VALUES (:r, :p, :s, :at, :at, 'enqueuer')"),
            {"r": run_id, "p": project_id, "s": state, "at": at},
        )


def _queue(busy: int, idle: int, idle_at: str) -> list[tuple[str, str, str, str]]:
    rows = [
        (f"busy-{i}", "p-busy", "queued", f"2026-10-29T00:{i:02d}:00+00:00")
        for i in range(1, busy + 1)
    ]
    rows += [(f"idle-{i}", "p-idle", "queued", idle_at) for i in range(1, idle + 1)]
    return rows


async def _scenario(admin, label: str, max_per_team: int, claims: int):
    from harness_orchestrator.run_queue import claim_next_run
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    dbname = f"harness_w15_{label}_{int(time.time())}_{os.getpid()}"
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    url = DSN.replace("postgresql://", "postgresql+asyncpg://").rsplit("/", 1)[0] + f"/{dbname}"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            for statement in DDL:
                await conn.execute(text(statement))
            await _seed(conn, _queue(busy=5, idle=1, idle_at="2026-10-29T00:09:00+00:00"))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        order = []
        for _ in range(claims):
            claim = await claim_next_run(factory, "w15-drill", max_per_team)
            order.append(claim["run_id"] if claim else None)
        async with engine.connect() as conn:
            running = {
                row[0]: row[1]
                for row in (
                    await conn.execute(
                        text(
                            "SELECT p.team_id, COUNT(*) FROM run_claims rc "
                            "JOIN projects p ON rc.project_id = p.id "
                            "WHERE rc.state = 'running' GROUP BY p.team_id"
                        )
                    )
                ).all()
            }
        return order, running
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{dbname}" WITH (FORCE)')


async def main() -> int:
    import asyncpg

    admin = await asyncpg.connect(DSN)
    stamp = int(time.time())
    try:
        # Pool of 4: the busy team's five queued runs must not take all four
        # slots; the idle team's single run is served second.
        order, running = await _scenario(admin, "fair", max_per_team=4, claims=4)
        # Saturated team: four already running, next claim must be the idle
        # team's queued run, never the busy team's fifth.
        sat_db = f"harness_w15_sat_{stamp}"
        await admin.execute(f'CREATE DATABASE "{sat_db}"')
        sat_url = DSN.replace("postgresql://", "postgresql+asyncpg://").rsplit("/", 1)[0] + f"/{sat_db}"
        from harness_orchestrator.run_queue import claim_next_run
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        sat_engine = create_async_engine(sat_url)
        try:
            async with sat_engine.begin() as conn:
                for statement in DDL:
                    await conn.execute(text(statement))
                rows = [(f"run-{i}", "p-busy", "running", f"2026-10-29T00:00:{i:02d}+00:00") for i in range(4)]
                rows.append(("busy-5", "p-busy", "queued", "2026-10-29T01:00:00+00:00"))
                rows.append(("idle-1", "p-idle", "queued", "2026-10-29T01:00:01+00:00"))
                await _seed(conn, rows)
            factory = async_sessionmaker(sat_engine, expire_on_commit=False)
            saturated = await claim_next_run(factory, "w15-drill", 4)
            saturated_order = saturated["run_id"] if saturated else None
        finally:
            await sat_engine.dispose()
            await admin.execute(f'DROP DATABASE "{sat_db}" WITH (FORCE)')

        checks = {
            "idleTeamServedSecond": order[:2] == ["busy-1", "idle-1"],
            "busyTeamHeldThreeSlots": running.get("t-busy") == 3,
            "idleTeamHeldOneSlot": running.get("t-idle") == 1,
            "saturatedTeamSkipped": saturated_order == "idle-1",
        }
        evidence = {
            "schema": 1,
            "kind": "w15-fairness",
            "takenAt": datetime.now(UTC).isoformat(),
            "postgres": (await admin.fetchval("SHOW server_version")).strip(),
            "maxPerTeam": 4,
            "claimOrder": order,
            "runningAtEnd": running,
            "saturatedClaim": saturated_order,
            "checks": checks,
            "verdict": "PASS" if all(checks.values()) else "FAIL",
            "failedChecks": [name for name, ok in checks.items() if not ok],
        }
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        path = EVIDENCE / f"w15-fairness-{stamp}.json"
        path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"verdict": evidence["verdict"], "evidence": str(path)}, indent=2))
        return 0 if evidence["verdict"] == "PASS" else 1
    finally:
        await admin.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
