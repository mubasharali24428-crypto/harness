# Harness Multiplayer AI — project state and resume guide

Rewritten 2026-10-19 at the end of Session 3. Read this first in a new session, then `docs/briefs/path-to-9-plan.md`,
`docs/briefs/capability-matrix.md` and `docs/briefs/path-to-9-checkpoint.md`. The repository is the source of truth:
re-verify any number below before relying on it. Nothing here is a claim of readiness for real users (see "Not cleared").

Repo `/Users/user/untitled folder 2/harness`, git branch `master`, **nothing pushed**. `git log --oneline` shows the commits.

## 1. What the project is

The "master prompt" is the 61-part *Harness Multiplayer AI — Full Product Master Build Prompt*
(`docs/master-prompts/harness-multiplayer-master-build-prompt.md`). Goal of the current programme: bring every part to 9/10
over ten sessions (`path-to-9-plan.md`). 9/10 means: every directive implemented or explicitly out of scope, automated invariant
tests, an evidence file, docs matching behaviour, and a `verified` matrix row. 10/10 needs external evidence; externally gated
parts are capped at 8: 03, 37/57, 53, 52/58, 61.

Layout: section1 identity/auth/audit/eventbus/orgs/policy/workspaces (migrations 0001–0040; 39 files, number 0001→0040) · section3/6/8/10
auxiliary · section4 orchestrator · section5 sandbox · section7 Next.js UI + Playwright · `deploy/` compose stack, runners,
observability, `deploy/pilot` disposable smoke stack · `docs/` · `scripts/` (spec_drift, sdk-gen) · `clients/` generated SDKs.

## 2. Sessions

| Session | Objective | State |
|---|---|---|
| 1 | Truth ledger, baseline | Done, committed |
| 2 | Pilot residuals + sandbox fixes | Done, committed (parts 05–08, 10, 11, 22, 23, 27 verified) |
| 3 | Orgs, permissions, workspaces (37, 38, 39) | Built and verified; see section 3; commit status in section 4 |
| 4–10 | Presence/sessions/leases/agent registry (40, 41, 42, 45), then discussion, plans/artifacts, integrations, compliance, scale, final acceptance | Not started |

## 3. Session 3 result

| Part | Matrix state | Why |
|---|---|---|
| 37 orgs & identity | verified (cap 8) | `orgs.py`, migration 0038, 22 org tests, live-PG test; SSO login records assurance; live SSO/Stripe external; no MFA factor |
| 38 permissions & policy | **partial** | `policy.py` engine (levels org>team>project>workspace>run, 7 permissions, custom roles, preview, decision log, step-up, emergency access) tested; but legacy routes and all section4 run/approve/steer paths still use the old role check. Step-up is wired only to team role change and audit export. |
| 39 workspaces | verified | `workspaces.py`, migration 0040, section4 `workspace_gate.py` (run start + queue claim refuse non-active workspaces); gaps in `evidence/s3/lane-w.md` |

Evidence: `docs/briefs/evidence/s3/` (`lane-o.md`, `lane-p.md`, `lane-w.md`, `session3-summary.md`). Docs: `organizations.md`,
`permissions.md`, `workspaces.md`.

## 4. Verification record (commands run by me, 2026-10-19)

| Check | Result |
|---|---|
| section1 pytest (sqlite) | 778 passed, 27 skipped |
| `make pg-probe-s1` (live Postgres, includes `test_session3_pg.py`) | 804 passed, 1 skipped |
| `make pg-probe-migrate` | PASS: 39 migrations (0001→0040), idempotent |
| section4 pytest | 844 passed, 32 skipped |
| ruff `src tests` section1, section4 | clean |
| `scripts/spec_drift.py` and its test; section4 spec drift test | match / pass |
| `run_smoke.py --otp` (two-user browser proof) | 1 passed (after one failure, see Facts) |

**Not run:** section5/section7 suites this session (unchanged except one e2e timeout), `make gate`, `make e2e*` (beyond the smoke leg),
`pip-audit`, `run_smoke.py --real`, Linux Landlock, a true multi-process archive-vs-start race, the concurrent-owner race on Postgres.

## 5. Open work, in order

1. **Part 38 enforcement carry-over**: route billing, teams, settings, SSO, rules, api-keys and section4 run/plan/approve/steer
   checks through the policy engine (section4 may import only `harness_contracts`, so it needs a decision endpoint or shared
   contract). Until done part 38 stays `partial`.
2. Session 4 (parts 40, 41, 42, 45): sessions containing runs, presence (also closes 05-002), control leases, agent registry.
   Use `docs/briefs/stable-prompt-prefix.md` as the first block of the prompt.
3. Backend gaps from earlier: no UI to file/amend a proposal; approvals API lacks a decision timestamp; part 12 item 009 and
   responsive layout untested; audit chain has no external anchor; browser socket connection not asserted in e2e.
4. Workspace gaps: no purge job after `purge_after`, running run not stopped on archive, `resume_agent` not gated, quota check race.
5. Dev SQLite has no `teams.org_id` (migration 0038 adds it on Postgres); `require_team_stepup` tolerates that.

## 6. Facts that bite

- `HARNESS_PILOT_TEAMS` is an **allowlist**: unset or empty opens collaboration to every team.
- Prometheus scrapes the **root `/metrics`**; sandbox `confinement=off` has no OS boundary; no Alertmanager.
- `ruff check --fix` deletes imported pytest fixtures. Never use it.
- **Sqlite tests with spawned engine tasks must await the tasks before `engine.dispose()`**, or teardown stalls 10 s to forever
  (found in `test_workspace_gate.py`; it hung two full-suite runs).
- With the collab push socket up, the UI safety poll is 20 s, so the peer-offline alert can take that long (e2e waits 30 s).
- `run_smoke.py` needs ~2 minutes between runs (port 8101 TIME_WAIT: wait until a bind succeeds) and overwrites `docs/briefs/evidence/p1/*`.
- Regenerating `section5/openapi.json` drops hand-written 400/409 docs from commit 5414a14: restore it with `git checkout`.
- Tooling: macOS zsh. No `timeout` command; `sleep N && cmd` is blocked (use bounded for-loops); BSD `sed -i` needs `''`;
  the repo path has a space (quote it); zsh does not expand `--include=*.py`.
- The working tree also holds ~100 uncommitted files from unrelated earlier work (section2, `docs/agent-program/`, section4
  `confine.py`/`logging_utils.py`/`signals.py`/`__init__.py` edits, `container_runtime.py`, `model_relay.py`, `pilot_metrics.py`,
  `section5/tests/unit/test_checkpoints.py`, `section7/e2e/real.e2e.ts`). Commit by explicit path only.

## 7. Not cleared

Pilot is **not cleared for real users**: no measured baseline (part 03), no Alertmanager, operator-present rules are manual, live SSO
and Stripe unproven, no pentest, PITR unproven, counsel review absent.

## 8. How to resume

```
cd "/Users/user/untitled folder 2/harness"
git status --short | head -40
make pg-probe-s1 && make pg-probe-migrate
```
