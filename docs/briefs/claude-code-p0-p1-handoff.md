# Continue P0/P1 in Claude Code

Work in `/Users/user/untitled folder 2/harness`. Continue the existing implementation; do not restart or discard the working tree. Read `AGENTS.md`, `docs/briefs/multiplayer-pilot-roadmap.md`, `docs/briefs/multiplayer-readiness.md`, and `docs/briefs/p0-container-isolation.md` first. Frozen contracts require a version bump and matching mocks for breaking changes. Do not commit, push, merge, or create real PRs without authorization.

## User's scope and constraints

Finish P0 and P1 and explain/verify all 25 skipped P0 tests. A phase is complete only when exit evidence exists. P2/P3 are subsequent work, not automatically authorized by this handoff. Use only free or dirt-cheap models for integration tests; prefer explicitly zero-price models. Never silently switch to paid models. Keep keys out of source, logs, prompts, screenshots, and version control.

The user supplied an OpenRouter key. It is stored privately in `section3/.env.pilot-openrouter` (mode 0600, gitignored). Read it only through the test's existing helper; never print or copy it into this document. No need to ask the user for the key again. The exact free Docker/OpenRouter test was explicitly approved and has now PASSED.

## Current truth

P0 previously passed all 18 Docker isolation checks, including real Ollama hello-world and cancellation. P0 evidence is under `docs/briefs/evidence/p0/`. P1 code and regression fixes are implemented, but **P1 is not yet complete**: the last clean-DB browser run failed its strict zero-errors check on an owned project's checkpoint 404. A test-first fix now exists but the running sandbox has not loaded it. The summary at the top of a run page also goes stale after stop and still needs a fix.

The original 25 skips now have separate passing evidence:

* 18 opt-in Docker tests: P0 evidence above.
* Six real PostgreSQL tests: `/tmp/harness-p1-pg.xml`, six passed against disposable PostgreSQL 18 on port 5814.
* One real OpenRouter test: `/tmp/harness-p1-openrouter-free.xml`, **one passed in 12.93 seconds** on 2026-10-18 using `liquid/lfm-2.5-2.6b:free`. The test checks the official catalog for zero input/output pricing before sending a synthetic file task and asserts the container worker creates `engine_ok.txt`.

A combined final regression run has NOT happened. Do not describe separately verified skips as a final all-green suite.

## Important changes already in the working tree

P0: `section4/worker.Dockerfile`, container confinement/runtime/relay, kill-and-confirm cancellation, fail-closed real-runtime gate, 18 Docker integration checks, boundary unit tests. Worker uses pinned runtime 0.2.1rc2, non-root, readonly root, private egress relay, resource limits and workspace/session-only mounts. `/tmp` must permit execution for the native addon. Do not weaken isolation to make tests pass.

P1 backend:

* Section 1 returning login reuses a single existing team; multiple memberships require selection. Regression `section1/tests/test_returning_login.py` passes.
* Section 4 handoff accepts ORM role `ENGINEER` through normalized comparison. Test in `unit/test_collab_backend.py`.
* Section 4 blocks done while staged approvals are pending, approved, or executing. Staged deletion test verifies pending -> approved -> applied before done succeeds. Added minimal approval table to older partial SQLite fixtures.
* Section 2 blocks draft PR side effects while staged changes remain unapplied; fails closed on staging query error. Full Section 2 suite: **39 passed**, `/tmp/harness-p1-section2.xml`. Test package imports fixed.
* Section 5 owned project with no checkpoint repository now lists empty checkpoints, while foreign project remains forbidden/not found. All 18 checkpoint tests passed. Running sandbox still has old code.
* Section 4 engine now preserves scrubbed SDK provider errors rather than reporting empty completion. New error-finish regression passed. Rebuild worker images after source changes.

P1 UI:

* Mounts plan gate on command center and run detail, preserves the draft and actual proposal, retains returned run ID, shows Open shared run, exposes Start after approved plan. Classifier test fixed.
* Shared UI shows withheld diff, leader approve/apply actions, and pending state blocks Done until application. Backend also gates Done and PR creation.
* Login waits for hydration to prevent accidental native form submit.
* StatusPanel exposes `data-testid="run-state"` for observable smoke checks.
* Still fix aggregate refresh in `section7/app/projects/[projectId]/runs/[runId]/page.tsx`: its `fetchAgg` effect only fetches once. Use cleanup-safe polling or another explicit refresh and add verification that heading summary updates after stop.

OpenRouter root cause: bundled native DeepSeek adapter uses Anthropic Messages; OpenRouter rejected it as INVALID_REQUEST. `section4/tests/fixtures/openrouter.patch.yml` explicitly replaces only that adapter with the bundled pi-ai OpenAI-completions adapter. Production engine isolation remains intact. `test_live_run_against_openrouter` now uses strict opt-in, no outage-to-skip behavior, a correct per-run file assertion, private credential helper, catalog price check, and that transport overlay. Latest test passed with the liquid free model. Its default model still names cohere free; set the explicit tested model in commands or consider making the tested model the default.

