# Test and skip ledger (TE-2) — Q0 draft, 2026-11-21

Every skip, xfail and conditional test in the repo, with its reason, a class and a next action.
Built from two sources so neither can hide the other:

1. **Static scan** (§1): every `pytest.skip / skipif / xfail / importorskip` and every TS
   `test.skip / it.skip / describe.skip / fixme / xit` in `section*`, `contracts`, `mcp`,
   `packages`, `integrations`, `integration`, `scripts`, `deploy`, `clients` (vendored
   `.venv`/`node_modules`/`build`/`dist` excluded). 72 marker lines.
2. **Runtime** (§2): what actually skipped or xfailed in the Q0 junit runs at `8349f93`
   (Q0 report §3), grouped by reason.

Classes: **env** = needs an environment this Mac run did not provide (live PG, Linux, Docker,
promtool); runs elsewhere or in Q1 · **opt-in** = by design, needs a paid model or a real
local model; never runs by default · **by-design** = documented product semantics, with the
decision named · **defect** = the test or the code is wrong and needs a fix.

Rule (prompt §2): nothing here may be resolved by loosening the test. A row leaves this ledger
only by running green, by a root-cause fix, or by a written owner decision.

## 1. Static markers by group

| Group | Files (markers) | Reason in code | Class | Next action |
|---|---|---|---|---|
| Live PostgreSQL | section1: `test_session{3,4,5,11}_pg.py`, `test_policy_sockets_pg.py`, `test_session_owner_transfer_pg.py`, `test_upgrade_rehearsal_pg.py`, `test_collab_push_pg.py`, `test_backup_metrics_pg.py`, `test_audit_chain_pg.py` (module-level); `test_f1_envelope_idempotency.py:470-477`, `test_fk_deletion_story.py:641`, `test_ws_fingerprint.py:535`, `test_money_numeric.py:536,728`, `test_partitioned_retention.py:381,472`, `test_runs_index_tuning.py:546` · section4: 14 `*_live_pg.py` modules + `test_pilot_metrics.py:246`, `test_demo_seed_live_pg.py:66`, `test_cost_rollup_live_pg.py:87` · `integration/test_checkpoint1.py:260` | `HARNESS_TEST_PG` / `HARNESS_DATABASE_URL` unset, or asyncpg missing | env | Q1: run all against a throwaway `postgres:18` (`HARNESS_TEST_PG_EXTERNAL=1`, migrated with `deploy/runners/migrate.py`); CI's `nightly-pg-probe` runs only `-k live_pg` in section4 and the s1 probe |
| section11 live PG | `section11/tests/test_live_pg.py:46` | needs Docker + `pgvector/pgvector:pg18` | env | Q1 with a throwaway pgvector container |
| Linux Landlock | section5 `test_landlock_confinement.py:10,15`, `test_landlock_read_isolation.py:17,23,126` | linux-only / landlock unusable | env | Covered by CI `native-linux-container` (blocked by billing); Q1: run in the Linux worker image locally |
| OS confinement present | section5 `test_shell_gate.py:14`, `test_byo_runner.py:24`, `test_confine_argv.py:15`, `test_confinement.py:21`, `test_sandbox_isolation.py:364`, `conformance/test_sandbox_conformance.py:26`, `test_hx_live_host_ops.py:119`; section4 `test_isolation_and_staging.py:15` (Seatbelt); `packages/extensions/tests/test_stdio_confinement.py:26` | needs Seatbelt (macOS) or Landlock | env | Runs on this Mac for the Seatbelt half; Linux half as above |
| Docker / node tools | section4 `test_forbidden_path_mounts.py:66`, `test_scope_mounts.py:111` (docker), `test_shell_breaker.py:13` (node); section4 `integration/test_container_p0.py:23` (`HARNESS_P0_DOCKER=1`) | binary missing / opt-in Docker | env | Q1: record whether they ran here; P0 container suite opt-in → add to `make ci-local` with Docker |
| promtool | section1 `test_alert_rules_load.py:134` | promtool not installed | env | Q1: run via the `prom/prometheus` image (`scripts/test-alerts.sh`) |
| Cross-venv | section4 `test_context_resolver.py:56` (`_NEEDS_S11_VENV`) | needs section11 venv | env | CI runs it in `nightly-pg-probe`; Q1 ci-local the same |
| Misc env | section1 `test_version_pin_conformance.py:172` (git), `test_examples_are_runnable.py:47,60` (pytest), section4 `test_live_mode.py:442` (`importorskip yaml`), `scripts/test_spec_drift.py:12` (section4 venv) | tool missing | env | Confirm each ran in Q0 (§2); any that skipped here is a venv gap to fix in Q1 |
| Real model | section4 `unit/test_engine.py:86` (`HARNESS_LIVE_OPENROUTER=1`), `integration/test_container_p0.py:217` (`HARNESS_P0_LIVE=1`) | opt-in paid / real local model | opt-in | Stays opt-in; runs only under an approved budget (Q5/Q7) |
| Read-scope xfails (strict) | section5 `test_shell_gate_real_boundary.py:220` `leak_xfail` (G6b `grep -rn … *`, G7i `python3 -c open(...)`) | `scopePaths` is a write boundary; reads outside it leak by design | by-design (pending owner) | **Accepted risk awaiting the owner's F-05 signature** (`90day/a01-xfail-disposition-2026-11-21.md` §3). Not a code fix without the F-05 ruling |
| macOS read xfails (strict, darwin-only) | section5 `test_shell_gate_real_boundary.py:149` `read_xfail` | macOS returns `denied=False, exitCode=0` on rule-7 reads outside the workspace | — | **Resolved**: applied to no row (`_R_FAIL_OPEN` is empty); all R rows pass (§2) |
| Scope-edge xfails (strict) | section5 `test_shell_gate_scope_edges.py:61` `_xfail` | "fail-open: command can touch a file outside src…" | — | **Resolved**: helper applied to no row; every case passes (§2). Leftover helper belongs to the security lane |
| checkpoint-1 | `integration/test_checkpoint1.py:111` | flow predates three product laws | **defect** (test design) | Build the faithful flow (`checkpoint1-redesign-2026-11-21.md`), un-skip (Q4) |
| Playwright conditional | section7 `e2e/journey.e2e.ts:165,211` | earlier leg minted no run | conditional | A skip here means the earlier leg failed silently; Q1: make it a failure of the earlier leg, not a skip |

