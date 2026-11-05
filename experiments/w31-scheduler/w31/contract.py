"""W31 experiment — independent allocation contract and validator.

This module is deliberately not the Scheduler's code. It exists so a result can
be checked against the request that produced it, by a reader that shares no
solver internals. Pure functions, no OR-Tools, no I/O.
"""

from __future__ import annotations

from typing import Any

DEFAULT_SLOT_MINUTES = 30
REQUEST_KEYS = {"slot_minutes", "horizon_slots", "tasks", "resources"}
TASK_KEYS = {"id", "duration_slots", "requires", "depends_on", "deadline_slot", "priority"}
RESOURCE_KEYS = {"id", "capacity", "capabilities", "availability"}


class RequestError(ValueError):
    """The request does not match the experiment's frozen input shape."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _strings(raw: Any, what: str, problems: list[str]) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        problems.append(f"{what} must be a list")
        return []
    values = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            problems.append(f"{what} entries must be non-empty strings")
            return []
        values.append(item)
    return values


def _normalize_task(raw: Any, index: int, problems: list[str]) -> dict | None:
    if not isinstance(raw, dict):
        problems.append(f"tasks[{index}] must be a dict")
        return None
    unknown = sorted(set(raw) - TASK_KEYS)
    if unknown:
        problems.append(f"tasks[{index}] unknown keys: {unknown}")
    task_id = raw.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        problems.append(f"tasks[{index}].id must be a non-empty string")
        return None
    duration = raw.get("duration_slots")
    if not _is_int(duration) or duration <= 0:
        problems.append(f"task {task_id!r} duration_slots must be a positive int")
    deadline = raw.get("deadline_slot")
    if deadline is not None and (not _is_int(deadline) or deadline < 0):
        problems.append(f"task {task_id!r} deadline_slot must be a non-negative int or null")
    priority = raw.get("priority", 0)
    if not _is_int(priority):
        problems.append(f"task {task_id!r} priority must be an int")
    return {
        "id": task_id,
        "duration_slots": duration,
        "requires": _strings(raw.get("requires"), f"task {task_id!r} requires", problems),
        "depends_on": _strings(raw.get("depends_on"), f"task {task_id!r} depends_on", problems),
        "deadline_slot": deadline,
        "priority": priority,
    }


def _normalize_resource(raw: Any, index: int, horizon: int, problems: list[str]) -> dict | None:
    if not isinstance(raw, dict):
        problems.append(f"resources[{index}] must be a dict")
        return None
    unknown = sorted(set(raw) - RESOURCE_KEYS)
    if unknown:
        problems.append(f"resources[{index}] unknown keys: {unknown}")
    resource_id = raw.get("id")
    if not isinstance(resource_id, str) or not resource_id.strip():
        problems.append(f"resources[{index}].id must be a non-empty string")
        return None
    capacity = raw.get("capacity", 1)
    if not _is_int(capacity) or capacity <= 0:
        problems.append(f"resource {resource_id!r} capacity must be a positive int")
    raw_windows = raw.get("availability", [])
    if not isinstance(raw_windows, (list, tuple)):
        problems.append(f"resource {resource_id!r} availability must be a list")
        raw_windows = []
    windows = []
    for window_index, window in enumerate(raw_windows):
        if not isinstance(window, dict):
            problems.append(f"resource {resource_id!r} availability[{window_index}] must be a dict")
            continue
        start, end = window.get("start_slot"), window.get("end_slot")
        if not _is_int(start) or not _is_int(end) or not 0 <= start < end <= horizon:
            problems.append(
                f"resource {resource_id!r} availability[{window_index}] must satisfy "
                "0 <= start_slot < end_slot <= horizon_slots"
            )
            continue
        windows.append({"start_slot": start, "end_slot": end})
    if not windows:
        # Same reading as the Scheduler contract: absent/empty means open everywhere.
        windows = [{"start_slot": 0, "end_slot": horizon}]
    return {
        "id": resource_id,
        "capacity": capacity,
        "capabilities": _strings(raw.get("capabilities"), f"resource {resource_id!r} capabilities", problems),
        "availability": windows,
    }


def load(request: dict) -> dict:
    """Validate and fill defaults. Raises RequestError on shape drift."""
    if not isinstance(request, dict):
        raise RequestError("request must be a dict")
    problems: list[str] = []
    unknown = sorted(set(request) - REQUEST_KEYS)
    if unknown:
        problems.append(f"unknown keys: {unknown}")
    slot_minutes = request.get("slot_minutes", DEFAULT_SLOT_MINUTES)
    if not _is_int(slot_minutes) or slot_minutes <= 0:
        problems.append("slot_minutes must be a positive int")
    horizon = request.get("horizon_slots")
    if not _is_int(horizon) or horizon <= 0:
        problems.append("horizon_slots must be a positive int")
        horizon = 0
    raw_tasks = request.get("tasks")
    if not isinstance(raw_tasks, (list, tuple)) or not raw_tasks:
        problems.append("tasks must be a non-empty list")
        raw_tasks = []
    raw_resources = request.get("resources")
    if not isinstance(raw_resources, (list, tuple)) or not raw_resources:
        problems.append("resources must be a non-empty list")
        raw_resources = []
    if problems:
        raise RequestError("; ".join(problems))

    tasks = [item for index, raw in enumerate(raw_tasks) if (item := _normalize_task(raw, index, problems))]
    resources = [
        item for index, raw in enumerate(raw_resources) if (item := _normalize_resource(raw, index, horizon, problems))
    ]
    task_ids = [task["id"] for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        problems.append("task ids must be unique")
    known_tasks = set(task_ids)
    for task in tasks:
        for dependency in task["depends_on"]:
            if dependency == task["id"]:
                problems.append(f"task {task['id']!r} depends on itself")
            elif dependency not in known_tasks:
                problems.append(f"task {task['id']!r} depends on unknown task {dependency!r}")
    resource_ids = [resource["id"] for resource in resources]
    if len(resource_ids) != len(set(resource_ids)):
        problems.append("resource ids must be unique")
    problems.extend(f"dependency cycle: {' -> '.join(cycle)}" for cycle in find_cycles(tasks))
    if problems:
        raise RequestError("; ".join(problems))
    return {
        "slot_minutes": slot_minutes,
        "horizon_slots": horizon,
        "tasks": tasks,
        "resources": resources,
    }


def find_cycles(tasks: list[dict]) -> list[list[str]]:
    """Return one closed trail per cycle found (unknown dependencies are ignored)."""
    by_id = {task["id"]: task for task in tasks}
    state: dict[str, int] = {}
    cycles: list[list[str]] = []

    def visit(task_id: str, trail: list[str]) -> None:
        if state.get(task_id) == 2:
            return
        if state.get(task_id) == 1:
            cycles.append(trail[trail.index(task_id):] + [task_id])
            return
        state[task_id] = 1
        for dependency in by_id[task_id]["depends_on"]:
            if dependency in by_id:
                visit(dependency, trail + [task_id])
        state[task_id] = 2

    for task in tasks:
        visit(task["id"], [])
    return cycles


def can_run(task: dict, resource: dict) -> bool:
    return set(task["requires"]).issubset(set(resource["capabilities"]))


def starts(task: dict, resource: dict) -> list[int]:
    """Start slots where the task fits wholly inside one availability window."""
    if not can_run(task, resource):
        return []
    duration = task["duration_slots"]
    found = []
    for window in resource["availability"]:
        latest = window["end_slot"] - duration
        if latest >= window["start_slot"]:
            found.extend(range(window["start_slot"], latest + 1))
    return found


def validate(request: dict, assignments: list[dict]) -> list[dict]:
    """Every hard constraint, checked from the request alone.

    Returns [] when the assignment is a valid complete schedule; otherwise one
    entry per violation. It never raises for a bad result — the report keeps it.
    """
    payload = load(request)
    tasks = {task["id"]: task for task in payload["tasks"]}
    resources = {resource["id"]: resource for resource in payload["resources"]}
    violations: list[dict] = []
    placed: dict[str, dict] = {}

    def add(code: str, detail: str, task_id: str | None = None) -> None:
        violations.append({"code": code, "task_id": task_id, "detail": detail})

    for assignment in assignments:
        if not isinstance(assignment, dict):
            add("bad_assignment", f"assignment is not a dict: {assignment!r}")
            continue
        task_id = assignment.get("task_id")
        if not isinstance(task_id, str) or task_id not in tasks:
            add("unknown_task", f"assignment names unknown task {task_id!r}", task_id if isinstance(task_id, str) else None)
            continue
        if task_id in placed:
            add("duplicate_task", f"task {task_id!r} assigned more than once", task_id)
            continue
        resource_id = assignment.get("resource_id")
        if not isinstance(resource_id, str) or resource_id not in resources:
            add("unknown_resource", f"task {task_id!r} assigned to unknown resource {resource_id!r}", task_id)
            continue
        task, resource = tasks[task_id], resources[resource_id]
        start, end = assignment.get("start_slot"), assignment.get("end_slot")
        if not _is_int(start) or not _is_int(end):
            add("bad_assignment", f"task {task_id!r} start/end must be ints", task_id)
            continue
        if start < 0 or end > payload["horizon_slots"] or end - start != task["duration_slots"]:
            add("bad_window", f"task {task_id!r} does not fit the horizon with its duration", task_id)
            continue
        if not any(
            window["start_slot"] <= start and end <= window["end_slot"] for window in resource["availability"]
        ):
            add("availability", f"task {task_id!r} runs outside {resource_id!r} availability", task_id)
        if not can_run(task, resource):
            missing = sorted(set(task["requires"]) - set(resource["capabilities"]))
            add("capability", f"task {task_id!r} needs {missing} which {resource_id!r} lacks", task_id)
        placed[task_id] = assignment

    for task in payload["tasks"]:
        if task["id"] not in placed:
            add("unassigned", f"task {task['id']!r} has no assignment", task["id"])

    for task in payload["tasks"]:
        assignment = placed.get(task["id"])
        if assignment is None:
            continue
        for dependency in task["depends_on"]:
            parent = placed.get(dependency)
            if parent is not None and assignment["start_slot"] < parent["end_slot"]:
                add(
                    "dependency",
                    f"task {task['id']!r} starts before dependency {dependency!r} ends",
                    task["id"],
                )
        if task["deadline_slot"] is not None and assignment["end_slot"] > task["deadline_slot"]:
            add("deadline", f"task {task['id']!r} ends after slot {task['deadline_slot']}", task["id"])

    for resource in payload["resources"]:
        events = []
        for assignment in placed.values():
            if assignment["resource_id"] != resource["id"]:
                continue
            events.append((assignment["start_slot"], 1))
            events.append((assignment["end_slot"], -1))
        events.sort(key=lambda event: (event[0], event[1]))
        load_now = 0
        for slot, delta in events:
            load_now += delta
            if load_now > resource["capacity"]:
                add(
                    "capacity",
                    f"{resource['id']!r} runs {load_now} tasks at slot {slot}, capacity is {resource['capacity']}",
                )
    return violations


def objective(request: dict, assignments: list[dict]) -> dict | None:
    """Makespan and priority-weighted completion, from an assignment alone."""
    payload = load(request)
    if validate(request, assignments):
        return None
    weights = {task["id"]: 1 + max(task["priority"], 0) for task in payload["tasks"]}
    ends = {assignment["task_id"]: assignment["end_slot"] for assignment in assignments}
    return {
        "makespan_slots": max(ends.values()),
        "weighted_completion": sum(weights[task_id] * ends[task_id] for task_id in ends),
    }
