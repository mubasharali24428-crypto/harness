"""C16: hunk-level checkpoint review — parse, revert a subset, restore, API.

Every test drives real git in a throwaway workspace; the API tests exercise
the same wire contract the Changes tab uses (writer role + team ownership).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_sandbox import api as api_mod
from harness_sandbox.api import get_principal, router
from harness_sandbox.checkpoints import (
    CheckpointConflictError,
    CheckpointManager,
    CheckpointSelectionError,
)
from harness_sandbox.hunk_review import (
    HunkParseError,
    build_reverse_patch,
    parse_unified_diff,
)
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService

PROJECT = "proj-hunks"
TWO_HUNK_DIFF = """diff --git a/src/calc.py b/src/calc.py
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,5 +1,5 @@
 line 0
 line 1
-old 2
+CHANGE-A
 line 3
 line 4
@@ -24,5 +24,5 @@
 line 24
 line 25
 line 26
-old 27
+CHANGE-B
 line 28
"""


def _lines() -> list[str]:
    return [f"line {i}" if i not in (2, 27) else f"old {i}" for i in range(30)]


def _repo(tmp_path: Path) -> tuple[CheckpointManager, Path, str]:
    """A task workspace with one committed file; returns (manager, workspace, base sha)."""
    root = tmp_path / "tasks"
    workspace = root / "t1" / "workspace"
    workspace.mkdir(parents=True)
    env = {
        "GIT_AUTHOR_NAME": "seed",
        "GIT_AUTHOR_EMAIL": "s@x",
        "GIT_COMMITTER_NAME": "seed",
        "GIT_COMMITTER_EMAIL": "s@x",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(workspace),
    }

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True, env=env)

    run("init", "--initial-branch=main")
    (workspace / "src").mkdir()
    (workspace / "src" / "calc.py").write_text("\n".join(_lines()) + "\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "seed")

    mgr = CheckpointManager(root=root)
    base = mgr.snapshot("t1", workspace, kind="post-edit", step=1, project_id=PROJECT)
    assert base
    return mgr, workspace, base


def _apply_two_hunk_commit(mgr: CheckpointManager, workspace: Path) -> str:
    text = "\n".join(_lines()).replace("old 2", "CHANGE-A").replace("old 27", "CHANGE-B")
    (workspace / "src" / "calc.py").write_text(text + "\n", encoding="utf-8")
    sha = mgr.snapshot("t1", workspace, kind="post-edit", step=2, project_id=PROJECT)
    assert sha
    return sha


# ---------------------------------------------------------------- parsing


def test_parse_splits_files_and_hunks():
    files = parse_unified_diff(TWO_HUNK_DIFF)
    assert [f.path for f in files] == ["src/calc.py"]
    assert [h.index for h in files[0].hunks] == [0, 1]
    assert files[0].hunks[1].header.startswith("@@ -24,5")
    assert "+CHANGE-B" in files[0].hunks[1].text()


def test_build_patch_keeps_only_selected_hunks():
    files = parse_unified_diff(TWO_HUNK_DIFF)
    patch, count = build_reverse_patch(files, [("src/calc.py", 1)])
    assert count == 1
    assert "CHANGE-B" in patch and "CHANGE-A" not in patch


def test_unknown_selection_fails_loudly():
    files = parse_unified_diff(TWO_HUNK_DIFF)
    with pytest.raises(HunkParseError):
        build_reverse_patch(files, [("src/other.py", 0)])
    with pytest.raises(HunkParseError):
        build_reverse_patch(files, [("src/calc.py", 9)])


# ---------------------------------------------------------------- manager


def test_revert_one_hunk_leaves_the_other_change(tmp_path):
    mgr, workspace, _base = _repo(tmp_path)
    sha = _apply_two_hunk_commit(mgr, workspace)

    out = mgr.revert_hunks(PROJECT, sha, [("src/calc.py", 1)])

    text = (workspace / "src" / "calc.py").read_text()
    assert "CHANGE-A" in text  # kept
    assert "CHANGE-B" not in text  # reverted
    assert out["reverted"] == 1
    assert out["sha"] != sha
    assert any(c["sha"] == out["sha"] for c in mgr.list_checkpoints(PROJECT))


def test_restore_returns_the_tree_to_the_snapshot(tmp_path):
    mgr, workspace, base = _repo(tmp_path)
    _apply_two_hunk_commit(mgr, workspace)

    out = mgr.restore(PROJECT, base)

    assert (workspace / "src" / "calc.py").read_text() == "\n".join(_lines()) + "\n"
    assert out["restoredTo"] == base
    assert any(c["sha"] == out["sha"] for c in mgr.list_checkpoints(PROJECT))


def test_reverting_twice_is_a_conflict_not_a_silent_noop(tmp_path):
    mgr, workspace, _base = _repo(tmp_path)
    sha = _apply_two_hunk_commit(mgr, workspace)
    mgr.revert_hunks(PROJECT, sha, [("src/calc.py", 1)])

    with pytest.raises(CheckpointConflictError):
        mgr.revert_hunks(PROJECT, sha, [("src/calc.py", 1)])


def test_empty_selection_is_refused(tmp_path):
    mgr, _workspace, _base = _repo(tmp_path)
    with pytest.raises(CheckpointSelectionError):
        mgr.revert_hunks(PROJECT, _base, [])


# ---------------------------------------------------------------- API


def _client(mgr: CheckpointManager, role: str = "admin", team: str = "t1"):
    svc = SandboxService(runner=LocalSandboxRunner(storage_root=mgr.root), checkpoints=mgr)
    api_mod.configure_sandbox(svc)
    app = FastAPI()
    app.include_router(router)
    app.state.project_team_lookup = {"proj-hunks": "t1"}.get
    app.dependency_overrides[get_principal] = lambda: type(
        "P", (), {"userId": "u1", "teamId": team, "role": role}
    )()
    return TestClient(app)


def test_api_lists_hunks(tmp_path):
    mgr, workspace, _base = _repo(tmp_path)
    sha = _apply_two_hunk_commit(mgr, workspace)
    client = _client(mgr)
    try:
        res = client.get(f"/api/v1/sandbox/{PROJECT}/checkpoints/{sha}/hunks")
        assert res.status_code == 200, res.text
        files = res.json()["files"]
        assert files[0]["path"] == "src/calc.py"
        assert [h["index"] for h in files[0]["hunks"]] == [0, 1]
        assert files[0]["hunks"][0]["additions"] == 1
    finally:
        api_mod.configure_sandbox(SandboxService())


def test_api_revert_requires_writer_and_team(tmp_path):
    mgr, workspace, _base = _repo(tmp_path)
    sha = _apply_two_hunk_commit(mgr, workspace)
    body = {"hunks": [{"path": "src/calc.py", "index": 1}]}
    try:
        viewer = _client(mgr, role="viewer")
        res = viewer.post(f"/api/v1/sandbox/{PROJECT}/checkpoints/{sha}/revert", json=body)
        assert res.status_code == 403, res.text

        foreign = _client(mgr, team="t2")
        res = foreign.post(f"/api/v1/sandbox/{PROJECT}/checkpoints/{sha}/revert", json=body)
        assert res.status_code == 404, res.text

        admin = _client(mgr)
        res = admin.post(f"/api/v1/sandbox/{PROJECT}/checkpoints/{sha}/revert", json=body)
        assert res.status_code == 200, res.text
        assert res.json()["reverted"] == 1
        assert "CHANGE-B" not in (workspace / "src" / "calc.py").read_text()
    finally:
        api_mod.configure_sandbox(SandboxService())


def test_api_empty_selection_is_422_and_conflict_is_409(tmp_path):
    mgr, workspace, _base = _repo(tmp_path)
    sha = _apply_two_hunk_commit(mgr, workspace)
    client = _client(mgr)
    try:
        res = client.post(f"/api/v1/sandbox/{PROJECT}/checkpoints/{sha}/revert", json={"hunks": []})
        assert res.status_code == 422, res.text

        body = {"hunks": [{"path": "src/calc.py", "index": 0}]}
        first = client.post(f"/api/v1/sandbox/{PROJECT}/checkpoints/{sha}/revert", json=body)
        assert first.status_code == 200, first.text
        res = client.post(f"/api/v1/sandbox/{PROJECT}/checkpoints/{sha}/revert", json=body)
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["error"]["code"] == "CHECKPOINT_CONFLICT"
    finally:
        api_mod.configure_sandbox(SandboxService())


def test_api_restore_records_a_new_snapshot(tmp_path):
    mgr, workspace, base = _repo(tmp_path)
    _apply_two_hunk_commit(mgr, workspace)
    client = _client(mgr)
    try:
        res = client.post(f"/api/v1/sandbox/{PROJECT}/checkpoints/{base}/restore")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "restored"
        assert (workspace / "src" / "calc.py").read_text() == "\n".join(_lines()) + "\n"
    finally:
        api_mod.configure_sandbox(SandboxService())
