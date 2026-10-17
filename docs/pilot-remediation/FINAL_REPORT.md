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
— procedures in `docs/runbooks/external-validation.md`; **SPA has no
`auth/sso` callback wiring** (OIDC is API-complete only) — decide pilot SSO
need; rollup "10k latency" anomaly not reproducible (need the original
failing command/log); mcp under the S11 venv (mcp 2.1.1 drift — version-bound
decision pending; global-env run green).

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

1. Review and commit the working-tree patches (program rule: no commits by
   the agent). Branch protection: add the stable job name `e2e-compose`.
2. Execute (or explicitly accept risk on) the external drills in
   `docs/runbooks/external-validation.md`; decide whether pilot SSO requires
   the SPA callback work.
3. Provide the original rollup failure repro (or accept closure as
   not-reproducible), and pick the mcp dependency resolution.
