"""Wave 8B — public Prometheus metrics route on the model router.

/api/v1/models/metrics must behave like the health endpoints: no auth dep,
Prometheus text exposition, and counters that move when routes get hit.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_router.api import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _counter_value(body: str, method: str, path: str) -> int:
    """Parse models_requests_total{method,path}; a missing series counts as 0."""
    prefix = f'models_requests_total{{method="{method}",path="{path}"}}'
    for line in body.splitlines():
        if line.startswith(prefix):
            return int(line.rsplit(maxsplit=1)[1])
    return 0


def test_metrics_route_is_public_and_text_plain():
    res = _client().get("/api/v1/models/metrics")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert "# HELP models_requests_total" in res.text
    assert "# TYPE models_requests_total counter" in res.text


def test_counter_increments_after_hitting_models_route():
    client = _client()
    path = "/api/v1/models"  # GET "" route (list available models)
    before = _counter_value(client.get("/api/v1/models/metrics").text, "GET", path)

    listed = client.get(path)
    assert listed.status_code == 200

    after_body = client.get("/api/v1/models/metrics").text
    assert _counter_value(after_body, "GET", path) == before + 1
