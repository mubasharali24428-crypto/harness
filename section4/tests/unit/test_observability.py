"""Wave 8 node A — orchestrator observability.

Covers: X-Request-ID echo/minting via RequestContextMiddleware, one single-line
JSON access-log entry per request (parsed with json.loads from a real captured
record), the in-process /metrics Prometheus exposition (counter + histogram,
status labels), and route-TEMPLATE path labeling (never raw ids).
"""

import json
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role

from harness_orchestrator import api as orch_api
from harness_orchestrator.api import (
    MetricsCollector,
    RequestContextMiddleware,
    get_principal,
    router,
)
from harness_orchestrator.logging_utils import (
    JsonFormatter,
    configure_logging,
    get_request_id,
    request_id_scope,
    reset_request_id,
    set_request_id,
)
from harness_orchestrator.service import OrchestratorService


class _ListHandler(logging.Handler):
    """List-appender: captures real LogRecords for JSON parsing."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def client(monkeypatch):
    """Orchestrator app as deployed: router + request middleware + auth wired."""
    monkeypatch.setattr(orch_api, "_service", OrchestratorService())
    app = FastAPI()
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.dependency_overrides[get_principal] = lambda: Principal(
        userId="u-obs", teamId="team-a", role=Role.ADMIN
    )
    app.add_middleware(RequestContextMiddleware)
    return TestClient(app)


# ------------------------------------------------------- contextvar helpers


def test_request_id_contextvar_set_get_reset_roundtrip():
    assert get_request_id() is None  # outside any request
    token = set_request_id("rid-direct")
    try:
        assert get_request_id() == "rid-direct"
    finally:
        reset_request_id(token)
    assert get_request_id() is None


def test_request_id_scope_restores_previous_value():
    with request_id_scope("outer"):
        with request_id_scope("inner") as bound:
            assert bound == "inner" and get_request_id() == "inner"
        assert get_request_id() == "outer"


# ------------------------------------------------------------ X-Request-ID


def test_request_id_echoed_from_client_header(client):
    res = client.get("/health", headers={"X-Request-ID": "abc123def456"})
    assert res.status_code == 200
    assert res.headers["x-request-id"] == "abc123def456"


def test_request_id_minted_when_absent(client):
    first = client.get("/health").headers["x-request-id"]
    second = client.get("/health").headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{12}", first), f"not uuid4().hex[:12]: {first!r}"
    assert re.fullmatch(r"[0-9a-f]{12}", second)
    assert first != second  # freshly minted per request


def test_request_id_header_always_present_on_errors(client):
    # Unmatched route -> FastAPI 404, but the header MUST still be set.
    res = client.get("/no/such/route")
    assert res.status_code == 404
    assert re.fullmatch(r"[0-9a-f]{12}", res.headers["x-request-id"])


# ----------------------------------------------------- JSON access log line


def _capture_request_logger():
    """Attach a list-appender to the middleware's logger (INFO enabled)."""
    handler = _ListHandler()
    logger = logging.getLogger("harness.orchestrator.request")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler, logger, old_level


def test_access_log_line_is_single_line_json_with_all_request_keys(client):
    handler, logger, old_level = _capture_request_logger()
    try:
        res = client.get("/health", headers={"X-Request-ID": "logtest000001"})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    assert res.status_code == 200

    matches = [r for r in handler.records if getattr(r, "request_id", "") == "logtest000001"]
    assert len(matches) == 1, "exactly one INFO log line per request"
    record = matches[0]

    line = JsonFormatter().format(record)
    assert "\n" not in line, "log line must be single-line JSON"

    parsed = json.loads(line)  # the captured REAL line must parse as JSON
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "harness.orchestrator.request"
    assert parsed["msg"] == "http request"
    assert parsed["requestId"] == "logtest000001"
    assert parsed["method"] == "GET"
    assert parsed["path"] == "/health"
    assert parsed["status"] == 200
    assert isinstance(parsed["durMs"], int | float) and parsed["durMs"] >= 0
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", parsed["ts"])


def test_plain_records_get_null_requestid_and_only_common_keys(client):
    plain_logger = logging.getLogger("harness.observability.test.plain")
    handler = _ListHandler()
    old_level = plain_logger.level
    plain_logger.setLevel(logging.INFO)
    plain_logger.addHandler(handler)
    try:
        client.get("/health")
        plain_logger.info("hello plain world")
    finally:
        plain_logger.removeHandler(handler)
        plain_logger.setLevel(old_level)

    record = next(r for r in handler.records if r.msg == "hello plain world")
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["requestId"] is None
    assert set(parsed) == {"ts", "level", "logger", "msg", "requestId"}


def test_configure_logging_installs_json_formatter_and_is_idempotent():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        configure_logging("json")
        configured = [h for h in root.handlers if getattr(h, "_harness_obs_handler", False)]
        assert len(configured) == 1
        assert isinstance(configured[0].formatter, JsonFormatter)

        configure_logging("console")  # replaces, never stacks a second handler
        configured = [h for h in root.handlers if getattr(h, "_harness_obs_handler", False)]
        assert len(configured) == 1
        assert not isinstance(configured[0].formatter, JsonFormatter)

        configure_logging("json")  # back to json for any later output

        with pytest.raises(ValueError, match="json.*console"):
            configure_logging("xml")
    finally:
        for h in list(root.handlers):
            if getattr(h, "_harness_obs_handler", False):
                root.removeHandler(h)
        root.handlers[:] = original_handlers


# ------------------------------------------------------------------ /metrics


@pytest.fixture(autouse=True)
def _isolated_metrics(monkeypatch):
    """Fresh collector per test so assertions see exactly this test's traffic."""
    monkeypatch.setattr(orch_api, "METRICS", MetricsCollector())
    yield
    monkeypatch.undo()