## 2. Runtime skips and xfails at `42f337fc` (pinned worktree, this Mac)

Source: `q0/junit-summary.tsv` (rebuilt from the junit XML of every suite by the Q0 parser).
Totals: **132 skipped, 4 xfailed, 1 failed** (the flake in §3). Everything else ran and passed.

| Suite | Skipped | Reason (grouped) | Class |
|---|---|---|---|
| section1 | 62 | live PG: `HARNESS_TEST_PG` unset (18 modules/classes; 25 of them in `test_audit_chain_pg`, 9 in `test_session11_pg`, 4 in `test_upgrade_rehearsal_pg`) | env |
| section4 | 42 | live PG: 14 `*_live_pg` modules + `test_pilot_metrics` | env |
| section4 | 17 + 1 | `test_container_p0.py` opt-in Docker (17) and opt-in real local model (1) | env / opt-in |
| section4 | 1 | `unit/test_engine.py` opt-in OpenRouter | opt-in |
| section5 | 8 | Landlock linux-only (`test_landlock_confinement` 2, `test_landlock_read_isolation` 6) | env |
| integration | 2 | `test_checkpoint1_full_flow` (law conflict) + its live-PG sibling | defect + env |
| all other suites | 0 | — | — |

Ran here, not skipped: promtool (`/opt/homebrew/bin/promtool`, `test_alert_rules_load.py`),
Seatbelt confinement rows, Docker-mount rows, node-based `test_shell_breaker.py`.

