# Harness Multiplayer AI: project state and resume guide

Updated 2026-10-20 (Session 11, cloud pass). Read this first in a new session, then `docs/briefs/path-to-9-plan.md`,
`docs/briefs/capability-matrix.md` and `docs/briefs/path-to-9-checkpoint.md`. The repository is the source of truth: re-verify any
number before relying on it. Nothing here claims readiness for real users (see "Not cleared"). Per-session results and the old
verification records are in `docs/briefs/evidence/s11/state-history.md`.

## 1. What the project is

The "master prompt" is the 61-part *Harness Multiplayer AI: Full Product Master Build Prompt*
(`docs/master-prompts/harness-multiplayer-master-build-prompt.md`; that file was never committed and is not in the repository).
The programme brings every part to 9/10: each directive implemented or explicitly out of scope, invariant tests, an evidence file under
`docs/briefs/evidence/`, docs that match behaviour including limits, and a `verified` matrix row. Parts that need an outside party stay
capped at 8: 03, 37, 52, 53, 58, 61. Part 57's cap was removed because Stripe is out of scope (owner direction, 2026-10-19).

Layout: section1 identity, auth, audit, event bus, orgs, policy, workspaces, integrations (migrations 0001 to 0056) · section3, 6, 8, 10
auxiliary · section4 orchestrator · section5 sandbox · section7 Next.js UI and Playwright · `deploy/` compose stack, runners,
observability, `deploy/pilot` smoke stack · `docs/` · `scripts/` (spec_drift, sdk-gen) · `clients/` generated SDKs.

## 2. Sessions

Sessions 1 to 10 are built. Codex built Sessions 6 to 10; Claude re-ran their suites, fixed the regressions it found (`6b6adfb`) and
closed part 44 (`00a1412`). Session 11 is the cloud pass (below).

| Session | Objective | State |
|---|---|---|
| 1 | Truth ledger, baseline | done |
| 2 | Pilot residuals, sandbox fixes | done (parts 05 to 08, 10, 11, 22, 23, 27 verified) |
| 3 | Orgs, permissions, workspaces (37 to 39) | done |
| 4 | Sessions, presence, control leases, agent registry (40 to 42, 45) | done |
| 5 | Discussion, notifications, real-time delivery, collab UI (43, 46, 47) | done |
| 6 | Plans, artifacts, search and export (44, 48, 50) | done; 44 closed by `00a1412` |
| 7 | Integrations, admin, billing controls (49, 51, 57) | done; partial for provider breadth, residency, in-flight spend |
| 8 | Security, compliance, data lifecycle (31, 52, 58) | done; partial, 52 and 58 externally capped |
| 9 | Reliability, scale, observability (53 to 55) | done; partial, 53 capped |
| 10 | Release, accessibility, readiness (56, 59 to 61) | done; NO-GO report |
| 11 | Cloud pass: parts 12, 14, 24, 29, process parts, PostgreSQL diagnosis | done; see `evidence/s11/` (`cloud-report.md`) |

