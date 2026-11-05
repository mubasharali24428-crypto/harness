"""W31 experiment — bridge from the neutral allocation contract to the Scheduler.

The Scheduler's frozen timetable contract speaks sessions, rooms, a 08:00-18:00
week grid and 60-180 minute blocks. This module translates an allocation request
into that shape, runs the Scheduler's own `solve()`, and maps the placements
back. Anything the timetable contract cannot represent is listed explicitly
instead of being silently dropped.

One bridge convention: a neutral "slot" is 30 minutes, a day is 20 slots, and a
week is 100 slots (Mon-Fri, 08:00-18:00). Slots are day-major.
"""

from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from w31.contract import load, validate

DAY_SLOTS = 20
WEEK_SLOTS = 5 * DAY_SLOTS
DAY_START_MIN = 8 * 60
MIN_DURATION_SLOTS = 2  # 60 minutes, the timetable contract's floor
MAX_DURATION_SLOTS = 6  # 180 minutes, the timetable contract's ceiling
MAX_CAPACITY_COPIES = 32  # a bridge limit, not a contract limit
DEFAULT_SCHEDULER_ROOT = Path("/Users/user/scheduler")


def scheduler_root() -> Path | None:
    candidate = Path(os.environ.get("SCHEDULER_ROOT", DEFAULT_SCHEDULER_ROOT))
    if (candidate / "solver" / "__init__.py").is_file():
        return candidate
    return None


def load_scheduler():
    """Import the Scheduler by path; never copy its code into the Harness."""
    root = scheduler_root()
    if root is None:
        return None
    root = root.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import solver  # path-dependent import, guarded above

    if Path(solver.__file__).resolve().parent.parent != root:
        raise RuntimeError(f"imported solver from {solver.__file__!r}, expected under {root}")
    return solver


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _day_windows(availability: list[dict]) -> list[dict]:
    """Split slot windows into per-day HH:MM windows on the timetable grid."""
    by_day: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for window in availability:
        for day in range(5):
            low = max(window["start_slot"], day * DAY_SLOTS)
            high = min(window["end_slot"], (day + 1) * DAY_SLOTS)
            if low < high:
                by_day[day].append((low - day * DAY_SLOTS, high - day * DAY_SLOTS))
    windows = []
    for day in range(5):
        spans = sorted(by_day.get(day, []))
        merged: list[list[int]] = []
        for low, high in spans:
            if merged and low <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], high)
            else:
                merged.append([low, high])
        for low, high in merged:
            windows.append(
                {
                    "days": [day],
                    "start": _hhmm(DAY_START_MIN + low * 30),
                    "end": _hhmm(DAY_START_MIN + high * 30),
                    "applies_to": "W31:allocation",
                }
            )
    return windows