### xfails (strict), section5 `test_shell_gate_real_boundary.py` G rows

| Row | Command | Disposition |
|---|---|---|
| G2 | `cat d?cs/y.txt` | **None on file.** Not listed in `a01-xfail-disposition-2026-11-21.md` |
| G-grep-r | `grep -r {DOCS_MARKER}` | **None on file.** Not listed in the disposition |
| G6b | `grep -rn {DOCS_MARKER} *` | Accepted risk pending the owner's F-05 signature (disposition §3) |
| G7i | `python3 -c "print(open('docs/y.txt').read())"` | Same |

All four are the same mechanism (`scopePaths` is a write boundary, so reads of in-workspace files
outside it leak). The disposition should name all four. Owner signature needed (Q1 hand-off).

### The "10 macOS read-scope xfails" named in the prompt

**Already resolved at this revision.** The R rows' `read_xfail` is applied only to ids in
`_R_FAIL_OPEN`, which is now the empty set (`test_shell_gate_real_boundary.py:176`), so every
macOS read row runs as a normal test and passes. `_xfail` in `test_shell_gate_scope_edges.py:60`
is defined but applied to no row: all its fail-open rows pass. Leftover helper; the security
lane owns that file (named here, not removed).

## 2b. Q1 resolutions (2026-11-21)

| Row (§1 / §2) | Resolution | Evidence |
|---|---|---|
| Live PostgreSQL (section1 62 skips, section4 42) | **Ran.** Every live-PG module executed against a throwaway `pgvector/pgvector:pg18` (127.0.0.1:55499, migrated 74/0076) in all 10 random-order runs; section1 then reports 0 skips, section4 19 (the opt-in Docker/model rows only) | `q1/random*/section{1,4}/run*.xml`; Q1 report §3 |
| Live-PG helpers pointed at 5499 | **Defect, fixed** (`2fda5bd8`): 22 helpers ignored `HARNESS_TEST_PG_DSN` under `HARNESS_TEST_PG=1` and defaulted to 5499 (the demo DB on this Mac). Pinned by `scripts/test_ci_local.py` (22 offenders at the parent, 0 now) | commit `2fda5bd8` |
| Linux Landlock (section5 8 skips) | **Ran on Linux** (Docker Desktop VM kernel 6.12.76-linuxkit, `landlock_supported: True`, code fed as a tar stream, not a bind mount, uid 10001, `HOME` on its own tmpfs): **542 passed, 10 skipped, 4 xfailed, 0 failed**. All 8 Landlock rows passed. The 10 skips there are the Seatbelt rows (macOS-only by design) | `q1/linux/section5.log` |
| section11 live PG | **Ran** (spins its own pgvector container); order-dependence found and fixed (`c509a4dc`, §3) | §3 |
| promtool, Docker-mount, node, Seatbelt rows | Ran on this Mac (no skip at runtime) | Q0 §2 |
| Opt-in real model (`test_engine.py:86`, `test_container_p0.py:217`) | **Justified, by-design opt-in**: paid/real-model calls never run without an owner budget (prompt §2). Run under Q5/Q7 budgets | — |
| `test_container_p0.py` opt-in Docker (17) | **Justified, env opt-in**: needs `harness-engine-worker:p0` and `:p0-tests` built from the revision under test (the local ones date from 14:53Z, pre-Q1). Not built in Q1 (CPU shared with the 10× runs); command in the Q1 report §7 | — |
| checkpoint-1 (`integration/test_checkpoint1.py:111`) | **Justified defect, owned by Q4** (faithful flow per `checkpoint1-redesign-2026-11-21.md`). Its live-PG sibling (`:260`) needs `HARNESS_TEST_PG=1` exactly and runs under `make pg-probe-integration` in ci-local | — |
| Playwright `journey.e2e.ts:165` | **Justified, unreachable guard**: the previous step asserts `expect(runId).not.toBe("")`, so an empty run id fails there first; the skip cannot mask a failure | `section7/e2e/journey.e2e.ts:155-165` |
| Playwright `journey.e2e.ts:211` | **Justified, env**: OTP capture needs `E2E_IDENTITY_URL` (a stack) | — |
| G-row xfails (4) | **By-design pending owner**: the A-01 disposition now names all four (correction §5 appended); signature still open | `90day/a01-xfail-disposition-2026-11-21.md` §5 |

