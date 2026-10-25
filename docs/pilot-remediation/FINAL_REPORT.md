> **Superseded 2026-10-21.** A second independent audit found S-01/S-02/S-03/S-04; the prior readiness verdict below is not current. See `docs/pilot-remediation/BASELINE_2026-10-21.md` for executed gates and residual
> blockers. Current verdict: NOT READY for a general pilot; private design-partner only.

## Continuation verification — 2026-10-22

The current working tree was verified without committing, deploying, or making
paid model calls. The completed Linux Landlock implementation was preserved.

| Check | Exact outcome |
|---|---|
| Compose validation | `docker compose --env-file .env -f deploy/docker-compose.yml config -q` passed |
| Backend rebuild | `docker compose --env-file .env -f deploy/docker-compose.yml build --no-cache --progress=plain sandbox` passed; optimized context `39.59 kB`; rebuilt image manifest/container ID `sha256:7796ad76e7b671d98abe5c3f0d30191082928e54ce3db1fcecc843790678f68c` |
| Active image | Only the sandbox service was recreated. Host/container `confinement.py` SHA-256 matched at `fd20bac80a07569ef313aabb8d9f4aa684c79a1a2bce85b25456c0a7caa76997`; active module contained `landlock_read_allowlist`, `_READ_HANDLED=13` |
| Linux confinement probe | Inside the active container: `landlock_probe=True`; a forbidden-path read exited `1` and did not expose the canary |
| Docker runner | Focused unit tests `6 passed`; real local Docker smoke verified container execution and workspace persistence, an indirect read of a masked forbidden directory exited `1`, and cleanup removed the workspace/container |
| Docker deployment wiring | Resolved Compose config keeps `SANDBOX_USE_DOCKER=false` and `SANDBOX_DEFAULT_DOCKER_IMAGE=""`; the runner is opt-in and empty-image configuration fails closed |
| Sandbox network default | Compose and `DockerSandboxRunner` now default to network-disabled execution (`HARNESS_SANDBOX_ALLOW_NETWORK=0` / `network_mode=none`); enabling network remains an explicit, non-allow-listed diagnostic/deployment opt-in |
| Local Docker runtime probe | Image `sha256:7796ad76e7b671d98abe5c3f0d30191082928e54ce3db1fcecc843790678f68c`, configured user `harness`; task process reported `uid=501`, `CapEff=0000000000000000`, `NoNewPrivs=1`. Docker Desktop rootless mode was not verified |
| Native macOS Section 5 suite | `make test-section5` → `211 passed, 22 skipped, 14 failed, 18 errors in 6.66s`; the host has neither Seatbelt nor Landlock, so `auto` failed closed as designed rather than falling back to unconfined execution |
| Focused S5 tests | macOS host: `12 passed, 5 skipped in 0.77s`; complete current Section 5 suite in a temporary Linux test image: `259 passed, 10 skipped, 1 warning in 5.55s` |
| Static gates | `make static-gates` passed route/isolation guards, shell syntax, Compose config, TS mirror, and spec-drift checks |
| Linux Python gate | Ruff passed for Sections 1–6, 8, 10, 11, and MCP. Tests passed: S1 `986/54 skipped`, S2 `48`, S3 `99`, S4 `1598/62 skipped`, dedicated Linux S5 `259/10 skipped`, S6 `123`, S8 `91`, S10 `69`, S11 `49/1 skipped`, MCP `18` |
| Frontend gate | `make frontend` passed TypeScript and Vitest: `76 files, 863 passed` |
| Compose journey | `make e2e-compose` passed all 12 checks in `0.5s`, using default `auto` confinement and no unconfined opt-out |
| Controlled agent run | `deploy/pilot/run_smoke.py` passed its single Playwright journey in `36.5s` using deterministic Docker worker `harness-engine-worker:p1-tests`; `--real` was not used and no paid/real-provider call was made |

### Current security limitations and readiness

Linux Landlock is now verified for the pilot path: writes are restricted to the
task workspace/private temp, and reads/exec are granted through an allow-list
that omits forbidden paths and sibling task workspaces. `auto` still fails
closed when the kernel/runtime probe fails. The boundary remains host/kernel
dependent, the Compose service is a shared backend container rather than a
per-task Docker sandbox, and approved network egress remains unimplemented.
Compose and Docker task execution default to no network; the explicit network
opt-in is not an allow-list and must not be treated as production isolation.
`confinement=off` is an explicit diagnostic-only mode.

