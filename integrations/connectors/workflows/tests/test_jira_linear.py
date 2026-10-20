"""F123 Linear + F124 Jira: intake from real webhook shapes, comments out."""

from __future__ import annotations

import json

import httpx
from conftest import sign_payload
from fake_providers import fake_jira, fake_linear

from harness_workflow_connectors.jira import JiraNotifier
from harness_workflow_connectors.linear import LinearNotifier


def linear_delivery(
    title: str = "Checkout latency spikes",
    delivery_id: str = "lin-del-1",
    action: str = "create",
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "type": "Issue",
            "action": action,
            "organizationId": "org-1",
            "data": {
                "id": "issue-uuid-1",
                "identifier": "ENG-123",
                "title": title,
                "description": "p95 went from 200ms to 3s after deploy 42",
                "url": "https://linear.app/acme/issue/ENG-123",
            },
        }
    ).encode()
    headers = sign_payload("linear", body)
    headers["Linear-Delivery"] = delivery_id
    return body, headers


async def test_linear_issue_proposes_plan_and_dedupes(harness):
    body, headers = linear_delivery()
    res = await harness.post("linear", body, headers)
    assert res.status_code == 202, res.text
    dup = await harness.post("linear", body, headers)
    assert dup.status_code == 200
    assert len(harness.gate.goals) == 1
    goal = harness.gate.goals[0]
    assert "provider=linear" in goal and "ENG-123" in goal
    assert "p95 went from 200ms" in goal


async def test_linear_signature_required(harness):
    body, _ = linear_delivery()
    res = await harness.post("linear", body, {})
    assert res.status_code == 401
    res2 = await harness.post("linear", body, {"Linear-Signature": "0" * 64})
    assert res2.status_code == 401
    assert harness.gate.goals == []


async def test_linear_non_create_events_ignored(harness):
    body, headers = linear_delivery(action="update", delivery_id="lin-del-2")
    res = await harness.post("linear", body, headers)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "EVENT_IGNORED"


async def test_linear_comment_posts_to_real_fake():
    async with fake_linear() as (base, fake), httpx.AsyncClient() as http:
        notifier = LinearNotifier(base, api_key=fake.api_key, client=http)
        comment = await notifier.post_comment(issue_id="issue-uuid-1", body="plan plan-1 proposed")
        assert comment["id"].startswith("c")
        assert fake.comments[0] == {"issueId": "issue-uuid-1", "body": "plan plan-1 proposed"}


def jira_delivery(
    event: str = "jira:issue_created", key: str = "OPS-7", ts: int = 12345
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "webhookEvent": event,
            "timestamp": ts,
            "baseUrl": "https://acme.atlassian.net",
            "issue": {
                "id": "10001",
                "key": key,
                "fields": {
                    "summary": "Nightly build red",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "compiler crashed on main"}],
                            }
                        ],
                    },
                },
            },
        }
    ).encode()
    return body, sign_payload("jira", body)


async def test_jira_issue_proposes_plan_with_adf_flattened(harness):
    body, headers = jira_delivery()
    res = await harness.post("jira", body, headers)
    assert res.status_code == 202, res.text
    goal = harness.gate.goals[0]
    assert "OPS-7" in goal and "Nightly build red" in goal
    assert "compiler crashed on main" in goal
    assert "https://acme.atlassian.net/browse/OPS-7" in goal


async def test_jira_replayed_identical_delivery_dedupes_by_hash(harness):
    body, headers = jira_delivery()
    assert (await harness.post("jira", body, headers)).status_code == 202
    assert (await harness.post("jira", body, headers)).status_code == 200
    assert len(harness.gate.goals) == 1


async def test_jira_comment_adf_and_auth():
    async with fake_jira() as (base, fake), httpx.AsyncClient() as http:
        notifier = JiraNotifier(base, email=fake.email, api_token=fake.token, client=http)
        created = await notifier.post_comment(issue_key="OPS-7", text="plan proposed")
        assert created["id"].startswith("1000")
        posted = fake.comments[0]
        assert posted["issue"] == "OPS-7"
        node = posted["body"]["body"]["content"][0]["content"][0]
        assert node == {"type": "text", "text": "plan proposed"}
