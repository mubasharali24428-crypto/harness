# Cloud continuation prompt: Harness path-to-9 (paste as the first message of the cloud session)

You are continuing an unattended build in the repo `mubasharali24428-crypto/harness`, branch `master`. The branch
`cloud/path-to-9-finish` already exists on origin (head `bd48c41`); start the session from it and commit on it. Do not push to `master`, do not open or merge a PR, and do not enable auto-merge. The owner is away; if you hit a
decision that is theirs, write it down in your final report and carry on with the rest.

## Goal
Bring the 61-part "Harness Multiplayer AI Full Product Master Build Prompt" (`docs/master-prompts/harness-multiplayer-master-build-prompt.md`) to 9/10 per part.
Bar for 9: directives implemented or explicitly out of scope; invariant tests; an evidence file under `docs/briefs/evidence/s11/`; docs that match behaviour
including limits; a `verified` row in `docs/briefs/capability-matrix.md`. Parts gated on outside parties stay capped at 8: 03, 37, 53, 52/58, 61. The part 57
cap is removed (owner: no Stripe for now). Never claim a check you did not run; label anything unrun "not run" with the reason.

## Step 0: find out what you actually have (the cloud checkout only contains pushed commits)
1. `git log --oneline -8`. Expect the newest to be `84703cb Part 14: collaboration contract page...` then `8ead8cd Part 29...`, `2fbf36f`, `0a1cc33`. If it is older, work from what is there and say so.
2. `ls docs/briefs/evidence/s11/`. `part14.md`, `part29.md`, `process-parts.md` (marked DRAFT), `state-history.md` and `ui-plain-language.md` should exist on `bd48c41`.
   The section7 plain-language UI change (task 1) is already committed there too; only verify it (task 1 check) and skip rewriting it.
3. Read `docs/briefs/PROJECT-STATE.md`, `docs/briefs/capability-matrix.md`, `docs/briefs/path-to-9-checkpoint.md`, `docs/collaboration-contract.md`. Do not re-derive; trust the matrix.

State at the time of writing: 35 of 61 rows verified, 14 partial, 12 implemented, 2 blocked (before the process rows below).

## Tasks, in order (each one ends with a named check and an explicit-path commit)
1. **UI plain language (parts 24, 12): ALREADY COMMITTED in `bd48c41`; just re-run its check.** In `section7`:
   - `app/projects/[projectId]/runs/[runId]/collabModel.ts`: add `PLAIN_ERRORS` and `plainError(code, fallback)` mapping the collab error codes in `docs/collaboration-contract.md` section 9 to plain sentences; use it twice in `sharedRunPanel.tsx`; new test `plainErrors.test.ts` beside it.
   - `app/runs/runsApi.ts`: add `STATE_LABELS` and `stateLabel(state)` (humanises unknown states, never throws); `runDetailAggregate.tsx` pill shows `stateLabel(agg.state)` with the raw value in `title`.
   - new `tests/unit/stateLabel.test.ts` reads the `AgentState` class from `section4/src/harness_orchestrator/state_machine.py` and asserts every state has a label.
   - UPDATE `tests/unit/runDetailPage.test.tsx`: the pill now reads `Pull request opened` (raw `pr_opened` in `title`), and "no PR card" assertions must test `aria-label="Pull request"`, not the text "Pull request" (the pill label now contains it). This was the only vitest breakage.
   - Check: `cd section7 && npx tsc --noEmit` clean and `npx vitest run` = 70 files, 824 tests passing (this was true locally).
2. **Diagnose the one section4 failure on real PostgreSQL**: `section4/tests/unit/test_session6_live_pg.py::test_session6_postgres_races_artifacts_search_export_and_retry`
   fails at line ~149 with `KeyError: 'items'` (`before["items"]` from the search call). Decide: real bug in search on PostgreSQL, or an environment difference
   (the local scratch PG had no `pgvector`; migration 0030 degrades gracefully). Fix it if it is a bug, with a test; if it is the environment, record that in the evidence.
   Needs PostgreSQL: install one (`apt`/package manager) or use the repo's `docker compose` if Docker exists in the cloud box; otherwise say "not run".
   Env used locally: `HARNESS_TEST_PG=1`, `HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:<port>/harness`, `HARNESS_TEST_PG_DSN=postgresql://harness:harness@localhost:<port>/harness`, `LC_ALL=en_US.UTF-8`.
   Results locally on PG 18.4: section1 978 passed, 1 skipped, 4 failures that need `docker exec harness-db-1` (not runnable without Docker); section4 1540 passed, 19 skipped, 1 failed (this one); `migrate_selftest` PASS on 55 migrations.
