"""Part 57 (directive 005): the warning-threshold crossing reaches the people who can act on it.

Before this, crossing the threshold only logged and flagged the run. Now a near-cap run puts one
`budget` notification in the inbox of each billing manager of the team: team admins for a team
outside an org, org owners and billing admins for an org-linked team (the rule of
section1 `billing._billing_manager`). One per person per team per UTC month.

Runs the real gate (`OrchestratorService`) and the real migration 0046 on sqlite."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import budget as budget_mod
from harness_orchestrator import notify
from harness_orchestrator.budget import ENV_BUDGET_WARN_PCT, ENV_TEAM_BUDGET_USD
from tests.unit.test_budget import (
    DDL,
    SEED,
    _add_spend,
    _approve,
    _drive_to_spawn,
    _wire_service,
    run,
)
from tests.unit.test_notify import migration_statements

MEMBERS = (
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36), display_name VARCHAR(200) NOT NULL DEFAULT 'x',"
    " member_type VARCHAR(16) NOT NULL DEFAULT 'human',"
    " role VARCHAR(16) NOT NULL DEFAULT 'engineer')"
)
ORG_SCHEMA = (
    "ALTER TABLE teams ADD COLUMN org_id VARCHAR(36)",
    "CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY,"
    " status VARCHAR(16) NOT NULL DEFAULT 'active',"
    " execution_disabled INTEGER NOT NULL DEFAULT 0)",  # read by the run-start org gate
    "CREATE TABLE org_members (org_id VARCHAR(36) NOT NULL, user_id VARCHAR(36) NOT NULL,"
    " role VARCHAR(16) NOT NULL, removed_at TIMESTAMP, PRIMARY KEY (org_id, user_id))",
)
# (user, team, role): boss is the only billing manager of team-a outside an org
PEOPLE = (
    ("boss", "team-a", "admin"), ("lead", "team-a", "engineer"), ("view", "team-a", "viewer"),
    ("evil", "team-b", "admin"),
)


class StubEngine:
    def execute_goal(self, goal=None, session_id=None):
        return {"sessionId": session_id, "engine": "stub", "status": "completed",
                "finalResponse": "done"}


async def _exec(factory, *stmts):
    async with factory() as db:
        for stmt in stmts:
            await db.execute(text(stmt))
        await db.commit()


async def _budget_rows(factory):
    async with factory() as db:
        return (await db.execute(text(
            "SELECT user_id, team_id, kind, summary_code, source_kind, source_id, run_id, count"
            " FROM notification_inbox WHERE kind = 'budget' ORDER BY user_id"))).all()


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "2.0")
    monkeypatch.delenv(ENV_BUDGET_WARN_PCT, raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/w.db")
    f = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        body = "\n".join(x for x in DDL.splitlines() if not x.strip().startswith("--"))
        stmts = [s.strip() for s in body.split(";") if s.strip()]
        await _exec(f, *stmts, *SEED, MEMBERS, *migration_statements())
        await _exec(f, *(
            f"INSERT INTO team_members (id, team_id, user_id, role)"
            f" VALUES ('m-{u}', '{t}', '{u}', '{r}')" for u, t, r in PEOPLE))
        await _exec(f, "INSERT INTO team_members (id, team_id, user_id, role)"
                       " VALUES ('m-agent', 'team-a', NULL, 'admin')")

    run(setup())
    run(_add_spend(f, "run-a", 1.7))  # 85% of the $2 cap: over the 80% warning line
    return f


def _near_cap_run(factory, run_id):
    svc, _ = _wire_service(factory, StubEngine())
    _approve(svc, run_id)
    assert _drive_to_spawn(svc, run_id, over_budget=False) is True
    run(asyncio.wait_for(svc.runs[run_id]["task"], timeout=5))
    return svc


def test_near_cap_run_tells_the_team_admin_once_and_nobody_else(factory):
    svc = _near_cap_run(factory, "w1")
    assert svc.runs["w1"]["budget_warned"] is True

    (row,) = run(_budget_rows(factory))
    assert (row.user_id, row.team_id, row.summary_code) == ("boss", "team-a", "budget_warning")
    assert (row.source_kind, row.source_id, row.run_id, row.count) == ("team", "team-a", None, 1)

    _near_cap_run(factory, "w2")  # a second run crossing the line in the same month
    assert len(run(_budget_rows(factory))) == 1  # one per person per team per month


def test_under_the_line_tells_nobody(factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "100.0")
    _near_cap_run(factory, "quiet")
    assert run(_budget_rows(factory)) == []


def test_each_month_gets_its_own_warning(factory, monkeypatch):
    monkeypatch.setenv(notify.GROUP_SECONDS_ENV, "0")  # a month apart is never one burst
    run(budget_mod.deliver_budget_warning(factory, "team-a"))
    run(budget_mod.deliver_budget_warning(factory, "team-a"))
    assert len(run(_budget_rows(factory))) == 1

    monkeypatch.setattr(budget_mod, "db_now", lambda: datetime(2999, 1, 5, tzinfo=UTC))
    run(budget_mod.deliver_budget_warning(factory, "team-a"))
    assert len(run(_budget_rows(factory))) == 2  # the first row is still unread: new month, new row


def test_org_linked_team_tells_org_billing_managers_not_team_admins(factory):
    run(_exec(factory, *ORG_SCHEMA,
              "INSERT INTO organizations (id) VALUES ('org-1')",
              "UPDATE teams SET org_id = 'org-1' WHERE id = 'team-a'",
              "INSERT INTO team_members (id, team_id, user_id, role)"
              " VALUES ('m-own', 'team-a', 'owner1', 'engineer'),"
              " ('m-bil', 'team-a', 'bill1', 'viewer'),"
              " ('m-gone', 'team-a', 'gone1', 'engineer'),"
              " ('m-oadm', 'team-a', 'orgadm', 'engineer')",
              "INSERT INTO org_members (org_id, user_id, role, removed_at) VALUES"
              " ('org-1', 'owner1', 'owner', NULL), ('org-1', 'bill1', 'billing_admin', NULL),"
              " ('org-1', 'gone1', 'billing_admin', '2026-01-01'),"
              " ('org-1', 'orgadm', 'admin', NULL), ('org-1', 'boss', 'member', NULL)"))
    _near_cap_run(factory, "o1")
    # boss is a team admin but only an org member; orgadm is an org admin, not a billing manager
    assert [r.user_id for r in run(_budget_rows(factory))] == ["bill1", "owner1"]


def test_a_delivery_failure_never_touches_the_run(factory):
    run(_exec(factory, "DROP TABLE team_members"))  # the recipient lookup itself raises
    svc = _near_cap_run(factory, "broken")  # asserts the engine task still ran to completion
    assert svc.runs["broken"]["budget_warned"] is True
