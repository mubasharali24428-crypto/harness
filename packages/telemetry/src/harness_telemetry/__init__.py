"""harness-telemetry: OTel export, measurable value metrics, run archive.

S4 non-negotiables encoded here:
  - a measured accepted change = observed pr_opened AND independent review pass
    (``RunRecord.accepted``); a model claiming completion is a SEPARATE number;
  - every metric ships its definition (``metrics.DEFINITIONS``);
  - all text is scrubbed at ingest (``events.scrub_text``);
  - archive access is team-scoped with fail-closed auth wiring.
"""

from .archive import Archive, ArchiveReport
from .events import CostEntry, RunEvent, parse_cost, parse_event, parse_stream, scrub_text
from .metrics import (
    DEFINITIONS,
    RunRecord,
    build_records,
    cost_metrics,
    productivity_metrics,
    quality_metrics,
)
from .otel import (
    SpanSpec,
    export_records,
    format_traceparent,
    parse_traceparent,
    spans_for_run,
    traceparent_for_run,
)

__all__ = [
    "DEFINITIONS",
    "Archive",
    "ArchiveReport",
    "CostEntry",
    "RunEvent",
    "RunRecord",
    "SpanSpec",
    "build_records",
    "cost_metrics",
    "export_records",
    "format_traceparent",
    "parse_cost",
    "parse_event",
    "parse_stream",
    "parse_traceparent",
    "productivity_metrics",
    "quality_metrics",
    "scrub_text",
    "spans_for_run",
    "traceparent_for_run",
]
