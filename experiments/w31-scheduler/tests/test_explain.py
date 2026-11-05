"""W31 — deletion-based explanation returns an irreducible conflict."""

from __future__ import annotations

from w31 import explain


def request(tasks, resources, horizon=10):
    return {"slot_minutes": 30, "horizon_slots": horizon, "tasks": tasks, "resources": resources}


def test_capability_conflict_reduces_to_the_impossible_task():
    payload = request(
        [
            {"id": "needs-gpu", "duration_slots": 2, "requires": ["gpu"]},
            {"id": "fine", "duration_slots": 1, "requires": ["cpu"]},
        ],
        [{"id": "r1", "capabilities": ["cpu"]}],
    )
    result = explain.explain_infeasible(payload)
    assert result["kind"] == "minimal_infeasible_subset"
    assert result["task_ids"] == ["needs-gpu"]
    assert result["minimal"] is True
    assert "capabilities" in result["reason"]


def test_deadline_capacity_conflict_keeps_both_tasks():
    payload = request(
        [
            {"id": "a", "duration_slots": 2, "deadline_slot": 3},
            {"id": "b", "duration_slots": 2, "deadline_slot": 3},
        ],
        [{"id": "r1"}],
    )
    result = explain.explain_infeasible(payload)
    assert result["kind"] == "minimal_infeasible_subset"
    assert result["task_ids"] == ["a", "b"]
    assert result["minimal"] is True


def test_feasible_request_is_not_explained_as_infeasible():
    payload = request([{"id": "a", "duration_slots": 1}], [{"id": "r1"}])
    result = explain.explain_infeasible(payload)
    assert result["kind"] == "not_infeasible"
    assert result["task_ids"] == []


def test_large_request_is_capped_instead_of_slow():
    payload = request(
        [
            {"id": f"t{index}", "duration_slots": 2, "deadline_slot": 1}
            for index in range(3)
        ],
        [{"id": "r1"}],
    )
    result = explain.explain_infeasible(payload, max_tasks=2)
    assert result["kind"] == "too_large"
    assert result["minimal"] is False


def test_render_is_plain_text():
    payload = request([{"id": "a", "duration_slots": 2, "requires": ["gpu"]}], [{"id": "r1"}])
    lines = explain.render(explain.explain_infeasible(payload))
    assert "a" in lines[0]
    assert len(lines) == 2
