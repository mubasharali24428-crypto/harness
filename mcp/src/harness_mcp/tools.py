"""The four harness tools: JSON schemas, LLM-facing docs, thin implementations.

Registry shape: each entry carries name, description (written for LLM
consumers), inputSchema (JSON Schema per the MCP spec) and a ``run`` coroutine.
``build_server`` publishes exactly this list through tools/list, so the
handshake and the registry can never drift apart.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from .client import HarnessApiClient, UpstreamError

ToolHandler = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]

_OBJECT = "object"


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": _OBJECT, "properties": properties, "required": required}


def _text_result(payload: dict[str, Any], *, is_error: bool = False) -> list[dict[str, Any]]:
    """One TextContent block carrying compact JSON of the wire payload.

    The full structured payload rides in the text block (LLMs consume it);
    errors additionally set isError=True at the CallToolResult level so MCP
    clients surface failure without parsing.
    """
    return [
        {"type": "text", "text": json.dumps(payload, separators=(",", ":")), "isError": is_error}
    ]


async def _tool_start_run(api: HarnessApiClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    """propose → approve → start, as the service principal.

    The orchestrator's plan-gate mints runId+planId together; approve returns
    the bound runId; /runs/{id}/start flips AWAITING_PLAN_APPROVAL → EXECUTING.
    A mid-walk failure surfaces which step died plus everything collected so
    far (planId/runId) so the caller can resume via REST instead of re-minting.
    """
    goal = str(args["goal"]).strip()
    plan_id: str | None = None
    run_id: str | None = None
    try:
        proposed = await api.propose_plan(goal)
        plan_id = proposed.get("planId")
        if not plan_id:
            return _text_result(
                {
                    "error": {
                        "code": "UPSTREAM_ERROR",
                        "message": "orchestrator returned no planId",
                        "upstream": proposed,
                    }
                }
            )
        approved = await api.approve_plan(str(plan_id))
        run_id = approved.get("runId")
        if not run_id:
            # Plan minted but not approved: report honestly with the handle.
            return _text_result(
                {
                    "error": {
                        "code": "PLAN_NOT_APPROVED",
                        "message": "approve did not return a runId; plan remains undecided",
                        "planId": plan_id,
                    }
                }
            )
        started = await api.start_run(str(run_id))
    except UpstreamError as exc:
        body = dict(exc.envelope)
        error = body.setdefault("error", {})
        # Attach walk progress so the caller can resume via REST (e.g. decide
        # the already-minted plan) instead of blindly re-running start_run.
        if plan_id is not None and "planId" not in body:
            error["planId"] = plan_id
        if run_id is not None and "runId" not in body:
            error["runId"] = run_id
        return _text_result(body, is_error=True)
    out: dict[str, Any] = {
        "runId": started.get("runId", run_id),
        "planId": plan_id,
        "state": started.get("state"),
        "goal": goal,
    }
    return _text_result(out)


async def _tool_get_run_status(api: HarnessApiClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        status = await api.run_status(str(args["runId"]))
    except UpstreamError as exc:
        return _text_result(exc.envelope, is_error=True)
    return _text_result(status)


async def _tool_search_context(api: HarnessApiClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    top_k = int(args.get("topK") or 8)
    top_k = max(1, min(top_k, 50))
    try:
        res = await api.search_context(str(args["query"]), str(args["projectId"]), top_k)
    except UpstreamError as exc:
        return _text_result(exc.envelope, is_error=True)
    results = res.get("results", [])
    return _text_result({"projectId": res.get("projectId", args["projectId"]), "results": results})


async def _tool_list_projects(api: HarnessApiClient, _args: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        res = await api.list_projects()
    except UpstreamError as exc:
        return _text_result(exc.envelope, is_error=True)
    return _text_result(res)


def build_tools(api: HarnessApiClient) -> list[dict[str, Any]]:
    """The registry. Order is stable so inspector output is deterministic."""
    return [
        {
            "name": "start_run",
            "description": (
                "Start a governed AI engineering run in the harness. Mints a plan "
                "for the given goal and drives it to EXECUTING through the human "
                "plan-approval gate using this server's service principal. Use "
                "this when an agent should DO engineering work in a project repo; "
                "pair with get_run_status to follow progress. Returns runId, "
                "planId and state. Requires an engineer/admin service principal."
            ),
            "inputSchema": _schema(
                {
                    "goal": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "What the run should accomplish, stated as a verifiable goal."
                        ),
                    },
                },
                ["goal"],
            ),
            "run": lambda args: _tool_start_run(api, args),
        },
        {
            "name": "get_run_status",
            "description": (
                "Read one harness run's current state (IDLE, PLANNING, "
                "AWAITING_PLAN_APPROVAL, EXECUTING, BLOCKED, DONE, ...), its "
                "plan approval flag and engine mode. Use after start_run to "
                "poll progress or before deciding next steps on a run."
            ),
            "inputSchema": _schema(
                {
                    "runId": {
                        "type": "string",
                        "description": "The run identifier returned by start_run.",
                    },
                },
                ["runId"],
            ),
            "run": lambda args: _tool_get_run_status(api, args),
        },
        {
            "name": "search_context",
            "description": (
                "Semantic codebase search over a project's indexed repository "
                "(Section 11 context pillar). Returns scored chunks with path, "
                "language, line range and content. Use to ground a goal in real "
                "code BEFORE calling start_run, or to answer 'where is X "
                "implemented' questions about the project."
            ),
            "inputSchema": _schema(
                {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query about the codebase.",
                    },
                    "projectId": {
                        "type": "string",
                        "description": "Harness project whose indexed repo to search.",
                    },
                    "topK": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 8,
                        "description": "How many chunks to return.",
                    },
                },
                ["query", "projectId"],
            ),
            "run": lambda args: _tool_search_context(api, args),
        },
        {
            "name": "list_projects",
            "description": (
                "List the projects visible to this server's service account "
                "(id, name, repo). Call first when you don't know a projectId."
            ),
            "inputSchema": _schema({}, []),
            "run": lambda args: _tool_list_projects(api, args),
        },
    ]
