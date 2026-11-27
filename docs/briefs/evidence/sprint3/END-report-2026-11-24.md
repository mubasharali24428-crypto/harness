# Harness Sprint 3 report — 2026-11-24 (OpenCode HY4)

Branch `w28-s1-delegate`, local only (**not pushed**). Baseline `fc291c48` (Sprint 2 closure);
pre-flight `03a03a40`; end = the Task 20 commit. Docker daemon down all sprint; the only real
Postgres was a local Homebrew PG 18.4, used through the Makefile's documented
`HARNESS_TEST_PG_EXTERNAL=1` path.

## Commits (Tasks 11–20)

| Task | SHA | Status |
|------|-----|--------|
| 11 | `1189d27e` | Done — migrate selftest PASS, 79 migrations 0001→0081, idempotent |
| 12 | `df6c890c` | **BLOCKED** — Docker daemon down; no compose health smoke, no faked curl |
| 13 | `2a1dc0cc` | Done — self-service Technical/Plain/Decision card in `/settings/account` |
| 14 | `29a0d455` | Done — `similarCases` on approvals API + read-only card list |
| 15 | `67fcf7f0` | Done (mock path) — live stack round-trip BLOCKED; openapi parity + axe/375 px guards pass |
| 16 | `fd176851` | Done — 15 unsigned DRAFTs + draft-path column; no status or signature changed |
| 17 | `66443641` | Done — 0078 + 0080 integration through the deployed routers (sqlite) |
| 18 | `5915f1ee` | Done — read-only, flag-gated W31 proposal panel; no dispatch wiring |
| 19 | `6e7aab5a` | Done — P-D6 negative test, flag on in test only; no global enable |
| 20 | this commit | Done — STATUS paragraph + this report |

## Verified at closure (Task 20 commit)

| Gate | Result |
|---|---|
| `make static-gates` | green (route law, isolation, compose config, TS mirror, spec drift, eval check, env knobs) |
| `make check-ts-dist` | green — TS client dist matches a fresh build (26 files) |
| `make test-section1` | **1298 passed, 62 skipped, 1 failed** — `test_notifications_delivery::test_mail_holds_no_content...`, an order-dependent flake: passes in isolation at this SHA and passed in an earlier full run (1299) |
| `make test-section4` | **2441 passed, 60 skipped, 2 failed** — `test_forbidden_path_mounts::test_real_container_readonly_bind_blocks_bypass` and `test_scope_mounts::test_real_container_scope_mounts_refuse_the_m01_bypass`; both shell out to `docker run` and the daemon is down (same two as Sprint 1/2) |
| `make test-section8` | 108 passed |
| frontend (`npx vitest run` + `npx tsc --noEmit`) | 121 files / 1233 tests passed; tsc clean |
| playwright (`approval-rendering.e2e.ts`, mocked API) | 6 passed — includes axe WCAG A/AA/21AA/22AA and 375 px clipping guards |
| `make pg-probe-migrate` (external PG) | PASS: 79 migrations (0001→0081) applied clean on real PG 18.4; re-run idempotent |

Targeted probes also green: `section1/test_approval_rendering.py` 5 passed;
section4 `-k "assignment_plan or case_memory or denial"` 113 passed;
section4 `-k scheduler` 4 passed; section4 `-k "collab or envelope or risk"` 178 passed, 8 skipped.

## BLOCKED (environment / owner)

- **Environment:** compose stack smoke (Task 12), live approval e2e (Task 15), and the two
  section4 Docker container tests. All require a running Docker daemon / compose `:5499`.
- **Owner:** SOWs and revenue lines, P-D2 rail choice + written seller eligibility, F-SEC-05
  redeploy, paid benchmark budget (X-D2), P-D7 / Gate 6 go-no-go, and signatures on every
  DEFAULT §17 row. Engineering produced DRAFTs only; **no 10.11 gate line moved**.
- P-D6 envelopes remain **not enabled** in production defaults; the flag is set only inside one test.
  P-D7 stays DEFERRED; no scheduler or case-memory auto-execution was wired.

## Migrations applied?

- **Verified on real PG**, not on a persistent reference DB: `migrate_selftest` creates a scratch
  database, applies 0001→0081 and drops it, twice (Task 11 and closure), both PASS on PostgreSQL
  18.4 at `127.0.0.1:5432` via `HARNESS_TEST_PG_EXTERNAL=1`.
- Compose `:5499` / a durable deployed database was **not** migrated — Docker is down. When the
  daemon is back, run the documented `docker compose ... up -d migrate` against the target DB.
  Do not treat the scratch-DB PASS as "the live database is migrated."

## Uncommitted files (not Sprint work; exclude from any push)

32 modified `docs/briefs/evidence/ui/r2-*.png` (pre-existing); 3 regenerated sprint2 `x*.png`
screenshots from the Task 15 playwright run; untracked `docs/briefs/evidence/eval/code-search-default-*.json`,
`docs/briefs/evidence/quality/ci-local/`, `.openclaw/`. No images were committed in Sprint 3.

## Push recommendation

**Hold until the owner explicitly asks for a push.** Branch is local-only and now ~60 commits ahead
of origin. The remaining migrations still need a real apply step on the deployed database once
Docker/compose is available; that is an operator action, not an engineering commit here.
