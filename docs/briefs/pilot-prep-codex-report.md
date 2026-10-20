# Independent pilot preparation — completion report

2026-10-18, America/Los_Angeles. All work left uncommitted. No production code, frozen contracts, protected paths or pre-existing untracked files were edited by this task. No branch/stash/commit/push/PR operations occurred. No credential file was read; no model was called. The shared checkout contains substantial earlier/concurrent work, shown in the final status snapshot; those changes are not attributed to this task.

## A — done: independent security review

Created `docs/briefs/security-review-collab.md` and `docs/briefs/evidence/security/repro_staging_digest.py`.

Command: `section4/.venv/bin/python docs/briefs/evidence/security/repro_staging_digest.py` → exit 0; CONFIRMED binary and truncated_text cases both retain a digest after staged byte replacement and apply the replacement. High-severity finding, conditioned on access to withheld content; no worker escape is claimed. All 15 routes and requested review categories examined, with negative results and limits explicitly recorded. Not verified: live authorization attacks, concurrency/retention races, crash recovery, or full penetration testing. No fixes made.

## B — done: SMTP tests

Created `section1/tests/test_smtp_otp_transport.py`, `docs/briefs/smtp-otp-tests.md`, `docs/briefs/evidence/pilot-prep/smtp-targeted.log`, and `smtp-final.log` in that evidence directory.

`section1/.venv/bin/python -m pytest section1/tests/test_smtp_otp_transport.py -q -rx` initially hit sandbox socket denial (1 passed, 1 xfailed, 3 errors). With host access, the same command exposed a test expectation casing mistake (1 failed, 3 passed, 1 xfailed); full output is in smtp-targeted.log. Corrected only the new test. Restricted xfail to AssertionError to prevent hiding transport/setup errors.

`section1/.venv/bin/python -m pytest section1/tests/test_smtp_otp_transport.py -q -rx --tb=short` → exit 0, 4 passed, 1 xfailed in 2.06s. Expected failure confirms raw SMTP error text can leak relay-echoed synthetic code/credentials. No real credentials used. Full Section 1 verification is the run listed under C (also fulfills B; not a second independent run). Not verified: real inbox/provider credentials, successful TLS, browser OTP flow.

`section1/.venv/bin/ruff check section1/src section1/tests section1/scripts` → initial E501 at the new decorator; wrapped it; rerun exit 0, All checks passed.

## C — done: separate full-suite evidence

Created `docs/briefs/evidence/pilot-prep/sections-1-2-5-verification.md`, `section1.junit.xml`, `section2.junit.xml`, `section5.junit.xml`, and full `section1.log`, `section2.log`, `section5.log` alongside them.

```
HARNESS_TEST_PG=postgresql://pilot:pilot@127.0.0.1:5815/pilot HARNESS_REQUIRE_OWNERSHIP=true HARNESS_DATABASE_URL=postgresql+asyncpg://pilot:pilot@127.0.0.1:5815/pilot section1/.venv/bin/python -m pytest section1/tests -q -ra --junit-xml=docs/briefs/evidence/pilot-prep/section1.junit.xml
section2/.venv/bin/python -m pytest section2/tests -q -ra --junit-xml=docs/briefs/evidence/pilot-prep/section2.junit.xml
section5/.venv/bin/python -m pytest section5/tests -q -ra --junit-xml=docs/briefs/evidence/pilot-prep/section5.junit.xml
```

All exit 0, separately: Section 1 **654 passed, 0 skipped, 1 xfailed**, 72 warnings, 57.84s; Section 2 **39 passed, 0 skipped**, 2 warnings, 0.97s; Section 5 **182 passed, 1 skipped**, 1 warning, 5.96s. Skip: Landlock is Linux-only, host macOS. Expected failure is the SMTP defect, not an ordinary skip. No full-suite failures required a reproduction rerun. Section 1/5 needed host socket/confinement permissions. No extra Section 2/5 opt-ins found. Linux confinement not verified.

Scratch database lifecycle (both exit 0):

```
docker run -d --name harness-codex-pg -p 127.0.0.1:5815:5432 -e POSTGRES_USER=pilot -e POSTGRES_PASSWORD=pilot -e POSTGRES_DB=pilot postgres:18
docker rm -f -v harness-codex-pg
```

Initial sandboxed Docker inventory failed with socket permission denied; authorized host execution succeeded. Only the newly created scratch container was removed. Existing pilot containers/ports were not touched.

## D — done: baseline measurement kit

Created `docs/briefs/pilot-baseline-guide.md`, `docs/briefs/evidence/pilot-prep/baseline-example-FICTIONAL.json`, and `baseline-validation.log` alongside it. Guide includes a printable one-task sheet, selection/timing/acceptance rules, exact fields and descriptive-comparison caveats. Template and metrics code unchanged.

`section4/.venv/bin/python /tmp/harness-codex-validate-baseline.py` → exit 0: template-compatible fields/types, dates, unique IDs and five valid durations; actual compare() reports claimable false at four pilot durations and true/descriptive-only at five. Full output quoted in the guide/log. The temporary validator first ran from the evidence directory, then was moved to /tmp so deliverables stay docs/JSON only. No real observations or database-backed metric collection claimed.

## E — done: engineer/supervisor guides

Created `docs/briefs/pilot-user-guide.md` and `docs/briefs/pilot-supervisor-checklist.md`; each has a claim/source appendix. Verified UI labels, server role/leader/gate logic, approval/apply behavior, stop confirmation, retention and metrics directly against source. Re-read the release plan and the newly added workspace provisioning source after concurrent edits; documents reflect that source snapshot without claiming a runtime retest. No deployment, retention apply, rollback or user messages were sent.

Outside-scope observations (unfixed):

- SMTP STARTTLS refusal currently permits plaintext fallback; real-provider/TLS deployment review remains necessary.
- Release-plan rollback needs a nonempty allowlist and a separate operational admission stop: the collaboration gate does not gate general run start.
- Metrics acceptance counts audit rows, and reports have no date filter; manual baseline acceptance and weekly windows need explicit qualification.
- Source-only audit coverage inspection found not all denied branches append audit rows; no end-to-end audit-completeness claim is made.

## Final hygiene

`git diff --check` → exit 0, no output.

`bash section7/scripts/ci_route_module_guard.sh isolation` → exit 0: `GUARD[isolation] OK: 171 src file(s) across 10 section(s) — no cross-section tree/package/sys.path/star-reexport edges.`

The status below is the literal `git status --short` output from the shared checkout, not a list of files changed solely by this task.

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
?? docs/briefs/codex-delegation-prompt.md
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
?? docs/briefs/pilot-prep-evidence.md
?? docs/briefs/pilot-supervisor-checklist.md
?? docs/briefs/pilot-user-guide.md
?? docs/briefs/security-review-collab.md
?? docs/briefs/smtp-otp-tests.md
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
?? section4/tests/unit/test_container_boundary.py
?? section4/tests/unit/test_pilot_metrics.py
?? section4/tests/unit/test_retention.py
?? section4/tests/unit/test_spec_drift.py
?? section4/worker.Dockerfile
?? section7/e2e/multiplayer.e2e.ts
?? section7/e2e/real.e2e.ts
```
