"""Phase 5: review evidence cannot be forged by the caller."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from harness_review.api import get_principal, make_app
from harness_review.service import NoDiffAvailableError, ReviewService
from tests.fixtures.golden_fixtures import GOLDEN_DIFF, GOLDEN_PR_ID


@pytest.mark.asyncio
async def test_blank_diff_is_refused(seeded_db):
    svc = ReviewService(session_factory=seeded_db)
    with pytest.raises(NoDiffAvailableError):
        await svc.get_review_data(GOLDEN_PR_ID, diff_text="   \n")


@pytest.mark.asyncio
async def test_diff_provider_receives_authenticated_actor(seeded_db):
    seen: list[str | None] = []

    def provider(project_id, pr_number, team_id=None, *, actor_user_id=None):
        seen.append(actor_user_id)
        return GOLDEN_DIFF

    svc = ReviewService(session_factory=seeded_db, diff_provider=provider)
    await svc.get_review_data(GOLDEN_PR_ID, team_id="t1", actor_user_id="reviewer-1")
    assert seen == ["reviewer-1"]


def test_http_diff_override_is_rejected_in_production(seeded_db):
    settings = SimpleNamespace(
        cors_origins=[],
        master_key=b"test-key",
        allow_request_diff=False,
    )
    app = make_app(seeded_db, settings)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="reviewer-1", teamId="t1", role="engineer"
    )
    res = TestClient(app).get(f"/api/v1/reviews/{GOLDEN_PR_ID}?diff_text=%2Bfake")
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["code"] == "DIFF_OVERRIDE_DISABLED"
