"""M12-G9 first slice: git snapshots for agent edit batches.

Covers the four required behaviors:
1. seeded temp git repo → a snapshot pair is committed per edit batch;
2. list + diff round-trip through both CheckpointManager and the REST surface;
3. traversal guard on the sha param (../, option injection, ref syntax);
4. no-repo → honest envelope error (404 CHECKPOINT_REPOSITORY_NOT_FOUND).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_sandbox.api import get_principal, router
from harness_sandbox.checkpoints import (
    CheckpointManager,
    InvalidShaError,
    validate_sha,
)
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService

PROJECT = "proj-ckpt"
DIFF_1 = "--- a/src/calc.py\n+++ b/src/calc.py\n@@\n+x = 1\n"


def _seed_repo(workspace: Path) -> None:
    """Make the fresh sandbox workspace look like an existing project repo."""
    workspace.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "seed",
        "GIT_AUTHOR_EMAIL": "s@x",
        "GIT_COMMITTER_NAME": "seed",
        "GIT_COMMITTER_EMAIL": "s@x",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(workspace),
    }
    for args in (
        ["git", "init"],
        ["git", "add", "-A"],
        ["git", "commit", "--allow-empty", "-m", "seed"],
    ):
        subprocess.run(args, cwd=workspace, check=True, capture_output=True, env=env)


def _service(tmp_path: Path) -> tuple[SandboxService, LocalSandboxRunner]:
    # Production wiring: the checkpoint manager guards the SAME storage_root
    # the runner creates workspaces under (both default to config.storage_root
    # in prod; here both are pointed at the tmp root).
    tasks_root = tmp_path / "tasks"
    runner = LocalSandboxRunner(storage_root=tasks_root)
    svc = SandboxService(runner=runner, checkpoints=CheckpointManager(root=tasks_root))
    return svc, runner


# --------------------------------------------------------------------------
# 1. snapshot per batch
# --------------------------------------------------------------------------


def test_edit_batch_creates_pre_and_post_snapshots(tmp_path):
    svc, _runner = _service(tmp_path)
    handle = svc.run_task("t1", "org/repo", "agent/x", [], project_id=PROJECT)
    workspace = Path(svc.runner.active_tasks["t1"]["workspace"])
    _seed_repo(workspace)

    # Simulate uncommitted agent work sitting in the tree when the batch
    # arrives: the pre-edit snapshot must capture exactly this state.
    (workspace / "notes.txt").write_text("wip\n", encoding="utf-8")

    svc.apply_edit(handle, "src/calc.py", DIFF_1)

    commits = CheckpointManager(root=tmp_path / "tasks").list_checkpoints(PROJECT)
    subjects = [c["message"] for c in commits]
    assert any(s.startswith("checkpoint: pre-edit") for s in subjects), subjects
    assert any("post-edit step-1" in s for s in subjects), subjects


def test_clean_tree_reuses_head_for_pre_snapshot(tmp_path):
    """No manufactured empty commits: a clean tree's pre-marker is HEAD itself."""
    svc, _runner = _service(tmp_path)
    handle = svc.run_task("t1b", "org/repo", "agent/x", [], project_id=PROJECT)
    workspace = Path(svc.runner.active_tasks["t1b"]["workspace"])
    _seed_repo(workspace)

    svc.apply_edit(handle, "src/calc.py", DIFF_1)

    mgr = CheckpointManager(root=tmp_path / "tasks")
    items = mgr.list_checkpoints(PROJECT)
    # Exactly one new commit beyond the seed: the post-edit one.
    assert len(items) == 2
    assert sum(1 for i in items if i["message"].startswith("checkpoint:")) == 1


def test_second_batch_increments_step(tmp_path):
    svc, _runner = _service(tmp_path)
    handle = svc.run_task("t2", "org/repo", "agent/x", [], project_id=PROJECT)
    workspace = Path(svc.runner.active_tasks["t2"]["workspace"])
    _seed_repo(workspace)

    sm = CheckpointManager(root=tmp_path / "tasks")
    svc.apply_edit(handle, "src/calc.py", DIFF_1)
    svc.apply_edit(handle, "src/calc.py", "--- a/src/calc.py\n+++ b/src/calc.py\n@@\n+x = 2\n")
    subjects = [c["message"] for c in sm.list_checkpoints(PROJECT)]
    assert any("step-2" in s for s in subjects)


def test_snapshot_failure_never_fails_the_edit(tmp_path):
    """Checkpoint telemetry loss must not break the actual edit."""
    svc, _runner = _service(tmp_path)
    handle = svc.run_task("t3", "org/repo", "agent/x", [], project_id=PROJECT)

    from harness_sandbox.checkpoints import CheckpointError

    class FailingMgr:
        def snapshot(self, task_id, workspace, *, kind, step=None, project_id=None):
            raise CheckpointError("disk gone")

    svc.checkpoints = FailingMgr()  # type: ignore[assignment] — duck-typed on purpose
    svc.apply_edit(handle, "src/new.py", DIFF_1)  # must not raise
    assert (Path(svc.runner.active_tasks["t3"]["workspace"]) / "src" / "new.py").exists()


