# STATUS — where the project stands

> **Terminology (canonical — see also README.md):** "engine" = this platform's
> Section 4 execution component; "DeepSeek Harness" (or "dsh") = the upstream SDK
> the engine drives — never our engine; "run" (not job/task); "team" (not org).
> Read the readiness block first; later entries are dated history, not a verdict.

## Current readiness (2026-10-24)

An independent 2026-10-21 startup audit
(`docs/briefs/startup-audit-2026-10-21.md`) found security gaps (S-01…S-04)
and pilot blockers; the remediation program is tracked in
`docs/pilot-remediation/`.

Current verdict: **NOT READY** for production or a general pilot; private
design-partner only. Fixed and gate-verified work is listed in
`docs/pilot-remediation/BASELINE_2026-10-21.md`.

Updated 2026-10-25: the real-provider smoke, the OIDC drill (Keycloak 26.1.5,
14/14 checks) and the object-storage PITR drill (MinIO S3 API, versioned
bucket) have executed; evidence is in `docs/briefs/evidence/pilot-prep/` and
the matrix in `docs/runbooks/external-validation.md`. A native-Linux container
tool drill passed on ubuntu-latest (kernel 6.17); it exposed and fixed two
Landlock allow-list holes that had granted `/` as readable (via `/bin/sh` and
via `HOME=/` in `--user` containers).
Stripe remains undrilled by decision — Stripe is not available in Pakistan, so
billing stays flag-off. Remaining blockers: a reviewed dedicated sandbox
worker/daemon, host certification, the first native-Linux drill run, and
counsel-approved terms (a review packet now exists under `docs/legal/`).

Updated 2026-10-31: the **Hx execution track** (M1–M8, design in
`docs/hx-execution-architecture.md`) is complete through M8, committed at `332d273`
(M2–M5), `3637e3f` (M6), `746a2d2` (M7) and `9e54132` (M8). A
program in the `agent` profile now checks, is authorised against its own
manifest, runs inside its declared budget across a host bridge, and produces
evidence that says `approved: false` / `autoMerge: false`. Section 4 executes a
workflow through `harness_contracts.workflow` (contracts `1.0.0rc7`, TS mirror
in the same change) and publishes its events, which Section 1 persists;
Section 5's shipped `LocalSandboxRunner` serves the live `repo.read` /
`verify.tests` seam; `model.invoke` is routed by Section 3's `ModelRouter` with
the role the source declares, fails closed without one, and records a downgrade
instead of performing it. M6 made the two limits that used to be recorded but not
checked actually check: `budget.cost` is reserved against a **host-supplied price
table** before each crossing (an unpriced crossing is refused rather than free, and
a ceiling with no table is announced in its own evidence as `declared-not-enforced`),
and a grant can be **revoked mid-run** — the next crossing is denied, completed ones
stand, and the revision is logged with the host's reason. M7 closed the last gap
in that story's honesty: a recorded **failure** now replays as itself (same code,
status, line, message and contract digest) instead of being masked by the replay's
own "nothing recorded" error, recordings are versioned (`hx.recording/v2`) with an
explicit outcome per exchange, and a call that never settled makes the recording
incomplete — which a replay refuses (`HXH104`) rather than fills in. M8 made a program
**several files** without making anything load during a run: `use notes` names a module
whose bytes the host supplies, a used module arrives renamed so `notes.Summary` is one
identifier in the checker, the runtime and the evidence, and the manifest carries a
digest per file (`sources[]`, `program_sha256` over module names rather than paths, so an
approved digest survives a clone) — which closes the hole where an unchanged entry file
with a swapped helper used to run. Two ways to compute a value arrived, neither of them
assignment: `for … accumulate` binds per pass and `base with { … }` yields a new record,
so `n = 2` is still a syntax refusal and the loop's termination story is unchanged. Suites
at `9e54132`:
language **367** (spec/toolchain now `0.4.0`), section 4's Hx-adjacent subset
`pytest tests/unit -q -k "hx or workflow or runner"` **22**, section 5's
`tests/unit/test_hx_live_host_ops.py` **4**, `make guard-isolation` clean (276 src
files, 10 sections), `scripts/spec_drift.py` clean, TS mirror current. The whole-suite
counts for sections 1/4/5 are deliberately **not** pinned to this commit: a concurrent
lane was committing into `section4` while these were measured (section 4's suite came in
at 2033, then 2081, with 48 tests from an in-flight `review_summary` module in between),
so a number here would be theirs and unverifiable rather than a claim about M8. Two
observations from those runs are worth keeping: one section 4 randomized-order run failed
while the same suite passed in fixed order, and
`section1/tests/test_ratelimit_expensive_ops.py::test_model_call_burst_over_limit_429`
failed once in a full section 1 run and passed three times in isolation — order- and
timing-sensitive tests in sections this track does not touch (Section 1 imports nothing
from it, and `guard-isolation` proves that). Both are recorded rather than hidden.
This does **not**
move the verdict above: the driver bounds host calls, not CPU; a price is the
host's estimate, not a settled bill from Section 3's ledger; a revoked capability
stops effects but does not kill a call in flight; a replay can reproduce an effect
it recorded but cannot undo one; a module is only as trustworthy as the resolver the
host hands it; Section 4 still cannot pin a multi-file program until `WorkflowRequest`
carries a program digest; and the track adds no approval surface of its own.

