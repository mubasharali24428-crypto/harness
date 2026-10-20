"""F129 acceptance: approval bindings are signed, expiring, action-bound and
single-use; approve hits the real baseline route; reject is recorded honestly
(no fabricated orchestrator route); roles gate every action."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from harness_workflow_connectors.core import IntakeRejected
from harness_workflow_connectors.plan_gate import PlanGateError, ProposePlanGate
from support import fake_orchestrator, slack_action_body

from harness_ops.chat import (
    ACTION_IDS,
    ActionTokenError,
    ApprovePlanGate,
    SlackActionHandler,
    build_approval_message,
    sign_action_token,
    verify_action_token,
)
from harness_ops.store import OpsStore

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
SLACK_SIGNING = "slack-signing-secret"
ACTION_SECRET = "action-token-secret"


def test_tokens_roundtrip_and_expire():
    token = sign_action_token(ACTION_SECRET, plan_id="plan-1", run_id="run-1",
                              action="approve", now=NOW)
    binding = verify_action_token(ACTION_SECRET, token, now=NOW + timedelta(minutes=5))
    assert binding["planId"] == "plan-1"
    assert binding["action"] == "approve"
    with pytest.raises(ActionTokenError) as exc:
        verify_action_token(ACTION_SECRET, token, now=NOW + timedelta(hours=1))
    assert exc.value.code == "TOKEN_EXPIRED" and exc.value.status_code == 409


def test_tampered_tokens_rejected():
    token = sign_action_token(ACTION_SECRET, plan_id="plan-1", run_id="run-1",
                              action="approve", now=NOW)
    body, _, mac = token.partition(".")
    flipped = "A" if mac[0] != "A" else "B"
    with pytest.raises(ActionTokenError) as exc:
        verify_action_token(ACTION_SECRET, f"{body}.{flipped}{mac[1:]}", now=NOW)
    assert exc.value.code == "TOKEN_BAD_SIGNATURE"
    with pytest.raises(ActionTokenError) as exc:
        verify_action_token(ACTION_SECRET, "garbage", now=NOW)
    assert exc.value.code == "TOKEN_MALFORMED"
    with pytest.raises(ValueError):
        sign_action_token(ACTION_SECRET, plan_id="p", run_id="r", action="execute",
                          now=NOW)


def test_message_carries_three_signed_buttons():
    message = build_approval_message(
        plan_id="plan-1", run_id="run-1", goal="Fix login", secret=ACTION_SECRET,
        now=NOW, requestor="alice",
    )
    actions = message["blocks"][-1]["elements"]
    assert [a["action_id"] for a in actions] == [
        "harness_approve", "harness_reject", "harness_inspect",
    ]
    assert message["blocks"][-1]["block_id"] == "harness_plan_plan-1"
    for action in actions:
        binding = verify_action_token(ACTION_SECRET, action["value"], now=NOW)
        assert binding["planId"] == "plan-1"
    assert ACTION_IDS["harness_approve"] == "approve"


@pytest.fixture()
async def wired(tmp_path):
    async with fake_orchestrator() as (base, state):
        store = OpsStore(str(tmp_path / "ops.sqlite"))
        gate = ApprovePlanGate(base, bearer_token="approve-token")
        handler = SlackActionHandler(
            signing_secret=SLACK_SIGNING, action_secret=ACTION_SECRET,
            store=store, approve_gate=gate,
        )
        yield handler, store, state
        await gate.aclose()


def approve_token(plan="plan-1", run="run-1", ttl=900, action="approve"):
    return sign_action_token(ACTION_SECRET, plan_id=plan, run_id=run, action=action,
                             now=NOW, ttl_s=ttl)


async def test_approve_calls_real_route_once_and_replays_are_duplicates(wired):
    handler, store, state = wired
    raw, headers = slack_action_body(
        signing_secret=SLACK_SIGNING, action_id="harness_approve",
        token=approve_token(), ts=str(int(NOW.timestamp())),
    )
    result = await handler.handle(raw, headers, actor="alice",
                                  actor_roles=["approver"], now=NOW)
    assert result["duplicate"] is False
    assert result["outcome"] == {"decision": "approved", "planId": "plan-1",
                                 "runId": "run-1"}
    assert state["approve_calls"] == ["plan-1"]

    replay = await handler.handle(raw, headers, actor="alice",
                                  actor_roles=["approver"], now=NOW)
    assert replay["duplicate"] is True
    assert replay["outcome"]["runId"] == "run-1"
    assert state["approve_calls"] == ["plan-1"]


async def test_reject_is_recorded_and_never_calls_orchestrator(wired):
    handler, store, state = wired
    raw, headers = slack_action_body(
        signing_secret=SLACK_SIGNING, action_id="harness_reject",
        token=approve_token(plan="plan-9", action="reject"),
        ts=str(int(NOW.timestamp())),
    )
    result = await handler.handle(raw, headers, actor="alice",
                                  actor_roles=["approver"], now=NOW)
    outcome = result["outcome"]
    assert outcome["decision"] == "rejected"
    assert outcome["recorded"] is True
    assert "no orchestrator reject route verified" in outcome["note"]
    assert state["approve_calls"] == []


async def test_inspect_allowed_for_engineers(wired):
    handler, _, _ = wired
    raw, headers = slack_action_body(
        signing_secret=SLACK_SIGNING, action_id="harness_inspect",
        token=approve_token(action="inspect"), ts=str(int(NOW.timestamp())),
    )
    result = await handler.handle(raw, headers, actor="dee",
                                  actor_roles=["engineer"], now=NOW)
    assert result["outcome"]["decision"] == "inspected"


async def test_action_binding_stops_token_transplant(wired):
    handler, _, state = wired
    reject_token = approve_token(action="reject")
    raw, headers = slack_action_body(
        signing_secret=SLACK_SIGNING, action_id="harness_approve",
        token=reject_token, ts=str(int(NOW.timestamp())),
    )
    with pytest.raises(ActionTokenError) as exc:
        await handler.handle(raw, headers, actor="alice",
                             actor_roles=["approver"], now=NOW)
    assert exc.value.code == "ACTION_MISMATCH"
    assert state["approve_calls"] == []


async def test_role_gate_blocks_viewer(wired):
    handler, _, state = wired
    raw, headers = slack_action_body(
        signing_secret=SLACK_SIGNING, action_id="harness_approve",
        token=approve_token(), ts=str(int(NOW.timestamp())),
    )
    with pytest.raises(ActionTokenError) as exc:
        await handler.handle(raw, headers, actor="vic", actor_roles=["viewer"],
                             now=NOW)
    assert exc.value.code == "ROLE_FORBIDDEN"
    assert state["approve_calls"] == []


async def test_expired_binding_rejected_at_handler(wired):
    handler, _, state = wired
    raw, headers = slack_action_body(
        signing_secret=SLACK_SIGNING, action_id="harness_approve",
        token=approve_token(ttl=-60), ts=str(int(NOW.timestamp())),
    )
    with pytest.raises(ActionTokenError) as exc:
        await handler.handle(raw, headers, actor="alice",
                             actor_roles=["approver"], now=NOW)
    assert exc.value.code == "TOKEN_EXPIRED"
    assert state["approve_calls"] == []


async def test_slack_request_signature_required(wired):
    handler, _, _ = wired
    raw, headers = slack_action_body(
        signing_secret="some-other-secret", action_id="harness_approve",
        token=approve_token(), ts=str(int(NOW.timestamp())),
    )
    with pytest.raises(IntakeRejected) as exc:
        await handler.handle(raw, headers, actor="alice",
                             actor_roles=["approver"], now=NOW)
    assert exc.value.code == "SIGNATURE_INVALID"


async def test_missing_gate_fails_closed(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    handler = SlackActionHandler(signing_secret=SLACK_SIGNING,
                                 action_secret=ACTION_SECRET, store=store,
                                 approve_gate=None)
    raw, headers = slack_action_body(
        signing_secret=SLACK_SIGNING, action_id="harness_approve",
        token=approve_token(), ts=str(int(NOW.timestamp())),
    )
    with pytest.raises(PlanGateError) as exc:
        await handler.handle(raw, headers, actor="alice",
                             actor_roles=["approver"], now=NOW)
    assert exc.value.code == "PLAN_GATE_UNAVAILABLE"


def test_approve_gate_has_no_execute_capability():
    for name in ("start", "execute", "run", "approve_and_start"):
        assert not hasattr(ProposePlanGate, name)
        assert not hasattr(ApprovePlanGate, name)
