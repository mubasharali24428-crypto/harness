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
    clients surface failure without parsing. The data-not-instructions
    framing block is appended once at the protocol boundary (server.py
    ``_result``, S1-R04e) so error paths get it too.
    """
    return [
        {"type": "text", "text": json.dumps(payload, separators=(",", ":")), "isError": is_error}
    ]


async def _tool_start_run(api: HarnessApiClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Propose a plan ONLY. A human must approve it in the Harness UI/REST.

    S-04: this tool used to walk propose → approve → start, bypassing the
    human plan gate for anything holding the service principal. It now stops
    at AWAITING_PLAN_APPROVAL; the caller can poll get_run_status after a
    human approves, but the agent can never approve its own plan.
    """
    goal = str(args["goal"]).strip()
    try:
        proposed = await api.propose_plan(goal)
    except UpstreamError as exc:
        return _text_result(exc.envelope, is_error=True)
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
    return _text_result(
        {
            "planId": plan_id,
            "state": "AWAITING_PLAN_APPROVAL",
            "approvalRequired": True,
            "goal": goal,
        }
    )


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
                "Propose a governed AI engineering run and STOP at the human "
                "approval gate. Returns planId and state=AWAITING_PLAN_APPROVAL; "
                "a human must approve the plan in the Harness UI/REST before the "
                "run can start. This tool never approves or starts a run on the "
                "caller's behalf. Use get_run_status to follow progress after "
                "approval."
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
