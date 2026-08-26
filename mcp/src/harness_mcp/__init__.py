"""harness-mcp — the AI Engineering Harness as a Model Context Protocol server.

Exposes four LLM-facing tools over MCP (stdio + streamable HTTP):

    start_run       — mint a run+plan and (per mission contract) drive it to
                      EXECUTING through the frozen plan-gate walk.
    get_run_status  — read one run's state machine position.
    search_context  — semantic codebase search against Section 11's index.
    list_projects   — list the caller's team's projects.

Every tool is a THIN authenticated wrapper over an existing section API
(isolation law: this module imports ONLY harness_contracts; it never opens a
database connection). The service token from ``HARNESS_MCP_SERVICE_TOKEN``
acts as the principal for every upstream call, so all harness invariants
(team scoping, role gates, audit trail) keep applying unchanged.

Governance invariant (CLAUDE.md / AGENTS.md): human plan approval is
mandatory. ``start_run`` drives the same propose→approve→start walk the
plan-gate UI drives, using the configured SERVICE principal — it does not
bypass or weaken any gate. Teams that want the human gate between propose and
approve should use the REST API directly instead of this tool.

Error honesty: upstream failures NEVER raise out of the tool layer. They come
back as ``isError=True`` CallToolResults carrying the section's own
``{"error": {"code", "message"}}`` envelope, so an LLM consumer can read,
classify and react to the exact failure the harness produced.
"""

from __future__ import annotations

from .config import Settings
from .server import build_server

__all__ = ["Settings", "build_server"]
__version__ = "1.0.0rc1"
