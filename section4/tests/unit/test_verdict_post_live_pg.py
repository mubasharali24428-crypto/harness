"""P-15 wiring on real PostgreSQL; opt in with HARNESS_TEST_PG=1 (and HARNESS_DATABASE_URL).

SQLite hides PostgreSQL typing differences (JSONB payloads come back as dicts, NUMERIC as Decimal).
Seeds one run inside a transaction with foreign keys relaxed, runs collect_facts, then rolls
everything back.
"""
import json
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from harness_orchestrator import verdict_post as vp
from harness_orchestrator.verdict_comment import build_verdict_comment

# Assembled at runtime so no contiguous token-shaped literal sits in the source (secret scanners).
GHP = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"

pytestmark = pytest.mark.skipif(
    not (os.environ.get("HARNESS_TEST_PG") and os.environ.get("HARNESS_DATABASE_URL")),
    reason="HARNESS_TEST_PG / HARNESS_DATABASE_URL not set; verdict PG probe skipped",
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)  # inside a partition every migrated database has


async def test_collect_facts_and_build_on_real_pg():
    engine = create_async_engine(os.environ["HARNESS_DATABASE_URL"])
    try:
        async with AsyncSession(engine) as db:
            await db.execute(text("SET LOCAL session_replication_role = replica"))  # no FKs
            await db.execute(text(
                "INSERT INTO agent_runs (id, project_id, state, updated_at, created_at, pr_id)"
                " VALUES ('v-run','v-proj','done', now(), now(), 'v-pr')"))
            await db.execute(text(
                "INSERT INTO pull_requests (id, project_id, run_id, github_pr_number, pr_url,"
                " summary_md, status, created_at)"
                " VALUES ('v-pr','v-proj','v-run',7,'u','','open',now())"))
            await db.execute(
                text("INSERT INTO agent_events (project_id, run_id, type, payload, ts) VALUES"
                     " ('v-proj','v-run','plan_approved', CAST(:p AS jsonb), :ts)"),
                {"p": json.dumps({"approvedBy": "user-9", "approvedAt": "2026-10-15T00:00:01Z"}),
                 "ts": NOW})
            await db.execute(text(
                "INSERT INTO agent_tool_calls (id, assignment_id, run_id, team_id, tool, target,"
                " summary, outcome, cost_microusd, reported_by, created_at) VALUES"
                " ('v-c1','v-a','v-run','v-team','bash','pytest -q','pytest -q (exit 0)','ok',0,"
                "'engine', now())"))
            await db.execute(text(
                "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens, output_tokens,"
                " cost_usd, latency_ms, created_at)"
                " VALUES ('v-run','coder','m',1,1,0.0123,1,now())"))
            await db.execute(text(
                "INSERT INTO agent_plans (id, run_id, goal, files_likely_to_change, steps, tests,"
                " risks, estimated_cost_usd, forbidden_paths, expected_result, status, version,"
                " created_at) VALUES ('v-pl','v-run',"
                f" 'fix it {GHP}',"
                " '[]','[]','[]','[]',0.05,'[]','ok','approved',1, now())"))
            got = await vp.collect_facts(db, "v-run")
            await db.rollback()
        f = got["facts"]
        assert got["pr_number"] == 7 and got["project_id"] == "v-proj"
        assert f["plan"]["approved_by"] == "user-9"  # JSONB arrives as a dict, not a str
        assert float(f["cost"]["actual_usd"]) == pytest.approx(0.0123)  # NUMERIC -> Decimal
        assert f["tests"]["known"] is True and f["tests"]["runs"][0]["result"] == "passed"
        body = build_verdict_comment(f, pr_number=7)
        assert "$0.012300" in body and "user-9" in body and "ghp_abcdef" not in body
    finally:
        await engine.dispose()
