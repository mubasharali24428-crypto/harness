"""X-07 standing KPI: blind-approval rate, split by the rendering the reviewer used.

Source: Section 1's audit_log (raw parameterized SQL like decision_context.py), two decision
surfaces with the SAME semantics:

* PR review — ``review.approved`` / ``review.changes_requested`` (this section, since 2026-11-24).
* Shared-run collab approvals — ``approval.approved`` / ``approval.rejected`` (section4
  collab.py, since Sprint 5 Task 32, 2026-11-25).

An approval row carries ``teamId``, ``rendering`` and ``viewedDetail`` (was the diff on screen
before Approve, measured by the client; ``None`` or absent = not measured). Collab rows written
before Task 32 have ``teamId`` but no ``viewedDetail``: they count as unknown, never blind.

* blind      = an approval whose ``viewedDetail`` is ``False``.
* rate       = blind / approvals-with-a-signal; ``None`` below MIN_SAMPLE ("insufficient data").
* unknown    = approvals without a signal (older clients, API callers): reported, never blind.
* Rows without ``teamId`` (before this change) are not attributable to a team and are skipped.
No user ids leave this function: counts only.

ponytail: scans the newest WINDOW review rows and filters the team in Python; add a team_id
index/column on audit_log if review volume makes that slow. No cache (bounded query).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from .decision_context import RENDERINGS

WINDOW = 500
MIN_SAMPLE = 10
_APPROVED = ("review.approved", "approval.approved")
_ACTIONS = (*_APPROVED, "review.changes_requested", "approval.rejected")


def _bucket() -> dict[str, Any]:
    return {"decisions": 0, "approvals": 0, "withSignal": 0, "blind": 0, "unknownSignal": 0}


def _finish(b: dict[str, Any]) -> dict[str, Any]:
    b["rate"] = round(b["blind"] / b["withSignal"], 4) if b["withSignal"] >= MIN_SAMPLE else None
    return b


def aggregate(details: list[tuple[str, dict]], team_id: str) -> dict[str, Any]:
    by = {r: _bucket() for r in RENDERINGS}
    overall = _bucket()
    for action, d in details:
        if d.get("teamId") != team_id:
            continue
        rendering = d.get("rendering") if d.get("rendering") in RENDERINGS else "unknown"
        for b in (by[rendering], overall):
            b["decisions"] += 1
            if action in _APPROVED:
                b["approvals"] += 1
                seen = d.get("viewedDetail")
                if seen is None:
                    b["unknownSignal"] += 1
                else:
                    b["withSignal"] += 1
                    b["blind"] += seen is False
    return {"window": WINDOW, "minSample": MIN_SAMPLE, "overall": _finish(overall),
            "byRendering": {r: _finish(b) for r, b in by.items()}}


async def blind_approval_kpi(db: Any, team_id: str) -> dict[str, Any]:
    marks = ", ".join(f":a{i}" for i in range(len(_ACTIONS)))
    rows = (await db.execute(text(
        f"SELECT action, detail FROM audit_log WHERE action IN ({marks})"  # noqa: S608 - fixed marks
        " ORDER BY seq DESC LIMIT :n"),
        {**{f"a{i}": a for i, a in enumerate(_ACTIONS)}, "n": WINDOW})).all()
    details = [(r[0], r[1] if isinstance(r[1], dict) else json.loads(r[1] or "{}")) for r in rows]
    return aggregate(details, team_id)
