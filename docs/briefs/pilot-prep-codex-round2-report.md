# Codex round 2 pilot preparation — 2026-10-18

Work is uncommitted in the shared checkout. Only the task-authorized files below and this report were written. Protected production files, contracts, other engineers' changes, credentials, branches and stashes were not modified. No model calls, notifications, pilot deployment or real-inbox tests were made.

## Task F — done

Changed:
- `docs/briefs/pilot-user-guide.md`
- `docs/briefs/pilot-supervisor-checklist.md`
- `docs/briefs/pilot-baseline-guide.md`

Rechecked claims against current source, added file:line/result appendices, corrected rollback step 1 to match the release plan, documented operator seed refresh/copy behavior and full-byte staged approval binding. Updated baseline/supervisor date-window descriptions after Task H so the final guides agree with the final code. Recommended manual procedures are distinguished from software behavior.

Check: `section4/.venv/bin/python -` (heredoc below), exit 0:

```python
import json
from harness_orchestrator.pilot_metrics import compare
with open('docs/briefs/evidence/pilot-prep/baseline-example-FICTIONAL.json') as f:
    b = json.load(f)
for n in (4, 5):
    print(compare({'elapsedSeconds': {'n': n, 'median': 2400},
                   'runs': {'finished': 5, 'accepted': 4}}, b))
```

Results: baselineN=5, baseline median=50 minutes, acceptance=0.8; pilotN=4 → claimable=false; pilotN=5 → claimable=true, medianTimeChangePct=-20.0, descriptive-only verdict. This checks the actual comparison consumer, not a schema validator. The round-1 validation log is historical evidence, not presented as rerun.

Release-plan discrepancies/qualifications found (protected plan left unchanged):

| Plan location | Difference from code or corrected guides |
|---|---|
| Flag/allowlist section: named test covers all 15 routes | `test_pilot_allowlist_gates_every_collab_route` currently sends requests to eight routes, not fifteen. Source inspection finds gates on all fifteen. The new tests cover cross-team denial on all fifteen, but are not a replacement for missing allowlist-route cases. |
| Support table: if leader gone, request handoff | `collab.py:327` requires the current leader to offer handoff. An absent leader cannot be replaced through these routes. Guides say escalate; no forced takeover is claimed. |
| Known limits: restarts do not replay guidance | `service.py:959` calls the engine before persisting delivery at `:962`. A crash between them may replay guidance. The user guide states this limit. |
| Baseline: five tasks on each side | `pilot_metrics.py:43` counts nonnegative baseline durations and valid nonnegative pilot elapsed durations. Five created runs alone are insufficient. Acceptance counts audit rows, not unique accepted tasks. |
| Retention summary | The abbreviated plan omits the additional latest-event cutoff (`retention.py:84`), terminal approval diff blanking and collaboration-event deletion (`:99`). Supervisor guide describes them. This is missing detail rather than conflicting behavior. |
| Rollback audit preservation | This retention implementation preserves audit rows; the universal statement about every job/external operator cannot be verified from this review. Supervisor guide limits the verification accordingly. |
| Metrics examples | All-history examples remain valid; Task H adds optional creation-date selection. Guides explain this cohort window is not an activity-time window. |

The updated allowlist rollback and workspace-source wording agree with current code. Evidence gaps remain: no rerun of UI/browser proofs, no real baseline/pilot, no real inbox, no deployment-wide assertion that every external job preserves audit data. The plan's recorded suite counts are historical snapshots, not current combined-suite evidence.

Outside scope: `pilot-prep-evidence.md` still says provisioning is missing; `security-review-collab.md` marks the digest defect fixed near its top but ends by recommending it be resolved. Both are protected and were left unchanged.

## Task G — done for the requested denial categories

Created:
- `section4/tests/unit/test_collab_denial_audit.py`
- `docs/briefs/collab-denial-audit-matrix.md`

