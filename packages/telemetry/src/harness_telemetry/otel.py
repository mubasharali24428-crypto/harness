"""F112 — OTel export: frozen agent events -> OpenTelemetry spans.

Two deliberate exports:
  1. ``spans_for_run`` produces *SpanSpecs* with DETERMINISTIC ids (derived
     from runId) — used for goldens, JSON dumps and trace propagation, and
     directly testable without an SDK.
  2. ``export_records`` replays those spans through the real OTel SDK to an
     OTLP/HTTP endpoint (or any span exporter), letting the SDK own the wire
     format.

W3C trace context is supported both directions: ``parse_traceparent`` honours
an inbound header, ``traceparent_for_run`` yields the header a caller should
send so connector -> orchestrator calls join the run's trace.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

from .metrics import RunRecord

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-(?P<span>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def trace_id_for_run(run_id: str) -> str:
    return hashlib.md5(f"harness-run:{run_id}".encode()).hexdigest()


def span_id_for(run_id: str, name: str, index: int) -> str:
    return hashlib.md5(f"{run_id}:{name}:{index}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class SpanSpec:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    start_unix_ns: int
    end_unix_ns: int
    attributes: dict[str, object] = field(default_factory=dict)
    error: bool = False


def _ns(dt) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def spans_for_run(record: RunRecord, *, outcome: str) -> list[SpanSpec]:
    trace_id = trace_id_for_run(record.runId)
    root_id = span_id_for(record.runId, "harness.run", 0)
    root_start = record.planProposedAt or record.lastEventAt
    root_end = record.prOpenedAt or record.blockedAt or record.lastEventAt or root_start
    attrs: dict[str, object] = {
        "harness.run_id": record.runId,
        "harness.project_id": record.projectId,
        "harness.team_id": record.teamId or "unattributed",
        "harness.outcome": outcome,
        "harness.cost_usd": str(record.costUsd),
        "harness.attempts": record.attempts,
    }
    if record.prId:
        attrs["harness.pr_id"] = record.prId
    if record.accepted and record.planProposedAt and record.prOpenedAt:
        attrs["harness.time_to_first_useful_change_minutes"] = round(
            (record.prOpenedAt - record.planProposedAt).total_seconds() / 60, 2
        )
    spans: list[SpanSpec] = [
        SpanSpec(
            name="harness.run",
            trace_id=trace_id,
            span_id=root_id,
            parent_span_id=None,
            start_unix_ns=_ns(root_start),
            end_unix_ns=_ns(root_end),
            attributes=attrs,
            error=outcome in ("rejected", "abandoned"),
        )
    ]

    if record.planProposedAt is not None:
        plan_end = record.approvedAt or record.planProposedAt
        spans.append(
            SpanSpec(
                name="harness.plan",
                trace_id=trace_id,
                span_id=span_id_for(record.runId, "harness.plan", 1),
                parent_span_id=root_id,
                start_unix_ns=_ns(record.planProposedAt),
                end_unix_ns=_ns(plan_end),
                attributes={"harness.plan.approved": record.approvedAt is not None},
                error=record.approvedAt is None and outcome in ("abandoned",),
            )
        )
    if record.firstExecutionAt is not None:
        exec_end = record.engineCompletedAt or record.blockedAt or record.lastEventAt
        spans.append(
            SpanSpec(
                name="harness.execution",
                trace_id=trace_id,
                span_id=span_id_for(record.runId, "harness.execution", 2),
                parent_span_id=root_id,
                start_unix_ns=_ns(record.firstExecutionAt),
                end_unix_ns=_ns(exec_end),
                attributes={
                    "harness.attempts": record.attempts,
                    "harness.engine_failures": record.engineFailedCount,
                },
                error=record.engineFailedCount > 0 and not record.accepted,
            )
        )
    for i, (ts, passed) in enumerate(record.reviewVerdicts):
        spans.append(
            SpanSpec(
                name="harness.review",
                trace_id=trace_id,
                span_id=span_id_for(record.runId, "harness.review", 10 + i),
                parent_span_id=root_id,
                start_unix_ns=_ns(ts),
                end_unix_ns=_ns(ts),
                attributes={"harness.review.passed": passed, "harness.review.verdict_index": i},
                error=not passed,
            )
        )
    if record.prOpenedAt is not None:
        spans.append(
            SpanSpec(
                name="harness.change",
                trace_id=trace_id,
                span_id=span_id_for(record.runId, "harness.change", 20),
                parent_span_id=root_id,
                start_unix_ns=_ns(record.prOpenedAt),
                end_unix_ns=_ns(record.prOpenedAt),
                attributes={"harness.pr_id": record.prId or "", "harness.accepted": record.accepted},
                error=not record.accepted,
            )
        )
    return spans


def export_records(
    records: list[RunRecord],
    *,
    as_of,
    exporter,
    service_name: str = "harness-a5-telemetry",
) -> int:
    """Replay records through the real OTel SDK to ``exporter``; returns spans."""
    provider = TracerProvider(resource=Resource({SERVICE_NAME: service_name}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("harness.telemetry")
    count = 0
    for record in records:
        for spec in spans_for_run(record, outcome=record.outcome(as_of)):
            span = tracer.start_span(spec.name, start_time=spec.start_unix_ns)
            for key, value in spec.attributes.items():
                span.set_attribute(key, value)
            span.set_status(_STATUS_ERROR if spec.error else _STATUS_OK)
            span.end(end_time=spec.end_unix_ns)
            count += 1
    provider.shutdown()
    return count


_STATUS_ERROR = Status(StatusCode.ERROR)
_STATUS_OK = Status(StatusCode.OK)


# ---------------------------------------------------------------------------
# W3C trace context
# ---------------------------------------------------------------------------


def parse_traceparent(header: str) -> tuple[str, str, bool] | None:
    """Strict-enough W3C parse: returns (trace_id, span_id, sampled) or None."""
    match = _TRACEPARENT_RE.match(header.strip().lower())
    if match is None:
        return None
    if match.group("version") == "ff":
        return None
    trace_id, span_id = match.group("trace"), match.group("span")
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id, span_id, bool(int(match.group("flags"), 16) & 0x01)


def format_traceparent(trace_id: str, span_id: str, *, sampled: bool = True) -> str:
    if not _TRACE_ID_RE.match(trace_id) or trace_id == "0" * 32:
        raise ValueError(f"invalid trace id: {trace_id!r}")
    if not _SPAN_ID_RE.match(span_id) or span_id == "0" * 16:
        raise ValueError(f"invalid span id: {span_id!r}")
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"


def traceparent_for_run(run_id: str) -> str:
    """The header a caller should send so its call joins this run's trace."""
    return format_traceparent(trace_id_for_run(run_id), span_id_for(run_id, "harness.run", 0))
