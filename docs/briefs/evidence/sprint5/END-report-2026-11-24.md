# Sprint 5 END report — Task 40 (closure)

- Date (UTC): 2026-11-25 (file name keeps the sprint's 2026-11-24 label)
- Branch: `w28-s2-sprints1-4` (PR #27, **not merged**). Start `17015624` → report written at `5b59af8b`.
- Lanes: cloud thread A (Tasks 0, 31, 32, 37, 38, 39; no Docker) and owner Mac (Tasks 33–36, truth run).
- This report only restates what the committed evidence and Mac logs show. Cloud did not re-run any live step.

## Verdict

- **`make test-all` (Docker, PG 18) rc=0 and `make gate` rc=0 (GATE GREEN) at `a55ba75f`.**
  Source: [`mac-gate-a55ba75f.md`](mac-gate-a55ba75f.md). The gate was run at `a55ba75f`; the only commit after it
  (`5b59af8b`) adds that evidence file, so no code was re-gated at `5b59af8b` itself.
- **Task 36 is a FAIL.** The live B-09 invite → revoke journey found two unfixed product defects (D2, D3): removing a
  member does not work. The green gate does not cover this: `make gate` does not run e2e.
- Product verdict stays **NOT READY**. No 10.11 gate line moved. P-D7 stays DEFERRED; payment and global P-D6 not enabled.
- GitHub Actions is still blocked by account billing, so there is no CI run for this branch. The Mac run is local truth, not CI.

## Task ledger

| Task | Result | Commit | Evidence | Run on |
|---|---|---|---|---|
| 0 pre-flight | done | `260b24b` | `START-rev.md` | cloud |
| 31 collab `viewed_detail` API | **PASS** (unit, SQLite; static gates) | `c5bcd3f` | `task-31-…` | cloud |
| 32 collab audit + blind KPI | **PASS** (unit; 6 collab PG tests on local PG 16) | `66847e9` | `task-32-…`, `task-38-…` | cloud |
| 37 denial-learning UI (read-only offer) | **PASS** (unit + static render; not browser-run in cloud) | `bb124ba` | `task-37-…` | cloud |
| 38 `make test-all` evidence | cloud: 12 + 4 + Docker P0 **BLOCKED (env)**; superseded by Mac run | `f73ada2` | `task-38-…` | cloud |
| 39 `make ci-local` + C-07 template | cloud: **FAILED** (6 env steps); Mac re-run of security-scan + frontend green | `5932eaf` | `task-39-…`, `mac-gate-…` | cloud + Mac |
| 33 live blind-approval KPI | **PASS** — live compose stack, no mocks | `a0871b2` | `task-33-…` | Mac |
| 34 live planning | **PASS** — live stack, real planner | `c42f855` | `task-34-…` | Mac |
| 35 live similarCases | **PASS** — fresh P1 stack; denial text from the deterministic stand-in worker | `2fd6de1` | `task-35-…` | Mac |
| **36 live invite → revoke** | **FAIL** — removal broken (D2, D3); D1 fixed | `a55ba75` | `task-36-…` | Mac |
| 40 closure | this report | (this commit) | — | cloud docs |

The Task 38 and 39 cloud failures (section1 8–12, section4 4, section5 32, gitleaks, `check-ts-dist`) are all
environment-caused and re-ran green on the Mac (see "Cloud failures, re-run here" in `mac-gate-a55ba75f.md`).

## Mac truth run at `a55ba75f` (from the Mac logs)

| Command | rc | Result |
|---|---|---|
| `HARNESS_TEST_PG=1 TEST_ALL_DOCKER=1 make test-all` | 0 | integration 3 passed / 1 skipped; section1 on PG 1361; migrate selftest 79 (0001→0081); section4 on PG 2504 passed, 19 skipped; Docker P0 17 passed, 1 skipped; promtool 37; landlock SKIP |
| `make gate` | 0 | GATE GREEN; section5 553 passed, 4 xfailed; vitest 1243 |
| `make check-ts-dist` | 0 | dist matches fresh build |
| `make ci-local` (security-scan + frontend) | 0 | gitleaks and frontend ok |

## Defects and open items

| ID | Item | State |
|---|---|---|
| D1 | UI invite redeem always 422 (form omitted the invited email) | **Fixed** in `a55ba75`; vitest `app/settings/team` 19 passed; live redeem then worked |
| D2 | Removing a member who has acted returns 500 on Postgres (`fk_audit_log_actor_member_id` RESTRICT; SQLite hides it) | **Open — owner design decision** (soft-delete memberships vs another revoke path; detaching the FK would rewrite append-only audit) |
| D3 | Roster returns membership `id` but no `userId`; Remove/role-change buttons send the wrong id → 404 | **Open — contract change** (`MemberListResponse.userId`, OpenAPI + TS client regen) |
| — | `invite-revoke-live.e2e.ts` carries `test.fail(...)`; it reports as an expected failure until D2 is fixed | Deliberate; Playwright flags it when it starts passing |
| — | Member's planning view is checked via `/mine` API, not UI (two-team login needs a `teamId`; no team picker) | Product gap, not fixed |
| — | `readiness-drift`: manifest 0076 vs disk 0081 | Open; needs RC redeploy + manifest rewrite (owner lane) |
| — | Landlock confinement tests run only on Linux; Task 35 denial text is stand-in, not the real shell plugin | Known limits |
| — | GitHub Actions blocked by billing | Open (owner) |

Until D2 and D3 are fixed, a member **cannot be removed or have access revoked through the product**. That matters
for B-09 (P-D7 stays DEFERRED) and must be fixed before any multiplayer pilot.

## Not measured

- Blind-approval **rate** on real pilot decisions: no pilot traffic (live KPI shows "insufficient data (2 of 10)").
  Only the `technical` rendering bucket was exercised live.
- Repeat-denial count on pilot tasks: not measured.
- Owner-lane items untouched: SOW signatures, P-D2 rail, F-SEC-05 redeploy, P-13 benchmark budget, P-D7, G1/G2 acceptance.

## Handoff

1. Owner: decide D2 (soft-delete vs other), approve the D3 contract change; then a follow-up sprint fixes both and
   removes `test.fail` from `invite-revoke-live.e2e.ts`.
2. Owner: fix GitHub billing so CI can run on PR #27; then review and merge PR #27 (not done here).
3. Owner: RC redeploy + `--write-manifest` to clear readiness-drift.
4. Thread C (checkpoint-1 redesign) may continue from `66847e9` or later.
