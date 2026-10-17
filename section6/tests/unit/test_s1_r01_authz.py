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
