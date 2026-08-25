"""M12-G3 — request-size caps on Section 6 meeting routes (raw-06 #9).

Adds explicit Pydantic ``max_length`` caps to every free-text/id request field
(CreateMeetingRequest, PostMessageRequest, InvokeAIRequest, ApproveBriefRequest,
CreatePromptRequest) backed by the shared platform registry
(``harness_contracts.limits``, re-exported for S6 via ``harness_meeting._limits``).
Caps are additive: generous enough that no legitimate payload trips them.

Covered here at the HTTP seam:

* over-limit → 422 (with F1's envelope middleware installed, the frozen
  ``{"error": {"code": "VALIDATION_ERROR", ...}}`` shape);
* at-limit → accepted: validation passes and the handler's normal semantics
  take over;
* parity — this section's caps equal the canonical contracts registry.

Harness note: mirrors tests/conformance/test_meeting_conformance.py — a bare
FastAPI app with the router mounted and ``get_principal`` overridden via
``dependency_overrides`` (auth resolves before body validation in FastAPI, so
the principal override is what lets requests reach the size caps at all).
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

# M17-B3: the canonical middleware + caps live in harness_identity /
# harness_contracts; this venv installs neither, so section1's src tree is
# appended explicitly (tests only).
_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "section1" / "src"))

from harness_contracts.limits import (  # noqa: E402
    MEETING_PROJECT_ID_MAX,
    MEETING_TITLE_MAX,
    MEETING_USER_ID_MAX,
    MESSAGE_CONTENT_MAX,
)
from harness_identity.envelope_middleware import install_error_envelope  # noqa: E402

from harness_meeting.api import get_principal, router  # noqa: E402


def _client(app: FastAPI) -> TestClient:
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="user-g3", teamId="team-g3"
    )
    return TestClient(app)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_over_limit_requests_rejected_422_with_envelope():
    """Over-cap payloads die at validation with the frozen error envelope."""
    app = _app()
    install_error_envelope(app)
    client = _client(app)

    cases = [
        (
            "/api/v1/meetings",
            {"projectId": "p" * (MEETING_PROJECT_ID_MAX + 1), "title": "ok"},
        ),
        (
            "/api/v1/meetings",
            {"projectId": "proj-g3", "title": "T" * (MEETING_TITLE_MAX + 1)},
        ),
    ]
    for path, payload in cases:
        resp = client.post(path, json=payload)
        assert resp.status_code == 422, f"{path} head={str(payload)[:50]}"
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["detail"]["error"]["code"] == "VALIDATION_ERROR"

    # message-bearing routes: create a real meeting first, then over-cap each
    # body field per route.
    meeting_id = client.post(
        "/api/v1/meetings", json={"projectId": "proj-g3", "title": "caps"}
    ).json()["id"]
    over = "x" * (MESSAGE_CONTENT_MAX + 1)
    cases2 = [
        (f"/api/v1/meetings/{meeting_id}/messages", {"userId": "u1", "content": over}),
        (f"/api/v1/meetings/{meeting_id}/ai", {"userId": "u1", "content": over}),
        (f"/api/v1/meetings/{meeting_id}/prompts", {"userId": "u1", "content": over}),
        (
            f"/api/v1/meetings/{meeting_id}/messages",
            {
                "userId": "u" * (MEETING_USER_ID_MAX + 1),
                "content": "ok",
            },
        ),
    ]
    for path, payload in cases2:
        resp = client.post(path, json=payload)
        assert resp.status_code == 422, f"{path} head={str(payload)[:50]}"
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_at_limit_requests_pass_validation():
    """Fields exactly AT each cap are accepted end-to-end."""
    client = _client(_app())

    title = "T" * MEETING_TITLE_MAX
    r = client.post("/api/v1/meetings", json={"projectId": "proj-at", "title": title})
    assert r.status_code == 201
    assert len(r.json()["title"]) == MEETING_TITLE_MAX
    meeting_id = r.json()["id"]

    content = "c" * MESSAGE_CONTENT_MAX
    r = client.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "u" * MEETING_USER_ID_MAX, "content": content},
    )
    assert r.status_code == 201
    assert len(r.json()["content"]) == MESSAGE_CONTENT_MAX

    user = "u" * MEETING_USER_ID_MAX

    # AI invoke at cap: deterministic participant answers inline → 200.
    r = client.post(
        f"/api/v1/meetings/{meeting_id}/ai",
        json={"userId": user, "content": "@ai " + content[: MESSAGE_CONTENT_MAX - 4]},
    )
    assert r.status_code == 200

    # prompt at cap: versioned prompt stored → 201.
    r = client.post(
        f"/api/v1/meetings/{meeting_id}/prompts",
        json={"userId": user, "content": content},
    )
    assert r.status_code == 201
    assert r.json()["version"] == 1


def test_over_limit_without_envelope_handlers_is_still_422():
    """Without F1 handlers installed the floor contract still holds: 422."""
    client = _client(_app())
    resp = client.post(
        "/api/v1/meetings",
        json={"projectId": "proj-x", "title": "T" * (MEETING_TITLE_MAX + 1)},
    )
    assert resp.status_code == 422
    err = resp.json().get("error")
    if isinstance(err, dict):
        assert err["code"] == "VALIDATION_ERROR"
    else:
        assert isinstance(resp.json().get("detail"), list)  # bare FastAPI 422


def test_section6_mirror_matches_shared_limits():
    """S6's _limits shim must re-export the canonical contracts registry.

    M17-B3 replaced the byte-equal copy with a re-export over
    harness_contracts.limits — parity now means: every symbol S6's shim
    exports exists in the canonical registry with the same value.
    """
    import harness_contracts.limits as shared
    from harness_meeting import _limits as mirror

    for name in mirror.__all__:
        assert hasattr(shared, name), f"{name} missing from harness_contracts.limits"
        assert getattr(mirror, name) == getattr(shared, name)
