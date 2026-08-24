"""M15-G6b — Section 8 side: reviewer consumes team thresholds.

Covers the resolution contract (missing config → defaults; malformed →
defaults; valid config → applied) and the reviewer's decision-time path
(ReviewService.evaluate_verdict) against a seeded store.
"""

import json

import pytest
from sqlalchemy import text

from harness_review.heuristic import (
    DEFAULT_REVIEWER_THRESHOLDS,
    REVIEWER_THRESHOLD_KEY,
    ReviewerThresholds,
)
from harness_review.team_thresholds import get_team_reviewer_thresholds

TEAM_ID = "t1"  # seeded by conftest's seeded_db fixture


async def _set_team_setting(db_factory, team_id: str, raw: str | None) -> None:
    async with db_factory() as db:
        await db.execute(
            text(
                "INSERT INTO team_settings (team_id, key, value) "
                "VALUES (:team_id, :key, :value)"
            ),
            {"team_id": team_id, "key": REVIEWER_THRESHOLD_KEY, "value": raw},
        )
        await db.commit()


@pytest.mark.asyncio
async def test_missing_config_answers_defaults(seeded_db):
    thresholds, source = await get_team_reviewer_thresholds(
        seeded_db(), TEAM_ID
    )
    assert source == "defaults"
    assert thresholds == DEFAULT_REVIEWER_THRESHOLDS


@pytest.mark.asyncio
async def test_none_team_id_answers_defaults(db):
    thresholds, source = await get_team_reviewer_thresholds(db(), None)
    assert source == "defaults"
    assert thresholds == DEFAULT_REVIEWER_THRESHOLDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        None,                       # NULL value column
        "",                         # empty string
        "not json at all",          # unparseable
        "[1, 2, 3]",                # JSON but not an object
        '"just a string"',          # JSON scalar
        '{"maxCriticalRiskFlags": "lots"}',   # wrong type
        '{"maxCriticalRiskFlags": -5}',       # out of range
        '{"maxChangedFiles": 0}',             # below ge=1 bound
        '{"maxDiffLines": true}',             # bool where int belongs
    ],
)
async def test_malformed_config_degrades_to_defaults(seeded_db, raw):
    await _set_team_setting(seeded_db, TEAM_ID, raw)
    thresholds, source = await get_team_reviewer_thresholds(seeded_db(), TEAM_ID)
    assert source == "defaults"
    assert thresholds == DEFAULT_REVIEWER_THRESHOLDS


@pytest.mark.asyncio
async def test_valid_config_is_applied(seeded_db):
    doc = {
        "maxCriticalRiskFlags": 2,
        "maxHighRiskFlags": 1,
        "maxMediumRiskFlags": 9,
        "maxTotalRiskFlags": 40,
        "maxChangedFiles": 120,
        "maxDiffLines": 9000,
        "requireTestEvidence": True,
    }
    await _set_team_setting(seeded_db, TEAM_ID, json.dumps(doc))
    thresholds, source = await get_team_reviewer_thresholds(seeded_db(), TEAM_ID)
    assert source == "team"
    assert isinstance(thresholds, ReviewerThresholds)
    assert thresholds.max_critical_risk_flags == 2
    assert thresholds.max_high_risk_flags == 1
    assert thresholds.require_test_evidence is True


@pytest.mark.asyncio
async def test_unknown_keys_ignored_valid_rest_applied(seeded_db):
    """Forward compatibility: junk keys can't poison a future-valid doc."""
    await _set_team_setting(
        seeded_db,
        TEAM_ID,
        json.dumps({"maxMediumRiskFlags": 7, "futureKnob": "whatever"}),
    )
    thresholds, source = await get_team_reviewer_thresholds(seeded_db(), TEAM_ID)
    assert source == "team"
    assert thresholds.max_medium_risk_flags == 7


# ------------------------------------------------- reviewer decision path


@pytest.mark.asyncio
async def test_reviewer_verdict_runs_at_defaults_without_config(review_service):
    verdict = await review_service.evaluate_verdict("pr-golden-001")
    # Golden diff touches src/auth.py -> one CRITICAL flag -> fail at defaults.
    assert verdict.thresholds_source == "defaults"
    assert not verdict.passed
    assert any("critical" in r for r in verdict.reasons)


@pytest.mark.asyncio
async def test_reviewer_honors_custom_thresholds(seeded_db, fake_github, fake_router, fake_audit):
    """Same PR, tuned team: raising the critical cap flips the verdict."""
    from harness_review.service import ReviewService

    await _set_team_setting(
        seeded_db, TEAM_ID, json.dumps({"maxCriticalRiskFlags": 5})
    )
    svc = ReviewService(
        session_factory=seeded_db,
        github_service=fake_github,
        model_router=fake_router,
        diff_provider=lambda pid, n: __import__(
            "tests.fixtures.golden_fixtures", fromlist=["GOLDEN_DIFF"]
        ).GOLDEN_DIFF,
        write_audit=fake_audit,
    )
    verdict = await svc.evaluate_verdict("pr-golden-001")
    assert verdict.thresholds_source == "team"
    assert verdict.passed, f"loosened thresholds should rescue the golden PR: {verdict.reasons}"