def test_workspace_outside_root_is_refused(tmp_path):
    """Mandatory path guard: no checkpoints outside the storage root."""
    mgr = CheckpointManager(root=tmp_path / "tasks")
    outside = tmp_path / "elsewhere" / "workspace"
    outside.mkdir(parents=True)
    with pytest.raises(Exception, match="outside storage root"):
        mgr.snapshot("evil", outside, kind="pre-edit", project_id=PROJECT)


# --------------------------------------------------------------------------
# 2. list/diff round-trip
# --------------------------------------------------------------------------


def test_list_and_diff_round_trip_manager(tmp_path):
    svc, _runner = _service(tmp_path)
    handle = svc.run_task("t4", "org/repo", "agent/x", [], project_id=PROJECT)
    workspace = Path(svc.runner.active_tasks["t4"]["workspace"])
    _seed_repo(workspace)

    svc.apply_edit(handle, "src/calc.py", DIFF_1)

    mgr = CheckpointManager(root=tmp_path / "tasks")
    items = mgr.list_checkpoints(PROJECT)
    post = next(i for i in items if i["message"].startswith("checkpoint: post-edit"))
    assert post["taskId"] == "t4"

    diff_text = mgr.get_diff(PROJECT, post["sha"])
    assert diff_text.startswith("diff --git")
    assert "+x = 1" in diff_text


def test_rest_round_trip_list_and_diff(tmp_path):
    svc, _runner = _service(tmp_path)
    handle = svc.run_task("t5", "org/repo", "agent/x", [], project_id=PROJECT)
    workspace = Path(svc.runner.active_tasks["t5"]["workspace"])
    _seed_repo(workspace)
    svc.apply_edit(handle, "src/calc.py", DIFF_1)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: type(
        "P", (), {"userId": "u1", "teamId": "t1", "role": "admin"}
    )()
    # Point the API's service at our tmp-rooted instance.
    from harness_sandbox import api as api_mod

    api_mod.configure_sandbox(svc)
    client = TestClient(app)
    try:
        res = client.get(f"/api/v1/sandbox/{PROJECT}/checkpoints")
        assert res.status_code == 200
        body = res.json()
        assert body["projectId"] == PROJECT
        shas = [i["sha"] for i in body["items"]]
        # seed + post-edit (clean-tree pre reuses the seed as HEAD marker)
        assert len(shas) >= 2

        post_sha = next(
            i["sha"] for i in body["items"] if i["message"].startswith("checkpoint: post-edit")
        )
        res = client.get(f"/api/v1/sandbox/{PROJECT}/checkpoints/{post_sha}/diff")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/plain")
        assert "+x = 1" in res.text
    finally:
        api_mod.configure_sandbox(SandboxService())  # restore module default


# --------------------------------------------------------------------------
# 3. sha traversal guard
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "../../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "main:secret.txt",
        "--upload-pack=evil",
        "-oProxyCommand=evil",
        "HEAD;rm -rf /",
        "deadbeef extra",
        "",
    ],
)
def test_sha_guard_rejects_traversal_and_options(bad):
    with pytest.raises(InvalidShaError):
        validate_sha(bad)


def test_sha_guard_api_answers_400(tmp_path):
    """A malformed sha answers 400 BEFORE any repository lookup happens."""
    mgr = CheckpointManager(root=tmp_path / "tasks")
    (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
    with pytest.raises(InvalidShaError):
        mgr.get_diff("no-such-project", "../../escape")


# --------------------------------------------------------------------------
# 4. honest envelope error when nothing exists
# --------------------------------------------------------------------------


def test_no_repo_returns_honest_envelope_error(tmp_path):
    CheckpointManager(root=tmp_path / "empty-root")  # ensure dir exists but is bare
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: type(
        "P", (), {"userId": "u1", "teamId": "t1", "role": "admin"}
    )()
    from harness_sandbox import api as api_mod

    empty_svc = SandboxService(
        runner=LocalSandboxRunner(storage_root=tmp_path / "empty-tasks"),
        checkpoints=CheckpointManager(root=tmp_path / "empty-tasks"),
    )
    api_mod.configure_sandbox(empty_svc)
    client = TestClient(app)
    try:
        res = client.get("/api/v1/sandbox/nope/checkpoints")
        assert res.status_code == 404
        detail = res.json()["detail"]
        code = detail["error"]["code"] if isinstance(detail, dict) else None
        assert code == "CHECKPOINT_REPOSITORY_NOT_FOUND"

        res = client.get("/api/v1/sandbox/nope/checkpoints/deadbeef1234/diff")
        assert res.status_code == 404
    finally:
        api_mod.configure_sandbox(SandboxService())
