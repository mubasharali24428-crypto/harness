"""W31 experiment — deletion-based explanation for an infeasible request.

Start from the whole request, try removing one task at a time, and keep the
removal when the remainder is still infeasible. What survives is irreducible by
single-task deletion, so a reader sees the smallest set this method can prove.

This is a diagnostic over proposals. It never mutates the request it was given.
"""

from __future__ import annotations

from w31.contract import load
from w31.model import solve

DEFAULT_TIME_LIMIT_S = 2.0
DEFAULT_MAX_TASKS = 12


def _request_with(payload: dict, tasks: list[dict]) -> dict:
    return {
        "slot_minutes": payload["slot_minutes"],
        "horizon_slots": payload["horizon_slots"],
        "tasks": tasks,
        "resources": payload["resources"],
    }


def explain_infeasible(
    request: dict,
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    max_tasks: int = DEFAULT_MAX_TASKS,
) -> dict:
    """Return an irreducible infeasible task set, or say why none was computed."""
    payload = load(request)
    initial = solve(request, time_limit_s)
    if initial["status"] != "infeasible":
        return {
            "kind": "not_infeasible",
            "status": initial["status"],
            "reason": initial["reason"],
            "task_ids": [],
            "minimal": False,
        }
    if len(payload["tasks"]) > max_tasks:
        return {
            "kind": "too_large",
            "status": "infeasible",
            "reason": initial["reason"],
            "task_ids": [task["id"] for task in payload["tasks"]],
            "minimal": False,
            "limit": max_tasks,
        }

    kept = [dict(task) for task in payload["tasks"]]
    undecided: list[str] = []
    for task in list(kept):
        if len(kept) == 1:
            break
        trial_tasks = [candidate for candidate in kept if candidate["id"] != task["id"]]
        trial = solve(_request_with(payload, trial_tasks), time_limit_s)
        if trial["status"] == "infeasible":
            kept = trial_tasks
        elif trial["status"] == "unknown":
            undecided.append(task["id"])

    reduced = solve(_request_with(payload, kept), time_limit_s)
    return {
        "kind": "minimal_infeasible_subset",
        "status": "infeasible",
        "reason": reduced["reason"] or initial["reason"],
        "task_ids": [task["id"] for task in kept],
        "minimal": not undecided,
        "undecided_task_ids": undecided,
    }


def render(explanation: dict) -> list[str]:
    """Plain lines a report or reviewer can read without the dict shape."""
    if explanation["kind"] == "too_large":
        return [
            (
                f"infeasible over {len(explanation['task_ids'])} tasks; explanation is capped "
                f"at {explanation['limit']} tasks"
            ),
            explanation["reason"],
        ]
    if explanation["kind"] == "not_infeasible":
        return [f"not infeasible (status {explanation['status']})", explanation["reason"] or ""]
    prefix = "infeasible" if explanation["minimal"] else "infeasible (minimality unproven)"
    return [
        f"{prefix} on tasks: {', '.join(explanation['task_ids'])}",
        explanation["reason"],
    ]