Branches: `master` is the older v1.0.0-candidate (2026-08-26) and holds none of the programme. The programme's commits are on
`cloud/path-to-9-finish` (owner's last commit `bd48c41`) and on `claude/path-to-9-finish-ax6hie` (Session 11 work). Nothing is merged.

## 3. Where the matrix stands

Counts (2026-10-20): verified 43 · implemented 4 · partial 12 · missing 0 · blocked 2.
Implemented: 12, 25, 26, 32. Partial: 28, 31, 49, 51 to 56 (52, 53 capped), 57, 58 (capped), 59. Blocked: 03, 61.
Readiness: **NO-GO** for broad rollout (`docs/briefs/final-deliverables.md`, section 10).

## 4. Open work, in order

1. **Owner: make the pushed history runnable.** Committed `engine.py` needs `container_runtime.py`, `confine.py` container mode, a test
   fixture entry and a stand-in worker case that exist only on the owner's machine. Details and the exact list:
   `docs/briefs/evidence/s11/clean-checkout-gaps.md`. Until then a clean checkout cannot import section 4. Also a stale fake in `section1/tests/test_slim_bus.py` fails one test.
2. T13b: OTP redeem race in `section1/src/harness_identity/auth.py` `verify_otp`. Fix is a guarded
   `UPDATE ... WHERE consumed_at IS NULL` and `attempts = attempts + 1 ... RETURNING`. Not done: the owner has uncommitted SMTP edits in that file.
3. Rows that need Docker: 25, 26, 28, 32, 53, 59 (compose stack, pilot smoke, restore drill). Four section 1 tests (live-PG FK and index probes, opt-in via `HARNESS_TEST_PG`) call `docker exec`.
4. Rows that need a real browser or screen reader: 12, 56. A real-browser responsive and keyboard check has not been run.
5. Externally gated: 03 (real pilot cohort and baseline), 37 (live identity provider), 52 and 58 (pen test, counsel), 53 (real restore), 61.
6. Backend gaps from earlier sessions are listed per part in the matrix rows; the largest: no UI to file or amend a proposal; approvals
   carry no decision timestamp; audit chain has no external anchor; running runs are not stopped on workspace archive.

## 5. Facts that bite

- `HARNESS_PILOT_TEAMS` is an **allowlist**: unset or empty opens collaboration to every team.
- Prometheus scrapes the **root `/metrics`**; sandbox `confinement=off` has no OS boundary; there is no Alertmanager.
- `ruff check --fix` deletes imported pytest fixtures. Never use it broadly (`--select I001 --fix` on one new file is fine).
- SQLite tests with spawned engine tasks must await the tasks before `engine.dispose()`, or teardown stalls (it hung two full-suite runs).
- SQLite hides PostgreSQL typing errors: an untyped empty `IN` list failed only on PostgreSQL (`evidence/s11/live-pg-search-export.md`).
  Run the live-PG modules (`HARNESS_TEST_PG`) after touching raw SQL.
- `execution.retry()` returns `(body, created)`; a repeated `requestId` is `created == False` (HTTP 200).
- With the collab push socket up the UI safety poll is 20 s, so the peer-offline alert can take that long (e2e waits 30 s).
- `run_smoke.py` needs about 2 minutes between runs (port 8101 TIME_WAIT) and overwrites `docs/briefs/evidence/p1/*`.
- FastAPI 0.141 nests included routers: walk `app.openapi()["paths"]`, not `app.routes`.
- Regenerating `section5/openapi.json` drops hand-written 400/409 docs: restore it with `git checkout -- section5/openapi.json`.
- Dev SQLite has no `teams.org_id` (migration 0038 adds it on PostgreSQL); `require_team_stepup` tolerates that.
- Migrations are 55 files numbered 0001 to 0056 (there is no 0014); `deploy/runners/migrate_selftest.py` checks disk against the ledger.
- Owner's machine is macOS zsh (no `timeout`, BSD `sed -i ''`, repo path has a space). The cloud box is Linux, root, no Docker daemon,
  PostgreSQL installable (run your own on another port, never 5432 of a shared machine).
- The owner's machine holds uncommitted work (section2, `docs/agent-program/`, section4 `confine.py` and `container_runtime.py`
  changes, `model_relay.py`, `pilot_metrics.py`, section1 `auth.py` SMTP edits, `docs/briefs/codex-*`, `smtp-*`, `multiplayer-*`).
  Commit by explicit path only; never `git add -A`.

## 6. Not cleared

The pilot is **not cleared for real users**: no measured baseline (part 03), no Alertmanager, operator-present rules are manual, live
SSO unproven, no pen test, point-in-time recovery unproven, no counsel review, the pushed history is not runnable on a clean checkout,
and no pilot smoke or real-browser run exists for the current tree.

## 7. How to resume

```
git fetch origin && git log --oneline -8          # confirm the branch tip
git status --short | head -40
cd section4 && ./.venv/bin/python -m pytest tests -q   # needs the owner's uncommitted files; see clean-checkout-gaps.md
cd .. && make pg-probe-s1 && make pg-probe-migrate     # expect 55 migrations (0001 to 0056)
```

Per-part work: read the matrix row, audit directive by directive, add the missing invariant test, write
`docs/briefs/evidence/s11/<part>.md`, flip the row only with that evidence, and run `section4/tests/unit/test_program_docs.py`.