## 2026-11-24 — Sprint 3: migrations proven, X-D1 settings UI, similar denials, owner DRAFT pack (local, not pushed)

Commits `03a03a40`..`3eb09124` (not pushed); SHAs in `docs/briefs/evidence/sprint3/END-report-2026-11-24.md`.
`migrate_selftest` PASS: 79 migrations (0001→0081) on real PG via the caller-supplied path; Docker was down, so compose smoke and live approval e2e stayed BLOCKED and the browser spec remained mocked (openapi-checked).
Shipped: self-service rendering in `/settings/account` (display only), similar denials on the approval card (team-scoped, max 3), 0078/0080 router integration, a read-only flag-gated W31 panel, 15 unsigned §17 DRAFTs.
Gates: static, check-ts-dist, section8, frontend and migrations green; section1 had one order-flaky test, section4 two Docker-only failures.
**No 10.11 line moved; P-D2/P-D7/payment BLOCKED.** Verdict: **NOT READY**.

## 2026-11-24 — Sprint 4: live stack verified, migrations on compose, Phase 7 gap closed (local, not pushed)

Commits `d93ee869`..Task 30 on `w28-s1-delegate` (not pushed); SHAs in `docs/briefs/evidence/sprint4/END-report-2026-11-24.md`.
Docker recovered, stale image rebuilt; `migrate` applied 0076–0081 on compose `:5499` (`max(version)=0081`), identity/orchestrator health 200. `pg-probe`, compose journey (13/13), live approval e2e (1 live + 6 mocked) and the P1 multiplayer spec passed at named SHAs.
Section1 flake hardened (two full greens), section4 2445 passed with Docker up; C-05 dependency status + C-07 reject note shipped; collab `viewed_detail` measured (section8 seam still open); operator runbook added. **`make gate` GREEN (rc=0)** — the full ci.yml PR gate. P-D7 stays DEFERRED; no 10.11 line moved; verdict **NOT READY**.

## 2026-11-24 — Sprints 1–2: Phase 7 slice, X-07/X-21 browser-verified, X-14 deferred (local, not pushed)

Commits `c9d40d74`..`2c8abe1d` on `w28-s1-delegate` (not pushed). Full index with SHAs:
`docs/briefs/evidence/sprint2/END-report-2026-11-24.md`. `make gate` at `2c8abe1d`: every stage
green except two section4 tests that need a running Docker daemon (down here). Shipped: Phase 7
admin-reviewed assignment planning (API + pages); Plain / Decision-card approval renderings
verified in Chromium with a mocked API (keyboard, axe, 375 px; found and fixed a clipped grid);
blind-approval KPI with a measured diff-on-screen signal; X-08 classification (display only,
P-D6 off); case memory on denial stops; W31 tier proposals (flag-off); observer-only external
adapters; `python -c` pre-exec parse. X-14 dry benchmark → **DEFER** (faces tie flat chunks).
Owner register recorded with defaults; **no 10.11 gate line moved** and P-D7 stays deferred
(`docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md`). Verdict unchanged: **NOT READY**.

## 2026-11-23 — F-SEC-06 closed; the PG-gated authz matrix is green unskipped

The first routine `make test-all` (X-02) halted on one red PG-gated test. The root
cause is now known and the test is fixed — **no product change was needed**: the walk
probed `DELETE /api/v1/orgs/me/identity` with its own actors, and that route
deactivates the caller and kills every session/seat, so each later `/orgs/{org_id}/…`
401 was the session layer working correctly. The foreign-200 reads that remained are
`WHERE team_id=:t` / `WHERE user_id=:u` own-data queries (agents, notifications,
sessions, workspaces, orgs); `orgs/service/whoami` is a service-identity door. The
matrix now uses per-actor throwaway twins for the self-deleting route, pins own-data
reads honestly, and adds an `own_data_write` class for `read-all`. **Matrix 3/3 on
real PG and 3/3 on sqlite; section1 full PG 1351 passed**, with only the four
`docker exec harness-db-1` rows red because no Docker daemon is running here (1355
tests total in both runs — the only delta vs X-02 is the matrix). Evidence:
`docs/briefs/evidence/10x/fsec06-authz-matrix-2026-11-23.md`. This unblocks the rest
of `make test-all`; the verdict does not move — G-C still wants the matrix over the
deployed surface, and the deployed set still predates the P2 sandbox fixes until
F-SEC-05's redeploy (owner).

## 2026-11-21 — PR #24 merged to master; vv90r6 one-tag set deployed; A-06 met live

