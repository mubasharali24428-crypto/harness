"""M12-G9: human-edit checkpoint emit hook (first slice).

The orchestrator marks every accepted human-edit batch with a DIFF_PROGRESS
carrying a ``checkpoint`` descriptor so the S7 panel can correlate the pause
with snapshot pairs. The git snapshots themselves live in S5's sandbox edit
flow; this file guards the orchestrator-side seam only.
"""

from __future__ import annotations

import pytest
from harness_contracts.events import EventType

from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.state_machine import AgentState

RUN = "run-ckpt"


async def _approved_executing_run(svc: OrchestratorService, run_id: str) -> None:
    plan_id = await svc.propose_plan(run_id, goal="ship checkpoints")
    await svc.approve_plan(run_id, plan_id, principal_user_id="u1")
    await svc.start_execution(run_id)


@pytest.mark.asyncio
async def test_human_edit_emits_checkpoint_marker():
    """Every accepted human-edit batch emits one checkpoint-marked event."""
    events: list[tuple[EventType, dict]] = []
    svc = OrchestratorService(on_event=lambda etype, payload: events.append((etype, payload)))

    await _approved_executing_run(svc, RUN)
    sm = svc.runs[RUN]["state_machine"]

    await svc.submit_human_edit(RUN, path="src/app.py", content="print('hi')\n")

    marked = [
        payload
        for etype, payload in events
        if etype == EventType.DIFF_PROGRESS and isinstance(payload.get("checkpoint"), dict)
    ]
    assert marked, "human edit must emit a checkpoint-marked DIFF_PROGRESS event"
    assert marked[0]["runId"] == RUN
    assert marked[0]["path"] == "src/app.py"
    assert marked[0]["checkpoint"]["kind"] == "human-edit"

    # The legacy semantics are untouched: the run pauses, resumable.
    assert sm.state is AgentState.PAUSED
    assert sm.is_human_editing is True


@pytest.mark.asyncio
async def test_human_edit_emits_one_marker_per_batch():
    """Two sequential edit batches ⇒ two markers."""
    events: list[tuple[EventType, dict]] = []
    svc = OrchestratorService(on_event=lambda etype, payload: events.append((etype, payload)))

    await _approved_executing_run(svc, RUN)
    sm = svc.runs[RUN]["state_machine"]

    await svc.submit_human_edit(RUN, path="a.txt", content="1\n")
    sm.resume()  # allow the next legal batch

    await svc.submit_human_edit(RUN, path="b.txt", content="2\n")

    marked = [
        p
        for e, p in events
        if e == EventType.DIFF_PROGRESS and isinstance(p.get("checkpoint"), dict)
    ]
    assert len(marked) == 2