def test_metrics_exposes_counter_and_histogram_after_traffic(client):
    client.get("/health")
    client.get("/health")
    client.get("/no/such/route")  # a non-2xx series too

    scrape = client.get("/api/v1/orchestrator/metrics")
    assert scrape.status_code == 200
    assert scrape.headers["content-type"].startswith("text/plain")

    body = scrape.text
    assert "# TYPE http_requests_total counter" in body
    assert "# TYPE http_request_duration_seconds histogram" in body

    health_series = re.search(
        r'^http_requests_total\{method="GET",path="/health",status="200"\} (\d+)$',
        body,
        re.MULTILINE,
    )
    assert health_series, body
    assert int(health_series.group(1)) >= 2  # both /health hits counted

    notfound_series = re.search(
        r'^http_requests_total\{method="GET",path="unmatched",status="404"\} (\d+)$',
        body,
        re.MULTILINE,
    )
    assert notfound_series and int(notfound_series.group(1)) == 1

    # Histogram: standard buckets, monotone per series, count matches +Inf.
    buckets = [
        float(m)
        for m in re.findall(r'http_request_duration_seconds_bucket\{.*?le="([\d.]+)"', body)
    ]
    assert buckets[:5] == [0.005, 0.01, 0.025, 0.05, 0.1]

    per_series: dict[str, list[int]] = {}
    for labels, count in re.findall(
        r"^http_request_duration_seconds_bucket\{(.+?)\} (\d+)$", body, re.M
    ):
        per_series.setdefault(labels, []).append(int(count))
    assert per_series, body
    for labels, values in per_series.items():
        assert values == sorted(values), f"non-monotone buckets for {labels}"
    assert len({len(v) for v in per_series.values()}) == 1, "ragged series"

    inf_match = re.search(r'le="\+Inf"\} (\d+)', body)
    assert inf_match
    sum_match = re.search(
        r"http_request_duration_seconds_sum\{.*?\} ([\d.eE+-]+)", body
    )
    assert sum_match
    assert float(sum_match.group(1)) > 0
    # 2x /health + 1x unmatched were observed BEFORE the scrape rendered.
    count_lines = re.findall(r"http_request_duration_seconds_count\{.*?\} (\d+)", body)
    assert sum(int(n) for n in count_lines) == 3


def test_metrics_path_label_is_route_template_never_raw_id(client):
    res = client.get("/api/v1/orchestrator/runs/xyz/status")
    assert res.status_code == 200

    body = client.get("/api/v1/orchestrator/metrics").text
    assert 'path="/api/v1/orchestrator/runs/{runId}/status"' in body
    assert "/runs/xyz/status" not in body  # raw id never becomes a label

    template_series = re.search(
        r'^http_requests_total\{method="GET",'
        r'path="/api/v1/orchestrator/runs/\{runId\}/status",status="200"\} (\d+)$',
        body,
        re.MULTILINE,
    )
    assert template_series, body
    assert int(template_series.group(1)) >= 1


def test_metrics_endpoint_is_public_like_health(client):
    # No principal wiring beyond the override; the endpoint itself takes NO
    # auth dependency — same posture as /health.
    res = client.get("/api/v1/orchestrator/metrics")
    assert res.status_code == 200
    assert res.text.startswith("# HELP")


def test_collector_thread_safety_smoke():
    collector = MetricsCollector()
    import threading

    def hammer():
        for i in range(500):
            collector.observe(
                method="GET",
                path="/p",
                status_code=200 + i % 3,
                duration_s=0.001 + (0.001 * i % 0.004),
            )

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    body = collector.render()
    total = sum(int(n) for n in re.findall(r"^http_requests_total\{.*?\} (\d+)$", body, re.M))
    assert total == 4000


# ----------------------------------------------------- W17 Stage-2 eligible-request


def _eligible(body: str) -> tuple[int, int]:
    ok = re.search(r'harness_eligible_requests_total\{outcome="ok"\} (\d+)', body)
    err = re.search(r'harness_eligible_requests_total\{outcome="error"\} (\d+)', body)
    return int(ok.group(1)), int(err.group(1))


def test_eligible_counter_excludes_caller_4xx_and_probes():
    m = MetricsCollector()
    m.observe(method="GET", path="/api/v1/orchestrator/runs", status_code=200, duration_s=0.01)
    m.observe(method="GET", path="/api/v1/orchestrator/runs", status_code=503, duration_s=0.01)
    m.observe(method="GET", path="/api/v1/orchestrator/runs", status_code=404, duration_s=0.01)
    m.observe(method="GET", path="/health", status_code=503, duration_s=0.01)
    m.observe(method="GET", path="/metrics", status_code=500, duration_s=0.01)
    body = m.render()
    # one eligible ok (200), one eligible error (503); the 404 and the two probe
    # 5xx are NOT eligible, but the raw http_requests_total still counts all five.
    assert _eligible(body) == (1, 1)
    assert sum(int(n) for n in re.findall(r"^http_requests_total\{.*?\} (\d+)$", body, re.M)) == 5


def test_eligible_counter_rendered_at_zero_when_no_traffic():
    assert _eligible(MetricsCollector().render()) == (0, 0)


def test_declared_maintenance_window_excludes_requests(monkeypatch):
    monkeypatch.setenv("HARNESS_MAINTENANCE_MODE", "1")
    m = MetricsCollector()
    m.observe(method="GET", path="/api/v1/orchestrator/runs", status_code=500, duration_s=0.01)
    m.observe(method="GET", path="/api/v1/orchestrator/runs", status_code=200, duration_s=0.01)
    # during a declared maintenance window nothing counts against availability, so
    # a real outage masked by maintenance cannot silently read as 0% uptime.
    assert _eligible(m.render()) == (0, 0)