54 cases: all fifteen routes for cross-team requests; all twelve writer routes for viewer writes; nonparticipant, nonrequester, nonleader, self-approval, expired/stale approvals (including apply), wrong-recipient handoff, and additional state/recipient denials. Each verifies HTTP status/code and newly written audit actions. The 44 missing-audit cases use strict AssertionError-only xfail. Wrong HTTP responses use pytest.fail, so authorization failures cannot be mistaken for expected audit gaps. Existing fixture/database helpers are reused; production authorization is not mocked. Matrix lists each case and observed audit presence.

Exact test commands, separate invocations:

| Command | Exit | Result |
|---|---:|---|
| `section4/.venv/bin/python -m pytest section4/tests/unit/test_collab_denial_audit.py -q` (initial) | 2 | Collection error: `ModuleNotFoundError: No module named 'test_collab_backend'`; corrected import to `tests.unit.test_collab_backend` |
| Same command after correction | 0 | 10 passed, 44 xfailed |
| `HARNESS_TEST_PG=postgresql://pilot:pilot@127.0.0.1:5815/pilot section4/.venv/bin/python -m pytest section4/tests/unit/test_collab_*.py -q` (sandboxed) | 1 | 37 passed, 44 xfailed, 4 setup errors: `PermissionError: [Errno 1] Operation not permitted` connecting to 127.0.0.1:5815 |
| Same collaboration command with approved localhost access | 0 | 41 passed, 44 xfailed, 0 skipped; includes existing real-PG chaos cases |
| New-file command after import/format cleanup | 0 | 10 passed, 44 xfailed |

Infrastructure commands:
- `docker run -d --name harness-codex-pg -p 127.0.0.1:5815:5432 -e POSTGRES_USER=pilot -e POSTGRES_PASSWORD=pilot -e POSTGRES_DB=pilot postgres:18`: sandbox exit 1 (Docker socket permission); approved retry exit 0.
- `docker rm -f -v harness-codex-pg`: approved cleanup exit 0, output `harness-codex-pg`.

No other containers or reserved service ports were touched. New tests use SQLite; existing chaos tests use scratch PG18. No claim of exhaustive malformed-request, database-failure, filesystem-failure or race-branch fault injection. In particular, simultaneous CAS loser branches are not individually audit-pinned in this new matrix; existing concurrency tests run separately within the collaboration suite.

Outside scope: the tests confirm audit gaps but intentionally make no production fixes or policy decision about which denials must be audited.

## Task H — done

Changed only the allowed module and test file:
- `section4/src/harness_orchestrator/pilot_metrics.py`
- `section4/tests/unit/test_pilot_metrics.py`

Added optional `--since YYYY-MM-DD` and `--until YYYY-MM-DD`, parsed as UTC midnight. Since is inclusive; until exclusive. Invalid dates/formats and since > until exit 2 with a clear argparse error before any DB access. Equal bounds are valid and select no runs. Each flag also works alone. Bound SQL parameters filter `agent_runs.created_at` in every run-selection query. Both omitted preserve all-history behavior; output keys and baseline calculations are unchanged. Documentation is in the module docstring; Task F guides reflect the final behavior.

“Accepted” is the count of rows in `audit_log` where `action = 'review.approved'` and `target_id` is a selected run ID. It does not deduplicate run IDs, require that the run finished, or independently verify human task acceptance. All associated audit/event activity for the selected creation cohort counts, including activity outside the date window. `baselineComparison.pilotAcceptRate` divides this count by finished runs and can exceed one when approval rows repeat.

Commands:
- `section4/.venv/bin/python -m pytest section4/tests/unit/test_pilot_metrics.py -q`: exit 0, 17 passed. Rerun after strict ISO-date roundtrip adjustment: exit 0, 17 passed.
- `section4/.venv/bin/ruff check section4/src/harness_orchestrator/pilot_metrics.py section4/tests/unit/test_pilot_metrics.py section4/tests/unit/test_collab_denial_audit.py`: final exit 0. Initial check found import ordering/long lines in the new denial file; corrected with `section4/.venv/bin/ruff check --fix --select I section4/tests/unit/test_collab_denial_audit.py` (exit 0) and `section4/.venv/bin/ruff format section4/tests/unit/test_collab_denial_audit.py` (exit 0).

