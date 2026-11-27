"""X-07 (10X Wave B) — decision-time rendering context for the blind-approval KPI.

When a reviewer approves or requests changes, the service records WHICH rendering
they were working from (technical | plain | decision | unknown). That label is the
denominator split the X-07 kill criterion needs: "if blind-approval rate does not
fall on pilot tasks after Wave B, tone rendering is revisited" — without it, the
rate can only be measured in aggregate and the rendering hypothesis stays untestable.

Where the preference lives: ``team_members.approval_rendering`` (migration 0077,
written by Section 1's ``PUT /api/v1/me/rendering`` and the admin door). Reading a
shared-schema column with raw parameterized SQL is the same law store.py and
team_thresholds.py follow — Section 8 never imports harness_identity.

Fail-open rule: absent row, missing column (pre-0077 schema), closed DB or any
surprise answers ``"unknown"`` — instrumentation is best-effort and must never
turn a working approval into a 500.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The closed label set. ``unknown`` covers: no row, pre-0077 schema, or any
# reader failure — an honest "we could not tell" instead of a guessed tone.
RENDERINGS: tuple[str, ...] = ("technical", "plain", "decision", "unknown")
_DEFAULT = "unknown"

_SELECT = (
    "SELECT approval_rendering FROM team_members "
    "WHERE team_id = :team_id AND user_id = :user_id"
)


async def reviewer_rendering(db: AsyncSession, team_id: str | None, user_id: str) -> str:
    """The rendering the reviewer had selected, or ``"unknown"`` when not knowable."""
    if not team_id or not user_id:
        return _DEFAULT
    try:
        row = (
            await db.execute(
                text(_SELECT), {"team_id": team_id, "user_id": user_id}
            )
        ).mappings().first()
    except Exception:  # noqa: BLE001 — telemetry context must never break a decision
        return _DEFAULT
    if row is None:
        return _DEFAULT
    value = str(row["approval_rendering"] or "").strip()
    return value if value in RENDERINGS[:3] else _DEFAULT
