# Platform and testing master prompt (2026-11-21)

Paste everything below the line into a coding agent whose working directory is the `harness/` repo root.
Run **one phase per session**: after the prompt, say "do phase Q0" (then Q1 … Q10). Q0 must come first.

**Why this exists.** On 2026-11-21 two areas of Harness were scored:

- **Platform and backend: about 88%.** The 61-part capability matrix stands at 50 verified, 1 implemented, 8 partial and
  2 blocked, but those counts date from 2026-10-21. Several limits and backend gaps are still open.
- **Testing, eval and benchmark: about 70%.** The suites are large and mostly green (section4 2398, section1 1269,
  section7 1201, section5 542). But the preregistered live benchmark has not run, the eval corpus has no task with a
  test step, flaky tests are recorded but not ledgered, and **GitHub Actions CI has not run since 2026-11-21 15:16Z**.

This prompt takes both areas to 100%, with a scorecard per area (§3). It sits beside two others; do not do their work here:

- `docs/briefs/ui-ux-enhancement-master-prompt-2026-11-21.md` owns UI work. This prompt builds the **backend** pieces the
  UI is waiting for.
- `docs/briefs/security-prod-readiness-master-prompt-2026-11-21.md` owns sandbox gaps G1–G14, authz, supply chain,
  PITR, alerting, SSO and the pen test. Where a platform item overlaps, link to its finding id; do not duplicate the fix.

**Honest ceiling.** An agent can take platform to about 95% and testing to about 90% alone. The rest needs the owner:
model spend for the live benchmark (preregistered cap $11), GitHub billing for CI, two open product rulings, real pilot
data for row 03, and a cloud bucket for row 53. For each one, the prompt has the agent prepare everything up to the
handoff.

---

You are the platform and quality engineer for the AI Engineering Harness. Your job is to close every open platform
limit that engineering can close, give the product a test and evaluation system whose numbers can be trusted, and
publish a benchmark that would survive a skeptical reader. A capability counts only when a test, drill or measurement
shows it working at a recorded revision (and on the deployed tag where the claim is about the running system), with the
evidence in the repo.

## 1. Read first (in this order, before any change)

1. `docs/briefs/PROJECT-STATE.md` (§2 where the work is, §4 open work, §5 facts that bite), the top of `STATUS.md` and
   its newest entries, and `docs/roadmap-3.0.md` (stage table, owner gates, immediate next actions).
2. `docs/briefs/capability-matrix.md`, especially the non-verified rows: **03** (blocked: no pilot data),
   **26** (implemented: `run_smoke.py --real` never ran), **31** (data and privacy), **51** (residency is deployment-wide),
   **52/53/58** (external caps), **55** (Grafana and paging), **56** (accessibility: the UI prompt owns it),
   **57** (in-flight engine spend is an estimate), **61** (blocked: full acceptance).
3. `docs/master-prompts/harness-multiplayer-master-build-prompt.md`: the 61-part source of the matrix.
4. Testing and eval: `docs/eval/PROTOCOL.md`, `docs/eval/heldout-frozen.json`,
   `docs/briefs/evidence/90day/benchmark-methodology-2026-11-21.md`, `p13-spend-estimate-2026-11-21.md`, `scripts/eval/`
   (`run_eval.py`, `bench_trials.py`, `bench_stats.py`, `bench_leaderboard.py`, `compare_harness.py`, `freeze_heldout.py`,
   `w25_metrics.py`, `tasks/`), and `docs/briefs/evidence/w7-bench/`.
5. `docs/briefs/perf-audit-2026-11-21.md` §4–§6: the denial-turn A/B result (span −34%), the two changes to upstream
   (bash DESCRIPTION line and `compileall` in `section4/worker.Dockerfile`), `pytest` missing from the worker image, the
   unused first-class `grep` tool, and the `_with_repo_context` observation (`engine.py:1178` builds context from
   `self.workspace`, not the run workspace).
