# Harness Multiplayer AI — project state and resume guide

Updated 2026-10-19 at the end of Session 7. Read this first in a new session, then `docs/briefs/path-to-9-plan.md`,
`docs/briefs/capability-matrix.md` and `docs/briefs/path-to-9-checkpoint.md`. The repository is the source of truth:
re-verify any number below before relying on it. Nothing here is a claim of readiness for real users (see "Not cleared").

Repo `/Users/user/untitled folder 2/harness`, git branch `master`, **nothing pushed**. `git log --oneline` shows the commits.

## 1. What the project is

The "master prompt" is the 61-part *Harness Multiplayer AI — Full Product Master Build Prompt*
(`docs/master-prompts/harness-multiplayer-master-build-prompt.md`). Goal of the current programme: bring every part to 9/10
over ten sessions (`path-to-9-plan.md`). 9/10 means: every directive implemented or explicitly out of scope, automated invariant
tests, an evidence file, docs matching behaviour, and a `verified` matrix row. 10/10 needs external evidence; externally gated
parts are capped at 8: 03, 37/57, 53, 52/58, 61.

Layout: section1 identity/auth/audit/eventbus/orgs/policy/workspaces/integrations (migrations 0001–0051) · section3/6/8/10
auxiliary · section4 orchestrator · section5 sandbox · section7 Next.js UI + Playwright · `deploy/` compose stack, runners,
observability, `deploy/pilot` disposable smoke stack · `docs/` · `scripts/` (spec_drift, sdk-gen) · `clients/` generated SDKs.

## 2. Sessions

| Session | Objective | State |
|---|---|---|
| 1 | Truth ledger, baseline | Done, committed |
| 2 | Pilot residuals + sandbox fixes | Done, committed (parts 05–08, 10, 11, 22, 23, 27 verified) |
| 3 | Orgs, permissions, workspaces (37, 38, 39) | Done, committed, incl. the part 38 enforcement carry-over |
| 4 | Sessions, presence, control leases, agent registry (40, 41, 42, 45) | Done, committed (lanes S, P, C, A, U) |
| 5 | Discussion, notifications, real-time delivery, collab UI (43, 46, 47, 56-collab) | Done, committed (lanes D, N, R, X) |
| 6 | Plans/artifacts/search (44, 48, 50) | Integrated locally; 48 and 50 verified; 44 partial pending native version approval/execution |
| 7 | Integrations/admin/billing (49, 51, 57) | Done locally; partial outcomes and cap 8 for 57; see Session 7 evidence |
| 8–10 | Security/compliance, scale, final acceptance | Next: Session 8 (52, 58, 31 enforcement) |

## 3. Session 3 result

| Part | Matrix state | Why |
|---|---|---|
| 37 orgs & identity | verified (cap 8) | `orgs.py`, migration 0038, 22 org tests, live-PG test; SSO login records assurance; live SSO/Stripe external; no MFA factor |
| 38 permissions & policy | verified | pure engine now in `contracts/policy_engine.py`; `contracts/policy_enforce.py` classifies every identity and orchestrator route and enforces org policy (additive to legacy checks) for org-linked teams; step-up on role change and export. Limits: WebSocket and `/orgs/*` not engine-gated |
| 39 workspaces | verified | `workspaces.py`, migration 0040, section4 `workspace_gate.py` (run start + queue claim refuse non-active workspaces); gaps in `evidence/s3/lane-w.md` |

Evidence: `docs/briefs/evidence/s3/` (`lane-o.md`, `lane-p.md`, `lane-w.md`, `session3-summary.md`). Docs: `organizations.md`,
`permissions.md`, `workspaces.md`.

## 3b. Session 4 result

Parts 40, 41, 42, 45 verified (matrix rows list tests and gaps). Evidence `docs/briefs/evidence/s4/` (`session4-summary.md` has the
command table); docs `sessions.md`, `presence.md`, `control.md`, `agents.md`. Behaviour change: a non-holder's guidance is queued until the
lease holder accepts it (`control/decide`); the UI has the accept step.

## 3c. Session 5 result

Parts 43, 46, 47 verified; 56 stays partial (collaboration UI done, rest is Session 10). Evidence `docs/briefs/evidence/s5/` (`session5-summary.md`
has the command table); docs `discussion.md`, `notifications.md`, `realtime.md`, `accessibility.md`. Migrations 0045 (discussion), 0046
(notifications); no 0047. Collab events carry `prevSeq`; `collab-history` returns pages with `nextCursor/hasMore/headSeq`. Notification bell polls
`/api/v1/notifications/unread-count` every 60 s. Needs `tzdata` in slim images and `HARNESS_APP_URL` for mail links.

## 3d. Session 6 result

Parts 48 and 50 are verified; part 44 remains partial. Migrations 0047–0050 add versioned plans,
execution records, artifacts/version-control metadata, search/export/deletion. Routers are mounted in
`deploy/runners/services.py`; OpenAPI and SDKs were regenerated. Section 7 run details now include
the Session 6 workbench. Evidence is in `docs/briefs/evidence/s6/`; docs are `plans.md`,
`execution.md`, `artifacts.md`, and `search-and-export.md`.

