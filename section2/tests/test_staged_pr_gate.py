"""P1: withheld changes cannot escape via the external PR producer."""

import pytest
from sqlalchemy import text

from tests.test_service import connect_user, seed


@pytest.mark.parametrize("state", ["pending", "approved", "executing"])
async def test_pr_open_waits_for_staged_application(client, db, state):
    await seed(db)
    await connect_user(db)

    async with db() as session:
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS high_impact_approvals (id TEXT,run_id TEXT,"
                "requester_user_id TEXT,state TEXT)"
            )
        )
        await session.execute(
            text(
                "INSERT INTO agent_runs (id,project_id,branch,state) "
                "VALUES ('staged-run','p1','agent/staged-run','executing')"
            )
        )
        await session.execute(
            text("INSERT INTO high_impact_approvals VALUES ('a','staged-run','system:staging',:s)"),
            {"s": state},
        )
        await session.commit()
    response = await client.post(
        "/api/v1/projects/p1/pulls/draft",
        headers={"Authorization": "Bearer tok1"},
        json=dict(branch="agent/staged-run", baseBranch="main", title="test", bodyMd="test"),
    )
    assert response.status_code == 409, response.text
    assert not any("/pulls" in url for _, url, _ in client.fake.requests)