6. `docs/briefs/evidence/90day/week-13.md` (suite health, the section11 race fix), `checkpoint1-redesign-2026-11-21.md`
   (the skipped `integration/test_checkpoint1.py` and its faithful redesign), `a01-xfail-disposition-2026-11-21.md`
   (the 10 macOS read-scope xfails).
7. Backend gaps the UI reported (UI reports in `docs/briefs/evidence/ui/r3-report-2026-11-15.md` … `u-open-items-report-2026-11-15.md`).
8. `.github/workflows/ci.yml` (jobs: `changes`, `static-gates`, `python-tests` matrix, `mcp`, `language`, `frontend`,
   `e2e-compose`, `nightly-pg-probe`, `migrations-pg`, `security-scan`, `native-linux-container`) and the `Makefile`
   targets (`make gate`, `guard-isolation`, `guard-routes`, `guard-route-law`, `check-ts-dist`, `pg-probe-*`).
9. `AGENTS.md`: the invariants (no auto-merge, no forced model downgrade, plan approval required, honesty labels,
   never-resurrect, section isolation through `contracts/` only).

## 2. Hard rules

**Live system**
- Two stacks run on this Mac. `harness-rc` (loopback 18001–18008, frontend :3011, tag `vv90r6`) is the pre-production
  target. `harness-demo` (8001/8004/3010) is the owner's demo. **Never touch the demo stack.** Never `docker compose down`,
  never `down -v`. Never rebuild, redeploy or migrate a stack without the owner's "yes" **in this session**.
- Eval and e2e drivers default to the **demo** ports. Always set `E2E_COMPOSE_PROJECT=harness-rc` plus
  `E2E_IDENTITY_URL` / `E2E_ORCHESTRATOR_URL` (the bench runner refuses otherwise). Back-to-back logins trip the identity
  rate limit (429): space them out.
- Never use `make pg-up` for probes. Use your own throwaway `postgres:18` with `HARNESS_TEST_PG_EXTERNAL=1`, migrated
  with `deploy/runners/migrate.py`.
- Another session may be using the live stack or the checkout. Check `docker ps` and `git status` before you start.

**Money and outside parties**
- No paid model call without an owner-approved budget **in this session**. The benchmark has a preregistered cap
  (Haiku $5 + DeepSeek $3 + comparator $3 = $11), enforced by `--max-total-usd` and checked against `cost_ledger`
  actuals. Cheap models first. Every paid invocation is announced with its cap before it runs.
- Keys live in the gitignored root `.env` and `harness/.env`. Never print, commit or log a key; evidence is grep-checked
  for `sk-|whsec_|ghp_|AKIA`.
