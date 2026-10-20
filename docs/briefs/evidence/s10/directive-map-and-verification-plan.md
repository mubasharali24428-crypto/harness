# Session 10 directive map and verification plan

Date: 2026-10-19. This map is an initial Session 10 assessment. It separates local documentation
and automated checks from evidence that requires real users, deployed services or external review.

## Part 03 — pilot success

| Directive | Existing control/evidence | Remaining state |
|---|---|---|
| 001 repeatable real-user pilot | `docs/briefs/multiplayer-pilot-release-plan.md` defines supervised scope | No real pilot has run; blocked. |
| 002 task completion and accepted outcomes | `section4/pilot_metrics.py`, manual reviewer protocol | Baseline inputs are now validated before comparison; tool acceptance is still an audit-row proxy and no real cohort exists. |
| 003 elapsed workflow time | baseline guide and manual sheet | No measured comparable baseline/pilot pair. |
| 004 repeat usage | pilot metrics can aggregate repeated team runs | No real usage evidence. |
| 005 redirection frequency/reasons | event/audit sources and pilot report | No human-coded reason collection/report yet. |
| 006 approval latency/blocked time | run/event/approval timestamps and dashboard | No real pilot latency evidence. |
| 007 rework/failures/abandonment/friction | audit, lifecycle and manual feedback guide | Participant feedback remains uncollected. |
| 008 method before interpretation | `docs/briefs/pilot-baseline-guide.md` | Method and shape validation are documented; actual sample is missing. |
| 009 no invented traction/ROI | fictional example is labeled fictional | Keep the real-pilot gate blocked. |
| 010 limitations and missing evidence | release plan and baseline guide | Consolidate gaps in S10 readiness report. |

## Part 56 — accessibility, localization and clients

| Directive | Existing control/evidence | Remaining state |
|---|---|---|
| 001 accessibility target | `docs/accessibility.md`, focus/keyboard and announcement tests | Target is documented; no browser/axe/contrast/screen-reader evidence. |
| 002 live announcements | throttled announcer and unit tests | Screen-reader behavior unverified. |
| 003 responsive layout | AppShell responsive test | Real device/reflow test absent. |
| 004 browser/client policy | New `docs/runbooks/client-support.md` | Automated browser matrix not run. |
| 005 dates/numbers/status localization | locale-aware Intl date/number helpers | Most interface strings remain English. |
| 006 approval language | English approval copy and collaboration tests | Non-English copy not available. |
| 007 reduced motion | CSS media rule and helper tests | OS/browser behavior not manually checked. |
| 008 keyboard approvals/redirect/handoff | handler-level keyboard tests | Real keyboard navigation not run. |
| 009 long history accessibility | bounded render window and keyboard controls | Real assistive-technology verification absent. |
| 010 unsupported-client fallback | new client support policy | Graceful behavior must be confirmed per browser; no broad support claim. |

## Part 59 — rollout, migration and compatibility

| Directive | Existing control/evidence | Remaining state |
|---|---|---|
| 001 feature flags/eligibility | `HARNESS_PILOT_TEAMS`, pilot runbook | Empty/unset flag opens access; pilot must explicitly configure a non-empty list. |
| 002 order/backfill/rollback | forward-only migrations, drift gate, rollback and PITR runbooks | No fresh current-chain PostgreSQL migration/restore run. |
| 003 API/event/client compatibility | `/api/v1`, frozen contracts, one-release deprecation, schema-version checks | Release matrix and client communication consolidated in new runbook. |
| 004 single-user to shared sessions | collaboration routes/roster and migration 0035 | Existing history/access compatibility must be exercised on deployed data. |
| 005 preserve history/access semantics | additive collaboration storage and team-scoped checks | No live upgrade verification. |
| 006 shadow reads/dry runs | drift checks and pilot gates | Policy-specific shadow-read comparison not documented for all gates. |
| 007 prerequisites | pilot/rollback/security runbooks | Consolidate go/no-go list. |
| 008 release notes/admin notice | runbook checklist | No actual release or customer communication. |
| 009 rollback with new events/schema | forward-only/PITR policy in `rollback.md` | Combined image/schema rollback has not been rehearsed as one live sequence. |
| 010 health/stop expansion | alert rules and pilot stop conditions | Alert delivery and live expansion monitoring remain unverified. |

## Part 60 — roadmap

`docs/roadmap.md` separates committed phases from exploratory ecosystem work and specifies phase
exit criteria. Revisit priorities only after real pilot data and customer interviews; neither exists.
Part 60 remains verified for the planning artifact, not for product completion.

## Part 61 — full-product acceptance

The capability matrix is the requirement owner/state/evidence ledger. Final readiness remains
blocked because it requires all critical rows verified and current cross-domain evidence; external
gates include live pilot, real object-store PITR, independent security/counsel review, and live
provider drills. The readiness decision and current blockers are recorded in `readiness-report.md`.

## Targeted checks

Section 7 accessibility/reduced-motion/responsive tests, TypeScript typecheck, pilot-metric tests,
and migration drift-gate checks are recorded in `verification.md`. If Docker, a browser stack, or
external services are unavailable, report that and leave the associated row partial/blocked.