Coverage includes omitted-window equivalence, inclusive/exclusive bounds, older-run exclusion across related tables, activity beyond the window, bad dates/reversed bounds, CLI parsing, unchanged JSON keys and baseline output. Metrics tests use SQLite; the new window was not separately exercised against Postgres.

Outside scope: acceptance remains an audit-row proxy; no metric-schema or acceptance-policy redesign was made.

## Task I — done

Created `docs/briefs/smtp-tls-policy-options.md` from source inspection of notifier.py, config.py and the SMTP transport tests. It documents exact current behavior/lines, proposed variables and defaults, refusal behavior, tests/migration risks for three alternatives, and recommends default TLS enforcement with an explicit development opt-out. Proposed variables are clearly not existing settings. The memo covers both OTP and general notification senders and calls for certificate/hostname validation.

Commands: source reads with `nl -ba section1/src/harness_identity/notifier.py`, `cat section1/src/harness_identity/config.py`, `cat section1/tests/test_smtp_otp_transport.py`, and focused `sed`/`rg` reads, exit 0. No SMTP suite was run in this docs-only task. Refusal/TLS handshake/certificate behavior and actual inbox compatibility remain unverified dynamically.

Outside scope: general notification delivery has the same fallback; a future policy implementation should cover both paths.

## Final checks and working tree

- `section4/.venv/bin/ruff check section4/src section4/tests`: exit 0, all checks passed (final rerun also exit 0).
- `git diff --check`: exit 0, no output. This checks tracked diffs; task files were also inspected directly because many were already untracked at task start.
- These are separately run suites, not one combined green run. Deprecation warnings remain for Starlette/httpx and SQLite datetime adaptation.
- The status below contains pre-existing work by other engineers as well as this task's changes; it is not an attribution list.

`git status --short` (exit 0):

