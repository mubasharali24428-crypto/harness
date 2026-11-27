# Task 26 — Phase 7: C-05 dependency status + C-07 visible disagreement

- Date (UTC): 2026-11-24
- Status: **done**

## Backend (`section4/src/harness_orchestrator/assignment_plan.py`)

- **C-05:** `GET /projects/{projectId}/assignment-plans/mine` now adds
  `dependsOnStatus: {dependencyKey: "ready" | "waiting"}` per task. Status is computed from the
  latest **approved** plan graph only: `ready` when the prerequisite task is assigned to the
  caller, `waiting` otherwise. Only dependency keys the caller already sees in their own
  `dependsOn` are sent — no other member's title, body, task list or user id.
- **C-07:** `POST .../review` with `decision: "reject"` now requires a non-blank `note`
  (422 `NOTE_REQUIRED`); the note is stored on the new version and returned in list/history.

## Frontend (`section7/app/projects/[projectId]/planning/`)

- Admin version history renders the note in its own labelled line
  (`data-testid="note-<version>"`, "Review note:"), not buried in the byline.
- Reject is disabled until the note has non-whitespace text, with an inline explanation and
  `aria-describedby` pointing at the note hint. Approve stays note-optional.
- Member tasks show `key: ready|waiting` for each dependency, with a fallback of `waiting`
  when the server did not send a status.

## Verification

```
cd section4 && ./.venv/bin/python -m pytest tests/unit/test_assignment_plan.py -q   → 7 passed
cd section7 && npx vitest run app/projects/[projectId]/planning/planning.test.tsx    → 10 passed
cd section7 && npx tsc --noEmit                                                       → clean
```

New tests prove the no-oracle boundary explicitly: `sec`'s response never contains `ui`'s task
body, and `ui`'s never contains either of `sec`'s bodies, while readiness is correct for both.

## Out of scope (unchanged)

C-06 agent assignments and scheduler dispatch are not built. No plan approval starts work (D-05).
