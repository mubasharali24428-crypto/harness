# Task 27 — Collab approve blind-approval signal (X-07 KPI)

- Date (UTC): 2026-11-24
- Status: **done (client signal wired; server seam gap documented)**

## What changed

- `section7/.../sharedRunPanel.tsx` measures diff visibility on the **shared-run collab approval**
  path with an `IntersectionObserver` at **threshold 0.25**, the same threshold as
  `ReviewPane.tsx`. The observed element is a wrapper carrying `data-approval-diff=<approvalId>`
  around the `<pre aria-label="Proposed change">` (the existing `<pre>` markup assertion is unchanged).
- The measured value is passed from `SharedRunView.onDecide` to `decideApproval` on **Approve**.
  When the proposal has no diff, the signal is omitted rather than reported as a blind approval
  (matches section8's `viewedDetail: null` = unknown, never blind).
- `decideApproval` adds `viewed_detail` to the collab decision body only when measured.

## Documented seam gap (not invented around)

`section4/src/harness_orchestrator/api.py::ApprovalDecision` accepts only `approve` and `digest`;
the collab decision writes a collab approval row/audit, **not** a section8 review decision. The X-07
KPI (`section8/src/harness_review/kpi.py`) aggregates `viewedDetail` from section8 PR-review
approvals only. Therefore the new field is currently **ignored by section4** and does **not** enter
the blind-approval KPI. Per the task, no second audit store and no new section4→section8 coupling was
invented; closing that seam needs a separate owner/gate decision. The ReviewPane PR-review path keeps
its existing, genuinely KPI-counted signal.

## Verification

```
cd section7 && npx vitest run                  → 121 files / 1238 tests passed
cd section7 && npx tsc --noEmit                → clean
```

New/updated tests prove: `decideApproval` sends `{approve, digest, viewed_detail:false}` when
measured false, `true` when measured true, and omits the field otherwise; the collab card renders
the measured diff hook; keyboard Approve still parks focus and now receives the measured signal.
`section8` KPI tests are untouched and still pass in their own suite (no signal semantics changed).
