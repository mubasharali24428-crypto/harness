"""Dedicated engine execution runtime: bounded executor, hard run deadlines,
and a hung-provider circuit breaker (M17 Phase B lane B5 — PBC1 F2).

Why this exists
---------------
Every claimed run used to execute via ``asyncio.to_thread``, which rides the
CPython-default ``ThreadPoolExecutor`` (``max_workers = min(32, cpu_count+4)``
⇒ ~16–36 slots depending on the host). Meanwhile the durable queue advertises
``HARNESS_RUN_CONCURRENCY`` (per-team) × teams concurrent runs, so the REAL
process-wide ceiling was invisible and lower than the promise. Worse: a
single hung provider call occupied a slot forever (an OS thread cannot be
cancelled), so N hangs froze ALL engine runs platform-wide while heartbeats
kept every claim looking alive — the zombie reaper correctly refused to
touch them.

This module makes the three guarantees the advertising implies:

1. **Bounded, explicit concurrency** — every engine call funnels through ONE
   ``ThreadPoolExecutor(max_workers=ENGINE_MAX_CONCURRENCY)``. The shipped
   default (8) matches the queue math honestly: per-team cap 4 × 2 seeded
   teams = the most claims the queue can hold ``running`` at once, so the
   executor is never the silent bottleneck hiding below what the queue
   admits. Raise it together with the per-team cap × team count if you mean
   to oversubscribe (see ``docs/engine-concurrency.md``).
2. **Hard per-run deadline** — the synchronous SDK call is wrapped in
   ``asyncio.wait_for(ENGINE_RUN_TIMEOUT_SECONDS)`` (default 600). On expiry
   the run fails honestly (ENGINE_FAILED with a reason starting
   ``timeout:``) and its queue claim is released by the normal finish path.
   Honesty note: the deadline stops the RUN, not the OS thread — threads
   cannot be killed. The breaker below is what stops the pile-up.
3. **Hung-provider circuit breaker** — after
   ``ENGINE_BREAKER_THRESHOLD`` (default 3) consecutive timeouts on one
   ``provider|model``, further runs targeting it fail FAST with a clear
   error until ``ENGINE_BREAKER_COOLDOWN_SECONDS`` (default 300) elapse,
   instead of burning every remaining slot behind a provider already dead.

Env knobs are resolved ONCE at construction (mirrors the
``resolve_heartbeat_timeout_seconds`` law in ``run_queue.py``): a restart
re-reads the deployment's current values; tests inject explicit numbers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)

# Honest default: per-team running cap (HARNESS_RUN_CONCURRENCY, default 4)
# × 2 seeded teams — the largest number of claims the durable queue can hold
# 'running' simultaneously at shipped settings. NOT cpu-derived: the finding
# (PBC1 F2) is precisely that an inherited CPU-derived bound sat invisibly
# below the advertised policy.
_DEFAULT_ENGINE_MAX_CONCURRENCY = 8

# Per-run wall-clock deadline for one engine (SDK) call, in seconds.
_DEFAULT_RUN_TIMEOUT_SECONDS = 600.0

# Circuit breaker: N consecutive timeouts on one provider|model opens the
# circuit for M seconds of cool-down.
_DEFAULT_BREAKER_THRESHOLD = 3
_DEFAULT_BREAKER_COOLDOWN_SECONDS = 300.0


def _int_from_env(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("ignoring non-integer %s=%r", name, raw)
        return default
    if value < minimum:
        logger.warning("ignoring out-of-range %s=%r (minimum %d)", name, raw, minimum)
        return default
    return value


def _float_from_env(
    env: Mapping[str, str], name: str, default: float, minimum: float
) -> float:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("ignoring non-numeric %s=%r", name, raw)
        return default
    if value < minimum:
        logger.warning("ignoring out-of-range %s=%r (minimum %g)", name, raw, minimum)
        return default
    return value


def resolve_engine_max_concurrency(environ: Mapping[str, str] | None = None) -> int:
    """Executor bound: env override, default 8 (= per-team cap × 2 teams)."""
    env = os.environ if environ is None else environ
    return _int_from_env(env, "ENGINE_MAX_CONCURRENCY", _DEFAULT_ENGINE_MAX_CONCURRENCY, 1)


def resolve_engine_run_timeout_seconds(environ: Mapping[str, str] | None = None) -> float:
    """Per-run engine deadline in seconds: env override, default 600."""
    env = os.environ if environ is None else environ
    return _float_from_env(
        env, "ENGINE_RUN_TIMEOUT_SECONDS", _DEFAULT_RUN_TIMEOUT_SECONDS, 0.000001
    )


class CircuitOpenError(RuntimeError):
    """A run was REFUSED because the provider's circuit is open (fail fast).

    Carries ``key`` (the ``provider|model`` identity) and
    ``retry_after_seconds`` so callers/UI can say exactly why a run did not
    even start instead of piling onto a hung provider.
    """

    def __init__(self, key: str, retry_after_seconds: float) -> None:
        self.key = key
        self.retry_after_seconds = max(float(retry_after_seconds), 0.0)
        super().__init__(
            f"provider circuit open for {key} after consecutive timeouts; "
            f"retry in ~{self.retry_after_seconds:.0f}s"
        )


class EngineRunTimeout(TimeoutError):
    """One engine call blew past ENGINE_RUN_TIMEOUT_SECONDS.

    Subclasses ``TimeoutError`` so existing ``except TimeoutError`` shapes
    keep matching; carries the breaker key and the deadline for honest
    reporting. The run's queue claim is released by the caller's normal
    finish path.
    """

    def __init__(self, key: str, seconds: float) -> None:
        self.key = key
        self.seconds = float(seconds)
        super().__init__(
            f"engine run exceeded {self.seconds:.0f}s deadline (provider {key})"
        )


class ProviderCircuitBreaker:
    """Per ``provider|model`` consecutive-timeout circuit breaker.

    Pure in-process state by design: the orchestrator is a single process
    (PBC1 F1), and a distributed breaker is meaningless while that holds.
    Keys isolate providers/models — one hung endpoint never blocks another.
    """

    def __init__(
        self,
        *,
        threshold: int | None = None,
        cooldown_seconds: float | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        self.threshold = (
            _int_from_env(env, "ENGINE_BREAKER_THRESHOLD", _DEFAULT_BREAKER_THRESHOLD, 1)
            if threshold is None
            else int(threshold)
        )
        self.cooldown_seconds = (
            _float_from_env(
                env,
                "ENGINE_BREAKER_COOLDOWN_SECONDS",
                _DEFAULT_BREAKER_COOLDOWN_SECONDS,
                0.000001,
            )
            if cooldown_seconds is None
            else float(cooldown_seconds)
        )
        self._consecutive_timeouts: dict[str, int] = {}
        # monotonic-deadline per open circuit; monotonic clock survives NTP
        # steps and is all this module ever compares.
        self._open_until: dict[str, float] = {}

    @staticmethod
    def key_for(provider: str, model: str | None) -> str:
        """Stable breaker identity: ``provider|model``."""
        return f"{provider}|{model or 'unknown'}"

    def check(self, key: str) -> None:
        """Raise :class:`CircuitOpenError` iff this key's circuit is open.

        Once the cool-down elapses the circuit half-opens: the entry is
        cleared and the timeout streak restarts from zero (the next attempt
        is the probe).
        """
        until = self._open_until.get(key)
        if until is None:
            return
        remaining = until - time.monotonic()
        if remaining > 0:
            raise CircuitOpenError(key, remaining)
        del self._open_until[key]
        self._consecutive_timeouts[key] = 0
        logger.info("provider circuit half-open for %s: cool-down elapsed", key)

    def record_timeout(self, key: str) -> bool:
        """Count one timeout; open the circuit at ``threshold``. True=opened."""
        streak = self._consecutive_timeouts.get(key, 0) + 1
        self._consecutive_timeouts[key] = streak
        if streak < self.threshold:
            logger.warning(
                "provider %s timeout %d/%d", key, streak, self.threshold
            )
            return False
        self._open_until[key] = time.monotonic() + self.cooldown_seconds
        logger.error(
            "provider circuit OPEN for %s: %d consecutive timeouts; "
            "cool-down %.0fs",
            key,
            streak,
            self.cooldown_seconds,
        )
        return True

    def record_success(self, key: str) -> None:
        """A completed call proves the provider answers — clear the streak."""
        self._consecutive_timeouts.pop(key, None)

    def is_open(self, key: str) -> bool:
        until = self._open_until.get(key)
        return until is not None and until > time.monotonic()

    def retry_after_seconds(self, key: str) -> float | None:
        """Seconds until an open circuit half-opens; None when not open."""
        until = self._open_until.get(key)
        if until is None:
            return None
        return max(until - time.monotonic(), 0.0)


def provider_key_for(engine: Any) -> str:
    """Name WHO would hang, for the breaker: ``provider|model``.

    An injected ``backend_runner`` is a local callable (tests/custom
    runtimes) — keyed ``injected`` so its behavior never shades a real
    provider. Everything else drives the dsh SDK against the configured
    model, so the key names ``deepseek-official`` + that model.
    """
    if getattr(engine, "backend_runner", None) is not None:
        provider = "injected"
    else:
        provider = "deepseek-official"
    model = getattr(engine, "model", None)
    return ProviderCircuitBreaker.key_for(provider, model if isinstance(model, str) else None)


class EngineRuntime:
    """Owns the dedicated bounded executor + breaker for engine calls.

    One instance per :class:`~harness_orchestrator.service.OrchestratorService`.
    Threads spawn lazily on first submit, so idle construction costs nothing.
    """

    def __init__(
        self,
        *,
        max_concurrency: int | None = None,
        run_timeout_seconds: float | None = None,
        breaker: ProviderCircuitBreaker | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        self.max_concurrency = (
            resolve_engine_max_concurrency(env)
            if max_concurrency is None
            else int(max_concurrency)
        )
        self.run_timeout_seconds = (
            resolve_engine_run_timeout_seconds(env)
            if run_timeout_seconds is None
            else float(run_timeout_seconds)
        )
        self.breaker = breaker or ProviderCircuitBreaker(environ=env)
        # The ONLY engine-call executor in the process: explicit sizing, named
        # threads (visible in py-spy/faulthandler dumps as engine-run_N).
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_concurrency, thread_name_prefix="engine-run"
        )
        self._inflight: dict[str, Future] = {}

    def is_running(self, session_id: str) -> bool:
        """True while the engine OS thread for this session is still alive."""
        return session_id in self._inflight

    def shutdown(self) -> None:
        """Best-effort executor teardown (app shutdown seam).

        ``cancel_futures`` drops QUEUED work instantly; already-running calls
        cannot be interrupted (OS threads), so ``wait=False`` never blocks
        shutdown behind a hung provider.
        """
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def run(
        self, engine: Any, *, goal: str, session_id: str
    ) -> dict[str, Any]:
        """Run one engine call under breaker + deadline; result or raise.

        Raises:
            CircuitOpenError: provider circuit currently open (nothing ran).
            EngineRunTimeout: call exceeded the deadline; the timeout was
                recorded on the breaker before raising.
        """
        key = provider_key_for(engine)
        self.breaker.check(key)  # fail fast BEFORE consuming a slot
        call: Callable[[], dict[str, Any]] = partial(
            engine.execute_goal, goal=goal, session_id=session_id
        )
        # Submit directly so the thread's real lifetime is tracked: a cancelled
        # or timed-out await does NOT end the thread, and callers (stop) must
        # not claim the run is quiet while it can still touch files.
        cf = self._executor.submit(call)
        self._inflight[session_id] = cf
        cf.add_done_callback(lambda f, sid=session_id: self._inflight.pop(sid, None)
                             if self._inflight.get(sid) is f else None)
        try:
            result = await asyncio.wait_for(
                asyncio.wrap_future(cf), timeout=self.run_timeout_seconds
            )
        except TimeoutError:
            self.breaker.record_timeout(key)
            raise EngineRunTimeout(key, self.run_timeout_seconds) from None
        self.breaker.record_success(key)
        return result
