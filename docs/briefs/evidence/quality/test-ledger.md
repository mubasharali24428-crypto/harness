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

## 3. Flake suspects

| Test | Q0 evidence | Reproduction rate | Suspected cause | Owner / phase |
|---|---|---|---|---|
| section4 `tests/unit/test_s1_r05_resume_queue.py::test_resume_after_restart_on_done_run_stays_noop` | Failed in the pinned full-suite run (`assert len(calls) == 1`, got 0); passed in the first Q0 full run at `8349f93` | 1/2 full-suite runs; 0/20 module-alone runs | Test sleeps 50 ms after `_dispatch_claim` and then stops, assuming the engine call has happened; under full-suite load the dispatch has not called the engine yet. Timing assumption in the test, not yet shown to be a product bug | Q1: root-cause (wait on the dispatch, not a sleep); no retry |
| section4 random-order failure (named by the prompt) | Not reproduced: no random-order plugin installed | — | Unknown | Q1, after the owner OKs `pytest-randomly` or `pytest-random-order` |
| section1 `test_ratelimit_expensive_ops.py::test_model_call_burst_over_limit_429` (named by the prompt) | Passed in both Q0 runs | 0/2 | Unknown | Q1 repeat runs |

## 4. Coverage of the ledger itself

Suites that exist but CI does not run (so their skips were invisible before Q0): `packages/extensions`
(93 passed), `packages/ops` (77), `packages/telemetry` (31), `integrations/connectors/wiki` (8),
`integrations/connectors/workflows` (35), `scripts/eval` (41), `scripts/ops` (36), `scripts/test_spec_drift.py` (1),
`clients/python` (106), `clients/ts` (33), `clients/vscode` (43), `integration/` (2 passed, 2 skipped).
None of them has a skip or xfail.

Not yet in this ledger (Q1): section7 Playwright specs (`e2e/*.e2e.ts`, need a stack), the
live-PG run of every `env` row above, the Linux run of the Landlock rows.
