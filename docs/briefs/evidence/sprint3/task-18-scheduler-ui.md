# Task 18 — Scheduler proposal UI (read-only)

- Date (UTC): 2026-11-24
- Status: **done** — display only, no dispatch/apply/execute wiring.

## Environment contract

- Server flag: `HARNESS_SCHEDULER_PROPOSALS=1` enables `GET/POST /api/v1/orchestrator/runs/{runId}/scheduler-proposals`.
  It is **off by default**; with it off the endpoint answers 404 `FEATURE_OFF`. Nothing in this UI turns it on.
- Solver path: `HARNESS_W31_ROOT` points at the W31 experiment directory. Missing root or OR-Tools →
  503 `SCHEDULER_UNAVAILABLE`; there is no silent fallback.
- Access: admin only (403 `ROLE_DENIED` otherwise); foreign runs are 404 without an oracle.
- On 404 / 403 / 503 / empty `latest`, the panel renders nothing — no error banner on an ordinary run page.

## What was added

- `collabApi.ts`: `getSchedulerProposal(runId, token)` + `SchedulerProposal` types (wire shape from
  `scheduler_proposal._view`: `schema`, `proposal.placements[]`, `violations`, `valid`, `proposalOnly`,
  `requiresHumanApproval`, `createdBy`).
- `schedulerProposalCard.tsx`: tier breakdown (hot/warm/cold counts), validator status + violations,
  and the banner "Proposal only — nothing was dispatched. A human must approve before any change."
- `sharedRunPanel.tsx`: admin-only read in the container (same pattern as the `me/rendering` read),
  optional `schedulerProposal` prop, rendered above Pending decisions only when a proposal exists.

## Verification

```
cd section4 && ./.venv/bin/python -m pytest tests/unit -q -k scheduler   → 4 passed
cd section7 && npx vitest run                                            → 121 files / 1233 tests
cd section7 && npx tsc --noEmit                                          → clean
```

The card test explicitly asserts there is no `<button>`, link or dispatch/apply aria-label, and that
the proposal-only banner is present.
