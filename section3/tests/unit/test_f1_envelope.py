"""M12-W2.1 lane F1 tests (Squad ALPHA) — H-019, section3 (envelope smoke).

RequestValidationError (FastAPI 422) on the model-router now answers the
FROZEN error_body() envelope (+ legacy ``detail`` mirror) instead of the
bare {"detail": [...]} default. S3 is a read-mostly compute service with no
mutating POSTs in scope for H-020 this wave (brief: "S3 if trivially safe"
— envelope only; idempotency wiring arrives with its pool owner).

Scratch-app pattern per tests/unit/test_ledger.py.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_router import api as router_api
from harness_router.envelope_middleware import install_error_envelope


def test_validation_error_envelope_on_models_router():
    app = FastAPI()
    app.include_router(router_api.router)
    install_error_envelope(app)
    client = TestClient(app)

    res = client.post("/api/v1/models/call", json={"agentId": 12})
    assert res.status_code == 422
    body = res.json()
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "VALIDATION_ERROR"
    # Backward-compat mirror identical to the pre-unification detail shape.
    assert body["detail"]["error"]["code"] == "VALIDATION_ERROR"
    assert "role" in body["error"]["message"]


def test_string_http_exception_details_normalize():
    from fastapi import HTTPException

    app = FastAPI()
    app.include_router(router_api.router)
    install_error_envelope(app)

    @app.get("/api/v1/models/__f1_plain409")
    async def plain_409():
        raise HTTPException(status_code=409, detail="conflict")

    client = TestClient(app)
    res = client.get("/api/v1/models/__f1_plain409")
    assert res.status_code == 409
    body = res.json()
    assert body["error"]["code"] == "REQUEST_ERROR"
    assert body["error"]["message"] == "conflict"
    assert body["detail"]["error"] == body["error"]
