"""M4: ``model.invoke`` through the relay — and the gate is a secret that never lands.

Four surfaces are asserted, because "no secret in the prompt" is only half of
the claim: the prompt, the log, the events, and the evidence bundle a reviewer
keeps. A token is seeded in repository content, which is where a real one comes
from — under a key called ``input``, so key-name redaction would not have seen it.

The relay is given a router shaped like Section 3's ``ModelRouter``. Nothing here
imports a section: the contract's Protocol is the only shared vocabulary, and the
fake satisfies it by structure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import pytest
from harness_contracts.signatures import ModelResult, ModelRole, ModelRouter

from harness_hx_host import CheckRefused, FakeHostBridge, ModelRelay, run_source

SECRET = "sk-live-abcdef1234567890"
README = "# demo\n\napi_key = " + SECRET + "\n"

RELAY = """\
module relay
schema RepoFile { path: String  text: String }
schema Summary { text: String }
workflow relay() -> Summary {
  requires repo.read, model.invoke
  budget { steps: 20; tool_calls: 4; duration: 1m }
  agent summarizer: Agent<Summary> { role: "developer" model: "frontier-1" }
  let readme = await repo.read("README.md")
  let summary = await summarizer.plan(readme.text)
  return summary
}
"""


@dataclass
class FakeRouter:
    """Section 3's shape, minimal: route, cost, catalogue."""

    replies: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)
    models: list[str] = field(default_factory=lambda: ["frontier-1", "cheap-1"])

    def call_model(self, agent_id, role, messages, opts=None):
        self.calls.append(
            {"agentId": agent_id, "role": role, "messages": messages, "opts": opts}
        )
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def estimate_cost(self, role, input_tokens, output_tokens):
        return 0.0

    def list_available_models(self):
        return list(self.models)


def reply(text: str, model: str = "frontier-1") -> ModelResult:
    return ModelResult(text=text, costUsd=0.01, model=model, latencyMs=12)


def host(relay: ModelRelay, files: dict[str, str] | None = None) -> FakeHostBridge:
    return FakeHostBridge(
        files=files or {"README.md": README},
        agents={"summarizer": relay},
    )


def relay(router: FakeRouter | None = None, **kwargs) -> ModelRelay:
    return ModelRelay.from_source(
        RELAY, router if router is not None else FakeRouter([reply('{"text": "ok"}')]), **kwargs
    )


def run(bridge: FakeHostBridge, **kwargs):
    return run_source(
        RELAY,
        bridge=bridge,
        granted=["repo.read", "model.invoke"],
        path="relay.hx",
        **kwargs,
    )


def test_the_relay_asks_a_router_shaped_like_the_contracts():
    router = FakeRouter([reply('{"text": "ok"}')])
    outcome = run(host(relay(router)))

    assert outcome.status == "ok", outcome.evidence()
    assert outcome.output.to_json() == {"text": "ok"}
    assert isinstance(router, ModelRouter)
    call = router.calls[0]
    assert call["agentId"] == "summarizer.plan"
    assert call["role"] is ModelRole.PRIMARY
    assert call["opts"].maxTokens == 4096
    assert [message["role"] for message in call["messages"]] == ["system", "user"]
    assert "developer" in call["messages"][0]["content"]


def test_a_secret_in_the_payload_never_reaches_the_router():
    router = FakeRouter([reply('{"text": "ok"}')])
    relayed = relay(router)

    assert run(host(relayed)).status == "ok"

    sent = json.dumps(router.calls, default=str)
    assert SECRET not in sent
    assert "[redacted]" in router.calls[0]["messages"][1]["content"]
    assert relayed.report()["redactions"]["kinds"] == {"provider-key": 1}
    assert relayed.calls == 1


def test_the_secret_reaches_no_log_no_event_and_no_evidence(caplog):
    caplog.set_level(logging.DEBUG)
    relayed = relay(FakeRouter([reply('{"text": "ok"}')]))
    outcome = run(host(relayed))

    assert SECRET not in caplog.text
    assert SECRET not in json.dumps(outcome.events)
    assert SECRET not in json.dumps(outcome.evidence())
    # The bundle's own tally is empty *because* the relay removed the token before
    # it became a value; the relay's report is where that redaction is recorded.
    assert outcome.evidence()["redactions"]["total"] == 0
    assert relayed.report()["redactions"]["kinds"] == {"provider-key": 1}


def test_a_reply_that_echoes_a_token_is_scrubbed_before_it_becomes_a_value():
    relayed = relay(FakeRouter([reply('{"text": "use ' + SECRET + '"}')]))
    outcome = run(host(relayed))

    assert outcome.status == "ok", outcome.error
    assert outcome.output.to_json() == {"text": "use [redacted]"}
    assert outcome.evidence()["outcome"]["output"]["text"] == "use [redacted]"


def test_a_host_without_a_router_refuses_rather_than_invents_an_answer():
    relayed = ModelRelay(router=None)
    outcome = run(
        FakeHostBridge(files={"README.md": README}, agents={"summarizer": relayed})
    )

    assert outcome.status == "error"
    assert outcome.error.code == "HXH403"
    assert "needs Section 3's ModelRouter" in outcome.error.message


def test_a_model_substitution_is_recorded_and_not_retried():
    router = FakeRouter([reply('{"text": "ok"}', model="cheap-1")])
    relayed = relay(router)
    outcome = run(host(relayed))

    assert outcome.status == "ok"
    assert len(router.calls) == 1, "a substitution must never cause a second attempt"
    assert relayed.downgrades == [
        {
            "agent": "summarizer",
            "operation": "plan",
            "requested": "frontier-1",
            "answered": "cheap-1",
            "performed": False,
        }
    ]


def test_a_provider_error_carrying_a_key_is_scrubbed_everywhere(caplog):
    caplog.set_level(logging.DEBUG)
    relayed = relay(FakeRouter([RuntimeError(f"401 unauthorized for {SECRET}")]))
    outcome = run(host(relayed))

    assert outcome.status == "error"
    assert outcome.error.code == "HXH403"
    assert SECRET not in outcome.error.message
    assert "[redacted]" in outcome.error.message
    assert SECRET not in caplog.text
    assert SECRET not in json.dumps(outcome.evidence())


def test_the_report_keeps_facts_and_no_text():
    relayed = relay(FakeRouter([reply('{"text": "ok"}')]))
    assert run(host(relayed)).status == "ok"

    report = relayed.report()
    assert SECRET not in json.dumps(report)
    assert report["exchanges"][0]["promptSha256"] != ""
    assert report["exchanges"][0]["promptBytes"] > 0
    assert "messages" not in report["exchanges"][0]


def test_roles_come_from_the_source_and_the_host_decides_the_routing_role():
    router = FakeRouter([reply('{"text": "ok"}')])
    relayed = ModelRelay.from_source(RELAY, router, roles={"summarizer": "reviewer"})
    run(host(relayed))

    policy = relayed.agents["summarizer"]
    assert policy.label == "developer"
    assert policy.model == "frontier-1"
    assert policy.role is ModelRole.REVIEWER
    assert router.calls[0]["role"] is ModelRole.REVIEWER


def test_a_program_that_does_not_check_cannot_have_its_agents_routed():
    with pytest.raises(CheckRefused, match="does not check"):
        ModelRelay.from_source("module broken\nworkflow broken() -> Nope { return 1 }\n", None)
