import pytest
from fastapi.testclient import TestClient
from fixtures.golden_fixtures import (
    GOLDEN_BRANCH,
    GOLDEN_DIFF_NEW_FILE,
    GOLDEN_FORBIDDEN_PATHS,
    GOLDEN_REPO_REF,
    GOLDEN_TASK_ID,
)
from harness_contracts.signatures import TaskHandle

from harness_sandbox.api import router
from harness_sandbox.service import SandboxService


def test_sandbox_implements_protocol():
    service = SandboxService()
    for method_name in ["run_task", "exec", "apply_edit", "destroy"]:
        method = getattr(service, method_name, None)
        assert method is not None, f"SandboxService missing {method_name}"
        assert callable(method)


@pytest.mark.asyncio
async def test_full_sandbox_lifecycle(tmp_path):
    from harness_sandbox.local_runner import LocalSandboxRunner

    runner = LocalSandboxRunner(storage_root=tmp_path)
    service = SandboxService(runner=runner)

    # 1. run_task
    handle = service.run_task(
        task_id=GOLDEN_TASK_ID,
        repo_ref=GOLDEN_REPO_REF,
        branch=GOLDEN_BRANCH,
        forbidden_paths=GOLDEN_FORBIDDEN_PATHS,
    )
    assert isinstance(handle, TaskHandle)
    assert handle.taskId == GOLDEN_TASK_ID
    assert handle.branch == GOLDEN_BRANCH

    # 2. apply_edit
    service.apply_edit(handle, "src/calc.py", GOLDEN_DIFF_NEW_FILE)

    # 3. exec
    cmd = "python3 -c 'import src.calc; print(src.calc.add(2, 3))'"
    result = await service.exec_structured(handle, cmd)
    assert result.status == "passed"
    assert result.exit_code == 0

    # 4. destroy
    service.destroy(handle)
    assert handle.taskId not in runner.active_tasks


def _principal(role="admin"):
    from types import SimpleNamespace

    return SimpleNamespace(userId="u-golden", teamId="team-golden", role=role)


def _app_with_auth(principal=None):
    from fastapi import FastAPI

    from harness_sandbox.api import get_principal

    app = FastAPI()
    app.include_router(router)
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    client = TestClient(app)
    return app, client


def test_rest_api_conformance():
    app, client = _app_with_auth(_principal("ADMIN"))
    # 1. POST /api/v1/sandbox/tasks
    res = client.post(
        "/api/v1/sandbox/tasks",
        json={
            "taskId": "api-task-001",
            "repoRef": GOLDEN_REPO_REF,
            "branch": GOLDEN_BRANCH,
            "forbiddenPaths": GOLDEN_FORBIDDEN_PATHS,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["taskId"] == "api-task-001"
    assert data["branch"] == GOLDEN_BRANCH

    # 2. POST /api/v1/sandbox/tasks/{taskId}/edits
    res = client.post(
        "/api/v1/sandbox/tasks/api-task-001/edits",
        json={"path": "module.py", "diff": GOLDEN_DIFF_NEW_FILE},
    )
    assert res.status_code == 200

    # 3. POST /api/v1/sandbox/tasks/{taskId}/exec (allowed)
    res = client.post(
        "/api/v1/sandbox/tasks/api-task-001/exec",
        json={"command": "python3 -c 'import module; print(module.add(10, 5))'"},
    )
    assert res.status_code == 200
    exec_data = res.json()
    assert exec_data["status"] == "passed"

    # 4. POST /api/v1/sandbox/tasks/{taskId}/exec (forbidden policy violation)
    res = client.post(
        "/api/v1/sandbox/tasks/api-task-001/exec",
        json={"command": "rm -rf /"},
    )
    assert res.status_code == 403

    # 5. POST /api/v1/sandbox/tasks/{taskId}/destroy
    res = client.post("/api/v1/sandbox/tasks/api-task-001/destroy")
    assert res.status_code == 200


def test_unwired_auth_rejected():
    """D6 regression guard: no auth wiring => 503/401, never an open endpoint."""
    _, client = _app_with_auth()
    res = client.post(
        "/api/v1/sandbox/tasks",
        json={"taskId": "x", "repoRef": "r", "branch": "b"},
    )
    assert res.status_code in (401, 403, 503)


@pytest.mark.parametrize("role", ["viewer", "guest"])
def test_read_only_roles_denied(role):
    _, client = _app_with_auth(_principal(role))
    res = client.post(
        "/api/v1/sandbox/tasks",
        json={"taskId": "x", "repoRef": "r", "branch": "b"},
    )
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"
