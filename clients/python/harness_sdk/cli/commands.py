"""Command implementations: plan → approval → run → follow, plus reads.

Nothing here talks HTTP directly; every call goes through `harness_sdk`, so the
CLI cannot drift from the service contracts. The approval prompt is the product's
plan gate: `--yes` is an explicit operator decision, and a refusal rejects the
plan rather than leaving it pending.
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
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


def _default_launch() -> Callable[..., dict[str, Any]]:
    from .local import launch

    return launch


def _default_apply() -> Callable[..., dict[str, Any]]:
    from .local import apply_back

    return apply_back


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _record_local_provenance(
    sdk: Any,
    run_id: str,
    engine: dict[str, Any],
    applied: dict[str, Any],
    state: str,
    note: Callable[[str], None],
) -> None:
    """Server-side trace of a LOCAL execution, existing endpoints only.

    A diff artifact (the bytes are the boundary's own diff) plus a run comment
    carrying the provenance: what ran, where, under what confinement, and what
    the apply touched. Tool-call-level `agent_events` cannot be minted off-host
    by any existing endpoint — saying so IS the audit story (docs/week-6-plan.md,
    slice 4 decision 3). A failed trace is reported, never hidden.
    """
    import hashlib
    import json as _json

    diff = engine.get("diff") or ""
    sha = hashlib.sha256(diff.encode()).hexdigest()[:16]
    try:
        sdk.orchestrator.upload_artifact(
            run_id, name=_SAFE_NAME.sub("-", f"local-diff-{run_id}") + ".patch", kind="diff"
        )
    except Exception as exc:  # noqa: BLE001 - trace failure must not lose the local result
        note(f"local: diff artifact could not be registered ({exc})")
    provenance = {
        "executed": "local",
        "confinement": (engine.get("local") or {}).get("confinement"),
        "image": (engine.get("local") or {}).get("image"),
        "status": engine.get("status"),
        "state": state,
        "diffSha256": sha,
        "diffChars": len(diff),
        "diffOmitted": engine.get("diffOmitted"),
        "applied": applied.get("applied", []),
        "deleted": applied.get("deleted", []),
    }
    try:
        sdk.orchestrator.run_comments_create(
            run_id, {"body": "harness-cli local run: " + _json.dumps(provenance, sort_keys=True),
                     "refKind": "diff"}
        )
    except Exception as exc:  # noqa: BLE001
        note(f"local: provenance comment could not be posted ({exc})")


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
    local_dir: str | None = None,
    local_launch: Callable[..., dict[str, Any]] | None = None,
    local_apply: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One run through the same gate the web uses.

    ``local_dir`` switches the EXECUTION site, not the gate: propose → approve
    happen on the server exactly as for a web run, ``start_run`` is deliberately
    NOT called (the queue only claims EXECUTING runs, so an un-started approved
    run never phantom-executes server-side and can never double-run with the
    local engine). The turn then goes through the confined local launcher, the
    result is applied back to the directory, and the diff + an `executed=local`
    provenance comment are posted through existing APIs. No new endpoint, no
    bypassed approval.
    """
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

    if local_dir is None:
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

    # ---- local-workspace mode (slice 4): same gate, confined local execution --
    note(f"approved; run {run_id} executes LOCALLY in {local_dir}")
    launch = local_launch or _default_launch()
    apply_fn = local_apply or _default_apply()
    policy: dict[str, Any] | None = None
    try:
        plan_state = sdk.orchestrator.get_plan(run_id) or {}
        policy = plan_state.get("shellPolicy") or plan_state.get("policy") or None
    except Exception:  # noqa: BLE001 - a plan without a readable policy still runs; boundary laws hold
        note("local: plan policy not readable, running with the boundary defaults")
    engine = launch(
        local_dir, goal, run_id, shell_policy=policy, timeout=timeout, note=note
    )
    state = {"completed": "done", "error": "error"}.get(
        str(engine.get("status") or ""), str(engine.get("status") or "error")
    )
    applied: dict[str, Any] = {"applied": [], "deleted": []}
    try:
        applied = apply_fn(
            Path(local_dir), Path(engine["workDir"]),
            list(engine.get("changed") or []), list(engine.get("deleted") or []),
        )
    finally:
        shutil.rmtree(engine.get("workDir", ""), ignore_errors=True)
    _record_local_provenance(sdk, run_id, engine, applied, state, note)
    return {
        "runId": run_id,
        "planId": plan_id,
        "state": state,
        "answer": str(engine.get("finalResponse") or ""),
        "toolCalls": engine.get("toolCalls") or [],
        "steps": [],
        "local": {
            "workspace": local_dir,
            "applied": applied["applied"],
            "deleted": applied["deleted"],
            "diffChars": len(engine.get("diff") or ""),
            "confinement": (engine.get("local") or {}).get("confinement"),
            "image": (engine.get("local") or {}).get("image"),
        },
    }


def chat_loop(
    sdk: Any,
    session: Any,
    *,
    read_line: Callable[[str], str | None],
    emit: Callable[[str], None],
    confirm: Callable[[str], str] = input,
    assume_yes: bool = False,
    note: Callable[[str], None] = lambda _m: None,
    poll_seconds: float = 2.0,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """`harness chat`: an interactive loop over the same run path.

    Session identity is explicit (see cli/session.py): each turn is a normal
    plan-gated run whose goal is the composed history block plus the message,
    so the engine remembers exactly what is rendered into that block and
    nothing else. Every turn goes through the gate — `--yes` covers all turns
    because the operator opted in explicitly, and a refusal rejects that
    turn's plan and ends the loop rather than silently continuing without the
    approval the next message would assume.
    """
    auto = bool(assume_yes)
    while True:
        message = read_line("you> ")
        if message is None or not message.strip():
            break
        message = message.strip()
        if message in {"/exit", "/quit"}:
            break
        if message == "/id":
            emit(f"[session {session.session_id}]")
            continue
        goal = session.compose_goal(message)
        result = execute_run(
            sdk,
            goal,
            project_id=session.project_id,
            assume_yes=auto,
            confirm=confirm,
            note=note,
            poll_seconds=poll_seconds,
            timeout=timeout,
        )
        session.add(message, goal, result)
        session.save()
        if result["state"] == "rejected":
            emit("plan rejected; ending the chat — approve is a per-run decision, "
                 "use --yes to cover every turn")
            break
        emit(f"{result['answer']}\n[run {result['runId']}: {result['state']}]")
    return {
        "sessionId": session.session_id,
        "exchanges": session.exchanges,
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