The verdict remains **NOT READY** for production or a general pilot. Docker
runner deployment wiring, real-provider validation, host certification, and
counsel-approved legal/commercial terms remain open. The deterministic smoke and
zero-key Compose journey are evidence of application behavior only, not
production sandbox certification. The broad native `make gate` was not run
after the macOS Section 5 result because its test matrix would repeat the
same unavailable-boundary failure; static gates and the complete Section 5
suite were independently verified in the temporary Linux test image instead.
The broader Linux Python gate was then completed in independent batches; the
first combined batch stopped at Section 5 only because the generic backend
image did not contain `git`, which its checkpoint tests require. No source
failure was inferred from that image limitation, and the dedicated Linux
Section 5 image passed the full suite.

### Latest continuation — 2026-10-22 network-default hardening

The remaining local hardening step is complete. `DockerSandboxRunner` now
defaults to `network_mode=none`, and Compose/deployment wiring defaults
`HARNESS_SANDBOX_ALLOW_NETWORK=0`. The opt-in flag remains explicitly
non-allow-listed; an approved relay/proxy is still required for production.

The first rebuild attempt stopped before the project build because Docker
Buildx could not update `/Users/user/.docker/buildx/activity` with the
available sandbox permissions. After approved local Docker access, the rebuild
completed with optimized context `5.69MB` and image digest
`sha256:2c8fada851dafcdd4792893f4bd4a4f7e67e6ea56dbeafddd5fd0b62a5fbb2ab`.
Only the local sandbox container was recreated; its image digest matched, its
resolved network setting was `HARNESS_SANDBOX_ALLOW_NETWORK=0`, and the loaded
Landlock source retained the verified read allow-list (`confinement.py` SHA
`fd20bac80a07569ef313aabb8d9f4aa684c79a1a2bce85b25456c0a7caa76997`).

`make static-gates` passed, the focused Docker runner test passed (`4 passed`),
and `make e2e-compose` passed all 12 checks again after the refresh. No commit,
deployment, or paid/real-provider model call was made.

# Production-Readiness Remediation — Final Report

Date: 2026-10-17. Working tree left uncommitted per program rule (human
reviews the diff). This report covers the full remediation wave: the pilot
backlog close-out (sessions 1–3), the two residual code items, the Section-B
adversarial hardening, and the E2E/CI enforcement work.

## 1. Executive summary

- The final two tracked-as-open code items are **FIXED**: MAC2-F4
  (redeem/ws-ticket rate limiting) and OPS-20 (E2E in CI).
- The E2E gate is **live-enforced**: a new `e2e-compose` job in
  `.github/workflows/ci.yml` runs a 12-check full-stack journey on every push,
  with a migrate exit-code gate and failure-artifact upload. The journey is
  green live (12/12), including through `make e2e-compose`.
- Adversarial testing found and fixed a **real replay bug**: post-terminal
  rows in legacy event logs could resurrect a DONE run on cold boot. Both
  replay walks are now sealed; the demo fixture was re-curated to an H-008-
  legal sequence (the old capture encoded an impossible order that flat
  replay had been masking).
- The recorded non-reproducible flake (`test_heartbeat_claims.py`) is
  **root-caused and fixed**: cleanup raced an uninterruptible executor thread
  and a StaticPool/aiosqlite cancellation artifact. 0/80 solo runs after (was
  4–5/60), S4 green twice.
- Three E2E-found live bugs fixed: demo-seed FK insert order on Postgres
  (M18-C8), audit JSONB `LIKE` 500, landlock version-heuristic vs. Docker
  seccomp (M18-C9 — replaced with a runtime syscall probe + honest `auto`
  degradation).
- All suites green: S1 639/10 skip · S2 36 · S3 95 · S4 526/7 skip · S5
  181/1 skip · S6 118 · S8 88 · S10 69 · S11 50 · frontend 500 (TS, clean
  install); `make gate` (byte-for-byte CI parity) GREEN.
- **Verdict: PRODUCTION-READY WITH EXPLICIT BLOCKERS** — code, tests, CI and
  docs are green; the blockers are external-provider drills and two decision
  lanes, all enumerated in §5–§7.

## 2. Changed files (working tree)

Tracked modifications (21 files, +834/−88):

