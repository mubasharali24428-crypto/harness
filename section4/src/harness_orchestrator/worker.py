"""Single in-process worker loop that drains the durable run queue.

M20-T1 — claim batching (the first 10x lever). BASELINE.md measured the wall:
this loop admitted at most ONE run per ``poll_interval`` (0.5 s shipped), so
platform throughput was hard-bounded near 1/poll_interval ≈ 2 journeys/s no
matter how wide the engine executor was. Two changes move the ceiling:

1. CLAIM BATCHING: each cycle asks ``service.poll_once(max_claims=…)`` for up
   to ``WORKER_CLAIM_BATCH`` runs (default = ``ENGINE_MAX_CONCURRENCY``, the
   dedicated executor's width — claiming wider than the executor cannot add
   throughput). Every claimed run still dispatches individually through the
   service's unchanged gates (N1 durable/CAS reads, Wave 9 B budget pre-spawn
   gate, live-task fence, B5 executor + deadline + breaker).

2. ADAPTIVE CADENCE: the inter-cycle sleep adapts to demand around the
   configured idle cadence (``poll_interval``, ``HARNESS_WORKER_POLL_SECONDS``):

   - previous cycle claimed a FULL batch  → floor (``WORKER_POLL_MIN_SECONDS``):
     work kept arriving faster than one batch per cycle, so poll at maximum
     urgency;
   - previous cycle claimed SOME runs (partial batch or single) → hold the
     current interval (productive cadence, neither accelerating nor backing
     off);
   - previous cycle claimed NOTHING (drained) → multiply the current interval
     by ``WORKER_POLL_BACKOFF_FACTOR`` toward the configured idle cadence
     (``poll_interval`` = ``HARNESS_WORKER_POLL_SECONDS``, the ceiling), so an
     idle platform stops burning claim queries while still sweeping
     zombies/reapers every cycle.

   Env knobs (all optional, clamped, non-numeric values ignored with a
   warning — same law as every other knob here): ``WORKER_CLAIM_BATCH``
   (≥1), ``WORKER_POLL_MIN_SECONDS`` (>0), ``WORKER_POLL_BACKOFF_FACTOR``
   (>1). The backoff CEILING is deliberately NOT a knob: it is the already
   documented ``HARNESS_WORKER_POLL_SECONDS`` idle cadence.

M19-N2 SURVIVAL — the wake seam: polling alone cannot beat a long backoff
when new work arrives during one. :meth:`RunQueueWorker.wake` ends the sleep
early; the CLEAN INTEGRATION point for N2's slim bus is a bus listener that
calls ``wake()`` when a lifecycle announcement lands (a slim signal carries no
payload, so it substitutes for polling purely as a timing hint — the claim
path stays the durable queue, and a missed/duplicate signal is harmless
because the next cycle polls anyway). Wiring example::

    bus.add_event_listener(lambda *_: worker.wake())

Nothing in section4 constructs such a listener yet (delivery ownership is
section1's), so the loop keeps polling at the adapted cadence as the
fallback — the seam is additive and safe either way.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from harness_orchestrator.engine_runtime import resolve_engine_max_concurrency

logger = logging.getLogger(__name__)

# M20-T1: default claim batch = the executor width (ENGINE_MAX_CONCURRENCY,
# default 8 — see docs/engine-concurrency.md). Claiming more than the
# executor can run concurrently just parks extra claims in 'running' behind
# queued executor work; claiming less leaves admission slower than execution.
_DEFAULT_CLAIM_BATCH_FALLBACK = 8

# Adaptive-cadence bounds. The floor trades a little DB traffic for latency
# while saturated; backoff grows geometrically but its ceiling is the
# configured idle cadence (poll_interval) — no separate max knob needed.
_DEFAULT_MIN_POLL_SECONDS = 0.01
_DEFAULT_BACKOFF_FACTOR = 2.0
#: W8 slice 2: how often the worker tries to reclaim abandoned boundary networks.
_DEFAULT_BOUNDARY_SWEEP_SECONDS = 600.0
#: W22: a sweep that judged nothing (daemon down, no socket) retries on this clock.
#: Idling the full interval after a skipped sweep is how an orphan outlived its
#: run: the orchestrator restart that skipped it was the only chance it got.
_SWEEP_RETRY_SECONDS = 30.0
#: W22: suspend detection and the reap deferral after it, sized from the
#: service's 10s heartbeat interval (two missed beats plus margin).
_SUSPEND_SLACK_SECONDS = 10.0
_POST_SUSPEND_REAP_GRACE_SECONDS = 25.0


def resolve_worker_claim_batch(environ=None) -> int:
    """Runs claimable per poll cycle: WORKER_CLAIM_BATCH override, else the
    executor width (ENGINE_MAX_CONCURRENCY), else 8."""
    env = os.environ if environ is None else environ
    raw = str(env.get("WORKER_CLAIM_BATCH", "")).strip()
    if not raw:
        return resolve_engine_max_concurrency(env)
    try:
        value = int(raw)
    except ValueError:
        logger.warning("ignoring non-integer WORKER_CLAIM_BATCH=%r", raw)
        return resolve_engine_max_concurrency(env)
    if value < 1:
        logger.warning("ignoring out-of-range WORKER_CLAIM_BATCH=%r (minimum 1)", raw)
        return resolve_engine_max_concurrency(env)
    return value


def _float_knob(
    env, name: str, default: float, minimum: float, maximum: float | None = None
) -> float:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("ignoring non-numeric %s=%r", name, raw)
        return default
    if value < minimum or (maximum is not None and value > maximum):
        logger.warning("ignoring out-of-range %s=%r", name, raw)
        return default
    return value


class RunQueueWorker:
    def __init__(
        self,
        service: Any,
        poll_interval: float = 0.5,
        claim_batch: int | None = None,
        min_poll_seconds: float | None = None,
        backoff_factor: float | None = None,
    ) -> None:
        self.service = service
        # Idle/base cadence AND the backoff target: adaptation never exceeds
        # the deployment's configured HARNESS_WORKER_POLL_SECONDS.
        self.poll_interval = float(poll_interval)
        env = os.environ
        self.claim_batch = (
            resolve_worker_claim_batch(env) if claim_batch is None else max(int(claim_batch), 1)
        )
        self.min_poll_seconds = (
            _float_knob(
                env,
                "WORKER_POLL_MIN_SECONDS",
                _DEFAULT_MIN_POLL_SECONDS,
                minimum=0.000001,
                maximum=self.poll_interval,
            )
            if min_poll_seconds is None
            else max(float(min_poll_seconds), 0.000001)
        )
        self.backoff_factor = (
            _float_knob(
                env, "WORKER_POLL_BACKOFF_FACTOR", _DEFAULT_BACKOFF_FACTOR, minimum=1.000001
            )
            if backoff_factor is None
            else max(float(backoff_factor), 1.000001)
        )
        self._task: asyncio.Task | None = None
        # W8 slice 2: the boundary sweep is housekeeping, not claim work — run it
        # on its own slow cadence (first pass immediately after startup).
        self.boundary_sweep_seconds = _float_knob(
            env, "BOUNDARY_SWEEP_INTERVAL_SECONDS", _DEFAULT_BOUNDARY_SWEEP_SECONDS, minimum=1.0
        )
        self._last_boundary_sweep = 0.0
        # N2 slim-bus wake seam: created when the loop starts (needs a loop);
        # wake() before that is a harmless no-op.
        self._wake: asyncio.Event | None = None

    def wake(self) -> None:
        """End the current inter-cycle sleep early (N2 slim-bus seam).

        Purely a timing hint — see the module docstring for the integration
        contract. Safe to call from any task/thread before the loop starts.
        """
        if self._wake is not None:
            self._wake.set()

    def _next_interval(self, claimed: int, current: float) -> float:
        """Adaptive cadence rule — one place, unit-tested."""
        if claimed >= self.claim_batch:
            return self.min_poll_seconds  # saturated: minimum latency
        if claimed > 0:
            # Productive but not saturated: hold (floor at min, capped at the
            # configured base so a tiny min never outruns deployment config).
            return max(self.min_poll_seconds, min(current, self.poll_interval))
        return min(current * self.backoff_factor, self.poll_interval)

    async def _loop(self) -> None:
        try:
            await self.service.recover_orphans()
        except Exception:  # noqa: BLE001 — startup recovery must not prevent serving
            logger.exception("orphan recovery failed at worker start")
        self._wake = asyncio.Event()
        interval = self.poll_interval
        cycle_started = time.time()
        reap_after = 0.0
        while True:
            now = time.time()
            # W22: a cycle that starts far later than its sleep allowed means this
            # process was frozen (host sleep, VM or container pause). Our own runs'
            # heartbeats went stale with it, so reaping first would reap live runs
            # (2026-10-30: reaped 2 s after the Mac woke). Let them beat first.
            if now - cycle_started > interval + _SUSPEND_SLACK_SECONDS:
                reap_after = now + _POST_SUSPEND_REAP_GRACE_SECONDS
                logger.warning(
                    "worker loop was suspended %.0fs; reaping deferred", now - cycle_started
                )
            cycle_started = now
            reaped: list[str] = []
            try:
                # M13-H1b: release timed-out claims every cycle so a dead
                # worker's run is blocked + emitted instead of stuck 'running'.
                if hasattr(self.service, "reap_zombies") and now >= reap_after:
                    reaped = await self.service.reap_zombies() or []
            except Exception:  # noqa: BLE001 — one bad sweep must never kill the worker
                logger.exception("zombie reaper sweep failed")
            if reaped:
                # W22: these runs were just proven dead by the durable reaper, so
                # their boundaries are garbage now — not after the ~10 min sweep
                # interval. This is what closes the window when the orchestrator
                # restarted and lost its in-memory `/reap` loop: the terminal event
                # the reaper just wrote is the settlement proof the sweep reads.
                self._last_boundary_sweep = 0.0
            claimed = 0
            try:
                # W8 slice 2: reclaim boundary networks of runs the lifecycle says
                # are over. Rate-limited, because in the split deployment the
                # Docker socket lives on the worker daemon and this call cannot
                # see it: a per-cycle failure is expected noise, not an incident.
                if (
                    hasattr(self.service, "recover_boundaries")
                    and time.monotonic() - self._last_boundary_sweep >= self.boundary_sweep_seconds
                ):
                    self._last_boundary_sweep = time.monotonic()
                    report = await self.service.recover_boundaries()
                    if report.get("count"):
                        logger.info("boundary sweep removed %s network(s)", report["count"])
                    elif report.get("error") or report.get("skipped"):
                        # W22: nothing was judged, so nothing was gained by starting
                        # the interval — retry on the short clock instead.
                        self._last_boundary_sweep = (
                            time.monotonic() - self.boundary_sweep_seconds + _SWEEP_RETRY_SECONDS
                        )
                        logger.debug(
                            "boundary sweep did nothing: %s",
                            report.get("error") or report.get("skipped"),
                        )
            except Exception:  # noqa: BLE001 — cleanup must never kill the worker
                logger.debug("boundary sweep failed", exc_info=True)
            try:
                # M20-T1: one cycle may admit a whole batch. Services predating
                # the max_claims seam raise TypeError here; fall back to the
                # single-claim shape rather than dying (duck-typed fakes in
                # tests and older deployments both keep working).
                claimed = len(await self.service.poll_once(max_claims=self.claim_batch))
            except TypeError:
                try:
                    await self.service.poll_once()
                    claimed = 1
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — one bad poll must never kill the worker
                    logger.exception("run-queue poll failed")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad poll must never kill the worker
                logger.exception("run-queue poll failed")
            interval = self._next_interval(claimed, interval)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
                # Woken by a slim-bus signal: poll again immediately.
                self._wake.clear()
            except TimeoutError:
                pass

    def run_forever(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
