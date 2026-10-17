"""S1-R01a negative tests — model router auth, role matrix, spend team lock.

Locks the pilot-blocking fix: every /api/v1/models route except /metrics
requires bearer identity; spending and reconfiguration need write role;
/spend is locked to the principal's own team.
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_router.api import get_principal, router

CALL_BODY = {
    "agentId": "agent-r01",
    "role": "primary",
    "messages": [{"role": "user", "content": "hi"}],
}
CONFIG_BODY = {"role": "primary", "model": "deepseek/deepseek-coder"}


def _app(principal=None):
    app = FastAPI()
    app.include_router(router)
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    return app


def _principal(role="engineer", team_id="t1"):
    return SimpleNamespace(userId="u-r01", teamId=team_id, role=role)


def test_unwired_auth_fails_closed_503_on_all_gated_routes():
    client = TestClient(_app())
    cases = [
        ("post", "/api/v1/models/call", CALL_BODY),
        ("get", "/api/v1/models/spend", None),
        ("get", "/api/v1/models", None),
        ("get", "/api/v1/models/catalog", None),
        (
            "post",
            "/api/v1/models/estimate-cost",
            {"role": "primary", "inputTokens": 1, "outputTokens": 1},
        ),
        ("post", "/api/v1/models/config", CONFIG_BODY),
    ]
    for method, path, body in cases:
        call = getattr(client, method)
        res = call(path) if body is None else call(path, json=body)
        assert res.status_code == 503, (path, res.status_code)
        assert res.json()["detail"]["error"]["code"] == "AUTH_NOT_CONFIGURED"


def test_metrics_stays_public_without_auth():
    res = TestClient(_app()).get("/api/v1/models/metrics")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")


def test_viewer_cannot_spend_or_reconfigure_but_can_read():
    client = TestClient(_app(_principal(role="viewer")))
    assert client.post("/api/v1/models/call", json=CALL_BODY).status_code == 403
    assert client.post("/api/v1/models/config", json=CONFIG_BODY).status_code == 403
    assert client.get("/api/v1/models").status_code == 200
    assert client.get("/api/v1/models/catalog").status_code == 200


def test_spend_denies_foreign_team_and_locks_to_principal():
    client = TestClient(_app(_principal(team_id="t1")))
    res = client.get("/api/v1/models/spend", params={"teamId": "t2"})
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"
    # Same team passes authz, then hits the unwired ledger (proves the lock
    # let it through without a cross-team query).
    res = client.get("/api/v1/models/spend", params={"teamId": "t1"})
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "LEDGER_UNAVAILABLE"
    res = client.get("/api/v1/models/spend")
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "LEDGER_UNAVAILABLE"
