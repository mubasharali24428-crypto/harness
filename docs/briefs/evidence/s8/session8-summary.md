# Session 8 — security, privacy, compliance lifecycle (2026-10-19)

## Objective and result

Continued the Session 8 review of parts 31, 52 and 58. Section 4 fixes the SDK profile's shipped
`danger-full-access` default by forcing `sdk-minimal` and appending a per-run `workspace-write`
overlay after operator patches. It also refuses approved plans with non-empty `forbiddenPaths`
before invoking the SDK. The SDK shell still does not use Section 5's command allow-list, and path
enforcement is all-or-nothing at run start; a common execution adapter remains necessary. Migration
0056 adds operator-managed run-retention holds, which Section 4's run purge honors. The global
event/audit retention worker, account deletion, and backup expiry do not honor those holds; this is
not a complete legal-hold workflow.

The Session 7 prerequisite commit `f583d89` is in local history. Its continuation edits remain in the
working tree alongside many unrelated changes; they were left untouched by this Session 8 pass.

## Evidence files

- `part31.md` — data/privacy directives and the Section 4/Section 5 enforcement boundary.
- `part52.md` — security program controls and external evidence gaps.
- `part58.md` — lifecycle, retention, export/deletion, residency and counsel gates.
- `directive-map-and-verification-plan.md` — numbered pre-edit mapping and verification plan.
- `docs/data-map.md` — refreshed storage/provider/retention map.

## Verification run

| Command | Result |
|---|---|
| `cd section5 && .venv/bin/python -m pytest tests/unit/test_policy.py tests/unit/test_s1_r03_sandbox_policy.py tests/unit/test_sandbox_isolation.py -q -k 'not seatbelt_sibling_workspace and not seatbelt_shared_system_temp'` | 73 passed, 2 deselected |
| Same Section 5 tests without deselection | 73 passed, 2 failed: macOS Seatbelt subprocess returned exit 71 for writes to its own workspace/private temp; isolation denied the tested sibling paths. |
| `cd section4 && .venv/bin/python -m pytest tests/unit/test_exports_deletion.py tests/unit/test_exports.py tests/unit/test_retention.py -q` | 44 passed |
| `cd section1 && .venv/bin/python -m pytest tests/test_security_headers.py tests/test_audit_emission.py -q -k 'not billing_plan_change_emits_from_to'` | 29 passed, 1 deselected |
| Same Section 1 tests without deselection | 29 passed, 1 failed: fixture schema has no `teams.budget_usd` column. |
| `cd section5 && .venv/bin/ruff check src/harness_sandbox/policy.py src/harness_sandbox/local_runner.py src/harness_sandbox/api.py` | passed |
| `section4/.venv/bin/python -m pytest section4/tests/unit/test_run_isolation_and_stop_truth.py section4/tests/unit/test_engine.py -q` | 27 passed, 1 skipped; includes the mandatory SDK policy overlay and profile pin regression check. |
| `section4/.venv/bin/python -m pytest section4/tests/unit/test_execution_policy_s8.py section4/tests/unit/test_retention_holds_s8.py section4/tests/unit/test_engine.py -q` | 14 passed, 1 skipped; verifies deny-before-engine for plans with forbidden paths, the mandatory SDK overlay, and run-hold exclusion. |
| `section4/.venv/bin/ruff check section4/src/harness_orchestrator/engine.py section4/src/harness_orchestrator/service.py section4/src/harness_orchestrator/retention.py section4/tests/unit/test_run_isolation_and_stop_truth.py section4/tests/unit/test_execution_runs.py section4/tests/unit/test_retention.py` | passed. |
| `DSH_HOME=/private/tmp/harness-s8-dsh section4/.venv/bin/dsh --profile sdk-minimal --patch /private/tmp/harness-s8-enforce.yml --dump-config` | passed; composed config shows final `workspace-write` overlay with explicit run root. |

No PostgreSQL migration, Docker/container boundary, Landlock, live provider, production storage,
backup expiry or external security/counsel check was run. No contracts were changed.
The installed DeepSeek SDK API was source-inspected: its callback observes notifications after
dispatch and exposes no pre-tool authorization hook suitable for the Section 5 allow-list.

## Remaining high-priority work

1. Wire every Section 4 model/tool invocation through a common hard execution boundary with Section
   5 command allow-list and selective path checks, covering run start, queue claim, retry, agents and
   reviewers. The current SDK shell has workspace-write confinement and a run-level refusal when
   any forbidden path is declared, but it has no command allow-list integration.
2. Add an audited hold administration flow and make event/audit retention, user deletion and backup
   expiry honor holds. Current migration 0056 holds protect Section 4 run purge only.
3. Decide account erasure and retention for identity/session/provider tables; part 50's authored
   content deletion is not complete account deletion.
4. Obtain operator evidence for encryption, deployment roles, provider data handling and backup
   expiry, plus independent pentest and counsel review. No compliance/certification claim is made.

Parts 31, 52 and 58 remain partial. Parts 52/58 remain externally capped pending the specified
independent review. Part 44 remains partial; Session 9 has not started.
