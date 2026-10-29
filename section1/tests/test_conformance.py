import json
import re
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
TS_MIRROR = Path(__file__).resolve().parents[2] / "contracts" / "ts" / "types.d.ts"


def test_all_nineteen_event_types_exist():
    """W15: contracts 1.0.0rc6 adds model_routed (additive) — which model an
    execution attempt got and why, so opt-in routing is verifiable after the
    run instead of living only in process memory."""
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
        "engine_completed",
        "engine_failed",
        "tool_call",
        "tool_result",
        "context_compacted",
        "goal_images",
        "model_routed",
    }


def test_ts_mirror_declares_the_same_event_types():
    """`contracts/ts/types.d.ts` is maintained BY HAND on a version bump (the
    generator writes `src/*.ts` only), so its `EventType` union is the one place
    an additive event can be silently forgotten. `model_routed` (rc6) is exactly
    the kind of miss this catches: the union and the Python enum must agree
    member-for-member."""
    union = re.search(
        r"export type EventType =\n(.*?);", TS_MIRROR.read_text(), re.MULTILINE | re.DOTALL
    )
    assert union, "types.d.ts no longer declares an EventType union"
    declared = set(re.findall(r'"([a-z_]+)"', union.group(1)))
    assert declared == set(EventType), (
        f"TS EventType union diverges from the Python enum; "
        f"missing in TS: {sorted(set(EventType) - declared)}, "
        f"unknown in TS: {sorted(declared - set(EventType))}"
    )
    assert EVENT_TYPES == frozenset(EventType)


def test_event_envelope_wire_shape():
    env = event_envelope(EventType.PLAN_PROPOSED, "proj-1", 42, {"planId": "p1"}, "run-1")
    raw = env.model_dump()
    assert set(raw) == {"type", "payload", "ts", "seq", "projectId", "runId", "schemaVersion"}
    assert raw["schemaVersion"] == 1  # part 14 (session 12), docs/realtime.md 5.5
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
