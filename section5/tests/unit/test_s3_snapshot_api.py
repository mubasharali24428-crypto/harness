"""S3: snapshot HTTP surface - authz, isolation, dirty conflict, forbidden paths."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_sandbox.api import configure_sandbox, get_principal, router
from harness_sandbox.checkpoints import CheckpointManager
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService


def client(svc, team="team-a", role="admin"):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u", teamId=team, role=role)
    app.state.project_team_lookup = {"p1": "team-a"}.get
    configure_sandbox(svc)
    return TestClient(app)


@pytest.fixture()
def svc(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="off")
    s = SandboxService(runner=runner, checkpoints=CheckpointManager(root=tmp_path / "tasks"))
    yield s
    configure_sandbox(SandboxService())


def test_snapshot_lifecycle_and_isolation(svc):
    a = client(svc)
    a.post("/api/v1/sandbox/tasks", json={"taskId": "t1", "repoRef": "r", "branch": "main",
                                          "projectId": "p1", "forbiddenPaths": ["infra"]})
    ws = svc.runner.active_tasks["t1"]["workspace"]
    (ws / "a.txt").write_text("one")
    snap = a.post("/api/v1/sandbox/tasks/t1/snapshots", json={"label": "base"})
    assert snap.status_code == 201
    sid = snap.json()["id"]

    (ws / "a.txt").write_text("two")
    conflict = a.post(f"/api/v1/sandbox/tasks/t1/snapshots/{sid}/restore", json={})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["paths"] == ["a.txt"]
    ok = a.post(f"/api/v1/sandbox/tasks/t1/snapshots/{sid}/restore", json={"discardDirty": True})
    assert ok.status_code == 200 and (ws / "a.txt").read_text() == "one"
    assert ok.json()["safetySnapshot"]

    # other team: indistinguishable from a missing task
    b = client(svc, team="team-b")
    for r in (b.get("/api/v1/sandbox/tasks/t1/snapshots"),
              b.post("/api/v1/sandbox/tasks/t1/snapshots", json={}),
              b.post(f"/api/v1/sandbox/tasks/t1/snapshots/{sid}/restore", json={})):
        assert r.status_code == 404
    # viewers cannot write
    v = client(svc, role="viewer")
    assert v.post("/api/v1/sandbox/tasks/t1/snapshots", json={}).status_code == 403
    assert v.get("/api/v1/sandbox/tasks/t1/snapshots").status_code == 200
    assert client(svc).post(
        f"/api/v1/sandbox/tasks/t1/snapshots/{sid}/restore", json={"paths": ["../x"]}
    ).status_code == 403
