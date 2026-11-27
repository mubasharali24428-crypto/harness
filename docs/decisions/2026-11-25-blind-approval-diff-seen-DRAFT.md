# Decision draft: what counts as "diff seen" for the blind-approval KPI

**Date:** 2026-11-25 · **Status:** APPLIED, owner-confirmable. The owner did not object to the
recommendation; a written "confirmed" from the owner turns this from DRAFT into a decision.
**Context:** merging `master` (R4 "since last iteration", PR 29) into PR 27 (`w28-s2-sprints1-4`,
Task 18 / Task 32 blind-approval KPI). This was the only code conflict in that merge.

## The question

Task 18 (X-07) sends `diffSeen` with every Approve from the run page's review pane: true when the
diff came on screen (IntersectionObserver, 25% visible) before the click. Section 8 counts an
approval as blind when `viewedDetail` is `false`.

R4 changed the Diff pane: it now opens on **"Since last iteration"** (only the newest checkpoint's
hunks) and the reviewer switches to **"Full pull request"** on request. So: does seeing only the
since-last hunks count as "diff seen"?

## Rule applied

**Count "diff seen" only for a view of the whole change:**

1. the **Full pull request** scope was on screen, or
2. the **Since last iteration** scope was on screen **and it covered every hunk of the full PR**
   (every PR file is in the since-last view, and every PR hunk's added/removed lines appear as a
   hunk of the same file; an empty or unloaded PR diff never counts).

A reviewer who saw only the newest hunks of a multi-iteration change has not seen the whole
change, so an Approve in that state reports `diffSeen: false` and counts as blind.

**Why:** the KPI exists to catch approvals made without looking at the whole change. Counting the
partial view would flatter the metric and weaken the trust claim. This is the stricter choice and
is easy to loosen later (one function).

**Alternative not taken:** count either view. Better-looking numbers, weaker guarantee.

## Where it lives

- `section7/src/lib/diffScope.ts`: `sinceLastCoversFullPr(prDiff, files)` and
  `diffViewCountsAsSeen(scope, coversAll)`, pure; unit tests in
  `section7/tests/unit/diffScope.test.ts`.
- `section7/src/components/workspace/IterationDiffScope.tsx`: new optional `onIterationFiles`
  prop reports the since-last files once loaded.
- `section7/src/components/workspace/ReviewPane.tsx`: the observed element (`diffRef`) wraps the
  whole `IterationDiffScope`; the observer only arms while `diffViewCountsAsSeen` is true and is
  re-created when that flips, so switching to Full PR while the pane is already on screen is
  measured. The wrapper carries `data-diff-counts` for browser checks.

## Not proven yet

- Unit-tested only (vitest, cloud). No browser run of the KPI path with R4 yet.
- Blind-approval rate itself stays UNPROVEN: no pilot data.

## To loosen later

Change `diffViewCountsAsSeen` to return true for `"since-last"` as well. Nothing else moves.
