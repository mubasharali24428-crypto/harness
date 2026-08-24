"""M12-G10: fetch user-authored project rules and build the planner prompt block.

Part3 gap #9 first slice (roadmap X2): the harness's ``.cursorrules``
equivalent. Users author per-project rules text (stored in S1's
``project_rules`` table via the identity API); the planner must honor it.

HARD BOUNDARY — rules are ADVISORY, by design and by law:
- This loader only ever produces a PROMPT BLOCK for plan-proposal time.
- It never touches the sandbox allow-list, never mutates
  ``forbidden_paths``, and cannot grant any execution capability. Section 5
  remains the sole hard-enforcement layer (allow-list + forbidden_paths are
  hard constraints regardless of what the rules text says).
- Every read is failure-tolerant: a missing table, a dead DB, or an oversized
  document degrades to "no rules" with a warning log — loading rules can
  never break planning (same posture as the plan mirror).

Prompt-block contract (asserted by tests/unit/test_rules_loader.py):
- ``build_rules_block("")`` → ``""`` (no rules ⇒ nothing injected)
- non-empty text ⇒ exactly one line starting with the
  ``PROJECT_RULES_BLOCK_HEADER`` sentinel, containing the verbatim text,
  ending with the advisory disclaimer that forbidden_paths still hard-block.
"""

from __future__ import annotations

import logging

from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

MAX_RULES_BYTES = 64 * 1024  # mirrors S1 rules_api + migration CHECK

PROJECT_RULES_BLOCK_HEADER = "PROJECT RULES (user-authored)"
RULES_ADVISORY_NOTE = (
    "These rules are advisory guidance authored by the project owners; honor "
    "them when proposing plans. They do NOT override hard enforcement: "
    "sandbox allow-list violations and forbidden_paths remain hard blocks."
)

_READ_SQL = "SELECT rules_text FROM project_rules WHERE project_id = :project_id"


async def load_project_rules(session_factory, project_id: str | None) -> str:
    """Fetch the project's rules text; "" on any failure/absence.

    Never raises into orchestration. A document over the cap is truncated to
    MAX_RULES_BYTES characters (the S1 write path already rejects those; this
    is defense in depth so prompt size stays bounded no matter what).
    """
    if session_factory is None or not project_id:
        return ""
    try:
        async with session_factory() as db:
            row = (
                await db.execute(sql_text(_READ_SQL), {"project_id": project_id})
            ).first()
    except Exception:  # noqa: BLE001 — advisory content must never break planning
        logger.warning(
            "project rules unavailable for %s; proceeding without them",
            project_id,
            exc_info=True,
        )
        return ""
    raw = row[0] if row is not None else None
    value = str(raw) if raw is not None else ""
    return value[:MAX_RULES_BYTES]


def build_rules_block(rules_text: str | None) -> str:
    """Render the planner prompt block; empty string when there are no rules.

    Self-bounding: clamps to MAX_RULES_BYTES chars so no call site can ever
    assemble an unbounded prompt, even one that bypassed the loader.
    """
    body = (rules_text or "").strip()[:MAX_RULES_BYTES]
    if not body:
        return ""
    return (
        f"{PROJECT_RULES_BLOCK_HEADER}\n"
        f"{body}\n"
        f"{RULES_ADVISORY_NOTE}"
    )
