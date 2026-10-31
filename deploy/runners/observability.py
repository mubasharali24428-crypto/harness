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
    HARNESS_COST_METRICS_WINDOW_HOURS  latency-quantile window in hours
                                     (default 24; <=0 falls back to 24)

The orchestrator keeps importing its own copies from
``harness_orchestrator.api`` / ``logging_utils`` (its tests pin those exact
objects); every other service mounts THIS module's equivalents.
"""

from __future__ import annotations

import asyncio
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


# Synthetic-probe / self-scrape route templates excluded from the availability
# SLI denominator (docs/runbooks/slo-and-capacity.md: "Do not treat /health alone
# as service availability"; probes failing before real work are not eligible).
_PROBE_PATHS = frozenset({"/health", "/health?deep=1", "/ready", "/metrics"})


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
        # W17 Stage-2: availability-eligible request rollup. The raw
        # http_requests_total{status} is present, but the API-availability SLI
        # must be measured on the ELIGIBLE set only — caller 4xx, synthetic
        # probes (/health, /ready, /metrics) and a declared maintenance window
        # are excluded, per docs/runbooks/slo-and-capacity.md. Availability =
        # ok / (ok + error); this counter is that SLI's denominator. Not a
        # substitute for the owner's decision to adopt a threshold.
        self._eligible = {"ok": 0, "error": 0}

    @staticmethod
    def _classify_eligible(path: str, status_code: int | str) -> str | None:
        """'ok' | 'error' | None (None = not eligible, excluded)."""
        if path in _PROBE_PATHS:
            return None  # synthetic probe / self-scrape
        if os.environ.get("HARNESS_MAINTENANCE_MODE", "").strip().lower() in {"1", "true", "on"}:
            return None  # a declared, announced maintenance window
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            return None  # non-numeric (e.g. websocket/aborted) — not an HTTP outcome
        if 200 <= code < 400:
            return "ok"
        if 400 <= code < 500:
            return None  # caller error: the backend served the request, the input was wrong
        if code >= 500:
            return "error"
        return None  # 1xx informational

    # ------------------------------------------------------------- traffic

    def observe(
        self, *, method: str, path: str, status_code: int | str, duration_s: float
    ) -> None:
        ckey = (method, path, str(status_code))
        hkey = (method, path)
        idx = bisect.bisect_right(self._buckets, duration_s)
        outcome = self._classify_eligible(path, status_code)
        with self._lock:
            self._counts[ckey] = self._counts.get(ckey, 0) + 1
            raw = self._hist_raw.setdefault(hkey, [0] * len(self._buckets))
            if 0 <= idx < len(raw):
                raw[idx] += 1
            self._hist_sum[hkey] = self._hist_sum.get(hkey, 0.0) + duration_s
            if outcome is not None:
                self._eligible[outcome] += 1

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
            eligible = dict(self._eligible)

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

        # Availability SLI denominator: eligible ok/error only (4xx + probes +
        # maintenance excluded at observe()). Both series always rendered so a
        # PromQL ok/(ok+error) is stable at zero traffic. Adopting a threshold and
        # an alert on it is the owner's decision, not implied here.
        lines.append(
            "# HELP harness_eligible_requests_total Availability-eligible HTTP "
            "requests, excluding caller 4xx, synthetic probes and declared maintenance."
        )
        lines.append("# TYPE harness_eligible_requests_total counter")
        lines.append(f'harness_eligible_requests_total{{outcome="ok"}} {eligible["ok"]}')
        lines.append(f'harness_eligible_requests_total{{outcome="error"}} {eligible["error"]}')

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


def install_metrics_endpoint(app: "FastAPI", extra=None) -> None:
    """Mount GET /metrics on an app (public like /health, no auth dependency).

    ``extra``: optional zero-arg callable returning more Prometheus text (a
    service's closed-label domain counters, e.g. harness_event_delivery_total);
    appended after the HTTP series so ONE scrape of /metrics carries them.

    Root-level path on every service so ONE static prometheus.yml scrapes all
    backends uniformly. The orchestrator additionally keeps its legacy
    /api/v1/orchestrator/metrics route (a different collector instance).
    """
    from fastapi.responses import Response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        body = METRICS.render()
        if extra is not None:
            body = body.rstrip("\n") + "\n" + extra()
        return Response(
            content=body,
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
    # Stalled-run signals (worker dead / zombie claims). 0 = healthy.
    oldest_queued_age_s: int = 0
    stale_running_claims: int = 0
    # Part 28 (session 12): backup and WAL-archive health, read from PostgreSQL. Empty = unknown
    # (SQLite, no backup recorded yet), so the series is absent rather than a false zero.
    backup_last_success: dict = field(default_factory=dict)
    wal_archive_failed: dict = field(default_factory=dict)
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


async def _read_domain_gauges(factory: "async_sessionmaker") -> tuple[object, object]:
    """Queue depth and newest-agent_events-ts in one session.

    Handles BOTH a sync sessionmaker (unit fixtures) and an
    async_sessionmaker (the deployed orchestrator factory). Runs on the
    app's event loop — the ONLY place an async engine's pooled connections
    may be used (asyncpg binds them to the creating loop; SQLite aiosqlite
    only supports async IO). MUST NEVER be called via asyncio.run from a
    foreign thread: the poll task lives on the app's own loop.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    session = factory()
    queued_sql = text("SELECT COUNT(*) FROM run_claims WHERE state = 'queued'")
    lag_sql = text("SELECT MAX(ts) FROM agent_events")
    if isinstance(session, AsyncSession):
        async with session:
            queued = (await session.execute(queued_sql)).first()
            latest = (await session.execute(lag_sql)).first()
    else:
        with session:
            queued = session.execute(queued_sql).first()
            latest = session.execute(lag_sql).first()
    return (
        int(queued[0]) if queued else 0,
        latest[0] if latest else None,
    )


async def _read_backup_gauges(factory: "async_sessionmaker") -> tuple[float | None, int | None]:
    """(newest successful base backup as Unix time, pg_stat_archiver.failed_count); None = unknown.
    PostgreSQL only: other databases return (None, None)."""
    from sqlalchemy import text

    async with factory() as session:
        if session.bind.dialect.name != "postgresql":
            return None, None
        failed = (await session.execute(text("SELECT failed_count FROM pg_stat_archiver"))).scalar()
        newest = (
            await session.execute(
                text("SELECT extract(epoch FROM max(finished_at)) FROM backup_runs WHERE kind = 'base'")
            )
        ).scalar()
    return (None if newest is None else float(newest)), (None if failed is None else int(failed))


# --------------------------------------------------------------------------
# Cost/latency rollups (W14) — the cost dashboard reads these; the frozen
# cost_ledger stays the single source of truth.
# --------------------------------------------------------------------------

#: "This month" per dialect. PostgreSQL carries the SAME predicate as
#: ``harness_contracts.budget.MONTH_WINDOW_SQL`` (one definition of the month
#: boundary); SQLite only ever serves scratch schemas and stores ISO text.
_MONTH_WINDOW_SQL = {
    "postgresql": "created_at >= date_trunc('month', now())",
    "sqlite": "created_at >= strftime('%Y-%m-01 00:00:00', 'now')",
}

DEFAULT_COST_WINDOW_HOURS = 24.0


def resolve_cost_window_hours(environ=None) -> float:
    """Latency-quantile window in hours (env override, default 24).

    A non-numeric or non-positive value falls back to the default instead of
    blanking the series — a typo must not silently remove latency telemetry.
    """
    env = os.environ if environ is None else environ
    raw = str(env.get("HARNESS_COST_METRICS_WINDOW_HOURS", "")).strip()
    if not raw:
        return DEFAULT_COST_WINDOW_HOURS
    try:
        hours = float(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "ignoring non-numeric HARNESS_COST_METRICS_WINDOW_HOURS=%r", raw
        )
        return DEFAULT_COST_WINDOW_HOURS
    return hours if hours > 0 else DEFAULT_COST_WINDOW_HOURS


@dataclass
class _CostState:
    """Latest ledger rollups. Fields are REBOUND (never mutated in place) so a
    scrape iterating a snapshot can't tear against the next poll."""

    cost: dict = field(default_factory=dict)  # (team, model) -> USD
    calls: dict = field(default_factory=dict)  # (team, model, role) -> count
    spend_month: dict = field(default_factory=dict)  # team -> USD this month
    budget: dict = field(default_factory=dict)  # team -> resolved cap (USD)
    latency_p50: dict = field(default_factory=dict)  # (team, model) -> seconds
    latency_p95: dict = field(default_factory=dict)  # (team, model) -> seconds


async def _read_cost_gauges(
    factory: async_sessionmaker, *, window_hours: float
) -> _CostState:
    """Per-team/model metering read from the frozen ``cost_ledger``.

    Attribution rides the frozen chain cost_ledger.run_id -> agent_runs ->
    projects -> teams. Rows whose run cannot be resolved are reported under
    ``unattributed`` rather than disappearing. Money is summed in SQL (exact
    NUMERIC on PostgreSQL) and converted once here.

    Latency quantiles use ``percentile_cont``, so they are PostgreSQL-only —
    the deployed database. Other dialects omit the two latency series instead
    of guessing from a truncated fetch.
    """
    from harness_contracts.budget import resolve_cap
    from sqlalchemy import text

    state = _CostState()
    async with factory() as session:
        dialect = session.bind.dialect.name
        totals = (
            await session.execute(
                text(
                    "SELECT COALESCE(p.team_id, 'unattributed') AS team_id,"
                    " l.model, l.agent_role AS role, COUNT(*) AS calls,"
                    " COALESCE(SUM(l.cost_usd), 0) AS cost_usd"
                    " FROM cost_ledger l"
                    " LEFT JOIN agent_runs r ON l.run_id = r.id"
                    " LEFT JOIN projects p ON r.project_id = p.id"
                    " GROUP BY 1, 2, 3"
                )
            )
        ).mappings().all()

        month_predicate = _MONTH_WINDOW_SQL.get(dialect)
        spend_rows = []
        if month_predicate is not None:
            spend_rows = (
                await session.execute(
                    text(
                        "SELECT p.team_id AS team_id, SUM(l.cost_usd) AS spend_usd"
                        " FROM cost_ledger l"
                        " JOIN agent_runs r ON l.run_id = r.id"
                        " JOIN projects p ON r.project_id = p.id"
                        f" WHERE l.{month_predicate}"
                        " GROUP BY 1"
                    )
                )
            ).mappings().all()

        cap_rows = (
            await session.execute(
                text(
                    "SELECT t.id AS team_id, t.budget_usd AS team_budget,"
                    " b.monthly_budget_usd AS plan_budget"
                    " FROM teams t"
                    " LEFT JOIN billing_plans b ON b.team_id = t.id"
                )
            )
        ).mappings().all()

        quantile_rows = []
        if dialect == "postgresql":
            quantile_rows = (
                await session.execute(
                    text(
                        "SELECT COALESCE(p.team_id, 'unattributed') AS team_id,"
                        " l.model,"
                        " percentile_cont(0.5) WITHIN GROUP (ORDER BY l.latency_ms)"
                        " AS p50_ms,"
                        " percentile_cont(0.95) WITHIN GROUP (ORDER BY l.latency_ms)"
                        " AS p95_ms"
                        " FROM cost_ledger l"
                        " LEFT JOIN agent_runs r ON l.run_id = r.id"
                        " LEFT JOIN projects p ON r.project_id = p.id"
                        " WHERE l.created_at >= now()"
                        " - make_interval(secs => :seconds)"
                        " GROUP BY 1, 2"
                    ),
                    {"seconds": float(window_hours) * 3600.0},
                )
            ).mappings().all()

    for row in totals:
        key = (str(row["team_id"]), str(row["model"]))
        state.cost[key] = state.cost.get(key, 0.0) + float(row["cost_usd"] or 0)
        state.calls[(key[0], key[1], str(row["role"]))] = int(row["calls"] or 0)

    # Idle teams still need a zero so "spend vs cap" is a complete picture.
    state.spend_month = {str(row["team_id"]): 0.0 for row in cap_rows}
    for row in spend_rows:
        state.spend_month[str(row["team_id"])] = float(row["spend_usd"] or 0)

    for row in cap_rows:
        cap = resolve_cap(row["team_budget"], row["plan_budget"])
        if cap.cap_usd is not None:
            state.budget[str(row["team_id"])] = float(cap.cap_usd)

    for row in quantile_rows:
        key = (str(row["team_id"]), str(row["model"]))
        state.latency_p50[key] = float(row["p50_ms"]) / 1000.0
        state.latency_p95[key] = float(row["p95_ms"]) / 1000.0

    return state


async def _read_stall_gauges(factory: "async_sessionmaker") -> tuple[int, int]:
    """(oldest queued claim age in s, running claims with a stale heartbeat).

    run_claims timestamps are aware-UTC ISO TEXT (frozen schema), so the age is
    computed in Python and the staleness cutoff compares ISO strings. A dead
    worker shows up here even when queue depth is tiny (one stuck run).
    """
    import datetime as dt

    from sqlalchemy import text

    stale_after = int(os.environ.get("HEARTBEAT_TIMEOUT_SECONDS", "90") or 90)
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(seconds=stale_after)).isoformat()
    async with factory() as session:
        oldest = (
            await session.execute(
                text("SELECT MIN(claimed_at) FROM run_claims WHERE state = 'queued'")
            )
        ).scalar()
        stale = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM run_claims"
                    " WHERE state = 'running' AND heartbeat_at < :cutoff"
                ),
                {"cutoff": cutoff},
            )
        ).scalar()
    return (_bus_lag_seconds(oldest) if oldest else 0, int(stale or 0))


