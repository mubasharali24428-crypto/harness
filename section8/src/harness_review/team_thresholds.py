"""M15-G6b — team reviewer-threshold resolution for Section 8.

Reads the team's stored thresholds document from Section 1's frozen
``team_settings`` table (migration 0024) and hands the heuristic a usable
ReviewerThresholds no matter what: absent row → defaults; NULL value →
defaults; malformed JSON / wrong types / negative numbers → defaults. The
reviewer NEVER fails open into an unusable state because of config.

Section isolation law: this module talks to the shared schema with raw
parameterized SQL exactly like store.py — it must never import
harness_identity (S1) directly.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .heuristic import (
    DEFAULT_REVIEWER_THRESHOLDS,
    REVIEWER_THRESHOLD_KEY,
    ReviewerThresholds,
    ThresholdSource,
)


async def get_team_reviewer_thresholds(
    db: AsyncSession, team_id: str | None
) -> tuple[ReviewerThresholds, str]:
    """Resolve the effective thresholds for a team.

    Returns ``(thresholds, source)`` where source is ``"team"`` only when a
    well-formed stored document was applied; every other path answers the
    documented defaults with source ``"defaults"``.
    """
    if not team_id:
        return DEFAULT_REVIEWER_THRESHOLDS, "defaults"
    row = (
        await db.execute(
            text(
                "SELECT value FROM team_settings "
                "WHERE team_id = :team_id AND key = :key"
            ),
            {"team_id": team_id, "key": REVIEWER_THRESHOLD_KEY},
        )
    ).mappings().first()
    if row is None:
        return DEFAULT_REVIEWER_THRESHOLDS, "defaults"
    return _parse_thresholds(row["value"])


def _parse_thresholds(raw: Any) -> tuple[ReviewerThresholds, ThresholdSource]:
    """Best-effort parse of the stored document; anything off → defaults."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return DEFAULT_REVIEWER_THRESHOLDS, "defaults"
        # Pydantic validates shape AND ranges (ge bounds); unknown keys are
        # ignored by the model, so forward-compatible junk can't poison us.
        parsed = ReviewerThresholds.model_validate(data)
    except Exception:  # noqa: BLE001 — any malformed config degrades to defaults
        return DEFAULT_REVIEWER_THRESHOLDS, "defaults"
    return parsed, "team"


def parse_stored_thresholds_for_admin(raw: Any) -> ReviewerThresholds:
    """Strict variant used by S1's admin PUT handler (raises on malformed).

    Lives here so the validation rules have exactly ONE definition shared by
    the writer (S1) and the reader (S8). Raises whatever Pydantic raises on
    invalid documents.
    """
    if isinstance(raw, (str, bytes, bytearray)):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("thresholds document must be a JSON object")
    return ReviewerThresholds.model_validate(raw)
