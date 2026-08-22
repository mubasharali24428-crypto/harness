import json
from pathlib import Path

import pytest
from harness_contracts import (
    API_PREFIX,
    EVENT_TYPES,
    AgentPlan,
    EngineeringBrief,
    EventType,
    Principal,
    Role,
    can,
    error_body,
    event_envelope,
)

GOLDEN = Path(__file__).parent / "golden"


def test_all_twelve_event_types_exist():
    assert {e.value for e in EVENT_TYPES} == {
        "plan_proposed",
        "plan_approved",
        "execution_started",
        "paused",
        "resumed",
        "blocked_awaiting_clarification",
        "done_signal_received",
        "review_passed",
        "review_failed",
        "pr_opened",
        "terminal_output",
        "diff_progress",
    }


def test_event_envelope_wire_shape():
    env = event_envelope(EventType.PLAN_PROPOSED, "proj-1", 42, {"planId": "p1"}, "run-1")
    raw = env.model_dump()
    assert set(raw) == {"type", "payload", "ts", "seq", "projectId", "runId"}
    assert raw["seq"] == 42 and isinstance(raw["seq"], int)


def test_golden_event_fixtures():
    """Golden fixtures every section's mock must match. Regenerate only via deliberate bump."""
    GOLDEN.mkdir(exist_ok=True)
    for et in EventType:
        fixture = GOLDEN / f"{et.value}.json"
        golden = event_envelope(et, "proj-golden", 7, {"fixture": True}, "run-golden")
        if not fixture.exists():
            fixture.write_text(golden.model_dump_json(indent=2))
        assert json.loads(fixture.read_text())["type"] == et.value


def test_plan_has_exactly_brief_fields():
    plan = AgentPlan(goal="g", expectedResult="r")
    fields = set(type(plan).model_fields)
    assert fields == {
        "goal",
        "filesLikelyToChange",
        "steps",
        "tests",
        "risks",
        "estimatedCostUsd",
        "forbiddenPaths",
        "expectedResult",
    }
    with pytest.raises(ValueError):
        AgentPlan(goal="g")  # missing expectedResult


def test_brief_five_fields():
    brief = EngineeringBrief(id="b", meetingId="m", projectId="p")
    for f in ("requirements", "constraints", "acceptanceCriteria", "risks", "openQuestions"):
        assert f in type(brief).model_fields
    assert brief.status == "draft"


def test_role_permissions():
    admin = Principal(userId="u", teamId="t", role=Role.ADMIN)
    viewer = Principal(userId="v", teamId="t", role=Role.VIEWER)
    guest = Principal(userId="g", teamId="t", role=Role.GUEST)
    assert can(admin, "approve")
    assert not can(viewer, "approve")
    assert not can(guest, "write")
    assert can(guest, "read")


def test_api_conventions_constants():
    assert API_PREFIX == "/api/v1"
    body = error_body("NOT_FOUND", "missing")
    assert body == {"error": {"code": "NOT_FOUND", "message": "missing"}}
