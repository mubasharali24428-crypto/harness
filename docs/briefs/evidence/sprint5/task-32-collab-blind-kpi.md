# Task 32 — Collab audit context + blind-approval KPI (G-X07-3, part 2 of 2)

- Date (UTC): 2026-11-25
- Base: `c5bcd3f` (Task 31) on `w28-s2-sprints1-4` (cloud, no Docker, no Postgres)
- Status: **Verified (unit, SQLite)**. Live KPI fetch is Task 33 (owner Mac).

## What changed

- `section4/src/harness_orchestrator/collab.py`
  - `member_rendering(factory, team_id, user_id)`: raw parameterised read of
    `team_members.approval_rendering` (migration 0077) — the same column section1's `/me/rendering`
    doors write and section8 `decision_context.reviewer_rendering` reads. Closed label set
    `technical|plain|decision|unknown`; any miss/error → `unknown`. Own session, before the decision
    transaction (a failed statement would otherwise abort the approval on Postgres).
  - `decide_approval`: the `approval.approved` audit row gains `rendering` + `viewedDetail`
    (`teamId` was already written by `_audit`); `approval.rejected` gains `rendering`.
  - `_approval_change(..., audit_only=)`: these keys go to the audit row only, not to the
    collaborator event stream.
- `section8/src/harness_review/kpi.py`: `_ACTIONS` = `review.approved`, `approval.approved`,
  `review.changes_requested`, `approval.rejected`; both `*.approved` count as approvals. Semantics
  unchanged: blind = `viewedDetail is False`; absent/`null` = unknown, never blind; rate `null`
  below 10 measured approvals; team filter on `teamId`; counts only.
- Delta documented in `docs/briefs/evidence/10x/x07-backend-preference-2026-11-24.md`.

## Verification

```
section4: pytest tests/unit/test_collab_viewed_detail.py      → 13 passed
section4: pytest tests/ -k "collab or approval"               → 276 passed, 8 skipped
section4: ruff check src tests                                → All checks passed!
section8: pytest tests/ (make test-section8 recipe)           → 111 passed
section8: ruff check src tests                                → All checks passed!
```

## Honest limits

- PG-gated collab tests (`*_live_pg.py`) were skipped: no Postgres in this container. Task 38 /
  owner Mac `make test-all` covers them.
- Real blind-approval rate on pilot tasks: **not measured** (no pilot decisions yet).
- The section7 admin tile reads the same endpoint; its copy still says "approvals" generically,
  which is now accurate for both surfaces. No UI change in this task.
