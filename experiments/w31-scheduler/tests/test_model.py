"""W31 — fresh model: feasible, infeasible, dependencies, capacity, timeout."""

from __future__ import annotations

import pytest

from w31 import contract, model


def request(tasks, resources, horizon=12):
    return {"slot_minutes": 30, "horizon_slots": horizon, "tasks": tasks, "resources": resources}


def task(task_id, duration, **extra):
    return {"id": task_id, "duration_slots": duration, **extra}


def test_feasible_solution_validates():
    payload = request(
        [task("a", 2, requires=["cpu"]), task("b", 1, depends_on=["a"]), task("c", 2)],
        [{"id": "r1", "capabilities": ["cpu"]}, {"id": "r2"}],
    )
    result = model.solve(payload, time_limit_s=5)
    assert result["status"] in {"optimal", "feasible"}
    assert result["encoding"] == "time-indexed"
    assert contract.validate(payload, result["assignments"]) == []
    assert result["objective"] == contract.objective(payload, result["assignments"])


def test_dependency_is_ordered():
    payload = request(
        [task("late", 2, depends_on=["early"]), task("early", 3)],
        [{"id": "r1"}],
    )
    result = model.solve(payload, time_limit_s=5)
    placed = {assignment["task_id"]: assignment for assignment in result["assignments"]}
    assert placed["late"]["start_slot"] >= placed["early"]["end_slot"]
    assert contract.validate(payload, result["assignments"]) == []


def test_capacity_two_allows_concurrency():
    payload = request(
        [task("a", 2), task("b", 2)],
        [{"id": "r1", "capacity": 2}],
        horizon=2,
    )
    result = model.solve(payload, time_limit_s=5)
    assert result["objective"]["makespan_slots"] == 2
    assert {assignment["start_slot"] for assignment in result["assignments"]} == {0}
    assert contract.validate(payload, result["assignments"]) == []


def test_capability_mismatch_is_infeasible():
    payload = request([task("train", 2, requires=["gpu"])], [{"id": "r1", "capabilities": ["cpu"]}])
    result = model.solve(payload, time_limit_s=5)
    assert result["status"] == "infeasible"
    assert result["assignments"] == []
    assert "capabilities" in result["reason"]


def test_deadline_capacity_conflict_is_infeasible():
    payload = request(
        [task("a", 2, deadline_slot=3), task("b", 2, deadline_slot=3)],
        [{"id": "r1"}],
        horizon=10,
    )
    result = model.solve(payload, time_limit_s=5)
    assert result["status"] == "infeasible"
    assert result["assignments"] == []


def test_availability_window_is_honoured():
    payload = request(
        [task("a", 2)],
        [{"id": "r1", "availability": [{"start_slot": 3, "end_slot": 6}]}],
    )
    result = model.solve(payload, time_limit_s=5)
    assert result["assignments"][0]["start_slot"] in {3, 4}
    assert contract.validate(payload, result["assignments"]) == []


def _hard_request():
    tasks = [
        task(f"t{index:03d}", 2 + index % 4, depends_on=[f"t{index - 1:03d}"] if index % 3 else [])
        for index in range(40)
    ]
    return request(tasks, [{"id": f"r{index}"} for index in range(4)], horizon=200)


def test_time_limit_never_claims_more_than_it_knows():
    result = model.solve(_hard_request(), time_limit_s=0.01)
    assert result["status"] in {"unknown", "feasible"}
    if result["status"] == "unknown":
        assert result["assignments"] == []
        assert result["reason"]
    assert result["elapsed_ms"] < 10_000


def test_feasibility_mode_skips_the_objective():
    payload = request([task("a", 2), task("b", 2)], [{"id": "r1"}], horizon=6)
    result = model.solve(payload, time_limit_s=5, optimize=False)
    assert result["optimized"] is False
    assert contract.validate(payload, result["assignments"]) == []


def test_determinism_repeats_exactly():
    payload = request(
        [task("a", 2, priority=1), task("b", 1, depends_on=["a"]), task("c", 3)],
        [{"id": "r1"}, {"id": "r2", "capacity": 2}],
    )
    first = model.solve(payload, time_limit_s=5)
    second = model.solve(payload, time_limit_s=5)
    assert first["assignments"] == second["assignments"]
    assert first["objective"] == second["objective"]


def test_contract_drift_raises():
    payload = request([task("a", 1)], [{"id": "r1"}])
    payload["unknown"] = True
    with pytest.raises(contract.RequestError):
        model.solve(payload)