Executed by the owner's instruction ("do it"): **PR #24 merged to master**
(`1753171..11d2e7a`; master CI run 44917358323 SUCCESS), then the RC stack
**rebuilt from the post-merge tip and redeployed as `vv90r6`** with the runbook
flow (dry mode, worker profile). **5-point verification all green**: `stack_tags
--project harness-rc` = **Agreement: AGREE (vv90r6), Missing none**; schema
**74|0076**; identity on `harness-backend:vv90r6` (the untagged `abdc28b02505`
is gone); guest gate **401** without a session; frontend **200**. Bonus checks:
`/api/v1/audit/pack` is in the deployed identity spec (148 paths) and the
deployed shell gate is **byte-identical to HEAD** (diff 0, `OUT_OF_SCOPE`
present). No migration delta since `2de9167`, so the vv90r5 migrate selftest
carries over; the one-shot worker exited 0. Manifest:
`image-manifest-vv90r6-deployed.json`. **Closed:** week-03's RC-drift finding
(MIXED → AGREE), the week-02 deployed-gate delta (vw24b1 failed 4 of 8 probe
shapes — the live gate now matches HEAD), and **A-06 "one reviewed image set"
is MET on the live stack**. Also shipped in this wave: the step-3 operations
checklist (Grafana/offsite/5499/restore drill — `step3-operations-checklist-
2026-11-21.md`) and the checkpoint-1 redesign design doc
(`checkpoint1-redesign-2026-11-21.md`) so the next lane builds the faithful
flow instead of patching the old one green.

## 2026-11-21 (later) — pushed to origin, weeks 2–3 of the 90-day plan executed, measured data

`w28-s1-delegate` is fully pushed: `19f5cae..c286d22` (75 commits) on origin; **0 commits
ahead of its upstream** for the first time since 2026-11-06. PR #24 carries it; CI runs at
the pushed SHA. The first push caught a real defect: `vitest + tsc (section7)` failed on
`make check-ts-dist` because the P-17 commit updated `clients/ts/src/identity.ts` without
rebuilding `clients/ts/dist` — fixed at `c286d22` (fresh build, 26 files, check green).

**Week 2 (P-04 drills) — all executed and recorded (week-02 §5):** native-Linux container
drill **PASS in CI** on ubuntu-latest (kernel 7.0.0-1020-azure, run 44882932634, at
`19f5cae`); Landlock pytest subset 42/42 on a real Linux VM kernel including both October
regressions; **G1 gap confirmed live** (Landlock-confined `allow_network=False` child
connected out — documented ABI-v1 limitation, `confinement.py:446`; pilot unaffected);
**G2 macOS halves split** (environ side-channel absent on macOS; `$HOME` canary read-gap
confirmed through Seatbelt). Evidence JSONs + probes committed under
`docs/briefs/evidence/pilot-prep/`. The named holes now wait on **owner risk-acceptance
(G1/G2, week-02 §2)** — that is the acceptance's remaining move.

**Week 3 (P-06) — the Nov-12 handoff executed (week-03 §5):** all four regression groups
green (9 + 13 + 57+4xfail + 189). Isolation verified at YAML level (volume renamed
`harness_rc_runs`, no demo networks, every port loopback). **The RC stack has drifted from
its reviewed manifest**: `stack_tags.py --project harness-rc` reports **MIXED** — `vv90i13`
×10, `vv90r4` ×2, identity + review on an **untagged** image (`abdc28b02505`); RC schema at
0075 vs HEAD's 0076. The A-06 "one reviewed image set, no drift" acceptance is **not met
today**; the A-01 argument checks are code-side closed at HEAD but four shapes pass the
deployed tag's decision layer (one demonstrably executed an out-of-workspace read).
Closing both = **one redeploy from a post-`e24b830` revision + a fresh manifest (owner)**.

**Measured data (2026-11-21, `c286d22`):** 800 commits on the branch since 2026-08-22
(795 Mubashar Ali, 5 Claude); 3,525 tracked files; ~256k lines Python, ~55k TS/TSX,
~69k lines of Markdown; 1,724 docs files of which 1,223 evidence, 15 decision records,
15 runbooks; 74 migrations (0001→0076, no 0014/0074); collected tests: section1 1,327,
section4 2,452, section5 554, section8 104 (+ section7 1,201 vitest) — all measured green
today except the skipped/xfail rows noted above. Open PRs: #24 (this integration) and
#25 (perf). The 61-part matrix counts (50 verified / 1 / 8 / 0 / 2) still date from
2026-10-21 and remain unaudited row-by-row.

Verdict unchanged: **NOT READY** — the distance to pilot is owner actions (outreach,
SOWs, D-01/rename/payment decisions, redeploy, pen test, counsel), not engineering.


## Older history

Every entry before 2026-11-06 — the v1.0-candidate missions, the August waves, and the
2026-11-04/05/06 reconciliation entries — lives verbatim in
**[docs/archive/STATUS-history-to-2026-11-06.md](docs/archive/STATUS-history-to-2026-11-06.md)**;
the 2026-11-21 (earlier) entry is in [docs/archive/STATUS-history-2026-11-21.md](docs/archive/STATUS-history-2026-11-21.md).
This file stays the current-readiness record: newest entries at the top.