- GitHub Actions is **blocked by account billing** (jobs not started: "recent account payments have failed or your spending
  limit needs to be increased"). Only the owner can fix that. Until then, `make gate` plus the per-section suites on this
  machine are the CI of record, and every report says so.

**Integrity of results (the benchmark law)**
- Preregister before you measure. Thresholds, the task split, trial counts, models and the comparator are written and
  committed **before** any run, and they may not be loosened after results are seen (`docs/eval/PROTOCOL.md`).
- Every attempt is retained: failures, timeouts, skips, refused setups. Summaries are rebuilt from raw files by
  `bench_stats.py`; a summary that cannot be rebuilt does not count.
- Held-out results are the only ones that may be called generalisation. A task that was used to tune anything becomes a
  seen task and is reported as such.
- Never compare runs from different revisions, models, budgets or task versions as if they were the same condition.
- Cost comes from `cost_ledger` actuals, never the rate card alone. Where actuals are missing (the openai-completions
  transport reports `usage: null`), say "not metered", never $0.

**Tests**
- Never skip, xfail, retry or loosen a test to make a suite green. A flaky test gets a root-cause fix (like `2ddf03b`,
  which released the lease before the terminal status became visible) or a ledger entry with its reproduction rate and
  an owner. A skip needs a written reason classified as environmental or defect.
- Never weaken a product law to pass a test (staged-approval completion gate, I-09 verification-first, reviewer
  integrity, plan approval, no auto-merge). `checkpoint1-redesign` exists precisely because patching that test green
  would have meant faking one of those laws.

**Repo hygiene**
- Run each section's tests from its own directory with its own venv (`cd section1 && .venv/bin/python -m pytest tests`).
  From the repo root the root `.env` leaks in and `test_f1_envelope_idempotency` fails.
- Run the live-PG modules after touching raw SQL (SQLite hides PostgreSQL typing errors).
- A new HTTP route: `section4/.venv/bin/python scripts/spec_drift.py --write`, restore `section5/openapi.json` with
  `git checkout`, then `python3 scripts/sdk-gen/generate.py` and `--target ts`, then `make check-ts-dist`.
- A new migration takes the next free number after the current head (0076 at `vv90r6`) and passes
  `deploy/runners/migrate_selftest.py` and the upgrade/rollback rehearsal tests.
- Never `ruff check --fix` broadly (it deletes imported pytest fixtures).
- SQLite tests that spawn engine tasks must await them before `engine.dispose()`.
- Commit per slice with `git commit --only <paths>`; never `git add -A`. Do not commit `.openclaw/`, `FUTURE PLAN 2.0.md`,
  `docs/briefs/evidence/eval/*.json` you did not create for this work, or an `AGENTS.md` carrying `<claude-mem-context>`.
  **Do not push** unless the owner says so in this session.

**Working style (owner's CLAUDE.md, hard rules)**
- Before code, write one line starting `Assumptions:`, then a numbered plan where each step names its check
  (`1. step → verify: check`). If the phase has two valid readings, list them and stop. If anything is unclear, ask.
- No abstraction, config option or helper the phase does not need. Surgical diffs. Name unrelated bugs in one line; do
  not fix them in the same diff.
- Never declare done without naming the exact command you ran and its counts.

## 3. What 100% means: two scorecards

Each area is scored out of 100. A gate scores only with evidence at a recorded revision. Q0 writes the honest starting
score, and every later phase updates `docs/briefs/evidence/quality/scorecard.md`.

### 3a. Platform and backend (100 points)

| # | Gate | Pts | Full marks when |
|---|---|---|---|
| PL-1 | **Matrix is current** | 8 | Every one of the 61 rows re-verified against today's code and evidence; counts recomputed; stale claims corrected; `test_program_docs.py` green |
| PL-2 | **Backend gaps the UI needs** | 14 | Shipped with tests, spec and SDK updates: (a) a per-plan-step progress event the engine emits as steps complete; (b) a "key configured" flag on the model catalog per team; (c) a "since last iteration" snapshot diff (changes since a reviewer's last decision); (d) server-side runs search by goal with cursor paging; (e) collab history exposes `guidance.delivered` (verify, it may already ship) |
| PL-3 | **Engine correctness limits** | 14 | Each closed or explicitly accepted: tool calls of a timed-out engine call are recorded (row 44); a session state change reaches running runs by push instead of the 30 s sweep, or the bound is justified (row 40); `_with_repo_context` uses the run workspace (or is shown to be intended); a repeated stop of a stopped run does not re-emit `paused`; the checkpoint-1 faithful flow is built and `integration/test_checkpoint1.py` is un-skipped |
| PL-4 | **Metering and quotas** | 10 | Actual spend recorded on every supported transport (fix `usage: null` on the openai-completions path, or record provider-reported usage another way); in-flight engine spend uses metered actuals where they exist and labels estimates where not (row 57); `run_smoke.py --real` passes on the RC with an approved budget (row 26) |
| PL-5 | **Delegation and remote execution** | 12 | The assignment scope is **bound** to the child's engine, not just recorded; the two open owner rulings answered and implemented (delegating to paths that do not exist yet; what `0` means for the denial knobs); a criteria doc for turning `HARNESS_SUBAGENTS` on, with the drill evidence it needs; the W13 BYO exec leg re-drilled on the current tag |
| PL-6 | **Agent efficiency follow-ups** | 8 | The perf A/B changes upstreamed through a normal PR (DESCRIPTION line, `compileall`); `pytest` (and the test runners the corpus needs) in the worker image; a finding on why the first-class `grep` tool is never chosen, with a fix measured in replay; the Landlock probe cached only if it stays fail-closed (coordinate with security finding G-B) |
| PL-7 | **Data lifecycle** | 10 | Holds cover every expiry path including bucket lifecycle rules, or a guard refuses a lifecycle config that would bypass a hold (rows 31/58); a per-team residency design (routing, storage and provider region per team) written, with the smallest implementable slice built behind a flag or a written owner decision to keep residency deployment-wide (row 51) |
| PL-8 | **Multi-host readiness** | 10 | The artifact, export and diff store moved behind an interface with an object-store backend (S3 API, tested against SeaweedFS) and the local volume kept as the default; the store included in backup and restore scripts (coordinate with security G-H); a two-host or two-replica drill with the object-store backend |
| PL-9 | **Full acceptance (row 61)** | 14 | A cross-domain acceptance run at one revision: every verified row's invariant tests green, the compose journey green, `run_smoke.py --otp` and `--real` green, the upgrade and rollback rehearsals green, the pilot smoke and `multiplayer.e2e.ts` green, and a written NO-GO/GO for the platform scope with every remaining external cap named |

### 3b. Testing, eval and benchmark (100 points)

| # | Gate | Pts | Full marks when |
|---|---|---|---|
| TE-1 | **CI is real again** | 10 | GitHub Actions runs every job green at HEAD (owner fixes billing; you fix anything red after that). Until then, partial 5/10: a `make ci-local` target that runs the same jobs as `ci.yml` on this machine, in the same order, with the same skips, and writes a dated junit summary |
| TE-2 | **Test and skip ledger** | 8 | `docs/briefs/evidence/quality/test-ledger.md`: every skip, xfail and conditional test across all sections, packages and the frontend, each with its reason, its class (environmental / defect / by-design) and a next action. The 10 macOS read-scope xfails and the checkpoint-1 skip resolved or justified |
| TE-3 | **Flake-free** | 10 | Each suite run 10 times in random order (`pytest -p random_order` or `pytest-randomly`; ask before adding the plugin) with zero failures, or each flaky test root-caused and fixed. Known suspects: the section4 random-order failure and `section1 test_ratelimit_expensive_ops.py::test_model_call_burst_over_limit_429` |
| TE-4 | **Depth on critical modules** | 12 | Line and branch coverage measured per section and reported (no target gaming: report the number, then raise it on the critical modules). Mutation testing (mutmut or cosmic-ray; ask first) on the safety core: `section5 shell_gate.py` and `policy.py`, section4 `collab.py`, `authz.py`, `staging.py`, budget and denial-breaker code, section1 auth and audit-chain code, with a surviving-mutant score per module and every surviving mutant either killed by a new test or justified. Property tests (Hypothesis) on the gate, the lifecycle table and money formatting |
| TE-5 | **Contract and integration** | 8 | `spec_drift.py` clean for every service; TS dist current; consumer tests that call each service through its published SDK; the upgrade rehearsal from the oldest supported schema to head; live-PG suites green on PostgreSQL 18 |
| TE-6 | **Eval corpus that measures real work** | 14 | New tasks added and **frozen before any measurement**: at least 4 tasks with a real test step (fix a failing test, add a test that must fail first and then pass, refactor under tests, upgrade a dependency); at least 2 security tasks (prompt-injection bait in repo content, a tempting forbidden-path edit); at least 2 tasks on a real open-source repository snapshot pinned by commit (licence checked); a multi-file change; a recovery task (resume after a killed worker). Each task has a deterministic grader and an anti-gaming check (the grader cannot pass on a no-op or a deleted test). Held-out split re-frozen with `freeze_heldout.py` |
| TE-7 | **Live benchmark** | 16 | The preregistered run executed under the approved cap: ≥ 5 independent trials per task per model, seen and held-out reported separately, p50/p95 latency, cost from `cost_ledger`, every failure retained, raw files committed, summary rebuilt by `bench_stats.py`, the opencode comparator on its supported tasks under the same model class and budget. Partial 6/16: everything dry-verified and ready to run in one command per model |
| TE-8 | **Published and reproducible** | 8 | A one-page report plus the leaderboard (`bench_leaderboard.py`, I-04 public page) naming the harness SHA, model ids, opencode version, date, budget and every known limitation; a reproduction README that a stranger can follow; the report says what the result does **not** claim |
| TE-9 | **Performance and load regression** | 8 | A zero-spend replay benchmark (the stub-provider replay from the perf audit) in `scripts/eval/` with a stored baseline and a threshold that fails on regression; the load probe (`scripts/loadtest/run_probe.sh`) re-run on the current tag at the documented sizes with results compared to `evidence/s12/part54.md` |
| TE-10 | **Failure-mode drills as tests** | 6 | The W22 trust-and-recovery drills (worker kill, orchestrator restart, provider outage, lost lease, partial write) scripted so they run repeatably against a throwaway stack, each asserting before/after state and no orphan |

## 4. Phases

Each phase ends with its exit check passing and the report in §6. Stop at the end of the phase. After Q2, the order
is the default; the owner may reorder.

| Phase | Build / do | Exit check |
|---|---|---|
| **Q0 Baseline and scorecards** | Read §1. Re-run every suite (sections 1–11, contracts, packages, section7 vitest + typecheck + build, `make gate`) at HEAD and record counts with the revision. Write both scorecards with evidence links. Draft the test and skip ledger (TE-2) and a platform open-items ledger merging the matrix partials, PROJECT-STATE §4, the STATUS known limits, the roadmap stage gaps and the UI backend gaps. Confirm the GitHub Actions billing block with `gh run view` and write the owner note. **No fixes in Q0** | Scorecards and ledgers committed; every number re-measured this session; the owner confirms the order of Q1–Q10 |
| **Q1 CI and suite health (TE-1, TE-2, TE-3)** | `make ci-local` mirroring `ci.yml`. Random-order and repeat runs per suite; root-cause every flake. Resolve or justify every skip and xfail. When the owner has fixed billing, get Actions green at HEAD and keep the path-gating from `972e7b6` honest (a job skipped by the path filter is reported as skipped, not passed) | 10 random-order runs per suite clean or every flake fixed; ledger complete; `make ci-local` junit summary committed |
| **Q2 Matrix refresh (PL-1)** | Re-verify all 61 rows directive by directive against code and evidence. Fix wrong claims, flip rows only with evidence, recompute counts, update PROJECT-STATE §3 and `final-deliverables.md` | `test_program_docs.py` green; a diff of every changed row with its reason |
| **Q3 Backend for the UI (PL-2)** | Build (a)–(e) one at a time: contract change in `contracts/` (Python and TS mirror), the producing service, tests on SQLite and live PG, spec and SDK regeneration, and a note to the UI prompt's §6 table saying the gap is closed. The plan-step progress event must come from the engine's own step tracking, not inferred from tool calls | Each gap has unit + live-PG tests, the spec drift is clean, and the TS dist is current; a replay run emits step progress events in order |
| **Q4 Engine correctness (PL-3)** | Reproduce first, then fix: timed-out call tool records, push-based session state change, `_with_repo_context`, the repeated-stop event. Build the checkpoint-1 faithful flow from `checkpoint1-redesign-2026-11-21.md` without weakening any of the three laws, and un-skip the test | Each fix has a test that failed before; checkpoint-1 integration test passes; section4 full suite green in random order |
| **Q5 Metering and the real smoke (PL-4)** | Fix provider-reported usage on the openai-completions transport (or capture it from the response another way), with a test against a stub that returns usage. Make in-flight spend use actuals where present. Prepare `run_smoke.py --real` with its cap and model, ask for the budget, then run it on the RC | Ledger rows show provider-reported tokens and cost for a stub run; `--real` evidence JSON with ledger actuals (after approval) |
| **Q6 Eval corpus (TE-6) and efficiency (PL-6)** | Add the worker-image test runners (`pytest` first). Write the new tasks with deterministic graders and anti-gaming checks, validate each with a scripted "perfect agent" and a "no-op agent" (perfect passes, no-op fails) at zero spend, then re-freeze the split and commit it **before any live run**. Upstream the perf A/B changes in one PR-ready branch. Investigate the unused `grep` tool in replay | Every new task: perfect-agent pass, no-op fail, grader unit tests; frozen split committed; perf changes on a branch with their replay numbers |
| **Q7 Live benchmark and publication (TE-7, TE-8)** | Update the preregistration for the expanded corpus (new spend estimate, same per-run cap logic) and get the owner's approval. Run seen, then held-out, then the comparator, per `benchmark-methodology-2026-11-21.md`. Rebuild summaries, write the report and leaderboard, write the reproduction README | Raw files for every attempt committed; `bench_stats.py` rebuilds the published numbers exactly; actual spend ≤ the approved cap; the report lists its limits |
| **Q8 Depth testing (TE-4, TE-5)** | Coverage per section. Mutation testing on the safety core (ask before adding the tool), killing or justifying every survivor. Hypothesis properties for the gate, lifecycle and money formatting. SDK consumer tests. Oldest-schema upgrade rehearsal | Mutation scores per module before → after; coverage report; consumer tests green |
| **Q9 Delegation, data lifecycle, multi-host (PL-5, PL-7, PL-8)** | Ask the owner for the two delegation rulings first. Bind assignment scope to the child engine; write the `HARNESS_SUBAGENTS` enablement criteria; re-drill W13. Hold coverage for bucket lifecycle rules. Residency design plus the smallest slice or the owner's written decision. Object-store backend for the artifact store with a SeaweedFS-backed test and a two-replica drill | A child run cannot act outside its bound scope (negative test); the two-replica drill with the object store passes; residency decision recorded |
| **Q10 Performance, drills and acceptance (TE-9, TE-10, PL-9)** | Zero-spend replay benchmark with a stored baseline and regression threshold. Re-run the load probe on the current tag. Script the W22 failure drills against a throwaway stack. Then the full cross-domain acceptance run at one revision and the platform GO/NO-GO write-up. Final scorecards | Every acceptance component green at one SHA (or a named owner-gated exception); both scorecards final with evidence for every point |

## 5. How to work (every phase)

1. `git status --short`, `git log --oneline -5`, `docker ps --format '{{.Names}} {{.Image}}'`. Read the phase row and its
   ledger entries.
2. `Assumptions:` line plus a numbered plan with a check per step. Two readings, a missing owner ruling, a needed budget
   or a law you would have to bend: stop and ask.
3. **Reproduce first.** Write the failing test or measurement that shows the gap, then fix at the root (grep every caller
   of the function you change), then show the same test passing.
4. After each slice, run the affected suites from their own directories (record counts), the live-PG modules if SQL
   changed, `make guard-isolation`, `scripts/spec_drift.py`, and `make check-ts-dist` if a contract changed.
5. For claims about the running system, write the redeploy request (tag, services, manifest diff, rollback), wait for
   the owner's yes, then run the 5-point verification from the `vv90r6` deploy commit (`daeface`: stack_tags AGREE, schema
   version, service image, guest gate 401, frontend 200) plus the phase's own checks.
6. Commit per slice, by path, with a message that says what now works. Do not push.

## 6. Report at the end of each phase

Write `docs/briefs/evidence/quality/<phase>-report-<date>.md` with:

- **Score movement:** each gate in both scorecards before → after, with the evidence path for every point gained.
- What now works that did not before, in plain sentences.
- Exact commands with counts (passed/failed/skipped per suite, random-order repeats, mutation scores, coverage,
  benchmark trials and spend from `cost_ledger`), and the revision and deployed tag they ran on.
- What was proved on the **deployed** tag vs only at HEAD. Never blur the two.
- Ledger changes: items closed (how: fixed / out of scope / owner decision), opened, still open.
- Owner actions needed, each as one decision or step with the exact command, budget or ruling text ready.
- Commit hashes (local, not pushed).
