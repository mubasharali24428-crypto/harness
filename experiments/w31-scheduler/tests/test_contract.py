"""W31 — the independent validator must catch every hard constraint."""

from __future__ import annotations

import pytest

from w31 import contract


def base_request():
    return {
        "slot_minutes": 30,
        "horizon_slots": 12,
        "tasks": [
            {"id": "a", "duration_slots": 2, "priority": 0},
            {"id": "b", "duration_slots": 1, "depends_on": ["a"]},
        ],
        "resources": [{"id": "r1", "capacity": 1}],
    }


def valid_assignments():
    return [
        {"task_id": "a", "resource_id": "r1", "start_slot": 0, "end_slot": 2},
        {"task_id": "b", "resource_id": "r1", "start_slot": 2, "end_slot": 3},
    ]


def codes(request, assignments):
    return [violation["code"] for violation in contract.validate(request, assignments)]


def test_complete_assignment_has_no_violations():
    assert contract.validate(base_request(), valid_assignments()) == []


def test_missing_task_is_reported():
    assert codes(base_request(), valid_assignments()[:1]) == ["unassigned"]


def test_duplicate_task_is_reported():
    assignments = valid_assignments() + [valid_assignments()[0]]
    assert "duplicate_task" in codes(base_request(), assignments)


def test_wrong_duration_and_bounds_are_reported():
    assignments = valid_assignments()
    assignments[0] = {"task_id": "a", "resource_id": "r1", "start_slot": 10, "end_slot": 11}
    assert "bad_window" in codes(base_request(), assignments)


def test_capability_is_reported():
    request = base_request()
    request["tasks"][0]["requires"] = ["gpu"]
    assert "capability" in codes(request, valid_assignments())


def test_availability_is_reported():
    request = base_request()
    request["resources"][0]["availability"] = [{"start_slot": 4, "end_slot": 6}]
    assert "availability" in codes(request, valid_assignments())


def test_dependency_violation_is_reported():
    assignments = valid_assignments()
    assignments[1]["start_slot"], assignments[1]["end_slot"] = 0, 1
    assert "dependency" in codes(base_request(), assignments)


def test_deadline_violation_is_reported():
    request = base_request()
    request["tasks"][0]["deadline_slot"] = 1
    assert "deadline" in codes(request, valid_assignments())


def test_capacity_violation_is_reported():
    request = base_request()
    request["tasks"] = [{"id": "a", "duration_slots": 1}, {"id": "b", "duration_slots": 1}]
    assignments = [
        {"task_id": "a", "resource_id": "r1", "start_slot": 0, "end_slot": 1},
        {"task_id": "b", "resource_id": "r1", "start_slot": 0, "end_slot": 1},
    ]
    assert "capacity" in codes(request, assignments)


def test_unknown_ids_and_non_dicts_are_reported():
    assignments = [
        {"task_id": "ghost", "resource_id": "r1", "start_slot": 0, "end_slot": 1},
        {"task_id": "a", "resource_id": "ghost", "start_slot": 0, "end_slot": 2},
        "not a dict",
    ]
    found = codes(base_request(), assignments)
    assert "unknown_task" in found
    assert "unknown_resource" in found
    assert "bad_assignment" in found


def test_empty_availability_means_open_horizon():
    payload = contract.load(
        {"horizon_slots": 7, "tasks": [{"id": "a", "duration_slots": 1}], "resources": [{"id": "r1"}]}
    )
    assert payload["resources"][0]["availability"] == [{"start_slot": 0, "end_slot": 7}]


def test_cycle_is_contract_drift():
    request = {
        "horizon_slots": 7,
        "tasks": [
            {"id": "a", "duration_slots": 1, "depends_on": ["b"]},
            {"id": "b", "duration_slots": 1, "depends_on": ["a"]},
        ],
        "resources": [{"id": "r1"}],
    }
    with pytest.raises(contract.RequestError) as caught:
        contract.load(request)
    assert "dependency cycle" in str(caught.value)


def test_unknown_dependency_is_contract_drift():
    request = {
        "horizon_slots": 7,
        "tasks": [{"id": "a", "duration_slots": 1, "depends_on": ["ghost"]}],
        "resources": [{"id": "r1"}],
    }
    with pytest.raises(contract.RequestError):
        contract.load(request)


def test_objective_is_computed_from_the_assignment():
    result = contract.objective(base_request(), valid_assignments())
    assert result == {"makespan_slots": 3, "weighted_completion": 2 + 3}
