# Codex handoff — continue the Harness "path to 9/10" programme at Session 6

Paste everything below the line into Codex. Working directory: `/Users/user/untitled folder 2/harness` (quote the path; it has a space). Branch `master`. Nothing is pushed.

---

You are continuing a multi-session engineering programme on the Harness multiplayer AI product. Read before acting, in this order:

1. `docs/briefs/PROJECT-STATE.md` (state of the repo; section 6 "Facts that bite")
2. `docs/briefs/path-to-9-checkpoint.md` and `docs/briefs/path-to-9-plan.md`
3. `docs/briefs/stable-prompt-prefix.md`
4. `docs/briefs/path-to-9-session6-prompt.md` (the current lane contract)
5. `docs/briefs/evidence/s6/lane-pl.md` (the one finished lane's report)
6. `docs/master-prompts/harness-multiplayer-master-build-prompt.md` — read only the parts you are working on (44, 48, 50 now).

## Goal
Bring all 61 parts of the master prompt to 9/10. A part is 9/10 only when: every directive is implemented or explicitly out of scope with a reason; invariant tests exist that fail when the invariant breaks; an evidence file exists; docs describe the behaviour as built (including limits); and the capability-matrix row is `verified`. Externally gated parts are capped at 8: 03, 37/57, 53, 52/58, 61. Never claim a check you did not run.

## Where we are
- Done and committed locally: part 38 carry-over and Sessions 1–5 (HEAD before Session 6 prep is `60fa11e`; Session 6 prep is `87314fc`). 30 of 61 matrix rows are `verified`.
- **Session 6 (parts 44 plans/execution, 48 artifacts, 50 search/export/deletion) is in progress, uncommitted.** Four backend lanes were building in the working tree:
  - **PL** (44.001–003): `section1/migrations/0047_plans.sql`, `section4/src/harness_orchestrator/plans.py`, `tests/unit/test_plans*.py`, `docs/plans.md`. **Finished and verified**: 53 tests pass, ruff clean.
  - **EX** (44.004–010): `0048_execution.sql`, `execution.py`. Files exist; **report not yet received — treat as unverified**.
  - **AR** (48): `0049_artifacts.sql`, `artifacts.py`, `artifact_store.py`. **Unverified.**
  - **SE** (50): `0050_search_export.sql`, `search.py`, `exports.py`. **Unverified.**
  If any lane's tests or evidence file (`docs/briefs/evidence/s6/lane-*.md`) or doc is missing or failing, finish that lane per the contract before integrating. Check `git status` first so you know what is really on disk.

## Known gap you must resolve or document (decision pending)
No route approves a plan **version**. Approval is still the legacy `POST .../runs/{id}/plan/approve` on a `planId`, and the engine (`service.py`, `planner.py`) executes the in-memory legacy plan. So a natively created version, or one produced by an accepted edit, can never become `approved` or reach the engine. Either (a) add a version-approval route plus a way for the engine to run `plan_versions.content` (EX is the only lane allowed additive calls in `service.py`), or (b) document it as an explicit limit in the evidence file and keep matrix row 44 `partial`. Do not mark 44 `verified` while this is open and undocumented.

## Remaining Session 6 work (integration, done by the lead, not by lanes)
1. Re-run every lane's own tests and `ruff check` yourself.
2. In `deploy/runners/services.py` mount `plans`, `execution`, `artifacts`, `search`, `exports` routers, each with its `configure_*(service)` call, next to `discussion.configure_discussion`.
3. Add the new routers to `scripts/spec_drift.py` and `section4/tests/unit/test_policy_gate_orchestrator.py` (raise the `len(paths)` guard). The policy rules for these paths are already in `contracts/src/harness_contracts/policy_enforce.py`; the 38 audit names are already reserved in `section1/src/harness_identity/audit.py`.
4. Check migration order 0047→0050 and that any `service.py` edits are additive.
5. Full suites: section1 and section4 pytest, plus ruff in both (`section4/.venv/bin/python -m pytest -p no:cacheprovider -q`, `section4/.venv/bin/ruff check src tests`).
6. Postgres: `make pg-probe-migrate` (expect 49/50 migrations, 0001→0050) and `make pg-probe-s1`; write a Session 6 live-PG test modelled on `section1/tests/test_session4_pg.py` and `section4/tests/unit/test_session4_live_pg.py` (opt-in via `HARNESS_TEST_PG=1`, throwaway DB each; PG is at 127.0.0.1:5499). Cover concurrent plan edits, retry `requestId`, artifact upload, search re-authorization, export.
7. Regenerate specs and SDKs: `section4/.venv/bin/python scripts/spec_drift.py --write`, then `git checkout -- section5/openapi.json`, then `section4/.venv/bin/python scripts/sdk-gen/generate.py` and `... --target ts`.
8. UI lane (section7): plan versions panel, task editor, subtasks, completion report/accept, abandon/retry, artifacts list/upload/download/preview, change review, lineage, search page, export dialog, deletion request. Keyboard-accessible; reuse the Session 5 announcer (`src/lib/a11y.ts`, `LiveAnnouncer.tsx`). Check with `tsc` and vitest. Spec-drift trap: build URLs from a variable first (`const pid = encodeURIComponent(x)`), not `${encodeURIComponent(..."")}` inside a template.
9. Run the single smoke once: `deploy/pilot/run_smoke.py --otp` (needs the port free and about 2 minutes between runs; it overwrites untracked `docs/briefs/evidence/p1/*`).
10. Update matrix rows 44, 48, 50 honestly, write `docs/briefs/evidence/s6/session6-summary.md`, update `PROJECT-STATE.md` and `path-to-9-checkpoint.md`.
11. Commit by task, local only.

## Then Sessions 7–10 (one session prompt each, written like `path-to-9-session6-prompt.md`)
- 7: integrations, admin, billing (49, 51, 57; 57 capped at 8)
- 8: security, privacy, compliance (52, 58, 31; 52 and 58 capped at 8)
- 9: reliability, scale, observability (53, 54, 55; 53 capped at 8)
- 10: rollout, a11y/i18n finish, final acceptance (56, 59, 60, 03, 61; 03 and 61 capped at 8)

## Hard constraints
- **No push. No auto-merge.** Commit only tested work, by task.
- **Never commit with a dirty file as a `git commit` pathspec** — that commits the whole file, including someone else's edits. Stage with `git add <explicit files>`; for a partial file, build a filtered patch and `git apply --cached --recount`, then a plain `git commit`. This already went wrong once in `section1/src/harness_identity/notifier.py`.
- **Do not touch the ~100 unrelated uncommitted files**: all of `section2/` changes; `docs/agent-program/`, `scripts/agent-program/`; `docs/briefs/smtp-tls-policy-options.md`, `multiplayer-session1-checkpoint.md`, `multiplayer-readiness.md`; `AGENTS.md`, `README.md`, `.dockerignore`, `.gitignore`; section1 `auth.py`, `config.py`, and the user's SMTP TLS edits in `section1/src/harness_identity/notifier.py` (~45 diff lines, uncommitted on purpose); section1 tests `test_notifications.py`, `test_notifier.py`, `test_roster_api.py`, `test_slim_bus.py`, `test_returning_login.py`, `test_smtp_otp_transport.py`; section4 `confine.py`, `logging_utils.py`, `signals.py`, `__init__.py`, `container_runtime.py`, `model_relay.py`, `pilot_metrics.py` and old tests (`test_collab_backend.py`, `test_isolation_and_staging.py`, `test_staging_e2e.py`, …); `section5/tests/unit/test_checkpoints.py`, `section7/e2e/real.e2e.ts`; the earlier collab-denial audit docs. Run `git status` and stage only paths you own.
- No `ruff --fix` (only `--select I001 --fix` on a single file you own). No docker/make/smoke from lane workers; the lead runs those, one smoke at a time.
- Architecture rules: section4 speaks raw SQL (`sqlalchemy.text`) and may import only `harness_contracts`; all migrations are section1 files (next free after 0050 is 0051). The section1 audit writer rejects unregistered action names; section4 writes audit rows raw, ids only, never content. Foreign and unknown resources answer 404 with the same envelope. An unclassified `/api/` path is refused by the policy gate. Sqlite tests that spawn engine tasks must await them before `engine.dispose()`.
- Shell quirks (zsh/macOS): no `timeout`; use bounded `for` loops; BSD `sed -i ''`; zsh does not word-split `$VAR`, use arrays for path lists; always quote the repo path.
- Before coding, state your assumptions and a numbered plan with a verify step per item; name the exact verification you ran before saying anything is done. Report honestly what was **not** run (not run so far: `make gate`, `make e2e*` beyond the smoke leg, pip-audit, `run_smoke.py --real`, Linux Landlock, the section5 suite, multi-process races, real-browser keyboard/axe tests).

## Report back
When Session 6 is integrated, give: commits made, test counts per suite (and which were not run), matrix rows changed, and the honest-gap list. Then proceed to Session 7.
