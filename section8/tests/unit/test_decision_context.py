"""X-07 blind-approval denominator: the rendering a reviewer worked from, fail-open to unknown."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_review.decision_context import reviewer_rendering


async def _factory(tmp_path, ddl: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d.db")
    async with engine.begin() as c:
        await c.execute(text(ddl))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_reads_member_preference(tmp_path):
    engine, f = await _factory(tmp_path, "CREATE TABLE team_members (team_id TEXT,"
                               " user_id TEXT, approval_rendering TEXT)")
    async with f() as db:
        await db.execute(text("INSERT INTO team_members VALUES ('t', 'u', 'plain'),"
                              " ('t', 'odd', 'shouty')"))
        await db.commit()
        assert await reviewer_rendering(db, "t", "u") == "plain"
        assert await reviewer_rendering(db, "t", "odd") == "unknown"  # out-of-set value
        assert await reviewer_rendering(db, "t", "nobody") == "unknown"
        assert await reviewer_rendering(db, None, "u") == "unknown"
    await engine.dispose()


@pytest.mark.asyncio
async def test_pre_0077_schema_is_unknown_not_500(tmp_path):
    engine, f = await _factory(tmp_path, "CREATE TABLE team_members (team_id TEXT, user_id TEXT)")
    async with f() as db:
        assert await reviewer_rendering(db, "t", "u") == "unknown"
    await engine.dispose()


# --- Sprint 2 Task 18: the KPI over audit rows -------------------------------------------

from harness_review.kpi import MIN_SAMPLE, aggregate, blind_approval_kpi  # noqa: E402


def _row(team, rendering, seen, action="review.approved"):
    return (action, {"teamId": team, "rendering": rendering, "viewedDetail": seen,
                     "reviewer_user_id": "secret-user"})


def test_kpi_splits_by_rendering_and_never_counts_unknown_as_blind():
    rows = ([_row("t", "plain", False)] * 3 + [_row("t", "plain", True)] * 7
            + [_row("t", "technical", None)] * 4 + [_row("t", "decision", False)] * 2
            + [_row("t", "plain", None, "review.changes_requested")]
            + [_row("other", "plain", False)] * 50)
    k = aggregate(rows, "t")
    assert k["byRendering"]["plain"] == {"decisions": 11, "approvals": 10, "withSignal": 10,
                                         "blind": 3, "unknownSignal": 0, "rate": 0.3}
    assert k["byRendering"]["technical"]["unknownSignal"] == 4
    assert k["byRendering"]["technical"]["rate"] is None  # no signal -> no rate
    assert k["byRendering"]["decision"]["rate"] is None  # 2 < MIN_SAMPLE: insufficient data
    assert k["overall"]["withSignal"] == 12 and k["overall"]["blind"] == 5
    assert "secret-user" not in str(k)  # counts only, no PII
    assert MIN_SAMPLE == 10


@pytest.mark.asyncio
async def test_kpi_reads_audit_rows_for_the_team_only(tmp_path):
    import json

    engine, f = await _factory(tmp_path, "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY"
                               " AUTOINCREMENT, action TEXT, detail TEXT)")
    async with f() as db:
        for team in ("t", "t2"):
            for i in range(12):
                d = {"teamId": team, "rendering": "plain", "viewedDetail": i % 2 == 0}
                await db.execute(text("INSERT INTO audit_log (action, detail) VALUES (:a, :d)"),
                                 {"a": "review.approved", "d": json.dumps(d)})
        await db.execute(text("INSERT INTO audit_log (action, detail) VALUES ('login', '{}')"))
        await db.commit()
        k = await blind_approval_kpi(db, "t")
    assert k["overall"]["approvals"] == 12 and k["overall"]["rate"] == 0.5
    await engine.dispose()
