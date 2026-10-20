# Prompt for Codex (GPT), round 2: independent pilot-prep tasks

Copy everything below the line into Codex. Repo root: `/Users/user/untitled folder 2/harness` (a git repo).

---

## Role and context

You are an engineer on the "multiplayer pilot" effort (several engineers share one agent run: leader, handoff, redirect guidance, digest-bound approvals). Round 1 is done and reviewed: your security review, SMTP tests, Sections 1/2/5 evidence, baseline guide, user guide and supervisor checklist exist. Since then Claude fixed several things in the same checkout, and is still working there right now.

Read first: `AGENTS.md`, `docs/briefs/codex-delegation-prompt.md` (its **Hard rules** and **Environment notes** apply here unchanged: no commit/branch/stash/push/PR, never touch or print the OpenRouter key, free models only, no isolation weakening, honest reporting, ruff + `git diff --check`), `docs/briefs/multiplayer-pilot-release-plan.md`, `docs/briefs/security-review-collab.md`.

## What changed since round 1 (verify against the code, do not trust this list blindly)

1. **Staged-approval digest** now binds the full bytes of every staged file (`staging._groups`, `staging._fingerprint`, `collab.proposal_digest(..., content=)`). `apply_group` reads each withheld file once and checks and lands that same snapshot.
2. **SMTP failure log** (`notifier._log_smtp_failure`) now logs only the exception type and SMTP status. The old xfail test is a normal passing test.
3. **Workspaces**: `engine.provision_workspace` creates the run workspace on first start and, when `HARNESS_WORKSPACE_SEED=/path` is set, copies that directory into it (`.git` skipped). Not set = empty workspace.
4. **Rollback wording** in the release plan: `HARNESS_PILOT_TEAMS` gates only the 15 collab routes, must be non-empty, and does not stop a team from starting runs.

## Boundaries: do NOT edit (Claude is verifying these right now)

- `section4/src/harness_orchestrator/{service,engine,engine_runtime,engine_worker,staging,api,collab,retention}.py`
- `section4/tests/unit/test_staging_digest_binding.py`, `section4/tests/unit/test_engine.py`
- `section1/src/harness_identity/notifier.py`
- `deploy/pilot/*`, `scripts/spec_drift.py`, `scripts/sdk-gen/*`, `*/openapi.json`, `clients/*`, `section7/e2e/*`
- `docs/briefs/multiplayer-pilot-release-plan.md`, `docs/briefs/security-review-collab.md`, `docs/briefs/pilot-prep-evidence.md` (read them; report discrepancies instead of editing)

Scratch Postgres: use port **5815** and container `harness-codex-pg` only (as in round 1). Claude uses other ports; do not touch other containers or ports 8101/8104/8105/3107. Remove your container when done.

## Tasks (independent; do in this order; one section in your report each)

### Task F: Bring the three round-1 documents up to date (docs only)
**Role:** technical writer + fact-checker.
**Files you may edit:** `docs/briefs/pilot-user-guide.md`, `docs/briefs/pilot-supervisor-checklist.md`, `docs/briefs/pilot-baseline-guide.md`.
**Do:** re-verify every claim in each appendix against the code *as it is now* and fix what changed. At minimum:
- supervisor checklist: rollback step 1 must say what the release plan now says (the allowlist is not an admission stop and must stay non-empty); add `HARNESS_WORKSPACE_SEED` to the daily/start-of-pilot checks (what it is, that the operator refreshes it, that existing runs keep their own copy); note that approvals of staged changes now bind full file bytes.
- user guide: nothing may promise a repo checkout from GitHub (there is none); say plainly that the workspace comes from the operator's prepared directory, or is empty.
- baseline guide: re-check the schema against `pilot_metrics.py`.
**Done when:** each appendix lists claim, file:line checked, and result; any claim you could not confirm is removed or marked unverified. Also list every place where the *release plan* (which you may not edit) disagrees with the code or with your corrected docs.

### Task G: Audit-completeness tests for denied collaboration actions (tests only)
**Role:** security test engineer.
**Why:** your review noted not every denied branch writes an audit row (e.g. `NOT_PARTICIPANT`, `NOT_REQUESTER`). We need that pinned by tests before deciding what to fix.
**Do:** read `collab.py`, `api.py`, `authz.py` and the existing audit tests (`section4/tests/unit/test_collab_backend.py`, `test_s1_r05_lifecycle_audit.py`). In a NEW file `section4/tests/unit/test_collab_denial_audit.py`, add one test per denied branch across the 15 collab routes: cross-team, viewer writes, non-participant, non-requester, non-leader, self-approval, expired/stale approval, wrong-recipient handoff. Each test asserts the HTTP status/code **and** whether an audit row was written. Where an audit row is missing today, mark the test `@pytest.mark.xfail(strict=True, raises=AssertionError, reason="...")` so the gap is recorded and a future fix flips it. Do **not** change production code.
**Verify:** run the new file and the existing `section4/tests/unit/test_collab_*` files in `section4/.venv` (with `HARNESS_TEST_PG` pointed at your scratch DB if those tests use it). Report passed/xfailed counts.
**Deliverable:** the test file plus `docs/briefs/collab-denial-audit-matrix.md`: a table of route × denial × audit written (yes/no) × test name.

### Task H: Metrics date window (code + tests, this module only)
**Role:** backend engineer.
**Why:** `pilot_metrics.py` has no date filter, so weekly reports cannot be separated from earlier data, and your review noted acceptance is derived from audit rows.
**Files you may edit:** `section4/src/harness_orchestrator/pilot_metrics.py`, `section4/tests/unit/test_pilot_metrics.py` only.
**Do:** add optional `--since YYYY-MM-DD` and `--until YYYY-MM-DD` (UTC; `since` inclusive, `until` exclusive; invalid input exits with a clear error; both omitted = today's behaviour exactly). Apply the window to runs by their creation time. Document in the report exactly what "accepted" counts and from which table/column, because pilot users will ask. Add tests for: no window = unchanged output, a window that excludes older runs, bad dates, `since > until`.
**Constraints:** do not change the existing output keys or the `--baseline` behaviour. Keep it small. Update the module docstring only.
**Verify:** `section4/.venv/bin/python -m pytest section4/tests/unit/test_pilot_metrics.py -q`, then ruff.

### Task I: SMTP TLS options memo (docs only, no code)
**Role:** security engineer writing a decision memo.
**Why:** `notifier.py` falls back to plaintext when STARTTLS is refused (the sign-in code and SMTP credentials would travel unencrypted). The product owner must choose a policy.
**Do:** read `notifier.py`, `config.py` and the tests in `section1/tests/test_smtp_otp_transport.py`. Write `docs/briefs/smtp-tls-policy-options.md`: current behaviour with file:line, risk in plain language, 3 concrete options (for example: refuse by default with an explicit dev opt-out; refuse only in production mode; keep plus loud warning), for each the exact config variable, behaviour on refusal, test changes needed and migration risk, and a recommendation for a supervised pilot with real inboxes. Do not change code or tests.

## Reporting format

Per task: status (done / partial / blocked), files created or changed, exact commands run with exit codes and counts, what you could not verify, one-line notes on anything noticed outside scope. End with `git status --short`. State which results are separately-run suites; never call them one combined green run. Do not commit.
