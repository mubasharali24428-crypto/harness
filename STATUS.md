# STATUS — where the project stands

> **Terminology (canonical — see also README.md):** "engine" = this platform's
> Section 4 execution component; "DeepSeek Harness" (or "dsh", always referring
> to that external product/SDK, never to our engine) = the upstream tool the
> engine drives; "run" (not job/task); "team" (not org/workspace).

> Read the current-readiness block immediately below first. The
> Mission 23 program-close entry records the Aug-2026 v1.0.0-candidate feature
> set; it is not a current readiness verdict.

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

## 2026-11-05 — Roadmap 3.0 position after PRs #18–#21 and W28 S1a

Merged since PR #12: W31 Scheduler experiment (`1dd3ebc`, decision `docs/decisions/2026-11-04-w31-scheduler.md`, ACCEPTED for pilot scope only: proposals behind approval, no dispatch), W32 seam analysis (`832c5d6`), W30 candidate audit (`b822923`, `docs/decisions/2026-11-05-w30-candidate-audit.md`, no candidate adopted for production). Stages 0–3, 7 and 8 are done on the evidence in those records; stage 4 (W25 protocol and corpus exist, live set not run, no model spend yet), 6 (W28 S1a: `delegate` verb behind `HARNESS_SUBAGENTS`, inert by default; two-agent/one-cancelled/no-live-orphan test at `9d7d67c`) and 9 (seam analysis only) are partial; stage 5 (W27) and 10 (W33) have not started. W28 S1b (isolated root, claim path, synthesis) is not built; an uncommitted draft in `agent_tree.py` plus migration 0075 is deliberately left uncommitted (guessed column lists, swallowed `OperationalError`, hard-coded `/workspace`). Verdict unchanged: **NOT READY**.

