"""Real-time delivery metrics (Prometheus text format) for the identity/bus process (part 46.010).

Same style as ``domain_metrics`` (hand-rendered exposition, one lock, closed label sets, every
known series rendered at 0 from process start, values in memory). ``render()`` must be appended
to the identity ``/metrics`` response next to ``domain_metrics.render`` (docs/realtime.md,
section "Metrics").

    harness_realtime_connections_total{event}   connect | resume | refused
    harness_realtime_close_total{reason}        client | slow_consumer | rate_limited |
                                                access_revoked | server_error
    harness_realtime_recovered_total{kind}      resumed_events | truncated_replay | poll_recovered
    harness_event_delivery_lag_seconds          histogram: commit time of a live event to the
                                                moment it was written to the socket
    harness_event_replay_age_seconds            histogram: commit time of a replayed event to the
                                                moment it was written on a catch-up send

* ``resume``          a socket opened with ``afterSeq > 0`` (a reconnect, or a page reload)
* ``refused``         admission refused after authentication (connection cap)
* ``resumed_events``  frames replayed to a resuming socket: events the client would have missed
* ``truncated_replay``  a resume whose gap exceeded the replay cap (history must be paged over HTTP)
* ``poll_recovered``  frames delivered by the poll tick with no wake-up (a lost NOTIFY, or SQLite,
                      which has no listener at all)

The two histograms measure the two halves of the freshness objective
(docs/runbooks/slo-and-capacity.md): "delivered **or replayable** within 5 seconds".

* ``harness_event_delivery_lag_seconds`` is the SLO signal. A committed event is
  durable (hence replayable) at commit, so live delivery is the only variable part
  of that objective. A bounded histogram is what makes "99% within 5 s" computable.
* ``harness_event_replay_age_seconds`` is diagnostic, **not** the SLO: it reports how
  far behind a reconnecting client was. A client that was closed for ten minutes
  legitimately replays ten-minute-old events, and that must not be read as a
  freshness violation.

Both lags are wall-clock on the identity host minus the event's ``ts`` written by the
orchestrator (inside the publishing transaction, immediately before commit), so they
include clock skew between the two hosts. "Written" means ``websocket.send_json``
completed without the slow-consumer timeout — the server handed the frame to the
ASGI transport. Clients are read-only by contract and send no application-level ack,
so that is the far edge this process can observe.
"""

from __future__ import annotations

import threading

COUNTERS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "harness_realtime_connections_total": (
        "Collaboration socket admissions.",
        "event",
        ("connect", "resume", "refused"),
    ),
    "harness_realtime_close_total": (
        "Collaboration and project socket closes by cause.",
        "reason",
        ("client", "slow_consumer", "rate_limited", "access_revoked", "server_error"),
    ),
    "harness_realtime_recovered_total": (
        "Events or gaps recovered by replay or by the poll fallback.",
        "kind",
        ("resumed_events", "truncated_replay", "poll_recovered"),
    ),
}
LAG_NAME = "harness_event_delivery_lag_seconds"
LAG_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
REPLAY_AGE_NAME = "harness_event_replay_age_seconds"
# Replay is catch-up history: seconds to minutes is normal, an hour is a client
# that was closed a long time. The 5.0 bound matches the freshness objective's
# window so a reviewer can see the same boundary on both histograms.
REPLAY_AGE_BUCKETS = (1.0, 5.0, 15.0, 60.0, 300.0, 900.0, 3600.0)

_lock = threading.Lock()
_counts: dict[tuple[str, str], int] = {}
_replay_buckets = [0] * len(REPLAY_AGE_BUCKETS)  # per-bucket (non-cumulative) counts
_replay_count = 0
_replay_sum = 0.0
_lag_buckets = [0] * len(LAG_BUCKETS)  # per-bucket (non-cumulative) counts
_lag_count = 0
_lag_sum = 0.0


def inc(name: str, label_value: str, amount: int = 1) -> None:
    """Increment ``name{label=label_value}``; unknown names or values are ignored (closed sets)."""
    spec = COUNTERS.get(name)
    if spec is None or label_value not in spec[2] or amount <= 0:
        return
    with _lock:
        _counts[(name, label_value)] = _counts.get((name, label_value), 0) + amount


def observe_lag(seconds: float) -> None:
    global _lag_count, _lag_sum
    seconds = max(0.0, float(seconds))
    with _lock:
        _lag_count += 1
        _lag_sum += seconds
        for i, bound in enumerate(LAG_BUCKETS):
            if seconds <= bound:
                _lag_buckets[i] += 1
                break


def observe_replay_age(seconds: float) -> None:
    """Record one replayed frame's age (diagnostic; never the freshness SLO)."""
    global _replay_count, _replay_sum
    seconds = max(0.0, float(seconds))
    with _lock:
        _replay_count += 1
        _replay_sum += seconds
        for i, bound in enumerate(REPLAY_AGE_BUCKETS):
            if seconds <= bound:
                _replay_buckets[i] += 1
                break


def value(name: str, label_value: str) -> int:
    with _lock:
        return _counts.get((name, label_value), 0)


def lag_count() -> int:
    with _lock:
        return _lag_count


def replay_age_count() -> int:
    with _lock:
        return _replay_count


def reset() -> None:
    global _lag_count, _lag_sum, _replay_count, _replay_sum
    with _lock:
        _counts.clear()
        _lag_buckets[:] = [0] * len(LAG_BUCKETS)
        _lag_count = 0
        _lag_sum = 0.0
        _replay_buckets[:] = [0] * len(REPLAY_AGE_BUCKETS)
        _replay_count = 0
        _replay_sum = 0.0


def render() -> str:
    with _lock:
        snap = dict(_counts)
        buckets = list(_lag_buckets)
        count, total = _lag_count, _lag_sum
        replay_buckets = list(_replay_buckets)
        replay_count, replay_total = _replay_count, _replay_sum
    lines: list[str] = []
    for name, (help_text, label, values) in COUNTERS.items():
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
        lines += [f'{name}{{{label}="{v}"}} {snap.get((name, v), 0)}' for v in values]
    lines += [
        f"# HELP {LAG_NAME} Seconds from an event's commit to its write on a live socket.",
        f"# TYPE {LAG_NAME} histogram",
    ]
    running = 0
    for bound, n in zip(LAG_BUCKETS, buckets, strict=True):
        running += n
        lines.append(f'{LAG_NAME}_bucket{{le="{bound}"}} {running}')
    lines += [
        f'{LAG_NAME}_bucket{{le="+Inf"}} {count}',
        f"{LAG_NAME}_sum {total}",
        f"{LAG_NAME}_count {count}",
        f"# HELP {REPLAY_AGE_NAME} Seconds from an event's commit to its write on a catch-up "
        "(replay) send; diagnostic, not the freshness SLO.",
        f"# TYPE {REPLAY_AGE_NAME} histogram",
    ]
    running = 0
    for bound, n in zip(REPLAY_AGE_BUCKETS, replay_buckets, strict=True):
        running += n
        lines.append(f'{REPLAY_AGE_NAME}_bucket{{le="{bound}"}} {running}')
    lines += [
        f'{REPLAY_AGE_NAME}_bucket{{le="+Inf"}} {replay_count}',
        f"{REPLAY_AGE_NAME}_sum {replay_total}",
        f"{REPLAY_AGE_NAME}_count {replay_count}",
    ]
    return "\n".join(lines) + "\n"
