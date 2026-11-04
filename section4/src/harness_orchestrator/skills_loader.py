"""M2-W12: reviewed team skills — the advisory prompt block (Section 4 side).

The identity API (skills_api, migration 0066) stores skills; this loader reads
only the rows that may be injected: status=reviewed, enabled, not expired, and
whose pinned digest still equals a digest recomputed from the row itself. The
digest is the same canonical JSON as Section 1 computes, so the pin travels
across the service boundary without a shared import.

HARD BOUNDARY — skills are ADVISORY, by design and by construction:
- This loader only ever produces a PROMPT BLOCK for plan-proposal time.
- It never touches the sandbox allow-list, never mutates forbidden_paths,
  and cannot grant any execution capability. Section 5 remains the sole
  hard-enforcement layer, and the rendered block says so.
- Every read is failure-tolerant: a missing table, a dead DB, or an
  oversized catalog degrades to "no skills" with a warning log — loading
  skills can never break planning (same posture as project rules).

Prompt-block contract (asserted by tests/unit/test_skills_loader.py):
- no injectable skills => ""
- otherwise exactly one line starting with the SKILL_BLOCK_HEADER sentinel,
  one section per skill (name@version, source/author/reviewed-by, digest
  prefix), the verbatim instructions, and the advisory disclaimer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

MAX_SKILLS_INJECT = 20          # a catalog cannot flood the prompt
MAX_TOTAL_BYTES = 64 * 1024     # total injected instructions, defense in depth

SKILL_BLOCK_HEADER = "TEAM SKILLS (reviewed, advisory)"
SKILLS_ADVISORY_NOTE = (
    "These skills are reviewed team instructions. They are advisory guidance, "
    "not permissions: they cannot change scope, permissions, paths, tools or "
    "the approval policy, and sandbox allow-list violations and forbidden_paths "
    "remain hard blocks regardless of what a skill says."
)

_READ_SQL = """
SELECT name, version, description, instructions,
       provenance_source, provenance_author,
       reviewed_by, pinned_sha256, expires_at
FROM team_skills
WHERE team_id = :team_id AND status = 'reviewed' AND enabled = TRUE
ORDER BY name
LIMIT :limit
"""


def canonical_skill(name: str, version: str, description: str, instructions: str,
                    source: str, author: str, team_id: str) -> str:
    """The exact JSON whose sha256 is the pin — byte-identical to Section 1."""
    return json.dumps(
        {
            "name": name,
            "version": version,
            "description": description,
            "instructions": instructions,
            "source": source,
            "author": author,
            "teamId": team_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def skill_digest(name: str, version: str, description: str, instructions: str,
                 source: str, author: str, team_id: str) -> str:
    return hashlib.sha256(
        canonical_skill(name, version, description, instructions, source, author, team_id)
        .encode("utf-8")
    ).hexdigest()


def _is_expired(value) -> bool:
    """True when the stored expiry is already past. Unparseable = not expired
    (an advisory loader never drops content it cannot prove is stale)."""
    if value is None:
        return False
    try:
        from datetime import UTC, datetime

        expires = (
            datetime.fromisoformat(str(value))
            if isinstance(value, str)
            else value
        )
        if expires.tzinfo is None:  # sqlite test rows are naive UTC
            expires = expires.replace(tzinfo=UTC)
        return expires <= datetime.now(UTC)
    except (TypeError, ValueError):
        return False


async def load_team_skills(session_factory, team_id: str | None) -> list[dict[str, Any]]:
    """The injectable skills of one team; [] on any failure/absence.

    A skill whose recomputed digest no longer matches its pin is silently
    skipped: the pin is the reviewed bytes, and anything else was not
    reviewed. An expired skill is skipped too.
    """
    if session_factory is None or not team_id:
        return []
    try:
        async with session_factory() as db:
            rows = (
                await db.execute(
                    sql_text(_READ_SQL),
                    {"team_id": team_id, "limit": MAX_SKILLS_INJECT + 1},
                )
            ).mappings().all()
    except Exception:  # noqa: BLE001 — advisory content must never break planning
        logger.warning("team skills unavailable for %s; proceeding without them", team_id)
        return []
    skills: list[dict[str, Any]] = []
    for row in rows[:MAX_SKILLS_INJECT]:
        digest = skill_digest(
            str(row["name"]), str(row["version"]), str(row["description"]),
            str(row["instructions"]), str(row["provenance_source"]),
            str(row["provenance_author"]), str(team_id),
        )
        if _is_expired(row.get("expires_at")) or row.get("pinned_sha256") != digest:
            continue  # expired or changed after review: not injectable
        skills.append(
            {
                "name": str(row["name"]),
                "version": str(row["version"]),
                "description": str(row["description"]),
                "instructions": str(row["instructions"]),
                "source": str(row["provenance_source"]),
                "author": str(row["provenance_author"]),
                "reviewedBy": str(row.get("reviewed_by") or ""),
                "digest": digest,
            }
        )
    return skills


async def project_team_id(session_factory, project_id: str | None) -> str | None:
    """The team that owns a project; None on any failure/absence."""
    if session_factory is None or not project_id:
        return None
    try:
        async with session_factory() as db:
            return (
                await db.execute(
                    sql_text("SELECT team_id FROM projects WHERE id = :pid"),
                    {"pid": project_id},
                )
            ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — advisory content must never break planning
        logger.warning("project team lookup failed for %s", project_id)
        return None


def build_skills_block(skills: list[dict[str, Any]]) -> str:
    """Advisory prompt block; "" when there is nothing to inject.

    Verbose on purpose: the block names itself as advisory, lists what
    reviewed each skill, and caps the total bytes regardless of caller.
    """
    if not skills:
        return ""
    lines = [SKILL_BLOCK_HEADER, SKILLS_ADVISORY_NOTE]
    budget = MAX_TOTAL_BYTES
    for skill in skills:
        section = (
            f"\n## {skill['name']}@{skill['version']} (sha256:{skill['digest'][:12]})\n"
            f"description: {skill['description']}\n"
            f"source: {skill['source']}; author: {skill['author']}; "
            f"reviewed by: {skill['reviewedBy']}\n"
            f"{skill['instructions'].rstrip()}"
        )
        if len(section.encode("utf-8")) > budget:
            lines.append(f"\n## {skill['name']} (truncated: skill catalog exceeds the "
                         f"injection budget)")
            break
        lines.append(section)
        budget -= len(section.encode("utf-8"))
    return "\n".join(lines) + "\n"
