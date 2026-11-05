"""W31 — the timetable bridge states what it can and cannot represent."""

from __future__ import annotations

import pytest

from w31 import bridge, contract, fixtures


def test_expressible_workload_translates_cleanly():
    request = fixtures.timetable_workload(10)
    translation = bridge.translate(request)
    assert translation["ok"] is True
    assert translation["unsupported"] == []
    assert len(translation["request"]["sessions"]) == 10
    assert len(translation["request"]["rooms"]) == 12
    windows = translation["request"]["rooms"][0]["windows"]
    assert [(window["days"], window["start"], window["end"]) for window in windows] == [
        ([0], "08:00", "18:00"),
        ([1], "08:00", "18:00"),
        ([2], "08:00", "18:00"),
        ([3], "08:00", "18:00"),
        ([4], "08:00", "18:00"),
    ]


def test_unrepresentable_features_are_listed_not_dropped():
    request = {
        "slot_minutes": 30,
        "horizon_slots": 120,
        "tasks": [
            {
                "id": "short",
                "duration_slots": 1,
                "depends_on": ["other"],
                "deadline_slot": 8,
                "priority": 2,
            },
            {"id": "other", "duration_slots": 2},
        ],
        "resources": [{"id": "r1"}],
    }
    translation = bridge.translate(request)
    assert translation["ok"] is False
    features = {item["feature"] for item in translation["unsupported"]}
    assert features == {"horizon", "duration", "dependency", "deadline", "priority"}


def test_capacity_is_emulated_by_duplicating_the_room():
    request = {
        "slot_minutes": 30,
        "horizon_slots": 10,
        "tasks": [{"id": "a", "duration_slots": 2}],
        "resources": [{"id": "r1", "capacity": 2}],
    }
    translation = bridge.translate(request)
    assert translation["ok"] is True
    assert translation["room_to_resource"] == {"r1#0": "r1", "r1#1": "r1"}


def test_oversized_capacity_is_reported_instead_of_expanded():
    request = {
        "slot_minutes": 30,
        "horizon_slots": 10,
        "tasks": [{"id": "a", "duration_slots": 2}],
        "resources": [{"id": "r1", "capacity": 33}],
    }
    translation = bridge.translate(request)
    assert translation["ok"] is False
    assert translation["unsupported"][0]["feature"] == "capacity"


def test_partial_window_splits_at_day_boundaries():
    request = {
        "slot_minutes": 30,
        "horizon_slots": 40,
        "tasks": [{"id": "a", "duration_slots": 2}],
        "resources": [{"id": "r1", "availability": [{"start_slot": 10, "end_slot": 30}]}],
    }
    windows = bridge.translate(request)["request"]["rooms"][0]["windows"]
    assert [(window["days"], window["start"], window["end"]) for window in windows] == [
        ([0], "13:00", "18:00"),
        ([1], "08:00", "13:00"),
    ]


def test_slot_mapping_is_day_major():
    assignment = bridge._assignment_from_placement(
        {"day": 1, "start": "08:00", "end": "09:00", "session_id": "a", "room_id": "r1#0"},
        {"r1#0": "r1"},
    )
    assert assignment == {"task_id": "a", "resource_id": "r1", "start_slot": 20, "end_slot": 22}


def test_not_expressible_short_circuits_before_solving():
    request = {
        "slot_minutes": 30,
        "horizon_slots": 10,
        "tasks": [{"id": "a", "duration_slots": 2, "depends_on": []}],
        "resources": [{"id": "r1"}],
    }
    request["tasks"][0]["deadline_slot"] = 5
    result = bridge.run(request)
    assert result["status"] == "not_expressible"
    assert result["assignments"] == []


def test_missing_scheduler_is_reported_not_hidden(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHEDULER_ROOT", str(tmp_path))
    request = fixtures.timetable_workload(2)
    result = bridge.run(request)
    assert result["status"] == "unavailable"


requires_scheduler = pytest.mark.skipif(
    bridge.load_scheduler() is None, reason="Scheduler repository not present"
)


@requires_scheduler
def test_scheduler_solves_the_translated_workload():
    request = fixtures.timetable_workload(10)
    result = bridge.run(request)
    assert result["status"] == "solved", result.get("reason")
    assert len(result["assignments"]) == 10
    assert contract.validate(request, result["assignments"]) == []
    assert result["scheduler_conflicts"] == []


@requires_scheduler
def test_capacity_two_is_valid_after_mapping_back():
    request = {
        "slot_minutes": 30,
        "horizon_slots": 20,
        "tasks": [
            {"id": "a", "duration_slots": 2},
            {"id": "b", "duration_slots": 2},
        ],
        "resources": [{"id": "r1", "capacity": 2}],
    }
    result = bridge.run(request)
    assert result["status"] == "solved", result.get("reason")
    assert len(result["assignments"]) == 2
    assert contract.validate(request, result["assignments"]) == []