No route approves a native plan version, and the engine continues to execute the legacy in-memory
plan. Tool-call counts are not enforced and the running-limit sweep is not scheduled. Artifact/export
storage and search indexing also lack deploy schedulers.

## 3e. Session 7 result
## 3f. Session 8 status

Session 8 reviewed parts 31, 52 and 58. Reports under `docs/briefs/evidence/s8/` map the directives
and record verification. Section 4 pins the SDK `sdk-minimal` profile, applies a mandatory per-run
`workspace-write` overlay, and refuses plans with non-empty `forbiddenPaths` before SDK invocation.
Its shell still lacks Section 5's command allow-list. Migration 0056 adds operator-managed run holds
that the Section 4 run purge honors; the event/audit retention sidecar, deletion workflow, and backup
expiry do not. Parts 31/52/58 remain partial; 52/58 require external evidence, and account-wide
deletion and tenant-specific residency remain absent. No legal/compliance claim is made. See
`docs/briefs/evidence/s8/session8-summary.md`.



Part 49 remains partial: team-scoped versioned outbound webhooks now have encrypted one-time
secrets, HMAC timestamped signatures, idempotency keys, bounded retries, host allowlisting and
rotation. GitHub still uses broad `repo` scope; issue tracker/chat adapters and extension sandbox
are absent. Part 51 remains partial: Section 7 now exposes organization membership/role, policy
preview/save, emergency access, history and team integration controls; dual policy approval,
org-wide execution disablement and retention/residency configuration remain absent. Part 57 is
partial and capped at 8 for live Stripe/OIDC evidence. Local tier and spend gates exist, but daily
run cap checks are not atomic and tool-call/in-flight spend caps and billing alerts are absent.
Details: `docs/briefs/evidence/s7/` and `docs/{integrations,billing-and-quotas}.md`.

## 4. Verification record (Session 7, 2026-10-19)

Session 7 targeted checks: Section 1 webhook/policy/billing/Stripe tests 58 passed; Section 4 budget
and tier tests 45 passed; Section 7 billing API UI test 7 passed; Section 7 typecheck and TypeScript
SDK typecheck passed; touched Section 1 Ruff passed. Contracts Ruff reports two findings in
untouched `contracts/src/harness_contracts/__init__.py` and `ratelimit.py`. No PostgreSQL migration,
full section suites, browser E2E or live provider check ran. See
`docs/briefs/evidence/s7/session7-summary.md`.

Session 6 verification record (2026-10-19):

Session 6 lane tests: PL 53, EX 66, AR 93, SE 74 passed; integrated policy/spec checks 8 passed;
Section 7 typecheck passed and full Vitest passed 817 tests across 68 files, including the new
workbench test.

Section 1 full suite: 923 passed, 29 skipped, 21 setup errors because SMTP/OIDC tests could not bind
localhost sockets in this sandbox. Section 4 full suite was interrupted after 1353 passed, 37 skipped
and 3 observed failures: one 1.011 s cost-rollup threshold miss and two macOS Seatbelt
`sandbox_apply` failures; it stalled at 93% for 159 s. Session 6 live-PG probe and
`make pg-probe-migrate` and `make pg-probe-s1` could not run: Docker socket absent and port 5499
refused connections. OTP smoke was attempted once but the script found port 8101 occupied and
declined to stop it. Section 4 full Ruff passed; Section 1 full Ruff reports three findings in the
unrelated untracked `scripts/verify_audit_chain.py`. See
`docs/briefs/evidence/s6/session6-summary.md` for the command record and gaps.

Session 5 record (superseded): section1 944 passed/29 skipped; section4 1143 passed/37 skipped;
Postgres migration probe through 0046 and live-PG checks passed; Section 7 typecheck and 816 Vitest
tests passed; OTP smoke passed.

(Session 4 record, superseded: section1 859, section4 979, vitest 572, migrations 0001→0044.)

section1 pytest 859 passed/27 skipped; section4 pytest 979 passed/32 skipped; ruff clean s1/s4; `make pg-probe-migrate` PASS 0001→0044;
`make pg-probe-s1` 885 passed/1 skipped; section4 live-PG session 4 + collab chaos tests pass; section7 tsc clean, vitest 572 passed;
`run_smoke.py --otp` passed (after one failure fixed by lane U). **Not run:** `make gate`, `make e2e*` beyond the smoke leg, `pip-audit`,
`--real`, Linux Landlock, section5 suite, a multi-process race, browser tests of the new UI handlers.

## 5. Open work, in order

1. ~~Part 38 enforcement carry-over~~ done (`evidence/s3/part38-enforcement.md`). Remaining limits are in `docs/permissions.md`.
2. ~~Sessions 4 and 5~~ done. Session 6 integrated; next Session 7 (49, 51, 57) per
   `path-to-9-plan.md`. Keep part 44 partial until native version approval and engine execution are connected.
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
- FastAPI 0.141 nests included routers (`_IncludedRouter`): `app.routes`/`route.path` walks are vacuous; walk `app.openapi()["paths"]` instead.
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
make pg-probe-s1 && make pg-probe-migrate  # expect 43 migrations
```
