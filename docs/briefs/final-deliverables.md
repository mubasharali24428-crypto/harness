# Final deliverables: Harness Multiplayer AI (master prompt parts 34 and 35)

Written 2026-10-20 (Session 11, cloud pass). This is an index. The matrix `capability-matrix.md` is the single source of truth for what
is built; this file points at it and at the evidence, and states the readiness decision. Nothing here is a claim beyond the evidence it
cites. Results are labelled **pass**, **fail** or **not verified**; "not verified" means nothing was run.

Counts (2026-10-21): verified 49 · implemented 2 · partial 8 · missing 0 · blocked 2.

## 1. Executive summary

The programme took 61 master-prompt parts toward a shared-run pilot product: several people watch, steer, approve and hand off one AI
coding run, with deny-by-default isolation, digest-bound approvals and an audit trail. Ten build sessions plus one cloud verification
session produced 44 verified rows, 3 implemented, 12 partial and 2 blocked; Session 12 raised that to 49 verified, 2 implemented, 8 partial. Rows 37, 52, 53, 58 and 61 and part 03 are capped at 8 or
blocked because they need an outside party (identity-provider and pen-test evidence, a real backup restore, counsel, a measured pilot).

**Readiness decision: NO-GO for broad rollout or full-product acceptance** (section 10). An operator-present rehearsal for named teams is
the most this evidence supports, and only after the pre-flight checks in section 7.

Two findings from the cloud pass change what can be trusted today (`evidence/s11/clean-checkout-gaps.md`):

* The pushed history does not import section 4 on a clean checkout: committed `engine.py` needs `container_runtime.py`, which was never
  committed. Every section 4 suite number reported from the owner's machine therefore describes their working tree, not a commit.
* A real PostgreSQL-only export bug was found and fixed (`evidence/s11/live-pg-search-export.md`).

## 2. Feature matrix

See `capability-matrix.md` (61 rows, one per part, each with its evidence). Grouped view of the current states:

| Area | Rows | State |
|---|---|---|
| Product, method, process | 01, 02, 15 to 19, 34, 35 | verified (`evidence/s11/process-parts.md`; practice rows state that in their limits) |
| Shared-run core: safety, approval, redirect, isolation, lifecycle, events | 04 to 11, 13, 14, 20 to 23, 27, 29, 30, 33, 36 | verified |
| Shared experience and end-to-end | 12, 24, 25, 26 | 12, 24 and 25 verified (12: Chromium check on synthetic data, no screen reader; 25: stand-in engine); 26 implemented |
| Identity, organisations, policy, workspaces, sessions | 37 to 40 | verified (37 capped at 8: live identity-provider evidence) |
| Presence, control, discussion, plans, agents, delivery, notifications, files, search | 41 to 48, 50 | verified, each with listed gaps |
| Integrations, governance, billing | 49, 51, 57 | 49 verified (Session 12); 51, 57 partial (residency, in-flight spend; Stripe is out of scope by owner direction) |
| Security, privacy, compliance, reliability, scale, observability | 28, 31, 52 to 55, 58 | 28 and 54 verified (Session 12); 31, 52, 53, 55, 58 partial (52, 53, 58 externally capped) |
| Release, rollout, accessibility | 32, 56, 59 | 32 implemented; 56 partial; 59 verified (Session 12 rollback rehearsal) |
| Roadmap | 60 | verified (planning artifact) |
| Pilot success and full acceptance | 03, 61 | blocked (no real pilot data; NO-GO report) |

## 3. Evidence references

| Session | Where | What it holds |
|---|---|---|
| 1 | `evidence/s1/` | truth ledger (`verification.md`), contracts, OTP end-to-end, ops acceptance |
| 2 | `evidence/s2/` | lanes A to D, core-flow acceptance, known-gap checklist |
| 3 to 6 | `evidence/s3/` to `evidence/s6/` | per-lane reports and session summaries (orgs and policy; sessions, presence, control; discussion, notifications, realtime; plans, artifacts, search) |
| 7 to 10 | `evidence/s7/` to `evidence/s10/` | integrations, governance, billing; security and privacy; reliability and observability; release, accessibility, readiness report |
| 11 | `evidence/s11/` | contract page, threat close-outs, schedulers, plain language UI, process parts, live PostgreSQL diagnosis, clean-checkout gaps, cloud report |

Docs that state behaviour and limits: `docs/collaboration-contract.md`, `docs/audit-and-events.md`, `docs/realtime.md`,
`docs/permissions.md`, `docs/lifecycle.md`, `docs/roadmap.md`, `docs/product-boundary.md`, `threat-model.md`.

## 4. Session outcomes

