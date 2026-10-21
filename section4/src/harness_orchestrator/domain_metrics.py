"""Domain counters for the collaboration/lifecycle layer (Prometheus text format).

Dependency-free like the rest of the repo's metrics (api.MetricsCollector, deploy
observability.py): hand-rendered exposition format, one lock. The names are the
contract with the alert rules in deploy/observability/alerts.yml:

    harness_approval_denied_total{reason}     high-impact approval/decision refused
    harness_run_cancel_total{outcome}         stop/pause cancellation lifecycle
    harness_isolation_violation_total{kind}   workspace/path isolation refusals
    harness_event_delivery_total{outcome}     collaboration event delivery (duplicate
                                              suppression here; push/replay in section1)
    harness_notifications_total{status}       notify.emit results
    harness_notification_purged_total{table}  rows deleted by the notification purge

Label values come from the closed sets below (unknown values collapse to
``other``), so cardinality is bounded by construction. Known series are rendered
at 0 from process start so ``increase()`` and absence alerts behave.

``render()`` is appended to ``GET /api/v1/orchestrator/metrics`` (api.py). The
deploy root ``/metrics`` collector must also append ``render()`` to scrape these
(see docs/audit-and-events.md).
"""

from __future__ import annotations

import threading

# metric -> (help text, label name, closed set of values)
METRICS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "harness_approval_denied_total": (
        "High-impact approval requests, decisions or executions refused.",
        "reason",
        (
            "not_leader", "self_approval", "digest_mismatch", "expired", "not_pending",
            "not_approved", "not_executing", "not_requester", "not_amendable",
            "staging_mismatch", "other",
        ),
    ),
    "harness_run_cancel_total": (
        "Run cancellation (stop/pause) outcomes.",
        "outcome",
        ("requested", "confirmed", "unconfirmed", "noop", "refused"),
    ),
    "harness_isolation_violation_total": (
        "Refused attempts to leave a run's isolation boundary.",
        "kind",
        ("bad_session_id", "workspace_escape", "path_traversal", "symlink_escape", "other"),
    ),
    "harness_event_delivery_total": (
        "Collaboration event delivery outcomes.",
        "outcome",
        ("delivered", "duplicate", "dropped", "replayed"),
    ),
    "harness_notifications_total": (
        "Notification emit results.",
        "status",
        ("created", "grouped", "duplicate", "suppressed", "rejected", "error"),
    ),
    "harness_notification_purged_total": (
        "Notification rows deleted by the retention purge.",
        "table",
        ("inbox", "dedupe", "unsub_tokens"),
    ),
}

_lock = threading.Lock()
_counts: dict[tuple[str, str], int] = {}


def inc(name: str, value: str, amount: int = 1) -> None:
    """Increment ``name{label=value}``; never raises (telemetry is best-effort)."""
    spec = METRICS.get(name)
    if spec is None:
        return
    if value not in spec[2]:
        value = "other" if "other" in spec[2] else spec[2][-1]
    with _lock:
        _counts[(name, value)] = _counts.get((name, value), 0) + amount


def value(name: str, label_value: str) -> int:
    with _lock:
        return _counts.get((name, label_value), 0)


def reset() -> None:
    """Test seam: zero every series."""
    with _lock:
        _counts.clear()


def render() -> str:
    with _lock:
        snap = dict(_counts)
    lines: list[str] = []
    for name, (help_text, label, values) in METRICS.items():
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        for v in values:
            lines.append(f'{name}{{{label}="{v}"}} {snap.get((name, v), 0)}')
    return "\n".join(lines) + "\n"
