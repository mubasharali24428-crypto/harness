"""W31 experiment — deterministic synthetic workloads.

Two families:

* `allocation_workload` exercises the full neutral contract: arbitrary 1-4 slot
  durations, capability tags, dependencies, priorities and capacities.
* `timetable_workload` is the subset the Scheduler's timetable contract can
  represent: 60-180 minute sessions, no dependencies, no deadlines, priority 0.

Both are generated from a fixed seed, so a rerun compares the same work.
"""

from __future__ import annotations

import random

CAPABILITIES = ("cpu", "gpu", "io")

ALLOCATION_RESOURCES = (
    ("cpu-1", 1, ("cpu",)),
    ("cpu-2", 1, ("cpu",)),
    ("cpu-3", 1, ("cpu",)),
    ("gpu-1", 1, ("gpu",)),
    ("gpu-2", 1, ("gpu",)),
    ("io-1", 1, ("io",)),
    ("io-2", 1, ("io",)),
    ("mixed-1", 1, ("cpu", "gpu", "io")),
)


def allocation_workload(size: int, horizon: int = 100) -> dict:
    """A feasible-by-construction workload with deps and priorities."""
    rng = random.Random(20261104 + size)
    resources = [
        {"id": resource_id, "capacity": capacity, "capabilities": list(capabilities)}
        for resource_id, capacity, capabilities in ALLOCATION_RESOURCES
    ]
    tasks = []
    for index in range(size):
        capability = rng.choices(CAPABILITIES, weights=(0.5, 0.3, 0.2), k=1)[0]
        depends_on = []
        if index and rng.random() < 0.15:
            depends_on.append(f"t{index - rng.randint(1, min(index, 8)):03d}")
        tasks.append(
            {
                "id": f"t{index:03d}",
                "duration_slots": rng.choice((1, 2, 2, 3, 3, 4)),
                "requires": [capability],
                "depends_on": depends_on,
                "deadline_slot": None,
                "priority": rng.choice((0, 0, 0, 1, 2)),
            }
        )
    return {"slot_minutes": 30, "horizon_slots": horizon, "tasks": tasks, "resources": resources}


def timetable_workload(size: int, horizon: int = 100) -> dict:
    """The expressible subset: 2-6 slot tasks, capability tags, no deps/deadlines."""
    rng = random.Random(20261105 + size)
    resources = [
        {
            "id": f"room-{index:02d}",
            "capacity": 1,
            "capabilities": ["cpu"] if index < 6 else ["gpu"],
        }
        for index in range(12)
    ]
    tasks = []
    for index in range(size):
        tasks.append(
            {
                "id": f"t{index:03d}",
                "duration_slots": rng.choice((2, 3, 4, 4, 5, 6)),
                "requires": [rng.choice(("cpu", "gpu"))],
                "depends_on": [],
                "deadline_slot": None,
                "priority": 0,
            }
        )
    return {"slot_minutes": 30, "horizon_slots": horizon, "tasks": tasks, "resources": resources}
