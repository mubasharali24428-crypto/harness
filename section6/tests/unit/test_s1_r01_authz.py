"""S1-R01d negative tests: Section 6 meeting creation is team-scoped.

- create for a foreign or unknown project → same 404 envelope
- unwired project_team_lookup → 503 (fail closed)
- successful create binds the principal's team on the record, off the wire
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import harness_meeting.api as api_module
from harness_meeting.api import get_principal
from harness_meeting.service import MeetingService


def _principal(team_id="team-a", user_id="u1"):
    return SimpleNamespace(userId=user_id, teamId=team_id, role="admin")


def _client(service, principal=None, lookup=None):
    app = FastAPI()
    app.include_router(api_module.router)
    app.dependency_overrides[api_module.get_meeting_service] = lambda: service
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    if lookup is not None:
        app.state.project_team_lookup = lookup
    return TestClient(app)


def test_create_in_own_team_binds_team():
    svc = MeetingService()
    client = _client(svc, _principal("team-a"), lookup={"p1": "team-a"}.get)
    res = client.post("/api/v1/meetings", json={"projectId": "p1", "title": "ok"})
    assert res.status_code == 201
    meeting_id = res.json()["id"]
    assert svc.meetings[meeting_id].teamId == "team-a"


def test_team_id_not_on_the_wire():
    svc = MeetingService()
    client = _client(svc, _principal("team-a"), lookup={"p1": "team-a"}.get)
    res = client.post("/api/v1/meetings", json={"projectId": "p1", "title": "ok"})
    assert res.status_code == 201
    assert "teamId" not in res.json()


def test_create_for_foreign_project_404_and_no_side_effect():
    svc = MeetingService()
    client = _client(svc, _principal("team-b"), lookup={"p1": "team-a"}.get)
    res = client.post("/api/v1/meetings", json={"projectId": "p1", "title": "no"})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "PROJECT_NOT_FOUND"
    assert svc.meetings == {}


def test_create_for_unknown_project_404():
    svc = MeetingService()
    client = _client(svc, _principal("team-a"), lookup={"p1": "team-a"}.get)
    res = client.post("/api/v1/meetings", json={"projectId": "nope", "title": "no"})
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "PROJECT_NOT_FOUND"


def test_create_with_unwired_lookup_fails_closed_503():
    svc = MeetingService()
    client = _client(svc, _principal("team-a"))  # no project_team_lookup
    res = client.post("/api/v1/meetings", json={"projectId": "p1", "title": "no"})
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "PROJECT_LOOKUP_NOT_CONFIGURED"
    assert svc.meetings == {}


def test_foreign_team_cannot_read_or_mutate_meeting():
    svc = MeetingService()
    own = _client(svc, _principal("team-a"), lookup={"p1": "team-a"}.get)
    created = own.post("/api/v1/meetings", json={"projectId": "p1", "title": "private"})
    meeting_id = created.json()["id"]
    own.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "forged", "content": "hello"},
    )

    foreign = _client(svc, _principal("team-b"), lookup={"p1": "team-a"}.get)
    reads = [
        foreign.get(f"/api/v1/meetings/{meeting_id}"),
        foreign.get(f"/api/v1/meetings/{meeting_id}/messages"),
        foreign.get(f"/api/v1/meetings/{meeting_id}/brief"),
        foreign.get(f"/api/v1/meetings/{meeting_id}/prompts"),
    ]
    writes = [
        foreign.post(
            f"/api/v1/meetings/{meeting_id}/messages",
            json={"userId": "intruder", "content": "leak"},
        ),
        foreign.post(
            f"/api/v1/meetings/{meeting_id}/ai",
            json={"userId": "x", "content": "@ai hi"},
        ),
        foreign.post(f"/api/v1/meetings/{meeting_id}/brief", json={}),
        foreign.post(f"/api/v1/meetings/{meeting_id}/summary", json={}),
        foreign.post(f"/api/v1/meetings/{meeting_id}/close"),
    ]
    for res in [*reads, *writes]:
        assert res.status_code == 404, res.text
        assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert len(svc.get_messages(meeting_id)[0]) == 1


def test_message_author_comes_from_token_not_body():
    svc = MeetingService()
    client = _client(svc, _principal("team-a", "real-user"), lookup={"p1": "team-a"}.get)
    meeting_id = client.post(
        "/api/v1/meetings", json={"projectId": "p1", "title": "authorship"}
    ).json()["id"]
    client.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "spoofed-admin", "content": "hi"},
    )
    assert svc.get_messages(meeting_id)[0][0].userId == "real-user"
