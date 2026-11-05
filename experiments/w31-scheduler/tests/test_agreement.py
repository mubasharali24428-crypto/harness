"""W31 — the two allocation models must agree on the optimum they can both prove."""

from __future__ import annotations

import pytest

from w31 import bridge, contract, model

requires_scheduler = pytest.mark.skipif(
    bridge.load_scheduler() is None, reason="Scheduler repository not present"
)


@requires_scheduler
def test_scheduler_and_fresh_model_reach_the_same_optimum():
    from solver.allocation import solve_allocation

    request = {
        "slot_minutes": 30,
        "horizon_slots": 12,
        "tasks": [
            {"id": "a", "duration_slots": 2, "requires": ["cpu"], "priority": 1},
            {"id": "b", "duration_slots": 3, "depends_on": ["a"]},
            {"id": "c", "duration_slots": 2, "requires": ["cpu"]},
        ],
        "resources": [
            {"id": "cpu-1", "capabilities": ["cpu"]},
            {"id": "generic-1"},
        ],
    }
    fresh = model.solve(request, time_limit_s=10)
    scheduler = solve_allocation(request, time_limit_s=10)
    assert fresh["status"] == "optimal"
    assert scheduler["status"] == "optimal"
    assert contract.validate(request, fresh["assignments"]) == []
    assert contract.validate(request, scheduler["assignments"]) == []
    assert fresh["objective"]["makespan_slots"] == scheduler["objective"]["makespan_slots"]
    assert (
        fresh["objective"]["weighted_completion"]
        == scheduler["objective"]["weighted_completion"]
    )
