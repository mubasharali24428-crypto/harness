"""I-03 / P-19 — the one-click compliance evidence pack (90-day plan W10).

An auditor for a design partner needs ONE artifact that answers the
AI-change-management questions — who approved what, what the tests said, what
it cost, whether the evidence was tampered with — without stitching together
paged export calls. The pack is that artifact: a single bounded JSON document
assembling, for the caller's team only,

  * ``manifest``         — provenance, row ranges/counts, explicit truncation,
                           and the retention statements an auditor asks first;
  * ``chainVerification``— the hash-chain verdict re-derived with the SAME SQL
                           function the trigger sealed rows by (PG-only; on a
                           lane without it the pack records ``performed: false``
                           and a reason — it never claims ``ok`` from a no-op);
  * ``auditTrail``       — the security audit rows, via ``audit_rows`` (the ONE
                           team-scope reader the export endpoint and the SIEM
                           pusher already share — a second copy of that rule
                           would eventually disagree with them);
  * ``collabEvents``     — the product event history of shared runs (the
                           "every change, approver" stream), column-scoped on
                           ``run_collab_events.team_id``.

Laws this module does not get to break:

* **Admin + step-up, same door as export.** The pack IS an export (more rows in
  one response than any single export page) — the route guard mirrors
  ``GET /audit/export`` exactly, and generating a pack is itself audited.
* **Truncation is said out loud.** A bounded response that claimed completeness
  would be the compliance-pack version of a fake ``ok``. The manifest carries
  the available counts next to the delivered ones, so an auditor can detect a
  cap without trusting us.
* **No new team-scope SQL.** The audit stream goes through ``audit_rows``; the
  collab stream is a direct ``team_id`` column filter, which is the same shape
  the export endpoint already uses for that stream.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit_rows
from .audit_chain import verify_window

# A pack that unbounded would be a way to pull the whole trail out through one
# call; a pack that hides the cap would be a lie. Delivered rows ≤ cap, and the
# manifest always states what was available.
PACK_ROWS_DEFAULT = 2000
PACK_ROWS_MAX = 10000
# The chain verdict is bounded exactly like POST /audit/verify: the response
# carries a per-row verdict, so an unbounded window would be an evidence dump.
PACK_VERIFY_ROWS = 1000

_RETENTION = {
    "erasure": (
        "a person's erasure pseudonymises, never deletes: audit_log and collab"
        " evidence keep opaque ids so held evidence stays intact"
    ),
    "retentionHold": (
        "a person who took part in a run under retention hold cannot be erased"
        " until the hold releases — the hold preserves that run's evidence"
    ),
    "chain": (
        "every audit row is sealed at commit by the audit_row_hash trigger and"
        " the trail is forward-only; edits and deletions break the chain"
    ),
}


def _detail_dict(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


async def _collab_rows(
    db: AsyncSession, team_id: str, *, after_seq: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Ascending collab rows + the team's TOTAL available count past ``after_seq``.

    The count is fetched in the same round trip shape the export endpoint uses
    for this stream (a direct ``team_id`` column filter — this table carries the
    team id as a column, unlike audit_log, whose scope rule lives in
    ``audit_rows``). One row beyond the cap answers ``hasMore``.
    """
    rows = (
        (
            await db.execute(
                text(
                    "SELECT seq, run_id, actor_user_id, kind, request_id, payload, ts"
                    " FROM run_collab_events WHERE team_id = :team AND seq > :after"
                    " AND kind <> 'guidance.delivered'"
                    " ORDER BY seq ASC LIMIT :n"
                ),
                {"team": team_id, "after": after_seq, "n": limit + 1},
            )
        )
        .mappings()
        .all()
    )
    total = int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM run_collab_events"
                    " WHERE team_id = :team AND seq > :after"
                    " AND kind <> 'guidance.delivered'"
                ),
                {"team": team_id, "after": after_seq},
            )
        ).scalar_one()
    )
    items = [
        {
            "seq": r["seq"],
            "runId": r["run_id"],
            "actor": r["actor_user_id"],
            "kind": r["kind"],
            "requestId": r["request_id"],
            "payload": _detail_dict(r["payload"]),
            "ts": str(r["ts"]) if r["ts"] is not None else None,
        }
        for r in rows[:limit]
    ]
    return items, total


async def build_audit_pack(
    db: AsyncSession,
    team_id: str,
    *,
    max_rows: int = PACK_ROWS_DEFAULT,
) -> dict[str, Any]:
    """Assemble the pack for ``team_id``. Bounded by ``max_rows`` per stream.

    Chain verification is best-effort by lane: the verdict functions are
    PostgreSQL-only, and a pack generated where they cannot run records
    ``performed: false`` with the reason instead of pretending.
    """
    max_rows = max(1, min(int(max_rows), PACK_ROWS_MAX))
    pack_id = uuid.uuid4().hex

    trail, audit_available = [], 0
    audit_rows_iter, _more = await audit_rows.read_audit_page(
        db, team_id, after_seq=0, limit=max_rows
    )
    trail = audit_rows_iter
    audit_available = await audit_rows.count_after(db, team_id, after_seq=0)

    # A deployment without the collab table fails the pack the same way
    # `stream=collab` export does (fail closed, say why) — a pack must never
    # quietly answer "no events" when it could not look.
    collab, collab_available = await _collab_rows(
        db, team_id, after_seq=0, limit=max_rows
    )

    have_chain = db.bind is not None and db.bind.dialect.name == "postgresql"
    chain: dict[str, Any]
    if have_chain:
        try:
            chain = await verify_window(db, after_chain_pos=0, max_rows=PACK_VERIFY_ROWS)
            chain["performed"] = True
            chain["coversAll"] = (
                int(chain.get("rowsChecked", 0)) >= audit_available
            )
        except Exception as exc:  # noqa: BLE001 — no verdict is better than a fake one
            chain = {
                "performed": False,
                "reason": f"{type(exc).__name__}: chain functions unavailable",
            }
    else:
        chain = {
            "performed": False,
            "reason": "chain verification needs the deployment's PostgreSQL database",
        }

    truncated = audit_available > len(trail) or collab_available > len(collab)
    return {
        "manifest": {
            "packId": pack_id,
            "generatedAt": datetime.now(UTC).isoformat(),
            "revision": os.environ.get("HARNESS_IMAGE_TAG") or None,
            "teamId": team_id,
            "maxRowsPerStream": max_rows,
            "rowCounts": {
                "auditTrailDelivered": len(trail),
                "auditTrailAvailable": audit_available,
                "collabEventsDelivered": len(collab),
                "collabEventsAvailable": collab_available,
            },
            "ranges": {
                "auditTrailSeq": [trail[0]["seq"], trail[-1]["seq"]] if trail else None,
                "collabSeq": [collab[0]["seq"], collab[-1]["seq"]] if collab else None,
            },
            "truncated": truncated,
            "retention": _RETENTION,
        },
        "chainVerification": chain,
        "auditTrail": trail,
        "collabEvents": collab,
    }
