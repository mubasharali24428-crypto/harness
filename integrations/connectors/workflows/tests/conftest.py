"""Shared wiring: a connector service under test with real fakes behind it."""

from __future__ import annotations

import contextlib
import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from fake_providers import fake_plan_gate

import harness_workflow_connectors.alerts as alerts_mod
import harness_workflow_connectors.jira as jira_mod
import harness_workflow_connectors.linear as linear_mod
import harness_workflow_connectors.sentry as sentry_mod
import harness_workflow_connectors.slack as slack_mod
import harness_workflow_connectors.teams as teams_mod
from harness_workflow_connectors import (
    IdempotencyStore,
    IntakePipeline,
    RateLimiter,
    SignatureScheme,
    TenantConfig,
    TenantMap,
    sign,
)
from harness_workflow_connectors.plan_gate import ProposePlanGate
from harness_workflow_connectors.service import make_service_app

SECRETS = {
    "slack": "slack-secret",
    "teams": "teams-secret",
    "linear": "linear-secret",
    "jira": "jira-secret",
    "sentry": "sentry-secret",
    "alerts": "alerts-secret",
}

TENANTS = TenantMap(
    tenants={
        "slack": {"T123": TenantConfig(team_id="team-a", rate_limit_per_minute=3)},
        "teams": {"tenant-1": TenantConfig(team_id="team-a")},
        "linear": {"org-1": TenantConfig(team_id="team-a")},
        "jira": {"acme": TenantConfig(team_id="team-a")},
        "sentry": {"org-1": TenantConfig(team_id="team-a")},
        "alerts": {"1": TenantConfig(team_id="team-a", rate_limit_per_minute=3)},
    }
)

PROVIDERS = {
    "slack": (slack_mod.SLACK_SCHEME_NAME, slack_mod.SLACK_SIGNATURE, slack_mod.parse_slack_events),
    "teams": (teams_mod.TEAMS_SCHEME_NAME, teams_mod.TEAMS_SIGNATURE, teams_mod.parse_teams_message),
    "linear": (linear_mod.LINEAR_SCHEME_NAME, linear_mod.LINEAR_SIGNATURE, linear_mod.parse_linear_issue),
    "jira": (jira_mod.JIRA_SCHEME_NAME, jira_mod.JIRA_SIGNATURE, jira_mod.parse_jira_issue),
    "sentry": (sentry_mod.SENTRY_SCHEME_NAME, sentry_mod.SENTRY_SIGNATURE, sentry_mod.parse_sentry_issue),
    "alerts": (alerts_mod.ALERT_SCHEME_NAME, alerts_mod.ALERT_SIGNATURE, alerts_mod.parse_alert_payload),
}

SCHEMES = {
    name: SignatureScheme(name=sname, **scheme) for name, (sname, scheme, _) in PROVIDERS.items()
}

HEADER_NAMES = {
    "slack": ("X-Slack-Signature", "X-Slack-Request-Timestamp"),
    "teams": ("Authorization", None),
    "linear": ("Linear-Signature", None),
    "jira": ("X-Jira-Secret", None),
    "sentry": ("Sentry-Hook-Signature", None),
    "alerts": ("X-Alert-Secret", None),
}


def sign_payload(provider: str, body: bytes, ts: str = "") -> dict[str, str]:
    """Build the header dict a provider would send for ``body``."""
    scheme = SCHEMES[provider]
    value = sign(scheme, SECRETS[provider], body, ts)
    sig_header, ts_header = HEADER_NAMES[provider]
    headers = {sig_header: value}
    if ts_header:
        headers[ts_header] = ts
    return headers


class Harness:
    """A fully wired connector service + its fakes."""

    def __init__(self, client: httpx.AsyncClient, gate, idempotency: IdempotencyStore, app) -> None:
        self.client = client
        self.gate = gate
        self.idempotency = idempotency
        self.app = app

    async def post(self, provider: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
        return await self.client.post(f"/webhooks/{provider}", content=body, headers=headers)


@contextlib.asynccontextmanager
async def connector_harness(tmp_path: pathlib.Path) -> AsyncIterator[Harness]:
    async with fake_plan_gate() as (gate_url, gate):
        plan_gate = ProposePlanGate(gate_url, bearer_token=gate.bearer_required)
        idempotency = IdempotencyStore(str(tmp_path / "deliveries.sqlite"))
        limiter = RateLimiter()
        pipelines = {
            name: IntakePipeline(
                provider=name,
                scheme=SignatureScheme(name=sname, **scheme),
                secret=SECRETS[name],
                tenants=TENANTS,
                idempotency=idempotency,
                limiter=limiter,
                plan_gate=plan_gate,
                parse=parse,
            )
            for name, (sname, scheme, parse) in PROVIDERS.items()
        }
        app = make_service_app(pipelines=pipelines)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://connector.test") as c:
            yield Harness(client=c, gate=gate, idempotency=idempotency, app=app)
        await plan_gate.aclose()


@pytest.fixture
async def harness(tmp_path) -> AsyncIterator[Harness]:
    async with connector_harness(tmp_path) as h:
        yield h