| Session | Outcome |
|---|---|
| 1 | Truth ledger and baseline suites; matrix created |
| 2 | Pilot residuals closed, sandbox fixes; parts 05 to 08, 10, 11, 22, 23, 27 verified |
| 3 | Organisations, policy engine, workspaces (37 to 39) |
| 4 | Sessions, presence, control leases, agent registry (40 to 42, 45) |
| 5 | Discussion, notifications, real-time delivery, collaboration UI (43, 46, 47) |
| 6 | Plans, artifacts, search and export (44, 48, 50); Claude closed 44 later (version approval, scheduled limit sweep) |
| 7 | Integrations, administration, billing controls (49, 51, 57): partial for provider breadth, residency, in-flight spend |
| 8 | Security and compliance review, sandbox profile pinning, run holds (31, 52, 58): partial |
| 9 | SLOs, capacity runbook, dashboard (53 to 55): partial |
| 10 | Client and compatibility runbooks, readiness report: NO-GO (56, 59 to 61) |
| 11 | Cloud pass: contract page and threat close-outs (14, 29), plain-language UI (12, 24), process parts, PostgreSQL diagnosis and fix, matrix to 44 verified (12 after the browser check) |

Sessions 6 to 10 were built by Codex and verified by Claude (`path-to-9-checkpoint.md`).

## 5. Acceptance results

| Check | Result | Evidence |
|---|---|---|
| Core flow, two users (part 27) | **pass** in Session 2; not re-run on this tree | `evidence/s2/acceptance-core-flow.md` |
| Operations: alerts, compose, backup (part 28) | **pass** (2026-10-21): compose journey 12/12, a real WAL archive failure paged by email, live WAL-G base backup recorded and read back by Prometheus | `evidence/s12/part28-55.md` |
| Security review (part 29) | **pass with one open item**: T13b (OTP parallel redeem) is open | `evidence/s11/part29.md`, `threat-model.md` |
| Collaboration contract executed by tests (part 14) | **pass** on the owner's tree; on a clean checkout 2 of the 46 tests fail for a missing fixture entry | `evidence/s11/part14.md`, `evidence/s11/clean-checkout-gaps.md` |
| Section 7: typecheck and unit tests | **pass**: `tsc --noEmit` clean; vitest 70 files, 824 tests (2026-10-20) | `evidence/s11/ui-plain-language.md` |
| Live PostgreSQL: section 4 live modules | **pass**: 18 tests across 7 modules (PG 16.15) | `evidence/s11/live-pg-search-export.md` |
| Migrations on real PostgreSQL | **pass**: 55 migrations (0001 to 0056), re-run idempotent (PG 16.15) | `evidence/s11/live-pg-search-export.md` |
| Section 4 suite on the pushed history, clean checkout | **fail**: package does not import; 102 collection errors | `evidence/s11/clean-checkout-gaps.md` |
| Full suites and gates for this session | see `evidence/s11/cloud-report.md` | `evidence/s11/cloud-report.md` |
| Pilot smoke on this tree | **pass** (2026-10-21): `run_smoke.py --otp`, real OTP sign-in, two users in Chromium, stand-in engine | `evidence/p1/browser.junit.xml` |
| Pilot baseline, at least 5 comparable tasks per side (part 03) | **not verified**: no real cohort | `pilot-baseline-guide.md` |
| Real-browser keyboard and reflow (parts 12, 56) | **pass** on `/login` and the run page, synthetic data (0 hard failures) | `evidence/s11/browser-check.md` |
| Screen reader, run-page contrast, other pages (part 56) | **not verified** / contrast **fail** (7 elements at 3.3 to 3.9:1) | `evidence/s11/browser-check.md` |
| Independent penetration test, Linux Landlock, `pip-audit` | **not verified** (not run) | `threat-model.md` |
| Real backup and point-in-time restore (part 53) | **partial**: PITR drilled 2026-10-21 from the live file archive and through self-hosted S3; no cloud bucket | `evidence/s12/part28-55.md` sections 5-6, `evidence/s9/verification.md` |
| Full product acceptance (part 61) | **fail**: critical rows partial or blocked | `evidence/s10/readiness-report.md` |

## 6. Risk register

| Risk | Severity | Owner | State |
|---|---|---|---|
| Pushed history cannot run section 4 (uncommitted `container_runtime.py`, `confine.py` container mode, fixture and stand-in edits) | High | program owner | **open**; nothing reproducible from a commit until fixed |
| T13b OTP parallel redeem or guess (`section1/src/harness_identity/auth.py` `verify_otp`) | Low | program owner | **open, accepted**; fix is a guarded `UPDATE ... WHERE consumed_at IS NULL` and `attempts = attempts + 1 ... RETURNING`; blocked behind the owner's uncommitted SMTP edits |
| Sandbox with `confinement=off` has no OS boundary; Linux Landlock path never run | High for any shared host | operator | open; pilot runbook requires container isolation on |
| SDK shell bypasses the Section 5 command allow-list (parts 31, 52) | Medium | program owner | mitigated 2026-10-21: the engine's bash tool goes through Section 5's shell gate, live in the worker image (`evidence/s12/part52.md`); path checks are argument-level and no pen test has run |
| `HARNESS_PILOT_TEAMS` empty or unset opens collaboration to every team | High if misconfigured | operator | documented; pilot environments must use a non-empty list |
| Alerts reach one email address only (no pager, no Alertmanager HA); backup and WAL alerts inert | Medium | operator | email delivery done and proved live (`evidence/s11/alert-delivery.md`); rest open |
| Backup restore, point-in-time recovery not exercised on a cloud object store (self-hosted S3 drilled 2026-10-21) | High | operator | open; part 53 stays capped at 8 |
| In-flight model spend is estimate-based; warning thresholds are not delivered | Medium | program owner | open (part 57) |
| Live identity-provider (SSO) flows unproven | Medium | program owner | open; part 37 capped at 8 |
| No counsel review, no pen test, no residency per tenant | High for real customers | program owner | open; parts 52, 58 capped at 8 |
| No measured baseline: no claim about speed or quality is supportable | n/a | program owner | open; part 03 blocked |
| UI never used with a screen reader; run-page contrast below 4.5:1 on secondary text; two pages walked in a browser; English only | Medium | program owner | open (part 56) |

