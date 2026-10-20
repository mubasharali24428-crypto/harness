# State history: per-session results and verification records

Moved verbatim from `docs/briefs/PROJECT-STATE.md` on 2026-10-19 (Session 11) so the durable brief stays under two pages
(master prompt 18.002). Facts here were true when written; re-verify before relying on them.

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

Native plan versions are now approvable (`POST .../plan/versions/{n}/approve`); approval adopts the version as the run's legacy plan, so
the engine runs exactly the approved content. The running-limit sweep is scheduled (`run_limit_sweeper`, `HARNESS_LIMIT_SWEEP_SECONDS`).
The tool-call cap counts only calls reported through the agent tool-call route. Artifact/export storage and search indexing still lack
deploy schedulers. Evidence: `evidence/s6/part44-version-approval.md`.

## 3f. Session 8 status

Session 8 reviewed parts 31, 52 and 58. Reports under `docs/briefs/evidence/s8/` map the directives
and record verification. Section 4 pins the SDK `sdk-minimal` profile, applies a mandatory per-run
`workspace-write` overlay, and refuses plans with non-empty `forbiddenPaths` before SDK invocation.
Its shell still lacks Section 5's command allow-list. Migration 0056 adds team-admin run holds with
audited create/read/release routes; Section 4 run purge, Section 1 event/audit pruning, and PostgreSQL
event-partition retirement honor them. User deletion and backup expiry do not. Parts 31/52/58 remain
partial; 52/58 require external evidence, and account-wide deletion and tenant-specific residency
remain absent. No legal/compliance claim is made. See
`docs/briefs/evidence/s8/session8-summary.md`.

## 3e. Session 7 result

Part 49 remains partial: team-scoped versioned outbound webhooks have encrypted one-time secrets,
HMAC timestamped signatures, idempotency keys, bounded retries, host allowlisting and rotation.
GitHub defaults to `public_repo read:user`, but classic OAuth still permits writes to public repos;
issue-tracker/chat adapters and extension sandbox are absent. Part 51 remains partial: dual approval
for high-risk policy changes is owner-configurable, and owners can set shorter event/audit retention
windows applied by the worker. Physical residency remains deployment-wide. Part 57 remains partial:
daily run reservations are atomic per run/team and tool-call limits read durable tool records;
in-flight spend is estimate-based and alert delivery is not wired. Stripe is intentionally out of
scope and does not cap the score. Details: `docs/briefs/evidence/s7/` and
`docs/{integrations,billing-and-quotas}.md`.

## 4. Verification record (Session 7, 2026-10-19)

Session 7 original targeted checks: Section 1 webhook/policy/billing/Stripe tests 58 passed; Section 4 budget
and tier tests 45 passed; Section 7 billing API UI test 7 passed; Section 7 typecheck and TypeScript
SDK typecheck passed. Completion pass: Section 1 org/policy/retention tests 80 passed, 2 skipped;
Section 2 GitHub OAuth tests 11 passed; Section 7 typecheck passed. The two Section 4 execution fixture
failures noted then were fixed in `6b6adfb` (full Section 4 now 1464 passed, 38 skipped). PostgreSQL
migrations and live provider flows were not run. See
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

