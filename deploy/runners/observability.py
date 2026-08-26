"""Deploy-time observability primitives (Mission 20, lane T2 — OPS-21/OPS-22).

Wave 8 gave ONLY the orchestrator request ids + JSON logs + /metrics; the
other six services answered requests with uvicorn's default text logs and no
metrics at all (m17-findings-sgamma OPS-21/22). This module lifts that stack
to deploy level so EVERY backend gets the same treatment:

* ``configure_logging(fmt)`` — single-line JSON logs on the root logger
  (re-export of section4's implementation; the canonical home stays in
  ``harness_orchestrator.logging_utils`` so its existing tests keep pinning
  it);
* ``RequestContextMiddleware`` — X-Request-ID propagation (echo inbound or
  mint a 12-hex id), one INFO access-log line per request carrying an
  ANONYMIZED user label (sha256(userId)[:12] when a handler stashed one,
  never the raw id), duration, route-template path, status;
* ``install_metrics_endpoint(app)`` — public GET /metrics in Prometheus text
  format (version 0.0.4): golden signals (http_requests_total +
  http_request_duration_seconds histogram) plus domain series where the
  service owns them;
* ``install_domain_metrics(...)`` — orchestrator-only gauges/counters:
  queue depth, event-bus lag, DB pool saturation, runs started/completed.

Deliberately dependency-free: hand-rendered Prometheus text format, same as
Wave 8 — prometheus_client is NOT added.

Env knobs:
    HARNESS_LOG_FORMAT               json|console   (default json)
    HARNESS_METRICS_DB_POLL_SECONDS  gauge refresh interval for DB-backed
                                     gauges; <=0 disables polling (default 15)

The orchestrator keeps importing its own copies from
``harness_orchestrator.api`` / ``logging_utils`` (its tests pin those exact
objects); every other service mounts THIS module's equivalents.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from starlette.datastructures import Headers, MutableHeaders  # noqa: E402
from starlette.routing import Match  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI, Request, Response
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

# --------------------------------------------------------------------------
# Logging — thin re-export so deploy/runners/services.py has ONE import site.
# --------------------------------------------------------------------------

from harness_orchestrator.logging_utils import (  # noqa: E402,F401
    REQUEST_ID_HEADER,
    JsonFormatter,
    configure_logging,
    get_request_id,
    reset_request_id,
    set_request_id,
)

_OBS_LOGGER = "harness.deploy.request"
_obs_logger = logging.getLogger(_OBS_LOGGER)

_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

POOL_BUCKETS: tuple[float, ...] = (2, 4, 6, 8, 10, 12)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt_float(v: float) -> str:
    out = f"{v:.6f}".rstrip("0").rstrip(".")
    return out if out else "0"


@dataclass
class GaugeSpec:
    """One gauge/histogram series family rendered from a callable.

    ``fn`` returns a plain number OR a dict mapping label-value tuples to
    numbers. Exceptions are swallowed and logged once per change — telemetry
    must never take a service down when its database is down (that is exactly
    when you need /metrics).
    """

    name: str
    mtype: str  # "gauge" | "counter" | "histogram"
    help: str
    fn: object
    label_names: tuple[str, ...] = ()
    bucket_bounds: tuple[float, ...] | None = None  # set => histogram shape
    _last_error: str | None = field(default=None, repr=False)


class MetricsCollector:
    """Minimal Prometheus text-format collector (no prometheus_client dep).

    Golden signals via ``observe()``: counter ``http_requests_total
    {method,path,status}`` + histogram ``http_request_duration_seconds``
    (standard buckets). Domain series register :class:`GaugeSpec`s evaluated
    lazily at scrape time. All mutation happens under a threading.Lock so
    worker threads + request tasks can't tear a series.
    """

    def __init__(self, buckets: tuple[float, ...] = _DEFAULT_BUCKETS) -> None:
        self._lock = threading.Lock()
        self._buckets = buckets
        self._counts: dict[tuple[str, str, str], int] = {}
        self._hist_raw: dict[tuple[str, str], list[int]] = {}
        self._hist_sum: dict[tuple[str, str], float] = {}
        self._gauges: dict[str, GaugeSpec] = {}

    # ------------------------------------------------------------- traffic

    def observe(
        self, *, method: str, path: str, status_code: int | str, duration_s: float
    ) -> None:
        ckey = (method, path, str(status_code))
        hkey = (method, path)
        idx = bisect.bisect_right(self._buckets, duration_s)
        with self._lock:
            self._counts[ckey] = self._counts.get(ckey, 0) + 1
            raw = self._hist_raw.setdefault(hkey, [0] * len(self._buckets))
            if 0 <= idx < len(raw):
                raw[idx] += 1
            self._hist_sum[hkey] = self._hist_sum.get(hkey, 0.0) + duration_s

    def add_gauge(self, spec: GaugeSpec) -> None:
        """Register a domain series family (idempotent by name)."""
        with self._lock:
            self._gauges[spec.name] = spec

    # ------------------------------------------------------------- rendering

    def _render_gauge(self, lines: list[str], spec: GaugeSpec) -> None:
        try:
            value = spec.fn()
        except Exception as exc:  # noqa: BLE001 — telemetry must never raise
            if spec._last_error != repr(exc):
                logging.getLogger(__name__).warning(
                    "metrics gauge %s evaluation failed: %r", spec.name, exc
                )
                spec._last_error = repr(exc)
            return
        if spec._last_error is not None:  # recovered this scrape
            logging.getLogger(__name__).info(
                "metrics gauge %s recovered", spec.name
            )
            spec._last_error = None

        lines.append(f"# HELP {spec.name} {spec.help}")
        lines.append(f"# TYPE {spec.name} {spec.mtype}")
        if isinstance(value, dict):
            if not value:
                return
            names = spec.label_names
            for labels in sorted(value, key=repr):
                vals = labels if isinstance(labels, tuple) else (labels,)
                pairs = ",".join(
                    f'{n}="{_escape_label(str(v))}"' for n, v in zip(names, vals)
                )
                body = f"{{{pairs}}}" if pairs else ""
                lines.append(f"{spec.name}{body} {_fmt_float(float(value[labels]))}")
        elif spec.bucket_bounds is not None and isinstance(value, tuple):
            cumulative = 0
            total = sum(value)
            for bound, n in zip(spec.bucket_bounds, value, strict=True):
                cumulative += n
                lines.append(f'{spec.name}_bucket{{le="{bound:g}"}} {cumulative}')
            lines.append(f'{spec.name}_bucket{{le="+Inf"}} {total}')
            lines.append(f"{spec.name}_sum {_fmt_float(sum(value))}")
            lines.append(f"{spec.name}_count {total}")
        else:
            lines.append(f"{spec.name} {_fmt_float(float(value))}")

    def render(self) -> str:
        """Prometheus text format (version 0.0.4), deterministic ordering."""
        with self._lock:
            counters = sorted(self._counts.items())
            hist_raw = {k: list(v) for k, v in self._hist_raw.items()}
            hist_sum = dict(self._hist_sum)
            gauges = list(self._gauges.values())

        lines = [
            "# HELP http_requests_total Total HTTP requests handled.",
            "# TYPE http_requests_total counter",
        ]
        for (method, path, status_label), count in counters:
            lines.append(
                f'http_requests_total{{method="{_escape_label(method)}",'
                f'path="{_escape_label(path)}",status="{_escape_label(status_label)}"}}'
                f" {count}"
            )

        lines.append(
            "# HELP http_request_duration_seconds HTTP request duration in seconds."
        )
        lines.append("# TYPE http_request_duration_seconds histogram")
        for (method, path), raw in sorted(hist_raw.items()):
            labels = f'method="{_escape_label(method)}",path="{_escape_label(path)}"'
            cumulative = 0
            for bucket, n in zip(self._buckets, raw, strict=True):
                cumulative += n
                lines.append(
                    f"http_request_duration_seconds_bucket{{{labels},"
                    f'le="{bucket:g}"}} {cumulative}'
                )
            total = sum(raw)
            lines.append(
                f'http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {total}'
            )
            lines.append(
                f"http_request_duration_seconds_sum{{{labels}}} "
                f"{hist_sum.get((method, path), 0.0)!r}"
            )
            lines.append(f"http_request_duration_seconds_count{{{labels}}} {total}")

        for spec in sorted(gauges, key=lambda s: s.name):
            self._render_gauge(lines, spec)
        return "\n".join(lines) + "\n"


METRICS = MetricsCollector()


def _route_template(scope: dict) -> str:
    """Label-safe path: matched route TEMPLATE or the constant "unmatched".

    FastAPI publishes the matched APIRoute as scope["route"]; raw request
    paths are NEVER used as labels (unbounded cardinality).
    """
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return str(template)
    # Defensive fallback for routers that don't publish scope["route"]:
    # re-match against the app's routes.
    from_app_routes = getattr(scope.get("app"), "routes", None) or ()
    for candidate in from_app_routes:
        try:
            match, _ = candidate.matches(scope)
        except Exception:  # noqa: BLE001 — telemetry must never raise
            continue
        if match is Match.FULL:
            return str(getattr(candidate, "path", "unmatched"))
    return "unmatched"


def anonymize_user_id(user_id: str) -> str:
    """Anonymize a principal id into a bounded, stable label value.

    sha256 hex prefix — enough to correlate one user's requests within an
    incident without writing raw account identifiers into metrics/logs.
    """
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


def get_anon_user(request: "Request") -> str:
    """Best-effort anonymized user label from a FastAPI Request object."""
    stash = getattr(request.state, "harness_user_id", None)
    return anonymize_user_id(stash) if isinstance(stash, str) and stash else ""


class RequestContextMiddleware:
    """Pure-ASGI middleware: X-Request-ID propagation + access log + metrics.

    Deliberately OUTSIDE auth so every response carries X-Request-ID and
    every request produces exactly one INFO JSON line — including 404s,
    401s and handler crashes. Inbound ``X-Request-ID`` is echoed back;
    otherwise a 12-hex-char id is minted. The authenticated user id (when a
    handler stashed it at scope.state.harness_user_id) appears ONLY as its
    sha256[:12] digest — never the raw id.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":  # lifespan/websocket pass through untouched
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(REQUEST_ID_HEADER)
        rid = inbound or uuid.uuid4().hex[:12]
        token = set_request_id(rid)
        started = time.perf_counter()
        seen_status = 500

        async def send_wrapped(message) -> None:
            nonlocal seen_status
            if message["type"] == "http.response.start":
                seen_status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            dur_ms = round((time.perf_counter() - started) * 1000.0, 2)
            path_label = _route_template(scope)
            method = scope.get("method", "-")
            # Read the handler's stash AFTER downstream ran — handlers set
            # request.state.harness_user_id during dispatch. Only the
            # sha256[:12] digest ever leaves this function.
            # NOTE: scope["state"] is a PLAIN DICT per the ASGI spec —
            # request.state.x writes dict KEYS, so read with .get(), never
            # getattr (which would silently hit the default).
            state_obj = scope.get("state")
            if isinstance(state_obj, dict):
                state_user = state_obj.get("harness_user_id", "")
            else:
                state_user = getattr(state_obj or {}, "harness_user_id", "")
            anon_user = (
                anonymize_user_id(state_user)
                if isinstance(state_user, str) and state_user
                else ""
            )
            METRICS.observe(
                method=method,
                path=path_label,
                status_code=seen_status,
                duration_s=dur_ms / 1000.0,
            )
            _obs_logger.info(
                "http request",
                extra={
                    "request_id": rid,
                    "method": method,
                    "path": path_label,
                    "status_code": seen_status,
                    "duration_ms": dur_ms,
                    "user": anon_user,
                },
            )
            reset_request_id(token)