## 7. Pilot setup

Operating rules are in `docs/runbooks/pilot.md` (flag `HARNESS_PILOT_TEAMS`, operator-present rules, stop conditions, feedback, rollback).
**Pre-flight checks before any operator-present rehearsal** (none has been run for this readiness decision):

1. The commit to be deployed imports and passes the section 4 and section 1 suites on a clean checkout (closes the first risk above).
2. Compose stack up on a Docker host and `deploy/pilot/run_smoke.py --otp` green on that exact commit.
3. `HARNESS_PILOT_TEAMS` non-empty and listing only the rehearsal teams; the rollback value `__rolled_back__` understood.
4. Container isolation on; confinement is not `off`; a Stop is confirmed (`run.cancel_confirmed`) in a dry run.
5. Alert rules loaded, `HARNESS_ALERT_EMAIL_TO` set, and a test alert received (`docs/runbooks/alerting.md` section 7).
6. Decision recorded on T13b before any email-OTP exposure beyond the rehearsal team.
7. A scratch-database run of `deploy/runners/migrate_selftest.py` on the target PostgreSQL version (passed on 16.15 here).
8. Named operator and backup (`docs/runbooks/pilot.md` section 3); incident record and feedback questions printed.
9. Automatic merge off; every generated change reviewed by a human before merge.

## 8. Feedback and metric plan

* Per task, the operator asks every participant the five questions in `docs/runbooks/pilot.md` section 4 on the same working day and
  writes one incident-record entry (run id, outcome, answers, incidents with request ids and error codes, whether Stop was confirmed).
* Baseline and comparison follow `pilot-baseline-guide.md`: choose at least five comparable tasks without the harness and at least five
  with it before seeing results, record elapsed minutes and a prewritten acceptance decision per task, and treat any comparison as
  descriptive only (no causal claim; `claimable` stays false below five per side).
* The metrics report is produced by `python -m harness_orchestrator.pilot_metrics TEAM_ID --baseline baseline.json`. That module is
  **not in the pushed history** (`evidence/s11/clean-checkout-gaps.md`), so the report can only be produced from the owner's tree today.
* Weekly: attach the incident records to the metrics report; expansion to more teams or riskier tasks is a human decision on those
  records (directive 010), never automatic.

## 9. Next steps (in order)

1. Commit the files the pushed history needs, or revert the engine wiring (`evidence/s11/clean-checkout-gaps.md`, "What the owner needs to do").
2. Decide T13b: apply the guarded `UPDATE` once `auth.py` is clean.
3. Run the section 1 tests that need Docker (4 failures locally), the compose stack and the pilot smoke on a Docker host.
4. Screen-reader pass of the collaboration UI and a fix for the secondary-text colour (row 56); add `section7/scripts/browser-check.mjs` to CI if wanted.
5. Obtain a supervised cohort and the baseline (part 03); run the release and rollback rehearsal on a scratch PostgreSQL stack (part 59).
6. Provider and external gates: identity provider, object-store restore, counsel, pen test.

## 10. Readiness decision

**NO-GO** for broad rollout, for new tenants, and for any claim of full-product readiness. Reasons: the pushed history is not runnable on
a clean checkout; parts 03 and 61 are blocked; parts 37, 52, 53 and 58 need outside evidence; no pilot smoke, restore or
penetration result exists for the current tree. **Conditional GO for an operator-present, allow-listed rehearsal** with named teams
only after every pre-flight check in section 7 has been run and recorded. Reassess only from recorded results, not from this document.

## 11. Final response template (part 35)

Every session ends with a response in this shape (the same six items as the stable prompt prefix):

1. **Readiness statement:** one of NO-GO, conditional GO (operator-present rehearsal), or GO, with the single reason that decides it.
2. **Existed versus changed:** what was already in the repository and verified, and what this session added or fixed (commit ids).
3. **Commands run and results:** each command with its result; anything not run is labelled "not run" with the reason.
4. **Parts now at 9 and still below 9:** matrix row numbers; externally capped parts named as capped.
5. **Open risks and whether they block the next session.**
6. **Files changed and the checkpoint link** (`path-to-9-checkpoint.md`); at most one focused question, only if a decision is the owner's.