3. **Evidence for the UI work (already written; matrix row 24 is verified, row 12 note updated, so only re-check)**: `docs/briefs/evidence/s11/ui-plain-language.md` (directive map for parts 24/12: what changed, tests, honest limits: no real browser or screen-reader pass was done) and set matrix row 24 to verified, row 12 note updated.
4. **Process parts 01, 02, 15, 16, 17, 18, 19, 34, 35**:
   - `docs/briefs/evidence/s11/process-parts.md` (exists as a DRAFT with a status banner; remove the banner once every artifact below exists): a directive-to-artifact table per part (each artifact must exist; open the file to check), with an "Honest limits" section (16/17/18.005-18.009/19.005-19.009 are practice, not testable; the pilot smoke `deploy/pilot/run_smoke.py` has not run on this tree).
   - `docs/briefs/evidence/s11/state-history.md` already holds a copy of the old PROJECT-STATE sections 3, 3b to 3f and 4; the trimmed PROJECT-STATE must drop them.
   - `docs/briefs/final-deliverables.md`: index for parts 34/35 (executive summary, feature matrix, evidence references, session outcomes, acceptance results labelled pass/fail/not-verified, risk register, pilot setup, feedback and metric plan, next steps, readiness decision **NO-GO for broad rollout**; operator-present allow-listed rehearsal only after listed pre-flight checks) plus the part 35 response template.
   - rewrite `PROJECT-STATE.md` to at most ~130 lines: Sessions 1 to 10 built, Codex did 6 to 10 and Claude verified them, 55 migrations, current open work, facts that bite, "Not cleared", how to resume; link to `state-history.md`. Remove stale text ("end of Session 7", "expect 43 migrations", "Next: Session 9").
   - add the 19.010 line to `docs/briefs/stable-prompt-prefix.md` (no cache controls are used).
   - new `section4/tests/unit/test_program_docs.py`: markdown links in the briefs resolve; PROJECT-STATE <= 130 lines; the matrix has 61 rows; every `verified` row cites an evidence path that exists.
   - set matrix rows 01, 02, 15 to 19, 34, 35 to verified, each citing `process-parts.md` or `final-deliverables.md`.
5. **Gates**, one at a time: `section4` pytest (SQLite), `section1` pytest, `ruff check` on the files you added (never `ruff --fix` broadly; `--select I001 --fix` on a single new file is fine), `python scripts/spec_drift.py`, `section7` tsc + vitest. If you change any route, regenerate: `section4/.venv/bin/python scripts/spec_drift.py --write`, then `git checkout -- section5/openapi.json`, then `scripts/sdk-gen/generate.py` and `... --target ts`, then re-verify with `scripts/spec_drift.py`.
6. **Remaining partial rows (only if time is left, in this order)**: 31, 49, 51, 54, 55, 56, 57, then 12 (real-browser responsive/keyboard check). Rows 25, 26, 28, 32, 53, 59 need Docker; 52, 58, 03, 61, 37 are externally gated. For each: audit directive by directive, add a missing invariant test, write an evidence file, flip to verified only with evidence.
7. **Final report** (put it in `docs/briefs/evidence/s11/cloud-report.md` and in your last message): what you verified, what you fixed, matrix count before/after, every check NOT run and why, what stays externally blocked.

## Rules
- Commit per task with explicit `git add <owned paths>` then a plain `git commit`. End each commit message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. No `git add -A`, no `git commit -a`, no force push, no push to `master`.
- Keep changes surgical; every non-trivial rule leaves one runnable test. Name the verification step before saying a task is done.
- Raw SQL via `sqlalchemy.text` in section4. SQLite test traps: "database is locked"; `db.rollback()` discards shared work; await engine tasks before `engine.dispose()`.
- Approval state machine (do not change): pending -> approved/rejected/expired/invalidated; approved -> executing -> completed/failed; amend returns pending/approved to pending; all transitions are compare-and-swap UPDATEs.
- Known open item, document only: T13b (OTP redeem race in `section1/src/harness_identity/auth.py` `verify_otp`). Needs a 6-line guarded `UPDATE ... WHERE consumed_at IS NULL` and `attempts = attempts + 1 ... RETURNING`; the owner has uncommitted SMTP edits in that file locally, so leave it unless your checkout shows the file clean, in which case you may do it with a test.
- These paths hold the owner's other work and must not be created, edited or committed by you: `docs/agent-program/`, `scripts/agent-program/`, `AGENTS.md`, `README.md`, `.dockerignore`, `.gitignore`, `section2/*`, section1 `auth.py`/`config.py`/`notifier.py` and their notification/SMTP/roster/slim-bus/returning-login tests, section4 `confine.py`/`logging_utils.py`/`signals.py`/`__init__.py`/`container_runtime.py`/`model_relay.py`/`pilot_metrics.py` and the collab/isolation/staging/engine tests, `section5/tests/unit/test_checkpoints.py`, `section7/e2e/real.e2e.ts`, `docs/briefs/codex-*`, `docs/briefs/smtp-*`, `docs/briefs/multiplayer-*`, and the p0 to p3 / pilot-prep / security evidence.
- Do not touch any Postgres on port 5432 of a shared machine; start your own on another port.
