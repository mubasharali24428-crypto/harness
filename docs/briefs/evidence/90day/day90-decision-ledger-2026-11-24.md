# Day-90 decision ledger — 2026-11-24

Base revision `aed8a339` (branch `w28-s1-delegate`). Register record:
`docs/decisions/2026-11-24-owner-register-defaults.md` (anchors per ID).
Status legend: **CONFIRMED** = owner-ACCEPTED record exists · **DEFAULT** = §17 default governs code,
owner signature missing (= BLOCKED on owner) · **BLOCKED** = gate cannot move without named evidence.

## §17 register

| ID | Status | Record | Owner input still missing | DRAFT path (unsigned) |
|---|---|---|---|---|
| D-01 | DEFAULT | owner-register-defaults#d-01 | signature on draft | `docs/decisions/2026-11-11-d01-deployment-priority-DRAFT.md` |
| D-02 | DEFAULT | #d-02 | multiple admins / transfer | `docs/decisions/2026-11-24-d02-project-admin-model-DRAFT.md` |
| D-03 | DEFAULT | #d-03 | shared goals visibility | `docs/decisions/2026-11-24-d03-member-visibility-DRAFT.md` |
| D-04 | DEFAULT | #d-04 | signature | `docs/decisions/2026-11-24-d04-member-communication-DRAFT.md` |
| D-05 | DEFAULT | #d-05 | signature | `docs/decisions/2026-11-24-d05-admin-approval-of-plans-DRAFT.md` |
| D-06 | DEFAULT | #d-06 | signature | `docs/decisions/2026-11-24-d06-members-agents-DRAFT.md` |
| D-07 | DEFAULT | #d-07 | signature | `docs/decisions/2026-11-24-d07-roles-and-capabilities-DRAFT.md` |
| D-08 | DEFAULT | #d-08 | signature | `docs/decisions/2026-11-24-d08-leavers-and-recovery-DRAFT.md` |
| D-09 | DEFAULT | #d-09 | provider/jurisdiction/reviewer list | `docs/decisions/2026-11-24-d09-pilot-providers-hosts-reviewers-DRAFT.md` |
| P-D2 | BLOCKED | #p-d2 | written seller-eligibility confirmation; rail choice | `docs/decisions/2026-11-12-payment-rail-P-D2-DRAFT.md` |
| P-D3 | BLOCKED | #p-d3 | counsel trademark search | `docs/decisions/2026-11-11-rename-shortlist-DRAFT.md` |
| P-D4 | DEFAULT | #p-d4 | counsel review | `docs/decisions/2026-11-24-p-d4-pilot-pricing-band-DRAFT.md` (counsel required) |
| P-D5 | DEFAULT | #p-d5 | none until 10.13 | `docs/decisions/2026-11-24-p-d5-open-source-flip-DRAFT.md` |
| P-D6 | CONFIRMED (not enabled) | `2026-11-13-du4-risk-tiers.md` | — | — (ACCEPTED record, no draft needed) |
| P-D7 | BLOCKED (deferred; design-only) | #p-d7 | pilot demand evidence, B-09, reviewer | `docs/decisions/2026-11-24-p-d7-multiplayer-build-gate6-DRAFT.md` |
| CD-01 | DEFAULT | #cd-01 | signature | `docs/decisions/2026-11-24-cd-01-cache-retention-tenancy-DRAFT.md` |
| CD-02 | DEFAULT (proposals only) | #cd-02 | signature | `docs/decisions/2026-11-24-cd-02-learned-memory-skills-DRAFT.md` |
| X-D1 | DEFAULT, implemented | #x-d1 · `docs/briefs/evidence/10x/x07-backend-preference-2026-11-24.md` | signature | `docs/decisions/2026-11-24-x-d1-approval-rendering-owner-DRAFT.md` |
| X-D2 | DEFAULT | #x-d2 | budget | `docs/decisions/2026-11-24-x-d2-benchmark-spend-cap-DRAFT.md` |

## 10.11 gate lines (gate window 9–11 Feb 2027)