def install_metrics_endpoint(app: "FastAPI") -> None:
    """Mount GET /metrics on an app (public like /health, no auth dependency).

    Root-level path on every service so ONE static prometheus.yml scrapes all
    backends uniformly. The orchestrator additionally keeps its legacy
    /api/v1/orchestrator/metrics route (a different collector instance).
    """
    from fastapi.responses import Response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(
            content=METRICS.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )


# --------------------------------------------------------------------------
# Domain metrics (queue depth / bus lag / pool saturation / run counters).
# Orchestrator-only — the only service that owns those subsystems.
# --------------------------------------------------------------------------


@dataclass
class _DomainState:
    queue_depth: int = 0
    bus_lag_s: int = 0
    # Start fully-shaped so the first scrape renders a valid histogram even
    # before the poller's first successful pass.
    pool_counts: tuple[int, ...] = ()
    runs_started: int = 0
    runs_completed: int = 0
    runs_completed_failed: int = 0
    runs_completed_passed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.pool_counts:
            self.pool_counts = (0,) * len(POOL_BUCKETS)


def _queued_claims(factory: "async_sessionmaker") -> int:
    """COUNT(run_claims WHERE state='queued') — migration 0006 vocabulary."""
    from sqlalchemy import text

    with factory() as db:
        row = db.execute(
            text("SELECT COUNT(*) FROM run_claims WHERE state = 'queued'")
        ).first()
    return int(row[0]) if row else 0


