"""D6/P0.6 regression tests: bearer identity drives Section 8 routes."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_review.api import get_principal, make_app, router


def _principal(user_id="u-session"):
    return SimpleNamespace(userId=user_id, teamId="team-golden", role="ADMIN")


def _app(principal=None):
    settings = SimpleNamespace(cors_origins=[], master_key=b"test-key")
    app = make_app(None, settings)
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    return app


def test_no_principal_is_rejected():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    res = client.get("/api/v1/reviews/pr-1")
    assert res.status_code in (401, 403, 503)


def test_approve_uses_principal_not_body():
    recorded = {}

    class StubService:
        async def approve(self, pr_id, reviewer_user_id):
            recorded["pr_id"] = pr_id
            recorded["reviewer_user_id"] = reviewer_user_id

    app = _app(_principal("u-real"))
    app.state.review_service = StubService()
    client = TestClient(app)
    res = client.post("/api/v1/reviews/pr-1/approve", json={"reviewer_user_id": "u-forged"})
    assert res.status_code == 200
    assert res.json()["reviewerUserId"] == "u-real"
    assert recorded["reviewer_user_id"] == "u-real"


def test_x_user_id_header_alone_is_not_trusted():
    app = _app()
    client = TestClient(app)
    res = client.get("/api/v1/reviews/pr-1/comments", headers={"x-user-id": "u-spoof"})
    assert res.status_code in (401, 403)
