"""S-02 / X-09 — the CI guard for the stable executor prompt prefix.

Provider prompt caches only hit on a byte-identical prefix. These tests fail
if any of the following regresses:

* the prefix is not frozen at the run's first attempt (a mid-run edit of
  rules, skills or memory changes the bytes a resume or guidance turn sends);
* a volatile value — human guidance, ids, clocks, routing, images, budgets, the
  task text itself — reaches the prefix;
* per-attempt data is placed before the prefix instead of after it;
* the prefix reads any run_data key beyond the four advisory sources.

No network, no model, no Docker: a capturing engine records the goal string
the service hands the engine.
"""

from __future__ import annotations

import hashlib

import pytest
from unit.test_goal_image_execution import _started, run

from harness_orchestrator import prompt_prefix
from harness_orchestrator.memory_loader import build_memory_block
from harness_orchestrator.prompt_prefix import (
    PREFIX_SOURCES,
    SNAPSHOT_KEY,
    assemble_prefix,
    frozen_prefix,
    reset_prefix,
)

# Canary strings for values that change between runs or attempts. None may ever
# appear in a prefix. They are deliberately unlike anything a block renders.
VOLATILE_CANARIES = {
    "guidance": "CANARY-GUIDANCE-7f3a",
    "run_id": "CANARY-RUNID-91c2",
    "clock": "2099-01-02T03:04:05.678901+00:00",
    "routing": "CANARY-MODEL-ROUTE-5d1e",
    "images": "CANARY-IMAGE-SUMMARY-0b77",
    "budget": "CANARY-BUDGET-4410",
    "goal": "CANARY-TASK-TEXT-e5e5",
}


def _sources() -> dict:
    return {
        "rules_prompt_block": "PROJECT RULES\nrun ruff before commit",
        "skills_prompt_block": "TEAM SKILLS\n## lint@1\nuse make lint",
        "memory_prompt_block": build_memory_block(
            [
                {
                    "text": "the dialog lives in dialog.tsx",
                    "source": "user",
                    "createdBy": "u1",
                    "reviewedBy": "u2",
                    "createdAt": "2026-11-01 10:00:00+00:00",
                }
            ]
        ),
        "context_bundle": {"rendered": "RETRIEVED CODEBASE CONTEXT\n@@ a.py:1-2 (py)\nx = 1"},
    }


def _with_volatile(base: dict) -> dict:
    """The sources plus every per-run / per-attempt value the service keeps."""
    return {
        **base,
        "goal": VOLATILE_CANARIES["goal"],
        "runId": VOLATILE_CANARIES["run_id"],
        "plan_id": VOLATILE_CANARIES["run_id"],
        "guidance_seq": 42,
        "teamId": VOLATILE_CANARIES["budget"],
        "routing": {"model": VOLATILE_CANARIES["routing"]},
        "goalImages": {"selected": [VOLATILE_CANARIES["images"]]},
        "_goal_images_sent": True,
        "engine_result": {"finalResponse": VOLATILE_CANARIES["guidance"]},
        "startedAt": VOLATILE_CANARIES["clock"],
        "budget": {"remaining": VOLATILE_CANARIES["budget"]},
        "some_future_key": VOLATILE_CANARIES["guidance"],
    }


# --------------------------------------------------------------- pure assembly


def test_order_is_rules_skills_memory_refs():
    snap = assemble_prefix(_sources())
    text = snap.text
    assert (
        text.index("PROJECT RULES")
        < text.index("TEAM SKILLS")
        < text.index("PROJECT MEMORY")
        < text.index("RETRIEVED CODEBASE CONTEXT")
    )
    assert snap.blocks == PREFIX_SOURCES
    assert text.endswith("\n\n")


def test_empty_sources_give_an_empty_prefix():
    snap = assemble_prefix({})
    assert snap.text == ""
    assert snap.bytes == 0
    assert snap.blocks == ()
    assert assemble_prefix({"context_bundle": {"rendered": "  "}}).text == ""


def test_digest_is_sha256_of_the_exact_bytes():
    snap = assemble_prefix(_sources())
    assert snap.sha256 == hashlib.sha256(snap.text.encode("utf-8")).hexdigest()
    assert snap.bytes == len(snap.text.encode("utf-8"))
    assert set(snap.summary()) == {"sha256", "bytes", "blocks"}
    assert snap.text not in str(snap.summary())  # the digest record carries no prompt text


def test_prefix_reads_only_the_four_sources():
    """X-09 core: adding ANY other run_data key cannot change a prefix byte."""
    base = _sources()
    assert assemble_prefix(_with_volatile(base)) == assemble_prefix(base)


def test_no_volatile_canary_reaches_the_prefix():
    text = assemble_prefix(_with_volatile(_sources())).text
    leaked = [name for name, canary in VOLATILE_CANARIES.items() if canary in text]
    assert leaked == [], f"volatile data entered the stable prefix: {leaked}"


def test_same_sources_same_bytes():
    assert assemble_prefix(_sources()).text == assemble_prefix(dict(_sources())).text


# --------------------------------------------------------------- frozen snapshot


def test_snapshot_is_frozen_after_the_first_read():
    run_data = _sources()
    first = frozen_prefix(run_data)
    # mid-run edits to every source must not move the bytes
    run_data["rules_prompt_block"] = "PROJECT RULES\nchanged mid-run"
    run_data["skills_prompt_block"] = ""
    run_data["memory_prompt_block"] = "PROJECT MEMORY (reviewed, advisory)\nnew note"
    run_data["context_bundle"] = {"rendered": "different"}
    again = frozen_prefix(run_data)
    assert again is first
    assert again.text == first.text


