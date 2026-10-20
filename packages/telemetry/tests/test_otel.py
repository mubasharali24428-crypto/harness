"""Span mapping, deterministic ids, W3C propagation, real SDK + OTLP export."""

from __future__ import annotations

import hashlib

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from support import fake_otlp_collector

from harness_telemetry import (
    build_records,
    export_records,
    fixtures,
    format_traceparent,
    parse_cost,
    parse_stream,
    parse_traceparent,
    spans_for_run,
    traceparent_for_run,
)
from harness_telemetry.otel import span_id_for, trace_id_for_run

AS_OF = fixtures.AS_OF


def _records():
    events = parse_stream(fixtures.events())
    costs = [parse_cost(c) for c in fixtures.costs()]
    return build_records(events, costs, fixtures.PROJECT_TEAM, as_of=AS_OF)


def test_span_specs_have_stable_ids_and_parentage():
    a1 = next(r for r in _records() if r.runId == "run-a1")
    spans = spans_for_run(a1, outcome=a1.outcome(AS_OF))
    names = [s.name for s in spans]
    assert names == ["harness.run", "harness.plan", "harness.execution", "harness.review",
                     "harness.change"]
    root = spans[0]
    assert root.trace_id == trace_id_for_run("run-a1") == hashlib.md5(
        b"harness-run:run-a1"
    ).hexdigest()
    assert root.span_id == span_id_for("run-a1", "harness.run", 0)
    assert all(s.parent_span_id == root.span_id for s in spans[1:])
    assert root.attributes["harness.time_to_first_useful_change_minutes"] == 35.0
    assert root.attributes["harness.pr_id"] == "pr-101"
    assert root.error is False


def test_span_specs_mark_rejected_and_failed_runs():
    records = {r.runId: r for r in _records()}
    a3 = records["run-a3"]
    spans = spans_for_run(a3, outcome=a3.outcome(AS_OF))
    root = spans[0]
    assert root.attributes["harness.outcome"] == "rejected"
    assert root.error is True
    review = next(s for s in spans if s.name == "harness.review")
    assert review.error is True
    assert "harness.change" not in [s.name for s in spans]  # no PR, no change span

    a2 = records["run-a2"]
    a2_spans = spans_for_run(a2, outcome=a2.outcome(AS_OF))
    execution = next(s for s in a2_spans if s.name == "harness.execution")
    assert execution.attributes["harness.attempts"] == 2
    assert execution.attributes["harness.engine_failures"] == 1
    reviews = [s for s in a2_spans if s.name == "harness.review"]
    assert [s.error for s in reviews] == [True, False]  # failed then passed


def test_traceparent_roundtrip_and_strict_parse():
    run_tp = traceparent_for_run("run-a1")
    parsed = parse_traceparent(run_tp)
    assert parsed == (trace_id_for_run("run-a1"), span_id_for("run-a1", "harness.run", 0), True)

    assert parse_traceparent("garbage") is None
    assert parse_traceparent("00-" + "0" * 32 + "-" + "0" * 16 + "-01") is None
    assert parse_traceparent("ff-" + "a" * 32 + "-" + "b" * 16 + "-01") is None
    assert format_traceparent("a" * 32, "b" * 16, sampled=False).endswith("-00")
    with pytest.raises(ValueError):
        format_traceparent("short", "b" * 16)


def test_export_records_through_real_sdk():
    exporter = InMemorySpanExporter()
    count = export_records(_records(), as_of=AS_OF, exporter=exporter)
    spans = exporter.get_finished_spans()
    assert count == len(spans)
    # roots 8 + plans 8 + executions 7 + review verdicts 6 + changes 3 = 32
    assert count == 32
    by_name = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)
    assert len(by_name["harness.run"]) == 8
    assert len(by_name["harness.change"]) == 3
    statuses = {s.attributes["harness.run_id"]: s.status.status_code.name
                for s in by_name["harness.run"]}
    assert statuses["run-a3"] == "ERROR" and statuses["run-a1"] == "OK"


async def test_export_records_posts_to_real_otlp_collector():
    async with fake_otlp_collector() as (endpoint, received):
        exporter = OTLPSpanExporter(endpoint=endpoint)
        count = export_records(_records(), as_of=AS_OF, exporter=exporter)
        assert count == 32
    assert len(received) == count  # deterministic flush: one POST per span
    assert all(len(body) > 20 for body in received)
    assert any(b"harness.run" in body for body in received)
