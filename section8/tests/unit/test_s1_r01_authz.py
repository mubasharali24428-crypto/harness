"""S1-R01d negative tests: Section 8 role gates and team scoping.

- approve/request-changes require an approver role (403 ROLE_DENIED)
- PR reads/actions are scoped to the principal's team (foreign PR → 404)
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from harness_review.api import get_principal, make_app
from tests.fixtures.golden_fixtures import GOLDEN_PR_ID, GOLDEN_PR_URL

OTHER_TEAM = "t2"


def _settings():
    return SimpleNamespace(cors_origins=[], master_key=b"test-key")


class _StubService:
    """Records calls; optionally raises LookupError like a foreign-PR read."""

    def __init__(self, raises: bool = False):
        self.calls: list[tuple] = []
        self.raises = raises

    async def approve(self, pr_id, reviewer_user_id, *, team_id=None):
        self.calls.append(("approve", pr_id, team_id))
        if self.raises:
            raise LookupError("not found")

    async def request_changes(self, pr_id, comments, reviewer_user_id, *, team_id=None):
        self.calls.append(("request_changes", pr_id, team_id))

    async def get_review_data(
        self, pr_id, diff_text=None, *, team_id=None, actor_user_id=None
    ):
        self.calls.append(("get_review_data", pr_id, team_id))
        raise LookupError("not found")


def _app(principal, service):
    app = make_app(None, _settings())
    app.state.review_service = service
    app.dependency_overrides[get_principal] = lambda: principal
    return app


async def test_foreign_team_read_is_not_found(review_service):
    with pytest.raises(LookupError):
        await review_service.get_review_data(GOLDEN_PR_ID, team_id=OTHER_TEAM)


async def test_own_team_read_succeeds(review_service):
    data = await review_service.get_review_data(GOLDEN_PR_ID, team_id="t1")
    assert data.pr_url == GOLDEN_PR_URL


async def test_foreign_team_approve_is_not_found(review_service):
    with pytest.raises(LookupError):
        await review_service.approve(GOLDEN_PR_ID, "u1", team_id=OTHER_TEAM)


async def test_foreign_team_comments_is_not_found(review_service):
    with pytest.raises(LookupError):
        await review_service.get_comments(GOLDEN_PR_ID, team_id=OTHER_TEAM)


def test_viewer_approve_denied_403():
    svc = _StubService()
    principal = SimpleNamespace(userId="u1", teamId="t1", role="viewer")
    client = TestClient(_app(principal, svc))
    res = client.post(
        f"/api/v1/reviews/{GOLDEN_PR_ID}/approve",
        json={"reviewer_user_id": "u-forged"},
    )
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"
    assert svc.calls == []


def test_viewer_request_changes_denied_403():
    svc = _StubService()
    principal = SimpleNamespace(userId="u1", teamId="t1", role="viewer")
    client = TestClient(_app(principal, svc))
    res = client.post(
        f"/api/v1/reviews/{GOLDEN_PR_ID}/request-changes",
        json={"reviewer_user_id": "u-forged", "comments": [{"body": "no"}]},
    )
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"
    assert svc.calls == []


def test_engineering_approve_passes_team_id():
    svc = _StubService()
    principal = SimpleNamespace(userId="u1", teamId="t1", role="engineer")
    client = TestClient(_app(principal, svc))
    res = client.post(
        f"/api/v1/reviews/{GOLDEN_PR_ID}/approve",
        json={"reviewer_user_id": "u-forged"},
    )
    assert res.status_code == 200
    assert res.json()["reviewerUserId"] == "u1"
    assert svc.calls == [("approve", GOLDEN_PR_ID, "t1")]


def test_foreign_team_read_maps_to_404():
    svc = _StubService()
    principal = SimpleNamespace(userId="u1", teamId=OTHER_TEAM, role="admin")
    client = TestClient(_app(principal, svc))
    res = client.get(f"/api/v1/reviews/{GOLDEN_PR_ID}")
    assert res.status_code == 404
    assert svc.calls == [("get_review_data", GOLDEN_PR_ID, OTHER_TEAM)]


def test_foreign_team_approve_maps_to_404():
    svc = _StubService(raises=True)
    principal = SimpleNamespace(userId="u1", teamId=OTHER_TEAM, role="admin")
    client = TestClient(_app(principal, svc))
    res = client.post(
        f"/api/v1/reviews/{GOLDEN_PR_ID}/approve",
        json={"reviewer_user_id": "u-forged"},
    )
    assert res.status_code == 404
    assert svc.calls == [("approve", GOLDEN_PR_ID, OTHER_TEAM)]
