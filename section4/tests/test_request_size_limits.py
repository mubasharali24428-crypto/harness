"""M12-G3 — request-size caps on Section 4 orchestrator routes (raw-06 #9).

Adds explicit Pydantic ``max_length`` caps to every free-text/id request field
(ProposePlanRequest, PlanActionRequest, HumanEditRequest, DoneSignalRequest)
backed by the shared platform registry (``harness_identity.limits``, mirrored
in ``harness_orchestrator._limits``). Caps are additive: generous enough that
no legitimate payload trips them.

Covered here at the HTTP seam:

* over-limit → 422; with F1's envelope middleware installed the body is the
  frozen ``{"error": {"code": "VALIDATION_ERROR", ...}}`` shape;
* at-limit → accepted: validation passes and the handler's own semantics take
  over (409 RUN_NOT_LIVE for done-signal, INVALID_STATE for propose);
* parity — this section's mirror equals section1's shared limits module.

Harness note: mirrors tests/test_done_signal.py — scratch aiosqlite with the
frozen S1 tables (conftest.py untouched), bare-mode OrchestratorService, and a
dependency-overridden admin principal. Auth is resolved BEFORE body validation
in FastAPI, so the principal override is what lets these requests reach the
validation layer at all.
"""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role

# section1 ships the shared cap constants; tests run under section4's
# interpreter paths, so its src tree is appended explicitly (same pattern as
# the wave-1 audit seam in test_done_signal.py).
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "section1" / "src"))

from harness_identity.envelope_middleware import install_error_envelope  # noqa: E402
from harness_identity.limits import PLATFORM_CAPS  # noqa: E402

from harness_orchestrator.api import get_principal, router  # noqa: E402

_ADMIN = Principal(userId="user-g3", teamId="team-g3", role=Role.ADMIN)


def _client(app: FastAPI) -> TestClient:
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    return TestClient(app)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_over_limit_requests_rejected_422_with_envelope():
    """Over-cap payloads die at validation with the frozen error envelope."""
    app = _app()
    install_error_envelope(app)  # F1 handlers are section-local; reuse verbatim
    client = _client(app)

    cases = [
        (
            "/api/v1/orchestrator/runs/run-g3/plan",
            {"goal": "g" * (PLATFORM_CAPS["S4.PROPOSE_GOAL_MAX"] + 1)},
        ),
        (
            "/api/v1/orchestrator/runs/run-g3/plan",
            {"projectId": "p" * (PLATFORM_CAPS["S4.PROPOSE_PROJECT_ID_MAX"] + 1)},
        ),
        (
            "/api/v1/orchestrator/runs/run-g3/plan/approve",
            {"planId": "z" * (PLATFORM_CAPS["S4.PLAN_ACTION_PLAN_ID_MAX"] + 1)},
        ),
        (
            "/api/v1/orchestrator/runs/run-g3/plan/reject",
            {"planId": "z" * (PLATFORM_CAPS["S4.PLAN_ACTION_PLAN_ID_MAX"] + 1)},
        ),
        (
            "/api/v1/orchestrator/runs/run-g3/human-edit",
            {"path": "/" + "d" * PLATFORM_CAPS["S4.HUMAN_EDIT_PATH_MAX"]},
        ),
        (
            "/api/v1/orchestrator/runs/run-g3/human-edit",
            {
                "path": "src/app.py",
                "content": "x" * (PLATFORM_CAPS["S4.HUMAN_EDIT_CONTENT_MAX"] + 1),
            },
        ),
        (
            "/api/v1/orchestrator/runs/run-g3/done-signal",
            {"diffText": "d" * (PLATFORM_CAPS["S4.DONE_DIFF_TEXT_MAX"] + 1)},
        ),
    ]
    for path, payload in cases:
        resp = client.post(path, json=payload)
        assert resp.status_code == 422, f"{path} head={str(payload)[:50]}"
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["message"]
        # legacy mirror kept for pre-unification clients
        assert body["detail"]["error"]["code"] == "VALIDATION_ERROR"


def test_over_limit_loose_422_without_envelope_handlers():
    """Without F1 handlers installed the floor contract still holds: 422."""
    client = _client(_app())
    resp = client.post(
        "/api/v1/orchestrator/runs/run-g3/human-edit",
        json={
            "path": "src/app.py",
            "content": "x" * (PLATFORM_CAPS["S4.HUMAN_EDIT_CONTENT_MAX"] + 1),
        },
    )
    assert resp.status_code == 422


def test_at_limit_requests_pass_validation():
    """Fields exactly AT each cap pass validation; handler semantics answer."""
    client = _client(_app())

    # propose: goal exactly at cap → ACCEPTED end-to-end (bare mode auto-seeds
    # the run): 201 with a minted planId proves validation never fired.
    goal = "g" * PLATFORM_CAPS["S4.PROPOSE_GOAL_MAX"]
    r = client.post("/api/v1/orchestrator/runs/at-limit/plan", json={"goal": goal})
    assert r.status_code == 201
    assert r.json()["planId"]

    # done-signal: diffText exactly at cap → ACCEPTED (idle is a legal
    # done-gate state; the deterministic reviewer answers without a model
    # call), proving the full handler ran on a boundary-size payload.
    diff = "d" * PLATFORM_CAPS["S4.DONE_DIFF_TEXT_MAX"]
    r = client.post(
        "/api/v1/orchestrator/runs/at-limit-ds/done-signal", json={"diffText": diff}
    )
    assert r.status_code == 200
    assert r.json().get("runId") == "at-limit-ds"

    # human-edit at cap: the request MODEL accepts boundary values (the HTTP
    # route's bare-mode liveness error path is W1's concern, not size caps').
    from harness_orchestrator.api import DoneSignalRequest, HumanEditRequest

    edit = HumanEditRequest(
        path="p" * PLATFORM_CAPS["S4.HUMAN_EDIT_PATH_MAX"],
        content="c" * PLATFORM_CAPS["S4.HUMAN_EDIT_CONTENT_MAX"],
    )
    assert len(edit.content) == PLATFORM_CAPS["S4.HUMAN_EDIT_CONTENT_MAX"]
    signal = DoneSignalRequest(diffText=diff)
    assert len(signal.diffText) == PLATFORM_CAPS["S4.DONE_DIFF_TEXT_MAX"]
    assert DoneSignalRequest().diffText == ""  # default unchanged (additive)


def test_section4_mirror_matches_shared_limits():
    """The byte-equal mirror of limits.py must not drift from section1."""
    shared = (_ROOT / "section1" / "src" / "harness_identity" / "limits.py").read_text()
    mirror = (_ROOT / "section4" / "src" / "harness_orchestrator" / "_limits.py").read_text()
    assert mirror == shared
