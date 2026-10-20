"""Team-channel and incident-to-agent connectors (A5-S3).

Six provider integrations over one evidence-first core:

* slack  — Slack task intake + status updates (F121)
* teams  — Microsoft Teams task intake + status updates (F122)
* linear — Linear issue integration (F123)
* jira   — Jira issue integration (F124)
* sentry — Sentry error-to-agent workflow (F126)
* alerts — Datadog/Grafana incident-to-agent workflow (F127)

Core invariants (see ``core.py``): signed webhooks verified before parsing,
tenant mapping fail-closed, idempotent deliveries that survive outages,
per-tenant rate limits, secret redaction, and intake that can ONLY propose
plans — a human approves before anything executes.
"""

from .core import (
    IdempotencyStore,
    IntakeEvent,
    IntakeOutcome,
    IntakePipeline,
    IntakeRejected,
    RateLimiter,
    SignatureScheme,
    TenantConfig,
    TenantMap,
    build_goal,
    delivery_id_from,
    redact_secrets,
    sign,
    verify_signature,
)
from .plan_gate import PlanGateError, ProposePlanGate

__all__ = [
    "IdempotencyStore",
    "IntakeEvent",
    "IntakeOutcome",
    "IntakePipeline",
    "IntakeRejected",
    "PlanGateError",
    "ProposePlanGate",
    "RateLimiter",
    "SignatureScheme",
    "TenantConfig",
    "TenantMap",
    "build_goal",
    "delivery_id_from",
    "redact_secrets",
    "sign",
    "verify_signature",
]