## Test harness and remaining execution

`deploy/pilot/stack.py` assembles real identity, orchestrator and sandbox services against real migrations and synthetic team/users. `deploy/pilot/worker.py` is an explicitly labeled deterministic Docker worker: P1_DELETE deletes a seeded file for approval; P1_SLOW supports a sleeping worker. Test-only Docker target includes this worker; production target excludes it.

`section7/e2e/multiplayer.e2e.ts` uses two real browser contexts, no API mocks: login, propose/approve/create, join both, claim leader, guidance, handoff, start, inspect withheld deletion, approve, apply, Done unblocks, stop. It asserts zero console/network/page errors. Previous execution completed every interaction but failed the final errors assertion on the checkpoint issue. Strengthen stale aggregate verification too.

`deploy/pilot/run_smoke.py` is newly written and **not yet executed**. It refuses occupied ports, rebuilds the test image, creates a fresh disposable PG18 database, runs the real stack, runs strict Playwright, verifies deletion in actual workspace, saves JUnit/PNG/live-result.json under `docs/briefs/evidence/p1/`, and cleans its own processes/database. Review it before execution. Backend and frontend children already use `start_new_session=True`. Ruff still flags two SIM115 calls: ExitStack owns opened log handles; use a justified targeted noqa or restructure with a context manager. It uses synthetic dev sign-in, so label evidence accordingly; it does not prove production email delivery.

Current scratch listeners owned by this task, verified at handoff:

| Service | Port | PID |
| --- | --- | --- |
| Identity | 8101 | 45219 |
| Orchestrator | 8104 | 45978 |
| Sandbox | 8105 | 46775 |
| Next | 3107 | 46893 |

Recheck process identity before stopping. Stop only these scratch services before `run_smoke.py`; leave unrelated services alone. Task-created PostgreSQL container `harness-p1-pg` exposes 5814, synthetic credentials/database `pilot`; additional manual DB `pilot_flow`. Existing broader harness deployment is unrelated.

Built-in browser manual two-user flow also reached applied deletion and stop on run `run-0186134cf92e`, project `p1-project`. Screenshot `docs/briefs/evidence/p1/builtin-browser-complete.png` is provisional: it contains the stale top summary and pre-fix checkpoint message. Replace it with final clean evidence, don't present it as final proof.

Suggested next steps:

1. Fix stale aggregate refresh and strengthen its test/e2e assertion. Finish runner lint. Inspect/remove temporary `deploy/pilot/provider_probe.py` if no longer needed; avoid leaving ad hoc provider-call tooling without the catalog guard.
2. Stop verified scratch listeners and run `section4/.venv/bin/python deploy/pilot/run_smoke.py`. Fix failures with tests first. Confirm fresh DB, zero browser errors, and actual deletion applied. Save final screenshots/checklist/JUnit.
3. Rebuild final P0 production/isolation images and rerun relevant regression suites. Use section-specific working directories. Section 1 full suite previously failed one ownership default test only because root `.env` set ownership false; rerun from `section1` with `HARNESS_REQUIRE_OWNERSHIP=true`. Required Section 1 lint: `./.venv/bin/ruff check src tests scripts`.
4. Section 4 combined opt-ins use `HARNESS_P0_DOCKER=1 HARNESS_P0_LIVE=1 HARNESS_TEST_PG=postgresql://pilot:pilot@127.0.0.1:5814/pilot HARNESS_LIVE_OPENROUTER=1 HARNESS_TEST_OPENROUTER_MODEL=liquid/lfm-2.5-2.6b:free`. Inspect integration test configuration/image tags before running. Local Ollama on 11434 has `qwen3.5:9b`. Individual six PG tests are `unit/test_cost_rollup_live_pg.py` and `unit/test_demo_seed_live_pg.py`.
5. Exact known-passing provider command from repository root: `env HARNESS_LIVE_OPENROUTER=1 HARNESS_TEST_OPENROUTER_MODEL=liquid/lfm-2.5-2.6b:free section4/.venv/bin/python -m pytest section4/tests/unit/test_engine.py::test_live_run_against_openrouter -q --tb=short --junitxml=/tmp/harness-p1-openrouter-free.xml`.
6. Frontend tests should limit workers: `npm test -- --maxWorkers=2 --minWorkers=1` from section7, then typecheck/build as appropriate. Earlier unrestricted run had two timing failures under resource pressure; do not call it green. Section4 full suite also needs rerunning after fixture fixes; targeted 64 affected tests passed.
7. Run relevant Ruff checks and `git diff --check`; preserve pre-existing user edits/untracked material. Copy durable regression evidence from `/tmp` into the P1 evidence folder. Write P1 completion/skip-audit documentation and update readiness only to the extent actual evidence supports it. Keep P2/P3 pilot gate explicitly outstanding.

Do not claim P1 done until its clean-DB browser proof is green. Continue autonomously within P0/P1 scope, but report any actual environment blocker honestly. No further model test is currently running.
