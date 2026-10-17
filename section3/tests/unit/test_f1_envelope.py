"""M12-W2.1 lane F1 tests (Squad ALPHA) — H-019, section3 (envelope smoke).

RequestValidationError (FastAPI 422) on the model-router now answers the
FROZEN error_body() envelope (+ legacy ``detail`` mirror) instead of the
bare {"detail": [...]} default. S3 is a read-mostly compute service with no
mutating POSTs in scope for H-020 this wave (brief: "S3 if trivially safe"
— envelope only; idempotency wiring arrives with its pool owner).

Scratch-app pattern per tests/unit/test_ledger.py.

M17-B3: install_error_envelope now comes from harness_identity (the ONE
canonical middleware copy); the byte-identical S3 fork is deleted. The S3
test venv reaches it through the same section1/src seam the limiter hooks
historically used (tests only).
"""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[3]
_S1_SRC = str(_ROOT / "section1" / "src")
if _S1_SRC not in sys.path:
    sys.path.append(_S1_SRC)

from harness_identity.envelope_middleware import install_error_envelope  # noqa: E402

from harness_router import api as router_api  # noqa: E402  (path seam above is test-only)


def test_validation_error_envelope_on_models_router():
    from types import SimpleNamespace

    app = FastAPI()
    app.include_router(router_api.router)
    install_error_envelope(app)
    app.dependency_overrides[router_api.get_principal] = lambda: SimpleNamespace(
        userId="u-f1", teamId="t1", role="engineer"
    )
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
