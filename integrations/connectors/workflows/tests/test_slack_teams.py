"""F121 Slack + F122 Teams: intake, status updates, attack paths."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from conftest import connector_harness, sign_payload
from fake_providers import fake_slack, fake_teams

from harness_workflow_connectors import IntakeRejected
from harness_workflow_connectors.slack import SlackNotifier
from harness_workflow_connectors.teams import TeamsNotifier


def slack_delivery(
    text: str = "deploy is broken on prod",
    event_id: str = "Ev001",
    ts: str | None = None,
    team_id: str = "T123",
) -> tuple[bytes, dict[str, str]]:
    ts = ts or str(int(time.time()))
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": event_id,
            "team_id": team_id,
            "event": {"type": "message", "text": text, "ts": "1729.001", "channel": "C1"},
        }
    ).encode()
    return body, sign_payload("slack", body, ts)


async def test_slack_message_proposes_plan(harness):
    body, headers = slack_delivery()
    res = await harness.post("slack", body, headers)
    assert res.status_code == 202, res.text
    data = res.json()
    assert data["status"] == "plan_proposed" and data["awaitingApproval"] is True
    assert data["planId"].startswith("plan-")
    (goal,) = harness.gate.goals
    assert "untrusted context, requires human plan approval" in goal
    assert "deploy is broken on prod" in goal
    assert "provider=slack" in goal


async def test_slack_duplicate_delivery_is_not_a_second_plan(harness):
    body, headers = slack_delivery(event_id="Ev-dup")
    first = await harness.post("slack", body, headers)
    second = await harness.post("slack", body, headers)
    assert first.status_code == 202 and second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert len(harness.gate.goals) == 1


async def test_slack_concurrent_duplicates_single_plan(harness):
    body, headers = slack_delivery(event_id="Ev-race")
    responses = await asyncio.gather(*[harness.post("slack", body, headers) for _ in range(5)])
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 200, 200, 200, 202]
    assert len(harness.gate.goals) == 1


async def test_slack_tampered_and_stale_signatures_rejected(harness):
    body, headers = slack_delivery()
    tampered = dict(headers)
    tampered["X-Slack-Signature"] = headers["X-Slack-Signature"][:-4] + "ffff"
    res = await harness.post("slack", body, tampered)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "SIGNATURE_INVALID"

    stale_ts = str(int(time.time()) - 3600)
    body2, headers2 = slack_delivery(event_id="Ev-stale", ts=stale_ts)
    res2 = await harness.post("slack", body2, headers2)
    assert res2.status_code == 401
    assert res2.json()["error"]["code"] == "SIGNATURE_STALE"
    assert harness.gate.goals == []


async def test_slack_unknown_tenant_refused_indistinctly(harness):
    body, headers = slack_delivery(team_id="T-EVIL")
    res = await harness.post("slack", body, headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "TENANT_UNKNOWN"
    assert harness.gate.goals == []


async def test_slack_secret_in_message_is_redacted_in_goal(harness):
    body, headers = slack_delivery(
        text="prod failing; token xoxb-1234567890-abcdefghij maybe leaked",
        event_id="Ev-secret",
    )
    res = await harness.post("slack", body, headers)
    assert res.status_code == 202
    (goal,) = harness.gate.goals
    assert "xoxb-1234567890-abcdefghij" not in goal
    assert "[REDACTED]" in goal


async def test_slack_rate_limited_per_tenant(harness):
    for i in range(3):
        body, headers = slack_delivery(event_id=f"Ev-rate-{i}")
        assert (await harness.post("slack", body, headers)).status_code == 202
    body, headers = slack_delivery(event_id="Ev-rate-4")
    res = await harness.post("slack", body, headers)
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"
    assert len(harness.gate.goals) == 3


async def test_slack_upstream_outage_then_recovery_reproposes(tmp_path):
    async with connector_harness(tmp_path) as harness:
        harness.gate.fail_next = 1
        body, headers = slack_delivery(event_id="Ev-outage")
        down = await harness.post("slack", body, headers)
        assert down.status_code == 502
        assert down.json()["error"]["code"] == "PLAN_GATE_REJECTED"

        retry = await harness.post("slack", body, headers)  # provider retry
        assert retry.status_code == 202, retry.text
        assert len(harness.gate.goals) == 1


async def test_slack_status_updates_post_to_real_fake():
    async with fake_slack() as (base, fake), httpx.AsyncClient() as http:
        notifier = SlackNotifier(base, bot_token=fake.bot_token, client=http)
        await notifier.post_status(
            channel="C1", text="plan awaiting approval", thread_ts="1729.001"
        )
        assert fake.posts == [
            {"channel": "C1", "text": "plan awaiting approval", "thread_ts": "1729.001"}
        ]
        bad = SlackNotifier(base, bot_token="wrong", client=http)
        try:
            await bad.post_status(channel="C1", text="x")
            raise AssertionError("expected rejection")
        except IntakeRejected as err:
            assert err.code == "SLACK_POST_FAILED"


def teams_delivery(
    text: str = "please triage the flaky test", msg_id: str = "m1"
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "type": "message",
            "id": msg_id,
            "text": text,
            "channelData": {"tenant": {"id": "tenant-1"}, "messageId": msg_id},
        }
    ).encode()
    return body, sign_payload("teams", body)


async def test_teams_message_proposes_plan_once(harness):
    body, headers = teams_delivery()
    res = await harness.post("teams", body, headers)
    assert res.status_code == 202, res.text
    dup = await harness.post("teams", body, headers)
    assert dup.status_code == 200
    assert len(harness.gate.goals) == 1
    assert "provider=teams" in harness.gate.goals[0]


async def test_teams_bad_bearer_rejected(harness):
    body, _ = teams_delivery()
    res = await harness.post("teams", body, {"Authorization": "Bearer nope"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "SIGNATURE_INVALID"


async def test_teams_status_card_to_real_fake():
    async with fake_teams() as (base, fake), httpx.AsyncClient() as http:
        notifier = TeamsNotifier(f"{base}/webhook", client=http)
        await notifier.post_status(text="plan plan-1 awaiting approval")
        assert fake.cards[0]["text"] == "plan plan-1 awaiting approval"
        assert fake.cards[0]["@type"] == "MessageCard"