def _bus_lag_seconds(factory: "async_sessionmaker") -> int:
    """Age of the newest agent_events row, floored at 0 (clock skew)."""
    import datetime as dt

    from sqlalchemy import text

    with factory() as db:
        row = db.execute(text("SELECT MAX(ts) FROM agent_events")).first()
    latest = row[0] if row else None
    if latest is None:
        return 0
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=dt.timezone.utc)
    return max(0, int((dt.datetime.now(dt.timezone.utc) - latest).total_seconds()))


def _pool_saturation(engine: "Engine") -> tuple[int, ...]:
    """Per-bucket counts for ONE observation of checked-out connections.

    Works through AsyncEngine's adapted QueuePool (same accessor style as
    tests/test_deploy_pool_math.py). NullPool (sqlite fixtures) reports zero
    instead of raising.
    """
    pool = engine.pool
    if not hasattr(pool, "checkedout") and hasattr(pool, "pool"):
        pool = pool.pool  # unwrap the async adapter's sync pool if needed
    if not hasattr(pool, "checkedout"):
        return (0,) * len(POOL_BUCKETS)
    checked_out = pool.checkedout()
    counts = [0] * len(POOL_BUCKETS)
    idx = bisect.bisect_right(POOL_BUCKETS, checked_out)
    if 0 <= idx < len(counts):
        counts[idx] = 1
    return tuple(counts)


