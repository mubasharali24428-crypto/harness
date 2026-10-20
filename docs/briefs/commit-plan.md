# Commit plan (not executed)

Excluded on purpose (untracked, older than this work, not produced by it): `packages/` (5,104 files), `integrations/`, `docs/agent-program/`, `docs/master-prompts/`, `docs/reviews/`, `scripts/agent-program/`. Two GLM-S2 git stashes are left alone. Branch now: `master`; proposed: create `pilot-prep-p0-p3` first.

Caveat: P0-P3, the pilot-prep work and the later fixes are all uncommitted in the same files (e.g. `service.py`, `collab.py`, `staging.py` contain all of them). Splitting hunks inside a file is not safe to automate, so commits are grouped by file. Intermediate commits will not each pass every gate; only the final tree is verified (last full run: all suites exit 0, see `pilot-prep-evidence.md`). If per-feature history matters, the alternative is a single commit.

## C1  s4: container boundary for the engine worker (P0) (11 files)

- `.dockerignore`
- `section4/src/harness_orchestrator/confine.py`
- `docs/briefs/evidence/p0/container.junit.xml`
- `docs/briefs/evidence/p0/hello.txt`
- `docs/briefs/evidence/p0/regression.junit.xml`
- `docs/briefs/p0-container-isolation.md`
- `section4/src/harness_orchestrator/container_runtime.py`
- `section4/src/harness_orchestrator/model_relay.py`
- `section4/tests/integration/test_container_p0.py`
- `section4/tests/unit/test_container_boundary.py`
- `section4/worker.Dockerfile`

## C2  s4: shared runs - collab, staging gate, digest-bound approvals, workspace provisioning, retention, metrics, denial audit (P1-P3) (34 files)

- `section4/src/harness_orchestrator/__init__.py`
- `section4/src/harness_orchestrator/api.py`
- `section4/src/harness_orchestrator/authz.py`
- `section4/src/harness_orchestrator/collab.py`
- `section4/src/harness_orchestrator/engine.py`
- `section4/src/harness_orchestrator/service.py`
- `section4/src/harness_orchestrator/signals.py`
- `section4/src/harness_orchestrator/staging.py`
- `section4/tests/test_done_signal.py`
- `section4/tests/test_stateless_orchestrator_n1.py`
- `section4/tests/unit/standin_worker.py`
- `section4/tests/unit/test_budget.py`
- `section4/tests/unit/test_collab_backend.py`
- `section4/tests/unit/test_done_diff_reference.py`
- `section4/tests/unit/test_engine.py`
- `section4/tests/unit/test_isolation_and_staging.py`
- `section4/tests/unit/test_run_isolation_and_stop_truth.py`
- `section4/tests/unit/test_s1_r05_lifecycle_audit.py`
- `section4/tests/unit/test_staging_e2e.py`
- `docs/briefs/evidence/p2/section4-final.junit.xml`
- `docs/briefs/evidence/p3/pilot-metrics-synthetic-run.json`
- `docs/briefs/evidence/p3/section1-final.junit.xml`
- `docs/briefs/evidence/p3/section2-final.junit.xml`
- `docs/briefs/evidence/p3/section4-final.junit.xml`
- `docs/briefs/evidence/p3/section5-final.junit.xml`
- `section4/src/harness_orchestrator/pilot_metrics.py`
- `section4/src/harness_orchestrator/retention.py`
- `section4/tests/fixtures/openrouter.patch.yml`
- `section4/tests/unit/test_collab_chaos_live_pg.py`
- `section4/tests/unit/test_collab_denial_audit.py`
- `section4/tests/unit/test_pilot_metrics.py`
- `section4/tests/unit/test_retention.py`
- `section4/tests/unit/test_spec_drift.py`
- `section4/tests/unit/test_staging_digest_binding.py`

## C3  s1: returning-login / audit changes (3 files)

- `section1/src/harness_identity/audit.py`
- `section1/src/harness_identity/auth.py`
- `section1/tests/test_returning_login.py`

## C4  s1: SMTP TLS enforcement and SMTP tests (5 files)

- `section1/src/harness_identity/config.py`
- `section1/src/harness_identity/notifier.py`
- `section1/tests/test_notifications.py`
- `section1/tests/test_notifier.py`
- `section1/tests/test_smtp_otp_transport.py`

## C5  s2/s5: staged-PR gate, sandbox api (8 files)

- `section2/src/harness_github/app.py`
- `section2/src/harness_github/service.py`
- `section2/tests/conftest.py`
- `section2/tests/test_oauth_flow.py`
- `section5/src/harness_sandbox/api.py`
- `section5/tests/unit/test_checkpoints.py`
- `section2/tests/__init__.py`
- `section2/tests/test_staged_pr_gate.py`

## C6  s7: shared-run panel, login and plan-gate UI (13 files)

