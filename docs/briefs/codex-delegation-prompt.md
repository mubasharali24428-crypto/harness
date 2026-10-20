# Prompt for Codex (GPT): independent pilot-prep tasks

Copy everything below the line into Codex. Repo root: `/Users/user/untitled folder 2/harness` (a git repo).

---

## Role and context

You are an engineer on the "multiplayer pilot" effort for an AI engineering harness: several engineers share one agent run, with a leader, handoff, redirect guidance, and digest-bound approvals for high-impact changes. Phases P0–P3 are implemented and tested. A supervised 2-week pilot with 1–2 real teams comes next. Another engineer (Claude) is working in the same checkout at the same time on workspace provisioning, so you must stay inside the boundaries below.

Read first, in this order: `AGENTS.md`, `docs/briefs/multiplayer-pilot-release-plan.md`, `docs/briefs/pilot-prep-evidence.md`, `docs/briefs/p3-contracts-data-release-evidence.md`.

## Hard rules (a violation means stop and report)

1. **Do not commit, push, merge, branch, stash or open PRs.** Leave all changes uncommitted in the working tree. Do not run `git checkout`, `git reset` or `git clean`. Two old GLM-S2 git stashes exist; leave them alone.
2. **Credentials.** `section3/.env.pilot-openrouter` holds an API key (mode 0600, gitignored). Never print, copy, log, or put its contents in any file, prompt, screenshot or test output. Tests may read it only through the env var `HARNESS_OPENROUTER_KEY_FILE=<path>`. You do not need it for the tasks below.
3. **Models.** Free or explicitly low-cost only. The tested model is `liquid/lfm-2.5-2.6b:free`. Never switch to a paid model.
4. **Do not weaken container isolation.** Do not change frozen contracts (`contracts/`) without a version bump plus matching mocks. None of your tasks should need that.
5. **Boundaries. Do NOT edit any of these** (Claude is working there):
   - `section4/src/harness_orchestrator/{service,engine,engine_runtime,engine_worker,staging,api,collab,retention,pilot_metrics}.py`
   - `deploy/pilot/*`, `scripts/spec_drift.py`, `scripts/sdk-gen/*`, `*/openapi.json`, `clients/*`
   - `section7/app/projects/**/runs/**` and `section7/e2e/*`
   Read them freely. If you think one needs changing, write it up as a finding instead.
6. **Surgical changes.** Touch only what each task needs. No reformatting or renaming of code you were not asked to change. Report unrelated bugs in one line; do not fix them.
7. **Honesty.** Report exactly what you ran and what it returned. If a test fails, show the output. Do not call separately run suites "all green together". Do not write "should work": run the check and name it.
8. **Isolation guard.** `section7/scripts/ci_route_module_guard.sh isolation` forbids a section's code or tests from referencing sibling-section paths. Keep each section self-contained.
9. Do not delete or overwrite pre-existing untracked files.

## Environment notes

- Python venvs: `section1/.venv`, `section2/.venv`, `section4/.venv`, `section5/.venv` (use the matching one per section: `section1/.venv/bin/python -m pytest`).
- Optional scratch Postgres for DB tests: `docker run -d --name harness-codex-pg -p 127.0.0.1:5815:5432 -e POSTGRES_USER=pilot -e POSTGRES_PASSWORD=pilot -e POSTGRES_DB=pilot postgres:18`, then remove it when done (`docker rm -f -v harness-codex-pg`). Do not touch the container `harness-p1-pg` or ports 8101/8104/8105/3107.
- Section 1 DB tests need `HARNESS_TEST_PG="postgresql://pilot:pilot@127.0.0.1:5815/pilot"` (plain DSN), `HARNESS_REQUIRE_OWNERSHIP=true`, `HARNESS_DATABASE_URL="postgresql+asyncpg://pilot:pilot@127.0.0.1:5815/pilot"`.
- Lint: `ruff check` in the section you changed. `git diff --check` must be clean.

## Tasks (independent of each other; do them in this order; one report file each)

### Task A: Independent security review of the collaboration code (read-only)
**Role:** security reviewer. You did not write this code; try to break it.
**Scope (read only):** `section4/src/harness_orchestrator/{collab,api,staging,retention,service}.py`, `section1/migrations/0035_run_collaboration.sql`, and the 15 `collab_*` routes in `api.py`.
**Look for:** authorization bypass across teams or roles (viewer acting as engineer, non-leader doing leader actions, cross-team run ids); approval replay or digest mismatch; time-of-check/time-of-use gaps in approve → claim → complete; the `HARNESS_PILOT_TEAMS` gate missing on any route; injection through the `<team_guidance>` block; path traversal or symlink escape in staging and retention (`_rm`, `_SAFE_ID`); SQL built with string formatting; unbounded input or missing size caps; audit rows missing for denied or sensitive actions; information leaks in error messages.
**Method:** for each claimed issue, write a failing test or a minimal reproduction under `/tmp` or in the new test file below. Mark each finding CONFIRMED (you reproduced it) or SUSPECTED (reasoned only). Do not fix anything.
**Known and already documented, do not re-report:** a leader can mark a staged approval completed without applying it; no rate limiting beyond size caps; guidance reaches the agent as prompt input by design; no presence indicator.
**Deliverable:** `docs/briefs/security-review-collab.md` (findings ranked by severity, each with file:line, scenario, evidence, suggested fix). Reproduction tests, if any, go in `docs/briefs/evidence/security/` as standalone scripts, not in the repo's test directories.
**Done when:** every route and every rule in the scope list has been examined and the report says so explicitly, including areas where you found nothing.

