"""Self-contained connector service: one ASGI app exposing signed webhook
endpoints per provider. Runnable standalone:

    uvicorn harness_workflow_connectors.service:app_factory --factory
"""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .core import IntakePipeline, IntakeRejected
from .plan_gate import PlanGateError


def make_service_app(*, pipelines: dict[str, IntakePipeline]) -> Starlette:
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "providers": sorted(pipelines)})

    async def webhook(request: Request) -> JSONResponse:
        provider = request.path_params["provider"]
        pipeline = pipelines.get(provider)
        if pipeline is None:
            return _error(404, "PROVIDER_UNKNOWN", f"no intake for {provider!r}")
        raw = await request.body()
        try:
            outcome = await pipeline.handle(raw, dict(request.headers))
        except IntakeRejected as exc:
            return _error(exc.status_code, exc.code, exc.message)
        except PlanGateError as exc:
            return _error(502, exc.code, exc.message)
        if outcome.status == "duplicate":
            return JSONResponse({"status": "duplicate"}, status_code=200)
        return JSONResponse(
            {
                "status": "plan_proposed",
                "planId": outcome.planId,
                "teamId": outcome.teamId,
                "awaitingApproval": True,
            },
            status_code=202,
        )

    def _error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    app = Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/webhooks/{provider}", webhook, methods=["POST"]),
        ]
    )
    app.state.pipelines = pipelines
    return app


def app_factory() -> Starlette:  # pragma: no cover - deployment entrypoint
    """Build from environment: CONNECTOR_TENANTS (tenants json path) plus one
    secret per provider and PLAN_GATE_URL/PLAN_GATE_TOKEN."""
    import os
    import tempfile

    from . import alerts, jira, linear, sentry, slack, teams
    from .core import IdempotencyStore, RateLimiter, SignatureScheme, TenantMap
    from .plan_gate import ProposePlanGate

    tenants = TenantMap.from_json(os.environ["CONNECTOR_TENANTS"])
    plan_gate = ProposePlanGate(
        os.environ["PLAN_GATE_URL"], bearer_token=os.environ["PLAN_GATE_TOKEN"]
    )
    idempotency = IdempotencyStore(
        os.environ.get("CONNECTOR_DB", f"{tempfile.gettempdir()}/connector-deliveries.sqlite")
    )
    limiter = RateLimiter()

    providers = {
        "slack": (slack.SLACK_SCHEME_NAME, slack.SLACK_SIGNATURE, "SLACK_SIGNING_SECRET",
                  slack.parse_slack_events),
        "teams": (teams.TEAMS_SCHEME_NAME, teams.TEAMS_SIGNATURE, "TEAMS_WEBHOOK_SECRET",
                  teams.parse_teams_message),
        "linear": (linear.LINEAR_SCHEME_NAME, linear.LINEAR_SIGNATURE, "LINEAR_WEBHOOK_SECRET",
                   linear.parse_linear_issue),
        "jira": (jira.JIRA_SCHEME_NAME, jira.JIRA_SIGNATURE, "JIRA_WEBHOOK_SECRET",
                 jira.parse_jira_issue),
        "sentry": (sentry.SENTRY_SCHEME_NAME, sentry.SENTRY_SIGNATURE, "SENTRY_CLIENT_SECRET",
                   sentry.parse_sentry_issue),
        "alerts": (alerts.ALERT_SCHEME_NAME, alerts.ALERT_SIGNATURE, "ALERT_WEBHOOK_SECRET",
                   alerts.parse_alert_payload),
    }
    pipelines = {
        name: IntakePipeline(
            provider=name,
            scheme=SignatureScheme(name=sname, **sscheme),
            secret=os.environ[env],
            tenants=tenants,
            idempotency=idempotency,
            limiter=limiter,
            plan_gate=plan_gate,
            parse=parse,
        )
        for name, (sname, sscheme, env, parse) in providers.items()
    }
    return make_service_app(pipelines=pipelines)


def tenants_json_schema_example() -> str:  # pragma: no cover - doc helper
    return json.dumps(
        {
            "slack": {"T0123ABCD": {"teamId": "team-a", "rateLimitPerMinute": 30}},
            "sentry": {"org-uuid": {"teamId": "team-a"}},
            "alerts": {"1": {"teamId": "team-a"}},
        },
        indent=2,
    )