def translate(request: dict) -> dict:
    """Map a request to {sessions, rooms}; list every unrepresentable feature."""
    payload = load(request)
    unsupported: list[dict] = []
    horizon = payload["horizon_slots"]
    if horizon > WEEK_SLOTS:
        unsupported.append(
            {
                "feature": "horizon",
                "detail": f"{horizon} slots exceeds the timetable week ({WEEK_SLOTS})",
            }
        )

    sessions = []
    for task in payload["tasks"]:
        if not MIN_DURATION_SLOTS <= task["duration_slots"] <= MAX_DURATION_SLOTS:
            unsupported.append(
                {
                    "feature": "duration",
                    "task_id": task["id"],
                    "detail": f"{task['duration_slots']} slots is outside the timetable's 2-6 slot range",
                }
            )
        if task["depends_on"]:
            unsupported.append(
                {
                    "feature": "dependency",
                    "task_id": task["id"],
                    "detail": f"depends_on {sorted(task['depends_on'])} has no timetable field",
                }
            )
        if task["deadline_slot"] is not None:
            unsupported.append(
                {
                    "feature": "deadline",
                    "task_id": task["id"],
                    "detail": f"deadline_slot {task['deadline_slot']} has no timetable field",
                }
            )
        if task["priority"] != 0:
            unsupported.append(
                {
                    "feature": "priority",
                    "task_id": task["id"],
                    "detail": f"priority {task['priority']} has no timetable field",
                }
            )
        sessions.append(
            {
                "id": task["id"],
                "course": "w31-allocation",
                "duration_min": task["duration_slots"] * 30,
                "is_merged": False,
                "section_ids": [],
                "combined_strength": 0,
                "required_features": list(task["requires"]),
            }
        )

    rooms = []
    room_to_resource: dict[str, str] = {}
    for resource in payload["resources"]:
        if resource["capacity"] > MAX_CAPACITY_COPIES:
            unsupported.append(
                {
                    "feature": "capacity",
                    "resource_id": resource["id"],
                    "detail": (
                        f"capacity {resource['capacity']} needs more than "
                        f"{MAX_CAPACITY_COPIES} room copies"
                    ),
                }
            )
        windows = _day_windows(resource["availability"])
        for copy in range(min(resource["capacity"], MAX_CAPACITY_COPIES)):
            room_id = f"{resource['id']}#{copy}"
            room_to_resource[room_id] = resource["id"]
            rooms.append(
                {
                    "id": room_id,
                    "erp_id": None,
                    "capacity": 1,
                    "type": "theory",
                    "home_dept": None,
                    "features": list(resource["capabilities"]),
                    "windows": windows,
                }
            )

    return {
        "ok": not unsupported,
        "unsupported": unsupported,
        "request": {"sessions": sessions, "rooms": rooms},
        "room_to_resource": room_to_resource,
    }


def _assignment_from_placement(placement: dict, room_to_resource: dict[str, str]) -> dict:
    start = placement["day"] * DAY_SLOTS + (_minutes(placement["start"]) - DAY_START_MIN) // 30
    end = placement["day"] * DAY_SLOTS + (_minutes(placement["end"]) - DAY_START_MIN) // 30
    return {
        "task_id": placement["session_id"],
        "resource_id": room_to_resource[placement["room_id"]],
        "start_slot": start,
        "end_slot": end,
    }


def run(request: dict, engine: str = "heuristic") -> dict:
    """Translate, solve with the Scheduler, and validate the mapped-back result."""
    translation = translate(request)
    if not translation["ok"]:
        return {
            "status": "not_expressible",
            "assignments": [],
            "violations": [],
            "unsupported": translation["unsupported"],
            "elapsed_ms": 0,
            "engine": None,
        }
    scheduler = load_scheduler()
    if scheduler is None:
        return {
            "status": "unavailable",
            "assignments": [],
            "violations": [],
            "unsupported": [],
            "elapsed_ms": 0,
            "engine": engine,
            "reason": f"Scheduler not found at {scheduler_root() or DEFAULT_SCHEDULER_ROOT}",
        }

    started = time.perf_counter()
    try:
        if engine == "heuristic":
            result = scheduler.solve(translation["request"])
        else:
            from solver.engine import solve_current

            result = solve_current(translation["request"], engine=engine)
    except Exception as error:  # noqa: BLE001 — report scheduler failures, never fake a plan
        return {
            "status": "error",
            "assignments": [],
            "violations": [],
            "unsupported": [],
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "engine": engine,
            "reason": f"{type(error).__name__}: {error}",
        }
    elapsed = int((time.perf_counter() - started) * 1000)

    assignments = [
        _assignment_from_placement(placement, translation["room_to_resource"])
        for placement in result.get("assignments", [])
    ]
    violations = validate(request, assignments)
    others = [violation for violation in violations if violation["code"] != "unassigned"]
    if not violations:
        status = "solved"
    elif others:
        status = "invalid"
    else:
        status = "partial"
    return {
        "status": status,
        "assignments": assignments,
        "violations": violations,
        "unsupported": [],
        "elapsed_ms": elapsed,
        "engine": engine,
        "scheduler_conflicts": result.get("conflicts", []),
        "scheduler_score": result.get("score"),
        "translated": {
            "sessions": len(translation["request"]["sessions"]),
            "rooms": len(translation["request"]["rooms"]),
        },
    }