```text
 M .dockerignore
 M .gitignore
 M AGENTS.md
 M Makefile
 M clients/python/harness_sdk/github.py
 M clients/python/harness_sdk/identity.py
 M clients/python/harness_sdk/meeting.py
 M clients/python/harness_sdk/orchestrator.py
 M clients/python/harness_sdk/review.py
 M clients/python/harness_sdk/router.py
 M clients/python/harness_sdk/sandbox.py
 M clients/ts/src/github.ts
 M clients/ts/src/identity.ts
 M clients/ts/src/meeting.ts
 M clients/ts/src/orchestrator.ts
 M clients/ts/src/review.ts
 M clients/ts/src/router.ts
 M clients/ts/src/sandbox.ts
 M docs/briefs/multiplayer-readiness.md
 M docs/briefs/multiplayer-session1-checkpoint.md
 M scripts/sdk-gen/specs/github.openapi.json
 M scripts/sdk-gen/specs/identity.openapi.json
 M scripts/sdk-gen/specs/meeting.openapi.json
 M scripts/sdk-gen/specs/orchestrator.openapi.json
 M scripts/sdk-gen/specs/review.openapi.json
 M scripts/sdk-gen/specs/router.openapi.json
 M scripts/sdk-gen/specs/sandbox.openapi.json
 M section1/src/harness_identity/audit.py
 M section1/src/harness_identity/auth.py
 M section1/src/harness_identity/notifier.py
 M section10/openapi.json
 M section2/src/harness_github/app.py
 M section2/src/harness_github/service.py
 M section2/tests/conftest.py
 M section2/tests/test_oauth_flow.py
 M section4/openapi.json
 M section4/src/harness_orchestrator/__init__.py
 M section4/src/harness_orchestrator/api.py
 M section4/src/harness_orchestrator/authz.py
 M section4/src/harness_orchestrator/collab.py
 M section4/src/harness_orchestrator/confine.py
 M section4/src/harness_orchestrator/engine.py
 M section4/src/harness_orchestrator/service.py
 M section4/src/harness_orchestrator/signals.py
 M section4/src/harness_orchestrator/staging.py
 M section4/tests/test_done_signal.py
 M section4/tests/test_stateless_orchestrator_n1.py
 M section4/tests/unit/standin_worker.py
 M section4/tests/unit/test_budget.py
 M section4/tests/unit/test_collab_backend.py
 M section4/tests/unit/test_done_diff_reference.py
 M section4/tests/unit/test_engine.py
 M section4/tests/unit/test_isolation_and_staging.py
 M section4/tests/unit/test_run_isolation_and_stop_truth.py
 M section4/tests/unit/test_s1_r05_lifecycle_audit.py
 M section4/tests/unit/test_staging_e2e.py
 M section5/src/harness_sandbox/api.py
 M section5/tests/unit/test_checkpoints.py
 M section7/app/dashboard/planGate.tsx
 M section7/app/dashboard/planGateApi.test.ts
 M section7/app/dashboard/planGateApi.ts
 M section7/app/login/page.tsx
 M section7/app/page.tsx
 M section7/app/projects/[projectId]/runs/[runId]/collab.test.tsx
 M section7/app/projects/[projectId]/runs/[runId]/collabApi.ts
 M section7/app/projects/[projectId]/runs/[runId]/collabModel.ts
 M section7/app/projects/[projectId]/runs/[runId]/page.tsx
 M section7/app/projects/[projectId]/runs/[runId]/sharedRunPanel.tsx
 M section7/e2e/support.ts
 M section7/playwright.config.ts
 M section7/src/components/Controls.tsx
 M section7/src/components/StatusPanel.tsx
 M section7/src/components/WorkingArea.tsx
?? deploy/pilot/
?? docs/agent-program/
?? docs/briefs/claude-code-p0-p1-handoff.md
?? docs/briefs/codex-delegation-prompt-2.md
?? docs/briefs/codex-delegation-prompt.md
?? docs/briefs/collab-denial-audit-matrix.md
?? docs/briefs/evidence/
?? docs/briefs/multiplayer-pilot-release-plan.md
?? docs/briefs/multiplayer-pilot-roadmap.md
?? docs/briefs/p0-container-isolation.md
?? docs/briefs/p1-shared-ui-evidence.md
?? docs/briefs/p2-hardening-evidence.md
?? docs/briefs/p3-contracts-data-release-evidence.md
?? docs/briefs/pilot-baseline-guide.md
?? docs/briefs/pilot-baseline-template.json
?? docs/briefs/pilot-prep-codex-report.md
?? docs/briefs/pilot-prep-codex-round2-report.md
?? docs/briefs/pilot-prep-evidence.md
?? docs/briefs/pilot-supervisor-checklist.md
?? docs/briefs/pilot-user-guide.md
?? docs/briefs/security-review-collab.md
?? docs/briefs/smtp-otp-tests.md
?? docs/briefs/smtp-tls-policy-options.md
?? docs/master-prompts/
?? docs/reviews/
?? integrations/
?? packages/
?? scripts/agent-program/
?? scripts/spec_drift.py
?? section1/tests/test_returning_login.py
?? section1/tests/test_smtp_otp_transport.py
?? section2/tests/__init__.py
?? section2/tests/test_staged_pr_gate.py
?? section4/src/harness_orchestrator/container_runtime.py
?? section4/src/harness_orchestrator/model_relay.py
?? section4/src/harness_orchestrator/pilot_metrics.py
?? section4/src/harness_orchestrator/retention.py
?? section4/tests/fixtures/openrouter.patch.yml
?? section4/tests/integration/
?? section4/tests/unit/test_collab_chaos_live_pg.py
?? section4/tests/unit/test_collab_denial_audit.py
?? section4/tests/unit/test_container_boundary.py
?? section4/tests/unit/test_pilot_metrics.py
?? section4/tests/unit/test_retention.py
?? section4/tests/unit/test_spec_drift.py
?? section4/tests/unit/test_staging_digest_binding.py
?? section4/worker.Dockerfile
?? section7/e2e/multiplayer.e2e.ts
?? section7/e2e/real.e2e.ts
```
