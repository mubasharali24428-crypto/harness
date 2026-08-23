"""Conformance tests for Section 6: Meeting Area."""

from fastapi.testclient import TestClient
from fixtures.golden_fixtures import (
    GOLDEN_MEETING_TITLE,
    GOLDEN_MESSAGES,
    GOLDEN_PROJECT_ID,
)
from harness_contracts.brief import BriefStatus, EngineeringBrief

from harness_meeting.api import router
from harness_meeting.service import MeetingService


def test_full_meeting_lifecycle():
    """End-to-end: create meeting -> chat -> AI invoke -> brief -> approve."""
    svc = MeetingService()

    # 1. Create meeting
    meeting = svc.create_meeting(GOLDEN_PROJECT_ID, GOLDEN_MEETING_TITLE)
    assert meeting.projectId == GOLDEN_PROJECT_ID
    assert meeting.title == GOLDEN_MEETING_TITLE

    # 2. Post chat messages
    for user_id, content in GOLDEN_MESSAGES:
        svc.post_message(meeting.id, user_id, content)
    messages = svc.get_messages(meeting.id)
    # @ai messages now auto-invoke a server-side AI reply (brief §76-84)
    ai_invoked = sum(1 for _, c in GOLDEN_MESSAGES if c.lstrip().startswith("@ai"))
    assert len(messages) == len(GOLDEN_MESSAGES) + ai_invoked

    # 3. Invoke AI (message 4 starts with @ai)
    ai_msg = svc.invoke_ai(meeting.id, "user-eng-001", "@ai What are JWT best practices?")
    assert ai_msg is not None
    assert ai_msg.isAiGenerated is True

    # 4. AI not invoked for normal message
    no_ai = svc.invoke_ai(meeting.id, "user-eng-001", "Just a normal comment")
    assert no_ai is None

    # 4b. Slash commands reach the AI participant too, not just @ai (regression:
    # is_ai_invoked used to gate on a narrower pattern than extract_ai_command supported).
    for command in ("/summary", "/brief", "/research OAuth2"):
        cmd_msg = svc.invoke_ai(meeting.id, "user-eng-001", command)
        assert cmd_msg is not None, f"{command!r} should invoke the AI participant"
        assert cmd_msg.isAiGenerated is True

    # 5. Generate brief
    brief = svc.generate_brief(meeting.id)
    assert isinstance(brief, EngineeringBrief)
    assert brief.status == BriefStatus.DRAFT
    assert brief.meetingId == meeting.id

    # 6. Approve brief
    approved = svc.approve_brief(meeting.id, brief.id, "user-lead-001")
    assert approved.status == BriefStatus.APPROVED

    # 7. Generate summary
    summary = svc.generate_summary(meeting.id)
    assert len(summary.content) > 0

    # 8. Create prompt
    prompt = svc.create_prompt(meeting.id, "user-eng-001", "Implement JWT auth middleware")
    assert prompt.version == 1
    prompt2 = svc.create_prompt(meeting.id, "user-eng-001", "Implement JWT auth with refresh")
    assert prompt2.version == 2

    # 9. List prompts
    prompts = svc.list_prompts(meeting.id)
    assert len(prompts) == 2


def test_rest_api_conformance():
    """Test all REST API endpoints."""
    from types import SimpleNamespace

    from fastapi import FastAPI

    from harness_meeting.api import get_principal

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u-session", teamId="team-golden"
    )
    client = TestClient(app)

    # 1. Create meeting
    res = client.post(
        "/api/v1/meetings",
        json={"projectId": "proj-api", "title": "API Test Meeting"},
    )
    assert res.status_code == 201
    meeting_id = res.json()["id"]

    # 2. Get meeting
    res = client.get(f"/api/v1/meetings/{meeting_id}")
    assert res.status_code == 200
    assert res.json()["title"] == "API Test Meeting"

    # 3. Post message
    res = client.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "u1", "content": "Hello team"},
    )
    assert res.status_code == 201

    # 4. Get messages
    res = client.get(f"/api/v1/meetings/{meeting_id}/messages")
    assert res.status_code == 200
    assert len(res.json()) == 1

    # 5. Invoke AI (not invoked)
    res = client.post(
        f"/api/v1/meetings/{meeting_id}/ai",
        json={"userId": "u1", "content": "Normal message"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "not_invoked"

    # 6. Invoke AI (invoked)
    res = client.post(
        f"/api/v1/meetings/{meeting_id}/ai",
        json={"userId": "u1", "content": "@ai what is REST?"},
    )
    assert res.status_code == 200
    assert res.json()["isAiGenerated"] is True

    # 7. Generate brief
    res = client.post(f"/api/v1/meetings/{meeting_id}/brief")
    assert res.status_code == 201
    brief_id = res.json()["id"]
    assert res.json()["status"] == "draft"

    # 8. Get brief
    res = client.get(f"/api/v1/meetings/{meeting_id}/brief")
    assert res.status_code == 200

    # 9. Approve brief
    res = client.post(
        f"/api/v1/meetings/{meeting_id}/brief/{brief_id}/approve",
        json={"userId": "u-lead"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "approved"

    # 10. Generate summary
    res = client.post(f"/api/v1/meetings/{meeting_id}/summary")
    assert res.status_code == 201
    assert len(res.json()["content"]) > 0

    # 11. Create prompt
    res = client.post(
        f"/api/v1/meetings/{meeting_id}/prompts",
        json={"userId": "u1", "content": "Build auth module"},
    )
    assert res.status_code == 201
    assert res.json()["version"] == 1

    # 12. List prompts
    res = client.get(f"/api/v1/meetings/{meeting_id}/prompts")
    assert res.status_code == 200
    assert len(res.json()) == 1

    # 13. 404 for nonexistent meeting
    res = client.get("/api/v1/meetings/mtg-nonexistent")
    assert res.status_code == 404


def test_unauthenticated_requests_are_rejected():
    """D6 regression guard: no auth wiring => 503/401, never an open endpoint."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)  # no dependency override / state wiring
    client = TestClient(app)
    res = client.get("/api/v1/meetings/mtg-any")
    assert res.status_code in (401, 403, 503)


def test_message_identity_comes_from_session_not_body():
    """D6: a forged body userId must not override the authenticated principal."""
    from types import SimpleNamespace

    from fastapi import FastAPI

    from harness_meeting.api import get_principal

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u-real", teamId="team-golden"
    )
    client = TestClient(app)
    meeting_id = client.post(
        "/api/v1/meetings", json={"projectId": "proj-auth", "title": "Identity Test"}
    ).json()["id"]
    res = client.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "u-forged", "content": "hello"},
    )
    assert res.status_code == 201
    assert res.json()["userId"] == "u-real"
