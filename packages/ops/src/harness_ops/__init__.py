"""harness-ops — operational acceptance for the harness.

F118 evidence exports, F119 actionable alerts, F120 usage APIs/webhooks,
F128 CI provider adapters, F129 chat approve/reject/inspect, F130 scheduled
maintenance. Everything is built on the frozen event vocabulary plus the
S1–S4 surfaces; heavy lifting (signing, idempotency, plan gate) is reused from
``harness-workflow-connectors``.

Hard invariants encoded here:
  * ops can PROPOSE plans (scheduled/CI work) and — only via chat, only with a
    signed unexpired binding, only for a human approver — APPROVE a specific
    plan. Nothing in ops can start or execute work.
  * alert and webhook deliveries are deduped; retries are bounded.
"""

from .alerts import AlertDraft, AlertEngine, AlertThresholds, evaluate_alerts
from .ci import SUPPORTED_CI_PROVIDERS, CIFailure, CIIntake, build_ci_adapters
from .evidence import EvidenceError, build_evidence, verify_evidence_zip
from .schedule import (
    ScheduleError,
    due_occurrences,
    next_occurrence,
    run_due_schedules,
)
from .usage import (
    DeliveryResult,
    UsageWebhookError,
    deliver_event,
    delivery_signature,
    register_webhook,
    usage_snapshot,
    validate_webhook_url,
    verify_delivery_signature,
)

__all__ = [
    "AlertDraft",
    "AlertEngine",
    "AlertThresholds",
    "SUPPORTED_CI_PROVIDERS",
    "CIFailure",
    "CIIntake",
    "DeliveryResult",
    "EvidenceError",
    "ScheduleError",
    "UsageWebhookError",
    "build_ci_adapters",
    "build_evidence",
    "deliver_event",
    "delivery_signature",
    "due_occurrences",
    "evaluate_alerts",
    "next_occurrence",
    "register_webhook",
    "run_due_schedules",
    "usage_snapshot",
    "validate_webhook_url",
    "verify_delivery_signature",
    "verify_evidence_zip",
]
