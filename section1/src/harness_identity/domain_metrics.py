"""Event-delivery counter (Prometheus text format) for the identity/bus process.

    harness_event_delivery_total{outcome}   delivered | duplicate | dropped | replayed

Same contract and shape as section4's ``harness_orchestrator.domain_metrics`` (the
sections may not import each other; the names are fixed by the alert rules in
deploy/observability/alerts.yml). Closed label set, known series rendered at 0.
``render()`` must be appended to the identity service's ``/metrics`` response by the
deploy collector (docs/audit-and-events.md).

* delivered - a live event frame sent to a subscriber
* replayed  - an event frame sent as reconnect/catch-up history
* duplicate - a delivery suppressed because that subscriber already has it
* dropped   - a live delivery lost to a slow subscriber (its socket is closed to resync)
"""

from __future__ import annotations

import threading

NAME = "harness_event_delivery_total"
OUTCOMES = ("delivered", "duplicate", "dropped", "replayed")

_lock = threading.Lock()
_counts: dict[str, int] = {}


def inc(outcome: str, amount: int = 1) -> None:
    if outcome not in OUTCOMES or amount <= 0:
        return
    with _lock:
        _counts[outcome] = _counts.get(outcome, 0) + amount


def value(outcome: str) -> int:
    with _lock:
        return _counts.get(outcome, 0)


def reset() -> None:
    with _lock:
        _counts.clear()


def render() -> str:
    with _lock:
        snap = dict(_counts)
    lines = [
        f"# HELP {NAME} Collaboration event delivery outcomes.",
        f"# TYPE {NAME} counter",
    ]
    lines += [f'{NAME}{{outcome="{o}"}} {snap.get(o, 0)}' for o in OUTCOMES]
    return "\n".join(lines) + "\n"
