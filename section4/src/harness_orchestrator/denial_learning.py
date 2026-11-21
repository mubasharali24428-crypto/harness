"""I-08 / P-23 — the denial-learning loop (90-day plan W12).

W27's guard stops a run that keeps hitting policy denials
(``denials_repeated``) and W27's ``scopeRequest`` names, on the wire, the one
path whose addition would unblock it. What nothing did with that fact is the
gap I-08 closes: the stop should hand the human the correction AS a rule, so
the next run's plan asks for the path up front instead of earning the same
denial again — repeat-denial count trending to zero on pilot tasks.

The loop, and where this module sits in it:

  denial stop (W27 guard) -> suggested project rule (HERE) -> the human decides
  and saves it through the EXISTING rules API (PUT /projects/{id}/rules) -> the
  rules loader injects it into the planner prompt (M12-G10, unchanged) -> the
  next plan includes the path in its intent scope -> no repeat denial.

Laws this module does not get to break:

* **A suggestion never saves itself.** Project rules are user-authored
  (`docs/extensions.md` §3; migration 0019 docstring). The orchestrator may
  PROPOSE the rule text; only the human writes it. Auto-writing rules from
  machine output would be a policy change smuggled in as convenience.
* **The suggestion is deterministic and bounded.** Same denials, same text —
  a rule the human must approve should not differ between replays. Paths are
  capped (most-specific wins by first appearance) and the text is one short
  paragraph, because it lands in a prompt block that is advisory, not law.
* **"Regression test" here means the pipeline is pinned, not the prose.** The
  tests pin: a scope-request record becomes a collected path; a denial stop
  carries the suggestion in the durable failure reason; a saved rule reaches
  the planner prompt through the untouched rules loader. The human-visible
  seam is the ENGINE_FAILED reason text, which the run record persists.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from harness_orchestrator.loop_guard import scope_request_of

#: Bound the collected paths: a run that probes fifty paths should not turn
#: into a fifty-line rule suggestion.
MAX_SUGGESTED_PATHS = 5
MAX_COLLECTED_PATHS = 20
#: Keep the durable reason text a reason, not a report.
SUGGESTION_MAX_CHARS = 400

RULE_HEADER = "LEARNED FROM DENIALS"


def collect_scope_request(run_data: dict[str, Any], record: dict) -> dict | None:
    """Pull the scope request out of one streamed result and remember it.

    Returns the request (the same dict the TOOL_RESULT payload carries) so the
    caller does not parse twice, or None. The list on ``run_data`` is bounded:
    a probe-happy run cannot grow it without end.
    """
    req = scope_request_of(record)
    if req is None:
        return None
    paths = run_data.setdefault("_denial_scope_requests", [])
    if req.get("path") not in [p.get("path") for p in paths]:
        if len(paths) < MAX_COLLECTED_PATHS:
            paths.append(req)
    return req


def suggested_rule_text(run_id: str, paths: list[dict]) -> str | None:
    """The rule text a human can save verbatim after a denial stop.

    ``paths`` are the scope requests the run collected (``{"path": ...}``).
    None when there are none — a stop without a named path has no lesson to
    suggest, and inventing one ("be more careful") would be noise the human
    has to delete.
    """
    clean: list[str] = []
    for req in paths[:MAX_SUGGESTED_PATHS]:
        path = req.get("path")
        if isinstance(path, str) and path and path not in clean:
            clean.append(path)
    if not clean:
        return None
    listed = ", ".join(f"`{p}`" for p in clean)
    suggestion = (
        f"{RULE_HEADER} (run {run_id}): include {listed} in the plan's"
        " scopePaths — these paths were repeatedly denied by the sandbox"
        " boundary, so future plans must request them up front."
    )
    return suggestion[:SUGGESTION_MAX_CHARS]


def append_learning(detail: str, suggestion: str | None) -> str:
    """One durable sentence the human actually sees where the run stopped."""
    if not suggestion:
        return detail
    return f"{detail}. Suggested project rule — {suggestion}"


async def repeat_denial_stops(
    db: AsyncSession, project_id: str
) -> dict[str, Any]:
    """The metric the plan's acceptance line asks for, per project.

    Counts the project's durable ``engine_failed`` stops whose reason names
    ``denials_repeated``. CAST ... LIKE is the same cross-engine pattern
    audit_rows uses (works on PostgreSQL jsonb and SQLite TEXT alike).
    """
    row = (
        await db.execute(
            text(
                "SELECT count(*), COALESCE(MAX(ts), 0) FROM agent_events"
                " WHERE project_id = :p AND type = 'engine_failed'"
                " AND CAST(payload AS TEXT) LIKE '%denials_repeated%'"
            ),
            {"p": project_id},
        )
    ).first()
    return {"repeatDenialStops": int(row[0] or 0), "lastStopAt": str(row[1]) if row[1] else None}