W25 fixed live set, first run (2026-11-05, stack model `anthropic/claude-haiku-5.5`, one attempt each, evidence `docs/briefs/evidence/eval/`): `multi-file-refactor` PASS (41 s, 20 tool calls, $0.0097 from `cost_ledger`), `tests-written` PASS (25 s), `w11-grounding` PASS (33 s); ledger total for the three about $0.015. One trial per task is a smoke result, not a benchmark: no repeats, no held-out variants, no latency percentiles. W26 has nothing to fix from it yet. The identity redeploy for the BYO claim marker was not done (shared live stack; the owner's call).

W25 live set, DeepSeek A/B (2026-11-05, stack switched to `deepseek/deepseek-v4-flash`, $0.091/$0.182 per M tokens, one attempt each): `multi-file-refactor` PASS (61 s, Haiku 41 s), `tests-written` PASS (37 s, Haiku 25 s), `w11-grounding` FAIL: the model has no image input on OpenRouter (404 "No endpoints found that support image input"), so this is a capability mismatch, not a grounding-quality result. Ledger for the two text tasks: 129,777 input / 3,869 output tokens, $0.0125, against about $0.0147 for the same two on Haiku, roughly 15% cheaper but about 50% slower; single trials, so neither gap is established. Vision drills need a vision model (Haiku, or the corpus-pinned `qwen/qwen3.7-flash`). The stack's `DSH_MODEL` and rate card (`.env` line 286/288, `deploy/.env` line 11) now point at DeepSeek; revert by restoring `DSH_MODEL=anthropic/claude-haiku-5.5` (the rate card keeps all three models). A one-off `openai/gpt-4o-mini` ledger row ($0.00003) appeared during the run; its caller is unidentified.

W25 vision tasks on Haiku via `scripts/eval/with_model.sh` (2026-11-05, one attempt each): `m07-vision-api` PASS (11 s), `w10-browser-repro` PASS (37 s), `w11-vision` PASS (43 s), `w14-design-heldout` PASS (28 s), plus `w11-grounding` PASS earlier. Ledger for the four-task batch: 198,336 input / 5,161 output tokens on `anthropic/claude-haiku-5.5`, $0.0224. The wrapper restored `deepseek/deepseek-v4-flash` afterwards. The recurring `openai/gpt-4o-mini` rows (under $0.0001 each) are the router's default reviewer role (`default_reviewer_model`), not a stray call. All eight live tasks in this set have now passed once; single trials, so this is still a smoke result. Session model spend to this point is about $0.05.

W28 S1b-1 (2026-11-05, decision `docs/decisions/2026-11-05-w28-child-run-approval.md`): a delegated child is now a real run with its own run id and proposed plan, waiting for human approval like any run; cancelling its assignment stops the run (and descendants'), and a planning failure cancels the assignment. Migration 0075 (`agent_assignments.child_run_id`) applies clean on real PG (73 migrations, 0001 to 0075, idempotent re-run). Section 4 unit suite 1883 passed / 41 skipped, spec drift clean. Verified on SQLite with the offline planner only: no live-stack two-agent run yet, and synthesis (S1b-2) is not built. Still inert unless `HARNESS_SUBAGENTS=1`. The earlier uncommitted draft was discarded (a copy is in the session scratchpad).

W28 live two-agent drill (2026-11-05, `scripts/e2e/w28_two_agent_drill.py`, evidence `docs/briefs/evidence/w28/two-agent-drill.json`, deployed `harness-backend:vw28s3`): PASS, 11 of 11 steps on the live stack. A root and two children were delegated (three runs, each awaiting human plan approval); child B was cancelled before approval; child A was approved by a second admin and started, and the drill waited for a real worker network before cancelling the ROOT, which stopped all three runs. Afterwards: no `run_claims` row, no worker container or network, no live assignment. Model spend for the drill was $0 (the cancel landed before A's first model call), so this proves the lifecycle and cleanup, not agent output quality. Defects the drill found and fixed: `delegation.router` was never mounted in `deploy/runners/services.py` (the verb 404'd on every real stack; guard test added), the delegate response carried no plan id so a human could not approve a child (`childPlanId` added), and compose did not pass `HARNESS_SUBAGENTS` through. Known limits: the assignment scope is recorded but not bound to the child's engine; a repeated stop of an already-stopped run re-emits a `paused` event (child B shows two); synthesis (S1b-2) is not built; one trial. The live flag is off again (inert).

**Live stack changes made for the drill (owner approved the drill):** migrations 0073 (runner_jobs rebuilt, 18 rows kept) and 0075 applied to the live database after a full dump; backend services, orchestrator and worker-daemon now run `vw28s3` (engine-worker retagged from `vw25b1`, same bytes); rolling back to `vw25b1` is refused by the migrate drift gate. This also closes the "redeploy for the BYO claim marker" item; the W13 drill has not been re-run on it.

## 2026-11-05 — PR #12 merged: BYO hosts mark the run when they take it

PR #12 merged at `4864a11` (2026-11-04/05 local). The commit carried three pieces, each verified in-session: (i) a durable `execution_started` event published at runner job **claim** (offered→leased) — the first time a BYO run left its pre-execution state on the harness side, which is why `finish` always settled it `failed` under the never-resurrect law regardless of what the host reported; placement is post-lease-commit (publish-before-commit silently dropped the pending UPDATE on the shared-sqlite-pool test fixture, which is what 9 red runner tests caught). (ii) The deterministic W13 drill now runs the honest path — approve the plan via the API, emit one passing call/result per planned command, then finish — so `runner claim → executing → composing → done → settle` is exercised end to end where it can be verified. (iii) Section 1 evidence: **1239 passed / 63 skipped**, ruff clean (one F841 from an unused exception binding, fixed pre-merge), full CI green including `e2e (compose journey)` and `migrations apply clean on real PG`.

Honest limits, carried forward unverified: the drill's two marker-dependent checks (`toolEventsAccepted`, `finishCompletesTheJob`) report **41/43 with 2 red** against the deployed `vw25a1` stack — this fix is invisible to it until the identity service is rebuilt and redeployed; the redeploy is the owner's call and has not happened. The claim marker orders after the lease commit by design; on PG both orders are equivalent, and a failed emit degrades to never-resurrect (state `failed`, which the settlement honestly reports), never a silent half-announce — but no live-stack proof exists yet either.

Working state after this merge: PRs #7–#12 merged into master this session; W17 lost-lease re-dispatch is merged (stages 0–2 of Roadmap 3.0 materially closed on the code side); W24 real-window task is PASS; the W25 fixed live set remains un-run against the stack (no live model call has been made) and awaits an agreed budget.



## 2026-11-04 — PR #7 merged: `w1-engine-tools` lands on master (CI green)

Branch `w1-engine-tools` (184 commits ahead of master: Hx M1–M8, W17/W19/W22/W23/W24, Roadmap 3.0 stages 0–3) was pushed, CI-triaged and merged into `master` as **`56bef5d`** (2026-11-04 04:57 UTC) via PR #7, a merge commit per repo convention. Branch protection was satisfied, not bypassed: the only required context missing from the checks was `vitest + tsc (section7)`, whose job had been renamed on the branch to `+ published TS dist` — `f9b205a` restored the exact protected label with steps unchanged. Final CI on the merge candidate: **all 15 required checks green** (e2e compose journey 2m37s; section1 2m16s; section4 4m01s). The long-running master nightly section1 failure (`test_per_route_budgets_apply_together`, 404≠429) did not reproduce on the branch — section1 passed there and in the merge candidate; the master-only red is expected to clear on the next scheduled run, unverified as of this entry.

Defects found and fixed in this pass (local evidence: section3 149 passed; section4 **2121 passed / 59 skipped**; section1 targeted file 12 passed; ruff clean; `make static-gates` green; a fresh-boot simulation confirmed the boundary-sweep fix):

1. **`694288e`** — `harness_orchestrator.model_client` (committed at `9a6f23d`) and its tests were untracked; fresh checkouts at that revision could not import the api module. Committed with their tests.
2. **`4289b70`** — CI triage, five defects: (a) two runtime knobs undocumented in `.env.example` (`HARNESS_MAINTENANCE_MODE`, `HARNESS_INDEX_LEASE_SECONDS`) — static gates red; (b) the confinement strict-mode refusal lost the `=off` opt-out hint when 55400df made refusals host-aware — fails on every non-macOS host, i.e. all CI (`test_real_runtime_fails_closed_unless_confinement_off`); (c) **real bug** — the durable boundary sweep used `0.0` as its "never swept" sentinel while comparing against `time.monotonic()` (host-boot-relative), so on freshly booted hosts the startup sweep that reclaims orphaned boundaries never fired — the exact pass that closes a dead orchestrator's corpses (4 W22 orphan-window tests failed on CI, passed on long-uptime dev machines); sentinel is now `-inf`; (d) W10 CDP fake server closed with the client's request unread → TCP RST → CI-only ConnectionResetError — it now reads the frame first; (e) the e2e compose job underbooted (8 services) while the journey's new step-0 gate counts every enabled default service including sidecars — CI now boots the full default set.
3. **`68f723a`** — W22/W24 drill evidence JSONs (cited by m2/roadmap3 plans) and `scripts/e2e/w22_repeat_batch.py` committed; `docs/yc` deliberately left local-only per the owner's call.
4. **`a70b671`** — **boot race, not drift**: 2026-11-04 e2e `migrate` pre-flight got ECONNREFUSED 0.5s after Compose reported the db Healthy (the db began its post-initdb shutdown 0.6s after Healthy — cause unclassified, WAL-g archive failing without a storage prefix is noise, not the cause) and refused boot with exit 37, the same exit as real schema drift. `check_drift_with_retry` now retries connect-side failures ~12× over ~60s before refusing; genuine `SchemaDriftError` is still raised immediately and never retried; both boot PYGATEs use it; exit codes and the rollback runbook contract unchanged.
5. **`f9b205a`** — section7 job renamed to the exact required context (see above).

**2026-11-04 follow-up — the "Docker-level look" happened.** A scratch repro (`docker compose -p harness-repro`, port 5599, never the dev stack) shows a healthy initdb boot in under a second on a machine where `/tmp/harness-walg-live` exists — wal-g pushes succeed locally ("storage: default … FILE PATH … .lz4"), the cluster reaches `database system is ready to accept connections` with no post-Healthy bounce. In CI (fresh runner, archive target absent) wal-g fails every cycle with "Failed to find any configured storage" (loud log spam by design, never fatal) and the db died 0.6s after Compose reported Healthy with **no shutdown-request line in the log** — the signature of an external kill (SIGKILL/OOM under full-default-boot pressure on the fresh runner), not a graceful stop and not drift. Locally unreproduced; per-run incidence ~1 of 3; the drift-gate retry masks the window but cannot name the killer. What would close it: Docker-level observability on the CI runner (`docker events` + cgroup metrics) or a db-limit bump for the e2e job; unverified hypotheses are not being recorded as root cause. Related, disclosed: the scratch repro ran without `HARNESS_RUNS_VOLUME`, so its `down -v` targeted the LIVE `harness_runs` volume — Docker refused removal because it is in use (dev stack unharmed, `harness_runs` present, pg healthy), per the compose's documented fixed-name rule. The repro itself was otherwise clean and was removed.

For the merge decision's legibility: runs `41774029511` (green) and `41774372699` (red, boot race) are explained above, and the merge candidate `41775829908` passed fully.

**Verdict unchanged: NOT READY** for production or a general pilot.

Open items carried forward: W17 lost-lease re-dispatch shipped **in this merge** (`76317e4`: migration 0073, attempt-keyed `runner_jobs`, `evidence_from_seq`, 5 attempt-scoped tests green), so it does not block W31; W7 real-window VS Code task owed; external human gates (counsel TERMS/PRIVACY, SLO/on-call adoption, dedicated sandbox worker certification, Hermes rights) untouched; status of the master CI cadence post-merge to be confirmed by the next 06:17 nightly.

## 2026-11-04 — CI repair wave: nightly lane green, three PRs merged (CI, not product)

The CI red that had been carried since the PR #7 merge was not one defect but a
stack of them. Fixed and merged in `ci-nightly-greens` (**PR #14**, merge
`5f827ff`), verified by master push run `41848168032` **success** and by a
dispatch-only nightly run `41847074335` **all jobs green** (the nightly job is
`schedule`/`dispatch` only, so a push run can never prove it):

1. **Nightly PG probe could not boot.** It started a second Postgres on :5499
   while the job's own service container already owned that port — every run
   died `address already in use`. It now targets the service container
   (`HARNESS_TEST_PG_EXTERNAL=1` + `HARNESS_TEST_PG_CONTAINER`) and applies
   `deploy/runners/migrate.py` first, because the service DB starts empty.
2. **Real EventBus race.** `subscribe()` returned before `LISTEN` was attached,
   so a publish landing in that window was delivered to nobody. It now awaits a
   `_listener_ready` event set only after the listener connection is armed. This
   was a product bug, found by a test that had been flaky rather than skipped.
3. **Wall-clock-dependent rate-limit bursts.** Fixed-window burst tests read
   real time and failed when the window rolled mid-test; a `fixed_window`
   fixture now pins `harness_identity.ratelimit.time`. Same class fixed in
   `test_ratelimit_expensive_ops.py`.
4. **Partition-retention test depended on today's date.** It now detaches
   partitions outside its frozen assertion window in the scratch DB, so it is
   calendar-independent.
5. **gitleaks full-history findings (16).** Each was traced to a synthetic
   fixture or a public docs example before being allowlisted by path — no
   blanket suppression, and nothing that looked like a live credential was left
   unexamined.

**PR #15** (`live-pg-probes`, merge `0ab9aa6`) un-skipped the section-4 live-PG
suites by giving the nightly job the venv they need: 11 `*_live_pg.py` files
(session 4/5/6, collab chaos/approvals/race codes, budget delivery, workspace
jobs, cost rollup, demo seed, purge/lease sweep). Their hand-split migrations
missed `ensure_forward_partitions`, so a fresh service DB failed them; each now
calls it, and the cost-rollup proof compares all closed history instead of the
current month. Verified on nightly run `41852048029` — the new
`Section4 live-PG probes` and `Section11 live-PG probe` steps both green.

**PR #16** (`portable-container-proof-tests`, merge `bc2edbb`) fixed the two
container mount proofs that had been skipping: they pinned historical local image
tags (`harness-engine-worker:m03`/`:b1`) and now use `python:3.13-alpine`, which
any Docker host can pull. The first CI run then failed on a real portability
assumption — Linux returns `EACCES` where macOS returns `EROFS` for the same
read-only bind — and the assertion now accepts either, since both prove the
write did not land (run `41853953406`, section4 green in 2m43s).

Master CI is green for all three merges (`41848168032`, `41854647290`,
`41854662877`).
**PR #17** (`nightly-context-roundtrip`) closes the last skipped-but-real test:
the @refs round-trip drives the real section11 app (its ASGI routes, auth
dependency and index job) through that section's own venv, which normal CI never
provisions, so it now runs in the nightly job where section11 is installed
anyway; `docs/ci-gates.md` gained a table of exactly what that lane un-skips and
why. Nightly dispatch `41855968014` passed every job, including that step, the
11-file section-4 live-PG set and the section11 live-PG probe (job 6m7s).

Remaining skips are environment gates, not coverage holes: landlock
(`sys.platform != linux`), Seatbelt (macOS only), `HARNESS_P0_DOCKER` /
`HARNESS_P0_LIVE` opt-ins, `promtool`, and provider-key live tests. On
`ubuntu-latest` the landlock half runs for real inside `pytest (section5)`.

**This entry changes no product verdict.** The verdict below still stands: green
CI says the gates that exist now pass, not that the product is pilot-ready.



The 10x program (missions 11–23, per docs/briefs/mission17-10x-plan.md) is
complete. After-action report with calibration notes:
docs/briefs/m23-aar.md. Lane scoreboards for missions 18–23:
evolution/ledgers/. Growth chart regenerated to final state (all missions
closed): scripts/campaign_growth_chart.py →
docs/briefs/mission19-growth-chart.png.

Final suites re-run INDEPENDENTLY by the close-out lane at finale HEAD
(numbers below are this session's own runs, not inherited claims):

- **section1: 628 passed · 10 skipped** — first sweep showed ONE
  load-dependent failure (`test_ws_replay_cap.py::
  test_resume_never_resends_at_or_below_afterseq_during_concurrent_publish`)
  under full-suite parallel load; it passed isolated ×6/×6 and the full
  rerun was green. Recorded honestly rather than hidden (see risk #1).
- **section3: 91 passed · section4: 465 passed · 5 skipped ·
  section11: 50 passed · mcp: 17 passed** (throwaway venv; mcp/pyproject.toml
  omits pydantic_settings from its deps — flagged for the polish lane).
- S7 vitest/tsc not re-run by this lane; last verified green at the Mission 21
  formal close (500 vitest + tsc clean, CI 32948436346 SUCCESS).
- Campaign totals: **264 commits** repo-wide at close (**168** inside the
  campaign window `d5d9619^..HEAD`), span 2026-08-22 → 2026-08-26.

Residual risk — what a v1.0 buyer should know:

1. **Replay-cap "flake" — diagnosed, reproduced and fixed (2026-10-31)**: the
   concurrent-resume test recovered a truncated page's skipped range with a
   plain `afterSeq` walk, and that path is newest-N too under
   `REPLAY_MAX_EVENTS` — so when a loaded runner let the gap outgrow the cap,
   the walk could not reach the rows the resume page had skipped and reported a
   loss that never happened. The test now fills each page's
   `(cursor, oldestSeqSent)` hole through `before_seq`, and
   `test_a_capped_resume_page_names_a_hole_a_client_can_fill` exercises the cap
   deterministically, so the load-dependent timing is no longer the only way in.
   Measured: the file green 20× consecutively, full section 1 green at 1223
   passed / 63 skipped, and a probe with the reader slowed to 20 ms fails on the
   old protocol and passes on the new one. The bar this entry set for itself —
   a ≥25-run **full-suite** census — has not been run; it measured frequency,
   and the frequency question is now answered by the mechanism above.
2. **pgvector-dependent features ship honestly-OFF** on the default compose
   image (postgres:18-alpine has no pgvector); migration 0030 degrades
   cleanly without it.
3. **Stripe and OIDC SSO are code-complete but never drilled against live
   providers** (CI exercises mocked Stripe only). TERMS/PRIVACY still carry
   `[PLACEHOLDER: counsel review]`; the MIT decision-flag header stands.
4. **Mission 22 never received an independent verifier pass** and had no
   STATUS close entry until this one — its tier-cap/SSO/MCP completeness
   rests on commit-message evidence (72cf901).
5. **Unbuilt from the 10x plan**: editor surface (C16 hunk accept/reject +
   snapshot checkpoints) and the MCP *client*/tool-registry direction (C17;
   only the server half shipped in M22-M5).
6. **Scale envelope is single-host and dry-engine-measured** (~37.6
   journeys/s @ 32 VU post-T1); multi-replica production operation is
   probed (N1 exactly-once) but not operated.
7. **NUMERIC(12,6) money ceiling**: budgets cap at $999,999.999999/team
   (operator note since M13).
8. **PITR is proven on the local stack only**; a real object-store (S3)
   WAL-G round-trip has never been exercised.

Record holes closed by this entry: missions 17 and 22 previously had no
STATUS close entries (completions attested by commits/side records only).

## Mission 11 / Wave 1 (2026-08-23) — CLIENTELA remediation: six CRITICALs + flow-blocking HIGHs

Baseline d5d9619, 7 lanes (graph: docs/briefs/mission11-wave1-graph.md). Sole-owner
law held all wave: services.py include block = C1 only; compose engine env block =
B1 only; state_machine.py = D1 only. Lane-by-lane:

1. **W1-A1 — C-001/H-004** (dc9b75f): verified ownership behind OTP — rate limiter
   DEFAULT_ROUTES moved onto /auth/request-login + /auth/verify-otp (dead legacy
   route removed); unverified team-create 403, burst 429+Retry-After with window
   reset, double-redeem rejected. Legacy direct-add removal lands at ATLAS merge
   (app.py is ATLAS-owned).
2. **W1-A2 — C-002** (7a6b2b3): audit emission wiring — audit_emit.py typed helpers
   (never-throw, secret-scrubbed) called at every sensitive site (api_keys,
   invites, billing); run.started/stopped helpers shipped as a merge seam for S4.
3. **W1-C1 — C-005 backend** (281d769): plan-gate orchestrator API per the verbatim
   W1-C1↔C2 pair contract — POST /api/v1/orchestrator/plans → 201 {planId, plan};
   approve → 200 {runId} idempotent; reject → 202 persisted+audited; role gates
   admin/engineer decide, viewer/guest 403; sole include block in services.py.
4. **W1-C2 — C-005 UI + C-006** (c2b487c): PlanGate card off status.state;
   planGateApi.ts mirrors the contract DTOs field-by-field; login routes real
   post-auth identity — adopt first team project or switch to create-project flow;
   fabricated proj-test-1 / run-timestamp literals deleted from source (runs are
   minted server-side on approve now).
5. **W1-B1 — C-004/H-005/H-006** (db9e333): engine boot integrity — engine_boot.py
   validates required env and exits non-zero naming vars (never values);
   contracts-path bootstrap for bare checkouts; compose engine block free of
   ollama placeholder/dev defaults; live_mode explicit {live,dry} enum; downgrade
   ladder removed.
6. **W1-A3 — C-003 budgets** (IN FLIGHT, uncommitted tree): budget_guard.py (S3)
   + cost_ledger.py writers (S1), atomic fail-closed reserve; test_budget_atomic.py.
7. **W1-D1 — H-007/H-008 done-signal** (IN FLIGHT, uncommitted tree): signals.py;
   role gate + non-empty diff + no resurrection; reviewer_passed→DONE terminal;
   reject_plan persisted+audited (state_machine.py is D1-owned — C1 reads only).

Glue lane (this entry): services.py import hygiene — both I001 blocks sorted
(pre-existing at baseline d5d9619, proven; sort-only, zero behavior delta);
fabricated-literal final sweep CLEAN (proj-test-1 / run-${Date…} survive only in
docs/clientela audit cards as historical record and in gitignored .next build
cache — zero occurrences in any shipped source); plan-gate conformance goldens
added at section4/tests/conformance/test_plan_gate_conformance.py (golden walks
over the pair-contract routes: 201 propose → idempotent 200 approve → gated
start executing; 202 audited reject ⇒ subsequent start 409 PLAN_NOT_APPROVED;
ROLE_DENIED / PLAN_NOT_FOUND error_body envelopes frozen).

Known drift & gate notes for ATLAS:
- Migration numbering: wave pre-assigned 0012 to auth hardening, but M10 already
  used 0012 (agent_events run_id index) — next free slot is 0013.
- S4 canonical suite (`section4/.venv`): 225 passed / 1 skipped, with 3 known-red
  TestEngineSingleModel stress cases — old wrappers construct engines without
  DEEPSEEK_BASE_URL and B1's new fail-closed boot validation trips them;
  engine-boot lane reconciles before merge.

## Mission 10 (2026-08-23) — ALEX_TEAM: Phase 2 completion + Phase 3 edge-hardening begins

First full ALEX_TEAM run: ATLAS + 2 lead review ladders + 10 parallel subagent
lanes in two squads, three PAIR×2 lanes on written interface contracts
(M10-C1 keys, M10-C2 runs, M10-C3 billing). Graph:
docs/briefs/mission10-graph.md, baseline abcab51. Two provider-outage deaths
(one 43-min explore-then-timeout with zero writes; one dispatch-call timeout
that actually spawned fine) — footprint-audited before each re-dispatch.

1. **BYO keys (M10-C1)** — /api/v1/keys CRUD over frozen api_keys table:
   KeyVault-encrypted at rest, hint-only exposure, idempotent soft revoke,
   foreign-team 404 without existence oracle; S7 /settings/keys page.
2. **Cost dashboard** — S7 /dashboard/cost over live /spend: range selector,
   totals cards, pure-CSS byModel bars, SVG sparkline, honest empty state,
   explanatory notice when metering unwired (503).
3. **Run history (M10-C2)** — GET /api/v1/orchestrator/runs team-scoped with
   server-driven paging (limit≤100 clamp) + filters; startedAt/finishedAt
   honestly derived from agent_events (frozen agent_runs lacks columns); S7
   /runs page w/ status pills and relative times.
4. **Billing (M10-C3)** — /api/v1/billing/state|plan behind HARNESS_BILLING;
   flag-off renders honest upsell; local quota = cost_ledger SUM per month vs
   teams.budget_usd; Stripe adapter is an injectable protocol (no package);
   migration 0010 billing_plans; S7 /settings/billing page w/ meter.
5. **Onboarding revival** — S10 checklist API derives `done` from real state
   only (no fabricated signals); interactive S7 /onboarding page w/ progress.
6. **WAL-G PITR** — deploy/walg scripts (base/wal push, PITR restore to target
   time), compose `walg` profile, Dockerfile.walg, runbook "WAL-G operation"
   section with explicit validated/not-validated split (needs S3 creds to
   exercise).
7. **Rate limiting** — fixed-window Postgres limiter on login/OTP routes:
   atomic ON CONFLICT upsert, 429+Retry-After error_body envelope, DB failure
   fails OPEN (availability > strictness); migration 0011 rate_buckets.

ATLAS wirings: keys+billing routers registered in app.py; rate limiter
installed at assembly level (app_identity) NOT in make_app — tests build
un-limited apps and install tuned limiters (double-install caught live at
merge: 6 red tests → moved wiring → 134 green). Nav links added (Runs, Cost +
settings-hub Workspace links). Gate sweep: S1 134 / S2 19 / S3 66 / S4 134+1
known LIVEPROBE flake / S5 118+1skip / S6 38 / S8 38, S7 tsc OK + 73 vitest,
section10 15 *(historical M10 figure — re-counted 65 collected / 65 passed on
2026-08-23, Mission 12 G4 docs-truth lane; the suite grew in W1/W2)*,
ruff clean everywhere, compose valid incl. walg profile.

## Wave 9 (2026-08-23) — Phase 2 SaaS core begins: metering, budgets, orgs, notifications, security headers

Five parallel subagent nodes (graph: docs/briefs/wave9-task-graph.md, baseline
6d5e52c). Three first attempts died to provider outages (HTTP 503 ×2, 300s API
timeout ×1) with ZERO tree writes each — all re-dispatched and completed. Every
node's delivery was independently re-verified by the orchestrator (own test
reruns + live probes), then two merge wirings landed:

1. **P2-METER (S3)** — cost_ledger has real writers: record_usage on every
   successful call_model, fire-and-forget swallow-on-failure;
   GET /api/v1/models/spend aggregates totals/byModel/byDay; app_models now
   boots an asyncpg ledger pool at startup and drains pending writes on
   shutdown. S3 66 passed.
2. **P2-BUDGET (S4)** — check_budget enforced pre-spawn and on reviewer model
   calls; cap = teams.budget_usd (migration 0007) > HARNESS_TEAM_BUDGET_USD >
   unlimited; over-cap ⇒ honest blocked event. S4 123 passed + 1 known flake
   (LIVEPROBE OpenRouter outage during this session — D2 honesty guard working,
   unrelated).
3. **P2-ORGS (S1+S7)** — team_invites (0008) with hashed tokens, admin-only
   mint/revoke, atomic redeem (404/409/410 honest codes), members roster;
   S7 /settings/team page (roles pills, invite form, revoke, join-by-token);
   verified against live Postgres. S1 105 passed; S7 vitest 58 passed.
4. **P2-NOTIF (S1+deploy)** — notification_outbox (0009); templates
   run_finished(←engine_completed)/review_failed/pr_opened; worker drains with
   max 3 attempts; dev mode offline-green; EventBus gained a disclosed,
   exception-guarded listener tap so notifications can never break publishing.
5. **SEC-HEADERS (all apps + CI)** — crypto audit verdict NO GAP (regression-
   tested); pure-ASGI middleware (nosniff/DENY/referrer everywhere, HSTS only
   behind HARNESS_TLS_ENABLED=1, report-only CSP on HTML) wired into
   S1/S4/S5/S6/S8 factories; ci.yml gained security-scan job (pip-audit WARN +
   gitleaks).

Merge wirings by orchestrator: invites router registered in app.py;
set_ledger_pool wired in app_models. Gate sweep: **7 sections pytest green
(105+19+66+124+119+38+38 incl. skips), ruff clean everywhere, compose config
valid**. Known-open follow-ups: WAL-G PITR (runbook documents it), real TLS
certs (needs domain), Stripe billing flag, BYO keys UI.

## Working right now

- `docker compose -f deploy/docker-compose.yml up` → healthy stack (PG + 7
  backends + frontend). Migrations 0001–0003 applied via migrate.py.
- **388 pytest green** (incl. real-PG fanout test + 9 OTP tests + 5 confinement
  tests) and **43 vitest green**; ruff clean; tsc clean.
- Live proofs today: ticket-auth WS delivering sandbox `terminal_output`
  cross-instance in 0.7s; orchestrator flow; restart rehydration.

## Wave 6a (2026-08-22, later session) — checkpoint-aware resume + ops hygiene

1. **RESUME DONE (S4)**: `resume_agent` previously flipped PAUSED→EXECUTING without
   re-spawning any engine task — UI said "executing" over a dead run (Appendix C lie).
   Now resume re-invokes `execute_goal` with the SAME run_id passed as the
   DeepSeek Harness ("dsh") session id, so an explicit human Resume CONTINUES
   the upstream engine session instead of restarting blind.
   Pause writes no terminal result (run stays resumable); stop stays terminal with an
   honest `cancelled`; live-run double-spawn guarded; same-tick pause→resume race fixed
   by awaiting the dying task. 5 new tests (`section4/tests/unit/test_resume.py`);
   integration test_checkpoint1 re-run green (4/4).
2. **Ops hygiene**: db service gained `restart: unless-stopped` (host restart had left
   the backend DNS-crash-looping for hours); root-level debug logs gitignored;
   Part 10 ledger synced with shipped reality (H2r/E-s3/E-cat/E-codegen/M4/M5/CI struck);
   RESUME recorded done. Appendix C FAQ annotated.
3. **Wave-6 planning doc committed** (docs/EXPANSION_PLAN.md) from the parallel
   planning session; its D2 engine experiment was rolled back by that session before
   handoff — tree verified clean, fallback-chain tests green again.

Verified this session: stack healthy on all 7 ports + frontend; seed_demo idempotent;
CI green (~36s runs); nightly probe schedule present.

## Wave 6b (2026-08-23) — Phase 0 landed (D1–D4) + OpenRouter live probe

1. **D1–D4 landed** (from docs/EXPANSION_PLAN.md Phase 0, authored by the wave-6
   planning session): D1 compose model drift fixed; D2 empty-completion honesty
   guard (fake-success now falls through the fallback ladder to an honest error);
   D3 per-run publish chains (bus seq order matches causal order); D4 additive
   engine lifecycle events — contracts 0.2.0→0.3.0, `engine_completed`/
   `engine_failed`, TS mirror regenerated same commit, SM advances
   EXECUTING→AWAITING_DONE_SIGNAL on completion / BLOCKED on failure (guarded to
   EXECUTING so pause/human-edit narratives win), golden fixtures + S1 conformance
   pin updated, new publish-ordering + SM + engine tests. Failed runs land in
   BLOCKED, which RESUME can legitimately resurrect.
2. **LIVEPROBE**: flaky local-Ollama live test replaced by an OpenRouter probe
   (model stealth/ox-alpha, per user) asserting the real filesystem side effect;
   fresh workspace/session dirs per run — reusing the dsh session root had poisoned
   reruns with dead session history (completed-with-0-chars). Skips without
   OPENROUTER_API_KEY so CI stays green offline.
3. Gate: 370 pytest (S4 72/72) + 58 vitest green; ruff clean; TS mirror in sync;
   compose config valid.

## Wave 6c (2026-08-23) — Phase 0 COMPLETE: D5 authz + D6 auth wiring

Phase 0 of docs/EXPANSION_PLAN.md is now fully landed (D1–D4 in 6b; the last two
defects closed here).

1. **D5 DONE (S4)**: every `/api/v1/orchestrator/runs/{runId}` route now runs a
   single authorization dependency (`_authz_dep(action)` → `authz.py`): identity
   via app.state bearer dep, team ownership via `agent_runs.project_id` →
   `projects.team_id` join (raw SQL on frozen names; skipped in bare unit mode
   like rehydration), and role permission via frozen `roles.can()` — approve/
   reject need "approve", run control needs "write", GETs are read-open.
   `propose` rejects foreign AND unknown projectId (TEAM_OWNERSHIP 403) so a
   propose can never mint a run into another team's project. 10 regression tests
   (`tests/unit/test_authz.py`) incl. viewer-can-read-but-not-drive and
   guest-cannot-pause. S4: 82 passed.
2. **D6 DONE (S5/S6/S8 + deploy)** — implemented by Codex CLI (`--profile ox`,
   OpenRouter stealth/ox-alpha) from a written brief (`docs/briefs/2026-08-23-d6-codex-auth-wiring.md`), diff
   reviewed and independently re-verified by ox-alpha:
   - Fail-closed `get_principal` dependency on EVERY route of all three services
     (401 bad token / 503 unwired — no open endpoint anywhere).
   - Identity always from the principal: S6 body `userId` and S8 body
     `reviewer_user_id` kept wire-compatible but IGNORED; S8's spoofable
     `x-user-id` header fallback DELETED (`_resolve_reviewer` removed).
   - S5 adds a write-gate: viewer/guest roles get ROLE_DENIED 403 on all four
     task routes (executing code is a write).
   - Deploy assembly wires `make_bearer_dep(factory)` into `app_sandbox` and a
     rebuilt `app_meeting` (own factory + CORS + /health); `app_review` already
     had it — now its routes actually consume it.
   - Tests: S5/S6 conformance updated to principal dependency-overrides + new
     unauthenticated-rejection tests; new `section8/tests/unit/test_api_auth.py`
     pins forged-body and x-user-id spoof rejections. S5 118 passed,
     S6 38 passed, S8 38 passed.
3. **CI fix en route**: ruff F841/F401 in S4 engine tests had failed the
   `pytest (section4)` job on commit 15326db; dropped in 72bd255 (suite green
   since).
4. Full sweep this session: 477 pytest across 8 sections + 58 vitest + tsc +
   ruff (all sections) + sh -n + compose config + TS-mirror --check, ALL GREEN.

Phase 0 exit criteria met except live-stack re-smoke (PG stack was down locally;
nightly CI covers the real-PG probe). Next up per plan: Phase 1 production
architecture (split containers, durable run queue, cross-process state).

## Wave 7 (2026-08-23) — Phase 1 starts: cross-process ephemeral auth state

Implemented by Codex CLI (`--profile ox`) from a written brief
(docs/briefs/2026-08-23-p1a-cross-process-state.md); diff reviewed and
independently re-verified by ox-alpha, which also caught + fixed one defect in
the delegation's output (see below).

1. **P1-XSTATE DONE (S1/S2)**: WS connect tickets and GitHub OAuth states moved
   from `app.state` dicts into Postgres (migration `0005_ephemeral_state.sql`,
   tables `ws_tickets` / `oauth_states`). Stores:
   `section1/src/harness_identity/ephemeral.py` (mint/redeem with atomic
   single-use `DELETE … RETURNING`, project-bound, opportunistic expiry sweep on
   mint) and `section2/src/harness_github/state_store.py` (insert/consume,
   same pattern). In-memory dicts deleted; HTTP contracts unchanged; tickets/
   states now redeemable on ANY replica.
2. **Timestamp discipline**: both stores write UTC ISO-8601 TEXT and migration
   0005 declares TEXT columns — SQLite keeps DATETIME as text, and mixing
   adapter-formatted datetimes (space separator) with `.isoformat()` rows (T
   separator) made an EXPIRED ticket compare as valid under string comparison.
   Caught via the new `test_ws_ticket_expired_rejected` regression; fixed by
   normalizing at every boundary so SQLite and PostgreSQL run identical code.
3. Tests: S1 ticket suite extended (roundtrip, single-use replay, expired,
   wrong-project, cross-instance over one shared SQLite file) → S1 50 passed;
   S2 OAuth state suite likewise → 19 passed. Ruff clean both.

Next Phase 1 candidates: split-container compose shape (brief drafted),
durable run queue (`run_claims` SKIP LOCKED), structured logs/metrics.

## Wave 7b (2026-08-23) — split-container deployment, live-proven

Implemented by Codex CLI (`--profile ox`) from
docs/briefs/2026-08-23-p1b-split-container.md; verified by ox-alpha against a
REAL `docker compose up -d --build` boot (not just static checks):

1. **P1-SPLIT DONE**: one image, service selected by `HARNESS_SERVICE`
   (`migrate|identity|github|models|orchestrator|sandbox|meeting|review`).
   entrypoint.sh: unknown values fail fast; `migrate` runs schema+seed then
   exits 0; single-service mode execs ONE uvicorn on its port (workspace copy
   only for sandbox/orchestrator); unset var keeps the legacy all-in-one flow
   byte-identical. docker-compose.yml: YAML anchors (`x-backend-build/env/
   depends`) expand into db + migrate (one-shot, restart:no) + seven services,
   each with its own port and self healthcheck, all gated on
   `service_completed_successfully` from migrate; frontend now waits on
   identity. services.py unchanged (factory names already matched).
2. **Live proof (this session)**: full stack booted split — migrate applied
   0005_ephemeral_state.sql to real Postgres then exited; all seven backends
   healthy on their own ports + frontend 200; unauthenticated calls to S5/S6/S8
   → 401 (D6 holds in containers); login → ws-ticket mint → WS upgrade with
   ticket CONNECTED, replay of the same ticket rejected (single-use across
   processes, P1-XSTATE proven live); authed meeting create on :8006 → 201.
3. Ops note: re-running `docker compose run --rm migrate` after creating new
   teams seeds their starter project (idempotent) — used during live probing.

Phase 1 remaining: durable run queue (`run_claims` SKIP LOCKED), structured
JSON logs + request IDs, Prometheus /metrics, secrets hardening.

## Wave 7c (2026-08-23) — durable run queue (crash-resumable execution)

Implemented by Codex CLI (`--profile ox`) from
docs/briefs/2026-08-23-p1c-durable-run-queue.md; diff reviewed and independently
re-verified by ox-alpha, which caught and fixed one production-breaking defect
in the delegation's output (asyncpg rejects ISO strings for TIMESTAMPTZ —
verified live against the compose Postgres before fixing; `_persist_plan` now
binds real datetime objects, ISO text remains reserved for text-compared
columns).

1. **P1-QUEUE DONE (S4 + migration 0006)**: `run_claims` table
   (queued/running/orphaned, TEXT ISO timestamps, no FK — claims may precede
   the lazy `agent_runs` insert). Store `run_queue.py`: enqueue with
   orphan-requeue + double-start guard; ONE atomic FIFO claim statement with a
   per-team running-count cap (`projects` join); worker-guarded heartbeats;
   finish-on-settle; `reclaim_orphans` flips stale heartbeats (90s default,
   10s heartbeat cadence) — a killed container's runs become claimable again.
2. **Service integration**: start/resume enqueue instead of spawning when a
   factory is present; the SM stays EXECUTING exactly as before; `_execute`
   heartbeats and finish-runs in `finally`; `poll_once()` rehydrates the
   claimed run (crashed-process EXECUTING-with-dead-task is precisely the
   resurrection case) and drops claims for PAUSED-rehydrated runs honestly;
   bare unit mode (no factory) spawns directly — every pre-existing suite
   untouched and green.
3. **Worker + deploy**: `RunQueueWorker` loop (recover-on-start, never dies on
   one bad poll) wired into `app_orchestrator` via startup/shutdown hooks;
   `HARNESS_RUN_CONCURRENCY` (default 4) and `HARNESS_WORKER_POLL_SECONDS`
   (default 0.5) env knobs.
4. Gate: S4 90 passed (8 new queue tests incl. FIFO, team cap, heartbeat
   guard, orphan requeue, crash-resume story, service enqueue→poll→complete,
   bare-mode regression); full 8-section sweep 447 passed; ruff clean; TS
   mirror in sync. Live-PG boot of the new migration happens on next
   `docker compose up` (migrate one-shot picks up 0006 automatically).

Phase 1 remaining: structured JSON logs + request IDs, Prometheus /metrics,
reverse proxy/TLS, backup/PITR runbook, secrets hardening.

## Wave 8 (2026-08-23) — parallel subagent wave: observability + TLS + backup

Orchestration pattern (new for this wave): ox-alpha built a task-dependency
graph (docs/briefs/wave8-task-graph.md) with strict per-node file ownership,
dispatched THREE subagents in parallel, independently re-verified every
delivery, and merged at the gate with zero conflicts. Graph tracked state
live; merge checklist fully ticked.

1. **P1-OBS DONE (S4, node A)**: JSON structured logging ({ts,level,logger,
   msg,requestId,method,path,status,durMs}) via contextvars request-id +
   RequestContextMiddleware (X-Request-ID echoed/minted on every response);
   hand-rolled thread-safe Prometheus /api/v1/orchestrator/metrics — counter +
   histogram with ROUTE-TEMPLATE path labels (raw ids leak-checked clean);
   HARNESS_LOG_FORMAT env knob. Verified through the real deploy app.
2. **P1-TLS DONE (deploy + S3, node B)**: Caddyfile harness.local (tls
   internal) routing all 7 backends by path prefix incl. WS passthrough;
   compose `proxy` service gated behind a `proxy` PROFILE (default up
   unchanged); S3 public /api/v1/models/metrics counter. caddy validate green;
   compose config green in both profile modes.
3. **P1-BACKUP DONE (node C)**: deploy/backup/{backup,restore,verify_restore}.sh
   (custom-format dumps, integrity gate, retention, drop-recreate restore w/
   prompt, scratch-db verify vs live row counts) + docs/runbooks/
   backup-and-pitr.md (explicit RPO/RTO, cron, honest WAL-G/pgBackRest gap).
   Orchestrator re-ran backup + verify live: all key tables PASS identical.
4. Gate: full sweep **461 pytest** across 8 sections green; ruff clean;
   sh -n + compose config (both modes) + TS mirror OK. Zero cross-node
   conflicts (file-ownership graph held).

Phase 1 remaining after this wave: real TLS certificates cutover (config-only,
needs a domain), WAL-G/pgBackRest PITR (documented next step), secrets
hardening, frontend e2e (Phase 3 territory).


## Wave 2 (this session) — read docs/GAP_ANALYSIS_AND_FIXES.md for full detail

1. **H8 DONE**: one-time WS tickets (`POST /api/v1/ws-ticket`, 30s, project-
   bound, pop-first redemption). S7 mints a fresh ticket per connect; bearer
   tokens no longer ride URLs. 8 new S1 tests + frontend rewired.
2. **M2 DONE**: additive EventTypes `terminal_output` / `diff_progress`
   (contracts py+ts, conformance pin now "twelve types"). S5 emits via injected
   `on_stream` (per-line streaming + edit progress); deploy bridges to the bus;
   S7 deriveView exposes capped `terminalLines` → TerminalPanel renders live.
3. **G-LIVE FOUND+FIXED (FATAL)**: two EventBus instances per stack meant live
   fan-out never crossed processes; and seq-gap close(1013) was wrong because
   `agent_events.seq` is table-GLOBAL (cross-project jumps are normal).
   Fix: migration 0003 pg_notify trigger + per-instance LISTEN/NOTIFY delivery;
   overflow detection moved onto a per-subscriber `overflowed` flag set by the
   bus itself. Proven live across a 27→28 seq jump.
4. **Engine fallback chain**: `DSH_FALLBACK_MODELS` env or ctor list; primary →
   fallbacks → honest `error`; never fake success.
5. Quick wins: `/health?deep=1` DB ping; bounded `list_projects?limit=`.

Incidents caught by tests/live probes this wave (details in GAP doc Part 5):
asyncpg ≥0.30 `add_listener` is a coroutine; `expire_on_commit=True` factories
made `row.seq` lazy-load outside greenlet after commit (flush-before-commit now);
migration splitter needed $$-dollar-quote AND comment-line awareness; host 5432
already taken → compose publishes PG on 127.0.0.1:**5499**.

## Wave 3 — both public-exposure blockers closed

1. **H2r DONE**: `HARNESS_REQUIRE_OWNERSHIP` flag (default off for dev) gates the
   passwordless hole behind an OTP flow — request-login (enumeration-safe,
   hashed codes, 5-attempt lockout, single-use) → verify-otp provisions exactly
   like legacy login. Migration 0004. Verified live: flag-on legacy=403, full
   request→verify→/me→replay-401 chain; flag-off dev login still 200.
   Dev code delivery prints `HARNESS-OTP ...` to container logs; SMTP is a hook.
2. **F6r DONE**: OS confinement around LocalSandboxRunner — Seatbelt on macOS
   (deny-default profile, writes confined to workspace+tmp, network flag),
   Landlock on Linux ≥5.13 (write-family-only restriction; degrades safely).
   auto|seatbelt|landlock|off per runner; 5 macOS tests execute REAL profiles;
   container sanity passed post-rebuild.

To run the platform with ownership enforcement on:
`HARNESS_REQUIRE_OWNERSHIP=true docker compose -f deploy/docker-compose.yml up -d --build backend`

## Wave 5 (latest) — GitHub OAuth connect flow + exhibition demo readiness

1. **E-oauth DONE (S2)**: real GitHub web application flow — `GET /api/v1/github/oauth/start`
   (302 to github.com/login/oauth/authorize, CSRF `state` bound server-side to the
   session user), `GET /api/v1/github/oauth/callback` (code→token exchange, single-use
   state, token encrypted at rest via KeyVault into `github_connections`, browser lands
   on `{frontend_base}/settings?connected=<login>` or `?error=<ExcName>` — never a stack).
   Browsers can't send Authorization headers on navigation, so a one-time
   `POST /api/v1/github/oauth/start-ticket` (60s TTL) bridges fetch→redirect.
   `GET /api/v1/github/connection` → `{connected, login, scopes}`; no token ever in a
   response body. Unconfigured creds ⇒ clean 503 `NOT_CONFIGURED`. 10 new tests pin the
   flow incl. replay/unknown-state rejection and ciphertext-at-rest assertions.
   Compose maps GITHUB_CLIENT_ID/SECRET (+FRONTEND_BASE/GITHUB_REDIRECT_URI) onto S2's
   HARNESS_ prefixed settings; legacy JSON `/oauth/url` endpoint removed.
   Runners base settings (deploy/runners/services.py) gained the S2 fields so the
   shared settings class can't AttributeError on the new config.
2. **Settings page DONE (S7)**: `/settings` with Connect GitHub button driving the
   ticket-hop flow, connected state (`@login`, scopes), error banner, URL-param cleanup;
   wired via `connectGithub()`/`getGithubConnection()` in src/lib/api.ts. Added to
   AppShell nav.
3. **Working nav restored (S7)**: AppShell "Working" item re-added; links to
   `/projects/{projectId}/runs/{runId}` from localStorage; disabled with tooltip when
   either is missing.
4. **Demo seed DONE**: deploy/seed_demo.py drives only the live REST API (session,
   project match-by-name, repo connect upsert) and prints the localStorage lines for
   DevTools. Idempotency fix: S1's passwordless login mints a NEW solo team (and an
   auto-seeded `demo-repo` project) per call — the script now persists its session to
   deploy/.seed_session.json (gitignored) and reuses it while valid, verified by two
   consecutive runs converging on the same team/project.

Test totals: 396 pytest + 58 vitest green; ruff clean; tsc clean; next build green.
Live proofs: :8002 start w/o creds → 503 NOT_CONFIGURED body; bogus callback state →
400 INVALID_STATE; unauth connection → 401; seed run twice → same team/project,
"already connected".

Remaining known work: none from this task's scope. Ledger additions: E-seed recorded
fixed-in-wave-5, Appendix F documents the OAuth design (per-process oauth state is
fine single-container; move to DB if services scale out). Still open from earlier:
M4/M5 trivia, E-s3, nightly probes (CI job).

## Wave 4 — ledger cleared: consolidation + E-smtp + E-cat + CI + E-codegen

1. **S7/S9 consolidation DONE**: one frontend now. S7 gained S9's design system,
   AppShell, and pages (command center, meeting, review, login) wired to the real
   typed API layer (meeting/review/OTP clients added, TDD'd). S9 retired
   (`git rm`); deploy/frontend.Dockerfile + compose now build section7.
   Live: all four routes 200 on :3000 against the running stack.
2. **E-fe-otp DONE**: /login is flag-aware — legacy login auto-switches to the
   request→verify OTP flow on 403 OWNERSHIP_REQUIRED. Verified live end-to-end:
   request → code in container logs → verify-otp → admin session on :8001.
3. **E-smtp DONE**: `notifier.py` — SMTP via HARNESS_SMTP_* env (host/port/
   user/pass/from/starttls), sync smtplib in a worker thread, delivery failures
   logged never raised (enumeration-safety kept). No host configured → visible
   dev log printer. 6 new tests; compose env-wired.
4. **E-cat DONE**: S3 `catalog.py` — declarative provider catalog with env-var
   indirection (names only), coverage asserted against the pricing catalog
   (caught a missing qwen provider), local fallback candidates = zero-priced
   models. GET /api/v1/models/catalog exposed; S4 engine consults it as
   last-resort fallback chain (ctor > env > catalog).
5. **CI gates DONE**: .github/workflows/ci.yml — sh -n entrypoint + compose
   config + TS-mirror drift check, pytest+ruff matrix over all 8 Python
   sections, tsc+vitest for section7, nightly real-PG probe job.
6. **E-codegen DONE**: contracts/ts/generate.py — AST-based pydantic→TS mirror
   generator; --check gate in CI makes hand-editing the mirror pointless.
   Regenerated mirrors; optional fields handled; consumers updated.

Test totals: 395 pytest (+4 PG integration) + 58 vitest green; ruff clean;
tsc clean; next build green.

Remaining known work: none from the GAP Part 10 ledger. Next candidates:
checkpoint-aware resume (Appendix C), per-service containers if scale demands,
real GitHub OAuth app for repo connection.

To run the platform with ownership enforcement on:
`HARNESS_REQUIRE_OWNERSHIP=true docker compose -f deploy/docker-compose.yml up -d --build backend`

## Known gaps / next actions (GAP doc Part 10 ledger)

1. **E-smtp**: wire real email delivery into the notifier hook before enabling
   HARNESS_REQUIRE_OWNERSHIP publicly (frontend UI already done — see wave 4).
2. E-cat provider catalog via S3; E-codegen pydantic→TS contract generation;
   CI gates (sh -n, compose config, nightly security probes incl. the PG fanout
   test with HARNESS_TEST_PG=1).

## Environment notes

- PG from host: `localhost:5499` (loopback; 5432 is your local Postgres).
- Integration PG test: `HARNESS_TEST_PG=1 HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:5499/harness ../section1/.venv/bin/python -m pytest integration/test_checkpoint1.py`.
- `EventBus.publish` flushes before commit — safe under any expire_on_commit.
- deepseek-harness-sdk pinned 0.1.1rc1 (no 0.2.0 on PyPI). CORS env must be JSON.

## Session log (most recent first)

- 2026-08-22 wave 3: H2r OTP ownership proof + F6r OS confinement, live-verified.
  Tests 415→431 total. GAP doc 1095 lines.
- 2026-08-22 wave 2: H8, M2, G-LIVE(+global-seq trap), engine fallback, health/
  limit quick wins. Tests 392→415 total. GAP doc now 1055 lines.
- 2026-08-22 wave 1: 9 FATAL + HIGH fixes, event-log rehydration, live authz
  proofs. See GAP doc Appendix E.

## Mission 12 — Wave 2 CLOSED (2026-08-23)

All 20 lanes landed and gated. Final suites: S1 262 · S4 274 · S6 64 · S7
tsc-clean + vitest 281/281. Verifier teams VT-1..VT-5 next.

## Mission 12 — Wave 2 VERIFIED (2026-08-24)

All five verifier teams returned: VT-3 ROSTER/RUNS READY, VT-4 REVIEW READY,
VT-5 CONCURRENCY READY (after MINORs), VT-1+VT-2 FIX-LOOPs fixed
(e5dc56a apiKey seam + synthesis kill; 96edf35 JSONB reserve) and
re-verified READY-TO-MERGE. Final suites: S1 265 · S3 86 · S4 274 · S6 69 ·
S7 vitest 281. CI green pending gitleaks rename run 32685579474.

## Mission 13 — Wave 3 LANDED (2026-08-24)

All 10 lanes committed: pool math (6dfa3b2), WAL-G PITR executed end-to-end
(c3aead1), Playwright E2E suite (eae5a41), Makefile/CI convergence
(4cd7074), NUMERIC money + Decimal sweep (da645f4/18918d5), timeutil tz
normalization (e0c61ad group), oracle-404s + OpenAPI typing 8→39 routes,
SKIP LOCKED + zombie reaper (06b8055). Suites: S1 309 · S3 91 · S4 287 ·
S6 76 · S7 tsc-clean + 303 vitest. CI green on pushed stack. W3 verifiers next.

## Mission 13 — Wave 3 CLOSED (2026-08-24)

CI 32694440516 SUCCESS. All five verifier teams READY:
VT-1 DB-INFRA (caught + confirmed fixed: SKIP LOCKED placement before
RETURNING), VT-2 PITR (independent drill re-execution, marker resurrected),
VT-3 QA-INFRA (make/CI parity byte-proof), VT-4 MONEY/TIME (live-PG exact
reserve proof), VT-5 API-HARDENING (oracle byte-compare; resume DTO MINOR
fixed in-lane). Suites: S1 309 · S3 91 · S4 291 · S6 76 · S7 303 vitest.
Operator note: NUMERIC(12,6) caps budgets at $999,999.999999.
Waves 4-5 remain.

## Mission 14 — Wave 4 CLOSED (2026-08-24)

CI 32701744809 SUCCESS. All five verifier teams READY:
VT-1 METERING, VT-2 A11Y/RESPONSIVE, VT-3 INVITES/OPTIMISTIC (live-PG probe
36/36, migration walk through 0021), VT-4 AGGREGATE (11-key DTO parity),
VT-5 DESIGN-SYSTEM+INFRA. Suites: S1 315 · S3 91 · S4 291 · S6 76 · S7
vitest 415/415 + tsc clean. Operator note: NUMERIC(12,6) budget cap.
Wave 5 (roadmap program) remains.

## ALEX-FORCE — Wave 5 CLOSED (2026-08-24)

First mission of the 20-agent ALEX-FORCE: independent close-out verification.
FORCE-COMMITMENT REPORT verdict: PROCEED TO CLOSE. Full matrix re-verified:
S1 400 · S4 312 · S6 76 · S8 77 · S7 vitest 431 + tsc clean; guards green
(routes: 15 modules, isolation: 374 files); live-PG probes passed (FK
deletion story 3/3, ws fingerprint 15/15, KDF/WS suites 26). Ruff clean.
Wave 5 CLOSED. Mission 16 (part4 §W5+ backlog) planning next.

## Mission 16 — Wave 6 LANDED (2026-08-24)

CI 32726153473 SUCCESS. All 5 lanes committed:
X1 runs-index tuning at 100k (0026 + N+1 elimination, batched extremes),
X2 meeting message pagination (keyset cursor, bounded windows),
X3 WS replay cap + truncation markers,
X4 UI systemization completion (all pages on DS primitives),
X5 edge-case corpus wave A (19 seed-ID-traceable regression tests).
Suites: S1 430 · S4 318 · S6 99 · S7 vitest 452/452. ALEX-FORCE
verification sweep next → Wave 6 close.

## Mission 16 — Wave 6 CLOSED (2026-08-24)

ALEX-FORCE FORCE-COMMITMENT verdict: PROCEED TO CLOSE. Full matrix
independently re-verified: S1 430 · S4 318 · S6 99 · S7 vitest 452/452;
guards green (15 routes, 395 files isolation); X1 N+1 spot-check clean
(2 batched GROUP BYs per request, zero correlated probes); X5 seed-ID
traceability confirmed against raw-18; migration chain 0001→0026 unique.
Wave 6 CLOSED.

## Mission 18 — Horizon NOW LANDED & CLOSED (2026-08-25)

10x-plan Horizon NOW executed by ELITE-FORCE dual teams. CI 32843200308
SUCCESS. C1 authz fail-closed (triple fail-open dead, audited denies);
C2 one budget truth (contracts/budget.py resolve_cap canonical, fail-CLOSED
+ degraded flag); C3 cost-gate O(1) (0027 indexes + daily rollup runner);
C4 loadtest harness live (BASELINE sweep carried to M19); C5 invite/ticket
hygiene (email binding, ?token= deprecated, fingerprint enforced, bucket GC);
C6 rollback v0 rehearsed (drift gate refused old image vs 0027 DB).
Suites: S1 496 · S3 91 · S4 369 · S7 vitest 452. Next: BASELINE sweep +
Horizon NEXT per mission17-10x-plan.md.

## Mission 19 — BASELINE + Horizon NEXT phase 1 CLOSED (2026-08-25)

CI 32852228986 SUCCESS. N1 stateless orchestrator (CAS approvals, durable-
first, two-replica exactly-once); N2 slim bus (0029 signals ~70 bytes,
8KB publish cap); N3 partitioned retention (0028 monthly partitions,
outbox+audit prune, migrate walk proven); N4 canned demo (zero-key 8-min
value, recorded-truth replay); N5 Python SDK v0 (91 paths/104 ops generated);
B1 BASELINE published — ceiling ~1.9 journeys/s, wall = worker single-claim
poll cycle (worker.py), $0.000051/run metered. Suites: S1 526 · S4 399 ·
S7 vitest 460. Next: claim batching (first 10x lever) + Horizon NEXT part 2
(C12 observability, C13 docs truth) per mission17-10x-plan.md.


## Mission 20 — Horizon NEXT part 2 + FIRST 10X LEVER CLOSED (2026-08-26)

CI 32922623925 SUCCESS. T1 claim batching: 1.69 -> 37.58 journeys/s at 32 VU
(~22x), zero failures / 4521 journeys — BASELINE wall torn down; throughput
ceiling now ~3.2M runs/day theoretical (executor + provider latency become
the real limits). T2 observability minimum: request-id middleware on all 9
services, anonymized user hashing, Prometheus scrape + 5 promtool-validated
alerts. T3 resource limits 22/22 services, log rotation, /ready != /health.
T4 extensions.md (3 real seams) + glossary completion. T5 TS SDK v0 — both
SDKs from one generator pipeline. Suites: S1 574 · S4 414 · S7 vitest 460.
Remaining per 10x plan: LATER horizon (context pillar/codebase indexing,
commercial skeleton) + M23 bets.


## Mission 21 — CONTEXT PILLAR CLOSED (2026-08-26)

CI 32932601871 SUCCESS. X1 indexing core (pgvector, pluggable embeddings,
incremental re-index); X2 @refs resolution (hybrid ranking, token-budgeted
bundles); X4 retrieval-augmented planner (path:line citations in steps);
X3 UI surface (@autocomplete, chunk preview panel, searchable tree).
0030 pgvector-gated — honest degrade without extension. Suites: S1 574 ·
S4 452 · S7 vitest 500 + tsc clean. Exit criterion met: plans demonstrably
cite indexed chunks. Next: Mission 22 commercial skeleton per 10x plan.


### Mission 21 formal close (2026-08-26): CI 32948436346 SUCCESS on
b83dbfd — replay-cap flake eliminated (30/30 stability sweep). Context
pillar + all Horizon NEXT work verified at 100% green.

# ════════════════════════════════════════════════════════════════
# PROGRAM COMPLETE — v1.0.0-candidate tagged 2026-08-26
# ════════════════════════════════════════════════════════════════

Missions 11–23 CLOSED. CI SUCCESS on all finale commits (32966578998,
32966793465). Tag: v1.0.0-candidate @ c5393f7.

Final suites: S1 628 · S2 25 · S3 91 · S4 465 · S5 154 · S6 99 · S8 77 ·
S10 65 · S11 50 · mcp 17 · S7 vitest 500 · tsc strict clean.

Delivered vs the 10x plan:
- Scale: claim batching 22x (1.69→37.58 journeys/s), stateless orchestrator
  (CAS approvals, exactly-once), slim bus, partitioned retention, bounded
  containers, readiness probes
- Throughput: measured BASELINE published; $0.000051/run metered honestly
- Time-to-value: zero-key canned demo (recorded-truth replay)
- Ecosystem: Python+TS SDKs from one generator, MCP tool server, extensions doc
- Commercial: LICENSE/TERMS/PRIVACY skeletons, tiered caps w/ upgrade CTAs,
  Stripe adapter + signed idempotent webhooks, OIDC SSO
- Context pillar: pgvector indexing, AST-aware chunking (bet executed),
  @refs resolution, RAG planner with path:line citations
- Ops: PITR rehearsed, rollback runbook + drift gate, observability +
  promtool-validated alerts

Honest residuals (see m23-aar.md + RELEASE_NOTES_v1.md): M22 landed-unverified
by dedicated verifier pass; live multi-replica proof = next-owner brief;
editor surface + MCP-client direction never shipped; counsel review required
on TERMS/PRIVACY placeholders.

Campaign: 13 missions, ~90 agent lanes, ELITE-FORCE standing team.

W28 S1b-2 (2026-11-05): `GET /api/v1/orchestrator/runs/{runId}/synthesis` gives a human a read-only review of a run's delegated children: owner, scope, state, spend, run status, final response, and `conflicts` where live sibling path scopes overlap (`reviewRequired` true, `autoMerge` false, nothing merged). Five new tests; section 4 unit suite 1889 passed / 41 skipped. Verified on SQLite only: not deployed to the live stack, so the endpoint has not been exercised on real children, and the conflict check is on declared scope, not files actually written. Stage 6 stays partial.

W28 S1b-2 deployed and drilled (2026-11-05, `harness-backend:vw28s4`, only the 11 backend-image services recreated, no migration, worker-daemon and engine-worker stay on `vw28s3`): the live two-agent drill now has a twelfth step and passes 12 of 12 (`docs/briefs/evidence/w28/two-agent-drill.json`). After the root was cancelled mid-run, `/synthesis` listed all three delegated runs (root, A, B) with state `cancelled`, `reviewRequired` true, `autoMerge` false, no conflicts (B was cancelled and the root is a different parent). Limit: the cancelled runs have no result (`runStatus` `not_started`, no final response), so this shows the endpoint and its fields on real children but not a synthesis of finished work; that needs a child that completes, which costs model spend. The live flag is off again (inert).

W28 S1b-2 completing-child drill (2026-11-05, `scripts/e2e/w28_synthesis_drill.py`, evidence `docs/briefs/evidence/w28/synthesis-drill.json`, `vw28s4`): PASS, 8 of 8 steps. Child A was approved by a second admin and ran to completion on deepseek-v4-flash; `/synthesis` returned its real result (`runStatus` completed, final response "done") and flagged A and sibling B (live, unapproved, same `src` scope) as overlapping; `reviewRequired` true, `autoMerge` false; cancelling the root left nothing live. Model spend $0.001062 from `cost_ledger`, so the session total is still about $0.05 of $0.50. **Defect found, not fixed:** the synthesis shows `spentUsd` "0" for A because it reads the assignment's own counter, which nothing feeds from the run's cost; the real figure is in `cost_ledger`. A reviewer reading synthesis would see a wrong spend. Other limits: one trial, one small task; conflicts judge declared scope, not files written; the cancelled root/B have no results. The live flag is off again.

W28 S1b-2 spend fix (2026-11-05, `harness-backend:vw28s5`, same 11 backend services recreated, no migration): `/synthesis` now reports each child's spend by summing its run's `cost_ledger` rows, so the "0" defect above is closed. New unit test (SQLite fixture creates a minimal `cost_ledger`); section 4 delegation, agent_tree and docs tests pass (85) and ruff is clean on `delegation.py`. The completing-child drill was re-run on the live stack and passes 9 of 9, including a new step that checks the synthesis figure equals the ledger total (0.002512 both). That run cost $0.002512, so the session total is still about $0.05 of $0.50. The live flag is off again. Limits unchanged: one small trial, conflicts judge declared scope not files written, cancelled runs show no result.

W13 drills re-run (2026-11-05) after the redeploy: `scripts/e2e/w13_runner_drill.py` against the live stack (backend `vw28s5`) passes **43/43**, closing the earlier 41/43 with two red marker-dependent checks (`toolEventsAccepted`, `finishCompletesTheJob`), which the claim-marker fix now satisfies on the deployed identity service (evidence `docs/briefs/evidence/eval/w13-runner-1793885527.json`). The isolated execution-leg drill `scripts/e2e/w13_exec_drill.py --tree . --label after` passes 16/16 on the working tree with its own throwaway database (evidence `docs/briefs/evidence/w13/after.*`, overwritten by this run). $0 model spend. Stage 6 stays partial: the runner is on the same host and network, not a real remote machine, and transport hardening is not covered.

W28 follow-ups (2026-11-05, `harness-backend:vw28s6`, same 11 backend services, no migration; commit `0c08b2f` plus the drill changes after it):

* **Duplicate stop event: fixed, and it was a bigger bug.** Replay sealed a stop only when the `paused` reason was exactly `user_requested_stop`. Delegation stops use `assignment_cancelled`, so after a replay (restart, eviction) a cancelled child run read as a resumable PAUSED run, and a repeat stop emitted a second `paused`. The stop envelope now carries `stopped: true` and replay seals on it for any reason. Live: the two-agent drill now passes **17/17**; every stopped run reports `done` (was `paused`) and has exactly one stop event. New unit tests: payload carries the marker, replay of a custom-reason stop is DONE, and a plain pause with no marker stays resumable.
* **Scope binding: written and refused-when-empty, enforcement only partly shown.** Delegation now writes the assignment's paths as the child run's intent v1 (the engine's shell gate and container mounts enforce the intent's `scopePaths`, and a run with none is unconfined), and refuses a scope with no paths (`SCOPE_UNBINDABLE`, 422). Live: all three child runs hold `["src"]`, the empty-paths delegation is refused. **Not shown:** an in-scope write succeeding while an out-of-scope write is refused. The live attempt (`docs/briefs/evidence/w28/scope-enforcement-drill-empty-workspace-FAIL.json`, kept as a failed attempt, $0.004) found that in a project with an empty workspace the child could write nothing at all, not even `src` or a file in the workspace root (`Read-only file system`); my inference is that the container binds only scope paths that already exist, so with no `src/` everything stays read-only. That is fail-closed, but it means a delegated child needs a workspace that already contains its scope paths. The first version of that drill reported PASS on loose word matching and was wrong (the refusals were an unrelated command-policy denial and a missing directory); I tightened it to match each command's own result and it now fails honestly. The scope's `tools`, `permissions` and `network` are still not bound to the engine, only `paths`.
* **Rollback below `vw28s3`: not done, by design; the restore path is verified.** The gate refuses because migrations are forward-only (`docs/runbooks/release-compatibility.md` section 7). The pre-migration dump restored cleanly on the scratch Postgres (71 migrations, newest 0072, 18 `runner_jobs` rows) and the `vw25b1` image's drift gate passed against it. Not verified: that old code runs correctly on it, and the dump sits in a session scratchpad under `/private/tmp`, so the owner should move it somewhere durable. Nothing on the live database was touched.
* **Pre-existing flake found:** `tests/unit/test_lifecycle_recovery.py::test_reclaimed_run_fences_the_original_worker` fails about 15% of runs with or without these changes (3/20 baseline, 4/20 with). Not fixed. Section 4 unit suite otherwise 1894 passed / 41 skipped.

Model spend this step: about $0.01 (two scope-drill attempts); session total about $0.07 of $0.50. The live flag is off.

**W25/W26 benchmark run (2026-11-05, 14:00 to 14:45 UTC, evidence `docs/briefs/evidence/eval/w25-metrics-*.json`, stack `harness-backend:vw28s6`, collector `scripts/eval/w25_metrics.py`).** Each cell is separate runner invocations (the runner's `--repeats` stops at the first pass, so it cannot give a rate). Timings come from `agent_events` and `cost_ledger`, not from the agent. n is 5 per cell, so "p95" below is just the slowest run.

| task | model | pass | wall p50 (min-max) | first tool call p50 | tool calls p50 | cost per run |
|---|---|---|---|---|---|---|
| multi-file-refactor | deepseek-v4-flash | 5/5 | 57.2 s (53.0-61.2) | 8.8 s | 26 | $0.0078 |
| multi-file-refactor | claude-haiku-5.5 | 5/5 | 29.0 s (25.0-37.0) | 7.1 s | 17 | $0.0080 |
| tests-written | deepseek-v4-flash | 5/5 | 45.2 s (41.4-49.2) | 8.4 s | 11 | $0.0064 |
| tests-written | claude-haiku-5.5 | 5/5 | 25.1 s (21.1-25.1) | 6.6 s | 9 | $0.0056 |
| w11-grounding | claude-haiku-5.5 | 5/5 drill checks, model accuracy 2/5 in every run | 31.7 s (31.3-39.8) | n/a | n/a | $0.0007 (6 calls, from the drill's own evidence) |
| multi-file-refactor, 3 at once | deepseek-v4-flash | **1/3** | 49.1 s (41.3-61.3) | 8.2 s | 25 | $0.0074 |

* **Model A/B (text tasks).** Same pass rate, Haiku about twice as fast end to end, and cost per run is equal within noise (Haiku is cheaper on tests-written, equal on the refactor, because it uses fewer tool calls). This reverses my earlier single-trial note that DeepSeek was about 15% cheaper; at n=5 that does not hold. DeepSeek has no image input, so vision tasks need Haiku regardless.
* **Start and cold start.** Start latency (plan approved to execution started) is 0.01-0.07 s everywhere, including with 3 runs at once, so no queueing shows at this load. Time to first tool call (7-9 s) is the model's first response. The first run of each block was about 4 s slower than the later p50 in three of four cells (Haiku refactor 33.2 vs 29.0 s, Haiku tests 25.1 vs 21.3 s, DeepSeek tests 49.2 vs 45.1 s) and 4 s faster in the fourth (DeepSeek refactor 53.2 vs 57.2 s); one run per cell, so a hint of a warm-up cost, not a cold-start measurement.
* **Concurrency.** Three simultaneous runs: 1 passed, 2 failed after 41 s and 49 s, both with the same miss (`README.md` still mentioned the old name, 4 files changed instead of 5). Sequentially the same task and model passed 5/5. With 3 against 5 runs this is not significant (a permutation test over the 8 runs gives about 11% for 1 or fewer passes by chance), and the failure is a task-quality miss, not a system fault (no orphan, no queue delay). It needs more batches before anyone says concurrency hurts quality.
* **Failed attempts kept.** (1) First concurrency batch: three processes started in the same second shared a run id and a baseline file (`code_change_task.py` ids runs by the second); two failed in under 0.4 s and their baselines were overwritten, only stdout survives (`w25-concurrency-attempt1-same-second-collision.json`, diagnosis inferred, the error text was not kept). (2) The two concurrent refactor failures above (`baseline-conc2-1.json`, `baseline-conc2-3.json`). (3) An infrastructure incident, below. Protocol note added.
* **Gaps, stated.** `w11-grounding`'s Haiku spend is not in `cost_ledger` (the ledger holds only the reviewer's `gpt-4o-mini` rows); the drill records its own usage in `docs/briefs/evidence/pilot-prep/w11-grounding-1793888793.json` and four siblings: 6 calls and $0.00071-0.00076 per run, $0.0037 for the five. **Its 5/5 is the drill's checks, not the model's grounding quality:** in all five runs the model hit 2 of 5 click targets (accuracy 0.4, the same `easy-target` miss about 115 px off each time), and the drill passes because the harness measured, judged and recorded that correctly. Do not read it as the model being good at grounding. Ledger rows are one per run, so per-model-call latency is not available. No comparison with another harness was done (needs a second harness under the same task, model, budget and permissions). Two text tasks plus one vision task are a narrow set. Single stack, single machine, same-host workers.
* **Recorded spend:** `cost_ledger` shows $0.1684 for 14:00 to 14:45 (DeepSeek $0.1001, Haiku $0.0681, reviewer $0.0002), plus $0.0037 of grounding calls from the drill's own evidence, about $0.172 in all. Session total is about $0.24 of $0.50.

**Model decision (yours to approve; I have not changed the stack default).** On this evidence Haiku is the better default for latency at equal cost and equal pass rate, and it is required for vision; DeepSeek costs the same here and is half as fast. The corpus still pins `qwen/qwen3.7-flash` on the vision tasks (`m07-vision-api`, `w10-browser-repro`, `w11-vision`, `w11-grounding`, `w14-design-heldout`); qwen failed the W20 vision gate earlier and all five pass on Haiku, so repinning them to Haiku looks right. The retirement of the "six locally pinned tasks" is not decided here: I could not match that list to the corpus and need you to name it.

**Incident during this run (my doing, fixed).** `scripts/eval/with_model.sh` used the orchestrator's image tag for both services; the worker-daemon runs a different tag (`vw28s3`), so the switch aborted half-applied and left the **orchestrator stopped from about 14:14 to 14:23 UTC** (no runs were active, no data lost, no spend). I restored it by hand, changed the script to read each service's own tag, and tested the switch and restore. The Haiku block was then run in full.

**Stack default changed to Haiku (2026-11-05, owner decision after the W25/W26 benchmark).** `DSH_MODEL=anthropic/claude-haiku-5.5` in the root `.env` (line 286; the file is gitignored, so this is not in git; the previous value was `deepseek/deepseek-v4-flash`). Recreated `identity`, `models` and `orchestrator` on `harness-backend:vw28s6` and `worker-daemon` on `harness-worker-daemon:vw28s3`; all four report Haiku, nothing was running, the delegate flag is off. Verified with a real run on the default (no wrapper): `tests-written` PASS in 25.2 s, `cost_ledger` shows `anthropic/claude-haiku-5.5`, $0.0056. To revert, set `.env` line 286 back to `deepseek/deepseek-v4-flash` and recreate those four services with their own image tags (not with one shared tag). The rate card already carries both models. The corpus still pins `qwen/qwen3.7-flash` on the vision tasks; that pin is stale and was not edited.

**W25/W26 open items worked (2026-11-05).**

* **The six "locally pinned" tasks are named:** the corpus pins `qwen-local:latest` on `m03-scope`, `m04-streaming`, `m07-api-run`, `m07-daemon-topology`, `w3-rewind` and `w3-partial-write`, all `blocked` since 2026-10-28 because the local Ollama holds only `nomic-embed-text` (no chat model). **Three are now repinned to the stack model and pass:** `m07-api-run`, `w3-rewind`, `w3-partial-write` drive the deployed stack over HTTP, so they never needed a local model. Run directly on Haiku they cost $0.0095 together; then through the runner `m07-api-run` PASS (14.7 s) and `w3-rewind` PASS (17.3 s), and `w3-partial-write` FAILED (47.1 s, kept: `baseline-20261105T150411Z.json`) although the driver's own result was `ok: true`. Cause, found and fixed: the runner calls a run PASS only when the driver's `verdict` is empty or `PASS`, and `w3_partial_write_drill.py` wrote a descriptive sentence there, so it could never have passed through the runner (the `blocked` status had hidden that). The drill now writes `verdict: PASS/FAIL` and the sentence in `summary`; rerun through the runner: PASS (47.6 s). Corpus now 36 implemented (was 33), edited in place for those three entries only. Evidence files `pilot-prep/m07-api-run.json`, `w3-rewind.json`, `w3-partial-write.json` were overwritten by the new runs (the earlier versions are in git history). **Not changed:** `m03-scope`, `m04-streaming`, `m07-daemon-topology` still block. Their drills run a host-side engine and `m03_scope_drill.py` refuses any non-local endpoint on purpose ("no spend"). Options for the owner: pull a small local chat model into Ollama (a download I have not made without asking), let those three drills target a hosted model (a code change that drops the no-spend guard; about a cent per run), or retire them. `m03-scope` covers scope mounts and `m07-daemon-topology` the daemon topology, which are the weakest to lose.
* **Concurrency, more batches** (3 runs started 2 s apart, separate output files; `w25-metrics-concurrency-*.json`): **Haiku 6/6** (wall p50 33.1 s against 29.0 s alone, about 14% slower; max start latency 0.04 s). **DeepSeek 4/6**, so 5/9 over all three concurrent batches, against 5/5 sequential (a one-sided Fisher test gives about 0.13, not significant). All four DeepSeek failures are the same miss: four files changed, `README.md` still names the old function (`oldNameGoneOutsideVendor`). No queueing, orphan or start delay was seen on either model. Read: DeepSeek may degrade under load or may simply miss that mention about 40% of the time and got lucky sequentially (six sequential passes would then have about a 5% chance); this data cannot tell which. Haiku shows no concurrency effect at n=6. Another reason, beyond cost, not to go back to DeepSeek as the default.
* **Other-harness comparison: not done, deliberately.** Claude Code, Codex, opencode, Gemini and Cursor CLIs are installed, but the roadmap allows a comparison only under the same task, model, budget and permissions. Any of them would run the model through its own account and provider route (not this stack's OpenRouter key), with its own permission model and cost accounting, so parity would be claimed, not established. It needs the owner to choose the harness and accept what "same model" means.
* **Spend, corrected.** `cost_ledger` shows $0.1307 since 14:55 UTC (Haiku $0.0757, DeepSeek $0.0550; this covers the default-change check, the pinned drills and the concurrency batches), on top of about $0.172 for the benchmark and about $0.07 before it. **Session total is about $0.37 of the $0.50**, not the $0.25 I reported after the default change. The stack was returned to Haiku.

* **The last three pinned tasks now run on Haiku ("Run on Haiku").** `m03-scope`, `m04-streaming` and `m07-daemon-topology` are repinned to `anthropic/claude-haiku-5.5` (cost live, `maxCostUsd` 0.05, status implemented; corpus check: 39 implemented). Code changes: `m03_scope_drill.py` no longer refuses hosted endpoints, it now needs `HARNESS_ALLOW_PAID_SMOKE=1` plus `OPENROUTER_API_KEY` in the caller's env; `m04_streaming_smoke.py` copies `--key-env` into the name the engine reads (it was only set for local runs); `m07_daemon_drill.py` accepts `--base-url stack` and uses the orchestrator container's own endpoint and key. Results, each run directly then through the runner: **m03 PASS** (in-scope `src/m03.txt` written, `docs/m03.txt` refused with EROFS, nothing outside `src/` changed; runner 10.2 s), **m04 PASS** (one tool_call/tool_result pair with a stable callId, first arrival 5.9 s; runner 11.4 s), **m07-daemon PASS** (worker spawned by the daemon, `calc.py` fixed, README untouched and read-only; runner 13.1 s). Evidence: `baseline-haiku-pinned-3.json` and the three `pilot-prep/` JSONs (overwritten). The m03 run also closes the earlier open item: an in-scope write succeeding while an out-of-scope write is refused. Cost was not read back from `cost_ledger` (host-run drills; m04 reported 3,673 in / 164 out tokens, about half a cent), so the figure is an estimate of roughly $0.01 to $0.02 for all six runs.
* **Open items closed out (2026-11-05).** (1) **Dump moved:** `live-before-0073-0075.dump` is now at `~/.harness-backups/` (directory 0700, file 0600); SHA-256 `f6fb936f…a97b` matches the scratch original. It is outside the repo on purpose (it holds live data). It is still on this one machine, so copying it off-machine remains the owner's call. (2) **Flaky `test_reclaimed_run_fences_the_original_worker`:** I could not reproduce it in isolation (12/12 alone, 10/10 with its whole file), so it only fails under full-suite load. The most likely cause is the test's fixed 5 s wait for the original worker's heartbeat to notice the lost claim; I have **not** changed it, because a timeout bump without a reproduction would be a guess. Left recorded, not fixed. (3) **"Only `paths` is bound" is not a gap I can close:** the child intent also has a `permissions` column, but nothing in the engine or the shell gate reads it (only `scope_paths` is consumed, `collab.py` scope lookup). Copying tools/network onto the child would record a restriction that nothing enforces, so I did not. Enforcement of `permissions` is a design task of its own (what it means, where the gate reads it), not a delegation fix. (4) **Other-harness comparison:** unchanged, still not done; it needs you to name the harness and accept a model that is not Haiku-via-OpenRouter for the other side, since the roadmap only allows a same-model comparison.
