"""W31 experiment — fresh allocation model, written independently of the Scheduler.

Encoding: one Boolean per (task, resource, start slot). Capacity is a per-slot
load constraint, dependencies and deadlines are linear constraints on the chosen
starts. This is deliberately a different formulation from the Scheduler's
interval-based CP-SAT model, so agreement is evidence rather than a shared bug.

Proposals only: nothing here queues, dispatches, approves or widens permissions.
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model

from w31.contract import can_run, load, starts

DEFAULT_TIME_LIMIT_S = 10.0
WORKERS = 1  # one worker + fixed seed + sorted ids => deterministic output
RANDOM_SEED = 20261104

_STATUS = {
    cp_model.OPTIMAL: "optimal",
    cp_model.FEASIBLE: "feasible",
    cp_model.INFEASIBLE: "infeasible",
    cp_model.UNKNOWN: "unknown",
    cp_model.MODEL_INVALID: "model_invalid",
}


def _empty(payload: dict, status: str, reason: str, elapsed_ms: int, optimized: bool = True) -> dict:
    return {
        "status": status,
        "assignments": [],
        "objective": None,
        "reason": reason,
        "elapsed_ms": elapsed_ms,
        "encoding": "time-indexed",
        "slot_minutes": payload["slot_minutes"],
        "horizon_slots": payload["horizon_slots"],
        "optimized": optimized,
    }


def solve(request: dict, time_limit_s: float | None = None, optimize: bool = True) -> dict:
    """Find a complete assignment, or say honestly why there is none.

    Infeasibility is a result, not an exception: only shape drift raises
    RequestError. A time limit that expires before a first solution yields
    status "unknown" with no assignments, never a partial schedule passed off
    as complete. optimize=False stops at the first feasible assignment.
    """
    started = time.perf_counter()
    payload = load(request)
    tasks = sorted(payload["tasks"], key=lambda task: task["id"])
    resources = sorted(payload["resources"], key=lambda resource: resource["id"])
    horizon = payload["horizon_slots"]

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    for task in tasks:
        capable = [resource for resource in resources if can_run(task, resource)]
        if not capable:
            return _empty(
                payload, "infeasible",
                f"task {task['id']!r} needs capabilities no resource provides",
                elapsed(), optimized=optimize,
            )
        if task["duration_slots"] > horizon:
            return _empty(
                payload, "infeasible",
                f"task {task['id']!r} is longer than the horizon",
                elapsed(), optimized=optimize,
            )
        if not any(starts(task, resource) for resource in capable):
            return _empty(
                payload, "infeasible",
                f"task {task['id']!r} fits in no availability window",
                elapsed(), optimized=optimize,
            )
        if task["deadline_slot"] is not None and task["deadline_slot"] < task["duration_slots"]:
            return _empty(
                payload, "infeasible",
                f"task {task['id']!r} has a deadline shorter than its duration",
                elapsed(), optimized=optimize,
            )

    model = cp_model.CpModel()
    picks: dict[tuple[str, str, int], cp_model.BoolVar] = {}
    by_task: dict[str, list[tuple[str, int, cp_model.BoolVar]]] = {
        task["id"]: [] for task in tasks
    }
    cover: dict[str, list[list]] = {resource["id"]: [[] for _ in range(horizon)] for resource in resources}

    for task in tasks:
        choices = []
        for resource in resources:
            if not can_run(task, resource):
                continue
            for slot in starts(task, resource):
                variable = model.NewBoolVar(f"x_{task['id']}_{resource['id']}_{slot}")
                picks[(task["id"], resource["id"], slot)] = variable
                by_task[task["id"]].append((resource["id"], slot, variable))
                choices.append(variable)
                for covered in range(slot, slot + task["duration_slots"]):
                    cover[resource["id"]][covered].append(variable)
        # The prechecks guarantee a non-empty domain for every task.
        model.AddExactlyOne(choices)

    start_terms: dict[str, list] = {task["id"]: [] for task in tasks}
    for (task_id, _resource_id, slot), variable in picks.items():
        start_terms[task_id].append(slot * variable)
    start_of = {task_id: sum(terms) for task_id, terms in start_terms.items()}
    end_of = {
        task["id"]: start_of[task["id"]] + task["duration_slots"] for task in tasks
    }

    for task in tasks:
        for dependency in task["depends_on"]:
            model.Add(start_of[task["id"]] >= end_of[dependency])
        if task["deadline_slot"] is not None:
            model.Add(end_of[task["id"]] <= task["deadline_slot"])

    for resource in resources:
        for slot, variables in enumerate(cover[resource["id"]]):
            if variables:
                model.Add(sum(variables) <= resource["capacity"])

    makespan = model.NewIntVar(0, horizon, "makespan")
    for task in tasks:
        model.Add(makespan >= end_of[task["id"]])
    weights = {task["id"]: 1 + max(task["priority"], 0) for task in tasks}
    if optimize:
        scale = len(tasks) * max(weights.values()) * horizon + 1
        model.Minimize(
            makespan * scale + sum(weights[task_id] * end for task_id, end in end_of.items())
        )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = (
        DEFAULT_TIME_LIMIT_S if time_limit_s is None else float(time_limit_s)
    )
    solver.parameters.num_search_workers = WORKERS
    solver.parameters.random_seed = RANDOM_SEED
    status = solver.Solve(model)
    label = _STATUS.get(status, "unknown")

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        reason = (
            "no assignment satisfies every constraint"
            if status == cp_model.INFEASIBLE
            else "solver returned no usable assignment"
        )
        return _empty(payload, label, reason, elapsed(), optimized=optimize)

    assignments = []
    for task in tasks:
        chosen = [
            (resource_id, slot)
            for resource_id, slot, variable in by_task[task["id"]]
            if solver.Value(variable)
        ]
        resource_id, slot = chosen[0]
        assignments.append(
            {
                "task_id": task["id"],
                "resource_id": resource_id,
                "start_slot": slot,
                "end_slot": slot + task["duration_slots"],
            }
        )

    return {
        "status": label,
        "assignments": assignments,
        "objective": {
            "makespan_slots": max(assignment["end_slot"] for assignment in assignments),
            "weighted_completion": sum(
                weights[assignment["task_id"]] * assignment["end_slot"]
                for assignment in assignments
            ),
        },
        "reason": None,
        "elapsed_ms": elapsed(),
        "encoding": "time-indexed",
        "slot_minutes": payload["slot_minutes"],
        "horizon_slots": horizon,
        "optimized": optimize,
    }