def install_domain_metrics(
    app: "FastAPI",
    *,
    factory: "async_sessionmaker",
    engine: "Engine",
    bus=None,
) -> None:
    """Orchestrator-only domain series wired onto the deployed app.

    Gauges evaluate lazily at each scrape against a cache refreshed by a
    daemon poller thread, so Prometheus intervals don't multiply DB load and
    values exist before the first scrape. DB errors leave the last good
    value in place and log a warning — never crash the app. Run lifecycle
    counters increment from bus events (execution_started / engine_completed
    / engine_failed vocabulary).
    """

    state = _DomainState()

    METRICS.add_gauge(
        GaugeSpec(
            name="harness_db_pool_checked_out_connections",
            mtype="histogram",
            help=(
                "SQLAlchemy QueuePool connections currently checked out"
                " (single observation per scrape; buckets approximate"
                " saturation)."
            ),
            fn=lambda: state.pool_counts,
            bucket_bounds=POOL_BUCKETS,
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_event_bus_lag_seconds",
            mtype="gauge",
            help="Seconds since the newest committed agent_events row.",
            fn=lambda: state.bus_lag_s,
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_queue_depth",
            mtype="gauge",
            help="Run claims waiting in state='queued' (run_claims table).",
            fn=lambda: state.queue_depth,
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_runs_completed_total",
            mtype="counter",
            help="Runs finished (terminal states), by outcome.",
            fn=lambda: {
                ("failed",): state.runs_completed_failed,
                ("passed",): state.runs_completed_passed,
            },
            label_names=("outcome",),
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_runs_started_total",
            mtype="counter",
            help="Runs accepted for execution (execution_started events).",
            fn=lambda: state.runs_started,
        )
    )

    def _poll_once() -> None:
        queued = _queued_claims(factory)
        lag = _bus_lag_seconds(factory)
        pool_counts = _pool_saturation(engine)
        with state.lock:
            state.queue_depth = queued
            state.bus_lag_s = lag
            state.pool_counts = pool_counts

    def _poll_loop() -> None:
        interval = float(os.environ.get("HARNESS_METRICS_DB_POLL_SECONDS", "15"))
        if interval <= 0:
            return
        while True:
            try:
                _poll_once()
            except Exception as exc:  # noqa: BLE001 — keep polling forever
                logging.getLogger(__name__).warning(
                    "domain-metrics poll failed: %r", exc
                )
            time.sleep(interval)

    threading.Thread(target=_poll_loop, name="harness-domain-metrics", daemon=True).start()

    def _on_event(envelope) -> None:
        etype = str(getattr(envelope, "type", "") or "").split(".")[-1].lower()
        if etype == "engine_failed":
            with state.lock:
                state.runs_completed += 1
                state.runs_completed_failed += 1
        elif "complete" in etype:
            with state.lock:
                state.runs_completed += 1
                state.runs_completed_passed += 1
        elif "start" in etype:
            with state.lock:
                state.runs_started += 1

    active_bus = bus if bus is not None else getattr(app.state, "event_bus", None)
    # Duck-typed on purpose: anything with add_event_listener(callback)
    # qualifies (the real EventBus satisfies this; tests may stub it).
    listener = getattr(active_bus, "add_event_listener", None)
    if callable(listener):
        listener(_on_event)
    else:
        logging.getLogger(__name__).warning(
            "no event bus available — harness_runs_* counters stay at zero"
        )



async def stash_principal_for_metrics(request: "Request", user_id: str | None) -> None:
    """Handler-side helper: remember WHO answered for the access-log label.

    Stores the RAW id on request scope state only; the middleware converts it
    to sha256[:12] before anything leaves the process. Never store raw ids in
    metrics/log output yourself.
    """
    if user_id:
        request.state.harness_user_id = user_id
