"""Part 57 (directive 005): the warning-threshold crossing reaches the people who can act on it.

Before this, crossing the threshold only logged and flagged the run. Now a near-cap run puts one
`budget` notification in the inbox of each billing manager of the team: team admins for a team
outside an org, org owners and billing admins for an org-linked team (the rule of
section1 `billing._billing_manager`). One per person per team per UTC month.

Runs the real gate (`OrchestratorService`) and the real migration 0046 on sqlite.

Directive 005, hard stop: when the monthly cap actually blocks a run (pre-spawn gate or reviewer
call) the same people get one `budget_exhausted` row per team per UTC month. A degraded denial
(money store down) is an outage, not a cap, and tells nobody."""

import asyncio
from datetime import UTC, datetime

import pytest
from harness_contracts.events import EventType
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import budget as budget_mod
from harness_orchestrator import notify
from harness_orchestrator.budget import ENV_BUDGET_WARN_PCT, ENV_TEAM_BUDGET_USD
from harness_orchestrator.reviewer import ReviewerSubAgent
from harness_orchestrator.service import OrchestratorService
from tests.unit.test_budget import (
    DDL,
    GOLDEN_CLEAN_DIFF,
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


# ------------------------------------------------------------ hard stop (cap reached)


def _blocked_run(factory, run_id):
    svc, events = _wire_service(factory, StubEngine())
    _approve(svc, run_id)
    assert _drive_to_spawn(svc, run_id, over_budget=True) is False
    return [e[1]["reason"] for e in events if e[0] is EventType.ENGINE_FAILED]


def _org_link(factory):
    run(_exec(factory, *ORG_SCHEMA,
              "INSERT INTO organizations (id) VALUES ('org-1')",
              "UPDATE teams SET org_id = 'org-1' WHERE id = 'team-a'",
              "INSERT INTO team_members (id, team_id, user_id, role)"
              " VALUES ('m-own', 'team-a', 'owner1', 'engineer'),"
              " ('m-bil', 'team-a', 'bill1', 'viewer')",
              "INSERT INTO org_members (org_id, user_id, role, removed_at) VALUES"
              " ('org-1', 'owner1', 'owner', NULL), ('org-1', 'bill1', 'billing_admin', NULL),"
              " ('org-1', 'boss', 'member', NULL)"))


def test_cap_hit_tells_the_team_admin_once_per_month(factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")  # $1.70 already spent: the cap bites
    (reason,) = _blocked_run(factory, "x1")
    assert reason.startswith("Budget cap reached")

    (row,) = run(_budget_rows(factory))
    assert (row.user_id, row.team_id, row.summary_code) == ("boss", "team-a", "budget_exhausted")
    assert (row.source_kind, row.source_id, row.run_id, row.count) == ("team", "team-a", None, 1)

    _blocked_run(factory, "x2")  # a second blocked run in the same month
    assert len(run(_budget_rows(factory))) == 1


def test_degraded_denial_is_an_outage_and_tells_nobody(factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    run(_exec(factory, "DROP TABLE cost_ledger"))  # the spend lookup raises: fail-closed, degraded
    (reason,) = _blocked_run(factory, "d1")
    assert reason.startswith("Budget gate degraded")
    assert run(_budget_rows(factory)) == []


def test_org_linked_cap_hit_tells_org_billing_managers(factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    _org_link(factory)
    _blocked_run(factory, "o1")
    rows = run(_budget_rows(factory))
    assert [(r.user_id, r.summary_code) for r in rows] == [
        ("bill1", "budget_exhausted"), ("owner1", "budget_exhausted")]


class Router:
    def call_model(self, *a, **k):
        raise AssertionError("no model call over the cap")

    def estimate_cost(self, role, input_tokens, output_tokens):
        return 0.01


def _review(factory, run_id):
    svc = OrchestratorService(engine=StubEngine(), session_factory=factory,
                              reviewer=ReviewerSubAgent(model_router=Router()))
    run(svc.propose_plan(run_id, project_id="proj-a"))
    run(svc.signal_done(run_id, diff_text=GOLDEN_CLEAN_DIFF))


def test_reviewer_call_over_the_cap_tells_the_team_admin(factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    _review(factory, "rv")
    (row,) = run(_budget_rows(factory))
    assert (row.user_id, row.summary_code) == ("boss", "budget_exhausted")


def test_reviewer_call_with_the_store_down_tells_nobody(factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    run(_exec(factory, "DROP TABLE cost_ledger"))
    _review(factory, "rv")
    assert run(_budget_rows(factory)) == []


def test_cap_hit_after_an_unread_warning_still_gets_its_own_row(factory, monkeypatch):
    _near_cap_run(factory, "w1")  # the 80% warning lands first, unread, inside the group window
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    _blocked_run(factory, "x1")
    codes = sorted(r.summary_code for r in run(_budget_rows(factory)))
    assert codes == ["budget_exhausted", "budget_warning"]


def test_a_delivery_failure_never_changes_the_blocked_runs_outcome(factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    run(_exec(factory, "DROP TABLE team_members"))  # the recipient lookup itself raises
    (reason,) = _blocked_run(factory, "b1")
    assert reason.startswith("Budget cap reached")


# ------------------------------------------- plan limit (daily run cap) and org managers


PLAN_TABLES = (
    "CREATE TABLE billing_plans (team_id TEXT PRIMARY KEY, plan TEXT NOT NULL,"
    " runs_per_day_override INTEGER, context_index_max_chunks_override INTEGER)",
    "CREATE TABLE team_daily_run_reservations (team_id TEXT NOT NULL, run_id TEXT PRIMARY KEY,"
    " reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "INSERT INTO billing_plans VALUES ('team-a', 'free', 1, NULL)",
    "INSERT INTO team_daily_run_reservations (team_id, run_id) VALUES ('team-a', 'earlier')",
)


@pytest.fixture
def plan_full(factory, monkeypatch):
    """team-a has used its one daily run; the money cap is far away."""
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "100.0")
    run(_exec(factory, *PLAN_TABLES))
    return factory


def test_daily_plan_limit_tells_the_team_admin_once_per_day(plan_full, monkeypatch):
    monkeypatch.setenv(notify.GROUP_SECONDS_ENV, "0")  # a day apart is never one burst
    monkeypatch.setattr(budget_mod, "db_now", lambda: datetime(2999, 1, 5, 9, tzinfo=UTC))
    (reason,) = _blocked_run(plan_full, "p1")
    assert reason.startswith("Plan limit reached")

    (row,) = run(_budget_rows(plan_full))
    assert (row.user_id, row.team_id, row.summary_code) == ("boss", "team-a", "plan_limit_reached")
    assert (row.source_kind, row.source_id, row.count) == ("team", "team-a", 1)

    _blocked_run(plan_full, "p2")  # a second blocked run the same day
    assert len(run(_budget_rows(plan_full))) == 1

    monkeypatch.setattr(budget_mod, "db_now", lambda: datetime(2999, 1, 6, 9, tzinfo=UTC))
    _blocked_run(plan_full, "p3")  # the next day, same month, is a new fact
    assert len(run(_budget_rows(plan_full))) == 2


def test_a_broken_daily_reservation_is_an_outage_and_tells_nobody(plan_full):
    run(_exec(plan_full, "DROP TABLE team_daily_run_reservations",
              "CREATE TABLE team_daily_run_reservations (team_id TEXT NOT NULL,"
              " run_id TEXT PRIMARY KEY)"))  # no reserved_at: the count raises, fail-closed
    (reason,) = _blocked_run(plan_full, "p1")
    assert reason.startswith("Daily run reservation degraded")
    assert run(_budget_rows(plan_full)) == []


def test_org_billing_managers_outside_the_team_get_it_in_their_own_team(factory, monkeypatch):
    monkeypatch.setenv(ENV_TEAM_BUDGET_USD, "1.0")
    _org_link(factory)
    run(_exec(factory,
              "INSERT INTO teams (id, name, org_id) VALUES ('team-c', 'C', 'org-1'),"
              " ('team-d', 'D', 'org-1')",
              "INSERT INTO team_members (id, team_id, user_id, role) VALUES"
              " ('m-o2c', 'team-d', 'owner2', 'engineer'),"
              " ('m-o2d', 'team-c', 'owner2', 'engineer')",
              "INSERT INTO org_members (org_id, user_id, role, removed_at) VALUES"
              " ('org-1', 'owner2', 'owner', NULL), ('org-1', 'homeless', 'owner', NULL)"))
    _blocked_run(factory, "o1")
    rows = run(_budget_rows(factory))
    # owner2 sits in no team-a seat: the alert lands in their first org team (team-c), once
    assert [(r.user_id, r.team_id, r.source_id) for r in rows] == [
        ("bill1", "team-a", "team-a"), ("owner1", "team-a", "team-a"),
        ("owner2", "team-c", "team-a")]  # homeless has no team at all: nowhere to show it

    _blocked_run(factory, "o2")  # the monthly dedupe still holds for the outsider
    assert len(run(_budget_rows(factory))) == 3