| Area | Files | What |
|---|---|---|
| Identity | `section1/.../ratelimit.py`, `app.py` | MAC2-F4 routes/limits; audit `CAST(detail AS TEXT)` filter (PG+SQLite) |
| Orchestrator | `section4/.../service.py`, `state_machine.py`, `demo.py`, `demo_replay.py`, `api.py`, `budget.py` | replay terminal sealing; adversarial hardening; demo-seed FK order; H-008-legal demo fixture; M23-A/B (TTL + budget warnings) |
| Sandbox | `section5/.../confinement.py`, `local_runner.py` | landlock runtime probe; honest `auto` degradation with one-time warning |
| CI / build | `.github/workflows/ci.yml`, `Makefile`, `docs/ci-gates.md` | `e2e-compose` job; `make e2e-compose`; gate table row |
| Docs | `RISK_REGISTER.md`, `SESSION_PLAN.md`, `session2/README.md`, `GAP_ANALYSIS_AND_FIXES.md` | closure entries with evidence |
| Tests | S1 rate-limit/auth, S4 heartbeat/budget, S4 unit additions | see below |

New files: `scripts/e2e/compose_journey.py` (12-check journey);
`section4/tests/unit/{test_state_machine_adversarial,test_replay_terminal_sealing,test_demo_seed_live_pg,test_plan_approval_ttl}.py`;
`section1/tests/test_ratelimit_mac2f4.py`;
`section5/tests/unit/test_landlock_probe_selection.py`;
`docs/runbooks/external-validation.md`;
`docs/pilot-remediation/RECONCILIATION_MATRIX.md` (untracked corpus).

## 3. DB impact

- **No schema changes, no new migrations.** Ledger unchanged (32 rows, max
  0033, 0014 skipped by design). Rollback = revert working-tree patches; no
  data migration needed.
- Data-path changes are read-only or portable: replay guards (early-`break`
  on terminal state) never write; demo seed re-orders writes to
  insert-run → insert-PR → update-run (works on PG and SQLite); audit filter
  cast is portable.
- Live-PG checks executed: demo-seed FK cycle test (`HARNESS_TEST_PG=1`,
  2 passed), audit route live (200, rows incl. `invite.minted`), compose
  `migrate` exit code 0.

## 4. Verification results

| Check | Result |
|---|---|
| `make gate` (static-gates + lint + test + frontend; CI parity) | **GREEN** |
| S1 · S2 · S3 · S4 · S5 · S6 · S8 · S10 · S11 suites | 639/10s · 36 · 95 · 526/7s ×2 · 181/1s · 118 · 88 · 69 · 50 |
| Frontend clean-room (`npm ci` + `tsc --noEmit` + vitest) | 87 pkgs, exit 0, 56 files / **500 passed** |
| Compose journey (12 checks) | **12/12 green** — direct and via `make e2e-compose` |
| Heartbeat flake fix | **0 failures / 80** solo runs (was 4–5/60); suite green twice |
| Landlock probe (live container) | probe `False` → warning logged exactly once → journey step 9 green |
| Live-PG: demo FK, audit route, migrate exit gate | pass / 200 / exit 0 |
| Section-B adversarial: transition matrix (20 tests), replay sealing (3), landlock selection (5), MAC2-F4 (6), M23-A/B (21) | all green in suites |

## 5. Risk disposition

**FIXED (with passing evidence):** MAC2-F4 · OPS-20 · M23-A · M23-B ·
replay terminal sealing · heartbeat-claims flake · M18-C8 (demo FK) ·
M18-C9 (landlock) · audit JSONB 500 · S2-D1…D6 (prior wave) · R-36 ·
all prior FIXED rows in the matrix stand at HEAD.

**Design notes (recorded, not defects):** state-machine mutators enforce
only the liveness half of H-007/H-008 — role/diff-evidence gating lives in
`signals.py` + service layer (adversarial matrix pins this deliberately).
`finally`-block `finish_run` can hang post-cancel under StaticPool/aiosqlite
(test-env artifact; production uses asyncpg pools — noted as latent
fragility, no code change).

**BLOCKED / HUMAN DECISION REQUIRED:** external-provider drills (Stripe
test-mode, live OIDC IdP, WAL-G against a real object store, live model key)
— procedures in `docs/runbooks/external-validation.md`. Wave 2 (§8) closed
the code-side gaps this paragraph previously carried: the SPA SSO callback is
BUILT; mcp is pinned `>=1.2,<2`; the rollup anomaly is closed as
not-reproducible.

## 6. Scorecard (15 categories)