- `section7/app/dashboard/planGate.tsx`
- `section7/app/dashboard/planGateApi.test.ts`
- `section7/app/dashboard/planGateApi.ts`
- `section7/app/login/page.tsx`
- `section7/app/page.tsx`
- `section7/app/projects/[projectId]/runs/[runId]/collab.test.tsx`
- `section7/app/projects/[projectId]/runs/[runId]/collabApi.ts`
- `section7/app/projects/[projectId]/runs/[runId]/collabModel.ts`
- `section7/app/projects/[projectId]/runs/[runId]/page.tsx`
- `section7/app/projects/[projectId]/runs/[runId]/sharedRunPanel.tsx`
- `section7/src/components/Controls.tsx`
- `section7/src/components/StatusPanel.tsx`
- `section7/src/components/WorkingArea.tsx`

## C7  pilot smoke stack and browser proofs (deploy/pilot, e2e, playwright) (18 files)

- `section7/e2e/support.ts`
- `section7/playwright.config.ts`
- `deploy/pilot/run_smoke.py`
- `deploy/pilot/stack.py`
- `deploy/pilot/worker.py`
- `docs/briefs/evidence/p1/browser.junit.xml`
- `docs/briefs/evidence/p1/harness-p1-openrouter-free.xml`
- `docs/briefs/evidence/p1/harness-p1-pg.xml`
- `docs/briefs/evidence/p1/harness-p1-section2.xml`
- `docs/briefs/evidence/p1/live-result.json`
- `docs/briefs/evidence/p1/multiplayer-complete.png`
- `docs/briefs/evidence/p1/section4-final.junit.xml`
- `docs/briefs/evidence/pilot-prep/browser.junit.xml`
- `docs/briefs/evidence/pilot-prep/real-complete.png`
- `docs/briefs/evidence/pilot-prep/real-result.json`
- `docs/briefs/p1-shared-ui-evidence.md`
- `section7/e2e/multiplayer.e2e.ts`
- `section7/e2e/real.e2e.ts`

## C8  spec-drift gate, regenerated specs and SDKs (25 files)

- `Makefile`
- `clients/python/harness_sdk/github.py`
- `clients/python/harness_sdk/identity.py`
- `clients/python/harness_sdk/meeting.py`
- `clients/python/harness_sdk/orchestrator.py`
- `clients/python/harness_sdk/review.py`
- `clients/python/harness_sdk/router.py`
- `clients/python/harness_sdk/sandbox.py`
- `clients/ts/src/github.ts`
- `clients/ts/src/identity.ts`
- `clients/ts/src/meeting.ts`
- `clients/ts/src/orchestrator.ts`
- `clients/ts/src/review.ts`
- `clients/ts/src/router.ts`
- `clients/ts/src/sandbox.ts`
- `scripts/sdk-gen/specs/github.openapi.json`
- `scripts/sdk-gen/specs/identity.openapi.json`
- `scripts/sdk-gen/specs/meeting.openapi.json`
- `scripts/sdk-gen/specs/orchestrator.openapi.json`
- `scripts/sdk-gen/specs/review.openapi.json`
- `scripts/sdk-gen/specs/router.openapi.json`
- `scripts/sdk-gen/specs/sandbox.openapi.json`
- `section10/openapi.json`
- `section4/openapi.json`
- `scripts/spec_drift.py`

## C9  docs, runbooks, evidence, Codex prompts and reports (34 files)

- `.gitignore`
- `AGENTS.md`
- `docs/briefs/multiplayer-readiness.md`
- `docs/briefs/multiplayer-session1-checkpoint.md`
- `docs/briefs/claude-code-p0-p1-handoff.md`
- `docs/briefs/codex-delegation-prompt-2.md`
- `docs/briefs/codex-delegation-prompt.md`
- `docs/briefs/collab-denial-audit-matrix.md`
- `docs/briefs/evidence/pilot-prep/baseline-example-FICTIONAL.json`
- `docs/briefs/evidence/pilot-prep/baseline-validation.log`
- `docs/briefs/evidence/pilot-prep/section1.junit.xml`
- `docs/briefs/evidence/pilot-prep/section1.log`
- `docs/briefs/evidence/pilot-prep/section2.junit.xml`
- `docs/briefs/evidence/pilot-prep/section2.log`
- `docs/briefs/evidence/pilot-prep/section5.junit.xml`
- `docs/briefs/evidence/pilot-prep/section5.log`
- `docs/briefs/evidence/pilot-prep/sections-1-2-5-verification.md`
- `docs/briefs/evidence/pilot-prep/smtp-final.log`
- `docs/briefs/evidence/pilot-prep/smtp-targeted.log`
- `docs/briefs/evidence/security/repro_staging_digest.py`
- `docs/briefs/multiplayer-pilot-release-plan.md`
- `docs/briefs/multiplayer-pilot-roadmap.md`
- `docs/briefs/p2-hardening-evidence.md`
- `docs/briefs/p3-contracts-data-release-evidence.md`
- `docs/briefs/pilot-baseline-guide.md`
- `docs/briefs/pilot-baseline-template.json`
- `docs/briefs/pilot-prep-codex-report.md`
- `docs/briefs/pilot-prep-codex-round2-report.md`
- `docs/briefs/pilot-prep-evidence.md`
- `docs/briefs/pilot-supervisor-checklist.md`
- `docs/briefs/pilot-user-guide.md`
- `docs/briefs/security-review-collab.md`
- `docs/briefs/smtp-otp-tests.md`
- `docs/briefs/smtp-tls-policy-options.md`
