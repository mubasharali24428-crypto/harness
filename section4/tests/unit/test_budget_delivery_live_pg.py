"""Part 57 budget alerts on a real PostgreSQL; opt in with HARNESS_TEST_PG=1 (a server DSN, like
the other *_live_pg tests).

`deliver_budget_warning` never raises into a run, so a dialect fault in its SQL would only show
up as a missing row. The sqlite tests stand in for the org and notification tables; this applies
the real migration chain to a disposable database and asserts on the inbox itself.
"""

import importlib.util
import os
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import budget, notify

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not os.environ.get("HARNESS_TEST_PG"),
    reason="HARNESS_TEST_PG not set; budget alert PG probe skipped",
)
_spec = importlib.util.spec_from_file_location(
    "harness_migrate_budget", ROOT / "deploy/runners/migrate.py"
)
assert _spec is not None and _spec.loader is not None
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)

CODES = ("budget_warning", "budget_exhausted", "plan_limit_reached")
# (user, team, role)
SEATS = (
    ("boss", "t-solo", "admin"), ("lead", "t-solo", "engineer"), ("view", "t-solo", "viewer"),
    ("bill", "t-org", "engineer"),      # billing admin of the org, sits in the capped team
    ("owner", "t-other", "viewer"),     # owner of the org, sits only in a sibling team
    ("plain", "t-org", "admin"),        # team admin, but only a plain member of the org
    ("gone", "t-org", "engineer"),      # billing admin of the org until removed
)


def server_dsn() -> str:
    raw = os.environ.get("HARNESS_TEST_PG", "")
    return raw if "://" in raw else "postgresql://harness:harness@127.0.0.1:5499/harness"


@pytest.fixture
async def database():
    """A throwaway database, dropped even when the fixture that fills it fails."""
    import asyncpg

    name = f"harness_bud_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(server_dsn())
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    yield name
    admin = await asyncpg.connect(server_dsn())
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


@pytest.fixture
async def pg(database):
    import asyncpg

    base = server_dsn().rsplit("/", 1)[0]
    name = database
    conn = await asyncpg.connect(f"{base}/{name}")
    try:
        for path in sorted((ROOT / "section1/migrations").glob("*.sql")):
            for stmt in migrate.split_statements(path.read_text(encoding="utf-8")):
                if stmt.strip():
                    await conn.execute(stmt)
        await conn.execute("INSERT INTO organizations (id, name, slug, status, created_at)"
                           " VALUES ('org-1', 'O', 'o', 'active', now())")
        for tid, org in (("t-solo", None), ("t-org", "org-1"), ("t-other", "org-1")):
            await conn.execute("INSERT INTO teams (id, name, org_id) VALUES ($1, $1, $2)", tid, org)
        for uid, tid, role in SEATS:
            await conn.execute("INSERT INTO users (id, email, name, is_active)"
                               " VALUES ($1, $2, $1, true)", uid, f"{uid}@example.test")
            await conn.execute(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ($4, $2, $1, $1, 'human', $3)", uid, tid, role, f"m-{uid}")
        await conn.execute("INSERT INTO users (id, email, name, is_active)"
                           " VALUES ('ghost', 'ghost@example.test', 'ghost', true)")
        await conn.execute("INSERT INTO users (id, email, name, is_active)"
                           " VALUES ('both', 'both@example.test', 'both', true)")
        for tid in ("t-org", "t-other"):  # an org owner with a seat in each of two teams
            await conn.execute(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ($1, $2, 'both', 'both', 'human', 'viewer')", f"m-both-{tid}", tid)
        for uid, role in (("both", "owner"), ("owner", "owner"), ("bill", "billing_admin"),
                          ("plain", "member"),
                          ("ghost", "owner"),  # owns the org but sits in none of its teams
                          ("gone", "billing_admin")):
            await conn.execute(
                "INSERT INTO org_members (org_id, user_id, role, created_at, removed_at)"
                " VALUES ('org-1', $1, $2, now(), CASE WHEN $3 THEN now() END)",
                uid, role, uid == "gone")
    finally:
        await conn.close()
    engine = create_async_engine(f"{base}/{name}".replace("postgresql://", "postgresql+asyncpg://"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def rows(factory):
    async with factory() as db:
        return (await db.execute(text(
            "SELECT user_id, team_id, summary_code, source_id, count FROM notification_inbox"
            " WHERE kind = 'budget' ORDER BY summary_code, user_id"))).all()


@pytest.mark.parametrize("code", CODES)
async def test_a_team_outside_an_org_tells_its_admin_once(pg, code):
    await budget.deliver_budget_warning(pg, "t-solo", code)
    await budget.deliver_budget_warning(pg, "t-solo", code)  # the same month or day: no second row
    got = await rows(pg)
    assert [(r.user_id, r.team_id, r.summary_code, r.source_id, r.count) for r in got] == [
        ("boss", "t-solo", code, "t-solo", 1)]


@pytest.mark.parametrize("code", CODES)
async def test_an_org_team_tells_the_org_managers_where_they_can_see_it(pg, code):
    await budget.deliver_budget_warning(pg, "t-org", code)
    got = await rows(pg)
    # bill sits in the capped team; owner only in a sibling team, so the row lands there; plain is
    # a team admin but not an org manager; ghost owns the org with no seat anywhere (nowhere to
    # show it); gone is a removed billing admin
    assert [(r.user_id, r.team_id, r.source_id) for r in got] == [
        ("bill", "t-org", "t-org"), ("both", "t-org", "t-org"), ("owner", "t-other", "t-org")]


async def test_a_manager_seated_in_the_capped_team_gets_it_there_not_in_the_first_team_by_id(pg):
    await budget.deliver_budget_warning(pg, "t-other", "budget_warning")
    got = await rows(pg)
    # `both` has seats in t-org and t-other: the capped team (t-other) wins over the earlier id
    assert [(r.user_id, r.team_id, r.source_id) for r in got] == [
        ("bill", "t-org", "t-other"), ("both", "t-other", "t-other"),
        ("owner", "t-other", "t-other")]


async def test_a_hard_stop_is_not_absorbed_by_the_unread_warning_before_it(pg):
    await budget.deliver_budget_warning(pg, "t-solo", "budget_warning")
    await budget.deliver_budget_warning(pg, "t-solo", "budget_exhausted")
    assert [(r.summary_code, r.count) for r in await rows(pg)] == [
        ("budget_exhausted", 1), ("budget_warning", 1)]


async def test_the_daily_cap_alert_repeats_each_day_but_the_monthly_ones_do_not(pg, monkeypatch):
    monkeypatch.setenv(notify.GROUP_SECONDS_ENV, "0")  # a day apart is never one burst
    for day in (5, 5, 6):  # twice on the 5th, then the 6th of the same month
        monkeypatch.setattr(budget, "db_now", lambda d=day: datetime(2999, 1, d, 9, tzinfo=UTC))
        for code in ("plan_limit_reached", "budget_exhausted"):
            await budget.deliver_budget_warning(pg, "t-solo", code)
    assert Counter(r.summary_code for r in await rows(pg)) == {
        "plan_limit_reached": 2, "budget_exhausted": 1}