## 3. Flake suspects

| Test | Q0 evidence | Reproduction rate | Suspected cause | Owner / phase |
|---|---|---|---|---|
All four found in Q1 are **fixed at the root in `c509a4dc`** (no retry, no skip, no loosened assertion):

| Test | Reproduction (before) | Root cause | Fix | After |
|---|---|---|---|---|
| section4 `test_s1_r05_resume_queue.py` ×2 and `test_resume.py` ×3 (the sleep-then-stop/pause tests) | Q0 pinned run + random seeds 2 and 6; under 8× parallel load **29/40** and **18/40** module runs failed | A fixed 50 ms sleep assumed the spawned engine task had started; under load the stop/pause landed first, so the runner never ran (`assert 0 == 1`) | Wait for the runner's recorded call (`_until_engine_started`, then the stub still sleeps `delay`, so the engine is genuinely mid-flight) | **0/40** and **0/40** under the same load |
| section4 `test_staging_e2e.py::test_done_is_blocked_until_staged_deletion_is_applied` | Random seed 4; **fails every time run alone** | (a) `artifacts.configure_artifacts()` in two fixtures (`test_artifacts_support`, `test_visuals_db`) set a module global and never reset it, so later tests' artifacts calls used an earlier test's DB; (b) that masked a real fixture gap: the shared collab `env` DDL lacked `run_changes`, which `signal_done` needs since `record_done_changes`. Found by bisection (`test_artifacts.py` is the module that made it pass) | Global registered with `monkeypatch` (restored at teardown); `run_changes` added from migration 0049 | Passes alone and with neighbours (176 passed); section4 full 2398 passed |
| section11 `test_live_pg.py::test_full_migration_walk_then_real_vector_search` | **6/10** random seeds | Two tests share one module-scoped container; the fresh-install walk asserted it was the first migration. Order-dependent | The walk gets an empty database of its own on the same server; assertions unchanged | Seeds 1, 3, 4, 7, 8 (failing) and 2 all pass |
| language `test_host_contract_runner.py::test_a_replay_reproduces_the_digest_without_touching_the_host` | 1/10 seeds (seed 10) | Compared wall-clock `durationMs` (0 vs 1 ms); the digest excludes wall-clock by design (`contract_runner.py` docstring) | Compare operations minus that one field; digest equality still asserted | 0/30 seeds |

Named by the prompt, not reproduced in Q1:

| Test | Q1 evidence |
|---|---|
| section4 random-order failure | The section4 random-order failures found are the three rows above; no other section4 test failed in 10 seeds (see §3b) |
| section1 `test_ratelimit_expensive_ops.py::test_model_call_burst_over_limit_429` | Passed in every Q0 and Q1 run (2 + 10 + 10 random) |

## 4. Coverage of the ledger itself

Suites that exist but CI does not run (so their skips were invisible before Q0): `packages/extensions`
(93 passed), `packages/ops` (77), `packages/telemetry` (31), `integrations/connectors/wiki` (8),
`integrations/connectors/workflows` (35), `scripts/eval` (41), `scripts/ops` (36), `scripts/test_spec_drift.py` (1),
`clients/python` (106), `clients/ts` (33), `clients/vscode` (43), `integration/` (2 passed, 2 skipped).
None of them has a skip or xfail.

Not yet in this ledger (Q1): section7 Playwright specs (`e2e/*.e2e.ts`, need a stack), the
live-PG run of every `env` row above, the Linux run of the Landlock rows.