def _bus_lag_seconds(latest: object) -> int:
    """Age (seconds) of the newest agent_events row, floored at 0."""
    import datetime as dt

    if isinstance(latest, str):  # sqlite stores TIMESTAMP as ISO text
        latest = dt.datetime.fromisoformat(latest)
    if latest is not None and latest.tzinfo is None:
        latest = latest.replace(tzinfo=dt.timezone.utc)
    if latest is None:
        return 0
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
    background poll task on the app's event loop, so Prometheus intervals
    don't multiply DB load and values exist before the first scrape. DB
    errors leave the last good value in place and log a warning — never
    crash the app. Run lifecycle counters increment from bus events
    (execution_started / engine_completed / engine_failed vocabulary).
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
            name="harness_oldest_queued_run_age_seconds",
            mtype="gauge",
            help="Age of the oldest run claim still state='queued' (0 = none).",
            fn=lambda: state.oldest_queued_age_s,
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_stale_running_claims",
            mtype="gauge",
            help="Running claims whose heartbeat is older than HEARTBEAT_TIMEOUT_SECONDS.",
            fn=lambda: state.stale_running_claims,
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_backup_last_success_timestamp_seconds",
            mtype="gauge",
            help="Unix time of the newest successful base backup (backup_runs, written by backup.sh).",
            fn=lambda: state.backup_last_success,
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_pg_wal_archive_failed_count_total",
            mtype="counter",
            help="pg_stat_archiver.failed_count: WAL segments the archive_command failed to ship.",
            fn=lambda: state.wal_archive_failed,
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

    # W14 metering: six ledger rollups the cost/latency dashboard reads.
    # All are GAUGES even where the value only grows: release() deletes
    # reserved ledger rows, so these sums can legitimately fall — counter
    # reset semantics would misreport that as a restart.
    cost_state = _CostState()
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_model_cost_usd",
            mtype="gauge",
            help="Ledger cost in USD per team and model (whole ledger).",
            fn=lambda: cost_state.cost,
            label_names=("team_id", "model"),
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_model_calls",
            mtype="gauge",
            help="Metered model calls per team, model and agent role.",
            fn=lambda: cost_state.calls,
            label_names=("team_id", "model", "role"),
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_team_spend_usd_month",
            mtype="gauge",
            help="Month-to-date ledger spend per team (DB-clock month boundary).",
            fn=lambda: cost_state.spend_month,
            label_names=("team_id",),
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_team_budget_usd",
            mtype="gauge",
            help=(
                "Resolved monthly cap per team"
                " (team override > plan > env); absent when unlimited."
            ),
            fn=lambda: cost_state.budget,
            label_names=("team_id",),
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_model_latency_p50_seconds",
            mtype="gauge",
            help="Rolling-window p50 model latency per team and model (PostgreSQL only).",
            fn=lambda: cost_state.latency_p50,
            label_names=("team_id", "model"),
        )
    )
    METRICS.add_gauge(
        GaugeSpec(
            name="harness_model_latency_p95_seconds",
            mtype="gauge",
            help="Rolling-window p95 model latency per team and model (PostgreSQL only).",
            fn=lambda: cost_state.latency_p95,
            label_names=("team_id", "model"),
        )
    )

    async def _poll_once() -> None:
        queued, latest = await _read_domain_gauges(factory)
        with state.lock:
            state.queue_depth = queued
            state.bus_lag_s = _bus_lag_seconds(latest)
            state.pool_counts = _pool_saturation(engine)
        try:  # separate: a schema without run_claims columns must not blank the rest
            oldest_age, stale = await _read_stall_gauges(factory)
            with state.lock:
                state.oldest_queued_age_s = oldest_age
                state.stale_running_claims = stale
        except Exception as exc:  # noqa: BLE001 — telemetry never raises
            logging.getLogger(__name__).warning("stall-gauge poll failed: %r", exc)
        try:  # separate again: SQLite has neither pg_stat_archiver nor backup_runs
            backup, wal_failed = await _read_backup_gauges(factory)
            with state.lock:
                state.backup_last_success = {} if backup is None else {(): backup}
                state.wal_archive_failed = {} if wal_failed is None else {(): wal_failed}
        except Exception as exc:  # noqa: BLE001 — telemetry never raises
            logging.getLogger(__name__).warning("backup-gauge poll failed: %r", exc)
        try:  # separate: scratch schemas may lack cost_ledger / teams
            cost = await _read_cost_gauges(
                factory, window_hours=resolve_cost_window_hours()
            )
            cost_state.cost = cost.cost
            cost_state.calls = cost.calls
            cost_state.spend_month = cost.spend_month
            cost_state.budget = cost.budget
            cost_state.latency_p50 = cost.latency_p50
            cost_state.latency_p95 = cost.latency_p95
        except Exception as exc:  # noqa: BLE001 — telemetry never raises
            logging.getLogger(__name__).warning("cost-gauge poll failed: %r", exc)

    async def _poll_task() -> None:
        interval = float(os.environ.get("HARNESS_METRICS_DB_POLL_SECONDS", "15"))
        while True:
            try:
                await _poll_once()
            except Exception as exc:  # noqa: BLE001 — keep polling forever
                logging.getLogger(__name__).warning(
                    "domain-metrics poll failed: %r", exc
                )
            await asyncio.sleep(interval)

    # Deterministic test/ops hook: drive one poll refresh on demand (await it).
    app.state.domain_metrics_poll = _poll_once

    @app.on_event("startup")
    async def _start_domain_metrics_poll() -> None:
        interval = float(os.environ.get("HARNESS_METRICS_DB_POLL_SECONDS", "15"))
        if interval <= 0:
            return
        app.state.domain_metrics_task = asyncio.create_task(
            _poll_task(), name="harness-domain-metrics"
        )

    @app.on_event("shutdown")
    async def _stop_domain_metrics_poll() -> None:
        task = app.state.domain_metrics_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

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
