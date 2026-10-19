# Harness Multiplayer AI — project state and resume guide

Written 2026-10-18 during Session 2. Read this first in a new session, then `docs/briefs/path-to-9-plan.md`,
`docs/briefs/capability-matrix.md` and `docs/briefs/path-to-9-checkpoint.md`. The repository is the source of truth:
re-verify any number below before relying on it. Nothing here is a claim of readiness for real users (see "Not cleared").

Repo `/Users/user/untitled folder 2/harness`, git branch `master`, HEAD `203e9a8`, **nothing pushed**.

## 1. What the project is

The "master prompt" is the 61-part *Harness Multiplayer AI — Full Product Master Build Prompt*
(`docs/master-prompts/harness-multiplayer-master-build-prompt.md`). Goal of the current programme: bring every part to 9/10
over ten self-contained sessions (`path-to-9-plan.md`). Definition of 9/10: every directive implemented or explicitly out of
scope, automated invariant tests, an evidence file, docs matching behaviour, and a `verified` row in the capability matrix.
10/10 needs external evidence; externally gated parts are capped at 8: 03, 37/57, 53, 52/58, 61.

Layout: section1 identity/auth/audit/eventbus (migrations 0001–0037) · section3/6/8/10 auxiliary · section4 orchestrator
(engine, collab, staging, lifecycle, retention) · section5 sandbox · section7 Next.js UI + Playwright · `deploy/` compose stack,
runners, observability, `deploy/pilot` disposable smoke stack · `docs/` · `scripts/` (spec_drift, sdk-gen) · `clients/` generated SDKs.

## 2. Sessions

| Session | Objective | State |
|---|---|---|
| 1 | Truth ledger, verification baseline, no new features | Done. Code/ops parts committed; most Session 1 **docs are still untracked** (see section 4b) |
| 2 | Pilot residuals (parts 01, 02, 05–08, 10–12, 22–24, 27, 32) + sandbox fixes pulled forward | **In progress** — see section 4 |
| 3–10 | Policy/orgs, presence, discussion, plans/artifacts, integrations, compliance, scale, final acceptance | Not started (`path-to-9-plan.md`) |

## 3. Done and committed this session (6 commits, local only)

| Commit | What |
|---|---|
| `a7bb2a6` | `make pg-probe-s1` self-contained: starts compose `db`, derives DSN/container |
| `5414a14` | Sandbox T15 (taskId takeover, HIGH), T16 (traversal), T17 (symlink), T7b (private tmp): 409 `TASK_ID_IN_USE`, `resume` flag, `.owner` file |
| `f59ac4b` | `deploy/observability/alerts.yml` wired via `rule_files`, `docs/runbooks/pilot.md`, flag-rollback test |
| `c9ca1d9` | Session 1 ops work: request/run/action log correlation |
| `2ab1ca0` | Shared-run UI (parts 12, 24), three Playwright legs, pilot smoke harness |
| `203e9a8` | Interim matrix, threat-model statuses, checkpoint |

## 4. Done but NOT yet committed (lane A + integration) — all tested, commit per task

Lane A (lifecycle, audit, rules) died when the Claude process exited; its code and tests survived intact and were verified.
Per-area contents:

- **Lifecycle (parts 10, 22, 23)** — `section4/src/harness_orchestrator/lifecycle.py` (durable transition table),
  `engine.py` (EPERM fix: `PermissionError` = group gone), `run_queue.py`, `service.py`, `retention.py`; tests
  `test_lifecycle_table.py`, `_races.py`, `_recovery.py`, `test_workspace_lifecycle_s2.py`; doc `docs/lifecycle.md`.
