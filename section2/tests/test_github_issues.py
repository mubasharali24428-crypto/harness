"""Part 49: GitHub issues list through the connector; creating one is gated and idempotent."""

import httpx
import pytest

from harness_github.client import GitHubClient
from tests.test_service import auth, connect_user, seed

ISSUES = "/api/v1/projects/p1/issues"
KEY = "create-issue-0001"


def _creates(client) -> list[str]:
    return [u for m, u, _ in client.fake.requests if m == "POST" and u.endswith("/issues")]


async def test_list_returns_issues_and_skips_pull_requests(client, db):
    await seed(db)
    await connect_user(db)
    response = await client.get(ISSUES, headers=auth())
    assert response.status_code == 200, response.text
    assert [item["number"] for item in response.json()["items"]] == [1]


async def test_create_is_refused_while_writes_are_off(client, db):
    await seed(db)
    await connect_user(db)
    await client.put("/api/v1/projects/p1/github/writes", headers=auth(), json={"enabled": False})
    response = await client.post(
        ISSUES, headers={**auth(), "Idempotency-Key": KEY}, json={"title": "bug"}
    )
    assert response.status_code == 403, response.text
    assert _creates(client) == []


async def test_repeated_create_with_one_key_makes_one_issue(client, db):
    await seed(db)
    await connect_user(db)
    headers = {**auth(), "Idempotency-Key": KEY}
    first = await client.post(ISSUES, headers=headers, json={"title": "bug"})
    second = await client.post(ISSUES, headers=headers, json={"title": "bug"})
    assert first.status_code == 201, first.text
    assert second.json() == first.json()
    assert len(_creates(client)) == 1


async def test_create_needs_an_idempotency_key(client, db):
    await seed(db)
    await connect_user(db)
    response = await client.post(ISSUES, headers=auth(), json={"title": "bug"})
    assert response.status_code == 422, response.text
    assert _creates(client) == []


async def test_failed_create_frees_the_key_for_a_retry(client, db, monkeypatch):
    await seed(db)
    await connect_user(db)

    async def unreachable(self, repo, title, body_md):
        raise httpx.ConnectError("github unreachable")

    monkeypatch.setattr(GitHubClient, "create_issue", unreachable)
    headers = {**auth(), "Idempotency-Key": KEY}
    with pytest.raises(httpx.ConnectError):
        await client.post(ISSUES, headers=headers, json={"title": "bug"})

    monkeypatch.undo()
    retry = await client.post(ISSUES, headers=headers, json={"title": "bug"})
    assert retry.status_code == 201, retry.text
    assert len(_creates(client)) == 1
