"""Conformance tests — Section 8 against the frozen contracts.

Gates checkpoint participation: if any of these fail, Section 8 does not
join integration.
"""

import pytest
from harness_contracts.signatures import ReviewGate

from tests.fixtures.golden_fixtures import (
    GOLDEN_PR_ID,
    GOLDEN_PR_URL,
)


def test_implements_review_gate_protocol(review_service):
    assert isinstance(review_service, ReviewGate)
    assert not hasattr(review_service, "merge"), "ReviewService must never merge"


@pytest.mark.asyncio
async def test_approve_records_audit_and_status(review_service, fake_audit):
    await review_service.approve(GOLDEN_PR_ID, reviewer_user_id="user-reviewer-001")
    assert any(w["action"] == "review.approved" for w in fake_audit.writes), (
        "every approve must be written to the audit log"
    )
    # X-07: the rendering the reviewer worked from is recorded (no team -> honest unknown).
    assert fake_audit.writes[-1]["detail"]["rendering"] == "unknown"


@pytest.mark.asyncio
async def test_request_changes_records_audit(review_service, fake_audit):
    await review_service.request_changes(
        GOLDEN_PR_ID,
        comments=[{"filePath": "src/auth.py", "line": 3, "body": "hash comparison is weak"}],
        reviewer_user_id="user-reviewer-001",
    )
    assert any(w["action"] == "review.changes_requested" for w in fake_audit.writes)


@pytest.mark.asyncio
async def test_review_data_shape_conformance(review_service):
    data = await review_service.get_review_data(GOLDEN_PR_ID)
    assert isinstance(data, object)
    raw = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    for key in ("prId", "pr_url", "changed_files", "risk_flags", "summary"):
        assert key in raw, f"missing {key} in review data"
    files = raw["changed_files"]
    assert files and all("path" in f and "additions" in f and "deletions" in f for f in files)


@pytest.mark.asyncio
async def test_summary_generated_via_section3_contract(review_service, fake_router):
    data = await review_service.get_review_data(GOLDEN_PR_ID)
    assert len(fake_router.call_history) >= 1, "summary must flow through the ModelRouter"
    call = fake_router.call_history[0]
    assert call["role"].value == "reviewer"
    summary = data.summary
    labels = [sec.label for sec in summary.sections]
    assert labels, "summary must have sections"


@pytest.mark.asyncio
async def test_risk_flags_fire_on_golden_diff(review_service):
    flags = await review_service.get_risk_flags(GOLDEN_PR_ID)
    paths = [f.path.lower() for f in flags]
    assert flags, "auth/ path in golden diff must raise at least one risk flag"
    assert any("auth" in p for p in paths)


@pytest.mark.asyncio
async def test_deep_link_points_to_github_never_local_merge(review_service):
    data = await review_service.get_review_data(GOLDEN_PR_ID)
    url = data.pr_url if hasattr(data, "pr_url") else data["pr_url"]
    assert url and url.startswith("https://github.com/")
    assert url == GOLDEN_PR_URL
    assert not hasattr(review_service, "merge")


@pytest.mark.asyncio
async def test_inline_comment_roundtrip(review_service):
    await review_service.add_inline_comment(
        GOLDEN_PR_ID, "src/auth.py", 3, "use hmac.compare_digest", "user-reviewer-001"
    )
    comments = await review_service.get_comments(GOLDEN_PR_ID)
    assert any(
        c.get("file_path") == "src/auth.py" and "hmac" in c.get("body", "") for c in comments
    )


def test_diff_provider_isolation():
    """Section 8 must not import Section 2 internals; diff arrives via callable."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "section8" / "src"
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, pathlib;"
         "bad = [l for p in pathlib.Path(r'" + str(root) + "').rglob('*.py')"
         " for l in p.read_text().splitlines()"
         " if 'harness_github' in l];"
         "print('\\n'.join(bad)); exit(1 if bad else 0)"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"S8 imports S2 internals:\n{result.stdout}"
