"""Task 14: the collab approvals list carries similar denial cases (team-scoped, max 3)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import collab
from harness_orchestrator.case_memory import MAX_RECALLED, record_case

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)

CASE_DDL = (
    "CREATE TABLE case_memory (case_id VARCHAR(36) PRIMARY KEY, team_id TEXT NOT NULL,"
    " project_id TEXT, run_id TEXT NOT NULL, failure_class TEXT, tool TEXT, paths TEXT,"
    " fingerprint TEXT, outcome TEXT, weight REAL, suggested_rule TEXT,"
    " status TEXT CHECK (status = 'proposed'), recorded_at TIMESTAMP)",
    "CREATE TABLE high_impact_approvals (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, requester_user_id VARCHAR(36) NOT NULL,"
    " action VARCHAR(16) NOT NULL, paths TEXT NOT NULL, summary TEXT NOT NULL,"
    " diff TEXT NOT NULL DEFAULT '', digest VARCHAR(64) NOT NULL,"
    " state VARCHAR(16) NOT NULL DEFAULT 'pending', decided_by VARCHAR(36),"
    " decided_at TIMESTAMP, expires_at TIMESTAMP NOT NULL, outcome TEXT, created_at TIMESTAMP)",
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL, actor_user_id VARCHAR(36),"
    " kind TEXT NOT NULL, request_id VARCHAR(36), payload TEXT, ts TIMESTAMP)",
)


def _db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/similar.db")

    async def insert_case(db, team, run_id, when, paths=("/etc/nginx/nginx.conf",), tool="bash"):
        c = record_case(run_id, "denials_repeated", tool, list(paths), "denied", when)
        await db.execute(text(
            "INSERT INTO case_memory (case_id, team_id, project_id, run_id, failure_class,"
            " tool, paths, fingerprint, outcome, weight, suggested_rule, status, recorded_at)"
            " VALUES (:c, :t, :p, :r, :f, :tool, :paths, :fp, :o, :w, :s, 'proposed', :at)"),
            {"c": c["caseId"], "t": team, "p": "p", "r": run_id, "f": c["failureClass"],
             "tool": c["tool"], "paths": json.dumps(c["paths"]), "fp": c["fingerprint"],
             "o": c["outcome"], "w": c["weight"], "s": c["suggestedRule"], "at": when})

    async def setup():
        async with engine.begin() as conn:
            for d in CASE_DDL:
                await conn.execute(text(d))
            await insert_case(conn, "t1", "r-now", NOW)
            await insert_case(conn, "t1", "r-old", NOW - timedelta(days=1))
            await insert_case(conn, "t1", "r-old2", NOW - timedelta(days=2))
            await insert_case(conn, "t1", "r-unrelated", NOW, paths=("/etc/other.conf",), tool="Write")
            await insert_case(conn, "t2", "r-foreign", NOW)  # same path, other team
            await conn.execute(text(
                "INSERT INTO high_impact_approvals (id, run_id, team_id, requester_user_id,"
                " action, paths, summary, digest, expires_at, created_at)"
                " VALUES ('a1', 'r-now', 't1', 'eng', 'modify', :p, 's', 'd', :x, :c)"),
                {"p": json.dumps(["/etc/nginx/nginx.conf"]),
                 "x": datetime.now(UTC) + timedelta(days=1), "c": NOW})

    asyncio.run(setup())
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_similar_cases_are_team_scoped_bounded_and_expose_no_other_teams_text(tmp_path):
    engine, factory = _db(tmp_path)
    try:
        async def go():
            similar = await collab.similar_cases_for_run(factory, "r-now")
            approvals = await collab.list_approvals(factory, "r-now")
            return similar, approvals

        similar, approvals = asyncio.run(go())
        assert [c["runId"] for c in similar] == ["r-old", "r-old2"]
        assert len(similar) <= MAX_RECALLED
        assert "r-foreign" not in json.dumps(similar)
        assert "r-unrelated" not in json.dumps(similar)
        assert approvals[0]["similarCases"] == similar
    finally:
        asyncio.run(engine.dispose())


def test_a_run_with_no_recorded_case_has_no_similar_cases(tmp_path):
    engine, factory = _db(tmp_path)
    try:
        assert asyncio.run(collab.similar_cases_for_run(factory, "r-unknown")) == []
    finally:
        asyncio.run(engine.dispose())