- **Audit and events (part 11)** — migration `0036_audit_hash_chain.sql` (hash chain, DB refuses UPDATE),
  `section1/src/harness_identity/audit_chain.py`, `section1/scripts/verify_audit_chain.py`; migration `0037_collab_event_push.sql`
  (LISTEN/NOTIFY), team-scoped bounded audit export, WebSocket push of collaboration events with cursor resume;
  tests `test_audit_chain_pg.py`, `test_audit_export_and_collab_push.py`, `test_collab_push_pg.py`, `ws_driver.py`.
- **Approval/redirect rules (05–08)** — `collab.py`, `staging.py`; tests `test_collab_rules_s2.py`, `test_collab_chaos_live_pg.py`.
- **Metrics** — `domain_metrics.py` in section1 and section4: `harness_approval_denied_total{reason}`,
  `harness_run_cancel_total{outcome}`, `harness_isolation_violation_total{kind}`, `harness_event_delivery_total{outcome}`
  (closed label sets, all series rendered at 0).
- **Integration fixes I made after finding them**
  - Alert rules reconciled to real labels (`outcome="unconfirmed"`, `"dropped"`, `"duplicate"`); new test fails if an alert matches
    a label value the code never emits; `docs/runbooks/alerting.md` updated.
  - **Counters did not reach Prometheus**: root `/metrics` (deploy collector) omitted them, so all pilot alerts would have been
    silent. Fixed: `install_metrics_endpoint(app, extra)` and `simple_app(..., extra_metrics)` in `deploy/runners/`; verified by
    scraping the real identity and orchestrator apps (all four names present).
  - `Makefile`: `pg-probe-integration`/`pg-probe-migrate` now depend on `pg-up` like `pg-probe-s1`.
  - Contracts: `spec_drift.py --write` + `sdk-gen/generate.py` (python and ts) so SDKs carry sandbox `resume`.
  - Ruff clean in sections 1, 4, 5 (lane A's files had E501/F401/F811 findings).

## 4b. Session 1 deliverables still untracked (commit when you choose)

`docs/architecture-map.md`, `data-map.md`, `roadmap.md`, `product-boundary.md`, `contracts-and-limits.md`,
`docs/briefs/{path-to-9-plan,path-to-9-session1-prompt,path-to-9-session2-prompt,stable-prompt-prefix,checkpoint-template}.md`,
`docs/briefs/evidence/s1/`, `scripts/spec_drift.py` + `test_spec_drift.py`, regenerated `section*/openapi.json`,
`scripts/sdk-gen/specs/*`, `clients/**`. Check with `git status --short`; some are mixed with earlier unrelated edits.

## 5. Verification record (commands run by me, 2026-10-18)

| Check | Result |
|---|---|
| `make pg-probe-s1` (live Postgres, compose db), re-run after lint fixes | 706 passed, 1 skipped |
| `make pg-probe-migrate` | PASS: 36 migrations (0001→0037) apply clean, re-run idempotent |
| section1 pytest (sqlite), after lint fixes | 681 passed, 26 skipped (skips = live-PG-only tests) |
| section4 pytest, after lint fixes | 833 passed, 32 skipped |
| section4 live-PG chaos + claim-cap, after lint fixes | 6 passed |
| ruff `src tests` in sections 1, 4, 5 | all checks passed |
| section5 pytest | 230 passed, 1 skipped (Linux-only landlock) |
| section7 `tsc --noEmit` / vitest | clean / 540 passed |
| `promtool check config` (compose mount) | 15 rules found |
| `scripts/spec_drift.py`, `scripts/test_spec_drift.py` | match / 1 passed |
| `run_smoke.py --otp` (real OTP two-user browser proof) | 1 passed, twice (run by lane C on final UI code); **not yet re-run on the integrated tree** |

**Not run:** `make gate`, `make e2e*`, `pip-audit`, full `docker compose up`, `run_smoke.py --real` (real engine),
Linux Landlock path, screen-reader/contrast checks.

## 6. Open work, in order

1. **Docs lane (running when this was written)**: `docs/audit-and-events.md`, `docs/briefs/evidence/s2/lane-a.md`,
   `docs/briefs/evidence/s2/part05-gap-checklist.md`. If missing, re-run it: read-only on code, docs/evidence only.
2. **One smoke run on the integrated tree**: `python3 deploy/pilot/run_smoke.py --otp`. Never two at once; wait ~2 minutes after a
   previous run (port TIME_WAIT); it overwrites `docs/briefs/evidence/p1/*` (expected).
3. ~~UI consumes the WebSocket push~~ done in Session 3 prep (wake-up socket, 20 s safety poll); pause/resume audited. Browser-connected socket not asserted in e2e.
4. Fill the `PENDING-LEAD` rows in `docs/briefs/evidence/s2/acceptance-core-flow.md` with fresh results.
5. Update matrix rows 01, 02, 05–08, 10, 11, 22, 23, 27 (and 09/12/24/28/32 if evidence changed); extend `threat-model.md`;
   overwrite `path-to-9-checkpoint.md`; then commit lane A per task with per-file pathspecs.
6. Backend gaps lane C found: approvals API has no decision timestamp and no event on amend; no UI to file/amend a proposal;
   no friendly error mapping; part 12 item 009 (terminal details) and responsive layout untested.

## 7. Facts that bite

- `HARNESS_PILOT_TEAMS` is an **allowlist**: unset or empty opens collaboration to every team. A rollback is a non-empty list
  without the team (e.g. `__rolled_back__`). Rolled-back teams cannot read roster/history/approvals through the API either.
- Sandbox: `confinement=off` (and `auto` degrading in the compose image) has no OS boundary; local runner stays dev-grade;
  `DockerSandboxRunner` is an empty subclass; task dirs from before T15 have no `.owner` and need `resume: true` to destroy.
- Alerts: no Alertmanager, so `severity: page` pages nobody; backup/WAL alerts load but are inert (nothing emits those metrics).
- Prometheus scrapes the **root `/metrics`**; the orchestrator's legacy `/api/v1/orchestrator/metrics` is not scraped.
- Real OTP sign-in needs `HARNESS_REQUIRE_OWNERSHIP=true`; the code is read from the identity service log (dev notifier).
  Real email delivery is unproven.
- **`ruff check --fix` deletes imported pytest fixtures** that tests re-request as parameters (it sees them as shadowed and
  unused). It silently broke `test_workspace_lifecycle_s2.py` (`env as retention_env`) until the suite caught it. Use
  `--select I001 --fix` only, and `# noqa: F401` / `# ruff: noqa: F811` on fixture imports.
- Tooling: macOS zsh. BSD `sed -i` needs a suffix; zsh does not split unquoted variables or expand `--include=*.py`;
  the repo path contains a space, so quote it everywhere including Makefile recipes; `echo ====` breaks in zsh.
- Docker Desktop was started during Session 1 and runs unrelated containers (e.g. facescope); do not stop what you did not start.
- The working tree also holds ~100 uncommitted files from earlier unrelated work (section2, `docs/agent-program/`, etc.).
  Commit by explicit path only. `section5/tests/unit/test_checkpoints.py` and `section7/e2e/real.e2e.ts` were deliberately left out.

## 8. Blocked / needs the user

- Commit scope for the remaining lane-A files (the tree mixes this session's work with earlier unrelated work).
- Externally gated evidence: real pilot data (part 03), live SSO/Stripe, pentest, PITR on a real object store, counsel review.
- Pilot is **not cleared for real users**: no measured baseline (part 03), Alertmanager absent, operator-present rules are manual.

## 9. How to resume

```
cd "/Users/user/untitled folder 2/harness"
git status --short | head -40          # confirm what is uncommitted
make pg-probe-s1 && make pg-probe-migrate   # live Postgres gates (starts compose db)
```
Then continue at section 6 item 1. For Session 3 onward use `docs/briefs/stable-prompt-prefix.md` as the first block of the prompt.
