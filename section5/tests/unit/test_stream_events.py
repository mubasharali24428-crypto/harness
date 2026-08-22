"""M2: sandbox streams model-visible work as contract events (terminal_output /
diff_progress). dsh rule: anything the model/human should see becomes an event."""

import pytest
from harness_contracts.events import EventType

from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService


def _svc(events, tmp_path):
    return SandboxService(
        runner=LocalSandboxRunner(storage_root=tmp_path / "tasks"),
        on_stream=lambda etype, payload: events.append((etype, payload)),
    )


@pytest.mark.asyncio
async def test_exec_emits_terminal_output_per_line(tmp_path):
    events = []
    svc = _svc(events, tmp_path)
    handle = svc.run_task("t-stream", "org/repo", "agent/x", [], project_id="proj-s")

    collected = []
    async for chunk in svc.exec(handle, "echo hello"):
        collected.append(chunk)

    assert "hello" in "".join(collected)
    terminal = [e for e in events if e[0] == EventType.TERMINAL_OUTPUT]
    assert terminal, "exec must stream terminal_output events"
    payload = terminal[-1][1]
    assert payload["taskId"] == "t-stream"
    assert payload["projectId"] == "proj-s"
    assert payload["line"].strip() == "hello"


@pytest.mark.asyncio
async def test_exec_structured_emits_summary_event(tmp_path):
    events = []
    svc = _svc(events, tmp_path)
    handle = svc.run_task("t-struct", "org/repo", "agent/x", [], project_id="proj-s")

    await svc.exec_structured(handle, "python -c 'print(1)'")
    terminal = [e for e in events if e[0] == EventType.TERMINAL_OUTPUT]
    assert terminal, "structured exec still publishes a terminal_output snapshot"


def test_apply_edit_emits_diff_progress(tmp_path):
    events = []
    svc = _svc(events, tmp_path)
    handle = svc.run_task("t-edit", "org/repo", "agent/x", [], project_id="proj-s")
    svc.apply_edit(handle, "src/new.py", "--- a/src/new.py\n+++ b/src/new.py\n@@\n+x\n")
    progress = [e for e in events if e[0] == EventType.DIFF_PROGRESS]
    assert progress, "apply_edit must publish diff_progress"
    payload = progress[0][1]
    assert payload["path"] == "src/new.py"
    assert payload["projectId"] == "proj-s"


def test_no_callback_is_fine(tmp_path):
    """Back-compat: service works untouched when nobody listens."""
    svc = SandboxService(runner=LocalSandboxRunner(storage_root=tmp_path / "t2"))
    handle = svc.run_task("t3", "o/r", "m", [])
    svc.apply_edit(handle, "a.txt", "--- a/a.txt\n+++ b/a.txt\n@@\n+b\n")