| Gate line | Verdict | Evidence |
|---|---|---|
| ≥2 signed SOWs by Day 28; 3–5 teams by Day 83 | **not met** (owner lane) | `STATUS.md` 2026-11-21: "SOW signatures" listed as owner action; no SOW in repo |
| ≥$1K MRR or ≥3 written pay-intents | **not met** | billing flag-off (`section1/src/harness_identity/billing.py`); no pay-intent recorded |
| Payment rail live + recorded test transaction | **blocked** on P-D2 | `docs/decisions/2026-11-12-payment-rail-P-D2-DRAFT.md` |
| Linux sandbox evidence on the deployed tag | **not met** | `docs/briefs/evidence/10x/wave-a-2026-11-22.md` / STATUS: vv90r6 predates P2 G2 fix (F-SEC-05, redeploy owed by owner) |
| Benchmark published | **not met** (dry only) | `docs/briefs/evidence/w7-bench/leaderboard.md` — dry fake-model trials; P-13 live run owner-gated (X-D2) |
| 13 weekly evidence entries | **met as files, not as calendar weeks** | `docs/briefs/evidence/90day/week-01.md` … `week-13.md`; week-13 says "executed ahead of the calendar week" |
| Multiplayer B decision recorded | **blocked** — recorded as DEFERRED | owner-register-defaults#p-d7 |

## Single BLOCKED summary

Owner (project owner) must supply: SOW signatures, P-D2 rail choice with written eligibility,
F-SEC-05 redeploy, P-13 benchmark budget, P-D7 go/no-go. Engineering cannot close any 10.11 line alone.

## Delta after Sprint 2 (2026-11-24, `dcadc02c`..`2c8abe1d`)

**No 10.11 gate line moved.** Every line above is still owner-lane (SOWs, revenue, P-D2 rail,
F-SEC-05 redeploy, paid benchmark budget, P-D7). Engineering items that changed state:

| Item | Before | After | Evidence |
|---|---|---|---|
| X-07 / X-21 keyboard, axe, 375 px | not run | verified in Chromium (mocked API) | `docs/briefs/evidence/sprint2/task-11-browser.md` |
| Phase 7 (C-01..C-04) | backend only | admin + member pages | `section7/e2e/planning.e2e.ts` |
| Gate 6 / B-09 | ledgered blocked | per-item checklist + threat model; still BLOCKED (P-D7) | `docs/briefs/evidence/multiplayer/B-09-checklist-2026-11-24.md` |
| X-14 | prototype | dry benchmark run; verdict DEFER | `docs/briefs/evidence/10x/x14-layout-benchmark-2026-11-24.md` |
| X-08 | not started | classification + advisory badge; P-D6 still not enabled | Task 17 commit `47f3c8ad` |
| Blind-approval KPI | denominator only | measured signal + admin tile; no pilot data yet | `docs/briefs/evidence/10x/x07-backend-preference-2026-11-24.md` |

## Delta after Sprint 4 (2026-11-24, engineering only)

**No owner decision closed; no 10.11 gate line moved.** One engineering verification row:

| Item | Before | After | Evidence |
|---|---|---|---|
| `pg-probe` on compose `:5499` | scratch-DB / external-PG only | **PASS** on compose PG: integration 3 passed/1 skipped, section1 1361 passed, migrate selftest 79 migrations (0001→0081) idempotent | `docs/briefs/evidence/sprint4/task-23-pg-probe.md` |

## Delta after Sprint 3 (2026-11-24)

**No status changed. No 10.11 gate line moved. No signature was written by engineering.**
This delta is doc plumbing only: every DEFAULT/BLOCKED §17 row now has an unsigned,
fill-in-the-blank DRAFT path in the table above (15 new dated drafts; D-01, P-D2 and P-D3
reuse their existing drafts; P-D6 is CONFIRMED and needs none). Each draft carries Context,
Options, Recommendation = the register default, and a blank owner Sign-off. The owner must
create a separate ACCEPTED record; these DRAFTs are not decisions.

| Item | Before | After | Evidence |
|---|---|---|---|
| §17 draft coverage | 3 drafts (D-01, P-D2, P-D3) | all 18 BLOCKED/DEFAULT rows have a DRAFT path (P-D6 is CONFIRMED, ACCEPTED record) | table above; `docs/decisions/2026-11-24-*-DRAFT.md` |
| Owner gates (10.11, P-D2, P-D7) | BLOCKED | unchanged — still BLOCKED | this file, "Single BLOCKED summary" |