### Task B: SMTP OTP delivery tests (Section 1 only)
**Role:** backend test engineer.
**Why:** sign-in is only proven through the dev log printer. `section1/src/harness_identity/notifier.py` has an SMTP path (stdlib `smtplib` in a worker thread) with no real-transport test.
**Do:** read `notifier.py` and the config that selects SMTP versus the dev printer. Add tests in `section1/tests/` that start a local in-process fake SMTP server (stdlib `smtpd`-style or `aiosmtpd` only if already installed; do NOT add dependencies) and assert: the OTP email is delivered to the right recipient with the code in the body; SMTP failure falls back or errors exactly as the code documents and never logs the code or credentials; the dev printer is used only when SMTP is unconfigured and says so visibly. Also assert that SMTP credentials never appear in logs.
**Constraints:** tests only. If you find a bug in `notifier.py`, report it with a failing test marked `xfail(strict=True)` and the reason; do not change production code.
**Verify:** run `section1/.venv/bin/python -m pytest section1/tests -q` (with the Postgres env above) and report the exact pass/skip counts. Baseline before your change was 650 passed.
**Deliverable:** the new test file(s) plus `docs/briefs/smtp-otp-tests.md` (what is covered, what is not, command, counts).

### Task C: Fresh verification of Sections 1, 2 and 5
**Role:** QA runner.
**Why:** those suites were last run before the latest changes elsewhere. Produce current evidence.
**Do:** run each section's full suite in its own venv with the relevant opt-in env vars (Section 1 with Postgres as above; Section 5 per its README or `AGENTS.md`). Save junit XML to `docs/briefs/evidence/pilot-prep/section{1,2,5}.junit.xml` (use `--junit-xml`). Report pass/fail/skip counts per section and the reason for every skip.
**Constraints:** no code changes. Any failure: capture the full output, check whether it reproduces on a second run, and report; do not "fix" tests.
**Deliverable:** `docs/briefs/evidence/pilot-prep/sections-1-2-5-verification.md` with a table (section, command, passed/failed/skipped, skip reasons, date) and a note for each failure.

### Task D: Baseline measurement kit (docs only)
**Role:** technical writer / research-methods helper.
**Why:** the pilot needs a human-measured baseline: at least 5 comparable tasks per side, done without the harness. The tooling is `python -m harness_orchestrator.pilot_metrics <team> --baseline FILE` with the template `docs/briefs/pilot-baseline-template.json`. Read `section4/src/harness_orchestrator/pilot_metrics.py` to get the exact schema and the meaning of "claimable".
**Do:** write a facilitator guide a non-engineer can follow: how to choose comparable tasks, what to time (start/stop definitions), what "accepted" means, how to record each task, how to fill the JSON, common mistakes (cherry-picking, rework counted inconsistently, mixing task sizes), and why the result is labelled descriptive, not a controlled comparison. Add a one-page printable recording sheet and a filled **example** clearly marked FICTIONAL that validates against the template.
**Constraints:** docs and the example JSON only; do not change `pilot-baseline-template.json` or the metrics code. Validate your example by running the metrics CLI's baseline loader or a small script and quote the output.
**Deliverable:** `docs/briefs/pilot-baseline-guide.md` and `docs/briefs/evidence/pilot-prep/baseline-example-FICTIONAL.json`.

### Task E: Pilot user and supervisor documents (docs only)
**Role:** technical writer.
**Do:** two short documents based only on facts in the release plan and the code (verify every claim by reading the code; do not invent features):
1. `docs/briefs/pilot-user-guide.md`: for pilot engineers. What the shared run is, roles (leader, engineer, viewer), how to join, send direction, hand off, review and approve a staged change, stop a run; what the "not enabled for your team" notice means; the known limits stated plainly; how to report a problem.
2. `docs/briefs/pilot-supervisor-checklist.md`: for the human supervisor. Daily checks (audit log events to review, stuck runs, `stopping` without `run.cancel_confirmed`, disk growth, retention dry-run then apply), weekly metrics command, escalation and rollback steps copied accurately from the release plan, and a go/no-go list for ending or pausing the pilot.
**Verify:** list each factual claim with the file you checked it against, in an appendix to each document.

## Reporting format (end of your work)

For each task: status (done / partial / blocked), files created or changed (paths), exact commands run with results, what you could not verify, and anything you noticed outside scope (one line each). Finish with `git status --short` output so the working-tree changes can be reviewed. Do not commit.
