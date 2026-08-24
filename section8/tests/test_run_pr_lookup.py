"""M12-W2 lane E4a: consumer-side hook — resolve a run's opened PR (for E4b).

Section 8's review page needs run → prId resolution. The producer (S2) writes
agent_runs.pr_id + emits pr_opened; this pins the S8 read half against the
same golden fixtures so VT-4 can check both sides of W2-C2.
"""

import pytest
from sqlalchemy import text

from harness_review.store import get_run_pr_record
from tests.fixtures.golden_fixtures import (
    GOLDEN_GITHUB_PR_NUMBER,
    GOLDEN_PR_ID,
    GOLDEN_PR_URL,
    GOLDEN_RUN_ID,
)


@pytest.fixture(autouse=True)
async def _prid_column(seeded_db):
    """Migration 0014 for the sqlite test DB.

    S8 builds its ephemeral schema via S1 ORM create_all, whose AgentRun
    mirror predates 0014 (the column lives in the SQL migration only).
    Real databases get pr_id from section1/migrations/0014_pr_id.sql.
    """
    async with seeded_db() as s:
        await s.execute(text("ALTER TABLE agent_runs ADD COLUMN pr_id VARCHAR(36)"))
        await s.commit()
    yield seeded_db


async def _seed_pr_link(db):
    """The golden PR, linked to its run via agent_runs.pr_id."""
    async with db() as s:
        await s.execute(
            text(
                "UPDATE pull_requests SET run_id = :run WHERE id = :pr"
            ),
            {"pr": GOLDEN_PR_ID, "run": GOLDEN_RUN_ID},
        )
        await s.execute(
            text("UPDATE agent_runs SET pr_id = :pr WHERE id = :run"),
            {"pr": GOLDEN_PR_ID, "run": GOLDEN_RUN_ID},
        )
        await s.commit()


async def test_get_run_pr_record_returns_linked_pr(seeded_db):
    await _seed_pr_link(seeded_db)
    async with seeded_db() as db:
        record = await get_run_pr_record(db, GOLDEN_RUN_ID)
    assert record is not None
    assert record == {
        "runId": GOLDEN_RUN_ID,
        "prId": GOLDEN_PR_ID,
        "url": GOLDEN_PR_URL,
        "githubPrNumber": GOLDEN_GITHUB_PR_NUMBER,
    }


async def test_get_run_pr_record_none_before_any_pr(seeded_db):
    """The golden run exists but has not opened a PR yet → None, never a raise."""
    async with seeded_db() as db:
        record = await get_run_pr_record(db, GOLDEN_RUN_ID)
    assert record is None


async def test_get_run_pr_record_unknown_run(seeded_db):
    async with seeded_db() as db:
        record = await get_run_pr_record(db, "no-such-run")
    assert record is None


def test_w2c2_payload_shape_is_the_wire_contract():
    """The triple every consumer of pr_opened may rely on (W2-C2)."""
    from harness_github.pr_open import opened_payload

    assert opened_payload({"runId": "r", "prId": "p", "url": "u"}) == {
        "runId": "r",
        "prId": "p",
        "url": "u",
    }
