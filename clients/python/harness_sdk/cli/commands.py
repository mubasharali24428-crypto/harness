"""Command implementations: plan → approval → run → follow, plus reads.

Nothing here talks HTTP directly; every call goes through `harness_sdk`, so the
CLI cannot drift from the service contracts. The approval prompt is the product's
plan gate: `--yes` is an explicit operator decision, and a refusal rejects the
plan rather than leaving it pending.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

#: States after which the CLI stops following a run. ``awaiting_done_signal`` is
#: where a successful engine turn settles in this stack, so it ends the follow
#: loop even though a human may still signal completion in the web UI.
SETTLED = frozenset(
    {
        "done",
        "error",
        "failed",
        "execution_failed",
        "stopped",
        "cancelled",
        "blocked",
        "awaiting_done_signal",
    }
)


class CliError(RuntimeError):
    """A refusal the user can act on (bad config, ambiguous project, no token)."""


def pick_project(sdk: Any, project_id: str | None) -> str:
    if project_id:
        return project_id
    page = sdk.identity.list_projects(50)
    items = page.get("items") if isinstance(page, dict) else page
    items = [item for item in (items or []) if item.get("id")]
    if not items:
        raise CliError("no projects; create one in the web UI or pass --project <id>")
    if len(items) == 1:
        return str(items[0]["id"])
    listing = "\n".join(f"  {item['id']}  {item.get('name', '')}" for item in items)
    raise CliError(f"multiple projects; pass --project <id>:\n{listing}")


def execute_run(
    sdk: Any,
    goal: str,
    *,
    project_id: str | None = None,
    assume_yes: bool = False,
    confirm: Callable[[str], str] = input,
    note: Callable[[str], None] = lambda _m: None,
    poll_seconds: float = 2.0,
    timeout: float = 900.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """One run through the same gate the web uses."""
    if not goal.strip():
        raise CliError("goal must not be empty")
    resolved_project = pick_project(sdk, project_id)
    plan = sdk.orchestrator.propose_plan({"goal": goal, "projectId": resolved_project})
    # POST /plans mints the plan (and the run) but returns only the planId; the
    # run binding comes back from APPROVE. Assuming a runId here made the whole
    # command fail against a real server while unit tests passed on a stub.
    plan_id = plan.get("planId")
    if not plan_id:
        raise CliError(f"plan response missing planId: {plan!r}")
    note(f"plan {plan_id} proposed for {goal.strip()[:60]}")

    if not assume_yes:
        answer = confirm("Approve and start this run? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            sdk.orchestrator.reject_plan(plan_id)
            note("plan rejected")
            return {
                "runId": None,
                "planId": plan_id,
                "state": "rejected",
                "answer": "",
                "toolCalls": [],
                "steps": [],
            }

    approved = sdk.orchestrator.approve_plan(plan_id) or {}
    run_id = approved.get("runId") if isinstance(approved, dict) else None
    if not run_id:
        raise CliError(f"approve response missing runId: {approved!r}")
    note(f"approved; run {run_id}")
    sdk.orchestrator.start_run(run_id)

    started = clock()
    state = "unknown"
    steps: list[dict[str, Any]] = []
    while True:
        run = sdk.orchestrator.get_run(run_id)
        state = str(run.get("state") or state)
        steps = list(run.get("steps") or [])
        if state in SETTLED:
            break
        if clock() - started > timeout:
            state = "timeout"
            break
        sleep(poll_seconds)

    engine: dict[str, Any] = {}
    if state != "timeout":
        engine = sdk.orchestrator.get_engine_result(run_id) or {}
    return {
        "runId": run_id,
        "planId": plan_id,
        "state": state,
        "answer": str(engine.get("finalResponse") or ""),
        "toolCalls": engine.get("toolCalls") or [],
        "steps": steps,
    }


def run_status(sdk: Any, run_id: str) -> dict[str, Any]:
    run = sdk.orchestrator.get_run(run_id)
    return {
        "runId": run.get("runId", run_id),
        "state": run.get("state"),
        "steps": list(run.get("steps") or []),
    }


def run_diff(sdk: Any, run_id: str, limit: int = 100) -> dict[str, Any]:
    return sdk.orchestrator.run_diff(run_id, limit=limit) or {}


def format_diff(payload: dict[str, Any]) -> str:
    files = payload.get("files") or []
    if not files:
        return "no diff recorded for this run"
    blocks = []
    for item in files:
        header = f"{item.get('path')}  (+{item.get('additions', 0)} -{item.get('deletions', 0)})"
        patch = item.get("patch")
        if patch is None:
            header += f"  [{item.get('patchOmitted') or 'omitted'}]"
            blocks.append(header)
        else:
            blocks.append(f"{header}\n{patch}")
    total = payload.get("totalFiles")
    suffix = f"\n\n{len(files)} of {total} file(s)" if total and total > len(files) else ""
    return "\n\n".join(blocks) + suffix


def format_steps(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "no events"
    return "\n".join(f"{step.get('seq')}\t{step.get('type')}" for step in steps)
