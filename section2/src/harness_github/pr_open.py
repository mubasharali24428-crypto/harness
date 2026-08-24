"""M12-W2 lane E4a (pair contract W2-C2): the prId producer.

When a draft PR is opened for a run (Section 2's ``openDraftPR`` path), this
module persists the PR against its run and announces it on the event bus so
the frontend review page can consume it (lane E4b):

1. Insert the canonical ``pull_requests`` row (project, run, GitHub number,
   url) — previously nothing in the platform wrote this table; Section 8 only
   reads it.
2. Link it on the run via ``agent_runs.pr_id`` (migration 0017).
3. Emit ``pr_opened`` on the shared bus with payload exactly::

       {"runId": str, "prId": str, "url": str}

   The frozen contracts enum already carries EventType.PR_OPENED; S1 owns
   delivery (agent_events persistence + pg_notify fan-out), so a plain insert
   through the EventBus reaches every subscriber and survives restarts.

All writes are best-effort and never fail the PR open itself: a run without a
runId, or without an agent_runs row, still opens its PR — it just skips the
parts it cannot bind. Secrets are never touched here, let alone logged.
"""

import uuid

from harness_contracts.events import EventType
from sqlalchemy import text

# The one wire shape W2-C2 promises E4b. Kept in ONE place; both the payload
# we emit and the response key we add to openDraftPR derive from it.
PR_OPENED_EVENT = "pr_opened"

_SET_RUN_PR_SQL = text("UPDATE agent_runs SET pr_id = :pr_id WHERE id = :run_id")

_INSERT_PR_SQL = text(
    """
    INSERT INTO pull_requests (id, project_id, run_id, github_pr_number,
                               pr_url, summary_md, status)
    VALUES (:id, :project_id, :run_id, :github_pr_number, :pr_url, '', 'open')
    """
)

_SELECT_PROJECT_SQL = text(
    "SELECT project_id FROM agent_runs WHERE id = :run_id"
)

# The platform's run branch convention: runs work on ``agent/<runId>``
# (see section8 conftest seed: agent_runs.branch = 'agent/demo'). The PR-open
# route carries no runId field, so the branch name is the producer's only
# binding signal — anything not shaped like that convention is simply an
# unbound (human/manual) PR open.
_BRANCH_RUN_PREFIX = "agent/"


def run_id_from_branch(branch: str | None) -> str | None:
    """Derive the runId from a run's working branch (``agent/<runId>``)."""
    if not branch or not branch.startswith(_BRANCH_RUN_PREFIX):
        return None
    run_id = branch[len(_BRANCH_RUN_PREFIX):].strip()
    return run_id or None


async def link_run_to_pr(db, run_id: str | None, pr_number: int, pr_url: str) -> dict | None:
    """Persist the PR row + run link for a just-opened draft PR.

    Returns the producer payload ``{"runId", "prId", "url"}`` when the run was
    bound (caller emits it), else None (unbound runs have no prId to announce).
    Commits once, after both writes, so the pair lands atomically.
    """
    if not run_id:
        return None
    try:
        # NOTE: the caller's session may have auto-begun a transaction already
        # (token/repo lookups run through it first), so this must stay plain
        # execute + commit — an explicit db.begin() here would raise
        # InvalidRequestError("already begun") on the routed path.
        row = (
            await db.execute(_SELECT_PROJECT_SQL, {"run_id": run_id})
        ).mappings().first()
        if row is None:
            # No agent_runs parent (e.g. bare ad-hoc opens): FK forbids
            # both writes. Honest skip — the PR itself is unaffected.
            return None
        project_id = row["project_id"]
        pr_id = str(uuid.uuid4())
        await db.execute(
            _INSERT_PR_SQL,
            {
                "id": pr_id,
                "project_id": project_id,
                "run_id": run_id,
                "github_pr_number": int(pr_number),
                "pr_url": pr_url,
            },
        )
        await db.execute(_SET_RUN_PR_SQL, {"pr_id": pr_id, "run_id": run_id})
        await db.commit()
        return {"runId": run_id, "prId": pr_id, "url": pr_url}
    except Exception:  # noqa: BLE001 — bookkeeping must never break the PR open
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 — dying session must not mask the error
            pass
        return None


def opened_payload(payload: dict | None) -> dict:
    """Extract the exact W2-C2 wire payload from a link_run_to_pr result."""
    if not isinstance(payload, dict):
        return {}
    return {
        "runId": payload.get("runId"),
        "prId": payload.get("prId"),
        "url": payload.get("url"),
    }


async def emit_pr_opened(bus, project_id: str, payload: dict) -> None:
    """Publish ``pr_opened`` through the S1 EventBus (persist + fan-out).

    ``payload`` must already be the wire dict {runId, prId, url}; the bus
    persists it into agent_events.payload and fans out the envelope.
    Best-effort by construction: delivery failure must never break the PR
    open that already succeeded against GitHub.
    """
    if bus is None or not project_id:
        return
    try:
        await bus.publish(
            EventType.PR_OPENED,
            project_id,
            opened_payload(payload),
            run_id=payload.get("runId"),
        )
    except Exception:  # noqa: BLE001 — delivery must never break orchestration
        pass


async def record_pr_opened(
    db, bus, project_hint: str, run_id: str | None, pr: dict
) -> dict | None:
    """Full producer step: persist the link, then announce it on the bus.

    ``db`` is a fresh AsyncSession from the caller's session factory (this
    function closes/commits it); ``bus`` is the duck-typed S1 EventBus or
    None when unwired (bare unit mode). Returns the emitted wire payload or
    None when the run could not be bound.
    """
    bound = await link_run_to_pr(db, run_id, int(pr["number"]), str(pr["url"]))
    if bound is None:
        return None
    await emit_pr_opened(bus, project_hint, bound)
    return bound
