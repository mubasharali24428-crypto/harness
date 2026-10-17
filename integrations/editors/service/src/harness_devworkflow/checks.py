"""Pure merge-readiness evidence logic (F040).

This computes an evidence gate verdict for a code session. It is NOT a merge
action and never will be: the repository law is "no auto-merge anywhere".
Merging remains a human/provider-side action; this module only reports whether
the evidence trail supports asking for one.
"""

from __future__ import annotations

from typing import Any

MERGE_ACTION_DISPOSITION = "not-available-by-design"


def compute_merge_readiness(
    *,
    latest_change_set: dict[str, Any] | None,
    evidence: list[dict[str, Any]],
    current_fingerprint: str,
    comments_count: int,
    sandbox_configured: bool,
    checkpoint_sha: str | None,
) -> dict[str, Any]:
    """Build the merge-check report with explicit per-gate pass/fail detail."""
    gates: list[dict[str, Any]] = []

    pending_hunks = 0
    rejected_hunks = 0
    if latest_change_set:
        for f in latest_change_set.get("files", []):
            decisions = f.get("decisions", {})
            for hunk in f.get("hunks", []):
                decision = decisions.get(hunk["id"], {}).get("decision", "pending")
                if decision == "pending":
                    pending_hunks += 1
                elif decision == "reject":
                    rejected_hunks += 1

    if latest_change_set is None:
        gates.append(
            {
                "id": "review-decisions-complete",
                "ok": True,
                "detail": "no change set has been proposed in this session",
            }
        )
    else:
        gates.append(
            {
                "id": "review-decisions-complete",
                "ok": pending_hunks == 0,
                "detail": (
                    "every hunk has an accept/reject decision"
                    if pending_hunks == 0
                    else f"{pending_hunks} hunk(s) still pending a decision"
                ),
            }
        )
        gates.append(
            {
                "id": "change-set-applied",
                "ok": latest_change_set.get("status") == "applied",
                "detail": f"latest change set status: {latest_change_set.get('status')}",
            }
        )

    newest = evidence[0] if evidence else None
    if newest is None:
        gates.append(
            {
                "id": "verification-on-current-tree",
                "ok": False,
                "detail": "no verification evidence recorded; run a verify command",
            }
        )
    else:
        matches = newest.get("treeFingerprint") == current_fingerprint
        passed = newest.get("status") == "passed"
        if not matches:
            detail = "workspace changed after the last verification; re-run verify"
        elif not passed:
            detail = f"last verification status: {newest.get('status')}"
        else:
            detail = "verification passed and matches the current workspace"
        gates.append(
            {
                "id": "verification-on-current-tree",
                "ok": bool(matches and passed),
                "detail": detail,
            }
        )

    if sandbox_configured:
        gates.append(
            {
                "id": "sandbox-synced",
                "ok": checkpoint_sha is not None,
                "detail": (
                    f"latest sandbox checkpoint: {checkpoint_sha}"
                    if checkpoint_sha
                    else "no sandbox checkpoint observed for this change"
                ),
            }
        )

    verdict = "ready-for-human-review" if all(g["ok"] for g in gates) else "gaps-found"
    return {
        "verdict": verdict,
        "gates": gates,
        "counts": {
            "pendingHunks": pending_hunks,
            "rejectedHunks": rejected_hunks,
            "comments": comments_count,
        },
        "mergeAction": MERGE_ACTION_DISPOSITION,
        "note": (
            "Evidence only. Harness never merges: approving, merging and branch "
            "protection stay with humans and the Git provider."
        ),
    }
