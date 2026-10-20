# Session 10 — rollout, accessibility, roadmap and final acceptance

Date: 2026-10-19. Scope: master-prompt parts 56, 59, 60, pilot-measurement tooling for 03,
and the readiness ledger/report for 61. Start from `path-to-9-plan.md`, the capability matrix,
and the current checkpoint. Preserve pre-existing working-tree changes.

## Required outcomes

1. Reconcile all 10 directives in parts 56, 59, 60, 03 and 61 against shipped behavior and
   evidence. Distinguish tool readiness from live pilot outcomes.
2. State supported client/browser and locale policy without claiming untested conformance.
   Verify accessible collaboration behavior with focused automated checks; list the manual
   browser, screen-reader, contrast and reflow checks that remain.
3. Consolidate staged rollout, migration order/backfill, compatibility windows, feature flags,
   release communication, health monitoring and rollback constraints into an operator runbook.
4. Reuse the existing pilot baseline template and metrics tool. Do not fabricate pilot results;
   preserve the ≥5 comparable tasks per side gate and record the exact evidence still needed.
5. Produce a cross-domain readiness decision from the capability matrix. Part 61 stays blocked
   until all required rows and critical evidence gates are satisfied.
6. Update matrix rows 03, 56, 59, 60 and 61, the S10 evidence and current checkpoint.

## Verification and release rules

- Run only targeted tests, type checks and lint for touched areas; record commands and results.
- No production deploy, migration against a shared database, broad tenant rollout, real pilot,
  external provider drill, or destructive rollback is part of this local session.
- Mark browser, assistive-technology, PostgreSQL, container, live pilot, pentest, counsel and
  provider evidence only when those checks actually run.
- Do not commit or push the mixed working tree.

## Exit

Session 10 may report a readiness decision, including NO-GO. It must not declare the full product
ready while any critical matrix row remains partial/blocked or any external evidence gate is open.