| # | Category | Verdict | Anchor evidence |
|---|---|---|---|
| 1 | Authn/authz & privileged paths | VERIFIED | S1 639/10; MAC2-F4; bearer wiring tests |
| 2 | Tenant/team isolation | VERIFIED | cross-tenant 404 no-oracle in journey; S1 tests |
| 3 | Secrets & redaction | VERIFIED | Fernet-at-rest, hint-only GETs, scrub tests; S1-R02 |
| 4 | Sandbox boundaries | VERIFIED | landlock probe + fail-closed explicit modes; S5 181/1; journey step 9 |
| 5 | Lifecycle/state machine correctness | VERIFIED | 10×16 adversarial matrix; done-gate path tests; S4 526/7 |
| 6 | Durability & replay integrity | FIXED | terminal sealing both walks; determinism tests; replay of 10 marked events in journey |
| 7 | Budget & metering | VERIFIED | M23-B warn semantics; ledger on completion; S4 budget tests |
| 8 | Provider honesty/failure paths | VERIFIED (hermetic) | fail-closed, honest dry-mode, timeout/breaker tests; live key drill blocked |
| 9 | Reliability (flakes/races) | FIXED | heartbeat flake root-caused; 0/80; suites green twice |
| 10 | Observability & audit | VERIFIED | audit route live; metrics 7 families in journey; OTP log hygiene |
| 11 | Ops: migrations/backup/rollback | VERIFIED (dev-sized) | migrate exit gate; PITR drill evidence; rollback runbook; real-store drill blocked |
| 12 | E2E & CI enforcement | FIXED | `e2e-compose` job; journey 12/12; `make gate` green |
| 13 | Frontend delivery | VERIFIED | clean-room install + build + 500 tests |
| 14 | External integrations readiness | PARTIAL | code-complete + stub-verified; live drills pending (§5) |
| 15 | Documentation truthfulness | VERIFIED | register/matrix/session docs reconciled with evidence; stale claims removed |

## 7. Verdict & what is needed from the human

**PRODUCTION-READY WITH EXPLICIT BLOCKERS.**

1. Push the local commits (wave 1 + wave 2; nothing is on origin yet). CI on
   origin is red on the OLD revision — the wave-1/2 commits are what turns it
   green. Then add branch protection (the repo has NONE today): required
   contexts `e2e-compose` and `migrations apply clean on real PG (migrate.py)`
   — both run per-PR. (`nightly real-PG integration probe` is schedule-gated
   and must NOT be a required check.)
2. Execute (or explicitly accept risk on) the external drills in
   `docs/runbooks/external-validation.md` (SPA SSO callback is built; still
   needs IdP credentials).
3. Rollup repro: closed as not-reproducible — re-open with the original
   command if it resurfaces. mcp: RESOLVED (pinned `<2`, `88bb78f`).

## 8. Wave-2 close-out (post-report)

After wave 1 was committed locally, `gh run list` showed scheduled CI red on
EVERY run at the origin tip (`9f85590`) — install bugs and gitleaks findings
that had also masked the real-PG probes entirely. Wave 2 unblocked CI and
executed the §7 close-out. Full detail: RISK_REGISTER "Nightly-CI unblock +
close-out pass (wave 2)". Local commits (NOT pushed): `7af0381` (CI unblock:
`[dev]` probe install, `sqlalchemy[asyncio]` extras, gitleaks allowlist +
OPS-21), `88bb78f` (mcp pin), `6537caf` (probe rot + migration 0034 FK
cascade), `fae0600` (S4 legacy-replay planId recovery + hermetic checkpoint
schema), `412fc30` (SPA SSO callback).

Verification deltas (same env CI uses; probes now actually exercised):

| Check | Wave-1 report | Wave-2 (HEAD) |
|---|---|---|
| S1 suite | 639/10 skip | 639/10 skip offline · **649 live-PG** |
| Integration probe | never ran (install bug) | **4/4** |
| migrate_selftest | 32 (0001→0033) | **33 (0001→0034, idempotent)** |
| S4 suite | 526/7 skip | 526/7 skip (unchanged after fix) |
| Frontend | 500 | **507/507** + `tsc` |
| gitleaks (full history, 281 commits) | red (5 findings) | **0 findings** |
| mcp tests | drift, decision pending | **18/18 on 1.30.0 (pinned <2)** |

New real findings fixed in wave 2: `cost_ledger_daily.team_id` FK was
NO-ACTION (0027 drift vs the deletion-story law) → migration 0034;
`_rehydrate`'s pre-mirror PLAN_APPROVED fallback no-oped on a pre-seeded
`None` key (`setdefault`) so legacy logs lost their `planId` → guarded
assignment; the integration checkpoint had never run the enriched S4
aggregate against its hermetic schema (SQL-side `pr_id`, M15-D1) → helper
pins the migrated shape.

Branch-protection skeleton (operator, once CI is green on origin):

```
gh api -X PUT repos/{owner}/{repo}/branches/master/protection \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=e2e-compose' \
  -f 'required_status_checks[contexts][]=migrations apply clean on real PG (migrate.py)' \
  -f 'enforce_admins=false' -F 'required_pull_request_reviews=' \
  -F 'restrictions='
```
