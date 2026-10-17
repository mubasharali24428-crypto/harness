"""S1-R01d negative tests: Section 5 task/team isolation.

- task-keyed routes (exec/edit/destroy): foreign tasks read exactly like
  missing ones — same status codes, same envelopes, no mutation
- project-keyed routes: foreign or unknown project → 404; unwired lookup
  → 503 (fail closed)
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_sandbox.api import configure_sandbox, get_principal, router
from harness_sandbox.checkpoints import CheckpointManager
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService


def _principal(team_id="team-a", role="admin"):
    return SimpleNamespace(userId="u1", teamId=team_id, role=role)


def _client(svc, principal=None, lookup=None):
    app = FastAPI()
    app.include_router(router)
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    if lookup is not None:
        app.state.project_team_lookup = lookup
    configure_sandbox(svc)
    return TestClient(app)


@pytest.fixture()
def svc(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks")
    service = SandboxService(
        runner=runner, checkpoints=CheckpointManager(root=tmp_path / "tasks")
    )
    yield service
    configure_sandbox(SandboxService())


def _create_own_task(svc, task_id="t-own"):
    client = _client(svc, _principal("team-a"), lookup={"p1": "team-a"}.get)
    res = client.post(
        "/api/v1/sandbox/tasks",
        json={"taskId": task_id, "repoRef": "org/r", "branch": "main", "projectId": "p1"},
    )
    assert res.status_code == 201
    return task_id


def test_task_created_via_route_records_team(svc):
    task_id = _create_own_task(svc)
    assert svc.runner.active_tasks[task_id]["team_id"] == "team-a"


def test_foreign_exec_matches_missing_task(svc):
    task_id = _create_own_task(svc)
    foreign = _client(svc, _principal("team-b"), lookup={"p1": "team-a"}.get)
    res_foreign = foreign.post(f"/api/v1/sandbox/tasks/{task_id}/exec", json={"command": "echo hi"})
    res_missing = foreign.post("/api/v1/sandbox/tasks/ghost/exec", json={"command": "echo hi"})
    assert res_foreign.status_code == 404
    assert res_foreign.status_code == res_missing.status_code
    assert res_foreign.json()["detail"]["error"]["code"] == "TASK_NOT_FOUND"
    assert res_missing.json()["detail"]["error"]["code"] == "TASK_NOT_FOUND"
    assert res_foreign.json()["detail"]["error"]["message"] == (
        f"Task '{task_id}' not found or destroyed."
    )


def test_foreign_edit_matches_missing_task(svc):
    task_id = _create_own_task(svc)
    foreign = _client(svc, _principal("team-b"), lookup={"p1": "team-a"}.get)
    payload = {"path": "x.py", "diff": "--- a/x.py\n+++ b/x.py\n@@\n+x = 1\n"}
    res_foreign = foreign.post(f"/api/v1/sandbox/tasks/{task_id}/edits", json=payload)
    res_missing = foreign.post("/api/v1/sandbox/tasks/ghost/edits", json=payload)
    assert res_foreign.status_code == 400
    assert res_foreign.status_code == res_missing.status_code
    assert res_foreign.json()["detail"]["error"]["code"] == "DIFF_APPLY_ERROR"
    assert res_missing.json()["detail"]["error"]["code"] == "DIFF_APPLY_ERROR"
    assert res_foreign.json()["detail"]["error"]["message"] == (
        f"Task '{task_id}' not found or destroyed."
    )


def test_foreign_destroy_is_noop_ack(svc):
    task_id = _create_own_task(svc)
    foreign = _client(svc, _principal("team-b"), lookup={"p1": "team-a"}.get)
    res = foreign.post(f"/api/v1/sandbox/tasks/{task_id}/destroy")
    assert res.status_code == 200
    assert res.json() == {"status": "destroyed", "taskId": task_id}
    assert task_id in svc.runner.active_tasks  # untouched

    own = _client(svc, _principal("team-a"), lookup={"p1": "team-a"}.get)
    own.post(f"/api/v1/sandbox/tasks/{task_id}/destroy")
    assert task_id not in svc.runner.active_tasks


def test_create_for_foreign_project_404(svc):
    client = _client(svc, _principal("team-b"), lookup={"p1": "team-a"}.get)
    res = client.post(
        "/api/v1/sandbox/tasks",
        json={"taskId": "t-x", "repoRef": "org/r", "branch": "main", "projectId": "p1"},
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "PROJECT_NOT_FOUND"
    assert "t-x" not in svc.runner.active_tasks


def test_create_for_unknown_project_404(svc):
    client = _client(svc, _principal("team-a"), lookup={"p1": "team-a"}.get)
    res = client.post(
        "/api/v1/sandbox/tasks",
        json={"taskId": "t-y", "repoRef": "org/r", "branch": "main", "projectId": "nope"},
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "PROJECT_NOT_FOUND"


def test_create_with_unwired_lookup_fails_closed_503(svc):
    client = _client(svc, _principal("team-a"))  # no project_team_lookup
    res = client.post(
        "/api/v1/sandbox/tasks",
        json={"taskId": "t-z", "repoRef": "org/r", "branch": "main", "projectId": "p1"},
    )
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "PROJECT_LOOKUP_NOT_CONFIGURED"


def test_checkpoints_foreign_project_404(svc):
    client = _client(svc, _principal("team-b"), lookup={"p1": "team-a"}.get)
    res = client.get("/api/v1/sandbox/p1/checkpoints")
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "CHECKPOINT_REPOSITORY_NOT_FOUND"