def test_reset_starts_a_new_snapshot():
    run_data = _sources()
    first = frozen_prefix(run_data)
    run_data["rules_prompt_block"] = "PROJECT RULES\nnew plan, new rules"
    reset_prefix(run_data)
    assert SNAPSHOT_KEY not in run_data
    assert frozen_prefix(run_data).sha256 != first.sha256


def test_a_foreign_value_under_the_snapshot_key_is_not_trusted():
    run_data = {**_sources(), SNAPSHOT_KEY: "not a snapshot"}
    assert frozen_prefix(run_data).text == assemble_prefix(_sources()).text


# --------------------------------------------------------------- through the service


class CapturingEngine:
    api_key = None

    def __init__(self):
        self.goals: list[str] = []

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        self.goals.append(goal or "")
        return {
            "sessionId": session_id,
            "engine": "capture",
            "status": "completed",
            "finalResponse": "ok",
        }


@pytest.fixture
def guidance(monkeypatch):
    """Per-attempt human guidance, the canonical volatile value."""
    import harness_orchestrator.service as service_module

    state = {"text": ""}

    async def _guidance_since(_factory, _run_id, after):
        return state["text"], after + 1

    monkeypatch.setattr(service_module.collab, "guidance_since", _guidance_since)
    return state


def _attempt(svc, run_data, plan):
    """One more execution attempt of the same run (a resume / guidance turn)."""
    run(svc._execute(run_data, plan))


def test_executor_goal_is_prefix_then_task_then_guidance(tmp_path, guidance):
    engine = CapturingEngine()
    svc, run_data, plan, _f, _r = _started(tmp_path, engine)
    run_data.update(_sources())
    guidance["text"] = "\n\nGUIDANCE: " + VOLATILE_CANARIES["guidance"]
    _attempt(svc, run_data, plan)

    goal = engine.goals[-1]
    snap = frozen_prefix(run_data)
    assert goal.startswith(snap.text), "the stable prefix must be the first bytes sent"
    task_at = goal.index(plan.goal, len(snap.text))
    assert goal.index(VOLATILE_CANARIES["guidance"]) > task_at, "volatile data goes last"
    assert VOLATILE_CANARIES["guidance"] not in snap.text


def test_prefix_bytes_identical_across_attempts_despite_mid_run_edits(tmp_path, guidance):
    """The cache-economics property: attempt 2 sends attempt 1's exact prefix."""
    engine = CapturingEngine()
    svc, run_data, plan, _f, _r = _started(tmp_path, engine)
    run_data.update(_sources())

    guidance["text"] = "\n\nGUIDANCE: first"
    _attempt(svc, run_data, plan)
    first_goal = engine.goals[-1]
    first_result = run_data.get("engine_result") or {}

    # Between attempts: someone edits rules and memory, new guidance arrives.
    run_data["rules_prompt_block"] = "PROJECT RULES\nedited while the run was paused"
    run_data["memory_prompt_block"] = "PROJECT MEMORY (reviewed, advisory)\nnew note"
    guidance["text"] = "\n\nGUIDANCE: second " + VOLATILE_CANARIES["guidance"]
    _attempt(svc, run_data, plan)
    second_goal = engine.goals[-1]

    snap = frozen_prefix(run_data)
    assert first_goal.startswith(snap.text) and second_goal.startswith(snap.text)
    assert "edited while the run was paused" not in second_goal
    assert first_goal != second_goal  # the volatile tail did change
    digest = (first_result.get("promptPrefix") or {}).get("sha256")
    assert digest == snap.sha256


def test_a_new_plan_refreshes_the_snapshot(tmp_path, guidance):
    engine = CapturingEngine()
    svc, run_data, plan, _f, _r = _started(tmp_path, engine)
    run_data.update(_sources())
    _attempt(svc, run_data, plan)
    assert SNAPSHOT_KEY in run_data

    run(svc.propose_plan("r1", goal="a new plan for the same run", project_id="p1"))
    assert SNAPSHOT_KEY not in svc.runs["r1"]


def test_engine_result_carries_the_digest_not_the_text(tmp_path, guidance):
    engine = CapturingEngine()
    svc, run_data, plan, _f, _r = _started(tmp_path, engine)
    run_data.update(_sources())
    _attempt(svc, run_data, plan)
    result = run_data.get("engine_result") or {}
    record = result.get("promptPrefix")
    assert record == frozen_prefix(run_data).summary()
    assert "run ruff before commit" not in str(record)


def test_a_run_without_advisory_context_keeps_its_exact_result_shape(tmp_path, guidance):
    engine = CapturingEngine()
    svc, run_data, plan, _f, _r = _started(tmp_path, engine)
    _attempt(svc, run_data, plan)
    assert engine.goals[-1].strip() == "why does this dialog look wrong"
    assert "promptPrefix" not in (run_data.get("engine_result") or {})


def test_service_uses_the_prompt_prefix_module():
    """The executor seam must go through the frozen snapshot, not a re-render."""
    from harness_orchestrator.service import OrchestratorService

    run_data = _sources()
    first = OrchestratorService._executor_context_prefix(run_data)
    run_data["rules_prompt_block"] = "PROJECT RULES\nchanged"
    assert OrchestratorService._executor_context_prefix(run_data) == first
    assert prompt_prefix.SNAPSHOT_KEY in run_data
