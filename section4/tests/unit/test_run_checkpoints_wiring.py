"""W3 — checkpoints at the service seam.

`test_run_checkpoints.py` pins the store. This pins the plumbing it cannot see:
that a real execution attempt leaves a before/after snapshot pair in the run's
workspace, and that a rewind is refused while the run is still live — resetting
files under a worker that is writing them would race the agent and could restore
a tree the agent never saw.

The engine double carries `run_workspace` because that is the capability the
service probes: a stub without it must simply get no checkpoints, not a crash.
"""

import asyncio
from pathlib import Path

import pytest

from harness_orchestrator import run_checkpoints as cp
from harness_orchestrator.state_machine import InvalidStateTransitionError
from unit.test_goal_image_execution import _started, run


class WorkspaceEngine:
    """A stub engine that also names a workspace, the way the real wrapper does."""

    api_key = None

    def __init__(self, root: Path, status: str = "completed"):
        self.root = root
        self.status = status
        self.calls = 0

    def run_workspace(self, session_id: str) -> str:
        path = self.root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        self.calls += 1
        workspace = Path(self.run_workspace(session_id))
        # Write like a real attempt would: the "after" snapshot must see this.
        (workspace / "calc.py").write_text(f"attempt {self.calls}\n")
        return {
            "sessionId": session_id,
            "engine": "stub",
            "status": self.status,
            "finalResponse": "done",
            "toolCalls": [],
        }


def _execute(tmp_path, engine, monkeypatch):
    import harness_orchestrator.service as service_module

    svc, run_data, plan, _factory, fake_read = _started(tmp_path, engine)

    async def resolve(factory_, run_id, team_id, model=None):
        return await fake_read(factory_, run_id, team_id, model)

    monkeypatch.setattr(service_module, "resolve_goal_images", resolve)
    run_data["state_machine"].start_execution()
    run(svc._execute(run_data, plan))
    return svc, run_data


def test_an_attempt_leaves_a_before_and_after_checkpoint(tmp_path, monkeypatch):
    engine = WorkspaceEngine(tmp_path / "ws")
    _svc, run_data = _execute(tmp_path, engine, monkeypatch)

    workspace = engine.run_workspace(run_data["runId"])
    labels = [c["label"] for c in cp.list_snapshots(workspace, run_data["runId"])]

    assert labels == ["before-attempt-1", "after-attempt-1"]


def test_the_before_checkpoint_does_not_contain_the_attempt_s_write(tmp_path, monkeypatch):
    """If "before" were taken after the engine ran, undo would be a no-op."""
    engine = WorkspaceEngine(tmp_path / "ws")
    _svc, run_data = _execute(tmp_path, engine, monkeypatch)

    workspace = engine.run_workspace(run_data["runId"])
    before = cp.list_snapshots(workspace, run_data["runId"])[0]

    assert before["files"] == 0  # the workspace was empty when the attempt started


def test_rewind_restores_the_tree_the_attempt_changed(tmp_path, monkeypatch):
    engine = WorkspaceEngine(tmp_path / "ws")
    svc, run_data = _execute(tmp_path, engine, monkeypatch)
    workspace = engine.run_workspace(run_data["runId"])

    out = run(svc.rewind_run(run_data["runId"], None))

    assert out["rewoundTo"].endswith("after-attempt-1")
    assert (Path(workspace) / "calc.py").read_text() == "attempt 1\n"


def test_listing_checkpoints_for_an_engine_without_a_workspace_is_empty(tmp_path):
    class NoWorkspace:
        api_key = None

    svc, _run_data, _plan, _f, _read = _started(tmp_path, NoWorkspace())
    assert run(svc.list_run_checkpoints("r1")) == {"runId": "r1", "items": []}


def test_rewind_is_refused_while_the_run_is_live(tmp_path):
    engine = WorkspaceEngine(tmp_path / "ws")
    svc, run_data, _plan, _f, _read = _started(tmp_path, engine)

    async def go():
        # A task that is not done is exactly the live case: a worker may still be
        # writing the files a rewind would reset.
        run_data["task"] = asyncio.create_task(asyncio.sleep(30))
        try:
            with pytest.raises(InvalidStateTransitionError):
                await svc.rewind_run("r1", None)
        finally:
            run_data["task"].cancel()
            with pytest.raises(asyncio.CancelledError):
                await run_data["task"]

    run(go())
