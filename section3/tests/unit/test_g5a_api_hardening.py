"""M13-G5a — OpenAPI typing island for the model-router spend route.

GET /api/v1/models/spend is one of the top-20-by-usage routes; it must expose
a concrete component in openapi.json (mirroring ledger.summarize_spend's
camelCase wire shape), not a bare object.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_router.api import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_spend_route_has_concrete_openapi_component():
    schema = _make_app().openapi()
    op = schema["paths"]["/api/v1/models/spend"]["get"]
    ref = op["responses"]["200"]["content"]["application/json"]["schema"].get("$ref")
    assert ref, "spend 200 response has no concrete component"
    name = ref.split("/")[-1]
    assert name == "SpendSummaryResponse"
    assert name in schema["components"]["schemas"]
    # The documented shape mirrors ledger.summarize_spend.
    props = set(schema["components"]["schemas"][name]["properties"])
    assert props == {"totals", "byModel", "byDay"}


def test_spend_503_is_documented_with_envelope():
    schema = _make_app().openapi()
    op = schema["paths"]["/api/v1/models/spend"]["get"]
    err_ref = op["responses"]["503"]["content"]["application/json"]["schema"].get("$ref")
    assert err_ref and err_ref.split("/")[-1] in schema["components"]["schemas"]


def test_spend_route_still_answers_without_ledger():
    """Typing must not change behavior: unwired ledger still yields the
    LEDGER_UNAVAILABLE envelope (503)."""
    client = TestClient(_make_app())
    res = client.get("/api/v1/models/spend")
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "LEDGER_UNAVAILABLE"
